"""
Pydantic schemas for PredictionResult entities and scoring responses.
"""

from datetime import date, datetime
from typing import List, Literal, Optional, Union
import uuid
from pydantic import BaseModel, ConfigDict, Field


class PredictionResponse(BaseModel):
    """Output prediction data for an invoice."""
    prediction_available: Literal[True] = True
    reason: None = None
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


class PredictionUnavailableResponse(BaseModel):
    """Expected response when the customer lacks sufficient prior outcomes."""

    prediction_available: Literal[False] = False
    reason: str
    invoice_id: uuid.UUID
    eligible_history_count: int
    required_history_count: int


PredictionOperationResponse = Union[PredictionResponse, PredictionUnavailableResponse]


class PredictionListResponse(BaseModel):
    """Paginated list of prediction records."""
    items: List[PredictionResponse]
    total: int
    skip: int
    limit: int
