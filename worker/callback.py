"""Worker callback endpoint — receives status updates from blenderworker."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from api.ws import broadcast
from core.config import settings
from models.schemas import TaskStatus, WorkerCallback

logger = logging.getLogger("blenderserver.worker")

router = APIRouter(prefix="/worker/callback", tags=["worker"])


@router.post("/{task_id}")
async def worker_callback(task_id: str, body: WorkerCallback, request: Request):
    """Receive progress/status updates from a blenderworker instance.

    The worker must include the correct ``secret`` matching the server's
    ``WORKER_CALLBACK_SECRET``.
    """
    # Authenticate
    if body.secret != settings.worker_callback_secret:
        raise HTTPException(status_code=403, detail="Invalid callback secret")

    tm = request.app.state.task_manager
    task = await tm.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Validate state transition
    valid_transitions = {
        TaskStatus.queued: [TaskStatus.running, TaskStatus.failed],
        TaskStatus.running: [TaskStatus.running, TaskStatus.completed, TaskStatus.failed],
    }
    current = TaskStatus(task["status"])
    allowed = valid_transitions.get(current, [])
    if body.status not in allowed and body.status != current:
        logger.warning(
            "Invalid transition %s -> %s for task %s",
            current, body.status, task_id,
        )

    # Handle auto-retry on failure
    if body.status == TaskStatus.failed:
        retries = task.get("retry_count", 0) or 0
        if retries < settings.max_task_retries:
            new_retries = retries + 1
            await tm.db.update_task_status(
                task_id, TaskStatus.ready,
                retry_count=new_retries,
                error_message=f"Retry {new_retries}/{settings.max_task_retries}: {body.error_message or 'Unknown error'}",
            )
            # Re-publish to queue
            intent = task.get("intent_json")
            if intent:
                q = getattr(request.app.state, "queue", None)
                if q:
                    await q.publish(task_id, intent)
            logger.info("Task %s auto-retrying (%d/%d)", task_id, new_retries, settings.max_task_retries)

            # WebSocket broadcast
            await broadcast(task_id, {
                "type": "status",
                "status": "ready",
                "progress": 0,
                "message": f"自动重试 ({new_retries}/{settings.max_task_retries})",
                "error_message": body.error_message,
            })

            return {"ok": True, "retry": True}

    # Persist update with all new fields
    task = await tm.update_progress(
        task_id,
        body.status,
        progress=body.progress,
        message=body.message,
        result_url=body.result_url,
        error_message=body.error_message,
        result_urls=body.result_urls,
        stage_name=body.stage_name,
        stage_progress=body.stage_progress,
        eta_seconds=body.eta_seconds,
    )

    # Push to WebSocket clients
    ws_payload = {
        "type": "status",
        "status": body.status.value,
        "progress": body.progress,
        "message": body.message,
        "result_url": body.result_url,
        "error_message": body.error_message,
    }
    if body.result_urls:
        ws_payload["result_urls"] = body.result_urls
    if body.stage_name:
        ws_payload["stage_name"] = body.stage_name
    if body.stage_progress is not None:
        ws_payload["stage_progress"] = body.stage_progress
    if body.eta_seconds is not None:
        ws_payload["eta_seconds"] = body.eta_seconds

    await broadcast(task_id, ws_payload)

    # Fire webhooks asynchronously
    try:
        from core.webhook_dispatcher import WEBHOOK_EVENTS, dispatch_webhooks
        event_name = WEBHOOK_EVENTS.get(body.status)
        if event_name:
            import asyncio
            asyncio.ensure_future(dispatch_webhooks(
                tm.db, event_name,
                {
                    "task_id": task_id,
                    "status": body.status.value,
                    "progress": body.progress,
                    "message": body.message,
                    "result_url": body.result_url,
                    "result_urls": body.result_urls,
                    "error_message": body.error_message,
                    "stage_name": body.stage_name,
                    "eta_seconds": body.eta_seconds,
                },
            ))
    except Exception:
        logger.exception("Webhook dispatch failed for task %s", task_id)

    logger.info("Task %s: %s (%.0f%%) — %s", task_id, body.status.value, body.progress * 100, body.message)
    return {"ok": True}
