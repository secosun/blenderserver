"""Scene engine API — list and inspect visual scene definitions."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends

from api.deps import require_admin
from core.scene_engine import list_scenes, get_scene, resolve_scene_commands

logger = logging.getLogger("blenderserver.scenes_engine")

router = APIRouter(prefix="/scenes-engine", tags=["scenes-engine"])


@router.get("")
async def list_all_scenes():
    """List all registered visual scenes with metadata."""
    return {"scenes": list_scenes(), "total": len(list_scenes())}


@router.get("/{scene_id}")
async def get_scene_detail(scene_id: str):
    """Get full definition of a scene."""
    scene = get_scene(scene_id)
    if not scene:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")
    return scene


@router.get("/{scene_id}/commands")
async def get_scene_commands(
    scene_id: str,
    product_category: str = "generic",
    bbox_size: float = 1.0,
):
    """Preview the Blender commands a scene resolves to."""
    cmds = resolve_scene_commands(scene_id, product_category=product_category, bbox_size=bbox_size)
    return {"scene_id": scene_id, "commands": cmds, "total": len(cmds)}
