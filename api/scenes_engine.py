"""Scene engine API — list and inspect visual scene definitions."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("blenderserver.scenes_engine")

router = APIRouter(prefix="/scenes-engine", tags=["scenes-engine"])

# ── Scene definitions (mirrors blenderworker's core/scene_engine.py) ───────────

SCENES: dict[str, dict[str, Any]] = {}


def _scene(id: str, name: str, desc: str, tags: list[str]) -> dict[str, Any]:
    s = {"id": id, "name": name, "description": desc, "tags": tags, "lights": {}}
    SCENES[id] = s
    return s


_scene("studio_neutral", "标准影棚", "中性三点布光，白灰渐变背景。适用大多数产品。", ["studio", "通用"])
_scene("studio_high_key", "高调影棚", "高亮柔和照明，浅色背景。适合白色/浅色产品。", ["studio", "亮色"])
_scene("studio_dark", "低调影棚", "强对比暗调，轮廓光突出。适合深色/金属产品。", ["studio", "深色", "金属"])
_scene("studio_soft", "柔光影棚", "大面积柔光箱，阴影柔和。适合曲面/反光产品。", ["studio", "柔光"])
_scene("outdoor_overcast", "阴天户外", "均匀漫射光，模拟多云天气自然光效果。", ["outdoor", "自然光"])
_scene("outdoor_sunset", "日落暖光", "暖色调侧光，长阴影。模拟傍晚金色阳光。", ["outdoor", "暖色"])


@router.get("")
async def list_all_scenes():
    """List all registered visual scenes."""
    return {
        "scenes": [
            {"id": s["id"], "name": s["name"], "description": s["description"], "tags": s["tags"]}
            for s in SCENES.values()
        ],
        "total": len(SCENES),
    }


@router.get("/{scene_id}")
async def get_scene_detail(scene_id: str):
    """Get scene detail."""
    scene = SCENES.get(scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")
    return scene
