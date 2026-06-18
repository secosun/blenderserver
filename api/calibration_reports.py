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


@router.get("/{finish_id}")
async def get_report(finish_id: str):
    """Return calibration_report.json for a finish."""
    report_path = _CAL_DIR / f"material_{finish_id}" / "calibration_report.json"
    if not report_path.is_file():
        raise HTTPException(status_code=404, detail=f"No calibration report for '{finish_id}'")
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read report: {e}")


@router.get("/{finish_id}/grid")
async def get_grid(finish_id: str):
    """Return 00_summary_grid.png for a finish."""
    grid_path = _CAL_DIR / f"material_{finish_id}" / "00_summary_grid.png"
    if not grid_path.is_file():
        raise HTTPException(status_code=404, detail=f"No summary grid for '{finish_id}'")
    return FileResponse(str(grid_path), media_type="image/png")


@router.get("/{finish_id}/images/{filename}")
async def get_image(finish_id: str, filename: str):
    """Return any image from the calibration output directory."""
    img_path = _CAL_DIR / f"material_{finish_id}" / filename
    if not img_path.is_file():
        raise HTTPException(status_code=404, detail=f"Image '{filename}' not found")
    ext = img_path.suffix.lower()
    media_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    return FileResponse(str(img_path), media_type=media_map.get(ext, "application/octet-stream"))


@router.get("/{finish_id}/validation/{filename}")
async def get_validation_image(finish_id: str, filename: str):
    """Return validation image."""
    img_path = _CAL_DIR / f"material_{finish_id}" / "validation" / filename
    if not img_path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(img_path), media_type="image/png")
