"""Deterministic queue double for worker lifecycle tests.

It deliberately mirrors the small TaskQueue interface. Production always uses
Redis; this only lets database/worker state transitions be tested when Redis is
not installed on a developer workstation or CI runner.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, Optional
import uuid


class InMemoryTaskQueue:
    redis_url = "memory://tests"
    queue_name = "test-memory-queue"

    def __init__(self) -> None:
        self._messages: deque[Dict[str, Any]] = deque()

    def ping(self) -> bool:
        return True

    def enqueue(
        self,
        task_id: uuid.UUID,
        task_type: str,
        business_id: uuid.UUID,
        payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        self._messages.append({
            "task_id": str(task_id),
            "task_type": task_type,
            "business_id": str(business_id),
            "payload": payload or {},
        })
        return True

    def dequeue(self, timeout: int = 1) -> Optional[Dict[str, Any]]:
        return self._messages.popleft() if self._messages else None

    def size(self) -> int:
        return len(self._messages)

    def clear(self) -> None:
        self._messages.clear()
