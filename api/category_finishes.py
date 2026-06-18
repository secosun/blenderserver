"""Category → finish mapping API — which finish each product category uses."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from api.deps import require_admin

logger = logging.getLogger("blenderserver.category_finishes")

router = APIRouter(prefix="/category-finishes", tags=["category-finishes"])

_TABLE = "category_finish_mappings"
_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    category_key VARCHAR(200) PRIMARY KEY,
    finish_id VARCHAR(100) NOT NULL,
    updated_at VARCHAR(32) NOT NULL
)
"""

# Built-in categories from product_presets.json
# (kept in sync manually; runtime may add via admin route)
_BUILTIN_CATEGORIES = [
    "generic", "aluminum_6063", "aluminum_gunmetal_railing",
    "door_window_railing", "coating_black_product",
    "coating_orange_yellow_powder", "coating_gray_metal_plate",
    "coating_automotive_texture", "polyhaven_anti_slip_concrete",
    "coating_black_rusted_shutter", "coating_black_rusty_painted_metal",
    "coating_black_worn_shutter", "coating_champagne_box_profile_metal_sheet",
    "coating_champagne_metal_plate_02", "coating_champagne_rusty_metal_sheet",
    "coating_gray_corrugated_iron", "coating_gray_worn_corrugated_iron",
]


class MappingBody(BaseModel):
    finish_id: str


class MappingItem(BaseModel):
    category_key: str
    finish_id: str


async def _ensure_table(db) -> None:
    try:
        await db._fetchone(text(f"SELECT 1 FROM {_TABLE} LIMIT 1"))
    except Exception:
        await db._execute(text(_DDL))
        logger.info("Created %s table", _TABLE)


@router.get("")
async def list_mappings(request: Request):
    """Return all category→finish mappings (DB values merged with built-in defaults)."""
    await _ensure_table(request.app.state.task_manager.db)
    db = request.app.state.task_manager.db
    rows = await db._fetchall(text(f"SELECT * FROM {_TABLE}"))
    overrides = {r["category_key"]: r["finish_id"] for r in rows}

    # Merge with built-in defaults, DB values win
    default_map = {
        "generic": "powder_matte",
        "aluminum_6063": "powder_matte",
        "aluminum_gunmetal_railing": "anodized_black",
        "door_window_railing": "electrophoretic",
        "coating_black_product": "powder_matte",
        "coating_orange_yellow_powder": "powder_glossy",
        "coating_gray_metal_plate": "gray_silver_metallic",
        "coating_automotive_texture": "powder_glossy",
        "polyhaven_anti_slip_concrete": "powder_matte",
        "coating_black_rusted_shutter": "powder_matte",
        "coating_black_rusty_painted_metal": "powder_matte",
        "coating_black_worn_shutter": "powder_matte",
        "coating_champagne_box_profile_metal_sheet": "champagne_gold",
        "coating_champagne_metal_plate_02": "champagne_gold",
        "coating_champagne_rusty_metal_sheet": "champagne_gold",
        "coating_gray_corrugated_iron": "gray_silver_metallic",
        "coating_gray_worn_corrugated_iron": "gray_silver_metallic",
    }

    result = []
    for cat in _BUILTIN_CATEGORIES:
        default = default_map.get(cat, "powder_matte")
        finish_id = overrides.get(cat, default)
        result.append({"category_key": cat, "finish_id": finish_id, "overridden": cat in overrides})

    return {"mappings": result, "total": len(result)}


@router.get("/{category_key}")
async def get_mapping(category_key: str, request: Request):
    """Get finish for a single category."""
    db = request.app.state.task_manager.db
    await _ensure_table(db)
    row = await db._fetchone(
        text(f"SELECT * FROM {_TABLE} WHERE category_key = :key"),
        {"key": category_key},
    )
    finish_id = row["finish_id"] if row else "powder_matte"
    return {"category_key": category_key, "finish_id": finish_id}


@router.put("/{category_key}")
async def set_mapping(
    category_key: str,
    body: MappingBody,
    request: Request,
    _admin: Annotated[dict, Depends(require_admin)],
):
    """Set finish for a category (admin only)."""
    db = request.app.state.task_manager.db
    await _ensure_table(db)
    now = datetime.now(timezone.utc).isoformat()
    await db._execute(
        text(f"""INSERT INTO {_TABLE} (category_key, finish_id, updated_at)
                VALUES (:key, :fid, :now)
                ON CONFLICT(category_key) DO UPDATE SET finish_id=:fid2, updated_at=:now2"""),
        {"key": category_key, "fid": body.finish_id, "now": now,
         "fid2": body.finish_id, "now2": now},
    )
    logger.info("Category finish mapping: %s → %s", category_key, body.finish_id)
    return {"ok": True}


@router.delete("/{category_key}")
async def delete_mapping(
    category_key: str,
    request: Request,
    _admin: Annotated[dict, Depends(require_admin)],
):
    """Reset category to default finish (admin only)."""
    db = request.app.state.task_manager.db
    await _ensure_table(db)
    await db._execute(
        text(f"DELETE FROM {_TABLE} WHERE category_key = :key"),
        {"key": category_key},
    )
    return {"ok": True}
