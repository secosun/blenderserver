"""Material preview API — render sphere with finish + texture combination."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger("blenderserver.preview")

router = APIRouter(prefix="/preview", tags=["preview"])

_PREVIEW_DIR = Path("/app/outputs/preview")
_BLENDERWORKER_SRC = Path("/blenderworker_src")


def _import_calibrate():
    """Import material_calibrate modules (mounted from blenderworker)."""
    import sys as _sys
    if str(_BLENDERWORKER_SRC) not in _sys.path:
        _sys.path.insert(0, str(_BLENDERWORKER_SRC))
    import transport.blender_client as _bc
    import orchestration.material_calibrate as _mc
    import orchestration.material_calibrate_phases as _phases
    return _bc, _mc, _phases


@router.get("/render")
async def render_preview(
    finish_id: str,
    texture_profile_id: str = "",
    samples: int = 128,
):
    """Render a sphere preview with given finish + texture combination."""
    _PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    out_path = str(_PREVIEW_DIR / f"{finish_id}_{texture_profile_id or 'notexture'}_{token}.png")

    # Read finish config
    finish_file = Path("/blender_mcp_presets/finishes") / f"{finish_id}.json"
    if not finish_file.is_file():
        raise HTTPException(status_code=404, detail=f"Finish '{finish_id}' not found")
    with open(finish_file, encoding="utf-8") as f:
        finish_cfg = json.load(f)

    # Apply texture profile if specified
    if texture_profile_id:
        finish_cfg["texture_profile"] = texture_profile_id

    try:
        _bc, _mc, _phases = _import_calibrate()
        finish_cfg = _phases.resolve_texture_profile_bakecoat(finish_cfg)
        finish_cfg = _mc._enrich_finish_cfg(finish_cfg, finish_id)

        client = _bc.BlenderTCPClient("host.docker.internal", 19876, timeout=120)
        client.connect()
        try:
            _mc._setup_sphere_scene(client, finish_cfg)
            _mc._apply_finish_bakecoat(client, finish_cfg)

            p = finish_cfg.get("principled") or {}
            _mc._set_sphere_material_params(
                client, p.get("base_color", [0.5, 0.5, 0.5, 1.0]),
                roughness=float(p.get("roughness", 0.5)),
                metallic=float(p.get("metallic", 0.0)),
                specular=float(p.get("specular_ior_level", 0.5)),
                coat_weight=float(p.get("coat_weight", 0.0)),
                coat_roughness=float(p.get("coat_roughness", 0.3)),
                bump_mult=1.0,
                base_bump_strength=0.02,
                anisotropic=float(p.get("anisotropic", 0.0)),
                anisotropic_rotation=float(p.get("anisotropic_rotation", 0.0)),
            )
            _mc._render_sphere(client, out_path, samples=samples)
        finally:
            client.disconnect()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Render failed: {e}")

    if not os.path.isfile(out_path):
        raise HTTPException(status_code=500, detail="Render produced no output")

    return {
        "ok": True,
        "image_url": f"/api/preview/image/{os.path.basename(out_path)}",
        "finish_id": finish_id,
        "texture_profile_id": texture_profile_id or None,
    }


@router.get("/image/{filename}")
async def get_preview(filename: str):
    """Return a rendered preview image."""
    img_path = _PREVIEW_DIR / filename
    if not img_path.is_file():
        raise HTTPException(status_code=404, detail="Preview not found")
    return FileResponse(str(img_path), media_type="image/png")
