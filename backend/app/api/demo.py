"""
Scripted demo path endpoints, scoped to the authenticated tenant.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.core.dependencies import TenantContext, get_db, get_tenant_context
from backend.app.core.rate_limit import demo_limit
from backend.app.schemas.demo import DemoRemoveResponse, DemoRunResponse, DemoStatusResponse
from backend.app.services.demo_service import (
    DemoConflictError,
    get_demo_status,
    remove_demo_data,
    run_demo_path,
)

router = APIRouter(prefix="/demo", tags=["Demo"])
logger = logging.getLogger(__name__)


@router.get("/scripted-path", response_model=DemoStatusResponse)
def demo_status(
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> DemoStatusResponse:
    return get_demo_status(db, tenant_ctx.business_id)


@router.post("/scripted-path", response_model=DemoRunResponse, dependencies=[Depends(demo_limit)])
def run_demo(
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> DemoRunResponse:
    """Create (or reuse) 3 paid invoices and 1 open invoice for a demo customer, then score it."""
    try:
        return run_demo_path(db, tenant_ctx.business_id)
    except DemoConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Scripted demo path failed for business %s", tenant_ctx.business_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The demo could not be completed. Any records already created can be removed and the demo re-run.",
        ) from exc


@router.delete("/scripted-path", response_model=DemoRemoveResponse)
def remove_demo(
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> DemoRemoveResponse:
    """Remove only the demo customer's DEMO-ST- invoices, their payments and estimates."""
    return remove_demo_data(db, tenant_ctx.business_id)
