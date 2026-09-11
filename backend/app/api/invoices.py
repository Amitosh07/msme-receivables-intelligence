"""
Invoice and InvoiceDocument API endpoints.
Provides invoice PDF upload, document metadata listing, file download, and invoice read endpoints.
"""

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from backend.app.core.dependencies import TenantContext, get_db, get_tenant_context
from backend.app.models.invoice import InvoiceOrigin
from backend.app.schemas.invoice import (
    InvoiceDocumentResponse,
    InvoiceListResponse,
    InvoiceResponse,
    InvoiceUploadResponse,
    InvoiceDetailResponse,
)
from backend.app.schemas.payment import (
    ManualPaymentRequest,
    ManualPaymentResponse,
    PaymentResponse,
)
from backend.app.schemas.payment_proof import (
    PaymentProofListResponse,
    PaymentProofResponse,
    PaymentProofUploadResponse,
)
from backend.app.services.invoice_service import (
    get_document_by_id,
    get_invoice_by_id,
    list_documents_for_tenant,
    list_invoices_for_tenant,
    upload_invoice_document,
)
from backend.app.services.manual_payment_service import (
    get_invoice_payment_summary,
    get_invoice_payments,
    record_manual_payment,
)
from backend.app.services.payment_proof_service import (
    get_proof_by_id,
    list_proofs_for_invoice,
    upload_payment_proof,
)
from backend.app.services.storage import get_storage
from backend.app.services.task_service import create_task
from backend.app.workers.queue import get_task_queue

router = APIRouter(prefix="/invoices", tags=["Invoices"])


@router.post(
    "/upload",
    response_model=InvoiceUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an invoice PDF document",
)
async def upload_invoice(
    file: UploadFile = File(..., description="Invoice document in PDF format"),
    origin: Optional[str] = Query(None, description="Data origin classification (CURRENT or HISTORICAL)"),
    origin_form: Optional[str] = Form(None, alias="origin", description="Data origin classification (CURRENT or HISTORICAL)"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> InvoiceUploadResponse:
    """
    Upload an invoice PDF for the authenticated business tenant.
    Stores the PDF safely in object storage and creates an InvoiceDocument record in PENDING state.
    Tenant context is derived server-side from authentication.
    """
    # Resolve and validate data origin
    raw_origin = origin_form if origin_form is not None else (origin if origin is not None else InvoiceOrigin.CURRENT.value)
    origin_upper = raw_origin.strip().upper()
    if origin_upper not in {InvoiceOrigin.HISTORICAL.value, InvoiceOrigin.CURRENT.value}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid invoice origin '{raw_origin}'. Allowed origins: {[e.value for e in InvoiceOrigin]}",
        )

    content = await file.read()
    filename = file.filename or "invoice.pdf"

    # Create asynchronous parsing task in PostgreSQL (authoritative source of truth)
    import logging
    _logger = logging.getLogger(__name__)

    try:
        doc = upload_invoice_document(
            db=db,
            business_id=tenant_ctx.business_id,
            file_content=content,
            original_filename=filename,
            origin=origin_upper,
            commit=False,
        )
        task = create_task(
            db=db,
            business_id=tenant_ctx.business_id,
            task_type="parse_invoice",
            payload={
                "invoice_document_id": str(doc.id),
                "business_id": str(tenant_ctx.business_id),
                "origin": origin_upper,
            },
            commit=False,
        )
        db.commit()
        db.refresh(doc)
        db.refresh(task)
    except Exception:
        db.rollback()
        # The metadata/task transaction failed, so remove the object that was
        # already written by the storage abstraction.
        if "doc" in locals():
            try:
                get_storage().delete(doc.storage_key)
            except Exception:
                _logger.warning("Could not clean up document storage after task creation failure.")
        raise

    # Enqueue task to Redis queue for background worker consumption
    try:
        queue = get_task_queue()
        queue.enqueue(
            task_id=task.id,
            task_type="parse_invoice",
            business_id=tenant_ctx.business_id,
            payload={
                "invoice_document_id": str(doc.id),
                "business_id": str(tenant_ctx.business_id),
                "origin": origin_upper,
            },
        )
    except Exception as e:
        _logger.warning(
            "Could not immediately enqueue task %s to Redis: %s. "
            "Task remains PENDING in PostgreSQL and will be processed via startup recovery.",
            task.id, e,
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


@router.get(
    "/documents",
    response_model=List[InvoiceDocumentResponse],
    summary="List uploaded invoice documents for the tenant",
)
def list_documents(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> List[InvoiceDocumentResponse]:
    """Retrieve metadata of uploaded invoice documents scoped to the authenticated tenant."""
    docs = list_documents_for_tenant(
        db=db,
        business_id=tenant_ctx.business_id,
        skip=skip,
        limit=limit,
    )
    return [InvoiceDocumentResponse.model_validate(d) for d in docs]


@router.get(
    "/documents/{document_id}",
    summary="Download or view an uploaded invoice PDF",
)
def get_document_file(
    document_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Response:
    """
    Download or stream an invoice PDF file.
    Validates tenant ownership: users cannot access documents belonging to other tenants.
    """
    doc = get_document_by_id(
        db=db,
        business_id=tenant_ctx.business_id,
        document_id=document_id,
    )
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice document not found.",
        )

    storage = get_storage()
    try:
        content = storage.read(doc.storage_key)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document file not found in storage.",
        ) from e

    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{doc.original_filename}"',
            "Content-Length": str(len(content)),
        },
    )


