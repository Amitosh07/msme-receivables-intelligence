"""
Task lifecycle service for database-backed asynchronous task tracking.
PostgreSQL is the authoritative source of truth for task status.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.task import Task

logger = logging.getLogger(__name__)


def create_task(
    db: Session,
    business_id: uuid.UUID,
    task_type: str,
    payload: Optional[Dict[str, Any]] = None,
    invoice_id: Optional[uuid.UUID] = None,
) -> Task:
    """
    Creates and persists a new Task in PostgreSQL with status PENDING.
    """
    task = Task(
        id=uuid.uuid4(),
        business_id=business_id,
        task_type=task_type,
        status="PENDING",
        payload=payload or {},
        invoice_id=invoice_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def claim_task(db: Session, task_id: uuid.UUID) -> Optional[Task]:
    """
    Atomically claims a task for processing: transitions status from PENDING to PROCESSING.
    Safe against concurrent worker executions.
    Returns the claimed Task or None if already claimed.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        update(Task)
        .where(Task.id == task_id, Task.status == "PENDING")
        .values(status="PROCESSING", started_at=now)
        .returning(Task)
    )
    task = db.scalar(stmt)
    if task:
        db.commit()
    return task


def complete_task(
    db: Session,
    task_id: uuid.UUID,
    invoice_id: Optional[uuid.UUID] = None,
) -> None:
    """
    Marks a task as COMPLETED with completion timestamp.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        update(Task)
        .where(Task.id == task_id)
        .values(
            status="COMPLETED",
            completed_at=now,
            invoice_id=invoice_id or Task.invoice_id,
        )
    )
    db.execute(stmt)
    db.commit()
    logger.info("Task completed: id=%s", task_id)


def fail_task(
    db: Session,
    task_id: uuid.UUID,
    error_message: str,
    retryable: bool = False,
) -> None:
    """
    Records a task failure. If retryable and attempts < MAX_TASK_RETRIES,
    resets status to PENDING for re-attempt. Otherwise transitions to FAILED.
    """
    now = datetime.now(timezone.utc)
    task = db.get(Task, task_id)
    if not task:
        return

    payload = dict(task.payload or {})
    attempts = payload.get("attempt", 1)
    payload["last_error"] = error_message
    payload["last_attempt_at"] = now.isoformat()

    if retryable and attempts < settings.MAX_TASK_RETRIES:
        payload["attempt"] = attempts + 1
        stmt = (
            update(Task)
            .where(Task.id == task_id)
            .values(
                status="PENDING",
                payload=payload,
                error_message=f"Attempt {attempts} failed: {error_message}",
            )
        )
        db.execute(stmt)
        db.commit()
        logger.warning(
            "Task id=%s failed (transient, attempt %d/%d). Reset to PENDING.",
            task_id, attempts, settings.MAX_TASK_RETRIES,
        )
    else:
        stmt = (
            update(Task)
            .where(Task.id == task_id)
            .values(
                status="FAILED",
                completed_at=now,
                error_message=error_message,
                payload=payload,
            )
        )
        db.execute(stmt)
        db.commit()
        logger.error(
            "Task id=%s permanently failed: %s (attempts=%d)",
            task_id, error_message, attempts,
        )


def get_pending_tasks(
    db: Session,
    task_type: Optional[str] = None,
    limit: int = 100,
) -> List[Task]:
    """
    Retrieves pending tasks for startup recovery if Redis was temporarily down.
    """
    query = select(Task).where(Task.status == "PENDING")
    if task_type:
        query = query.where(Task.task_type == task_type)
    return list(db.scalars(query.order_by(Task.created_at.asc()).limit(limit)).all())
