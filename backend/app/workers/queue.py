"""
Redis-backed task queue for asynchronous job dispatch.
Provides FIFO enqueue/dequeue with connection resiliency and serialization.
"""

import json
import logging
import uuid
from typing import Any, Dict, Optional
import redis

from backend.app.core.config import settings

logger = logging.getLogger(__name__)


class QueueConnectionError(Exception):
    """Raised when Redis queue is unreachable."""
    pass


class TaskQueue:
    """
    Redis task queue implementing reliable FIFO message passing.
    Redis is used for lightweight coordination; PostgreSQL remains authoritative.
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        queue_name: Optional[str] = None,
    ) -> None:
        self.redis_url = redis_url or settings.REDIS_URL
        self.queue_name = queue_name or settings.REDIS_TASK_QUEUE_NAME
        self._client: Optional[redis.Redis] = None

    @property
    def client(self) -> redis.Redis:
        """Lazily initialize Redis client connection."""
        if self._client is None:
            self._client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
        return self._client

    def ping(self) -> bool:
        """Check whether Redis server is responsive."""
        try:
            return bool(self.client.ping())
        except Exception as e:
            logger.warning("Redis ping failed: %s", e)
            return False

    def enqueue(
        self,
        task_id: uuid.UUID,
        task_type: str,
        business_id: uuid.UUID,
        payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Enqueues a task to the Redis list.
        Payload is JSON-serialized and pushed to the queue.
        Returns True on success, raises QueueConnectionError on Redis failure.
        """
        message = {
            "task_id": str(task_id),
            "task_type": task_type,
            "business_id": str(business_id),
            "payload": payload or {},
        }
        try:
            serialized = json.dumps(message)
            self.client.lpush(self.queue_name, serialized)
            logger.debug("Enqueued task %s (%s) to %s", task_id, task_type, self.queue_name)
            return True
        except Exception as e:
            logger.error("Failed to enqueue task %s to Redis: %s", task_id, e)
            raise QueueConnectionError(f"Redis queue unavailable: {e}") from e

    def dequeue(self, timeout: int = 1) -> Optional[Dict[str, Any]]:
        """
        Pops a task from the tail of the queue (FIFO) blocking up to timeout seconds.
        Returns deserialized dictionary or None if queue is empty.
        """
        try:
            result = self.client.brpop(self.queue_name, timeout=timeout)
            if not result:
                return None
            _queue, raw_data = result
            return json.loads(raw_data)
        except redis.TimeoutError:
            return None
        except Exception as e:
            logger.error("Error dequeuing task from Redis: %s", e)
            return None

    def size(self) -> int:
        """Return the current length of the task queue."""
        try:
            return int(self.client.llen(self.queue_name))
        except Exception:
            return 0

    def clear(self) -> None:
        """Remove all tasks from the queue (used for testing and teardown)."""
        try:
            self.client.delete(self.queue_name)
        except Exception:
            pass


_default_queue: Optional[TaskQueue] = None


def get_task_queue() -> TaskQueue:
    """Singleton getter for application task queue."""
    global _default_queue
    if _default_queue is None:
        _default_queue = TaskQueue()
    return _default_queue
