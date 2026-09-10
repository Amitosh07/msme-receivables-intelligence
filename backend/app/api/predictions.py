"""
API endpoints for invoice predictions and risk scoring.
"""

from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.core.dependencies import TenantContext, get_tenant_context
from backend.app.db.session import get_db
from backend.app.schemas.prediction import PredictionListResponse, PredictionResponse
from backend.app.services.prediction_service import (
    InvoiceNotReadyError,
    PredictionServiceError,
    get_prediction_for_invoice,
    list_predictions_for_tenant,
    predict_for_invoice,
)

router = APIRouter(prefix="", tags=["Predictions"])


@router.get(
    "/invoices/{invoice_id}/prediction",
    response_model=PredictionResponse,
    summary="Get prediction result for a specific invoice",
)
def get_invoice_prediction(
    invoice_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PredictionResponse:
    """
    Retrieve the payment delay prediction and timing estimation for an invoice.
    Enforces tenant isolation: users can only view predictions for their business.
    """
    pred = get_prediction_for_invoice(
        db=db,
        business_id=tenant_ctx.business_id,
        invoice_id=invoice_id,
    )
    if not pred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prediction not found for the requested invoice.",
        )
    return PredictionResponse.model_validate(pred)


@router.post(
    "/invoices/{invoice_id}/predict",
    response_model=PredictionResponse,
    summary="Trigger ML prediction for an invoice",
)
def generate_invoice_prediction(
    invoice_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PredictionResponse:
    """
    Generates and persists a V1 ML prediction for an invoice.
    Computes leakage-safe as-of customer features and executes classifier and timing models.
    """
    try:
        pred = predict_for_invoice(
            db=db,
            invoice_id=invoice_id,
            business_id=tenant_ctx.business_id,
        )
        return PredictionResponse.model_validate(pred)
    except InvoiceNotReadyError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except PredictionServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except Exception as e:
        # Log the diagnostic server-side; never expose model paths, SQL, or a
        # stack trace to the browser.
        import logging
        logging.getLogger(__name__).exception("Prediction generation failed for invoice %s", invoice_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Prediction could not be generated. Verify the model artifacts and try again.",
        ) from e


@router.get(
    "/predictions",
    response_model=PredictionListResponse,
    summary="List all predictions for the tenant with optional risk tier filter",
)
def list_predictions(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier: LOW, MEDIUM, HIGH"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PredictionListResponse:
    """
    List predictions scoped to the authenticated tenant.
    Supports filtering by risk tier (e.g. HIGH for prioritized collection).
    """
    items, total = list_predictions_for_tenant(
        db=db,
        business_id=tenant_ctx.business_id,
        skip=skip,
        limit=limit,
        risk_tier=risk_tier,
    )
    return PredictionListResponse(
        items=[PredictionResponse.model_validate(p) for p in items],
        total=total,
        skip=skip,
        limit=limit,
    )
