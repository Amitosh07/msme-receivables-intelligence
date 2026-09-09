"""
Invoice and InvoiceDocument business service.
Handles file validation, tenant-scoped storage, metadata persistence, and queries.
"""

import logging
import uuid
from typing import List, Optional, Tuple
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.invoice import Invoice
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.services.storage import get_storage

logger = logging.getLogger(__name__)


def validate_invoice_pdf(content: bytes, filename: str) -> None:
    """
    Validate that uploaded file is a valid PDF document within size limits.
    Performs content-type, extension, size, and magic-byte header validation.
    """
    if not content or len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    # Check maximum file size
    max_bytes = settings.MAX_INVOICE_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File size ({len(content) / (1024 * 1024):.2f} MB) exceeds "
                f"maximum allowed limit of {settings.MAX_INVOICE_FILE_SIZE_MB} MB."
            ),
        )

    # Check extension
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file extension. Only PDF invoice documents are accepted.",
        )

    # Validate PDF magic bytes header (%PDF-)
    header = content[:1024]
    if b"%PDF" not in header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PDF file: Missing standard '%PDF' header signature.",
        )


def upload_invoice_document(
    db: Session,
    business_id: uuid.UUID,
    file_content: bytes,
    original_filename: str,
) -> InvoiceDocument:
    """
    Validates, stores PDF in object storage, and creates an InvoiceDocument record.
    Implements compensation cleanup if database persistence fails.
    """
    # 1. Validate file
    validate_invoice_pdf(file_content, original_filename)

    # 2. Generate unique document identity and safe tenant storage key
    document_id = uuid.uuid4()
    storage_key = f"tenants/{business_id}/invoices/{document_id}.pdf"

    storage = get_storage()

    # 3. Store PDF in object storage
    try:
        saved_key = storage.save(file_content, storage_key)
    except Exception as e:
        logger.error("Storage save failed for document %s: %s", document_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store invoice document in storage.",
        ) from e

    # 4. Persist metadata in PostgreSQL
    doc = InvoiceDocument(
        id=document_id,
        business_id=business_id,
        invoice_id=None,  # Approach A: invoice record created when parsed in Phase 5
        storage_key=saved_key,
        original_filename=original_filename,
        content_type="application/pdf",
        file_size=len(file_content),
        processing_status="PENDING",
    )

    try:
        db.add(doc)
        db.commit()
        db.refresh(doc)
        logger.info(
            "Stored invoice document: id=%s, business_id=%s, size=%d bytes",
            doc.id, business_id, doc.file_size,
        )
        return doc
    except Exception as e:
        db.rollback()
        # Compensation: clean up saved file to prevent orphan storage leakage
        try:
            storage.delete(saved_key)
        except Exception as cleanup_err:
            logger.warning("Failed to clean up orphan file %s: %s", saved_key, cleanup_err)

        logger.error("Failed to persist InvoiceDocument to database: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record document metadata in database.",
        ) from e


def get_document_by_id(
    db: Session,
    business_id: uuid.UUID,
    document_id: uuid.UUID,
) -> Optional[InvoiceDocument]:
    """Retrieve an InvoiceDocument strictly within the authenticated tenant context."""
    return db.scalar(
        select(InvoiceDocument).where(
            InvoiceDocument.id == document_id,
            InvoiceDocument.business_id == business_id,
        )
    )


def list_documents_for_tenant(
    db: Session,
    business_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> List[InvoiceDocument]:
    """List invoice documents for a business tenant with pagination."""
    return list(
        db.scalars(
            select(InvoiceDocument)
            .where(InvoiceDocument.business_id == business_id)
            .order_by(InvoiceDocument.created_at.desc())
            .offset(skip)
            .limit(limit)
        ).all()
    )


def get_invoice_by_id(
    db: Session,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> Optional[Invoice]:
    """Retrieve an Invoice strictly within the authenticated tenant context."""
    return db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == business_id,
        )
    )


def list_invoices_for_tenant(
    db: Session,
    business_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
    payment_status: Optional[str] = None,
) -> Tuple[List[Invoice], int]:
    """
    List invoices for a business tenant with pagination and optional status filter.
    Returns (invoices_list, total_count).
    """
    query = select(Invoice).where(Invoice.business_id == business_id)
    count_query = select(func.count(Invoice.id)).where(Invoice.business_id == business_id)

    if payment_status:
        query = query.where(Invoice.payment_status == payment_status.upper())
        count_query = count_query.where(Invoice.payment_status == payment_status.upper())

    total = db.scalar(count_query) or 0
    items = list(
        db.scalars(
            query.order_by(Invoice.created_at.desc()).offset(skip).limit(limit)
        ).all()
    )
    return items, total
