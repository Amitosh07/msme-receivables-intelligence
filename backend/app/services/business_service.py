"""
Business service for managing business tenant records.
"""

import uuid
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.business import Business


def get_business_by_id(db: Session, business_id: uuid.UUID) -> Optional[Business]:
    """Retrieve a business tenant by its unique ID."""
    return db.scalar(select(Business).where(Business.id == business_id))
