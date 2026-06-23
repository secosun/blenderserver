"""Serve calibration report JSON + images for the calibration viewer."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("blenderserver.calibration_reports")

router = APIRouter(prefix="/calibration-reports", tags=["calibration-reports"])


def _cal_dir() -> Path:
    """Docker mount or host blenderworker/calibrate_out."""
    repo = Path(__file__).resolve().parents[2]
    for candidate in (
        Path("/app/calibrate_out"),
        repo / "blenderworker" / "calibrate_out",
        repo / "calibrate_out",
    ):
        if candidate.is_dir():
            return candidate
    return repo / "blenderworker" / "calibrate_out"


def _material_dir(finish_id: str) -> Path | None:
    """Resolve material calibration output dir (flat or legacy nested layout)."""
    cal = _cal_dir()
    flat = cal / f"material_{finish_id}"
    if flat.is_dir() and (flat / "calibration_report.json").is_file():
        return flat
    nested = flat / f"material_{finish_id}"
    if nested.is_dir() and (nested / "calibration_report.json").is_file():
        return nested
    if flat.is_dir():
        return flat
    return None


def _texture_dir(finish_id: str) -> Path | None:
    cal = _cal_dir()
    d = cal / f"texture_{finish_id}"
    if d.is_dir():
        return d
    return None


def _safe_image_path(base_dir: Path, filename: str) -> Path | None:
    """Resolve image under base_dir; reject path traversal."""
    rel = Path(filename)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    candidate = (base_dir / rel).resolve()
    try:
        candidate.relative_to(base_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _media_response(img_path: Path) -> FileResponse:
    ext = img_path.suffix.lower()
    media_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    return FileResponse(str(img_path), media_type=media_map.get(ext, "application/octet-stream"))


def _has_material_report(finish_id: str) -> bool:
    mat_dir = _material_dir(finish_id)
    if mat_dir is None:
        return False
    return (mat_dir / "calibration_report.json").is_file()


def _has_texture_report(finish_id: str) -> bool:
    tex_dir = _texture_dir(finish_id)
    if tex_dir is None:
        return False
    if (tex_dir / "texture_calibration_report.json").is_file():
        return True
    if (tex_dir / "compare_beauty_ref.png").is_file():
        return True
    return any(tex_dir.glob("trial_*.png"))


@router.get("/{finish_id}/availability")
async def get_finish_availability(finish_id: str):
    """Which calibration artifacts exist for a finish (material vs texture)."""
    return {
        "finish_id": finish_id,
        "material": _has_material_report(finish_id),
        "texture": _has_texture_report(finish_id),
    }


@router.get("/texture/index")
async def list_texture_reports():
    """Finish IDs with texture calibration output."""
    cal = _cal_dir()
    ids: list[str] = []
    if not cal.is_dir():
        return {"finish_ids": ids}
    for p in sorted(cal.glob("texture_*")):
        if not p.is_dir():
            continue
        finish_id = p.name.replace("texture_", "", 1)
        if (p / "texture_calibration_report.json").is_file() or list(p.glob("trial_*.png")):
            ids.append(finish_id)
    return {"finish_ids": ids}


@router.get("/texture/{finish_id}")
async def get_texture_report(finish_id: str):
    tex_dir = _texture_dir(finish_id)
    if tex_dir is None:
        raise HTTPException(status_code=404, detail=f"No texture calibration for '{finish_id}'")
    report_path = tex_dir / "texture_calibration_report.json"
    if report_path.is_file():
        try:
            return json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read report: {e}")
    # Fallback: pipeline report texture section
    pipeline = _cal_dir() / "calibration_pipeline_report.json"
    if pipeline.is_file():
        try:
            data = json.loads(pipeline.read_text(encoding="utf-8"))
            tex = data.get("texture")
            if tex and tex.get("finish_id") == finish_id:
                return {
                    "finish_id": finish_id,
                    "calibration_type": "texture_reference",
                    "reference_path": tex.get("reference_path"),
                    "best_score": tex.get("best_score"),
                    "best_params": tex.get("best_params"),
                    "n_trials_completed": tex.get("n_trials"),
                    "review_images": {
                        "reference": "reference.png",
                        "beauty_best": "beauty_best.png",
                        "proxy_best": "proxy_best.png",
                        "compare_beauty_ref": "compare_beauty_ref.png",
                        "compare_triple": "compare_triple.png",
                    },
                }
        except Exception:
            pass
    raise HTTPException(status_code=404, detail=f"No texture report for '{finish_id}'")


@router.get("/texture/{finish_id}/trials")
async def list_texture_trials(finish_id: str):
    tex_dir = _texture_dir(finish_id)
    if tex_dir is None:
        raise HTTPException(status_code=404, detail=f"No texture calibration for '{finish_id}'")

    scores: list[float] = []
    report_path = tex_dir / "texture_calibration_report.json"
    best_trial: int | None = None
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            scores = list(report.get("trial_scores") or [])
            bt = report.get("best_trial")
            if bt is not None:
                best_trial = int(bt)
        except Exception:
            pass

    images: list[dict] = []
    for f in sorted(tex_dir.glob("trial_*.png")):
        trial_num = None
        m = re.match(r"trial_(\d+)", f.stem)
        if m:
            trial_num = int(m.group(1))
        score = scores[trial_num] if trial_num is not None and trial_num < len(scores) else None
        images.append({
            "filename": f.name,
            "trial_id": f.stem,
            "score": score,
            "phase": "proxy",
            "is_best": trial_num is not None and trial_num == best_trial,
        })
    return {"images": images, "total": len(images)}


@router.get("/texture/{finish_id}/images/{filename:path}")
async def get_texture_image(finish_id: str, filename: str):
    tex_dir = _texture_dir(finish_id)
    if tex_dir is None:
        raise HTTPException(status_code=404, detail=f"No texture data for '{finish_id}'")
    img_path = _safe_image_path(tex_dir, filename)
    if img_path is None:
        raise HTTPException(status_code=404, detail=f"Image '{filename}' not found")
    return _media_response(img_path)


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
async def get_grid(finish_id: str, phase: str = ""):
    """Return summary grid PNG (``phase``: ``pbr`` | ``texture`` | default all-trials)."""
    mat_dir = _material_dir(finish_id)
    if mat_dir is None:
        raise HTTPException(status_code=404, detail=f"No summary grid for '{finish_id}'")
    phase_grids = {
        "pbr": mat_dir / "pbr" / "00_pbr_grid.png",
        "texture": mat_dir / "texture" / "00_texture_grid.png",
    }
    if phase in phase_grids:
        grid_path = phase_grids[phase]
    else:
        grid_path = mat_dir / "00_summary_grid.png"
    if not grid_path.is_file():
        raise HTTPException(status_code=404, detail=f"No summary grid for '{finish_id}'")
    return FileResponse(str(grid_path), media_type="image/png")


def _trial_phase(rel_name: str, trial_id: str) -> str:
    """Classify trial image: pbr | texture | confirm | legacy."""
    if rel_name.startswith("pbr/") or trial_id.startswith("pbr_"):
        return "pbr"
    if rel_name.startswith("texture/") or trial_id.startswith("tex_"):
        return "texture"
    if trial_id.startswith("confirm_t") or "confirm_t" in rel_name:
        return "confirm"
    return "legacy"


def _iter_trial_image_files(mat_dir: Path):
    """Yield (relative_posix_path, trial_id) for calibration trial PNGs."""
    layouts = (
        ("", "trial_*.png"),
        ("", "confirm_t*.png"),
        ("pbr", "pbr_*.png"),
        ("texture", "tex_*.png"),
    )
    seen: set[str] = set()
    for sub, pattern in layouts:
        root = mat_dir / sub if sub else mat_dir
        if not root.is_dir():
            continue
        for f in sorted(root.glob(pattern)):
            if f.name.endswith("_review.png") or "_review" in f.stem:
                continue
            rel = f.relative_to(mat_dir).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            yield rel, f.stem


@router.get("/{finish_id}/images/{filename:path}")
async def get_image(finish_id: str, filename: str):
    """Return any image from the calibration output directory (supports ``pbr/``, ``texture/``)."""
    mat_dir = _material_dir(finish_id)
    if mat_dir is None:
        raise HTTPException(status_code=404, detail=f"No calibration data for '{finish_id}'")
    img_path = _safe_image_path(mat_dir, filename)
    if img_path is None:
        raise HTTPException(status_code=404, detail=f"Image '{filename}' not found")
    return _media_response(img_path)


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
                cs = report.get("confirm_stage") or {}
                candidates = cs.get("candidates") or []
                for c in candidates:
                    trial_idx = int(c.get("source_trial", 0))
                    scores_map[f"confirm_t{trial_idx:03d}"] = c.get("confirm_score", 0)
        except Exception:
            pass

    images: list[dict] = []
    for rel_name, trial_id in _iter_trial_image_files(mat_dir):
        score = scores_map.get(trial_id.replace("trial_", "confirm_t"))
        if score is None and trial_id.startswith("confirm_t"):
            score = scores_map.get(trial_id)
        images.append({
            "filename": rel_name,
            "trial_id": trial_id,
            "score": score,
            "phase": _trial_phase(rel_name, trial_id),
        })

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


class SelectTrialBody(BaseModel):
    filename: str


@router.post("/{finish_id}/select-trial")
async def select_trial(finish_id: str, body: SelectTrialBody):
    """Let human pick the best trial by eye and save its params to finish JSON."""
    m = re.search(r"_r([\d.]+)_m([\d.]+)_s([\d.]+)", body.filename)
    if not m:
        raise HTTPException(status_code=400, detail=f"Cannot parse params from filename: {body.filename}")
    roughness = float(m.group(1))
    metallic = float(m.group(2))
    specular = float(m.group(3))

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
    logger.info(
        "Human pick for %s: %s → R=%.2f M=%.2f S=%.2f",
        finish_id, body.filename, roughness, metallic, specular,
    )

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
            for k, v in {
                "roughness": roughness,
                "metallic": metallic,
                "specular_ior_level": specular,
            }.items()
            if old.get(k) != v
        },
    }


def _get_finish_dir() -> Path:
    from core.config import settings

    return Path(settings.finishes_dir)
