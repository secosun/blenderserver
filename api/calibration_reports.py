"""Serve calibration report JSON + images for the calibration viewer."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger("blenderserver.calibration_reports")

router = APIRouter(prefix="/calibration-reports", tags=["calibration-reports"])

# Calibrate_out is mounted at /app/calibrate_out in the container
_CAL_DIR = Path("/app/calibrate_out")


def _material_dir(finish_id: str) -> Path | None:
    """Resolve material calibration output dir (flat or legacy nested layout)."""
    flat = _CAL_DIR / f"material_{finish_id}"
    if flat.is_dir() and (flat / "calibration_report.json").is_file():
        return flat
    nested = flat / f"material_{finish_id}"
    if nested.is_dir() and (nested / "calibration_report.json").is_file():
        return nested
    if flat.is_dir():
        return flat
    return None


@router.get("/{finish_id}")
async def get_report(finish_id: str):
    """Return calibration_report.json for a finish."""
    mat_dir = _material_dir(finish_id)
    if mat_dir is None:
        raise HTTPException(status_code=404, detail=f"No calibration report for '{finish_id}'")
    report_path = mat_dir / "calibration_report.json"
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read report: {e}")


@router.get("/{finish_id}/grid")
async def get_grid(finish_id: str):
    """Return 00_summary_grid.png for a finish."""
    mat_dir = _material_dir(finish_id)
    if mat_dir is None:
        raise HTTPException(status_code=404, detail=f"No summary grid for '{finish_id}'")
    grid_path = mat_dir / "00_summary_grid.png"
    if not grid_path.is_file():
        raise HTTPException(status_code=404, detail=f"No summary grid for '{finish_id}'")
    return FileResponse(str(grid_path), media_type="image/png")


@router.get("/{finish_id}/images/{filename}")
async def get_image(finish_id: str, filename: str):
    """Return any image from the calibration output directory."""
    mat_dir = _material_dir(finish_id)
    if mat_dir is None:
        raise HTTPException(status_code=404, detail=f"No calibration data for '{finish_id}'")
    img_path = mat_dir / filename
    if not img_path.is_file():
        raise HTTPException(status_code=404, detail=f"Image '{filename}' not found")
    ext = img_path.suffix.lower()
    media_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    return FileResponse(str(img_path), media_type=media_map.get(ext, "application/octet-stream"))


@router.get("/{finish_id}/trials")
async def list_trials(finish_id: str):
    """List individual trial images with scores."""
    mat_dir = _material_dir(finish_id)
    if mat_dir is None:
        raise HTTPException(status_code=404, detail=f"No calibration data for '{finish_id}'")

    report_path = mat_dir / "calibration_report.json"
    scores_map: dict[str, float] = {}
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if "trial_scores" in report:
                # Try to recover trial->filename mapping from confirm_stage
                cs = report.get("confirm_stage") or {}
                candidates = cs.get("candidates") or []
                for c in candidates:
                    trial_idx = int(c.get("source_trial", 0))
                    scores_map[f"confirm_t{trial_idx:03d}"] = c.get("confirm_score", 0)
        except Exception:
            pass

    images: list[dict] = []
    for f in sorted(mat_dir.glob("trial_*.png")):
        trial_id = f.stem
        score = scores_map.get(trial_id.replace("trial_", "confirm_t"))
        images.append({"filename": f.name, "trial_id": trial_id, "score": score})

    for f in sorted(mat_dir.glob("confirm_t*.png")):
        if not any(i["filename"] == f.name for i in images):
            images.append({"filename": f.name, "trial_id": f.stem, "score": None})

    return {"images": images, "total": len(images)}


@router.get("/{finish_id}/validation/{filename}")
async def get_validation_image(finish_id: str, filename: str):
    """Return validation image."""
    mat_dir = _material_dir(finish_id)
    if mat_dir is None:
        raise HTTPException(status_code=404)
    img_path = mat_dir / "validation" / filename
    if not img_path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(img_path), media_type="image/png")


import re
from datetime import datetime, timezone
from pydantic import BaseModel


class SelectTrialBody(BaseModel):
    filename: str


@router.post("/{finish_id}/select-trial")
async def select_trial(finish_id: str, body: SelectTrialBody):
    """Let human pick the best trial by eye and save its params to finish JSON.

    Parses roughness/metallic/specular from the trial filename pattern
    (``trial_NNN_rRRR_mMMM_sSSS.png``), reads coat/bump from the
    calibration report, and writes all params to the finish JSON file.
    """
    # Parse params from filename
    m = re.search(r"_r([\d.]+)_m([\d.]+)_s([\d.]+)", body.filename)
    if not m:
        raise HTTPException(status_code=400, detail=f"Cannot parse params from filename: {body.filename}")
    roughness = float(m.group(1))
    metallic = float(m.group(2))
    specular = float(m.group(3))

    # Read report for coat/bump params (use best values as reference)
    mat_dir = _material_dir(finish_id)
    report_path = (mat_dir / "calibration_report.json") if mat_dir else Path()
    coat_weight = 0.0
    coat_roughness = 0.3
    bump_mult = 1.0
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            best = report.get("best") or {}
            coat_weight = best.get("coat_weight", 0.0)
            coat_roughness = best.get("coat_roughness", 0.3)
            bump_mult = best.get("bump_mult", 1.0)
        except Exception:
            pass

    # Read finish JSON and update principled params
    finish_dir = _get_finish_dir()
    finish_path = finish_dir / f"{finish_id}.json"
    if not finish_path.is_file():
        raise HTTPException(status_code=404, detail=f"Finish file not found: {finish_id}.json")

    try:
        finish = json.loads(finish_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read finish file: {e}")

    if "principled" not in finish:
        finish["principled"] = {}

    old = dict(finish["principled"])
    finish["principled"]["roughness"] = roughness
    finish["principled"]["metallic"] = metallic
    finish["principled"]["specular_ior_level"] = specular
    finish["principled"]["coat_weight"] = coat_weight
    finish["principled"]["coat_roughness"] = coat_roughness

    if abs(bump_mult - 1.0) > 0.01:
        bakecoat = finish.setdefault("bakecoat_procedural", {})
        bump = bakecoat.setdefault("bump", {})
        base = float(bump.get("strength", 0.02))
        bump["strength"] = round(base * bump_mult, 5)

    finish["calibration_meta"] = {
        "last_human_pick": {
            "filename": body.filename,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "params": finish["principled"],
        }
    }

    finish_path.write_text(
        json.dumps(finish, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("Human pick for %s: %s → R=%.2f M=%.2f S=%.2f", finish_id, body.filename, roughness, metallic, specular)

    return {
        "ok": True,
        "finish_id": finish_id,
        "params": {
            "roughness": roughness,
            "metallic": metallic,
            "specular": specular,
            "coat_weight": coat_weight,
            "coat_roughness": coat_roughness,
            "bump_mult": bump_mult,
        },
        "changes": {
            k: {"from": old.get(k), "to": v}
            for k, v in {"roughness": roughness, "metallic": metallic, "specular_ior_level": specular}.items()
            if old.get(k) != v
        },
    }


def _get_finish_dir() -> Path:
    from core.config import settings
    return Path(settings.finishes_dir)
