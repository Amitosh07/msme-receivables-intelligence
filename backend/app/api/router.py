"""
Main API router aggregating all versioned endpoints.
"""

from fastapi import APIRouter

from backend.app.api.auth import router as auth_router
from backend.app.api.health import router as health_router
from backend.app.api.invoices import router as invoices_router
from backend.app.api.payments import router as payments_router
from backend.app.api.predictions import router as predictions_router

api_router = APIRouter()

# Health endpoints at top-level
api_router.include_router(health_router)

# Authentication endpoints under /auth
api_router.include_router(auth_router)

# Ingestion endpoints for invoices and payments
api_router.include_router(invoices_router)
api_router.include_router(payments_router)

# Prediction endpoints for scoring and risk assessment
api_router.include_router(predictions_router)

