"""Gallery/Assets API — view and manage rendered assets."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import get_current_user

router = APIRouter(prefix="/assets", tags=["assets"])


def _db(request: Request):
    return request.app.state.task_manager.db


@router.get("")
async def list_assets(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    limit: int = 50,
    offset: int = 0,
):
    """List rendered assets for the current user's organization."""
    db = _db(request)
    org = await db.get_organization_by_user(current_user["id"])
    assets = await db.list_assets(org_id=org["id"] if org else None, limit=limit, offset=offset)

    from sqlalchemy import text
    total_query = "SELECT COUNT(*) as cnt FROM gallery_assets"
    params = {}
    if org:
        total_query += " WHERE organization_id = :oid"
        params["oid"] = org["id"]
    row = await db._fetchone(text(total_query), params)
    total = row["cnt"] if row else 0

    return {"assets": assets, "total": total}


@router.delete("/{asset_id}")
async def delete_asset(
    asset_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Delete an asset."""
    db = _db(request)
    from sqlalchemy import text
    result = await db._execute(
        text("DELETE FROM gallery_assets WHERE id = :id AND user_id = :uid"),
        {"id": asset_id, "uid": current_user["id"]},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Asset not found")
    return {"ok": True}
