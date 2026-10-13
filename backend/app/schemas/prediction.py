"""
Pydantic schemas for PredictionResult entities and scoring responses.
"""

from datetime import date, datetime
from typing import Dict, List, Literal, Optional, Union
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


class ExplanationFactor(BaseModel):
    """One plain-language driver of an invoice's late-payment estimate."""
    key: str
    label: str
    effect: Literal["raises", "lowers"]
    contribution: float = Field(description="Additive effect on the score in log-odds.")
    share: float = Field(description="Share of the total absolute effect, 0 to 1.")
    evidence: str


class PredictionExplanationResponse(BaseModel):
    """Why the model produced this invoice's late-payment estimate."""
    invoice_id: uuid.UUID
    risk_score: float = Field(description="Score recomputed from the invoice's current history.")
    stored_risk_score: float
    matches_stored: bool
    risk_tier: Optional[str] = None
    baseline_risk: float = Field(description="Model starting point before any invoice facts.")
    factors: List[ExplanationFactor]
    unknown_categories: List[str] = []
    method: str


class ImportanceFactor(BaseModel):
    key: str
    label: str
    importance: float


class ModelInsightsResponse(BaseModel):
    """What the V1 models rely on overall, how well they tested, and their limits."""
    classifier_model_version: str
    timing_model_version: str
    risk_drivers: List[ImportanceFactor]
    timing_drivers: List[ImportanceFactor]
    roc_auc: float
    accuracy: float
    precision: float
    recall: float
    timing_mae_days: float
    timing_median_error_days: float
    training_rows: int
    training_period: str
    test_period: str
    required_history: int
    risk_tiers: Dict[str, str]
    limitations: List[str]
