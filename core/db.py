"""Async database layer — PostgreSQL (asyncpg) for production, aiosqlite for dev.

Usage::

    from core.db import AsyncDatabase
    db = AsyncDatabase()
    await db.initialize()
    user = await db.get_user("...")
    await db.close()
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import settings
from models.schemas import TaskStatus

try:
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection
    _HAS_SQLALCHEMY = True
except ImportError:
    _HAS_SQLALCHEMY = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(val: Any) -> str | None:
    if val is None:
        return None
    return json.dumps(val, ensure_ascii=False)


def _json_loads(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Async Database
# ---------------------------------------------------------------------------


class AsyncDatabase:
    """Async SQL database with SQLAlchemy Core (supports PostgreSQL + SQLite)."""

    def __init__(self, database_url: str = ""):
        self._url = database_url or settings.database_url
        self._engine = None
        self._is_sqlite = self._url.startswith("sqlite")

    async def initialize(self):
        if not _HAS_SQLALCHEMY:
            raise RuntimeError(
                "SQLAlchemy is required. Install with: pip install sqlalchemy[asyncio] asyncpg aiosqlite"
            )
        kwargs = {"echo": False}
        if not self._is_sqlite:
            kwargs["pool_size"] = 10
            kwargs["max_overflow"] = 20
        else:
            kwargs["poolclass"] = sa.pool.NullPool  # SQLite doesn't need pooling
        self._engine = create_async_engine(self._url, **kwargs)
        await self._create_tables()
        await self._migrate_schema()

    async def close(self):
        if self._engine:
            await self._engine.dispose()
            self._engine = None

    async def _execute(self, query: Any, *args) -> Any:
        """Execute a statement and return the result."""
        async with self._engine.connect() as conn:
            result = await conn.execute(query, *args)
            await conn.commit()
            return result

    async def _fetchone(self, query: Any, *args) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(query, *args)).mappings().first()
            return dict(row) if row else None

    async def _fetchall(self, query: Any, *args) -> list[dict]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query, *args)).mappings().all()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def _create_tables(self):
        meta = sa.MetaData()

        sa.Table(
            "users", meta,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("email", sa.String(255), unique=True, nullable=False),
            sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
            sa.Column("password_salt", sa.String(128), nullable=False),
            sa.Column("password_hash", sa.String(128), nullable=False),
            sa.Column("role", sa.String(20), nullable=False, server_default="user"),
            sa.Column("quota_concurrency", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("quota_max_resolution", sa.Integer(), nullable=False, server_default="4096"),
            sa.Column("quota_max_samples", sa.Integer(), nullable=False, server_default="512"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.String(32), nullable=False),
            sa.Column("updated_at", sa.String(32), nullable=False),
        )

        sa.Table(
            "api_keys", meta,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("key_hash", sa.String(128), unique=True, nullable=False),
            sa.Column("key_prefix", sa.String(16), nullable=False),
            sa.Column("label", sa.String(100), nullable=False, server_default=""),
            sa.Column("last_used_at", sa.String(32), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.String(32), nullable=False),
        )

        sa.Table(
            "tasks", meta,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("model_id", sa.String(255), nullable=False, server_default=""),
            sa.Column("scene_id", sa.String(100), nullable=True),
            sa.Column("scene_name", sa.String(200), nullable=True),
            sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
            sa.Column("storage_path", sa.String(500), nullable=False, server_default=""),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("intent_json", sa.Text(), nullable=True),
            sa.Column("result_url", sa.String(500), nullable=True),
            sa.Column("result_urls_json", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stage_name", sa.String(50), nullable=True),
            sa.Column("stage_progress", sa.Float(), nullable=True),
            sa.Column("eta_seconds", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("progress", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("progress_message", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.String(32), nullable=False),
            sa.Column("updated_at", sa.String(32), nullable=False),
        )

        sa.Table(
            "workers", meta,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("label", sa.String(200), nullable=False, server_default=""),
            sa.Column("hostname", sa.String(200), nullable=False, server_default=""),
            sa.Column("gpu_device", sa.String(200), nullable=False, server_default=""),
            sa.Column("status", sa.String(20), nullable=False, server_default="idle"),
            sa.Column("last_heartbeat", sa.String(32), nullable=False),
            sa.Column("current_task_id", sa.String(36), nullable=True),
            sa.Column("concurrency", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.String(32), nullable=False),
        )

        sa.Table(
            "audit_log", meta,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("action", sa.String(100), nullable=False),
            sa.Column("resource_type", sa.String(50), nullable=True),
            sa.Column("resource_id", sa.String(36), nullable=True),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("ip_address", sa.String(45), nullable=True),
            sa.Column("created_at", sa.String(32), nullable=False),
        )

        # ── Organizations ──────────────────────────────────────────────
        sa.Table(
            "organizations", meta,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("slug", sa.String(100), unique=True, nullable=False),
            sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("stripe_customer_id", sa.String(100), nullable=True),
            sa.Column("created_at", sa.String(32), nullable=False),
            sa.Column("updated_at", sa.String(32), nullable=False),
        )

        sa.Table(
            "organization_members", meta,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("role", sa.String(20), nullable=False, server_default="member"),
            sa.Column("created_at", sa.String(32), nullable=False),
            sa.UniqueConstraint("organization_id", "user_id", name="uq_org_member"),
        )

        # ── Subscription Plans ─────────────────────────────────────────
        sa.Table(
            "subscription_plans", meta,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("slug", sa.String(50), unique=True, nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("price_monthly_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("price_yearly_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stripe_monthly_price_id", sa.String(100), nullable=True),
            sa.Column("stripe_yearly_price_id", sa.String(100), nullable=True),
            sa.Column("features_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.String(32), nullable=False),
        )

        sa.Table(
            "subscriptions", meta,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("plan_id", sa.String(36), sa.ForeignKey("subscription_plans.id"), nullable=False),
            sa.Column("stripe_subscription_id", sa.String(100), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("billing_interval", sa.String(10), nullable=False, server_default="monthly"),
            sa.Column("current_period_start", sa.String(32), nullable=True),
            sa.Column("current_period_end", sa.String(32), nullable=True),
            sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.String(32), nullable=False),
            sa.Column("updated_at", sa.String(32), nullable=False),
        )

        # ── Gallery Assets ─────────────────────────────────────────────
        sa.Table(
            "gallery_assets", meta,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=True),
            sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("title", sa.String(200), nullable=False, server_default=""),
            sa.Column("file_url", sa.String(500), nullable=False),
            sa.Column("thumbnail_url", sa.String(500), nullable=True),
            sa.Column("file_size", sa.Integer(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.String(32), nullable=False),
        )

        # ── Webhooks ───────────────────────────────────────────────────
        sa.Table(
            "webhooks", meta,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("url", sa.String(500), nullable=False),
            sa.Column("events", sa.Text(), nullable=False),  # JSON array: ["task.completed", "task.failed"]
            sa.Column("secret", sa.String(128), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.String(32), nullable=False),
            sa.Column("updated_at", sa.String(32), nullable=False),
        )

        async with self._engine.begin() as conn:
            await conn.run_sync(meta.create_all)

    async def _migrate_schema(self):
        """Add missing columns to existing tables (for dev databases using SQLite)."""
        if not self._is_sqlite:
            return
        from sqlalchemy import text

        migrations: dict[str, list[tuple[str, str]]] = {
            "tasks": [
                ("result_urls_json", "TEXT"),
                ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
                ("stage_name", "VARCHAR(50)"),
                ("stage_progress", "FLOAT"),
                ("eta_seconds", "INTEGER"),
                ("name", "VARCHAR(200)"),
            ],
            "users": [
                ("quota_max_tasks_per_month", "INTEGER NOT NULL DEFAULT -1"),
            ],
        }

        for table, columns in migrations.items():
            try:
                cols = await self._fetchall(text(f"PRAGMA table_info({table})"))
                existing = {c["name"] for c in cols}
            except Exception:
                continue
            for col_name, col_type in columns:
                if col_name not in existing:
                    try:
                        await self._execute(
                            text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                        )
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    async def add_audit_log(self, user_id: str | None, action: str,
                            resource_type: str | None = None,
                            resource_id: str | None = None,
                            details: str | None = None,
                            ip_address: str | None = None) -> None:
        from sqlalchemy import text
        await self._execute(
            text("""INSERT INTO audit_log (id, user_id, action, resource_type, resource_id, details, ip_address, created_at)
                    VALUES (:id, :uid, :action, :rtype, :rid, :details, :ip, :now)"""),
            {
                "id": _uuid(), "uid": user_id, "action": action,
                "rtype": resource_type, "rid": resource_id,
                "details": details, "ip": ip_address, "now": _now(),
            },
        )

    async def list_audit_logs(self, limit: int = 100, offset: int = 0) -> list[dict]:
        from sqlalchemy import text
        return await self._fetchall(
            text("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT :lim OFFSET :off"),
            {"lim": limit, "off": offset},
        )

    # ------------------------------------------------------------------
    # User CRUD
    # ------------------------------------------------------------------

    async def create_user(self, email: str, display_name: str,
                          password_salt: str, password_hash: str,
                          role: str = "user") -> dict:
        from sqlalchemy import text
        user_id = _uuid()
        now = _now()
        await self._execute(
            text("""INSERT INTO users (id, email, display_name, password_salt, password_hash, role, created_at, updated_at)
                    VALUES (:id, :email, :name, :salt, :hash, :role, :now, :now)"""),
            {"id": user_id, "email": email, "name": display_name,
             "salt": password_salt, "hash": password_hash,
             "role": role, "now": now},
        )
        return await self.get_user(user_id)

    async def get_user(self, user_id: str) -> dict | None:
        from sqlalchemy import text
        return await self._fetchone(
            text("SELECT * FROM users WHERE id = :id"), {"id": user_id},
        )

    async def get_user_by_email(self, email: str) -> dict | None:
        from sqlalchemy import text
        return await self._fetchone(
            text("SELECT * FROM users WHERE email = :email"), {"email": email},
        )

    async def update_user(self, user_id: str, **kwargs) -> dict | None:
        from sqlalchemy import text
        now = _now()
        kwargs["updated_at"] = now
        set_clause = ", ".join(f"{k} = :{k}" for k in kwargs)
        values = {**kwargs, "id": user_id}
        await self._execute(
            text(f"UPDATE users SET {set_clause} WHERE id = :id"), values,
        )
        return await self.get_user(user_id)

    async def list_users(self, limit: int = 100, offset: int = 0) -> list[dict]:
        from sqlalchemy import text
        return await self._fetchall(
            text("SELECT * FROM users ORDER BY created_at DESC LIMIT :lim OFFSET :off"),
            {"lim": limit, "off": offset},
        )

    # ------------------------------------------------------------------
    # API Key CRUD
    # ------------------------------------------------------------------

    async def create_api_key(self, user_id: str, key_hash: str, key_prefix: str,
                             label: str = "") -> dict:
        from sqlalchemy import text
        key_id = _uuid()
        await self._execute(
            text("""INSERT INTO api_keys (id, user_id, key_hash, key_prefix, label, created_at)
                    VALUES (:id, :uid, :hash, :prefix, :label, :now)"""),
            {"id": key_id, "uid": user_id, "hash": key_hash,
             "prefix": key_prefix, "label": label, "now": _now()},
        )
        return await self.get_api_key(key_id)

    async def get_api_key(self, key_id: str) -> dict | None:
        from sqlalchemy import text
        return await self._fetchone(
            text("SELECT * FROM api_keys WHERE id = :id"), {"id": key_id},
        )

    async def get_api_key_by_hash(self, key_hash: str) -> dict | None:
        from sqlalchemy import text
        return await self._fetchone(
            text("SELECT * FROM api_keys WHERE key_hash = :hash AND is_active = true"),
            {"hash": key_hash},
        )

    async def list_api_keys(self, user_id: str) -> list[dict]:
        from sqlalchemy import text
        return await self._fetchall(
            text("SELECT * FROM api_keys WHERE user_id = :uid ORDER BY created_at DESC"),
            {"uid": user_id},
        )

    async def revoke_api_key(self, key_id: str, user_id: str) -> bool:
        from sqlalchemy import text
        result = await self._execute(
            text("UPDATE api_keys SET is_active = false WHERE id = :id AND user_id = :uid"),
            {"id": key_id, "uid": user_id},
        )
        return result.rowcount > 0

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    async def create_task(self, id: str, user_id: str, model_id: str, prompt: str,
                          scene_id: str | None = None, scene_name: str | None = None,
                          storage_path: str = "", name: str = "") -> dict:
        from sqlalchemy import text
        now = _now()
        await self._execute(
            text("""INSERT INTO tasks (id, user_id, model_id, scene_id, scene_name, prompt,
                                       storage_path, status, name, created_at, updated_at)
                    VALUES (:id, :uid, :mid, :sid, :sname, :prompt, :spath, :status, :name, :now, :now)"""),
            {"id": id, "uid": user_id, "mid": model_id, "sid": scene_id,
             "sname": scene_name, "prompt": prompt, "spath": storage_path,
             "status": TaskStatus.pending.value, "name": name, "now": now},
        )
        return await self.get_task(id)

    async def get_task(self, task_id: str) -> dict | None:
        from sqlalchemy import text
        task = await self._fetchone(
            text("SELECT * FROM tasks WHERE id = :id"), {"id": task_id},
        )
        if task and task.get("intent_json"):
            task["intent_json"] = _json_loads(task["intent_json"])
        return task

    async def list_tasks(self, user_id: str | None = None, limit: int = 50,
                         offset: int = 0) -> tuple[list[dict], int]:
        from sqlalchemy import text
        if user_id:
            tasks = await self._fetchall(
                text("SELECT * FROM tasks WHERE user_id = :uid ORDER BY created_at DESC LIMIT :lim OFFSET :off"),
                {"uid": user_id, "lim": limit, "off": offset},
            )
            row = await self._fetchone(
                text("SELECT COUNT(*) as cnt FROM tasks WHERE user_id = :uid"),
                {"uid": user_id},
            )
            total = row["cnt"] if row else 0
        else:
            tasks = await self._fetchall(
                text("SELECT * FROM tasks ORDER BY created_at DESC LIMIT :lim OFFSET :off"),
                {"lim": limit, "off": offset},
            )
            row = await self._fetchone(
                text("SELECT COUNT(*) as cnt FROM tasks"),
            )
            total = row["cnt"] if row else 0
        for t in tasks:
            if t.get("intent_json"):
                t["intent_json"] = _json_loads(t["intent_json"])
        return tasks, total

    async def update_task_status(self, task_id: str, status: TaskStatus,
                                 **extra) -> dict | None:
        from sqlalchemy import text
        now = _now()
        fields = {"status": status.value, "updated_at": now}
        fields.update(extra)
        if "intent_json" in fields and isinstance(fields["intent_json"], dict):
            fields["intent_json"] = _json_dumps(fields["intent_json"])
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        values = {**fields, "id": task_id}
        await self._execute(
            text(f"UPDATE tasks SET {set_clause} WHERE id = :id"), values,
        )
        return await self.get_task(task_id)

    async def claim_next_task(self, worker_type: str | None = None) -> dict | None:
        from sqlalchemy import text
        now = _now()
        async with self._engine.connect() as conn:
            # Worker type routing: filter by intent content
            type_filter = ""
            params: dict[str, Any] = {"status": TaskStatus.queued.value}
            if worker_type == "freecad":
                type_filter = " AND intent_json LIKE :tpl"
                params["tpl"] = '%"template_id"%'
            elif worker_type == "blender":
                type_filter = " AND (intent_json IS NULL OR intent_json NOT LIKE :tpl)"
                params["tpl"] = '%"template_id"%'

            # PostgreSQL: use FOR UPDATE SKIP LOCKED; SQLite: simple UPDATE
            if self._is_sqlite:
                row = (await conn.execute(
                    text(f"SELECT id FROM tasks WHERE status = :status{type_filter} ORDER BY created_at ASC LIMIT 1"),
                    params,
                )).mappings().first()
                if row is None:
                    return None
                task_id = row["id"]
                result = await conn.execute(
                    text("UPDATE tasks SET status = :status, updated_at = :now WHERE id = :id AND status = :old_status"),
                    {"status": TaskStatus.running.value, "now": now,
                     "id": task_id, "old_status": TaskStatus.queued.value},
                )
                await conn.commit()
                if result.rowcount == 0:
                    return None
            else:
                row = (await conn.execute(
                    text(f"""SELECT id FROM tasks WHERE status = :status{type_filter}
                            ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"""),
                    params,
                )).mappings().first()
                if row is None:
                    return None
                task_id = row["id"]
                result = await conn.execute(
                    text("UPDATE tasks SET status = :status, updated_at = :now WHERE id = :id AND status = :old_status"),
                    {"status": TaskStatus.running.value, "now": now,
                     "id": task_id, "old_status": TaskStatus.queued.value},
                )
                await conn.commit()
                if result.rowcount == 0:
                    return None

            task_row = (await conn.execute(
                text("SELECT * FROM tasks WHERE id = :id"), {"id": task_id},
            )).mappings().first()
            task = dict(task_row) if task_row else None
            if task and task.get("intent_json"):
                task["intent_json"] = _json_loads(task["intent_json"])
            return task

    async def count_tasks_by_status(self, user_id: str, status: str) -> int:
        from sqlalchemy import text
        row = await self._fetchone(
            text("SELECT COUNT(*) as cnt FROM tasks WHERE user_id = :uid AND status = :status"),
            {"uid": user_id, "status": status},
        )
        return row["cnt"] if row else 0

    async def count_tasks_this_month(self, user_id: str) -> int:
        from sqlalchemy import text
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        prefix = now.strftime("%Y-%m")
        row = await self._fetchone(
            text("SELECT COUNT(*) as cnt FROM tasks WHERE user_id = :uid AND created_at LIKE :prefix AND status = 'completed'"),
            {"uid": user_id, "prefix": f"{prefix}%"},
        )
        return row["cnt"] if row else 0

    async def count_workers_by_status(self, status: str | None = None) -> int:
        from sqlalchemy import text
        if status:
            row = await self._fetchone(
                text("SELECT COUNT(*) as cnt FROM workers WHERE status = :status"),
                {"status": status},
            )
        else:
            row = await self._fetchone(
                text("SELECT COUNT(*) as cnt FROM workers WHERE status != 'offline'"),
            )
        return row["cnt"] if row else 0

    async def raw_query(self, sql: str, params: dict | None = None) -> list[dict]:
        """Execute raw SQL via SQLAlchemy text(). For admin/debug use."""
        from sqlalchemy import text
        return await self._fetchall(text(sql), params or {})

    async def raw_execute(self, sql: str, params: dict | None = None) -> Any:
        from sqlalchemy import text
        return await self._execute(text(sql), params or {})

    # ------------------------------------------------------------------
    # Worker CRUD
    # ------------------------------------------------------------------

    async def register_worker(self, label: str = "", hostname: str = "",
                              gpu_device: str = "", concurrency: int = 1) -> dict:
        from sqlalchemy import text
        worker_id = _uuid()
        now = _now()
        await self._execute(
            text("""INSERT INTO workers (id, label, hostname, gpu_device, status, last_heartbeat, concurrency, created_at)
                    VALUES (:id, :label, :host, :gpu, 'idle', :now, :conc, :now)"""),
            {"id": worker_id, "label": label, "host": hostname,
             "gpu": gpu_device, "conc": concurrency, "now": now},
        )
        return await self.get_worker(worker_id)

    async def get_worker(self, worker_id: str) -> dict | None:
        from sqlalchemy import text
        return await self._fetchone(
            text("SELECT * FROM workers WHERE id = :id"), {"id": worker_id},
        )

    async def worker_heartbeat(self, worker_id: str) -> dict | None:
        from sqlalchemy import text
        now = _now()
        result = await self._execute(
            text("""UPDATE workers SET last_heartbeat = :now, status = 'idle'
                    WHERE id = :id AND status != 'offline'"""),
            {"now": now, "id": worker_id},
        )
        if result.rowcount == 0:
            return None
        return await self.get_worker(worker_id)

    async def mark_worker_busy(self, worker_id: str, task_id: str):
        from sqlalchemy import text
        await self._execute(
            text("UPDATE workers SET status = 'busy', current_task_id = :tid WHERE id = :id"),
            {"tid": task_id, "id": worker_id},
        )

    async def mark_worker_idle(self, worker_id: str):
        from sqlalchemy import text
        await self._execute(
            text("UPDATE workers SET status = 'idle', current_task_id = NULL WHERE id = :id"),
            {"id": worker_id},
        )

    async def mark_worker_offline(self, worker_id: str):
        from sqlalchemy import text
        await self._execute(
            text("UPDATE workers SET status = 'offline' WHERE id = :id"),
            {"id": worker_id},
        )

    async def list_workers(self, status: str | None = None) -> list[dict]:
        from sqlalchemy import text
        if status:
            return await self._fetchall(
                text("SELECT * FROM workers WHERE status = :status ORDER BY created_at DESC"),
                {"status": status},
            )
        return await self._fetchall(
            text("SELECT * FROM workers ORDER BY created_at DESC"),
        )

    async def cleanup_stale_workers(self, timeout_seconds: int = 120):
        from sqlalchemy import text
        import time
        cutoff_ts = time.time() - timeout_seconds
        workers = await self._fetchall(text("SELECT id, last_heartbeat FROM workers WHERE status NOT IN ('offline', 'dead')"))
        for w in workers:
            try:
                hb_time = datetime.fromisoformat(w["last_heartbeat"]).timestamp()
            except (ValueError, TypeError):
                hb_time = 0
            if hb_time < cutoff_ts:
                await self.mark_worker_offline(w["id"])

    async def get_available_capacity(self) -> int:
        from sqlalchemy import text
        row = await self._fetchone(
            text("SELECT COALESCE(SUM(concurrency), 0) as capacity FROM workers WHERE status = 'idle'"),
        )
        return row["capacity"] if row else 0

    # ------------------------------------------------------------------
    # Organization CRUD
    # ------------------------------------------------------------------

    async def create_organization(self, name: str, slug: str, owner_id: str,
                                  stripe_customer_id: str = "") -> dict:
        from sqlalchemy import text
        org_id = _uuid()
        now = _now()
        await self._execute(
            text("""INSERT INTO organizations (id, name, slug, owner_id, stripe_customer_id, created_at, updated_at)
                    VALUES (:id, :name, :slug, :owner, :stripe, :now, :now)"""),
            {"id": org_id, "name": name, "slug": slug, "owner": owner_id,
             "stripe": stripe_customer_id, "now": now},
        )
        return await self.get_organization(org_id)

    async def get_organization(self, org_id: str) -> dict | None:
        from sqlalchemy import text
        return await self._fetchone(
            text("SELECT * FROM organizations WHERE id = :id"), {"id": org_id},
        )

    async def get_organization_by_user(self, user_id: str) -> dict | None:
        from sqlalchemy import text
        return await self._fetchone(
            text("""SELECT o.* FROM organizations o
                    JOIN organization_members om ON o.id = om.organization_id
                    WHERE om.user_id = :uid LIMIT 1"""),
            {"uid": user_id},
        )

    async def add_organization_member(self, org_id: str, user_id: str,
                                      role: str = "member") -> dict:
        from sqlalchemy import text
        members = await self._fetchall(
            text("SELECT organization_id, user_id FROM organization_members WHERE organization_id = :oid AND user_id = :uid"),
            {"oid": org_id, "uid": user_id},
        )
        if members:
            return members[0]
        await self._execute(
            text("INSERT INTO organization_members (id, organization_id, user_id, role, created_at) VALUES (:id, :oid, :uid, :role, :now)"),
            {"id": _uuid(), "oid": org_id, "uid": user_id, "role": role, "now": _now()},
        )
        return {"organization_id": org_id, "user_id": user_id, "role": role}

    async def get_org_members(self, org_id: str) -> list[dict]:
        from sqlalchemy import text
        return await self._fetchall(
            text("SELECT * FROM organization_members WHERE organization_id = :oid"),
            {"oid": org_id},
        )

    async def update_organization(self, org_id: str, **kwargs) -> dict | None:
        from sqlalchemy import text
        now = _now()
        kwargs["updated_at"] = now
        set_clause = ", ".join(f"{k} = :{k}" for k in kwargs)
        values = {**kwargs, "id": org_id}
        await self._execute(
            text(f"UPDATE organizations SET {set_clause} WHERE id = :id"), values,
        )
        return await self.get_organization(org_id)

    # ------------------------------------------------------------------
    # Subscription Plan CRUD
    # ------------------------------------------------------------------

    async def create_plan(self, name: str, slug: str, description: str = "",
                          price_monthly_cents: int = 0, price_yearly_cents: int = 0,
                          stripe_monthly_price_id: str | None = None,
                          stripe_yearly_price_id: str | None = None,
                          features: dict | None = None,
                          is_public: bool = True, sort_order: int = 0) -> dict:
        from sqlalchemy import text
        plan_id = _uuid()
        await self._execute(
            text("""INSERT INTO subscription_plans (id, name, slug, description, price_monthly_cents,
                     price_yearly_cents, stripe_monthly_price_id, stripe_yearly_price_id,
                     features_json, is_public, sort_order, created_at)
                    VALUES (:id, :name, :slug, :desc, :pm, :py, :smp, :syp, :fj, :pub, :so, :now)"""),
            {"id": plan_id, "name": name, "slug": slug, "desc": description,
             "pm": price_monthly_cents, "py": price_yearly_cents,
             "smp": stripe_monthly_price_id, "syp": stripe_yearly_price_id,
             "fj": _json_dumps(features or {}), "pub": is_public, "so": sort_order, "now": _now()},
        )
        return await self.get_plan(plan_id)

    async def get_plan(self, plan_id: str) -> dict | None:
        from sqlalchemy import text
        row = await self._fetchone(
            text("SELECT * FROM subscription_plans WHERE id = :id"), {"id": plan_id},
        )
        if row and row.get("features_json"):
            row["features"] = _json_loads(row["features_json"])
        return row

    async def get_plan_by_slug(self, slug: str) -> dict | None:
        from sqlalchemy import text
        row = await self._fetchone(
            text("SELECT * FROM subscription_plans WHERE slug = :slug"), {"slug": slug},
        )
        if row and row.get("features_json"):
            row["features"] = _json_loads(row["features_json"])
        return row

    async def list_plans(self, public_only: bool = True) -> list[dict]:
        from sqlalchemy import text
        if public_only:
            rows = await self._fetchall(
                text("SELECT * FROM subscription_plans WHERE is_public = true ORDER BY sort_order"),
            )
        else:
            rows = await self._fetchall(
                text("SELECT * FROM subscription_plans ORDER BY sort_order"),
            )
        for r in rows:
            if r.get("features_json"):
                r["features"] = _json_loads(r["features_json"])
        return rows

    async def update_plan(self, plan_id: str, **kwargs) -> dict | None:
        from sqlalchemy import text
        if "features" in kwargs and isinstance(kwargs["features"], dict):
            kwargs["features_json"] = _json_dumps(kwargs["features"])
            del kwargs["features"]
        set_clause = ", ".join(f"{k} = :{k}" for k in kwargs)
        values = {**kwargs, "id": plan_id}
        await self._execute(
            text(f"UPDATE subscription_plans SET {set_clause} WHERE id = :id"), values,
        )
        return await self.get_plan(plan_id)

    async def get_default_plan(self) -> dict | None:
        return await self.get_plan_by_slug(settings.default_plan_slug)

    # ------------------------------------------------------------------
    # Subscription CRUD
    # ------------------------------------------------------------------

    async def create_subscription(self, organization_id: str, plan_id: str,
                                  billing_interval: str = "monthly",
                                  stripe_subscription_id: str = "") -> dict:
        from sqlalchemy import text
        sub_id = _uuid()
        now = _now()
        await self._execute(
            text("""INSERT INTO subscriptions (id, organization_id, plan_id, stripe_subscription_id,
                     status, billing_interval, current_period_start, current_period_end,
                     cancel_at_period_end, created_at, updated_at)
                    VALUES (:id, :oid, :pid, :ssid, 'active', :bi, :now, :now, false, :now, :now)"""),
            {"id": sub_id, "oid": organization_id, "pid": plan_id,
             "ssid": stripe_subscription_id, "bi": billing_interval, "now": now},
        )
        return await self.get_subscription(sub_id)

    async def get_subscription(self, sub_id: str) -> dict | None:
        from sqlalchemy import text
        return await self._fetchone(
            text("SELECT * FROM subscriptions WHERE id = :id"), {"id": sub_id},
        )

    async def get_subscription_for_org(self, org_id: str) -> dict | None:
        from sqlalchemy import text
        return await self._fetchone(
            text("""SELECT s.* FROM subscriptions s
                    WHERE s.organization_id = :oid AND s.status = 'active'
                    ORDER BY s.created_at DESC LIMIT 1"""),
            {"oid": org_id},
        )

    async def get_subscription_by_stripe_id(self, stripe_subscription_id: str) -> dict | None:
        from sqlalchemy import text
        return await self._fetchone(
            text("SELECT * FROM subscriptions WHERE stripe_subscription_id = :ssid"),
            {"ssid": stripe_subscription_id},
        )

    async def update_subscription(self, sub_id: str, **kwargs) -> dict | None:
        from sqlalchemy import text
        now = _now()
        kwargs["updated_at"] = now
        set_clause = ", ".join(f"{k} = :{k}" for k in kwargs)
        values = {**kwargs, "id": sub_id}
        await self._execute(
            text(f"UPDATE subscriptions SET {set_clause} WHERE id = :id"), values,
        )
        return await self.get_subscription(sub_id)

    async def list_subscriptions(self, org_id: str | None = None) -> list[dict]:
        from sqlalchemy import text
        if org_id:
            return await self._fetchall(
                text("SELECT * FROM subscriptions WHERE organization_id = :oid ORDER BY created_at DESC"),
                {"oid": org_id},
            )
        return await self._fetchall(
            text("SELECT * FROM subscriptions ORDER BY created_at DESC"),
        )

    # ------------------------------------------------------------------
    # Webhook CRUD
    # ------------------------------------------------------------------

    async def create_webhook(self, user_id: str, url: str, events: list[str],
                             secret: str) -> dict:
        from sqlalchemy import text
        wh_id = _uuid()
        now = _now()
        await self._execute(
            text("""INSERT INTO webhooks (id, user_id, url, events, secret, is_active, created_at, updated_at)
                    VALUES (:id, :uid, :url, :events, :secret, true, :now, :now)"""),
            {"id": wh_id, "uid": user_id, "url": url,
             "events": _json_dumps(events), "secret": secret, "now": now},
        )
        return await self.get_webhook(wh_id)

    async def get_webhook(self, webhook_id: str) -> dict | None:
        from sqlalchemy import text
        row = await self._fetchone(
            text("SELECT * FROM webhooks WHERE id = :id"), {"id": webhook_id},
        )
        if row and row.get("events"):
            row["events"] = _json_loads(row["events"])
        return row

    async def list_webhooks(self, user_id: str) -> list[dict]:
        from sqlalchemy import text
        rows = await self._fetchall(
            text("SELECT * FROM webhooks WHERE user_id = :uid ORDER BY created_at DESC"),
            {"uid": user_id},
        )
        for r in rows:
            if r.get("events"):
                r["events"] = _json_loads(r["events"])
        return rows

    async def update_webhook(self, webhook_id: str, user_id: str, **kwargs) -> dict | None:
        from sqlalchemy import text
        if "events" in kwargs and isinstance(kwargs["events"], list):
            kwargs["events"] = _json_dumps(kwargs["events"])
        now = _now()
        kwargs["updated_at"] = now
        set_clause = ", ".join(f"{k} = :{k}" for k in kwargs)
        values = {**kwargs, "id": webhook_id, "uid": user_id}
        await self._execute(
            text(f"UPDATE webhooks SET {set_clause} WHERE id = :id AND user_id = :uid"), values,
        )
        return await self.get_webhook(webhook_id)

    async def delete_webhook(self, webhook_id: str, user_id: str) -> bool:
        from sqlalchemy import text
        result = await self._execute(
            text("DELETE FROM webhooks WHERE id = :id AND user_id = :uid"),
            {"id": webhook_id, "uid": user_id},
        )
        return result.rowcount > 0

    async def get_active_webhooks_for_event(self, event: str) -> list[dict]:
        from sqlalchemy import text
        rows = await self._fetchall(
            text("SELECT * FROM webhooks WHERE is_active = true"),
        )
        result = []
        for r in rows:
            events = _json_loads(r.get("events", "[]"))
            if isinstance(events, list) and event in events:
                if r.get("events"):
                    r["events"] = events
                result.append(r)
        return result

    # ------------------------------------------------------------------
    # Gallery Assets CRUD
    # ------------------------------------------------------------------

    async def list_assets(self, org_id: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        from sqlalchemy import text
        if org_id:
            rows = await self._fetchall(
                text("SELECT * FROM gallery_assets WHERE organization_id = :oid ORDER BY created_at DESC LIMIT :lim OFFSET :off"),
                {"oid": org_id, "lim": limit, "off": offset},
            )
        else:
            rows = await self._fetchall(
                text("SELECT * FROM gallery_assets ORDER BY created_at DESC LIMIT :lim OFFSET :off"),
                {"lim": limit, "off": offset},
            )
        for r in rows:
            if r.get("metadata_json"):
                r["metadata"] = _json_loads(r["metadata_json"])
        return rows

    async def create_asset(self, task_id: str | None, organization_id: str | None,
                           user_id: str, title: str, file_url: str,
                           thumbnail_url: str | None = None,
                           file_size: int | None = None,
                           metadata: dict | None = None) -> dict:
        from sqlalchemy import text
        asset_id = _uuid()
        now = _now()
        await self._execute(
            text("""INSERT INTO gallery_assets (id, task_id, organization_id, user_id, title,
                     file_url, thumbnail_url, file_size, metadata_json, created_at)
                    VALUES (:id, :tid, :oid, :uid, :title, :url, :thumb, :size, :meta, :now)"""),
            {"id": asset_id, "tid": task_id, "oid": organization_id, "uid": user_id,
             "title": title, "url": file_url, "thumb": thumbnail_url,
             "size": file_size, "meta": _json_dumps(metadata or {}), "now": now},
        )
        return await self._fetchone(
            text("SELECT * FROM gallery_assets WHERE id = :id"), {"id": asset_id},
        )

    # ------------------------------------------------------------------
    # Custom Scenes CRUD
    # ------------------------------------------------------------------

    async def _ensure_custom_scenes_table(self):
        """Create custom_scenes table if it doesn't exist."""
        from sqlalchemy import text
        try:
            await self._fetchone(text("SELECT 1 FROM custom_scenes LIMIT 1"))
        except Exception:
            await self._execute(
                text("""CREATE TABLE IF NOT EXISTS custom_scenes (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36) NOT NULL,
                    name VARCHAR(200) NOT NULL,
                    description TEXT DEFAULT '',
                    category VARCHAR(100) DEFAULT 'custom',
                    params TEXT DEFAULT '{}',
                    is_public BOOLEAN DEFAULT false,
                    thumbnail_url VARCHAR(500),
                    created_at VARCHAR(32) NOT NULL,
                    updated_at VARCHAR(32) NOT NULL
                )"""),
            )

    async def create_custom_scene(self, user_id: str, name: str, description: str = "",
                                  category: str = "custom", params: dict | None = None,
                                  is_public: bool = False,
                                  thumbnail_url: str | None = None) -> dict:
        await self._ensure_custom_scenes_table()
        from sqlalchemy import text
        scene_id = _uuid()
        now = _now()
        await self._execute(
            text("""INSERT INTO custom_scenes (id, user_id, name, description, category,
                     params, is_public, thumbnail_url, created_at, updated_at)
                    VALUES (:id, :uid, :name, :desc, :cat, :params, :pub, :thumb, :now, :now)"""),
            {"id": scene_id, "uid": user_id, "name": name, "desc": description,
             "cat": category, "params": _json_dumps(params or {}), "pub": is_public,
             "thumb": thumbnail_url, "now": now},
        )
        return await self.get_custom_scene(scene_id)

    async def get_custom_scene(self, scene_id: str) -> dict | None:
        await self._ensure_custom_scenes_table()
        from sqlalchemy import text
        row = await self._fetchone(
            text("SELECT * FROM custom_scenes WHERE id = :id"), {"id": scene_id},
        )
        if row and row.get("params"):
            row["params"] = _json_loads(row["params"])
        return row

    async def list_custom_scenes(self, user_id: str | None = None) -> list[dict]:
        await self._ensure_custom_scenes_table()
        from sqlalchemy import text
        if user_id:
            rows = await self._fetchall(
                text("SELECT * FROM custom_scenes WHERE user_id = :uid ORDER BY created_at DESC"),
                {"uid": user_id},
            )
        else:
            rows = await self._fetchall(
                text("SELECT * FROM custom_scenes ORDER BY created_at DESC"),
            )
        for r in rows:
            if r.get("params"):
                r["params"] = _json_loads(r["params"])
        return rows

    async def update_custom_scene(self, scene_id: str, **kwargs) -> dict | None:
        await self._ensure_custom_scenes_table()
        from sqlalchemy import text
        if "params" in kwargs and isinstance(kwargs["params"], dict):
            kwargs["params"] = _json_dumps(kwargs["params"])
        now = _now()
        kwargs["updated_at"] = now
        set_clause = ", ".join(f"{k} = :{k}" for k in kwargs)
        values = {**kwargs, "id": scene_id}
        await self._execute(
            text(f"UPDATE custom_scenes SET {set_clause} WHERE id = :id"), values,
        )
        return await self.get_custom_scene(scene_id)
