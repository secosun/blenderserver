"""Admin API — user management, quota management, audit log, dead-letter."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from api.deps import get_current_user
from models.schemas import UserResponse

router = APIRouter(prefix="/admin", tags=["admin"])


async def _require_admin(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ── User management ──────────────────────────────────────────────────


class UpdateUserQuotaBody(BaseModel):
    quota_concurrency: int | None = Field(None, ge=1, le=32)
    quota_max_resolution: int | None = Field(None, ge=512, le=16384)
    quota_max_samples: int | None = Field(None, ge=16, le=10000)


class UpdateUserBody(BaseModel):
    display_name: str | None = None
    role: str | None = Field(None, pattern="^(admin|user|viewer)$")
    is_active: bool | None = None


@router.get("/users")
async def list_users(
    request: Request,
    _admin: Annotated[dict, Depends(_require_admin)],
    limit: int = 100,
    offset: int = 0,
):
    """List all users (admin only)."""
    users = await request.app.state.task_manager.db.list_users(limit, offset)
    return {"users": [UserResponse(
        id=u["id"], email=u["email"], display_name=u.get("display_name", ""),
        role=u.get("role", "user"),
        quota_concurrency=u.get("quota_concurrency", 2),
        quota_max_resolution=u.get("quota_max_resolution", 4096),
        quota_max_samples=u.get("quota_max_samples", 512),
        is_active=bool(u.get("is_active", True)),
        created_at=u["created_at"], updated_at=u["updated_at"],
    ) for u in users]}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserBody,
    request: Request,
    _admin: Annotated[dict, Depends(_require_admin)],
):
    """Update user details (admin only)."""
    db = request.app.state.task_manager.db
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    kwargs = {}
    if body.display_name is not None:
        kwargs["display_name"] = body.display_name
    if body.role is not None:
        kwargs["role"] = body.role
    if body.is_active is not None:
        kwargs["is_active"] = body.is_active

    if kwargs:
        await db.update_user(user_id, **kwargs)

    await db.add_audit_log(
        user_id=_admin["id"], action="update_user",
        resource_type="user", resource_id=user_id,
        details=str(kwargs),
    )
    return {"ok": True}


@router.patch("/users/{user_id}/quota")
async def update_user_quota(
    user_id: str,
    body: UpdateUserQuotaBody,
    request: Request,
    _admin: Annotated[dict, Depends(_require_admin)],
):
    """Update user resource quotas (admin only)."""
    db = request.app.state.task_manager.db
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    kwargs = {}
    if body.quota_concurrency is not None:
        kwargs["quota_concurrency"] = body.quota_concurrency
    if body.quota_max_resolution is not None:
        kwargs["quota_max_resolution"] = body.quota_max_resolution
    if body.quota_max_samples is not None:
        kwargs["quota_max_samples"] = body.quota_max_samples

    if kwargs:
        await db.update_user(user_id, **kwargs)

    await db.add_audit_log(
        user_id=_admin["id"], action="update_user_quota",
        resource_type="user", resource_id=user_id,
        details=str(kwargs),
    )
    return {"ok": True}


# ── Plan management ──────────────────────────────────────────────────


@router.get("/plans")
async def list_plans(
    request: Request,
    _admin: Annotated[dict, Depends(_require_admin)],
):
    """List all subscription plans (admin only)."""
    plans = await request.app.state.task_manager.db.list_plans(public_only=False)
    return {"plans": plans}


@router.post("/plans", status_code=201)
async def create_plan(
    body: dict,
    request: Request,
    _admin: Annotated[dict, Depends(_require_admin)],
):
    """Create a new subscription plan (admin only)."""
    db = request.app.state.task_manager.db
    plan = await db.create_plan(
        name=body.get("name", ""),
        slug=body.get("slug", ""),
        description=body.get("description", ""),
        price_monthly_cents=body.get("price_monthly_cents", 0),
        price_yearly_cents=body.get("price_yearly_cents", 0),
        stripe_monthly_price_id=body.get("stripe_monthly_price_id"),
        stripe_yearly_price_id=body.get("stripe_yearly_price_id"),
        features=body.get("features", {}),
        is_public=body.get("is_public", True),
        sort_order=body.get("sort_order", 0),
    )
    return plan


@router.patch("/plans/{plan_id}")
async def update_plan(
    plan_id: str,
    body: dict,
    request: Request,
    _admin: Annotated[dict, Depends(_require_admin)],
):
    """Update a subscription plan (admin only)."""
    db = request.app.state.task_manager.db
    plan = await db.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    kwargs = {k: v for k, v in body.items() if v is not None}
    if kwargs:
        plan = await db.update_plan(plan_id, **kwargs)
    return plan


# ── Audit log ────────────────────────────────────────────────────────


@router.get("/audit-log")
async def get_audit_log(
    request: Request,
    _admin: Annotated[dict, Depends(_require_admin)],
    limit: int = 100,
    offset: int = 0,
):
    """View the audit log (admin only)."""
    logs = await request.app.state.task_manager.db.list_audit_logs(limit, offset)
    return {"logs": logs}


# ── Dead letter management ──────────────────────────────────────────


@router.get("/dead-letter")
async def list_dead_letter(
    request: Request,
    _admin: Annotated[dict, Depends(_require_admin)],
):
    """List dead-letter queue entries."""
    q = getattr(request.app.state, "queue", None)
    if q is None:
        raise HTTPException(status_code=503, detail="Queue not available")
    try:
        count = await q.dead_letter_count()
    except Exception:
        count = 0
    return {"dead_letter_count": count}


@router.post("/dead-letter/replay")
async def replay_dead_letter(
    request: Request,
    _admin: Annotated[dict, Depends(_require_admin)],
):
    """Replay all dead-letter messages back to the pending queue.

    This is a best-effort operation — only InMemoryQueue supports it.
    """
    q = getattr(request.app.state, "queue", None)
    if q is None:
        raise HTTPException(status_code=503, detail="Queue not available")

    replayed = 0
    if hasattr(q, "_dead"):
        msgs = list(q._dead)
        q._dead.clear()
        for msg in msgs:
            await q.publish(msg.task_id, msg.intent)
            replayed += 1

    await request.app.state.task_manager.db.add_audit_log(
        user_id=_admin["id"], action="replay_dead_letter",
        details=f"replayed={replayed}",
    )
    return {"replayed": replayed}


# ── System status ────────────────────────────────────────────────────


@router.get("/status")
async def admin_status(
    request: Request,
    _admin: Annotated[dict, Depends(_require_admin)],
):
    """Overall system status for admins."""
    db = request.app.state.task_manager.db
    q = getattr(request.app.state, "queue", None)

    status_data = {
        "queue_backend": getattr(q, "__class__", None),
        "database": "postgresql" if not db._is_sqlite else "sqlite",
    }

    # Task counts by status
    for s in ("pending", "ready", "queued", "running", "completed", "failed", "cancelled"):
        try:
            status_data[f"tasks_{s}"] = await db.count_tasks_by_status("", s)
        except Exception:
            pass

    # Workers
    try:
        idle = await db.count_workers_by_status("idle")
        busy = await db.count_workers_by_status("busy")
        offline = await db.count_workers_by_status("offline")
        status_data["workers_idle"] = idle
        status_data["workers_busy"] = busy
        status_data["workers_offline"] = offline
        status_data["workers_total"] = idle + busy + offline
    except Exception:
        pass

    # Queue depth
    if q:
        try:
            status_data["queue_pending"] = await q.pending_count()
            status_data["queue_dead"] = await q.dead_letter_count()
        except Exception:
            pass

    return status_data


@router.get("/stats")
async def admin_stats(
    request: Request,
    _admin: Annotated[dict, Depends(_require_admin)],
):
    """Usage statistics for admin dashboard."""
    from sqlalchemy import text
    from datetime import datetime, timezone, timedelta

    db = request.app.state.task_manager.db

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m")

    # Tasks completed this month
    monthly_tasks = 0
    try:
        row = await db._fetchone(
            text("SELECT COUNT(*) as cnt FROM tasks WHERE status = 'completed' AND created_at LIKE :prefix"),
            {"prefix": f"{today}%"},
        )
        monthly_tasks = row["cnt"] if row else 0
    except Exception:
        pass

    # Total users
    total_users = 0
    try:
        row = await db._fetchone(text("SELECT COUNT(*) as cnt FROM users"))
        total_users = row["cnt"] if row else 0
    except Exception:
        pass

    # Active subscriptions (non-free)
    paid_subs = 0
    try:
        row = await db._fetchone(
            text("SELECT COUNT(*) as cnt FROM subscriptions s JOIN subscription_plans p ON s.plan_id = p.id WHERE s.status = 'active' AND p.price_monthly_cents > 0"),
        )
        paid_subs = row["cnt"] if row else 0
    except Exception:
        pass

    # Tasks per day (last 30 days)
    daily_tasks = []
    try:
        for i in range(30, -1, -1):
            day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            row = await db._fetchone(
                text("SELECT COUNT(*) as cnt FROM tasks WHERE status = 'completed' AND created_at LIKE :prefix"),
                {"prefix": f"{day}%"},
            )
            daily_tasks.append({"date": day, "count": row["cnt"] if row else 0})
    except Exception:
        pass

    # Revenue estimate
    estimated_revenue = paid_subs * 199  # rough estimate based on avg plan price

    return {
        "total_users": total_users,
        "monthly_tasks": monthly_tasks,
        "paid_subscriptions": paid_subs,
        "estimated_monthly_revenue": estimated_revenue,
        "daily_tasks": daily_tasks,
    }
