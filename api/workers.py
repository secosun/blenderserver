"""Worker pool API — registration, heartbeat, status listing."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from core.db import AsyncDatabase

logger = logging.getLogger("blenderserver.worker.routes")

router = APIRouter(prefix="/workers", tags=["workers"])


class WorkerRegisterBody(BaseModel):
    label: str = Field("", max_length=200)
    hostname: str = Field("", max_length=200)
    gpu_device: str = Field("", max_length=200)
    concurrency: int = Field(1, ge=1, le=8)


def _db(request: Request) -> AsyncDatabase:
    return request.app.state.task_manager.db


@router.post("/register", status_code=201)
async def register(body: WorkerRegisterBody, request: Request):
    """Register a new worker instance."""
    worker = await _db(request).register_worker(
        label=body.label,
        hostname=body.hostname,
        gpu_device=body.gpu_device,
        concurrency=body.concurrency,
    )
    logger.info("Worker registered: %s (%s)", worker["id"], body.hostname)
    return worker


@router.post("/{worker_id}/heartbeat")
async def worker_heartbeat(worker_id: str, request: Request):
    """Update worker heartbeat timestamp."""
    db = _db(request)
    worker = await db.worker_heartbeat(worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found or offline")
    await db.cleanup_stale_workers()
    return {"status": "ok", "worker_id": worker_id, "status_label": worker["status"]}


@router.get("")
async def list_all_workers(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)] = None,
    status: str | None = None,
):
    """List all registered workers. Admin only."""
    if current_user.get("role") not in ("admin",):
        raise HTTPException(status_code=403, detail="Admin access required")
    db = _db(request)
    workers = await db.list_workers(status=status)
    capacity = await db.get_available_capacity()
    return {"workers": workers, "available_capacity": capacity}


@router.get("/capacity")
async def capacity(request: Request):
    """Return the current available worker capacity."""
    db = _db(request)
    await db.cleanup_stale_workers()
    return {"available_capacity": await db.get_available_capacity()}
