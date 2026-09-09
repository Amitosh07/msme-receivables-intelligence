"""
Worker Service Runtime.
Single worker process that polls Redis task queue, claims tasks in PostgreSQL,
dispatches to TaskRouter, and manages graceful shutdown.
"""

import logging
import signal
import sys
import time
import uuid
from typing import Any, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import SessionLocal
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.services.parser import PermanentParserError, TransientParserError
from backend.app.services.task_service import (
    claim_task,
    complete_task,
    fail_task,
    get_pending_tasks,
)
from backend.app.workers.queue import TaskQueue, get_task_queue
from backend.app.workers.router import TaskRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Worker] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("worker_runtime")


class WorkerService:
    """
    Single Worker Service executing background tasks.
    Enforces at-least-once delivery semantics with database-backed task state.
    """

    def __init__(self, queue: Optional[TaskQueue] = None) -> None:
        self.queue = queue or get_task_queue()
        self.running = False

    def setup_signal_handlers(self) -> None:
        """Register signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        logger.info("Shutdown signal received (%s). Stopping worker gracefully...", signum)
        self.running = False

    def recover_pending_tasks(self, db: Session) -> int:
        """
        Scans PostgreSQL for PENDING tasks and re-enqueues them to Redis.
        Ensures tasks survive temporary Redis restarts or disconnections.
        """
        pending = get_pending_tasks(db, limit=100)
        re_enqueued = 0
        for task in pending:
            try:
                self.queue.enqueue(
                    task_id=task.id,
                    task_type=task.task_type,
                    business_id=task.business_id,
                    payload=task.payload or {},
                )
                re_enqueued += 1
            except Exception as e:
                logger.warning("Could not re-enqueue pending task %s: %s", task.id, e)
                break
        if re_enqueued > 0:
            logger.info("Startup recovery: Re-enqueued %d pending task(s) from PostgreSQL.", re_enqueued)
        return re_enqueued

    def process_one_task(self, timeout: int = 1) -> bool:
        """
        Pops and executes one task from Redis queue.
        Returns True if a task was processed, False if queue was empty.
        """
        message = self.queue.dequeue(timeout=timeout)
        if not message:
            return False

        task_id_str = message.get("task_id")
        if not task_id_str:
            logger.error("Dequeued message missing 'task_id': %s", message)
            return False

        try:
            task_id = uuid.UUID(str(task_id_str))
        except ValueError:
            logger.error("Invalid task_id UUID: %s", task_id_str)
            return False

        db: Session = SessionLocal()
        try:
            # 1. Atomically claim task in PostgreSQL
            task = claim_task(db, task_id)
            if not task:
                logger.info("Task %s already claimed or not in PENDING state. Skipping.", task_id)
                return True

            # 2. Dispatch to TaskRouter
            payload = message.get("payload", {})
            try:
                TaskRouter.dispatch(db, task, payload)
                # 3. Mark completed in PostgreSQL
                complete_task(db, task.id, task.invoice_id)

            except TransientParserError as e:
                logger.warning("Transient error processing task %s: %s", task_id, e)
                fail_task(db, task_id, str(e), retryable=True)
                db_task = db.get(type(task), task_id)
                if db_task and db_task.status == "PENDING":
                    try:
                        self.queue.enqueue(
                            task_id=db_task.id,
                            task_type=db_task.task_type,
                            business_id=db_task.business_id,
                            payload=db_task.payload,
                        )
                    except Exception as q_err:
                        logger.warning("Failed to re-enqueue retried task %s: %s", task_id, q_err)

            except (PermanentParserError, Exception) as e:
                logger.error("Permanent failure processing task %s: %s", task_id, e)
                # Roll back uncommitted transaction state if any
                try:
                    db.rollback()
                except Exception:
                    pass

                # If task refers to an invoice_document belonging to this business, ensure its status is ERROR
                doc_id_str = payload.get("invoice_document_id")
                if doc_id_str:
                    try:
                        doc_id = uuid.UUID(str(doc_id_str))
                        stmt_doc = (
                            update(InvoiceDocument)
                            .where(
                                InvoiceDocument.id == doc_id,
                                InvoiceDocument.business_id == task.business_id,
                            )
                            .values(processing_status="ERROR")
                        )
                        db.execute(stmt_doc)
                        db.commit()
                    except Exception as doc_err:
                        logger.warning("Could not update document status to ERROR for task %s: %s", task_id, doc_err)

                # Persist safe, single-line error message without leaking internal secrets
                clean_error = str(e).split("\n")[0][:500]
                fail_task(db, task_id, clean_error, retryable=False)

            return True

        finally:
            db.close()

    def run(self) -> None:
        """Main worker execution loop."""
        self.running = True
        self.setup_signal_handlers()

        logger.info("Starting MSME Receivables Worker Service v1.0...")
        logger.info("Connecting to Redis at: %s", self.queue.redis_url)

        # Startup recovery
        db: Session = SessionLocal()
        try:
            self.recover_pending_tasks(db)
        finally:
            db.close()

        logger.info("Worker ready. Listening for tasks on queue '%s'...", self.queue.queue_name)

        while self.running:
            try:
                processed = self.process_one_task(timeout=1)
                if not processed:
                    time.sleep(settings.WORKER_POLL_INTERVAL_SECONDS)
            except Exception as e:
                logger.error("Unexpected error in worker loop: %s", e)
                time.sleep(1.0)

        logger.info("Worker service shutdown complete.")


def run_worker() -> None:
    """CLI entry point for running the worker service."""
    worker = WorkerService()
    worker.run()


if __name__ == "__main__":
    run_worker()
