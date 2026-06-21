"""Texture profiles API — list and inspect texture templates."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("blenderserver.texture_profiles")

router = APIRouter(prefix="/texture-profiles", tags=["texture-profiles"])

# Try multiple paths: Docker mount, dev host, and fallback
_REPO = Path(__file__).resolve().parent.parent.parent
_TEXTURE_CANDIDATES = [
    Path("/blender_mcp_presets/texture_profiles"),                          # Docker mount
    _REPO / "blenderworker" / "blender_mcp_presets" / "texture_profiles",   # Host dev (git submodule)
    _REPO / "blender_mcp_presets" / "texture_profiles",                      # Host dev (alt)
]
_TEXTURE_DIR = next((p for p in _TEXTURE_CANDIDATES if p.is_dir()), _TEXTURE_CANDIDATES[0])


def _load_all() -> list[dict]:
    if not _TEXTURE_DIR.is_dir():
        return []
    profiles = []
    for f in sorted(_TEXTURE_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["id"] = f.stem
            profiles.append(data)
        except Exception as e:
            logger.warning("Failed to load texture profile %s: %s", f.name, e)
    return profiles


@router.get("")
async def list_profiles():
    """List all texture profiles."""
    profiles = _load_all()
    return {"profiles": profiles, "total": len(profiles)}


@router.get("/{profile_id}")
async def get_profile(profile_id: str):
    """Get a single texture profile."""
    path = _TEXTURE_DIR / f"{profile_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Texture profile '{profile_id}' not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["id"] = profile_id
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read profile: {e}")
