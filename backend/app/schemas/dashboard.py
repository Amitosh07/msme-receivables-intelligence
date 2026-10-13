"""
Pydantic schemas for the receivables dashboard summary.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional
import uuid

from pydantic import BaseModel


class AmountCount(BaseModel):
    """Outstanding amount and invoice count for one dashboard segment."""
    amount: float = 0.0
    count: int = 0


class AgingBucketSummary(AmountCount):
    key: str
    label: str


class ExpectedPaymentInvoice(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: Optional[str] = None
    customer_name: Optional[str] = None
    outstanding_balance: float


class ExpectedPaymentDay(AmountCount):
    """Predicted (not confirmed) collections grouped by expected payment date."""
    expected_date: date
    invoices: List[ExpectedPaymentInvoice] = []


class PriorityInvoice(BaseModel):
    """An outstanding invoice ranked for follow-up, with human-readable reasons."""
    invoice_id: uuid.UUID
    invoice_number: Optional[str] = None
    customer_name: Optional[str] = None
    due_date: Optional[date] = None
    days_past_due: Optional[int] = None
    amount: float
    outstanding_balance: float
    currency: Optional[str] = None
    payment_status: str
    risk_tier: Optional[str] = None
    risk_score: Optional[float] = None
    expected_payment_date: Optional[date] = None
    reasons: List[str] = []


class DashboardSummaryResponse(BaseModel):
    """Operational receivables summary for CURRENT invoices, using remaining balances."""
    as_of: date
    currency: str
    mixed_currency: bool = False
    due_soon_days: int
    expected_window_days: int
    current_invoice_count: int
    outstanding: AmountCount
    overdue: AmountCount
    due_soon: AmountCount
    high_risk: AmountCount
    expected_payments: AmountCount
    undated: AmountCount
    aging: List[AgingBucketSummary]
    expected_schedule: List[ExpectedPaymentDay]
    priority: List[PriorityInvoice]


ForecastBasis = Literal["predicted", "due_date", "past_predicted", "overdue_unestimated", "undated"]


class ForecastInvoice(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: Optional[str] = None
    customer_name: Optional[str] = None
    outstanding_balance: float
    due_date: Optional[date] = None
    expected_date: Optional[date] = None
    basis: ForecastBasis
    risk_tier: Optional[str] = None


class ForecastWeek(BaseModel):
    """One rolling 7-day window; amounts split by where the expected date came from."""
    start: date
    end: date
    total: float
    predicted: float
    due_date_based: float
    high_risk: float
    count: int
    invoices: List[ForecastInvoice] = []


class ForecastUnscheduled(AmountCount):
    """Balances the forecast deliberately does not place in a week."""
    key: str
    label: str
    detail: str
    invoices: List[ForecastInvoice] = []


class CashflowForecastResponse(BaseModel):
    """Expected collections: expected date x remaining balance, with stated assumptions."""
    as_of: date
    currency: str
    mixed_currency: bool = False
    horizon_weeks: int
    horizon_end: date
    outstanding: AmountCount
    scheduled: AmountCount
    model_dated: AmountCount
    due_date_dated: AmountCount
    model_coverage: float
    weeks: List[ForecastWeek]
    unscheduled: List[ForecastUnscheduled]
    assumptions: List[str]
