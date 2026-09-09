"""
Pydantic schemas for PredictionResult entities and scoring responses.
"""

from datetime import date, datetime
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class PredictionResponse(BaseModel):
    """Output prediction data for an invoice."""
    id: uuid.UUID
    business_id: uuid.UUID
    invoice_id: uuid.UUID
    prediction: bool
    risk_score: float
    risk_tier: str
    predicted_days_until_payment: Optional[float] = None
    expected_payment_date: Optional[date] = None
    classifier_model_version: Optional[str] = "payment_classifier_v1"
    timing_model_version: Optional[str] = "payment_timing_v1"
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PredictionListResponse(BaseModel):
    """Paginated list of prediction records."""
    items: List[PredictionResponse]
    total: int
    skip: int
    limit: int
