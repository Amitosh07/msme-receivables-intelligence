"""
Pydantic schemas for Historical Company Workspace.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator
from decimal import Decimal


class HistoricalCompanyCreate(BaseModel):
    """Explicit creation request for a tenant-owned historical company."""
    display_name: Optional[str] = Field(default=None, max_length=255)
    company_name: Optional[str] = Field(default=None, max_length=255)
    gstin: Optional[str] = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def populate_and_validate_name(self) -> "HistoricalCompanyCreate":
        name = (self.company_name or self.display_name or "").strip()
        if not name:
            raise ValueError("Company name is required.")
        if not self.display_name:
            self.display_name = name
        return self


class HistoricalCompanyUpdate(BaseModel):
    gstin: Optional[str] = Field(default=None, max_length=32)


class HistoricalInvoiceReview(BaseModel):
    """Manual completion of fields the historical parser could not establish."""
    customer_id: Optional[uuid.UUID] = None
    company_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    gstin: Optional[str] = Field(default=None, max_length=32)
    due_date: Optional[date] = None


class ManualHistoricalInvoiceCreate(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    due_date: date
    payment_date: Optional[date] = None


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


class HistoricalInvoiceCompanyCorrection(BaseModel):
    """Request to change/correct the company associated with a historical invoice."""
    replacement_customer_id: Optional[uuid.UUID] = None
    replacement_company_name: Optional[str] = Field(default=None, max_length=255)
    customer_id: Optional[uuid.UUID] = None
    company_name: Optional[str] = Field(default=None, max_length=255)
    create_if_missing: bool = False
    gstin: Optional[str] = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def populate_targets(self) -> "HistoricalInvoiceCompanyCorrection":
        if not self.replacement_customer_id and self.customer_id:
            self.replacement_customer_id = self.customer_id
        if not self.replacement_company_name and self.company_name:
            self.replacement_company_name = self.company_name
        if not self.replacement_customer_id and not (self.replacement_company_name and self.replacement_company_name.strip()):
            raise ValueError("Either replacement_customer_id or replacement_company_name is required.")
        return self


class HistoricalInvoiceItem(BaseModel):
    """Historical invoice item detail with factual payment attributes."""
    id: uuid.UUID
    customer_id: Optional[uuid.UUID] = None
    customer_name: Optional[str] = None
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
