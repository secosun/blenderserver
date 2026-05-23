from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File

from api.deps import get_current_user
from core.config import settings
from core.scenes import list_scenes
from core.storage import get_storage
from core.task_manager import TaskManager
from models.schemas import (
    TaskCreate, TaskResponse, TaskListResponse, TaskStatus,
    ModelUploadResponse,
)

router = APIRouter(tags=["api"])


def _tm(request: Request) -> TaskManager:
    return request.app.state.task_manager


ALLOWED_EXTENSIONS = {".fcstd", ".obj", ".stl", ".step", ".stp", ".fbx", ".glb", ".blend"}


@router.post("/upload", response_model=ModelUploadResponse)
async def upload_model(
    request: Request,
    file: UploadFile = File(...),
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    ext = Path(file.filename or "model.obj").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    model_id = str(uuid.uuid4())
    content = await file.read()
    storage = get_storage()

    storage_key = f"{current_user['id']}/{model_id}/{file.filename or f'model{ext}'}"
    url = await storage.upload_bytes(content, storage_key)

    return ModelUploadResponse(
        model_id=model_id,
        file_name=file.filename or f"model{ext}",
        file_size=len(content),
        file_type=ext.lstrip("."),
        storage_path=storage_key,
        upload_time=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/scenes")
async def get_scenes():
    return {"scenes": list_scenes()}


@router.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    body: TaskCreate,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    if not body.has_scene and not body.has_prompt:
        raise HTTPException(status_code=400, detail="Provide either scene_id, prompt, or both")

    tm = _tm(request)
    storage_path = f"{current_user['id']}/{body.model_id}"

    task = await tm.create_task(
        model_id=body.model_id,
        prompt=body.prompt or "",
        user_id=current_user["id"],
        scene_id=body.scene_id,
        storage_path=storage_path,
        camera_styles=body.camera_styles,
        name=body.name,
        output_format=body.output_format,
    )

    task = await tm.build_intent(task["id"])
    return task


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)] = None,
    limit: int = 50,
    offset: int = 0,
):
    tasks, total = await _tm(request).list_tasks(current_user["id"], limit, offset)
    return TaskListResponse(tasks=tasks, total=total)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    task = await _tm(request).get_task(task_id)
    if not task or task["user_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/tasks/{task_id}/dispatch", response_model=TaskResponse)
async def dispatch_task(
    task_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    tm = _tm(request)
    task = await tm.get_task(task_id)
    if not task or task["user_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Task not found")

    # Enforce user quota
    intent = task.get("intent_json") or {}
    rx = int(intent.get("resolution_x", 2048))
    ry = int(intent.get("resolution_y", 2048))
    sp = int(intent.get("samples", 320))

    user = await tm.db.get_user(current_user["id"])
    if user:
        max_res = user.get("quota_max_resolution", 4096)
        max_samples = user.get("quota_max_samples", 512)
        if rx > max_res or ry > max_res:
            raise HTTPException(status_code=429, detail=f"Resolution {rx}x{ry} exceeds quota (max {max_res})")
        if sp > max_samples:
            raise HTTPException(status_code=429, detail=f"Samples {sp} exceeds quota (max {max_samples})")

        running = await tm.db.count_tasks_by_status(current_user["id"], "running")
        max_conc = user.get("quota_concurrency", 2)
        if running >= max_conc:
            raise HTTPException(status_code=429, detail=f"Concurrency limit reached ({running}/{max_conc})")

    # Check worker pool capacity
    await tm.db.cleanup_stale_workers()
    registered = await tm.db.count_workers_by_status()
    if registered > 0:
        capacity = await tm.db.get_available_capacity()
        if capacity < 1:
            raise HTTPException(status_code=503, detail="No workers available — try again later")

    try:
        task = await tm.dispatch_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if task["intent_json"]:
        await request.app.state.queue.publish(task_id, task["intent_json"])

    return task


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    tm = _tm(request)
    task = await tm.get_task(task_id)
    if not task or task["user_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] in (TaskStatus.completed.value, TaskStatus.failed.value):
        raise HTTPException(status_code=400, detail="Cannot cancel a completed or failed task")

    await tm.db.add_audit_log(
        user_id=current_user["id"], action="cancel_task",
        resource_type="task", resource_id=task_id,
    )
    return await tm.update_progress(task_id, TaskStatus.cancelled, message="Cancelled by user")


@router.post("/tasks/{task_id}/clone", response_model=TaskResponse, status_code=201)
async def clone_task(
    task_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    """Clone an existing task — create a new task with the same config."""
    tm = _tm(request)
    original = await tm.get_task(task_id)
    if not original or original["user_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Task not found")

    intent = original.get("intent_json") or {}
    camera_styles = intent.get("camera_styles")
    output_format = intent.get("output_format")

    task = await tm.create_task(
        model_id=original["model_id"],
        prompt=original.get("prompt", ""),
        user_id=current_user["id"],
        scene_id=original.get("scene_id"),
        storage_path=original.get("storage_path", ""),
        camera_styles=camera_styles,
        name=f"{original.get('name') or original['id'][:8]} (副本)",
        output_format=output_format,
    )

    task = await tm.build_intent(task["id"])
    return task


@router.post("/tasks/claim-next", response_model=TaskResponse)
async def claim_next_task(request: Request):
    task = await _tm(request).claim_next_task()
    if not task:
        raise HTTPException(status_code=404, detail="No queued tasks")
    return task


@router.get("/tasks/{task_id}/result", response_model=dict)
async def get_task_result(
    task_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    tm = _tm(request)
    task = await tm.get_task(task_id)
    if not task or task["user_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] != TaskStatus.completed.value:
        raise HTTPException(status_code=400, detail=f"Task not completed (status: {task['status']})")
    return {
        "task_id": task_id,
        "result_url": task.get("result_url"),
        "intent": task.get("intent_json"),
    }
