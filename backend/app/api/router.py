"""
Main API router aggregating all versioned endpoints.
"""

from fastapi import APIRouter

from backend.app.api.auth import router as auth_router
from backend.app.api.health import router as health_router

api_router = APIRouter()

# Health endpoints at top-level
api_router.include_router(health_router)

# Authentication endpoints under /auth
api_router.include_router(auth_router)
