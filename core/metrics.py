"""Prometheus metrics for blenderserver.

Exposes:
- ``cadrender_request_duration_seconds`` — histogram of HTTP latency
- ``cadrender_requests_total`` — counter by method, path, status
- ``cadrender_tasks_total`` — gauge by status (pending, queued, running, etc.)
- ``cadrender_queue_depth`` — gauge of pending queue messages
- ``cadrender_workers_total`` — gauge by status (idle, busy, offline)
- ``cadrender_dead_letter_total`` — gauge of dead-letter queue

Usage::

    from core.metrics import metrics_app
    app.mount("/metrics", metrics_app)
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, REGISTRY

    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False

    # Stub
    class Counter:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): self._v = 0
        def inc(self, *args, **kwargs): self._v += 1
        def labels(self, **kwargs): return self

    class Histogram:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): pass
        def labels(self, **kwargs): return self
        def observe(self, v): pass

    class Gauge:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): self._v = 0
        def inc(self, *args, **kwargs): self._v += 1
        def dec(self, *args, **kwargs): self._v -= 1
        def set(self, v): self._v = v
        def labels(self, **kwargs): return self

    def generate_latest(*args, **kwargs):  # type: ignore[no-redef]
        return b"# prometheus_client not installed\n"

    class REGISTRY:  # type: ignore[no-redef]
        pass


# ── Metrics ──────────────────────────────────────────────────────────

http_request_duration = Histogram(
    "cadrender_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path", "status"],
    buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30),
)

http_requests_total = Counter(
    "cadrender_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

tasks_total = Gauge(
    "cadrender_tasks_total",
    "Tasks by status",
    ["status"],
)

queue_depth = Gauge(
    "cadrender_queue_depth",
    "Pending queue depth",
)

workers_total = Gauge(
    "cadrender_workers_total",
    "Workers by status",
    ["status"],
)

dead_letter_total = Gauge(
    "cadrender_dead_letter_total",
    "Dead-letter queue depth",
)


# ── Helpers ──────────────────────────────────────────────────────────


def _clean_path(path: str) -> str:
    """Normalise request path for metric labels (strip UUIDs)."""
    parts = path.strip("/").split("/")
    cleaned = []
    for p in parts:
        if p in ("tasks", "auth", "workers", "ws", "upload", "scenes") or p.startswith("api"):
            cleaned.append(p)
        elif len(p) == 36 and p.count("-") == 4:  # UUID
            cleaned.append("{id}")
        else:
            cleaned.append(p)
    return "/" + "/".join(cleaned)


def metrics_middleware(app: FastAPI):
    """Add Prometheus instrumentation to a FastAPI app."""
    if not _HAS_PROMETHEUS:
        return

    @app.middleware("http")
    async def instrument_requests(request: Request, call_next: Any) -> Response:
        start = time.monotonic()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            duration = time.monotonic() - start
            path = _clean_path(request.url.path)
            http_request_duration.labels(method=request.method, path=path, status=status).observe(duration)
            http_requests_total.labels(method=request.method, path=path, status=status).inc()
        return response

    # No startup event handler — use start_metrics_loop() called from lifespan


# ── Metrics endpoint ─────────────────────────────────────────────────


async def metrics_endpoint(request: Request) -> Response:
    """Serve Prometheus metrics at ``/metrics``."""
    return PlainTextResponse(generate_latest(REGISTRY).decode("utf-8"))


async def start_metrics_loop(app: FastAPI):
    """Periodically refresh gauge metrics every 30 seconds."""
    import asyncio

    while True:
        await asyncio.sleep(30)
        if not _HAS_PROMETHEUS:
            continue
        try:
            tm = getattr(app.state, "task_manager", None)
            if not tm:
                continue
            db = tm.db

            for status_name in ("pending", "ready", "queued", "running", "completed", "failed", "cancelled"):
                try:
                    count = await db.count_tasks_by_status("", status_name)
                    tasks_total.labels(status=status_name).set(count)
                except Exception:
                    pass

            q = getattr(app.state, "queue", None)
            if q:
                try:
                    depth = await q.pending_count()
                    queue_depth.set(depth)
                except Exception:
                    pass
                try:
                    dl = await q.dead_letter_count()
                    dead_letter_total.set(dl)
                except Exception:
                    pass

            try:
                for s in ("idle", "busy", "offline"):
                    c = await db.count_workers_by_status(s)
                    workers_total.labels(status=s).set(c)
            except Exception:
                pass
        except Exception:
            pass
