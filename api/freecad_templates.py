"""FreeCAD template management API.

Templates are parameterized .FCStd files with a Spreadsheet defining
editable dimensions. Administrators upload and manage templates;
users browse and select them when creating render tasks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel, Field

from api.deps import get_current_user, require_admin
from core import freecad_templates as tmpl_core

router = APIRouter(prefix="/freecad", tags=["freecad"])


# ======================================================================
# Schemas
# ======================================================================


class TemplateCreate(BaseModel):
    name: str = Field(..., max_length=200, description="Template display name")
    slug: str = Field(..., max_length=100, pattern=r"^[a-z0-9_-]+$", description="URL-friendly identifier")
    description: str = Field("", max_length=1000)
    category: str = Field("generic", max_length=100)
    params_schema: dict = Field(
        default={},
        description="JSON Schema defining editable parameters",
        examples=[{
            "type": "object",
            "properties": {
                "length": {"type": "number", "title": "长度 (mm)", "default": 100, "minimum": 10, "maximum": 3000},
                "width": {"type": "number", "title": "宽度 (mm)", "default": 50, "minimum": 10, "maximum": 1000},
                "height": {"type": "number", "title": "高度 (mm)", "default": 20, "minimum": 5, "maximum": 500},
                "thickness": {"type": "number", "title": "壁厚 (mm)", "default": 2.0, "minimum": 0.5, "maximum": 50},
                "hole_diameter": {"type": "number", "title": "孔径 (mm)", "default": 0, "minimum": 0, "maximum": 100},
                "finish": {"type": "string", "title": "表面处理", "enum": ["powder_coat", "anodized", "brushed", "polished"], "default": "powder_coat"},
            },
            "required": ["length", "width", "height"],
        }],
    )
    tags: list[str] = Field(default=[], max_length=10)


class TemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    params_schema: dict | None = None
    tags: list[str] | None = None
    is_active: bool | None = None


class TemplateResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    category: str
    storage_path: str | None = None
    params_schema: dict = {}
    tags: list[str] = []
    is_active: bool
    thumbnail_url: str | None = None
    created_by: str
    created_at: str
    updated_at: str


class TemplateListResponse(BaseModel):
    templates: list[TemplateResponse]
    total: int


# ======================================================================
# Routes
# ======================================================================


@router.get("/templates", response_model=TemplateListResponse)
async def list_templates(
    request: Request,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    """List available FreeCAD templates."""
    db = request.app.state.task_manager.db
    await tmpl_core.ensure_table(db)
    templates = await tmpl_core.list_templates(
        db, active_only=True, category=category, limit=limit, offset=offset,
    )
    total = await tmpl_core.count_templates(db, active_only=True)
    return {"templates": templates, "total": total}


@router.get("/templates/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: str,
    request: Request,
):
    """Get template detail by ID."""
    db = request.app.state.task_manager.db
    template = await tmpl_core.get_template(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.post("/templates", response_model=TemplateResponse, status_code=201)
async def create_template(
    body: TemplateCreate,
    request: Request,
    current_user: Annotated[dict, Depends(require_admin)],
):
    """Create a new FreeCAD template metadata entry (admin only)."""
    db = request.app.state.task_manager.db

    # Check slug uniqueness
    existing = await tmpl_core.get_template_by_slug(db, body.slug)
    if existing:
        raise HTTPException(status_code=409, detail=f"Template slug '{body.slug}' already exists")

    template = await tmpl_core.create_template(
        db=db,
        name=body.name,
        slug=body.slug,
        description=body.description,
        category=body.category,
        storage_path="",  # Set via upload endpoint
        params_schema=body.params_schema,
        tags=body.tags,
        created_by=current_user["id"],
    )
    return template


@router.post("/templates/{template_id}/upload")
async def upload_template_file(
    template_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: Annotated[dict, Depends(require_admin)] = None,
):
    """Upload .FCStd file for a template (admin only).

    Only .FCStd files are accepted. The file is stored in S3/local storage
    and the template's ``storage_path`` is updated.
    """
    db = request.app.state.task_manager.db

    template = await tmpl_core.get_template(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Validate file type
    ext = Path(file.filename or "").suffix.lower()
    if ext != ".fcstd":
        raise HTTPException(status_code=400, detail=f"Only .FCStd files accepted, got '{ext}'")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    upload_info = await tmpl_core.upload_template_file(
        filename=file.filename or "template.FCStd",
        content=content,
        created_by=current_user["id"],
    )

    await tmpl_core.update_template(
        db, template_id,
        storage_path=upload_info["storage_path"],
    )

    return {
        "ok": True,
        "storage_path": upload_info["storage_path"],
        "file_size": upload_info["file_size"],
    }


@router.post("/templates/{template_id}/thumbnail")
async def upload_template_thumbnail(
    template_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: Annotated[dict, Depends(require_admin)] = None,
):
    """Upload a thumbnail image for a template (admin only).

    Accepts jpg, png, webp images up to 5MB. Updates the template's
    ``thumbnail_url`` field.
    """
    db = request.app.state.task_manager.db

    template = await tmpl_core.get_template(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(status_code=400, detail=f"Only image files accepted, got '{ext}'")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")

    storage_key = await tmpl_core.upload_template_thumbnail(
        db, template_id,
        filename=file.filename or "thumb.png",
        content=content,
    )

    return {"ok": True, "thumbnail_url": storage_key}


@router.patch("/templates/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: str,
    body: TemplateUpdate,
    request: Request,
    current_user: Annotated[dict, Depends(require_admin)],
):
    """Update template metadata (admin only)."""
    db = request.app.state.task_manager.db

    template = await tmpl_core.get_template(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    kwargs = {}
    for field in ("name", "description", "category", "params_schema", "tags", "is_active"):
        val = getattr(body, field, None)
        if val is not None:
            kwargs[field] = val

    if kwargs:
        await tmpl_core.update_template(db, template_id, **kwargs)

    return await tmpl_core.get_template(db, template_id)


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: str,
    request: Request,
    _admin: Annotated[dict, Depends(require_admin)],
):
    """Delete a FreeCAD template (admin only)."""
    db = request.app.state.task_manager.db

    template = await tmpl_core.get_template(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    ok = await tmpl_core.delete_template(db, template_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete template")

    return {"ok": True}
