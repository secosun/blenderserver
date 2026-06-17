"""Material finishes management API — database-backed, cluster-safe.

Finishes are stored in the ``finishes`` SQL table and cached as JSON files
for the blenderworker to consume (via a mounted volume or S3 download).
In a multi-worker cluster, all API instances read/write the same DB table,
so finishes are always consistent. Workers download the full finish catalog
on startup via ``GET /api/finishes/export``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.deps import require_admin
from core.config import settings

logger = logging.getLogger("blenderserver.finishes")

router = APIRouter(prefix="/finishes", tags=["finishes"])

FINISHES_TABLE = "material_finishes"

_FINISHES_DDL = f"""
CREATE TABLE IF NOT EXISTS {FINISHES_TABLE} (
    id VARCHAR(100) PRIMARY KEY,
    label_zh VARCHAR(100) NOT NULL,
    data TEXT NOT NULL,
    updated_at VARCHAR(32) NOT NULL
)
"""


async def ensure_table(db) -> None:
    try:
        await db._fetchone(text(f"SELECT 1 FROM {FINISHES_TABLE} LIMIT 1"))
    except Exception:
        await db._execute(text(_FINISHES_DDL))
        logger.info("Created %s table", FINISHES_TABLE)


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def _migrate_from_files(db) -> None:
    """Import existing file-based finishes into DB (one-time migration)."""
    d = _finishes_dir()
    if not d.is_dir():
        return
    for f in sorted(d.glob("*.json")):
        finish_id = f.stem
        existing = await db._fetchone(
            text(f"SELECT id FROM {FINISHES_TABLE} WHERE id = :id"), {"id": finish_id},
        )
        if existing:
            continue
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
            label_zh = data.pop("label_zh", finish_id)
            data.pop("id", None)
            now = _now()
            await db._execute(
                text(f"""INSERT INTO {FINISHES_TABLE} (id, label_zh, data, updated_at)
                        VALUES (:id, :zh, :data, :now)"""),
                {"id": finish_id, "zh": label_zh, "data": json.dumps(data, ensure_ascii=False), "now": now},
            )
            logger.info("Migrated finish from file: %s", finish_id)
        except Exception as e:
            logger.warning("Failed to migrate finish %s: %s", finish_id, e)


async def _load_all(db) -> list[dict]:
    await ensure_table(db)
    rows = await db._fetchall(text(f"SELECT * FROM {FINISHES_TABLE} ORDER BY id ASC"))
    if not rows:
        # First call — migrate from filesystem
        await _migrate_from_files(db)
        rows = await db._fetchall(text(f"SELECT * FROM {FINISHES_TABLE} ORDER BY id ASC"))
    result = []
    for r in rows:
        finish = json.loads(r["data"])
        finish["id"] = r["id"]
        finish["label_zh"] = r["label_zh"]
        result.append(finish)
    return result


async def _load_one(db, finish_id: str) -> dict | None:
    await ensure_table(db)
    row = await db._fetchone(
        text(f"SELECT * FROM {FINISHES_TABLE} WHERE id = :id"), {"id": finish_id},
    )
    if not row:
        return None
    finish = json.loads(row["data"])
    finish["id"] = row["id"]
    finish["label_zh"] = row["label_zh"]
    return finish


async def _upsert(db, finish_id: str, label_zh: str, data: dict) -> None:
    await ensure_table(db)
    now = _now()
    await db._execute(
        text(f"""INSERT INTO {FINISHES_TABLE} (id, label_zh, data, updated_at)
                VALUES (:id, :zh, :data, :now)
                ON CONFLICT(id) DO UPDATE SET label_zh=:zh2, data=:data2, updated_at=:now2"""),
        {"id": finish_id, "zh": label_zh, "data": json.dumps(data, ensure_ascii=False),
         "now": now, "zh2": label_zh, "data2": json.dumps(data, ensure_ascii=False), "now2": now},
    )
    # Sync to filesystem for blenderworker compatibility
    _sync_to_file(finish_id, label_zh, data)


async def _delete(db, finish_id: str) -> bool:
    await ensure_table(db)
    result = await db._execute(
        text(f"DELETE FROM {FINISHES_TABLE} WHERE id = :id"), {"id": finish_id},
    )
    _remove_file(finish_id)
    return result.rowcount > 0


def _finishes_dir() -> Path:
    p = Path(settings.finishes_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sync_to_file(finish_id: str, label_zh: str, data: dict) -> None:
    """Write finish to filesystem for blenderworker consumption."""
    try:
        d = _finishes_dir()
        finish = {**data, "id": finish_id, "label_zh": label_zh}
        with open(d / f"{finish_id}.json", "w", encoding="utf-8") as f:
            json.dump(finish, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to sync finish %s to file: %s", finish_id, e)


def _remove_file(finish_id: str) -> None:
    try:
        p = _finishes_dir() / f"{finish_id}.json"
        if p.is_file():
            p.unlink()
    except Exception as e:
        logger.warning("Failed to remove finish file %s: %s", finish_id, e)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class PrincipledParams(BaseModel):
    base_color: list[float] = [0.5, 0.5, 0.5, 1.0]
    roughness: float = 0.5
    metallic: float = 0.0
    specular_ior_level: float = 0.5
    coat_weight: float = 0.0
    coat_roughness: float = 0.3
    coat_ior: float = 1.5
    anisotropic: float | None = None
    anisotropic_rotation: float | None = None


class FinishCreate(BaseModel):
    id: str = Field(..., pattern=r"^[a-z0-9_]+$", max_length=100)
    label_zh: str = Field(..., max_length=100)
    gate_profile: str = "mid_matte"
    lighting_profile: str = "mid"
    view_exposure: float = -0.3
    hdri_strength: float = 0.4
    world_strength: float = 0.2
    principled: PrincipledParams = PrincipledParams()


class FinishUpdate(BaseModel):
    label_zh: str | None = None
    gate_profile: str | None = None
    lighting_profile: str | None = None
    view_exposure: float | None = None
    hdri_strength: float | None = None
    world_strength: float | None = None
    principled: PrincipledParams | None = None


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("")
async def list_finishes(request: Request):
    """List all finishes."""
    db = request.app.state.task_manager.db
    finishes = await _load_all(db)
    return {"finishes": finishes, "total": len(finishes)}


@router.get("/export")
async def export_finishes(request: Request):
    """Download all finishes as a JSON object (for worker consumption)."""
    db = request.app.state.task_manager.db
    finishes = await _load_all(db)
    result = {}
    for f in finishes:
        result[f["id"]] = f
    return result


@router.get("/{finish_id}")
async def get_finish(finish_id: str, request: Request):
    """Get a single finish."""
    db = request.app.state.task_manager.db
    finish = await _load_one(db, finish_id)
    if not finish:
        raise HTTPException(status_code=404, detail=f"Finish '{finish_id}' not found")
    return finish


@router.post("", status_code=201)
async def create_finish(
    body: FinishCreate,
    request: Request,
    _admin: Annotated[dict, Depends(require_admin)],
):
    """Create a new finish (admin only)."""
    db = request.app.state.task_manager.db
    existing = await _load_one(db, body.id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Finish '{body.id}' already exists")
    data = body.model_dump()
    data["material_folder"] = f"materials/finishes/{body.id}"
    data.pop("id", None)
    data.pop("label_zh", None)
    await _upsert(db, body.id, body.label_zh, data)
    logger.info("Created finish: %s", body.id)
    return await _load_one(db, body.id)


@router.put("/{finish_id}")
async def update_finish(
    finish_id: str,
    body: FinishUpdate,
    request: Request,
    _admin: Annotated[dict, Depends(require_admin)],
):
    """Update a finish (admin only)."""
    db = request.app.state.task_manager.db
    existing = await _load_one(db, finish_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Finish '{finish_id}' not found")
    update = body.model_dump(exclude_unset=True)
    label_zh = update.pop("label_zh", existing.get("label_zh", ""))
    for key, value in update.items():
        if value is not None:
            existing[key] = value
    data = {k: v for k, v in existing.items() if k not in ("id", "label_zh")}
    await _upsert(db, finish_id, label_zh, data)
    logger.info("Updated finish: %s", finish_id)
    return await _load_one(db, finish_id)


@router.delete("/{finish_id}")
async def delete_finish(
    finish_id: str,
    request: Request,
    _admin: Annotated[dict, Depends(require_admin)],
):
    """Delete a finish (admin only)."""
    db = request.app.state.task_manager.db
    ok = await _delete(db, finish_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Finish '{finish_id}' not found")
    return {"ok": True}
