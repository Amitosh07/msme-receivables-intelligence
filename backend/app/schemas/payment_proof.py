"""
Pydantic schemas for PaymentProof entity and proof upload/status responses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict


class PaymentProofUploadResponse(BaseModel):
    """Response returned upon successful payment proof upload."""
    proof_id: uuid.UUID
    invoice_id: uuid.UUID
    task_id: Optional[uuid.UUID] = None
    original_filename: str
    file_size: int
    status: str = "PENDING"
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentProofResponse(BaseModel):
    """Full detail and status response for a PaymentProof record."""
    id: uuid.UUID
    business_id: uuid.UUID
    invoice_id: uuid.UUID
    original_filename: str
    content_type: str
    file_size: int
    status: str
    extracted_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    payment_id: Optional[uuid.UUID] = None
    task_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentProofListResponse(BaseModel):
    """List of payment proof records for an invoice."""
    items: List[PaymentProofResponse]
    total: int
