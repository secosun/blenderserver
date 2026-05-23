"""Structured JSON logging configuration.

Usage::

    from core.logging_config import setup_logging
    setup_logging()
    logger = logging.getLogger("blenderserver")
    logger.info("Task completed", extra={"task_id": "xxx", "duration_s": 12.3})

Outputs JSON lines like::

    {"ts": "2026-05-21T10:30:00Z", "level": "INFO", "logger": "blenderserver",
     "msg": "Task completed", "task_id": "xxx", "duration_s": 12.3}
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Output log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Add exception info
        if record.exc_info and record.exc_info[0]:
            data["exc"] = self.formatException(record.exc_info)

        # Add extra fields from the record
        for key, value in record.__dict__.items():
            if key not in ("args", "asctime", "created", "exc_info", "exc_text",
                           "filename", "funcName", "levelname", "levelno", "lineno",
                           "module", "msecs", "message", "msg", "name", "pathname",
                           "process", "processName", "relativeCreated", "stack_info",
                           "thread", "threadName"):
                data[key] = value

        return json.dumps(data, default=str, ensure_ascii=False)


def setup_logging(level: int = logging.INFO, json_format: bool = True):
    """Configure root logger with structured JSON output."""
    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
        ))

    root = logging.getLogger()
    root.setLevel(level)
    # Remove existing handlers and add ours
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(handler)

    # Set specific loggers
    logging.getLogger("blenderserver").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
