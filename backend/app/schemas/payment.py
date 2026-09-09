"""
Pydantic schemas for Payment entity and CSV import responses.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict


class ImportErrorRow(BaseModel):
    """Details of a single row rejected during payment CSV ingestion."""
    row_number: int
    reason: str
    raw_data: Optional[Dict[str, Any]] = None


class PaymentImportResponse(BaseModel):
    """Summary of payment CSV batch import results."""
    total_rows: int
    imported: int
    duplicates: int
    rejected: int
    unmatched: int
    errors: List[ImportErrorRow] = []


class PaymentResponse(BaseModel):
    """Payment record detail response."""
    id: uuid.UUID
    business_id: uuid.UUID
    invoice_id: Optional[uuid.UUID] = None
    invoice_reference: Optional[str] = None
    payment_date: datetime
    amount: float
    reference: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