@router.get(
    "",
    response_model=InvoiceListResponse,
    summary="List invoices for the tenant with pagination",
)
def list_invoices(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    payment_status: Optional[str] = Query(None, description="Filter by payment status (OPEN, PAID)"),
    origin: Optional[str] = Query(None, description="Filter by data origin (CURRENT, HISTORICAL)"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> InvoiceListResponse:
    """List invoices belonging to the authenticated business tenant."""
    items, total = list_invoices_for_tenant(
        db=db,
        business_id=tenant_ctx.business_id,
        skip=skip,
        limit=limit,
        payment_status=payment_status,
        origin=origin,
    )
    return InvoiceListResponse(
        items=[InvoiceResponse.model_validate(i) for i in items],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/{invoice_id}/payments",
    response_model=ManualPaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a manual payment against an invoice",
)
def record_payment(
    invoice_id: uuid.UUID,
    payload: ManualPaymentRequest,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ManualPaymentResponse:
    """Record a manual payment received for an invoice.
    
    Creates a real Payment row with provenance='manual'.
    Payment date must not be in the future.
    Amount must be positive.
    Duplicate submissions (same date + amount + invoice) are rejected.
    """
    try:
        result = record_manual_payment(
            db,
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


@router.get(
    "/{invoice_id}/payments",
    response_model=List[PaymentResponse],
    summary="List payments recorded against an invoice",
)
def list_invoice_payments(
    invoice_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> List[PaymentResponse]:
    """Retrieve all payment records for a specific invoice, scoped to tenant."""
    invoice = get_invoice_by_id(
        db=db,
        business_id=tenant_ctx.business_id,
        invoice_id=invoice_id,
    )
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )
    payments = get_invoice_payments(
        db=db,
        business_id=tenant_ctx.business_id,
        invoice_id=invoice_id,
    )
    return [PaymentResponse.model_validate(p) for p in payments]


@router.post(
    "/{invoice_id}/payment-proof",
    response_model=PaymentProofUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a payment proof document for verification",
)
async def upload_proof(
    invoice_id: uuid.UUID,
    file: UploadFile = File(..., description="Payment proof file (PDF, CSV, XLSX)"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PaymentProofUploadResponse:
    """Upload a payment proof document for an invoice.

    Stores proof in object storage, creates an asynchronous verification task,
    and returns 202 Accepted status.
    """
    content = await file.read()
    filename = file.filename or "payment_proof.pdf"

    proof, task = upload_payment_proof(
        db=db,
        business_id=tenant_ctx.business_id,
        invoice_id=invoice_id,
        file_content=content,
        original_filename=filename,
        content_type=file.content_type or "",
        commit=True,
    )

    try:
        queue = get_task_queue()
        queue.enqueue(
            task_id=task.id,
            task_type="parse_payment_proof",
            business_id=tenant_ctx.business_id,
            payload={
                "proof_id": str(proof.id),
                "invoice_id": str(invoice_id),
                "business_id": str(tenant_ctx.business_id),
            },
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "Could not immediately enqueue task %s to Redis: %s", task.id, e
        )

    return PaymentProofUploadResponse(
        proof_id=proof.id,
        invoice_id=proof.invoice_id,
        task_id=task.id,
        original_filename=proof.original_filename,
        file_size=proof.file_size,
        status=proof.status,
        created_at=proof.created_at,
    )


@router.get(
    "/{invoice_id}/payment-proofs",
    response_model=List[PaymentProofResponse],
    summary="List payment proof documents for an invoice",
)
def list_invoice_proofs(
    invoice_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> List[PaymentProofResponse]:
    """Retrieve all payment proof records for an invoice within authenticated tenant."""
    inv = get_invoice_by_id(
        db=db,
        business_id=tenant_ctx.business_id,
        invoice_id=invoice_id,
    )
    if not inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )
    proofs = list_proofs_for_invoice(
        db=db,
        business_id=tenant_ctx.business_id,
        invoice_id=invoice_id,
    )
    return [PaymentProofResponse.model_validate(p) for p in proofs]


@router.get(
    "/{invoice_id}/payment-proofs/{proof_id}",
    response_model=PaymentProofResponse,
    summary="Get status and details of a payment proof",
)
def get_invoice_proof(
    invoice_id: uuid.UUID,
    proof_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> PaymentProofResponse:
    """Retrieve details and live verification status for a specific payment proof."""
    proof = get_proof_by_id(
        db=db,
        business_id=tenant_ctx.business_id,
        proof_id=proof_id,
    )
    if not proof or proof.invoice_id != invoice_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment proof not found for this invoice.",
        )
    return PaymentProofResponse.model_validate(proof)


@router.get(
    "/{invoice_id}/payment-proofs/{proof_id}/file",
    summary="Download or stream an uploaded payment proof file",
)
def get_proof_file(
    invoice_id: uuid.UUID,
    proof_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Response:
    """Download the original payment proof document strictly within tenant boundary."""
    proof = get_proof_by_id(
        db=db,
        business_id=tenant_ctx.business_id,
        proof_id=proof_id,
    )
    if not proof or proof.invoice_id != invoice_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment proof not found for this invoice.",
        )

    storage = get_storage()
    try:
        content = storage.read(proof.storage_key)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment proof file not found in storage.",
        ) from e

    return Response(
        content=content,
        media_type=proof.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{proof.original_filename}"',
            "Content-Length": str(len(content)),
        },
    )


@router.get(
    "/{invoice_id}",
    response_model=InvoiceDetailResponse,
    summary="Get invoice details by ID",
)
def get_invoice(
    invoice_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> InvoiceDetailResponse:
    """Get single invoice details with payment summary. Strictly tenant scoped."""
    inv = get_invoice_by_id(
        db=db,
        business_id=tenant_ctx.business_id,
        invoice_id=invoice_id,
    )
    if not inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )
    payments = get_invoice_payments(
        db=db,
        business_id=tenant_ctx.business_id,
        invoice_id=invoice_id,
    )
    total_paid, outstanding = get_invoice_payment_summary(db, inv)
    response = InvoiceDetailResponse.model_validate(inv)
    response.total_paid = float(total_paid)
    response.outstanding_balance = float(outstanding)
    response.payments = [PaymentResponse.model_validate(p) for p in payments]
    return response
