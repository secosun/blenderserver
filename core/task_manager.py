"""Manages task lifecycle — create, build intent, dispatch, claim, update."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from core.config import settings
from core.db import AsyncDatabase
from core.intent_parser import LLMIntentParser
from core.scenes import get_scene
from models.schemas import TaskStatus


class TaskManager:
    """Async task manager."""

    def __init__(self):
        self.db = AsyncDatabase(settings.database_url)
        self._parser: LLMIntentParser | None = None

    async def initialize(self):
        await self.db.initialize()

    async def close(self):
        await self.db.close()

    @property
    def parser(self) -> LLMIntentParser:
        if self._parser is None:
            self._parser = LLMIntentParser()
        return self._parser

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    async def create_task(self, model_id: str, prompt: str, user_id: str = "anonymous",
                          scene_id: str | None = None, storage_path: str = "",
                          camera_styles: list[str] | None = None,
                          name: str | None = None,
                          output_format: str | None = None) -> dict:
        task_id = str(uuid.uuid4())
        scene = get_scene(scene_id) if scene_id else None
        scene_name = scene.name if scene else None

        task = await self.db.create_task(
            id=task_id, user_id=user_id, model_id=model_id,
            prompt=prompt or "", scene_id=scene_id,
            scene_name=scene_name, storage_path=storage_path,
            name=name or "",
        )

        # Store output_format in intent_json
        extras = {}
        if camera_styles:
            extras["camera_styles"] = camera_styles
        if output_format:
            extras["output_format"] = output_format
        if extras:
            existing = task.get("intent_json") or {}
            existing.update(extras)
            await self.db.update_task_status(task_id, TaskStatus.pending, intent_json=existing)

        # If camera_styles provided, store in intent_json for later
        if camera_styles:
            intent = {"camera_styles": camera_styles}
            await self.db.update_task_status(task_id, TaskStatus.pending, intent_json=intent)

        return self._format_task(task)

    async def get_task(self, task_id: str) -> dict | None:
        task = await self.db.get_task(task_id)
        return self._format_task(task) if task else None

    async def list_tasks(self, user_id: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        tasks, total = await self.db.list_tasks(user_id, limit, offset)
        return [self._format_task(t) for t in tasks], total

    # ------------------------------------------------------------------
    # Build render intent & dispatch
    # ------------------------------------------------------------------

    async def build_intent(self, task_id: str) -> dict:
        task = await self.db.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        scene_id = task.get("scene_id")
        prompt = task.get("prompt", "")
        model_path = task.get("storage_path", "")
        scene = get_scene(scene_id) if scene_id else None

        # Preserve camera_styles from initial creation
        existing_intent = task.get("intent_json") or {}

        if scene and not prompt:
            intent = dict(scene.params)
            intent["model_path"] = model_path
            # Carry over camera_styles if set
            if existing_intent.get("camera_styles"):
                intent["camera_styles"] = existing_intent["camera_styles"]
            await self.db.update_task_status(task_id, TaskStatus.ready, intent_json=intent)
            return await self.get_task(task_id)

        try:
            scene_params = scene.params if scene else None
            result = self.parser.parse(prompt, model_path, scene_params)
            intent = result.get("intent", {})
            if model_path:
                intent["model_path"] = model_path
            if existing_intent.get("camera_styles"):
                intent["camera_styles"] = existing_intent["camera_styles"]
            await self.db.update_task_status(task_id, TaskStatus.ready, intent_json=intent)
        except Exception as e:
            if scene:
                intent = dict(scene.params)
                intent["model_path"] = model_path
                if existing_intent.get("camera_styles"):
                    intent["camera_styles"] = existing_intent["camera_styles"]
                await self.db.update_task_status(task_id, TaskStatus.ready, intent_json=intent)
            else:
                await self.db.update_task_status(
                    task_id, TaskStatus.pending,
                    error_message=f"LLM unavailable: {e}",
                )

        return await self.get_task(task_id)

    async def dispatch_task(self, task_id: str) -> dict:
        task = await self.db.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task["status"] not in (TaskStatus.ready.value, TaskStatus.pending.value):
            raise ValueError(f"Task {task_id} cannot be dispatched (status: {task['status']})")
        await self.db.update_task_status(task_id, TaskStatus.queued)
        return await self.get_task(task_id)

    # ------------------------------------------------------------------
    # Worker callback
    # ------------------------------------------------------------------

    async def update_progress(self, task_id: str, status: TaskStatus, progress: float = 0.0,
                              message: str = "", result_url: str | None = None,
                              error_message: str | None = None,
                              result_urls: list[str] | None = None,
                              stage_name: str | None = None,
                              stage_progress: float | None = None,
                              eta_seconds: int | None = None) -> dict:
        kwargs = {"progress": progress, "progress_message": message}
        if result_url is not None:
            kwargs["result_url"] = result_url
        if error_message is not None:
            kwargs["error_message"] = error_message
        if result_urls is not None:
            kwargs["result_urls_json"] = json.dumps(result_urls, ensure_ascii=False)
        if stage_name is not None:
            kwargs["stage_name"] = stage_name
        if stage_progress is not None:
            kwargs["stage_progress"] = stage_progress
        if eta_seconds is not None:
            kwargs["eta_seconds"] = eta_seconds
        await self.db.update_task_status(task_id, status, **kwargs)
        return await self.get_task(task_id)

    async def claim_next_task(self) -> dict | None:
        task = await self.db.claim_next_task()
        return self._format_task(task) if task else None

    # ------------------------------------------------------------------
    # SLA: Task timeout
    # ------------------------------------------------------------------

    async def fail_stuck_tasks(self, timeout_minutes: int = 30):
        """Mark tasks stuck in 'running' longer than timeout_minutes as failed."""
        from sqlalchemy import text
        import time
        cutoff_ts = time.time() - timeout_minutes * 60
        tasks = await self.db._fetchall(
            text("SELECT id, created_at FROM tasks WHERE status = 'running'")
        )
        for t in tasks:
            try:
                created = datetime.fromisoformat(t["created_at"]).timestamp()
            except (ValueError, TypeError):
                continue
            if created < cutoff_ts:
                await self.db.update_task_status(
                    t["id"], TaskStatus.failed,
                    error_message=f"Task timed out after {timeout_minutes} minutes",
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_task(self, task: dict) -> dict:
        result = {
            "id": task["id"],
            "user_id": task["user_id"],
            "model_id": task.get("model_id", ""),
            "name": task.get("name") or None,
            "prompt": task.get("prompt", ""),
            "scene_id": task.get("scene_id"),
            "scene_name": task.get("scene_name"),
            "status": task["status"],
            "intent_json": task.get("intent_json"),
            "storage_path": task.get("storage_path"),
            "result_url": task.get("result_url"),
            "error_message": task.get("error_message"),
            "progress": task.get("progress", 0.0),
            "progress_message": task.get("progress_message", ""),
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
        }

        # Parse result_urls_json
        result_urls_json = task.get("result_urls_json")
        if result_urls_json:
            try:
                result["result_urls"] = json.loads(result_urls_json)
            except (json.JSONDecodeError, TypeError):
                pass

        # Stage tracking fields
        if task.get("stage_name"):
            result["stage_name"] = task["stage_name"]
        if task.get("stage_progress") is not None:
            result["stage_progress"] = task["stage_progress"]
        if task.get("eta_seconds") is not None:
            result["eta_seconds"] = task["eta_seconds"]
        if task.get("retry_count") is not None:
            result["retry_count"] = task["retry_count"]

        return result
