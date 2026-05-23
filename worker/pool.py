"""Worker pool management — registration, heartbeat, GPU assignment, quota.

Design
------
- Workers self-register via ``POST /api/workers/register``
- Workers send heartbeats every 30s via ``POST /api/workers/{id}/heartbeat``
    - Missing 3 consecutive heartbeats → worker marked ``offline``
- The ``claim_next_task`` endpoint checks available capacity before assigning
- GPU devices are tracked so concurrent renders don't share a single GPU
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from core.config import settings
from core.database import Database

logger = logging.getLogger("blenderserver.worker.pool")

_HEARTBEAT_TIMEOUT_SECONDS = 120  # 3 missed heartbeats at 30s interval


def register_worker(
    db: Database,
    label: str = "",
    hostname: str = "",
    gpu_device: str = "",
    concurrency: int = 1,
) -> dict:
    """Register a new worker instance. Returns the worker dict."""
    import uuid
    now = datetime.now(timezone.utc).isoformat()
    worker_id = str(uuid.uuid4())
    db._conn.execute(
        """INSERT INTO workers
           (id, label, hostname, gpu_device, status, last_heartbeat, concurrency, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (worker_id, label, hostname, gpu_device, "idle", now, concurrency, now),
    )
    db._conn.commit()
    return get_worker(db, worker_id)


def get_worker(db: Database, worker_id: str) -> dict | None:
    row = db._conn.execute(
        "SELECT * FROM workers WHERE id = ?", (worker_id,)
    ).fetchone()
    return dict(row) if row else None


def heartbeat(db: Database, worker_id: str) -> dict | None:
    """Update the worker's heartbeat timestamp. Returns the worker or None."""
    now = datetime.now(timezone.utc).isoformat()
    cur = db._conn.execute(
        "UPDATE workers SET last_heartbeat = ?, status = 'idle' WHERE id = ? AND status != 'offline'",
        (now, worker_id),
    )
    db._conn.commit()
    if cur.rowcount == 0:
        return None
    return get_worker(db, worker_id)


def mark_busy(db: Database, worker_id: str, task_id: str):
    db._conn.execute(
        "UPDATE workers SET status = 'busy', current_task_id = ? WHERE id = ?",
        (task_id, worker_id),
    )
    db._conn.commit()


def mark_idle(db: Database, worker_id: str):
    db._conn.execute(
        "UPDATE workers SET status = 'idle', current_task_id = NULL WHERE id = ?",
        (worker_id,),
    )
    db._conn.commit()


def mark_offline(db: Database, worker_id: str):
    db._conn.execute(
        "UPDATE workers SET status = 'offline' WHERE id = ?",
        (worker_id,),
    )
    db._conn.commit()


def list_workers(db: Database, status: str | None = None) -> list[dict]:
    if status:
        rows = db._conn.execute(
            "SELECT * FROM workers WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = db._conn.execute(
            "SELECT * FROM workers ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def check_available_capacity(db: Database) -> int:
    """Return how many additional tasks can be dispatched concurrently.

    Counts idle workers (each with its own concurrency slot).
    """
    idle = list_workers(db, status="idle")
    busy = list_workers(db, status="busy")

    total_idle_slots = sum(w.get("concurrency", 1) for w in idle)
    total_busy_slots = sum(w.get("concurrency", 1) for w in busy)

    logger.debug(
        "Worker pool capacity: %d idle slots, %d busy slots",
        total_idle_slots, total_busy_slots,
    )
    return total_idle_slots


def cleanup_stale_workers(db: Database):
    """Mark workers as offline if heartbeat is too old."""
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - _HEARTBEAT_TIMEOUT_SECONDS

    rows = db._conn.execute(
        "SELECT id, last_heartbeat FROM workers WHERE status NOT IN ('offline', 'dead')"
    ).fetchall()
    for row in rows:
        try:
            hb_time = datetime.fromisoformat(row["last_heartbeat"]).timestamp()
        except (ValueError, TypeError):
            hb_time = 0
        if hb_time < cutoff:
            logger.warning("Worker %s heartbeat timeout — marking offline", row["id"])
            mark_offline(db, row["id"])


# ---------------------------------------------------------------------------
# Quota enforcement
# ---------------------------------------------------------------------------


def check_user_quota(
    db: Database, user_id: str, resolution_x: int, resolution_y: int, samples: int
) -> tuple[bool, str]:
    """Check if the user's task respects their tier quota.

    Returns ``(allowed, reason)``.
    """
    user = db.get_user(user_id)
    if not user:
        return False, "User not found"

    if not user.get("is_active"):
        return False, "Account is disabled"

    # Resolution quota
    max_res = user.get("quota_max_resolution", 4096)
    if resolution_x > max_res or resolution_y > max_res:
        return False, f"Resolution {resolution_x}x{resolution_y} exceeds quota (max {max_res})"

    # Sample quota
    max_samples = user.get("quota_max_samples", 512)
    if samples > max_samples:
        return False, f"Samples {samples} exceeds quota (max {max_samples})"

    # Concurrency quota — count running tasks for this user
    running = db._conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'running'",
        (user_id,),
    ).fetchone()[0]
    max_conc = user.get("quota_concurrency", 2)
    if running >= max_conc:
        return False, f"Concurrency limit reached ({running}/{max_conc})"

    return True, ""
