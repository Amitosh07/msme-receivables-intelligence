"""
MSME Receivables Intelligence Platform — FastAPI Application Entrypoint
Version 1 Backend Foundation
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.router import api_router
from backend.app.core.config import Settings, enforce_secure_configuration, settings
from backend.app.core.rate_limit import GlobalRateLimitMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown events."""
    logger.info("Starting %s v%s in %s mode", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)
    yield
    logger.info("Shutting down %s", settings.APP_NAME)


def create_app(config: Optional[Settings] = None) -> FastAPI:
    """FastAPI application factory."""
    config = config or settings
    enforce_secure_configuration(config)
    docs = config.api_docs_enabled
    application = FastAPI(
        title=config.APP_NAME,
        version=config.APP_VERSION,
        description="AI/ML-powered Receivables Intelligence Platform for B2B MSMEs",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        lifespan=lifespan,
    )

    application.add_middleware(GlobalRateLimitMiddleware)
    # Bearer tokens travel in the Authorization header, never cookies, so
    # credentials are not allowed cross-origin.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Retry-After"],
        max_age=600,
    )

    application.include_router(api_router)

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
