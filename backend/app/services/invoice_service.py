"""
Invoice and InvoiceDocument business service.
Handles file validation, tenant-scoped storage, metadata persistence, and queries.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional
from fastapi import HTTPException, status
from sqlalchemy import and_, func, nulls_last, or_, select
from sqlalchemy.orm import Session, selectinload

from backend.app.core.config import settings
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.models.prediction import PredictionResult
from backend.app.services.manual_payment_service import business_today
from backend.app.services.prediction_service import eligible_predictions
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
    origin: str = "CURRENT",
    commit: bool = True,
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
        origin=origin,
    )

    try:
        db.add(doc)
        db.flush()
        if commit:
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


OUTSTANDING_STATUSES = ("OPEN", "PARTIAL")
PAYMENT_STATUS_FILTERS = frozenset({"OPEN", "PARTIAL", "PAID", "OUTSTANDING"})
AGING_FILTERS = frozenset({"current", "1-30", "31-60", "61-90", "90+", "overdue", "due-soon"})
RISK_FILTERS = frozenset({"HIGH", "MEDIUM", "LOW", "UNAVAILABLE"})
SORT_FIELDS = frozenset({"created", "uploaded", "due_date", "amount", "invoice_date"})
DUE_SOON_DAYS = 7
_LEGACY_LABEL = re.compile(r"^man-([0-9a-f]{1,8})$", re.IGNORECASE)


class InvoiceQueryError(ValueError):
    """A list filter or sort value is not recognised."""


@dataclass(frozen=True)
class InvoicePage:
    items: List[Invoice]
    total: int
    predictions: Dict[uuid.UUID, PredictionResult]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _aging_condition(aging: str, today: date):
    due = Invoice.due_date
    windows = {
        "current": due >= today,
        "1-30": due.between(today - timedelta(days=30), today - timedelta(days=1)),
        "31-60": due.between(today - timedelta(days=60), today - timedelta(days=31)),
        "61-90": due.between(today - timedelta(days=90), today - timedelta(days=61)),
        "90+": due < today - timedelta(days=90),
        "overdue": due < today,
        "due-soon": due.between(today, today + timedelta(days=DUE_SOON_DAYS)),
    }
    return and_(Invoice.payment_status.in_(OUTSTANDING_STATUSES), windows[aging])


def list_invoices_for_tenant(
    db: Session,
    business_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
    payment_status: Optional[str] = None,
    origin: Optional[str] = None,
    *,
    q: Optional[str] = None,
    aging: Optional[str] = None,
    risk: Optional[str] = None,
    sort: str = "created",
    direction: str = "desc",
    today: Optional[date] = None,
) -> InvoicePage:
    """One page of tenant invoices filtered, sorted and counted in the database.

    Aging matches the dashboard (only OPEN/PARTIAL invoices, India business
    date). Risk filters use estimates that pass the history gate, so the list
    never shows an estimate the invoice page would hide.
    """
    status_filter = payment_status.upper() if payment_status else None
    aging_filter = aging.lower() if aging else None
    risk_filter = risk.upper() if risk else None
    if status_filter and status_filter not in PAYMENT_STATUS_FILTERS:
        raise InvoiceQueryError(f"Unknown payment status '{payment_status}'.")
    if aging_filter and aging_filter not in AGING_FILTERS:
        raise InvoiceQueryError(f"Unknown aging filter '{aging}'.")
    if risk_filter and risk_filter not in RISK_FILTERS:
        raise InvoiceQueryError(f"Unknown risk filter '{risk}'.")
    if sort not in SORT_FIELDS:
        raise InvoiceQueryError(f"Unknown sort field '{sort}'.")
    if direction not in ("asc", "desc"):
        raise InvoiceQueryError("Sort direction must be 'asc' or 'desc'.")

    conditions = [Invoice.business_id == business_id]
    if origin:
        conditions.append(Invoice.origin == origin.upper())
    if status_filter == "OUTSTANDING":
        conditions.append(Invoice.payment_status.in_(OUTSTANDING_STATUSES))
    elif status_filter:
        conditions.append(Invoice.payment_status == status_filter)
    if aging_filter:
        conditions.append(_aging_condition(aging_filter, today or business_today()))

    needle = (q or "").strip()
    if needle:
        pattern = f"%{_escape_like(needle)}%"
        matches = [
            Invoice.invoice_number.ilike(pattern, escape="\\"),
            Invoice.unresolved_customer_name.ilike(pattern, escape="\\"),
            Customer.display_name.ilike(pattern, escape="\\"),
        ]
        legacy = _LEGACY_LABEL.match(needle)
        if legacy:
            matches.append(Invoice.invoice_number.ilike(f"{legacy.group(1)}%"))
        conditions.append(or_(*matches))

    predictions: Optional[Dict[uuid.UUID, PredictionResult]] = None
    if risk_filter:
        predictions = eligible_predictions(db, business_id)
        if risk_filter == "UNAVAILABLE":
            if predictions:
                conditions.append(Invoice.id.not_in(list(predictions)))
        else:
            conditions.append(
                Invoice.id.in_([iid for iid, p in predictions.items() if p.risk_tier == risk_filter])
            )

    joined = (
        select(Invoice)
        .outerjoin(Customer, Invoice.customer_id == Customer.id)
        .outerjoin(InvoiceDocument, Invoice.document_id == InvoiceDocument.id)
        .where(*conditions)
    )
    total = db.scalar(select(func.count()).select_from(joined.with_only_columns(Invoice.id).subquery())) or 0

    column = {
        "created": Invoice.created_at,
        "uploaded": func.coalesce(InvoiceDocument.created_at, Invoice.created_at),
        "due_date": Invoice.due_date,
        "amount": Invoice.amount,
        "invoice_date": Invoice.invoice_date,
    }[sort]
    ordered = column.asc() if direction == "asc" else column.desc()
    items = list(
        db.scalars(
            joined.options(selectinload(Invoice.customer))
            .order_by(nulls_last(ordered), Invoice.created_at.desc(), Invoice.id)
            .offset(skip)
            .limit(limit)
        ).all()
    )

    page_ids = [invoice.id for invoice in items]
    if predictions is None:
        predictions = eligible_predictions(db, business_id, page_ids)
    return InvoicePage(
        items=items,
        total=total,
        predictions={iid: predictions[iid] for iid in page_ids if iid in predictions},
    )
