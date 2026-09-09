"""
Payment history ingestion API endpoints.
Provides CSV batch upload and validation for historical receivables data.
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from backend.app.core.dependencies import TenantContext, get_db, get_tenant_context
from backend.app.schemas.payment import PaymentImportResponse
from backend.app.services.payment_import_service import import_payments_csv

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post(
    "/import",
    response_model=PaymentImportResponse,
    status_code=status.HTTP_200_OK,
    summary="Import payment history from a canonical CSV file",
)
async def import_payments(
    file: UploadFile = File(..., description="Canonical CSV payment history export"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PaymentImportResponse:
    """
    Import historical payment records from a CSV file into the authenticated business tenant.
    Validates required columns, formats dates/amounts, rejects duplicates, and matches to existing invoices.
    """
    filename = file.filename or ""
    if not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only CSV files are accepted for payment imports.",
        )

    content = await file.read()
    response = import_payments_csv(
        db=db,
        business_id=tenant_ctx.business_id,
        file_content=content,
    )
    return response
