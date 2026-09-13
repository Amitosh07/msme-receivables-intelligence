"""
Historical Company Workspace API endpoints.
Provides historical company listing, search, detail, invoice upload,
manual payment recording, and CSV/XLSX payment ingestion scoped to the company workspace.
"""

from __future__ import annotations

import logging
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.dependencies import TenantContext, get_db, get_tenant_context
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.schemas.historical import (
    HistoricalCompanyCreate,
    HistoricalCompanyDetail,
    HistoricalCompanySummary,
    HistoricalCompanyUpdate,
    HistoricalInvoiceReview,
    HistoricalInvoiceItem,
    ManualHistoricalInvoiceCreate,
)
from backend.app.schemas.invoice import InvoiceResponse, InvoiceUploadResponse
from backend.app.schemas.payment import (
    ManualPaymentRequest,
    ManualPaymentResponse,
    PaymentImportResponse,
    PaymentResponse,
)
from backend.app.services.historical_service import (
    create_historical_company,
    get_historical_company_detail,
    list_historical_companies,
    upload_historical_company_invoice,
    upload_unassigned_historical_invoice,
    complete_historical_invoice_review,
    create_manual_historical_invoice,
    update_historical_company_gstin,
)
from backend.app.services.manual_payment_service import (
    derive_invoice_payment_status,
    get_invoice_payment_summary,
    record_manual_payment,
)
from backend.app.services.payment_import_service import (
    import_payments_file,
    preview_payments_file,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/historical", tags=["Historical Workspace"])


@router.post(
    "/invoices/upload",
    response_model=InvoiceUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a historical invoice for automatic company discovery",
)
async def upload_historical_invoice(
    file: UploadFile = File(...),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> InvoiceUploadResponse:
    doc, task = upload_unassigned_historical_invoice(
        db, tenant_ctx.business_id, await file.read(), file.filename or "historical_invoice.pdf"
    )
    return InvoiceUploadResponse(
        document_id=doc.id, invoice_id=doc.invoice_id,
        original_filename=doc.original_filename, file_size=doc.file_size,
        processing_status=doc.processing_status, origin=doc.origin,
        task_id=task.id, created_at=doc.created_at,
    )


@router.patch(
    "/invoices/{invoice_id}/review",
    response_model=HistoricalInvoiceItem,
    summary="Complete company or due-date review for a historical invoice",
)
def review_historical_invoice(
    invoice_id: uuid.UUID,
    payload: HistoricalInvoiceReview,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> HistoricalInvoiceItem:
    try:
        invoice = complete_historical_invoice_review(
            db, tenant_ctx.business_id, invoice_id,
            customer_id=payload.customer_id, company_name=payload.company_name,
            gstin=payload.gstin, due_date=payload.due_date,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    total_paid, outstanding = get_invoice_payment_summary(db, invoice)
    return HistoricalInvoiceItem(
        id=invoice.id, invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date, due_date=invoice.due_date,
        amount=float(invoice.amount), currency=invoice.currency, origin=invoice.origin,
        payment_status=derive_invoice_payment_status(db, invoice),
        processing_status=invoice.processing_status,
        total_paid=float(total_paid), outstanding_balance=float(outstanding),
        payment_count=len(invoice.payments), document_id=invoice.document_id,
    )


@router.get(
    "/invoices/review",
    response_model=List[InvoiceResponse],
    summary="List historical invoices awaiting manual completion",
)
def list_historical_invoice_reviews(
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> List[InvoiceResponse]:
    return list(db.scalars(
        select(Invoice).where(
            Invoice.business_id == tenant_ctx.business_id,
            Invoice.origin == InvoiceOrigin.HISTORICAL.value,
            Invoice.processing_status == "NEEDS_REVIEW",
        ).order_by(Invoice.created_at.desc())
    ).all())


@router.post(
    "/companies",
    response_model=HistoricalCompanySummary,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant-scoped historical company",
)
def post_historical_company(
    payload: HistoricalCompanyCreate,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> HistoricalCompanySummary:
    try:
        return create_historical_company(
            db, tenant_ctx.business_id, payload.display_name, payload.gstin
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get(
    "/companies",
    response_model=List[HistoricalCompanySummary],
    summary="List or search companies having historical data in authenticated tenant",
)
def get_historical_companies(
    search: Optional[str] = Query(None, description="Search by company name"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> List[HistoricalCompanySummary]:
    """
    Dynamically returns companies having historical receivables data in the authenticated tenant.
    Supports search using normalized customer identity mechanisms.
    """
    return list_historical_companies(
        db=db,
        business_id=tenant_ctx.business_id,
        search=search,
    )


@router.get(
    "/companies/{customer_id}",
    response_model=HistoricalCompanyDetail,
    summary="Get detailed historical records for a specific company",
)
def get_company_historical_detail(
    customer_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> HistoricalCompanyDetail:
    """
    Returns historical invoices, amounts, payment dates, and payment statuses
    for the selected company strictly within tenant scope.
    """
    detail = get_historical_company_detail(
        db=db,
        business_id=tenant_ctx.business_id,
        customer_id=customer_id,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )
    return detail


@router.patch(
    "/companies/{customer_id}",
    response_model=HistoricalCompanyDetail,
    summary="Add, edit, or clear an optional historical company GSTIN",
)
def patch_historical_company(
    customer_id: uuid.UUID,
    payload: HistoricalCompanyUpdate,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> HistoricalCompanyDetail:
    try:
        update_historical_company_gstin(
            db, tenant_ctx.business_id, customer_id, payload.gstin
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    detail = get_historical_company_detail(db, tenant_ctx.business_id, customer_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Historical company not found.")
    return detail


@router.post(
    "/companies/{customer_id}/invoices/manual",
    response_model=HistoricalInvoiceItem,
    status_code=status.HTTP_201_CREATED,
    summary="Create a manual historical invoice in a selected company workspace",
)
def post_manual_historical_invoice(
    customer_id: uuid.UUID,
    payload: ManualHistoricalInvoiceCreate,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> HistoricalInvoiceItem:
    try:
        invoice, payment = create_manual_historical_invoice(
            db, tenant_ctx.business_id, customer_id,
            amount=payload.amount, due_date=payload.due_date,
            payment_date=payload.payment_date,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    total_paid, outstanding = get_invoice_payment_summary(db, invoice)
    return HistoricalInvoiceItem(
        id=invoice.id, invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date, due_date=invoice.due_date,
        amount=float(invoice.amount), currency=invoice.currency,
        origin=invoice.origin, payment_status=derive_invoice_payment_status(db, invoice),
        processing_status=invoice.processing_status, total_paid=float(total_paid),
        outstanding_balance=float(outstanding),
        payment_date=payment.payment_date.date() if payment else None,
        payment_count=1 if payment else 0,
    )


@router.post(
    "/companies/{customer_id}/invoices/upload",
    response_model=InvoiceUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a historical invoice PDF for a specific company",
)
async def upload_company_historical_invoice(
    customer_id: uuid.UUID,
    file: UploadFile = File(..., description="Historical invoice PDF file"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> InvoiceUploadResponse:
    """
    Upload an invoice PDF explicitly tagged as HISTORICAL and bound authoritatively
    to the selected company workspace.
    """
    content = await file.read()
    filename = file.filename or "historical_invoice.pdf"

    doc, task = upload_historical_company_invoice(
        db=db,
        business_id=tenant_ctx.business_id,
        customer_id=customer_id,
        file_content=content,
        original_filename=filename,
    )

    return InvoiceUploadResponse(
        document_id=doc.id,
        invoice_id=doc.invoice_id,
        original_filename=doc.original_filename,
        file_size=doc.file_size,
        processing_status=doc.processing_status,
        origin=doc.origin,
        task_id=task.id,
        created_at=doc.created_at,
    )


@router.post(
    "/invoices/{invoice_id}/payments",
    response_model=ManualPaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a manual payment for a historical invoice",
)
def record_historical_payment(
    invoice_id: uuid.UUID,
    payload: ManualPaymentRequest,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ManualPaymentResponse:
    """
    Record a factual historical manual payment against a historical invoice.
    Uses existing payment provenance semantics (provenance='manual').
    """
    invoice = db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == tenant_ctx.business_id,
            Invoice.origin == InvoiceOrigin.HISTORICAL.value,
        )
    )
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    try:
        result = record_manual_payment(
            db=db,
            business_id=tenant_ctx.business_id,
            invoice_id=invoice_id,
            payment_date=payload.payment_date,
            amount=payload.amount,
            reference=payload.reference,
            note=payload.note,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT
            if "already exists" in str(exc)
            else status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    return ManualPaymentResponse(
        payment=PaymentResponse.model_validate(result.payment),
        invoice_id=result.invoice.id,
        invoice_amount=float(result.invoice.amount),
        total_paid=float(result.total_paid),
        outstanding_balance=float(result.outstanding_balance),
        payment_status=result.payment_status,
    )


@router.post(
    "/companies/{customer_id}/invoices/{invoice_id}/payments",
    response_model=ManualPaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a manual payment for a historical invoice (scoped under company)",
)
def record_company_historical_payment(
    customer_id: uuid.UUID,
    invoice_id: uuid.UUID,
    payload: ManualPaymentRequest,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ManualPaymentResponse:
    """Scoped alias for recording historical payment under a company workspace."""
    invoice = db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == tenant_ctx.business_id,
            Invoice.customer_id == customer_id,
            Invoice.origin == InvoiceOrigin.HISTORICAL.value,
        )
    )
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found for this company.",
        )
    return record_historical_payment(
        invoice_id=invoice_id,
        payload=payload,
        tenant_ctx=tenant_ctx,
        db=db,
    )


@router.post(
    "/companies/{customer_id}/payments/import",
    response_model=PaymentImportResponse,
    status_code=status.HTTP_200_OK,
    summary="Import historical payment data from CSV or XLSX within company workspace",
)
async def import_company_historical_payments(
    customer_id: uuid.UUID,
    file: UploadFile = File(..., description="Historical payment CSV or XLSX file"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PaymentImportResponse:
    """
    Import historical payment records into the authenticated business tenant scoped
    to the selected company workspace. Reuses Phase B normalization and natural-key protection.
    """
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.business_id == tenant_ctx.business_id,
        )
    )
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    filename = (file.filename or "").lower()
    if filename.endswith(".csv"):
        file_type = "csv"
    elif filename.endswith(".xlsx"):
        file_type = "xlsx"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only CSV and XLSX files are accepted.",
        )

    return import_payments_file(
        db=db,
        business_id=tenant_ctx.business_id,
        file_content=await file.read(),
        file_type=file_type,
        customer_id=customer_id,
    )


@router.post(
    "/companies/{customer_id}/payments/preview",
    response_model=PaymentImportResponse,
    status_code=status.HTTP_200_OK,
    summary="Preview historical payment data from CSV or XLSX within company workspace",
)
async def preview_company_historical_payments(
    customer_id: uuid.UUID,
    file: UploadFile = File(..., description="Historical payment CSV or XLSX file"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PaymentImportResponse:
    """Preview historical payment CSV/XLSX without persisting."""
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.business_id == tenant_ctx.business_id,
        )
    )
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    filename = (file.filename or "").lower()
    if filename.endswith(".csv"):
        file_type = "csv"
    elif filename.endswith(".xlsx"):
        file_type = "xlsx"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only CSV and XLSX files are accepted.",
        )

    return preview_payments_file(
        db=db,
        business_id=tenant_ctx.business_id,
        file_content=await file.read(),
        file_type=file_type,
        customer_id=customer_id,
    )
