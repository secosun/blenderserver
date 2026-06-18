"""Legacy scenes module — re-exports from scene engine API.

Kept for backward compatibility. All existing imports still work.
New code should use ``api/scenes_engine.py`` directly.
"""

from __future__ import annotations

from typing import Any

from api.scenes_engine import SCENES as _ENGINE_SCENES


def list_scenes() -> list[dict[str, Any]]:
    """Return all scene presets (legacy format: id/name/description/params)."""
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "description": s["description"],
            "params": {},
        }
        for s in _ENGINE_SCENES.values()
    ]


def get_scene(scene_id: str) -> dict[str, Any] | None:
    """Return a single scene preset, or None."""
    s = _ENGINE_SCENES.get(scene_id)
    if not s:
        return None
    return {
        "id": s["id"],
        "name": s["name"],
        "description": s["description"],
        "params": {},
    }
