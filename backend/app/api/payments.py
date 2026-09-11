"""Payment history CSV/XLSX ingestion API endpoints."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from backend.app.core.dependencies import TenantContext, get_db, get_tenant_context
from backend.app.schemas.payment import PaymentImportResponse
from backend.app.services.payment_import_service import import_payments_file, preview_payments_file

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post(
    "/import",
    response_model=PaymentImportResponse,
    status_code=status.HTTP_200_OK,
    summary="Import historical payment data from CSV or XLSX",
)
async def import_payments(
    file: UploadFile = File(..., description="Historical payment CSV or XLSX file"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PaymentImportResponse:
    """
    Import historical payment records from a CSV file into the authenticated business tenant.
    Validates required columns, formats dates/amounts, rejects duplicates, and matches to existing invoices.
    """
    filename = (file.filename or "").lower()
    if filename.endswith(".csv"):
        file_type = "csv"
    elif filename.endswith(".xlsx"):
        file_type = "xlsx"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only CSV and XLSX files are accepted for payment imports.",
        )

    response = import_payments_file(
        db=db,
        business_id=tenant_ctx.business_id,
        file_content=await file.read(),
        file_type=file_type,
    )
    return response


@router.post(
    "/preview",
    response_model=PaymentImportResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate and preview payment-history CSV/XLSX without persisting it",
)
async def preview_payments(
    file: UploadFile = File(..., description="Historical payment CSV or XLSX file"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PaymentImportResponse:
    filename = (file.filename or "").lower()
    if filename.endswith(".csv"):
        file_type = "csv"
    elif filename.endswith(".xlsx"):
        file_type = "xlsx"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only CSV and XLSX files are accepted for payment imports.",
        )
    return preview_payments_file(
        db=db,
        business_id=tenant_ctx.business_id,
        file_content=await file.read(),
        file_type=file_type,
    )
