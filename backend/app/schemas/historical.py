"""
Pydantic schemas for Historical Company Workspace.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class HistoricalCompanyCreate(BaseModel):
    """Explicit creation request for a tenant-owned historical company."""
    display_name: str = Field(min_length=1, max_length=255)
    gstin: Optional[str] = Field(default=None, max_length=32)


class HistoricalInvoiceReview(BaseModel):
    """Manual completion of fields the historical parser could not establish."""
    customer_id: Optional[uuid.UUID] = None
    company_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    gstin: Optional[str] = Field(default=None, max_length=32)
    due_date: Optional[date] = None


class HistoricalCompanySummary(BaseModel):
    """Summary of a company having historical receivables data."""
    id: uuid.UUID
    display_name: str
    normalized_name: str
    gstin: Optional[str] = None
    customer_ref: Optional[str] = None
    historical_invoice_count: int = 0
    total_amount: float = 0.0
    total_paid: float = 0.0
    outstanding_balance: float = 0.0
    last_invoice_date: Optional[date] = None
    last_payment_date: Optional[date] = None

    model_config = ConfigDict(from_attributes=True)


class HistoricalInvoiceItem(BaseModel):
    """Historical invoice item detail with factual payment attributes."""
    id: uuid.UUID
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    amount: float
    currency: Optional[str] = None
    origin: str = "HISTORICAL"
    payment_status: str  # factual status: OPEN, PARTIAL, PAID
    processing_status: str = "PROCESSED"
    total_paid: float = 0.0
    outstanding_balance: float = 0.0
    payment_date: Optional[date] = None  # factual payment date if payments exist
    payment_count: int = 0
    document_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)


class HistoricalCompanyDetail(BaseModel):
    """Detailed historical workspace view for a single company."""
    id: uuid.UUID
    display_name: str
    normalized_name: str
    gstin: Optional[str] = None
    customer_ref: Optional[str] = None
    historical_invoice_count: int = 0
    total_amount: float = 0.0
    total_paid: float = 0.0
    outstanding_balance: float = 0.0
    invoices: List[HistoricalInvoiceItem] = []

    model_config = ConfigDict(from_attributes=True)
