"""
Health check and system status API endpoints.
"""

from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import get_db

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    summary="Application and database health check",
)
def health_check(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Returns application liveness and database readiness status.
    Does not expose sensitive credentials, SQL details, or stack traces.
    """
    db_status = "healthy"
    try:
        db.execute(text("SELECT 1;"))
    except Exception:
        db_status = "unhealthy"

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "database": db_status,
        "environment": settings.ENVIRONMENT,
        "version": settings.APP_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
