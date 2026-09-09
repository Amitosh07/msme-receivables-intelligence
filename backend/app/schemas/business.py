"""
Pydantic schemas for Business entity.
"""

import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class BusinessBase(BaseModel):
    name: str
    business_code: str | None = None
    currency: str = "USD"


class BusinessCreate(BusinessBase):
    pass


class BusinessResponse(BusinessBase):
    id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
