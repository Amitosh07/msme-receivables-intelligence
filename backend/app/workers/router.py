"""
Task router for dispatching queued tasks to registered handlers.
"""

import logging
from typing import Any, Callable, Dict
from sqlalchemy.orm import Session

from backend.app.models.task import Task
from backend.app.services.parser import PermanentParserError
from backend.app.workers.handlers import handle_parse_invoice, handle_predict_invoice

logger = logging.getLogger(__name__)

# Registry of task handlers
TASK_HANDLERS: Dict[str, Callable[[Session, Task, Dict[str, Any]], None]] = {
    "parse_invoice": handle_parse_invoice,
    "predict_invoice": handle_predict_invoice,
    "score_invoice": handle_predict_invoice,
}


class TaskRouter:
    """Routes background task messages to appropriate handler functions."""

    @classmethod
    def dispatch(
        cls,
        db: Session,
        task: Task,
        payload: Dict[str, Any],
    ) -> None:
        """
        Dispatches task to registered handler based on task_type.
        Raises PermanentParserError if task_type is unrecognized.
        """
        handler = TASK_HANDLERS.get(task.task_type)
        if not handler:
            raise PermanentParserError(
                f"Unknown task type '{task.task_type}'. "
                f"Registered types: {list(TASK_HANDLERS.keys())}"
            )

        logger.info("Dispatching task id=%s type='%s'", task.id, task.task_type)
        handler(db, task, payload)
