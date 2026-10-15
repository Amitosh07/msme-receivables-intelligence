"""Tenant-scoped task status. PostgreSQL is the source of truth, not Redis."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.dependencies import TenantContext, get_db, get_tenant_context
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.models.task import Task
from backend.app.schemas.task import TaskStatusResponse

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.get(
    "/{task_id}",
    response_model=TaskStatusResponse,
    summary="Read the processing status of one task",
)
def get_task_status(
    task_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TaskStatusResponse:
    """Return queued, processing, completed, or failed for a task in this tenant."""
    task = db.scalar(
        select(Task).where(
            Task.id == task_id,
            Task.business_id == tenant_ctx.business_id,
        )
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")

    payload = task.payload if isinstance(task.payload, dict) else {}
    document_id = None
    raw_document_id = payload.get("invoice_document_id")
    if raw_document_id:
        try:
            document_id = uuid.UUID(str(raw_document_id))
        except ValueError:
            document_id = None

    document = None
    if document_id is not None:
        document = db.scalar(
            select(InvoiceDocument).where(
                InvoiceDocument.id == document_id,
                InvoiceDocument.business_id == tenant_ctx.business_id,
            )
        )

    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else None
    error_message = None
    if document is not None and document.error_message:
        error_message = document.error_message
    elif task.status == "FAILED":
        error_message = task.error_message

    return TaskStatusResponse.from_task(
        task,
        document_id=document.id if document else document_id,
        processing_status=document.processing_status if document else None,
        error_message=error_message,
        identity=identity,
    )
