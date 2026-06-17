"""CADRender SaaS Middle-tier (blenderserver).

FastAPI application that sits between the web frontend and the blenderworker
execution kernel.  Provides:

- 3D model file upload
- Scene preset selection & LLM prompt-based render intent generation
- Async task dispatch via message queue
- WebSocket for real-time progress
- Worker callback endpoint for status updates
- JWT auth, API key management, user isolation
- Prometheus metrics, structured logging, admin API
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.queue import get_queue
from core.storage import get_storage
from core.task_manager import TaskManager

# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

from core.logging_config import setup_logging
setup_logging()
logger = logging.getLogger("blenderserver")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting blenderserver ...")

    tm = TaskManager()
    await tm.initialize()
    app.state.task_manager = tm
    logger.info("Task manager initialized", extra={"db": settings.database_url})

    queue = await get_queue()
    app.state.queue = queue
    logger.info("Message queue initialized", extra={"backend": settings.queue_backend})

    # Seed default plans
    from core.billing import seed_plans
    await seed_plans(tm.db)

    # Start periodic SLA background tasks
    import asyncio

    async def _sla_background_loop():
        while True:
            await asyncio.sleep(60)
            try:
                await tm.fail_stuck_tasks(settings.job_timeout_minutes)
            except Exception:
                logger.exception("Task timeout check failed")
            try:
                await tm.db.cleanup_stale_workers(settings.worker_heartbeat_timeout)
            except Exception:
                logger.exception("Worker cleanup failed")

    asyncio.create_task(_sla_background_loop())

    # Start periodic metrics update
    from core.metrics import start_metrics_loop
    asyncio.create_task(start_metrics_loop(app))

    logger.info("blenderserver ready", extra={"host": settings.host, "port": settings.port})
    yield

    # Shutdown
    await app.state.queue.disconnect()
    await tm.close()
    logger.info("blenderserver shut down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CADRender SaaS Middle-tier",
    description="LLM-powered 3D rendering orchestration service",
    version="0.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Rate limiting middleware
# ---------------------------------------------------------------------------

from core.rate_limiter import get_rate_limiter

_RATE_LIMIT_GROUPS: dict[str, str] = {
    "/api/auth/": "auth",
    "/api/upload": "upload",
    "/api/tasks": "tasks",
}

_RATE_LIMIT_DEFAULTS: dict[str, int] = {
    "auth": settings.rate_limit_auth_per_minute,
    "tasks": settings.rate_limit_tasks_per_minute,
    "upload": settings.rate_limit_upload_per_minute,
    "default": settings.rate_limit_default_per_minute,
}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if settings.rate_limit_enabled:
        user_id = _extract_user_id_for_ratelimit(request)
        if user_id:
            path = request.url.path
            group = "default"
            for prefix, g in _RATE_LIMIT_GROUPS.items():
                if path.startswith(prefix):
                    group = g
                    break
            limit = _RATE_LIMIT_DEFAULTS.get(group, _RATE_LIMIT_DEFAULTS["default"])
            limiter = get_rate_limiter()
            allowed, retry_after = await limiter.check_and_increment(
                f"ratelimit:{group}:{user_id}", limit
            )
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                    content={"detail": "请求过于频繁，请稍后重试", "retry_after": retry_after},
                )
    return await call_next(request)


def _extract_user_id_for_ratelimit(request: Request) -> str | None:
    """Extract a stable identity for rate limiting — JWT sub, API key prefix, or IP."""
    # Try JWT from Authorization header
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            from core.auth import decode_jwt
            payload = decode_jwt(auth[7:])
            if payload:
                return payload.get("sub") or payload.get("user_id")
        except Exception:
            pass
    # Try API key header
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        return f"apikey:{api_key[:12]}"
    # Fall back to client IP
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    return f"ip:{ip}"

# ---------------------------------------------------------------------------
# Prometheus metrics middleware + endpoint
# ---------------------------------------------------------------------------

from core.metrics import metrics_middleware, metrics_endpoint
metrics_middleware(app)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from api.routes import router as tasks_router
from api.ws import router as ws_router
from api.auth import router as auth_router
from api.workers import router as workers_router
from api.admin import router as admin_router
from api.stripe_webhook import router as billing_router
from api.webhooks import router as webhooks_router
from api.orgs import router as orgs_router
from api.assets import router as assets_router
from api.scenes_manage import router as scenes_manage_router
from api.freecad_templates import router as freecad_templates_router
from api.finishes import router as finishes_router
from api.tickets import router as tickets_router
from worker.callback import router as worker_router

app.include_router(auth_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")
app.include_router(ws_router, prefix="/api")
app.include_router(workers_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(billing_router, prefix="/api")
app.include_router(webhooks_router, prefix="/api")
app.include_router(orgs_router, prefix="/api")
app.include_router(assets_router, prefix="/api")
app.include_router(scenes_manage_router, prefix="/api")
app.include_router(freecad_templates_router, prefix="/api")
app.include_router(finishes_router, prefix="/api")
app.include_router(tickets_router, prefix="/api")
app.include_router(worker_router, prefix="/api")

# Metrics endpoint (no prefix, at root)
app.add_route("/metrics", metrics_endpoint, include_in_schema=False)

# Serve uploaded files — local or S3 proxy
upload_dir = Path(settings.upload_dir)
upload_dir.mkdir(parents=True, exist_ok=True)


@app.get("/uploads/{file_path:path}")
async def serve_upload(file_path: str):
    if settings.storage_backend == "s3":
        # Proxy from S3 — workers can't follow redirects
        import httpx
        storage = get_storage()
        url = await storage.get_url(file_path)
        if not url:
            return Response(status_code=404)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url)
            return Response(content=resp.content, media_type=resp.headers.get("content-type", "application/octet-stream"))
    local = upload_dir / file_path
    if local.is_file():
        return FileResponse(str(local))
    return Response(status_code=404)


# Serve output files (render results) — local first, then S3 proxy
output_dir = Path(settings.output_dir)
output_dir.mkdir(parents=True, exist_ok=True)


@app.get("/outputs/{file_path:path}")
async def serve_output(file_path: str):
    # Try local filesystem first (file was rendered and saved locally)
    local = output_dir / file_path
    if local.is_file():
        media_type = "image/png" if local.suffix == ".png" else "application/octet-stream"
        return FileResponse(str(local), media_type=media_type)
    # Fall back to S3 proxy
    if settings.storage_backend == "s3":
        import httpx
        storage = get_storage()
        s3_key = f"outputs/{file_path}"
        url = await storage.get_url(s3_key)
        if not url:
            return Response(status_code=404)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url)
            return Response(content=resp.content, media_type=resp.headers.get("content-type", "image/png"))
    return Response(status_code=404)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "service": "blenderserver", "version": "0.4.0"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        log_level="info",
    )
