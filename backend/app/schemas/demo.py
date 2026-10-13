"""Pydantic contracts for the scripted demo path."""

from __future__ import annotations

from typing import List, Optional
import uuid

from pydantic import BaseModel

from backend.app.schemas.prediction import PredictionExplanationResponse, PredictionResponse


class DemoStep(BaseModel):
    key: str
    title: str
    detail: str
    history_count: Optional[int] = None
    created: bool = False


class DemoRunResponse(BaseModel):
    customer_id: uuid.UUID
    customer_name: str
    open_invoice_id: uuid.UUID
    open_invoice_number: str
    eligible_history_count: int
    required_history: int
    steps: List[DemoStep]
    prediction: PredictionResponse
    explanation: PredictionExplanationResponse


class DemoStatusResponse(BaseModel):
    exists: bool
    customer_id: Optional[uuid.UUID] = None
    customer_name: Optional[str] = None
    open_invoice_id: Optional[uuid.UUID] = None
    scored: bool = False


class DemoRemoveResponse(BaseModel):
    removed_invoices: int
    removed_payments: int
    removed_customer: bool
