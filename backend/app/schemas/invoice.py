"""
Pydantic schemas for Invoice and InvoiceDocument entities.
"""

import uuid
from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict
from backend.app.schemas.payment import PaymentResponse
from backend.app.schemas.prediction import PredictionResponse


class InvoiceUploadResponse(BaseModel):
    """Response returned upon successful invoice PDF upload."""
    document_id: uuid.UUID
    invoice_id: Optional[uuid.UUID] = None
    original_filename: str
    file_size: int
    processing_status: str = "PENDING"
    origin: str = "CURRENT"
    task_id: Optional[uuid.UUID] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvoiceDocumentResponse(BaseModel):
    """Metadata response for an uploaded invoice document."""
    id: uuid.UUID
    business_id: uuid.UUID
    invoice_id: Optional[uuid.UUID] = None
    original_filename: str
    content_type: str
    file_size: int
    processing_status: str
    origin: str = "CURRENT"
    error_message: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvoiceResponse(BaseModel):
    """Full detail response for an invoice entity."""
    id: uuid.UUID
    business_id: uuid.UUID
    customer_id: Optional[uuid.UUID] = None
    unresolved_customer_name: Optional[str] = None
    customer_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    amount: float
    currency: Optional[str] = None
    payment_terms: Optional[str] = None
    payment_status: str
    processing_status: str
    origin: str = "CURRENT"
    document_id: Optional[uuid.UUID] = None
    total_paid: Optional[float] = None
    outstanding_balance: Optional[float] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvoiceListItem(InvoiceResponse):
    """Invoice row with its estimate, present only when it passes the history gate."""
    prediction: Optional[PredictionResponse] = None


class InvoiceListResponse(BaseModel):
    """One page of invoices matching the requested filters; total counts all matches."""
    items: List[InvoiceListItem]
    total: int
    skip: int
    limit: int


class InvoiceDetailResponse(InvoiceResponse):
    """Extended invoice response with payment summary for detail views."""
    total_paid: float = 0.0
    outstanding_balance: float = 0.0
    payments: List[PaymentResponse] = []
