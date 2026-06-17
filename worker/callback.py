"""Worker callback endpoint — receives status updates from workers.

Handles the FreeCAD → Blender two-stage pipeline handoff transparently:
when a freecad-worker completes CAD generation on a template-based task,
the server automatically re-queues it for the blender-worker to render."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from api.ws import broadcast
from core.config import settings
from core.scenes import get_scene
from models.schemas import TaskStatus, WorkerCallback

logger = logging.getLogger("blenderserver.worker")

router = APIRouter(prefix="/worker/callback", tags=["worker"])


async def _handoff_freecad_to_blender(
    task_id: str, task: dict, body: WorkerCallback, request: Request, tm,
) -> dict:
    """Transition a template-based task from FreeCAD stage to Blender render stage.

    The freecad-worker has generated the OBJ and stored it at ``body.result_url``.
    This function updates the task's ``intent_json`` to point at the generated OBJ
    as ``model_path``, removes the ``template_id``, re-queues the task, and
    publishes a fresh queue message for the blender-worker to claim.
    """
    intent = task["intent_json"]
    if isinstance(intent, str):
        intent = json.loads(intent)

    # Point blender-worker at the generated OBJ
    # Translate freecad-worker's local path to shared volume path
    obj_path = body.result_url or ""
    if obj_path.startswith("/output/"):
        obj_path = "/freecad_output/" + obj_path[len("/output/"):]
    intent["model_path"] = obj_path
    # Save template_id to model_id before removing so UI can show it
    orig_template_id = intent.get("template_id", "")
    orig_template_params = intent.get("template_params", {})
    intent.pop("template_id", None)
    intent.pop("template_params", None)

    # Merge scene params into intent so blender-worker has proper render config
    scene_id = intent.get("scene_id") or "studio_champagne"
    scene = get_scene(scene_id)
    if scene:
        for k, v in scene.params.items():
            if k not in intent or intent.get(k) is None or intent.get(k) == [] or intent.get(k) == "":
                intent[k] = v
        if not intent.get("scene_name"):
            intent["scene_name"] = scene.name
        # Ensure camera_styles is a non-empty list
        camera = intent.get("camera_style") or "three_quarter"
        intent["camera_styles"] = [camera]
        # Force generic category for demo templates
        intent["product_category"] = "generic"

    # Re-queue for the blender stage
    q = getattr(request.app.state, "queue", None)
    if q:
        await q.publish(task_id, intent)

    await tm.db.update_task_status(
        task_id, TaskStatus.queued,
        model_id=orig_template_id,
        intent_json=intent,
        stage_name="blender",
        stage_progress=0.0,
        progress=0.5,
        progress_message="CAD 生成完成，进入渲染阶段...",
    )

    # Notify UI
    await broadcast(task_id, {
        "type": "status",
        "status": "queued",
        "progress": 0.5,
        "message": "CAD 生成完成，进入渲染阶段...",
        "stage_name": "blender",
    })

    logger.info("Task %s: FreeCAD → Blender handoff (OBJ: %s)", task_id, body.result_url)
    return {"ok": True, "handoff": "freecad_to_blender"}


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

    # ── FreeCAD → Blender pipeline handoff ──
    # When a freecad-worker completes a template-based task with a result_url
    # (the generated OBJ path), intercept before marking as completed and
    # re-queue for the blender-worker to render.
    if (
        body.status == TaskStatus.completed
        and body.result_url
        and task.get("intent_json")
    ):
        intent = task["intent_json"]
        if isinstance(intent, str):
            intent = json.loads(intent)
        if intent.get("template_id"):
            return await _handoff_freecad_to_blender(task_id, task, body, request, tm)

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
