"""Pydantic contracts for tenant-scoped customers."""

import uuid
from datetime import date, datetime

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


class CustomerBalance(BaseModel):
    """What one customer owes now, and how they have paid before."""
    customer_id: uuid.UUID | None = None
    display_name: str
    matched: bool
    outstanding: float
    overdue: float
    share_of_outstanding: float
    open_invoice_count: int
    overdue_invoice_count: int
    oldest_days_overdue: int | None = None
    next_due_date: date | None = None
    next_expected_payment: date | None = None
    highest_risk_tier: str | None = None
    settled_invoice_count: int = 0
    on_time_rate: float | None = None
    average_days_late: float | None = None
    estimates_ready: bool = False
    has_history: bool = False


class CustomerBalancesResponse(BaseModel):
    as_of: date
    currency: str
    mixed_currency: bool = False
    total_outstanding: float
    customers_owing: int
    top_customer_share: float
    top_five_share: float
    required_history: int
    customers: list[CustomerBalance]
