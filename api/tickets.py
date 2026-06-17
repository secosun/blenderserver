"""Support ticket system — user feedback & admin responses."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.deps import get_current_user, require_admin

logger = logging.getLogger("blenderserver.tickets")

router = APIRouter(prefix="/tickets", tags=["tickets"])

TICKETS_TABLE = "support_tickets"

_TICKETS_DDL = f"""
CREATE TABLE IF NOT EXISTS {TICKETS_TABLE} (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    subject VARCHAR(200) NOT NULL,
    message TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    admin_reply TEXT,
    created_at VARCHAR(32) NOT NULL,
    updated_at VARCHAR(32) NOT NULL
)
"""


async def _ensure_table(db):
    try:
        await db._fetchone(text(f"SELECT 1 FROM {TICKETS_TABLE} LIMIT 1"))
    except Exception:
        await db._execute(text(_TICKETS_DDL))
        logger.info("Created %s table", TICKETS_TABLE)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    import uuid
    return str(uuid.uuid4())


class TicketCreate(BaseModel):
    subject: str = Field(..., max_length=200)
    message: str = Field(..., max_length=2000)


class TicketReply(BaseModel):
    reply: str = Field(..., max_length=2000)


@router.post("")
async def create_ticket(
    body: TicketCreate,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Submit a support ticket."""
    db = request.app.state.task_manager.db
    await _ensure_table(db)
    tid = _uuid()
    now = _now()
    await db._execute(
        text(f"""INSERT INTO {TICKETS_TABLE} (id, user_id, subject, message, status, created_at, updated_at)
                VALUES (:id, :uid, :subj, :msg, 'open', :now, :now)"""),
        {"id": tid, "uid": current_user["id"], "subj": body.subject,
         "msg": body.message, "now": now},
    )
    logger.info("Ticket created: %s by user %s", tid, current_user["id"])
    return {"ok": True, "ticket_id": tid}


@router.get("")
async def list_my_tickets(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """List current user's tickets."""
    db = request.app.state.task_manager.db
    await _ensure_table(db)
    rows = await db._fetchall(
        text(f"SELECT * FROM {TICKETS_TABLE} WHERE user_id = :uid ORDER BY created_at DESC"),
        {"uid": current_user["id"]},
    )
    return {"tickets": rows}


@router.get("/admin")
async def list_all_tickets(
    request: Request,
    _admin: Annotated[dict, Depends(require_admin)],
):
    """List all tickets (admin only)."""
    db = request.app.state.task_manager.db
    await _ensure_table(db)
    rows = await db._fetchall(
        text(f"SELECT * FROM {TICKETS_TABLE} ORDER BY created_at DESC"),
    )
    return {"tickets": rows}


@router.post("/{ticket_id}/reply")
async def reply_ticket(
    ticket_id: str,
    body: TicketReply,
    request: Request,
    _admin: Annotated[dict, Depends(require_admin)],
):
    """Admin reply to a ticket."""
    db = request.app.state.task_manager.db
    await _ensure_table(db)
    now = _now()
    await db._execute(
        text(f"UPDATE {TICKETS_TABLE} SET admin_reply = :reply, status = 'closed', updated_at = :now WHERE id = :id"),
        {"reply": body.reply, "now": now, "id": ticket_id},
    )
    logger.info("Ticket %s closed by admin", ticket_id)
    return {"ok": True}
