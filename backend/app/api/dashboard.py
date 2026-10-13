"""
Receivables dashboard endpoints.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.core.dependencies import TenantContext, get_db, get_tenant_context
from backend.app.schemas.dashboard import CashflowForecastResponse, DashboardSummaryResponse
from backend.app.services.cashflow_service import DEFAULT_HORIZON_WEEKS, build_cashflow_forecast
from backend.app.services.dashboard_service import build_dashboard_summary

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    summary="Operational receivables summary for current invoices",
)
def get_dashboard_summary(
    priority_limit: int = Query(5, ge=1, le=20),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> DashboardSummaryResponse:
    """Outstanding, overdue, due-soon, high-risk, aging, expected collections and
    a ranked follow-up list, scoped to the authenticated tenant's CURRENT invoices."""
    return build_dashboard_summary(
        db,
        tenant_ctx.business_id,
        priority_limit=priority_limit,
    )


@router.get(
    "/cashflow",
    response_model=CashflowForecastResponse,
    summary="Weekly expected collections with the assumptions behind them",
)
def get_cashflow_forecast(
    weeks: int = Query(DEFAULT_HORIZON_WEEKS, ge=1, le=26),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> CashflowForecastResponse:
    return build_cashflow_forecast(db, tenant_ctx.business_id, horizon_weeks=weeks)
