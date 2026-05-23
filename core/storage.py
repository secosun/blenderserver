"""Storage abstraction — local filesystem and S3-compatible (MinIO).

Usage::

    from core.storage import get_storage
    storage = get_storage()
    url = await storage.upload("/tmp/file.obj", "uploads/user123/model.obj")
"""

from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from core.config import settings


# ======================================================================
# Abstract backend
# ======================================================================


class StorageBackend(ABC):
    """Abstract object storage backend."""

    @abstractmethod
    async def upload(self, local_path: str, key: str) -> str:
        """Upload a local file to *key*. Returns the accessible URL."""
        ...

    @abstractmethod
    async def upload_bytes(self, data: bytes, key: str) -> str:
        """Upload raw bytes to *key*. Returns the accessible URL."""
        ...

    @abstractmethod
    async def download(self, key: str, local_path: str) -> str:
        """Download *key* to a local file. Returns local_path."""
        ...

    @abstractmethod
    async def download_bytes(self, key: str) -> bytes:
        """Download *key* and return its bytes."""
        ...

    @abstractmethod
    async def get_url(self, key: str) -> str:
        """Return an accessible URL for *key* (may be presigned)."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete the object at *key*. Returns True if existed."""
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if *key* exists."""
        ...


# ======================================================================
# Local filesystem
# ======================================================================


class LocalStorage(StorageBackend):
    """Stores files on the local filesystem under a root directory."""

    def __init__(self, root: str = ""):
        self.root = Path(root or settings.upload_dir)

    def _resolve(self, key: str) -> Path:
        # Prevent path traversal
        safe = Path(key).as_posix().lstrip("/")
        resolved = (self.root / safe).resolve()
        if not str(resolved).startswith(str(self.root.resolve())):
            raise PermissionError(f"Path traversal detected: {key}")
        return resolved

    async def upload(self, local_path: str, key: str) -> str:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(local_path, dest)
        return await self.get_url(key)

    async def upload_bytes(self, data: bytes, key: str) -> str:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return await self.get_url(key)

    async def download(self, key: str, local_path: str) -> str:
        src = self._resolve(key)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(str(src), local_path)
        return local_path

    async def download_bytes(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    async def get_url(self, key: str) -> str:
        if settings.cdn_base_url:
            return f"{settings.cdn_base_url.rstrip('/')}/uploads/{key.lstrip('/')}"
        # Return a relative URL for local serving via StaticFiles
        return f"/uploads/{key.lstrip('/')}"

    async def delete(self, key: str) -> bool:
        p = self._resolve(key)
        if p.exists():
            p.unlink()
            return True
        return False

    async def exists(self, key: str) -> bool:
        return self._resolve(key).exists()


# ======================================================================
# S3-compatible (MinIO, AWS S3, etc.)
# ======================================================================


class S3Storage(StorageBackend):
    """S3-compatible object storage backend.

    Requires ``boto3``.  Config via ``core.config.settings``:

    - ``s3_endpoint`` — e.g. ``http://localhost:9000`` (MinIO)
    - ``s3_region``, ``s3_access_key``, ``s3_secret_key``
    - ``s3_bucket`` — bucket name
    - ``s3_upload_prefix``, ``s3_output_prefix``
    """

    def __init__(self):
        import boto3
        from botocore.config import Config as BotoConfig

        session = boto3.Session(
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        cfg = BotoConfig(
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 3},
        )
        self.client = session.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            config=cfg,
        )
        self.bucket = settings.s3_bucket
        self._ensure_bucket()

    def _ensure_bucket(self):
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def _normalise_key(self, key: str) -> str:
        return key.lstrip("/")

    async def upload(self, local_path: str, key: str) -> str:
        key = self._normalise_key(key)
        self.client.upload_file(local_path, self.bucket, key)
        return await self.get_url(key)

    async def upload_bytes(self, data: bytes, key: str) -> str:
        key = self._normalise_key(key)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return await self.get_url(key)

    async def download(self, key: str, local_path: str) -> str:
        key = self._normalise_key(key)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, local_path)
        return local_path

    async def download_bytes(self, key: str) -> bytes:
        key = self._normalise_key(key)
        resp = self.client.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].read()

    async def get_url(self, key: str) -> str:
        key = self._normalise_key(key)
        if settings.cdn_base_url:
            return f"{settings.cdn_base_url.rstrip('/')}/{key}"
        # Generate presigned URL
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=settings.result_url_ttl_seconds,
        )
        return url

    async def delete(self, key: str) -> bool:
        key = self._normalise_key(key)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    async def exists(self, key: str) -> bool:
        key = self._normalise_key(key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


# ======================================================================
# Factory
# ======================================================================

_storage_instance: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """Return the configured storage backend singleton."""
    global _storage_instance
    if _storage_instance is None:
        if settings.storage_backend == "s3":
            _storage_instance = S3Storage()
        else:
            _storage_instance = LocalStorage()
    return _storage_instance


def reset_storage():
    """Reset the storage singleton (useful in tests)."""
    global _storage_instance
    _storage_instance = None
