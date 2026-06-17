"""Scene management API — CRUD for user-managed custom scenes.

System scenes (from core/scenes.py) are read-only and seeded at startup.
Users can create, update, and delete their own custom scenes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import get_current_user
from core.scenes import list_scenes, get_scene

router = APIRouter(prefix="/scenes/manage", tags=["scenes"])


def _db(request: Request):
    return request.app.state.task_manager.db


@router.get("")
async def list_all_scenes(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)] | None = None,
):
    """List all scenes — system presets + user custom scenes."""
    # System scenes (hardcoded)
    system_scenes = []
    for s in list_scenes():
        system_scenes.append({
            "id": s["id"],
            "name": s["name"],
            "description": s["description"],
            "category": "studio",
            "params": {},
            "is_system": True,
            "is_public": True,
            "thumbnail_url": None,
            "created_at": "",
            "updated_at": "",
        })

    # User custom scenes from DB
    db = _db(request)
    custom = []
    if current_user:
        custom = await db.list_custom_scenes(current_user["id"])

    return {"scenes": system_scenes + custom}


@router.post("", status_code=201)
async def create_custom_scene(
    body: dict,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Create a custom scene preset."""
    name = body.get("name", "Untitled Scene")
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name is required")

    db = _db(request)
    scene = await db.create_custom_scene(
        user_id=current_user["id"],
        name=name,
        description=body.get("description", ""),
        category=body.get("category", "custom"),
        params=body.get("params", {}),
        is_public=body.get("is_public", False),
        thumbnail_url=body.get("thumbnail_url"),
    )
    return scene


@router.patch("/{scene_id}")
async def update_custom_scene(
    scene_id: str,
    body: dict,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Update a custom scene."""
    db = _db(request)
    scene = await db.get_custom_scene(scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")
    if scene["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not your scene")

    kwargs = {}
    for key in ("name", "description", "category", "is_public", "thumbnail_url"):
        if key in body:
            kwargs[key] = body[key]
    if "params" in body and isinstance(body["params"], dict):
        kwargs["params"] = body["params"]

    if kwargs:
        scene = await db.update_custom_scene(scene_id, **kwargs)
    return scene


@router.delete("/{scene_id}")
async def delete_custom_scene(
    scene_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Delete a custom scene."""
    db = _db(request)
    scene = await db.get_custom_scene(scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")
    if scene["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not your scene")

    from sqlalchemy import text
    await db._execute(
        text("DELETE FROM custom_scenes WHERE id = :id"),
        {"id": scene_id},
    )
    return {"ok": True}
