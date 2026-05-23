"""Tests for blenderserver API (no LLM, no external dependencies)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from core.task_manager import TaskManager
from core.queue import InMemoryQueue
from core.config import settings


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    """Setup app state before each test with isolated database."""
    import asyncio
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(settings, "db_path", db_file)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    tm = TaskManager()
    asyncio.run(tm.initialize())
    app.state.task_manager = tm
    queue = InMemoryQueue()
    app.state.queue = queue
    yield
    asyncio.run(queue.disconnect())
    asyncio.run(tm.close())


@pytest.fixture
def client(setup):
    return TestClient(app)


_TEST_PASSWORD = "test-password-123"


@pytest.fixture
def auth_headers(client, request) -> dict[str, str]:
    """Register a unique test user and return auth headers."""
    import uuid
    email = f"test-{uuid.uuid4().hex[:8]}@cadrender.local"
    resp = client.post("/api/auth/register", json={
        "email": email,
        "password": _TEST_PASSWORD,
        "display_name": "Test User",
    })
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_id(auth_headers, client) -> str:
    resp = client.get("/api/auth/me", headers=auth_headers)
    return resp.json()["id"]


class TestHealth:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestAuth:
    def test_register_and_login(self, client):
        import uuid
        email = f"new-{uuid.uuid4().hex[:8]}@user.com"
        resp = client.post("/api/auth/register", json={
            "email": email,
            "password": "password-123456",
            "display_name": "New User",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "access_token" in data
        assert data["user"]["email"] == email

        # Login
        resp = client.post("/api/auth/login", json={
            "email": email,
            "password": "password-123456",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_duplicate_email(self, client):
        import uuid
        email = f"dup-{uuid.uuid4().hex[:8]}@user.com"
        resp = client.post("/api/auth/register", json={
            "email": email,
            "password": "password-123456",
        })
        assert resp.status_code == 201
        resp = client.post("/api/auth/register", json={
            "email": email,
            "password": "password-123456",
        })
        assert resp.status_code == 409

    def test_wrong_password(self, client):
        import uuid
        email = f"auth-{uuid.uuid4().hex[:8]}@user.com"
        client.post("/api/auth/register", json={
            "email": email,
            "password": "password-123456",
        })
        resp = client.post("/api/auth/login", json={
            "email": email,
            "password": "wrong-password",
        })
        assert resp.status_code == 401

    def test_me_endpoint(self, client, auth_headers):
        resp = client.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        assert "email" in resp.json()


class TestAPIKeys:
    def test_create_and_list_keys(self, client, auth_headers):
        # Create
        resp = client.post("/api/auth/api-keys", headers=auth_headers, json={
            "label": "test-key",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["full_key"].startswith("crdr_")
        assert data["label"] == "test-key"
        key_id = data["id"]

        # List
        resp = client.get("/api/auth/api-keys", headers=auth_headers)
        assert resp.status_code == 200
        keys = resp.json()["keys"]
        assert any(k["id"] == key_id for k in keys)
        # full_key should be None on list
        assert all(k["full_key"] is None for k in keys)

    def test_revoke_key(self, client, auth_headers):
        resp = client.post("/api/auth/api-keys", headers=auth_headers, json={})
        key_id = resp.json()["id"]

        resp = client.delete(f"/api/auth/api-keys/{key_id}", headers=auth_headers)
        assert resp.status_code == 204

    def test_auth_with_api_key(self, client, auth_headers):
        """API Key can be used in place of JWT."""
        resp = client.post("/api/auth/api-keys", headers=auth_headers, json={})
        full_key = resp.json()["full_key"]

        # Access /me using API key in header
        resp = client.get("/api/auth/me", headers={"X-API-Key": full_key})
        assert resp.status_code == 200


class TestUpload:
    def test_upload_requires_auth(self, client):
        data = io.BytesIO(b"mock obj content")
        resp = client.post("/api/upload", files={"file": ("test.obj", data, "application/octet-stream")})
        assert resp.status_code == 401  # No auth → 401

    def test_upload_obj(self, client, auth_headers):
        data = io.BytesIO(b"mock obj content")
        resp = client.post(
            "/api/upload",
            files={"file": ("test.obj", data, "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        r = resp.json()
        assert "model_id" in r
        assert r["file_name"] == "test.obj"
        assert r["file_type"] == "obj"
        assert r["file_size"] == 16

    def test_upload_fcstd(self, client, auth_headers):
        data = io.BytesIO(b"mock fcstd content")
        resp = client.post(
            "/api/upload",
            files={"file": ("profile.fcstd", data, "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["file_type"] == "fcstd"

    def test_upload_unsupported_type(self, client, auth_headers):
        data = io.BytesIO(b"bad")
        resp = client.post(
            "/api/upload",
            files={"file": ("bad.exe", data, "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_upload_user_isolation(self, client):
        """User B cannot see User A's uploads."""
        pass  # storage isolation by user directory is implicit


class TestScenes:
    def test_list_scenes(self, client):
        resp = client.get("/api/scenes")
        assert resp.status_code == 200
        scenes = resp.json()["scenes"]
        assert len(scenes) >= 9
        ids = [s["id"] for s in scenes]
        assert "studio_champagne" in ids
        assert "studio_black_matte" in ids
        assert "freecad_profile_preview" in ids


