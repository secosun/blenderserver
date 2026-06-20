"""Colors API — serves RAL + legacy color catalog to the frontend.

Converts linear RGBA to sRGB HEX for CSS display in the frontend.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/colors", tags=["colors"])


def _linear_to_srgb(c: float) -> int:
    """Convert linear 0-1 float to sRGB 0-255 integer."""
    if c <= 0.0031308:
        s = c * 12.92
    else:
        s = 1.055 * (c ** (1.0 / 2.4)) - 0.055
    return max(0, min(255, round(s * 255)))


def _rgba_to_hex(rgba: list[float]) -> str:
    """Convert linear RGBA float array to sRGB hex string."""
    r = _linear_to_srgb(rgba[0])
    g = _linear_to_srgb(rgba[1])
    b = _linear_to_srgb(rgba[2])
    return f"#{r:02x}{g:02x}{b:02x}"


# RAL series grouping for frontend display
RAL_SERIES = {
    "yellow": {"label": "黄/米色系", "range": (1000, 1099)},
    "orange": {"label": "橙色系", "range": (2000, 2099)},
    "red": {"label": "红色系", "range": (3000, 3099)},
    "violet": {"label": "紫色系", "range": (4000, 4099)},
    "blue": {"label": "蓝色系", "range": (5000, 5099)},
    "green": {"label": "绿色系", "range": (6000, 6099)},
    "grey": {"label": "灰色系", "range": (7000, 7099)},
    "brown": {"label": "棕色系", "range": (8000, 8099)},
    "white_black": {"label": "白/黑色系", "range": (9000, 9099)},
}


def _detect_series(code: str) -> str:
    """Detect RAL series from a color key like 'ral_5005'."""
    if not code.startswith("ral_"):
        return "legacy"
    try:
        num = int(code[4:])
        for key, info in RAL_SERIES.items():
            lo, hi = info["range"]
            if lo <= num <= hi:
                return key
    except ValueError:
        pass
    return "other"


class ColorResponse(BaseModel):
    key: str
    label_zh: str
    label_en: str = ""
    hex: str
    linear_rgba: list[float]
    series: str = ""


class ColorListResponse(BaseModel):
    colors: dict[str, ColorResponse]
    series: dict[str, str]


@router.get("", response_model=ColorListResponse)
async def list_colors():
    """Return all catalog colors with hex conversion and series grouping."""
    # File is at blenderserver/api/colors.py → 3 parents = repo root
    repo_root = Path(__file__).resolve().parent.parent.parent
    catalog_path = repo_root / "blenderworker" / "blender_mcp_presets" / "catalog_colors.json"

    if not catalog_path.is_file():
        # Fallback: check without blenderworker prefix
        catalog_path = repo_root / "blender_mcp_presets" / "catalog_colors.json"

    if not catalog_path.is_file():
        return {"colors": {}, "series": {}}

    try:
        with open(catalog_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"colors": {}, "series": {}}

    raw_colors = data.get("colors", {})
    result = {}
    for key, color_def in raw_colors.items():
        rgba = color_def.get("principled", {}).get("base_color", [0.5, 0.5, 0.5, 1.0])
        hex_color = _rgba_to_hex(rgba)
        series = _detect_series(key)
        result[key] = ColorResponse(
            key=key,
            label_zh=color_def.get("label_zh", key),
            label_en=color_def.get("label_en", ""),
            hex=hex_color,
            linear_rgba=rgba,
            series=series,
        )

    series_labels = {k: v["label"] for k, v in RAL_SERIES.items()}
    series_labels["legacy"] = "原有喷粉色"

    return {"colors": result, "series": series_labels}
