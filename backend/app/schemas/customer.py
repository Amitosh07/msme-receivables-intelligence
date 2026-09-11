"""Pydantic contracts for tenant-scoped customers."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomerCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    gstin: str | None = Field(default=None, max_length=32)
    customer_ref: str | None = Field(default=None, max_length=64)


class CustomerResponse(BaseModel):
    id: uuid.UUID
    business_id: uuid.UUID
    display_name: str
    normalized_name: str
    gstin: str | None = None
    customer_ref: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
