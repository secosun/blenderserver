from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.config import settings


@dataclass
class QueueMessage:
    task_id: str
    intent: dict
    created_at: str
    retries: int = 0


_MAX_RETRIES = 3
_DEAD_LETTER_KEY = "cadrender:queue:dead"


class MessageQueue(ABC):
    """Abstract message queue for dispatching render tasks to workers."""

    @abstractmethod
    async def connect(self):
        ...

    @abstractmethod
    async def disconnect(self):
        ...

    @abstractmethod
    async def publish(self, task_id: str, intent: dict):
        ...

    @abstractmethod
    async def consume(self, timeout: float = 5.0) -> QueueMessage | None:
        ...

    @abstractmethod
    async def acknowledge(self, task_id: str):
        ...

    @abstractmethod
    async def nack(self, task_id: str, requeue: bool = True):
        ...

    @abstractmethod
    async def dead_letter_count(self) -> int:
        """Return number of messages in the dead-letter queue."""
        ...

    @abstractmethod
    async def pending_count(self) -> int:
        """Return number of messages pending consumption."""
        ...


# ---------------------------------------------------------------------------
# In-memory backend (development / single-process)
# ---------------------------------------------------------------------------

class InMemoryQueue(MessageQueue):
    """Simple in-memory queue for development. NOT for production use."""

    def __init__(self):
        self._queue: list[QueueMessage] = []
        self._in_flight: dict[str, QueueMessage] = {}
        self._dead: list[QueueMessage] = []

    async def connect(self):
        pass

    async def disconnect(self):
        self._queue.clear()
        self._in_flight.clear()
        self._dead.clear()

    async def publish(self, task_id: str, intent: dict):
        msg = QueueMessage(
            task_id=task_id,
            intent=intent,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._queue.append(msg)

    async def consume(self, timeout: float = 5.0) -> QueueMessage | None:
        if not self._queue:
            return None
        msg = self._queue.pop(0)
        self._in_flight[msg.task_id] = msg
        return msg

    async def acknowledge(self, task_id: str):
        self._in_flight.pop(task_id, None)

    async def nack(self, task_id: str, requeue: bool = True):
        msg = self._in_flight.pop(task_id, None)
        if msg is None:
            return
        if requeue and msg.retries < _MAX_RETRIES:
            msg.retries += 1
            self._queue.append(msg)
        else:
            self._dead.append(msg)

    async def dead_letter_count(self) -> int:
        return len(self._dead)

    async def pending_count(self) -> int:
        return len(self._queue)


# ---------------------------------------------------------------------------
# Redis backend (production)
# ---------------------------------------------------------------------------

class RedisQueue(MessageQueue):
    """Production Redis-backed message queue with dead-letter support.

    Uses a Redis List as the pending queue and a separate List/set for
    in-flight messages with lease-based visibility timeout.
    """

    _QUEUE_KEY = "cadrender:queue:pending"
    _IN_FLIGHT_KEY = "cadrender:queue:inflight"
    _DEAD_KEY = _DEAD_LETTER_KEY
    _LEASE_SECONDS = 300  # 5 min visibility timeout

    def __init__(self, redis_url: str = ""):
        self._redis_url = redis_url or settings.redis_url
        self._redis = None

    async def connect(self):
        import redis.asyncio as aioredis
        self._redis = await aioredis.from_url(
            self._redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
        # Verify connection
        await self._redis.ping()

    async def disconnect(self):
        if self._redis:
            await self._redis.close()
            self._redis = None

    async def publish(self, task_id: str, intent: dict):
        msg = json.dumps({
            "task_id": task_id,
            "intent": intent,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "retries": 0,
        })
        await self._redis.lpush(self._QUEUE_KEY, msg)

    async def consume(self, timeout: float = 5.0) -> QueueMessage | None:
        # brpoplpush: atomically move from pending → in-flight with timeout
        msg_json = await self._redis.brpoplpush(
            self._QUEUE_KEY, self._IN_FLIGHT_KEY, timeout=timeout,
        )
        if msg_json is None:
            return None
        data = json.loads(msg_json)
        # Set a TTL on the in-flight entry (auto-reclaim if worker dies)
        await self._redis.expire(self._IN_FLIGHT_KEY, self._LEASE_SECONDS)
        return QueueMessage(
            task_id=data["task_id"],
            intent=data["intent"],
            created_at=data.get("created_at", ""),
            retries=data.get("retries", 0),
        )

    async def acknowledge(self, task_id: str):
        """Remove the message from in-flight."""
        # Since we use brpoplpush, we need to remove the specific msg from in-flight.
        # We do this by scanning the in-flight list and removing matching entries.
        await self._remove_from_list(self._IN_FLIGHT_KEY, task_id)

    async def nack(self, task_id: str, requeue: bool = True):
        """Negative acknowledgment — requeue or send to dead-letter."""
        msg_json = await self._pop_from_inflight(task_id)
        if msg_json is None:
            return
        data = json.loads(msg_json)
        if requeue and data.get("retries", 0) < _MAX_RETRIES:
            data["retries"] = data.get("retries", 0) + 1
            await self._redis.lpush(self._QUEUE_KEY, json.dumps(data))
        else:
            data["error"] = "max retries exceeded"
            await self._redis.lpush(self._DEAD_KEY, json.dumps(data))

    async def dead_letter_count(self) -> int:
        return await self._redis.llen(self._DEAD_KEY)

    async def pending_count(self) -> int:
        return await self._redis.llen(self._QUEUE_KEY)

    async def _remove_from_list(self, key: str, task_id: str):
        """Remove the first message matching *task_id* from a Redis list."""
        # LREM by scanning — O(n) but acceptable for our scale
        lst = await self._redis.lrange(key, 0, -1)
        for item in lst:
            try:
                data = json.loads(item)
                if data.get("task_id") == task_id:
                    await self._redis.lrem(key, 1, item)
                    return
            except (json.JSONDecodeError, TypeError):
                continue

    async def _pop_from_inflight(self, task_id: str) -> str | None:
        """Find and pop a message from the in-flight list by task_id."""
        lst = await self._redis.lrange(self._IN_FLIGHT_KEY, 0, -1)
        for item in lst:
            try:
                data = json.loads(item)
                if data.get("task_id") == task_id:
                    await self._redis.lrem(self._IN_FLIGHT_KEY, 1, item)
                    return item
            except (json.JSONDecodeError, TypeError):
                continue
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_queue_instance: MessageQueue | None = None


async def get_queue() -> MessageQueue:
    global _queue_instance
    if _queue_instance is None:
        if settings.queue_backend == "redis":
            _queue_instance = RedisQueue()
        else:
            _queue_instance = InMemoryQueue()
        await _queue_instance.connect()
    return _queue_instance
