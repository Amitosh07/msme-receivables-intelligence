"""
Database initialization and readiness check utilities.
"""

import logging
from sqlalchemy import text
from backend.app.db.session import engine

logger = logging.getLogger(__name__)


def check_db_connection() -> bool:
    """Check whether database connection is active and responsive."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1;"))
        return True
    except Exception as e:
        logger.error("Database connection check failed: %s", type(e).__name__)
        return False
