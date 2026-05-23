"""Webhook management API — users can register webhook URLs for task events."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.deps import get_current_user
from core.db import AsyncDatabase
from models.schemas import (
    WebhookCreate,
    WebhookResponse,
    WebhookUpdate,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

VALID_EVENTS = {"task.completed", "task.failed", "task.progress"}


def _db(request: Request) -> AsyncDatabase:
    return request.app.state.task_manager.db


def _format_webhook(wh: dict) -> WebhookResponse:
    return WebhookResponse(
        id=wh["id"],
        user_id=wh["user_id"],
        url=wh["url"],
        events=wh.get("events", []),
        is_active=bool(wh.get("is_active", True)),
        created_at=wh["created_at"],
        updated_at=wh.get("updated_at", wh["created_at"]),
    )


@router.post("", response_model=WebhookResponse, status_code=201)
async def create_webhook(
    body: WebhookCreate,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Register a new webhook endpoint.

    The webhook will receive POST requests when subscribed events occur.
    Each request includes an ``X-Webhook-Signature`` header with HMAC-SHA256
    signature of the request body using the webhook's secret.
    """
    # Validate events
    invalid_events = set(body.events) - VALID_EVENTS
    if invalid_events:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid events: {invalid_events}. Valid: {VALID_EVENTS}",
        )

    db = _db(request)
    secret = body.secret or secrets.token_hex(32)

    wh = await db.create_webhook(
        user_id=current_user["id"],
        url=body.url,
        events=body.events,
        secret=secret,
    )
    return _format_webhook(wh)


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """List all webhooks for the current user."""
    db = _db(request)
    webhooks = await db.list_webhooks(current_user["id"])
    return [_format_webhook(wh) for wh in webhooks]


@router.get("/{webhook_id}", response_model=WebhookResponse)
async def get_webhook(
    webhook_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Get details of a specific webhook."""
    db = _db(request)
    wh = await db.get_webhook(webhook_id)
    if not wh or wh["user_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return _format_webhook(wh)


@router.patch("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: str,
    body: WebhookUpdate,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Update a webhook (URL, events, active status)."""
    db = _db(request)
    wh = await db.get_webhook(webhook_id)
    if not wh or wh["user_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Webhook not found")

    kwargs = {}
    if body.url is not None:
        kwargs["url"] = body.url
    if body.events is not None:
        invalid_events = set(body.events) - VALID_EVENTS
        if invalid_events:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid events: {invalid_events}",
            )
        kwargs["events"] = body.events
    if body.is_active is not None:
        kwargs["is_active"] = body.is_active

    if kwargs:
        wh = await db.update_webhook(webhook_id, current_user["id"], **kwargs)
    return _format_webhook(wh)


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Delete a webhook."""
    db = _db(request)
    ok = await db.delete_webhook(webhook_id, current_user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Webhook not found")
