"""
Pydantic schemas for Payment entity and CSV import responses.
"""

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class ImportErrorRow(BaseModel):
    """Details of a single row rejected during payment CSV ingestion."""
    row_number: int
    reason: str
    raw_data: Optional[Dict[str, Any]] = None


class PaymentImportRowResult(BaseModel):
    """Per-row outcome for a payment import or preview."""

    row_number: int
    status: Literal["imported", "duplicate", "rejected"]
    reason: str
    unmatched: bool = False
    payment_id: Optional[uuid.UUID] = None
    payment_date: Optional[datetime] = None
    invoice_number: Optional[str] = None
    invoice_id: Optional[uuid.UUID] = None
    raw_data: Optional[Dict[str, Any]] = None


class PaymentImportResponse(BaseModel):
    """Summary of payment CSV batch import results."""
    total_rows: int
    valid_rows: int = 0
    imported: int
    matched: int = 0
    duplicates: int
    duplicates_in_file: int = 0
    duplicates_existing: int = 0
    rejected: int
    unmatched: int
    preview: bool = False
    errors: List[ImportErrorRow] = []
    row_results: List[PaymentImportRowResult] = []


class PaymentResponse(BaseModel):
    """Payment record detail response."""
    id: uuid.UUID
    business_id: uuid.UUID
    invoice_id: Optional[uuid.UUID] = None
    invoice_reference: Optional[str] = None
    payment_date: datetime
    amount: float
    reference: Optional[str] = None
    customer_identity_key: Optional[str] = None
    provenance: Optional[str] = None
    created_at: datetime
    note: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ManualPaymentRequest(BaseModel):
    """Request body for recording a manual payment."""
    payment_date: date
    amount: float
    reference: Optional[str] = Field(None, max_length=128)
    note: Optional[str] = Field(None, max_length=512)


class ManualPaymentResponse(BaseModel):
    """Response after recording a manual payment."""
    payment: PaymentResponse
    invoice_id: uuid.UUID
    invoice_amount: float
    total_paid: float
    outstanding_balance: float
    payment_status: str
    message: str = "Payment recorded successfully."
