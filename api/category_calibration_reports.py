"""Category calibration human-review API — reports, images, preset write."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.deps import require_admin
from services.category_preset_writer import presets_path, write_category_calibration

logger = logging.getLogger("blenderserver.category_calibration_reports")

router = APIRouter(prefix="/category-calibration-reports", tags=["category-calibration-reports"])

_CAL_DIR = Path("/app/calibrate_out")
_REPORT_NAME = "category_calibration_report.json"
_CAMERA_MODES = ("fullshot", "detail")


def _iter_reports() -> list[tuple[Path, dict[str, Any]]]:
    """All category_calibration_report.json under calibrate_out (newest first per file mtime)."""
    found: list[tuple[Path, dict[str, Any]]] = []
    if not _CAL_DIR.is_dir():
        return found
    for rp in _CAL_DIR.rglob(_REPORT_NAME):
        try:
            data = json.loads(rp.read_text(encoding="utf-8"))
            found.append((rp.parent, data))
        except Exception:
            continue
    found.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    return found


def _category_dir(category: str, camera_mode: str = "fullshot") -> Path | None:
    if camera_mode not in _CAMERA_MODES:
        camera_mode = "fullshot"
    for out_dir, data in _iter_reports():
        if data.get("category") != category:
            continue
        if data.get("camera_mode", "fullshot") != camera_mode:
            continue
        return out_dir
    # Legacy flat layout: calibrate_out/fullshot/report.json
    base = _CAL_DIR / camera_mode
    report = base / _REPORT_NAME
    if report.is_file():
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            if data.get("category") == category:
                return base
        except Exception:
            pass
    return None


def _load_report(category: str, camera_mode: str = "fullshot") -> tuple[Path, dict[str, Any]]:
    cam = camera_mode if camera_mode in _CAMERA_MODES else "fullshot"
    for mode in (cam, "fullshot", "detail"):
        d = _category_dir(category, mode)
        if d is None:
            continue
        rp = d / _REPORT_NAME
        if rp.is_file():
            return d, json.loads(rp.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail=f"No category calibration report for '{category}'")


def _find_candidate(report: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    for c in report.get("candidates") or []:
        if c.get("candidate_id") == candidate_id:
            return c
    if candidate_id == "auto_best":
        ab = report.get("auto_best") or {}
        return {
            "candidate_id": "auto_best",
            "params": ab.get("params", {}),
            "score": ab.get("score", 0),
            "image": ab.get("image", ""),
        }
    return None


def _report_summary(data: dict[str, Any], camera_mode: str, subdir: str = "") -> dict[str, Any]:
    ab = data.get("auto_best") or {}
    hp = data.get("human_pick")
    return {
        "category": data.get("category", ""),
        "run_id": data.get("run_id", ""),
        "camera_mode": data.get("camera_mode", camera_mode),
        "subdir": subdir,
        "created_at": data.get("created_at", ""),
        "elapsed_s": data.get("elapsed_s", 0),
        "auto_best_score": ab.get("score", 0),
        "n_candidates": len(data.get("candidates") or []),
        "human_picked": hp is not None,
        "use_vlm": data.get("use_vlm", False),
    }


@router.get("")
async def list_reports(
    camera_mode: str = Query("fullshot", description="fullshot or detail"),
):
    """List categories that have a category_calibration_report.json."""
    mode = camera_mode if camera_mode in _CAMERA_MODES else "fullshot"
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for out_dir, data in _iter_reports():
        cat = data.get("category", "")
        if not cat or cat in seen:
            continue
        if data.get("camera_mode", "fullshot") != mode:
            continue
        seen.add(cat)
        rel = ""
        try:
            rel = str(out_dir.relative_to(_CAL_DIR))
        except ValueError:
            rel = str(out_dir.name)
        items.append(_report_summary(data, mode, rel))

    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"reports": items, "total": len(items), "camera_mode": mode}


@router.get("/{category}")
async def get_report(
    category: str,
    camera_mode: str = Query("fullshot"),
):
    """Return the latest category calibration review report."""
    _, data = _load_report(category, camera_mode)
    return data


@router.get("/{category}/images/{filename}")
async def get_image(
    category: str,
    filename: str,
    camera_mode: str = Query("fullshot"),
):
    """Return a render PNG from the calibration output directory."""
    out_dir, _ = _load_report(category, camera_mode)
    img_path = out_dir / filename
    if not img_path.is_file():
        raise HTTPException(status_code=404, detail=f"Image '{filename}' not found")
    return FileResponse(str(img_path), media_type="image/png")


class SelectCandidateBody(BaseModel):
    candidate_id: str
    camera_mode: str = "fullshot"


@router.post("/{category}/select-candidate")
async def select_candidate(
    category: str,
    body: SelectCandidateBody,
    _admin: Annotated[dict, Depends(require_admin)],
):
    """Human picks a top-K candidate; write merged params to product_presets.json."""
    out_dir, report = _load_report(category, body.camera_mode)
    if report.get("category") != category:
        raise HTTPException(status_code=400, detail="Category mismatch in report")

    cand = _find_candidate(report, body.candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail=f"Candidate '{body.candidate_id}' not found")

    params = cand.get("params") or {}
    if not params:
        raise HTTPException(status_code=400, detail="Candidate has no params")

    result = write_category_calibration(
        category,
        params,
        presets_file=presets_path(),
        baseline_cv_score=float(report.get("baseline_cv_score") or 0),
        baseline_metrics=report.get("baseline_metrics"),
        baseline_final_metrics=report.get("baseline_final_metrics"),
    )
    if not result.get("updated") and not result.get("dry_run"):
        raise HTTPException(status_code=500, detail=result.get("error", "Write failed"))

    report["human_pick"] = {
        "candidate_id": body.candidate_id,
        "score": cand.get("score"),
        "image": cand.get("image", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    report_path = out_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    logger.info("Human pick for %s: %s", category, body.candidate_id)
    return {
        "ok": True,
        "category": category,
        "candidate_id": body.candidate_id,
        "params": params,
        "preset_write": result,
    }
