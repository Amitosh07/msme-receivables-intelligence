"""
Invoice and InvoiceDocument API endpoints.
Provides invoice PDF upload, document metadata listing, file download, and invoice read endpoints.
"""

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from backend.app.core.dependencies import TenantContext, get_db, get_tenant_context
from backend.app.schemas.invoice import (
    InvoiceDocumentResponse,
    InvoiceListResponse,
    InvoiceResponse,
    InvoiceUploadResponse,
)
from backend.app.services.invoice_service import (
    get_document_by_id,
    get_invoice_by_id,
    list_documents_for_tenant,
    list_invoices_for_tenant,
    upload_invoice_document,
)
from backend.app.services.storage import get_storage

router = APIRouter(prefix="/invoices", tags=["Invoices"])


@router.post(
    "/upload",
    response_model=InvoiceUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an invoice PDF document",
)
async def upload_invoice(
    file: UploadFile = File(..., description="Invoice document in PDF format"),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> InvoiceUploadResponse:
    """
    Upload an invoice PDF for the authenticated business tenant.
    Stores the PDF safely in object storage and creates an InvoiceDocument record in PENDING state.
    Tenant context is derived server-side from authentication.
    """
    content = await file.read()
    filename = file.filename or "invoice.pdf"

    doc = upload_invoice_document(
        db=db,
        business_id=tenant_ctx.business_id,
        file_content=content,
        original_filename=filename,
    )

    return InvoiceUploadResponse(
        document_id=doc.id,
        invoice_id=doc.invoice_id,
        original_filename=doc.original_filename,
        file_size=doc.file_size,
        processing_status=doc.processing_status,
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
    )
    return InvoiceListResponse(
        items=[InvoiceResponse.model_validate(i) for i in items],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{invoice_id}",
    response_model=InvoiceResponse,
    summary="Get invoice details by ID",
)
def get_invoice(
    invoice_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> InvoiceResponse:
    """Get single invoice details. Strictly tenant scoped."""
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
    return InvoiceResponse.model_validate(inv)
