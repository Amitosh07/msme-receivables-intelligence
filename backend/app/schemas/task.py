"""Pydantic schemas for tenant-scoped task status."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel


_STATES = {
    "PENDING": "queued",
    "PROCESSING": "processing",
    "COMPLETED": "completed",
    "FAILED": "failed",
}


class TaskStatusResponse(BaseModel):
    """Authoritative processing state for one task owned by the caller."""

    id: uuid.UUID
    task_type: str
    status: str
    state: str
    invoice_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    processing_status: str | None = None
    error_message: str | None = None
    identity: dict[str, Any] | None = None

    @classmethod
    def from_task(
        cls,
        task,
        *,
        document_id: uuid.UUID | None,
        processing_status: str | None,
        error_message: str | None,
        identity: dict | None,
    ) -> TaskStatusResponse:
        status = task.status or "PENDING"
        return cls(
            id=task.id,
            task_type=task.task_type,
            status=status,
            state=_STATES.get(status, "processing"),
            invoice_id=task.invoice_id,
            document_id=document_id,
            processing_status=processing_status,
            error_message=error_message,
            identity=identity or None,
        )

