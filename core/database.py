from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from models.schemas import TaskStatus


class Database:
    """SQLite database for task persistence."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def initialize(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                quota_concurrency INTEGER NOT NULL DEFAULT 2,
                quota_max_resolution INTEGER NOT NULL DEFAULT 4096,
                quota_max_samples INTEGER NOT NULL DEFAULT 512,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                key_hash TEXT NOT NULL UNIQUE,
                key_prefix TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                last_used_at TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                model_id TEXT NOT NULL DEFAULT '',
                scene_id TEXT,
                scene_name TEXT,
                prompt TEXT DEFAULT '',
                storage_path TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                intent_json TEXT,
                result_url TEXT,
                error_message TEXT,
                progress REAL DEFAULT 0.0,
                progress_message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workers (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL DEFAULT '',
                hostname TEXT NOT NULL DEFAULT '',
                gpu_device TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'idle',
                last_heartbeat TEXT NOT NULL,
                current_task_id TEXT,
                concurrency INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
        """)
        self._conn.commit()

    # --- User CRUD ---

    def create_user(self, email: str, display_name: str,
                    password_salt: str, password_hash: str,
                    role: str = "user") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        user_id = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO users
               (id, email, display_name, password_salt, password_hash, role,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, email, display_name, password_salt, password_hash, role, now, now),
        )
        self._conn.commit()
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None

    def update_user(self, user_id: str, **kwargs) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        kwargs["updated_at"] = now
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [user_id]
        self._conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        self._conn.commit()
        return self.get_user(user_id)

    # --- API Key CRUD ---

    def create_api_key(self, user_id: str, key_hash: str, key_prefix: str,
                       label: str = "") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        key_id = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO api_keys
               (id, user_id, key_hash, key_prefix, label, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (key_id, user_id, key_hash, key_prefix, label, now),
        )
        self._conn.commit()
        return self.get_api_key(key_id)

    def get_api_key(self, key_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM api_keys WHERE id = ?", (key_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_api_key_by_hash(self, key_hash: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        ).fetchone()
        return dict(row) if row else None

    def list_api_keys(self, user_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def revoke_api_key(self, key_id: str, user_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ? AND user_id = ?",
            (key_id, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # --- Task CRUD ---

    def create_task(self, id: str, user_id: str, model_id: str, prompt: str,
                    scene_id: str | None = None, scene_name: str | None = None,
                    storage_path: str = "") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO tasks
               (id, user_id, model_id, scene_id, scene_name, prompt, storage_path,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (id, user_id, model_id, scene_id, scene_name, prompt, storage_path,
             TaskStatus.pending.value, now, now),
        )
        self._conn.commit()
        return self.get_task(id)

    def get_task(self, task_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        task = dict(row)
        if task.get("intent_json"):
            task["intent_json"] = json.loads(task["intent_json"])
        return task

    def list_tasks(self, user_id: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        if user_id:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
            total = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
            total = self._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        tasks = [dict(r) for r in rows]
        for t in tasks:
            if t.get("intent_json"):
                t["intent_json"] = json.loads(t["intent_json"])
        return tasks, total

    def claim_next_task(self) -> dict | None:
        """Atomically find the oldest queued task and mark it running."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            row = self._conn.execute(
                "SELECT id FROM tasks WHERE status = ? ORDER BY created_at ASC LIMIT 1",
                (TaskStatus.queued.value,),
            ).fetchone()
            if row is None:
                return None
            task_id = row["id"]
            self._conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (TaskStatus.running.value, now, task_id, TaskStatus.queued.value),
            )
            self._conn.commit()
            # Verify we actually claimed it (handle race condition)
            task = self.get_task(task_id)
            if task and task["status"] == TaskStatus.running.value:
                return task
            return None
        except Exception:
            return None

    def update_task_status(self, task_id: str, status: TaskStatus, **extra) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        fields = {"status": status.value, "updated_at": now}
        fields.update(extra)
        if "intent_json" in fields and isinstance(fields["intent_json"], dict):
            fields["intent_json"] = json.dumps(fields["intent_json"])
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [task_id]
        self._conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        self._conn.commit()
        return self.get_task(task_id)
