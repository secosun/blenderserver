from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    # Server
    host: str = os.getenv("SERVER_HOST", "0.0.0.0")
    port: int = int(os.getenv("SERVER_PORT", "8060"))

    # Database — store as a filesystem path, convert to URI in Database
    db_path: str = os.getenv("DB_PATH", str(_ROOT / "data" / "blenderserver.db"))

    # LLM (Claude API)
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # Queue
    queue_backend: str = os.getenv("QUEUE_BACKEND", "memory")  # memory | redis
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Worker callback
    worker_callback_secret: str = os.getenv("WORKER_CALLBACK_SECRET", "dev-secret")

    # CORS
    cors_origins: list[str] = field(default_factory=lambda: os.getenv("CORS_ORIGINS", "*").split(","))

    # Asset storage
    upload_dir: str = os.getenv("UPLOAD_DIR", str(_ROOT / "uploads"))
    output_dir: str = os.getenv("OUTPUT_DIR", str(_ROOT / "outputs"))

    # Storage backend (local | s3)
    storage_backend: str = os.getenv("STORAGE_BACKEND", "local")
    s3_endpoint: str = os.getenv("S3_ENDPOINT", "")
    s3_region: str = os.getenv("S3_REGION", "us-east-1")
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "")
    s3_bucket: str = os.getenv("S3_BUCKET", "cadrender")
    s3_upload_prefix: str = os.getenv("S3_UPLOAD_PREFIX", "uploads")
    s3_output_prefix: str = os.getenv("S3_OUTPUT_PREFIX", "outputs")
    result_url_ttl_seconds: int = int(os.getenv("RESULT_URL_TTL", "3600"))

    # CDN
    cdn_base_url: str = os.getenv("CDN_BASE_URL", "")

    # Stripe / Billing
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe_price_lookup_key: str = os.getenv("STRIPE_PRICE_LOOKUP_KEY", "cadrender_monthly")
    default_plan_slug: str = os.getenv("DEFAULT_PLAN_SLUG", "free")

    @property
    def stripe_enabled(self) -> bool:
        return bool(self.stripe_secret_key)

    # Rate limiting
    rate_limit_enabled: bool = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("1", "true", "yes")
    rate_limit_backend: str = os.getenv("RATE_LIMIT_BACKEND", "memory")  # memory | redis
    rate_limit_auth_per_minute: int = int(os.getenv("RATE_LIMIT_AUTH_PER_MINUTE", "20"))
    rate_limit_tasks_per_minute: int = int(os.getenv("RATE_LIMIT_TASKS_PER_MINUTE", "60"))
    rate_limit_upload_per_minute: int = int(os.getenv("RATE_LIMIT_UPLOAD_PER_MINUTE", "10"))
    rate_limit_default_per_minute: int = int(os.getenv("RATE_LIMIT_DEFAULT_PER_MINUTE", "120"))

    # Job timeout (SLA)
    job_timeout_minutes: int = int(os.getenv("JOB_TIMEOUT_MINUTES", "30"))

    # Worker health
    worker_heartbeat_timeout: int = int(os.getenv("WORKER_HEARTBEAT_TIMEOUT", "90"))

    # Auto-retry
    max_task_retries: int = int(os.getenv("MAX_TASK_RETRIES", "3"))

    # Webhook
    webhook_delivery_timeout: int = int(os.getenv("WEBHOOK_DELIVERY_TIMEOUT", "10"))
    webhook_max_retries: int = int(os.getenv("WEBHOOK_MAX_RETRIES", "3"))

    # JWT
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-jwt-secret-change-in-production")
    jwt_expiry_hours: int = int(os.getenv("JWT_EXPIRY_HOURS", "72"))

    # FreeCAD Worker
    freecad_enabled: bool = os.getenv("FREECAD_ENABLED", "false").lower() in ("1", "true", "yes")
    freecad_template_dir: str = os.getenv("FREECAD_TEMPLATE_DIR", str(_ROOT / "freecad_templates"))
    freecad_generate_timeout: int = int(os.getenv("FREECAD_GENERATE_TIMEOUT", "300"))

    @property
    def database_url(self) -> str:
        """Database URL — PostgreSQL in production, SQLite for dev."""
        pg_url = os.getenv("DATABASE_URL", "")
        if pg_url:
            return pg_url
        path = Path(self.db_path).resolve()
        return f"sqlite+aiosqlite:///{path.as_posix()}"


settings = Settings()
