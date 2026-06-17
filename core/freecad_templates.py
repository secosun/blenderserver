"""FreeCAD template management — .FCStd template CRUD.

Templates are parameterized .FCStd files created by administrators.
Each template contains a Spreadsheet that defines the editable dimensions.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from core.db import AsyncDatabase
from core.storage import get_storage


FREECAD_TEMPLATE_TABLE = "freecad_templates"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


# ======================================================================
# Schema helpers
# ======================================================================

_DDL = f"""
CREATE TABLE IF NOT EXISTS {FREECAD_TEMPLATE_TABLE} (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    description TEXT DEFAULT '',
    category VARCHAR(100) DEFAULT 'generic',
    storage_path VARCHAR(500) NOT NULL,
    params_schema TEXT NOT NULL DEFAULT '{{}}',
    thumbnail_url VARCHAR(500),
    tags TEXT DEFAULT '[]',
    is_active BOOLEAN DEFAULT true,
    created_by VARCHAR(36) NOT NULL,
    created_at VARCHAR(32) NOT NULL,
    updated_at VARCHAR(32) NOT NULL
);
"""


async def ensure_table(db: AsyncDatabase) -> None:
    from sqlalchemy import text
    try:
        await db._fetchone(text(f"SELECT 1 FROM {FREECAD_TEMPLATE_TABLE} LIMIT 1"))
    except Exception:
        await db._execute(text(_DDL))


# ======================================================================
# CRUD
# ======================================================================


async def create_template(
    db: AsyncDatabase,
    name: str,
    slug: str,
    description: str,
    category: str,
    storage_path: str,
    params_schema: dict | None,
    tags: list[str] | None,
    created_by: str,
    thumbnail_url: str | None = None,
) -> dict:
    from sqlalchemy import text

    await ensure_table(db)
    tid = _uuid()
    now = _now()

    await db._execute(
        text(f"""INSERT INTO {FREECAD_TEMPLATE_TABLE}
                (id, name, slug, description, category, storage_path,
                 params_schema, tags, created_by, created_at, updated_at)
                VALUES (:id, :name, :slug, :desc, :cat, :spath,
                        :schema, :tags, :cb, :now, :now)"""),
        {
            "id": tid,
            "name": name,
            "slug": slug,
            "desc": description,
            "cat": category,
            "spath": storage_path,
            "schema": json.dumps(params_schema or {}, ensure_ascii=False),
            "tags": json.dumps(tags or [], ensure_ascii=False),
            "cb": created_by,
            "now": now,
        },
    )
    return await get_template(db, tid)


async def get_template(db: AsyncDatabase, template_id: str) -> dict | None:
    from sqlalchemy import text

    row = await db._fetchone(
        text(f"SELECT * FROM {FREECAD_TEMPLATE_TABLE} WHERE id = :id"),
        {"id": template_id},
    )
    if row is None:
        return None
    return _format_template(row)


async def get_template_by_slug(db: AsyncDatabase, slug: str) -> dict | None:
    from sqlalchemy import text

    row = await db._fetchone(
        text(f"SELECT * FROM {FREECAD_TEMPLATE_TABLE} WHERE slug = :slug"),
        {"slug": slug},
    )
    if row is None:
        return None
    return _format_template(row)


async def list_templates(
    db: AsyncDatabase,
    active_only: bool = True,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    from sqlalchemy import text

    conditions = []
    params: dict[str, Any] = {"lim": limit, "off": offset}

    if active_only:
        conditions.append("is_active = true")
    if category:
        conditions.append("category = :cat")
        params["cat"] = category

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    rows = await db._fetchall(
        text(f"SELECT * FROM {FREECAD_TEMPLATE_TABLE} {where} ORDER BY name ASC LIMIT :lim OFFSET :off"),
        params,
    )
    return [_format_template(r) for r in rows]


async def update_template(
    db: AsyncDatabase,
    template_id: str,
    **kwargs,
) -> dict | None:
    from sqlalchemy import text

    now = _now()
    kwargs["updated_at"] = now

    if "params_schema" in kwargs and isinstance(kwargs["params_schema"], dict):
        kwargs["params_schema"] = json.dumps(kwargs["params_schema"], ensure_ascii=False)
    if "tags" in kwargs and isinstance(kwargs["tags"], list):
        kwargs["tags"] = json.dumps(kwargs["tags"], ensure_ascii=False)

    set_clause = ", ".join(f"{k} = :{k}" for k in kwargs)
    values = {**kwargs, "id": template_id}
    await db._execute(
        text(f"UPDATE {FREECAD_TEMPLATE_TABLE} SET {set_clause} WHERE id = :id"),
        values,
    )
    return await get_template(db, template_id)


async def delete_template(db: AsyncDatabase, template_id: str) -> bool:
    from sqlalchemy import text

    result = await db._execute(
        text(f"DELETE FROM {FREECAD_TEMPLATE_TABLE} WHERE id = :id"),
        {"id": template_id},
    )
    return result.rowcount > 0


async def count_templates(db: AsyncDatabase, active_only: bool = True) -> int:
    from sqlalchemy import text

    if active_only:
        row = await db._fetchone(
            text(f"SELECT COUNT(*) as cnt FROM {FREECAD_TEMPLATE_TABLE} WHERE is_active = true"),
        )
    else:
        row = await db._fetchone(
            text(f"SELECT COUNT(*) as cnt FROM {FREECAD_TEMPLATE_TABLE}"),
        )
    return row["cnt"] if row else 0


# ======================================================================
# Template file upload
# ======================================================================


async def upload_template_file(
    filename: str,
    content: bytes,
    created_by: str,
) -> dict:
    """Upload .FCStd file to storage. Returns storage metadata."""
    storage = get_storage()
    storage_key = f"freecad_templates/{_uuid()}/{filename}"
    url = await storage.upload_bytes(content, storage_key)
    return {
        "storage_path": storage_key,
        "url": url,
        "file_size": len(content),
    }


async def upload_template_thumbnail(
    db: AsyncDatabase,
    template_id: str,
    filename: str,
    content: bytes,
) -> str:
    """Upload a thumbnail image for a template. Returns the thumbnail URL."""
    storage = get_storage()
    ext = Path(filename).suffix.lower() or ".png"
    storage_key = f"freecad_templates/{template_id}/thumb{ext}"
    url = await storage.upload_bytes(content, storage_key)
    await update_template(db, template_id, thumbnail_url=storage_key)
    return storage_key


# ======================================================================
# Formatting
# ======================================================================


def _format_template(row: dict) -> dict:
    result = dict(row)
    # Parse JSON fields
    for field in ("params_schema", "tags"):
        if isinstance(result.get(field), str):
            try:
                result[field] = json.loads(result[field])
            except (json.JSONDecodeError, TypeError):
                pass
    # Cast is_active to bool
    if "is_active" in result:
        result["is_active"] = bool(result["is_active"])
    return result
