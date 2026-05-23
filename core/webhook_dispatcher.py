"""Webhook event dispatcher — HMAC-signed delivery to user-configured URLs."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from core.config import settings
from core.db import AsyncDatabase

logger = logging.getLogger("blenderserver.webhooks")

# Maps TaskStatus enum values → webhook event name strings
WEBHOOK_EVENTS = {
    "completed": "task.completed",
    "failed": "task.failed",
    "running": "task.progress",
}


async def dispatch_webhooks(db: AsyncDatabase, event_type: str, payload: dict):
    """Fire webhooks for a given event type.

    Fires asynchronously — callers should use ``asyncio.ensure_future()``.
    """
    if not event_type:
        return

    # For progress events, only fire at milestone percentages (every 10%)
    # to avoid flooding webhook receivers
    if event_type == "task.progress":
        progress = payload.get("progress", 0)
        # Fire at 0%, 10%, 20%, ..., 100%
        milestone = round(progress * 10) / 10
        if abs(progress - milestone) > 0.03:
            return
        # Don't fire on every small progress update
        if payload.get("_last_milestone") == milestone:
            return
        payload["_last_milestone"] = milestone

    webhooks = await db.get_active_webhooks_for_event(event_type)
    if not webhooks:
        return

    body_bytes = json.dumps({
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }, ensure_ascii=False).encode("utf-8")

    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed — webhook delivery disabled")
        return

    async with httpx.AsyncClient(timeout=settings.webhook_delivery_timeout) as client:
        for wh in webhooks:
            secret = wh.get("secret", "").encode("utf-8")
            signature = hmac.new(secret, body_bytes, hashlib.sha256).hexdigest()

            try:
                response = await client.post(
                    wh["url"],
                    content=body_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Signature": f"sha256={signature}",
                        "User-Agent": "CADRender-Webhook/1.0",
                    },
                )
                if response.is_success:
                    logger.debug("Webhook delivered to %s [%d]", wh["url"], response.status_code)
                else:
                    logger.warning(
                        "Webhook delivery to %s returned %d",
                        wh["url"], response.status_code,
                    )
            except Exception as e:
                logger.warning("Webhook delivery failed: %s to %s", e, wh["url"])