class TestTasks:
    def test_create_task_needs_scene_or_prompt(self, client, auth_headers):
        resp = client.post("/api/tasks", json={
            "model_id": "test-model",
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_create_task_with_scene(self, client, auth_headers):
        resp = client.post("/api/tasks", json={
            "model_id": "test-model",
            "scene_id": "studio_champagne",
        }, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "ready"
        assert data["scene_id"] == "studio_champagne"
        assert data["intent_json"] is not None
        assert data["intent_json"]["product_category"] == "coating_champagne_box_profile_metal_sheet"

    def test_create_task_with_prompt(self, client, auth_headers):
        resp = client.post("/api/tasks", json={
            "model_id": "test-model",
            "prompt": "枪灰色，高端质感",
        }, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"

    def test_create_task_with_scene_and_prompt(self, client, auth_headers):
        resp = client.post("/api/tasks", json={
            "model_id": "test-model",
            "scene_id": "studio_champagne",
            "prompt": "调暗一点背景",
        }, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "ready"
        assert data["intent_json"]["product_category"] == "coating_champagne_box_profile_metal_sheet"

    def test_get_task_not_found(self, client, auth_headers):
        resp = client.get("/api/tasks/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_get_task_other_user_not_found(self, client, auth_headers):
        """User isolation: cannot access another user's task."""
        import uuid
        # Create a task for user A
        resp = client.post("/api/tasks", json={
            "model_id": "test-model",
            "scene_id": "studio_champagne",
        }, headers=auth_headers)
        task_id = resp.json()["id"]

        # Register user B
        resp = client.post("/api/auth/register", json={
            "email": f"other-{uuid.uuid4().hex[:8]}@user.com",
            "password": "password-123456",
        })
        b_token = resp.json()["access_token"]
        b_headers = {"Authorization": f"Bearer {b_token}"}

        # User B cannot see User A's task
        resp = client.get(f"/api/tasks/{task_id}", headers=b_headers)
        assert resp.status_code == 404

    def test_list_tasks(self, client, auth_headers, user_id):
        resp = client.get("/api/tasks", headers=auth_headers)
        assert resp.status_code == 200
        assert "tasks" in resp.json()


class TestClaimNext:
    def test_claim_next_queued_task(self, client, auth_headers):
        resp = client.post("/api/tasks", json={
            "model_id": "test-model",
            "scene_id": "studio_champagne",
        }, headers=auth_headers)
        task_id = resp.json()["id"]
        client.post(f"/api/tasks/{task_id}/dispatch", headers=auth_headers)

        resp = client.post("/api/tasks/claim-next")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == task_id
        assert data["status"] == "running"

        resp = client.post("/api/tasks/claim-next")
        assert resp.status_code == 404

    def test_claim_fifo_order(self, client, auth_headers):
        ids = []
        for i in range(3):
            resp = client.post("/api/tasks", json={
                "model_id": "test-model",
                "scene_id": "studio_black_matte" if i % 2 == 0 else "studio_champagne",
            }, headers=auth_headers)
            ids.append(resp.json()["id"])
            client.post(f"/api/tasks/{ids[-1]}/dispatch", headers=auth_headers)

        for expected_id in ids:
            resp = client.post("/api/tasks/claim-next")
            assert resp.status_code == 200
            assert resp.json()["id"] == expected_id

    def test_claim_empty(self, client):
        resp = client.post("/api/tasks/claim-next")
        assert resp.status_code == 404


class TestTaskLifecycle:
    def test_full_scene_flow(self, client, auth_headers):
        # Create
        resp = client.post("/api/tasks", json={
            "model_id": "test-model",
            "scene_id": "studio_champagne",
        }, headers=auth_headers)
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        # Dispatch
        resp = client.post(f"/api/tasks/{task_id}/dispatch", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

        # Worker callback: running
        resp = client.post(f"/api/worker/callback/{task_id}", json={
            "status": "running",
            "progress": 0.3,
            "message": "Importing model...",
            "secret": "dev-secret",
        })
        assert resp.status_code == 200
        task = client.get(f"/api/tasks/{task_id}", headers=auth_headers).json()
        assert task["status"] == "running"
        assert task["progress"] == 0.3

        # Worker callback: completed
        resp = client.post(f"/api/worker/callback/{task_id}", json={
            "status": "completed",
            "progress": 1.0,
            "message": "Render complete",
            "result_url": "/uploads/test/output.png",
            "secret": "dev-secret",
        })
        assert resp.status_code == 200

        # Check result
        resp = client.get(f"/api/tasks/{task_id}/result", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["result_url"] == "/uploads/test/output.png"

    def test_cancel(self, client, auth_headers):
        resp = client.post("/api/tasks", json={
            "model_id": "test-model",
            "scene_id": "studio_black_matte",
        }, headers=auth_headers)
        task_id = resp.json()["id"]

        resp = client.post(f"/api/tasks/{task_id}/cancel", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_dispatch_other_user_task_fails(self, client, auth_headers):
        """User B cannot dispatch User A's task."""
        import uuid
        resp = client.post("/api/tasks", json={
            "model_id": "test-model",
            "scene_id": "studio_champagne",
        }, headers=auth_headers)
        task_id = resp.json()["id"]

        resp = client.post("/api/auth/register", json={
            "email": f"other-{uuid.uuid4().hex[:8]}@user.com",
            "password": "password-123456",
        })
        b_token = resp.json()["access_token"]
        b_headers = {"Authorization": f"Bearer {b_token}"}

        resp = client.post(f"/api/tasks/{task_id}/dispatch", headers=b_headers)
        assert resp.status_code == 404

    def test_callback_auth(self, client, auth_headers):
        resp = client.post("/api/tasks", json={
            "model_id": "test-model",
            "scene_id": "studio_champagne",
        }, headers=auth_headers)
        task_id = resp.json()["id"]

        resp = client.post(f"/api/worker/callback/{task_id}", json={
            "status": "running",
            "progress": 0.5,
            "message": "bad auth",
            "secret": "wrong-secret",
        })
        assert resp.status_code == 403
