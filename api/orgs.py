"""Organization / Team management API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import get_current_user
from models.schemas import UserResponse

router = APIRouter(prefix="/orgs", tags=["orgs"])


def _db(request: Request):
    return request.app.state.task_manager.db


@router.get("/my")
async def get_my_organization(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Get the current user's organization."""
    db = _db(request)
    org = await db.get_organization_by_user(current_user["id"])
    if not org:
        raise HTTPException(status_code=404, detail="No organization found")
    return org


@router.patch("/{org_id}")
async def update_organization(
    org_id: str,
    body: dict,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Update organization name (owner only)."""
    db = _db(request)
    org = await db.get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if org["owner_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner can update the organization")

    kwargs = {}
    if "name" in body:
        kwargs["name"] = body["name"]
    if kwargs:
        org = await db.update_organization(org_id, **kwargs)
    return org


@router.get("/members")
async def list_members(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """List all members of the current user's organization."""
    db = _db(request)
    org = await db.get_organization_by_user(current_user["id"])
    if not org:
        raise HTTPException(status_code=404, detail="No organization found")

    members = await db.get_org_members(org["id"])
    users = []
    for m in members:
        u = await db.get_user(m["user_id"])
        if u:
            users.append(UserResponse(
                id=u["id"], email=u["email"], display_name=u.get("display_name", ""),
                role=u.get("role", "user"),
                quota_concurrency=u.get("quota_concurrency", 2),
                quota_max_resolution=u.get("quota_max_resolution", 4096),
                quota_max_samples=u.get("quota_max_samples", 512),
                is_active=bool(u.get("is_active", True)),
                created_at=u["created_at"], updated_at=u["updated_at"],
            ))
    return {"members": members, "users": users}


@router.post("/invite")
async def invite_member(
    body: dict,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Invite a user to the organization by email."""
    email = body.get("email", "")
    role = body.get("role", "member")
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    db = _db(request)
    org = await db.get_organization_by_user(current_user["id"])
    if not org:
        raise HTTPException(status_code=404, detail="No organization found")

    target = await db.get_user_by_email(email)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if not target.get("is_active", True):
        raise HTTPException(status_code=400, detail="Target user is inactive")

    existing = await db.get_org_members(org["id"])
    if any(m["user_id"] == target["id"] for m in existing):
        raise HTTPException(status_code=409, detail="User is already a member")

    await db.add_organization_member(org["id"], target["id"], role=role)

    await db.add_audit_log(
        user_id=current_user["id"], action="invite_member",
        resource_type="organization", resource_id=org["id"],
        details=f"Invited user {target['id']} as {role}",
    )
    return {"ok": True, "message": f"User {email} added as {role}"}


@router.delete("/members/{member_id}")
async def remove_member(
    member_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Remove a member from the organization."""
    db = _db(request)
    org = await db.get_organization_by_user(current_user["id"])
    if not org:
        raise HTTPException(status_code=404, detail="No organization found")

    # Verify the member exists in this org
    members = await db.get_org_members(org["id"])
    target = next((m for m in members if m["id"] == member_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Member not found")

    if target["user_id"] == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    if current_user["id"] != org["owner_id"]:
        raise HTTPException(status_code=403, detail="Only the owner can remove members")

    # Delete the member
    from sqlalchemy import text
    await db._execute(
        text("DELETE FROM organization_members WHERE id = :id"),
        {"id": member_id},
    )

    await db.add_audit_log(
        user_id=current_user["id"], action="remove_member",
        resource_type="organization", resource_id=org["id"],
        details=f"Removed member {target['user_id']}",
    )
    return {"ok": True}
