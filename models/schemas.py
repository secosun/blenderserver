from __future__ import annotations

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, EmailStr
from typing import Optional


class TaskStatus(str, Enum):
    pending = "pending"
    ready = "ready"
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


# ---------------------------------------------------------------------------
# Authentication / User
# ---------------------------------------------------------------------------

class UserRole(str, Enum):
    admin = "admin"
    user = "user"
    viewer = "viewer"


class UserCreate(BaseModel):
    email: str = Field(..., description="User email")
    password: str = Field(..., min_length=8, max_length=128, description="Password")
    display_name: str = Field("", max_length=100)


class UserLogin(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: UserRole
    quota_concurrency: int
    quota_max_resolution: int
    quota_max_samples: int
    is_active: bool
    created_at: str
    updated_at: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class APIKeyCreate(BaseModel):
    label: str = Field("", max_length=100)


class APIKeyResponse(BaseModel):
    id: str
    key_prefix: str
    label: str
    full_key: str | None = None  # only returned on creation
    last_used_at: str | None = None
    is_active: bool
    created_at: str


class APIKeyListResponse(BaseModel):
    keys: list[APIKeyResponse]


# ---------------------------------------------------------------------------
# Model upload
# ---------------------------------------------------------------------------

class ModelUploadResponse(BaseModel):
    model_id: str
    file_name: str
    file_size: int
    file_type: str  # fcstd, obj, stl, etc.
    storage_path: str
    upload_time: str


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

class TaskCreate(BaseModel):
    # Legacy: direct model upload (admin only)
    model_id: str | None = Field(None, description="ID of the uploaded 3D model (legacy)")
    # New: FreeCAD parametric template
    template_id: str | None = Field(None, description="FreeCAD template ID for parametric generation")
    template_params: dict | None = Field(None, description="Parameters for FreeCAD template (e.g., {length: 100, width: 50})")

    name: str | None = Field(None, max_length=200, description="Human-readable task name")
    scene_id: str | None = Field(None, description="Predefined scene preset ID")
    prompt: str | None = Field(None, max_length=500, description="Text description of desired rendering (alternative to scene_id)")
    camera_styles: list[str] | None = Field(None, description="Multiple camera angles for batch rendering")
    output_format: str | None = Field(None, description="Output image format: png, jpg, exr, webp")
    user_id: str = Field(default="anonymous")

    @property
    def has_scene(self) -> bool:
        return bool(self.scene_id)

    @property
    def has_prompt(self) -> bool:
        return bool(self.prompt)

    @property
    def has_template(self) -> bool:
        return bool(self.template_id)


class TaskResponse(BaseModel):
    id: str
    user_id: str
    model_id: str
    name: str | None = None
    prompt: str
    scene_id: str | None = None
    scene_name: str | None = None
    status: TaskStatus
    intent_json: dict | None = None
    storage_path: str | None = None
    result_url: str | None = None
    result_urls: list[str] | None = None
    error_message: str | None = None
    progress: float = 0.0
    progress_message: str = ""
    stage_name: str | None = None
    stage_progress: float | None = None
    eta_seconds: int | None = None
    retry_count: int = 0
    created_at: str
    updated_at: str


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int


# ---------------------------------------------------------------------------
# Worker callback
# ---------------------------------------------------------------------------

class WorkerCallback(BaseModel):
    status: TaskStatus
    progress: float = 0.0
    message: str = ""
    result_url: str | None = None
    result_urls: list[str] | None = None
    error_message: str | None = None
    stage_name: str | None = None
    stage_progress: float | None = None
    eta_seconds: int | None = None
    secret: str


# ---------------------------------------------------------------------------
# Render Intent (produced by LLM or from scene preset)
# ---------------------------------------------------------------------------

class RenderIntent(BaseModel):
    """Maps to ecommerce_product_shot_pipeline parameters."""

    model_path: str
    output_path: str = ""
    product_category: str = "generic"
    material_mode: str = "enhance"
    camera_style: str = "three_quarter"
    backdrop: str = "cyclorama"
    lighting_profile: str = "mid_standard"
    resolution_x: int | None = None
    resolution_y: int | None = None
    engine: str | None = None
    samples: int | None = None
    transparent_background: bool | None = None
    freecad_mm_obj: bool = False
    normalize_max_dimension: float | None = None
    auto_upright_yz_mode: str | None = "xz_elevation_pca"
    obj_forward_axis: str = "NEGATIVE_Z"
    obj_up_axis: str = "Y"
    prefer_gpu: bool = True
    preview_output_path: str = ""
    skip_final_render: bool = False


class LLMResponse(BaseModel):
    type: str  # "intent"
    intent: RenderIntent


# ---------------------------------------------------------------------------
# Organization / Billing
# ---------------------------------------------------------------------------


class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    owner_id: str
    stripe_customer_id: str | None = None
    created_at: str
    updated_at: str


class PlanResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    price_monthly_cents: int
    price_yearly_cents: int
    stripe_monthly_price_id: str | None = None
    stripe_yearly_price_id: str | None = None
    features: dict = {}
    is_public: bool
    sort_order: int


class PlanCreate(BaseModel):
    name: str
    slug: str
    description: str = ""
    price_monthly_cents: int = 0
    price_yearly_cents: int = 0
    stripe_monthly_price_id: str | None = None
    stripe_yearly_price_id: str | None = None
    features: dict = {}
    is_public: bool = True
    sort_order: int = 0


class SubscriptionResponse(BaseModel):
    id: str
    organization_id: str
    plan_id: str
    plan: PlanResponse | None = None
    stripe_subscription_id: str | None = None
    status: str
    billing_interval: str
    current_period_start: str | None = None
    current_period_end: str | None = None
    cancel_at_period_end: bool
    created_at: str
    updated_at: str


class CheckoutSessionRequest(BaseModel):
    price_id: str
    success_url: str = ""
    cancel_url: str = ""
    payment_method: str = "stripe"  # stripe | alipay | wechat


class CheckoutSessionResponse(BaseModel):
    url: str


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


class WebhookCreate(BaseModel):
    url: str = Field(..., max_length=500)
    events: list[str] = Field(..., description="e.g. ['task.completed', 'task.failed']")
    secret: str | None = Field(None, description="Custom secret for HMAC signing; auto-generated if empty")


class WebhookUpdate(BaseModel):
    url: str | None = None
    events: list[str] | None = None
    is_active: bool | None = None


class WebhookResponse(BaseModel):
    id: str
    user_id: str
    url: str
    events: list[str]
    is_active: bool
    created_at: str
    updated_at: str
