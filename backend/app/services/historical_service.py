"""
Historical company workspace business service.
Provides listing, search, detail retrieval, and invoice/payment ingestion for the historical workspace.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import logging
from typing import List, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import String, func, null, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.models.payment import Payment
from backend.app.models.task import Task
from backend.app.schemas.historical import (
    HistoricalCompanyDetail,
    HistoricalCompanySummary,
    HistoricalInvoiceItem,
)
from backend.app.services.customer_identity import (
    create_customer,
    normalize_customer_name,
    normalize_gstin,
)


def create_historical_company(
    db: Session,
    business_id: uuid.UUID,
    display_name: str,
    gstin: Optional[str] = None,
) -> HistoricalCompanySummary:
    """Create one explicit historical workspace, rejecting normalized duplicates."""
    normalized_name = normalize_customer_name(display_name)
    if not normalized_name:
        raise ValueError("Company name is required.")
    existing = db.scalar(
        select(Customer).where(
            Customer.business_id == business_id,
            Customer.normalized_name == normalized_name,
        )
    )
    if existing is not None:
        raise FileExistsError("A company with this name already exists.")

    customer = create_customer(
        db,
        business_id=business_id,
        display_name=display_name,
        gstin=gstin,
    )
    customer.has_historical_context = True
    try:
        db.commit()
        db.refresh(customer)
    except IntegrityError as exc:
        db.rollback()
        raise FileExistsError("A company with this name or GSTIN already exists.") from exc
    return HistoricalCompanySummary(
        id=customer.id,
        display_name=customer.display_name,
        normalized_name=customer.normalized_name,
        gstin=customer.gstin,
        customer_ref=customer.customer_ref,
    )


def update_historical_company_gstin(
    db: Session,
    business_id: uuid.UUID,
    customer_id: uuid.UUID,
    gstin: Optional[str],
) -> Customer:
    customer = db.scalar(select(Customer).where(
        Customer.id == customer_id,
        Customer.business_id == business_id,
        Customer.has_historical_context.is_(True),
    ))
    if customer is None:
        raise LookupError("Historical company not found.")
    clean_gstin = gstin.strip() if gstin else None
    if clean_gstin and normalize_gstin(clean_gstin) is None:
        raise ValueError("GSTIN structure or checksum is invalid.")
    customer.gstin = clean_gstin
    try:
        db.commit()
        db.refresh(customer)
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("GSTIN is already associated with another company.") from exc
    return customer
from backend.app.services.invoice_service import upload_invoice_document
from backend.app.services.manual_payment_service import (
    derive_invoice_payment_status,
    get_invoice_payment_summary,
    record_manual_payment,
)
from backend.app.services.task_service import create_task
from backend.app.workers.queue import get_task_queue

logger = logging.getLogger(__name__)


def generate_manual_invoice_reference() -> str:
    """Generate an opaque invoice reference; database uniqueness is authoritative."""
    return str(uuid.uuid4())


def create_manual_historical_invoice(
    db: Session,
    business_id: uuid.UUID,
    customer_id: uuid.UUID,
    *,
    amount: Decimal,
    due_date: date,
    payment_date: Optional[date] = None,
) -> tuple[Invoice, Optional[Payment]]:
    """Create a historical invoice with a generated DB-unique reference."""
    customer = db.scalar(select(Customer).where(
        Customer.id == customer_id, Customer.business_id == business_id
    ))
    if customer is None:
        raise LookupError("Company not found.")
    if due_date.year < 1990 or due_date.year > 2100:
        raise ValueError("Due date must be between 1990 and 2100.")
    reference = generate_manual_invoice_reference()
    invoice = Invoice(
        business_id=business_id,
        customer_id=customer.id,
        invoice_number=reference,
        invoice_date=None,
        due_date=due_date,
        amount=amount,
        currency=null(),
        payment_status="OPEN",
        processing_status="PROCESSED",
        origin=InvoiceOrigin.HISTORICAL.value,
    )
    customer.has_historical_context = True
    try:
        db.add(invoice)
        db.flush()
        payment = None
        if payment_date is not None:
            payment = record_manual_payment(
                db,
                business_id=business_id,
                invoice_id=invoice.id,
                payment_date=payment_date,
                amount=amount,
                note="Manual historical invoice settlement",
            ).payment
        else:
            db.commit()
            db.refresh(invoice)
        return invoice, payment
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("Generated invoice identifier already exists; please retry.") from exc
    except Exception:
        db.rollback()
        raise


def list_historical_companies(
    db: Session,
    business_id: uuid.UUID,
    search: Optional[str] = None,
) -> List[HistoricalCompanySummary]:
    """
    Dynamically list companies/customers that have historical records within the authenticated tenant.
    Optionally filters by company name using normalized customer identity mechanisms.
    """
    # Identify customer IDs having historical invoices or historical payments
    hist_invoice_subq = (
        select(Invoice.customer_id)
        .where(
            Invoice.business_id == business_id,
            Invoice.origin == InvoiceOrigin.HISTORICAL.value,
            Invoice.customer_id.isnot(None),
        )
    )

    hist_payment_subq = (
        select(Customer.id)
        .join(
            Payment,
            Payment.customer_identity_key == func.concat("customer:", func.cast(Customer.id, String(36))),
        )
        .where(
            Customer.business_id == business_id,
            Payment.business_id == business_id,
            Payment.provenance.in_(["legacy", "import"]),
        )
    )

    eligible_subq = hist_invoice_subq.union(hist_payment_subq)

    query = select(Customer).where(
        Customer.business_id == business_id,
        (Customer.has_historical_context.is_(True)) | Customer.id.in_(eligible_subq),
    )

    if search and search.strip():
        norm_search = normalize_customer_name(search)
        raw_search = f"%{search.strip().lower()}%"
        if norm_search:
            norm_pattern = f"%{norm_search}%"
            query = query.where(
                Customer.normalized_name.contains(norm_pattern)
                | func.lower(Customer.display_name).like(raw_search)
            )
        else:
            query = query.where(func.lower(Customer.display_name).like(raw_search))

    customers = list(db.scalars(query.order_by(Customer.display_name.asc())).all())

    results: List[HistoricalCompanySummary] = []
    for cust in customers:
        # Load all historical invoices for this customer
        invoices = list(
            db.scalars(
                select(Invoice).where(
                    Invoice.business_id == business_id,
                    Invoice.customer_id == cust.id,
                    Invoice.origin == InvoiceOrigin.HISTORICAL.value,
                )
            ).all()
        )

        inv_ids = [inv.id for inv in invoices]
        payments_query = select(Payment).where(
            Payment.business_id == business_id,
        )
        if inv_ids:
            payments_query = payments_query.where(
                (Payment.invoice_id.in_(inv_ids))
                | (Payment.customer_identity_key == f"customer:{cust.id}")
            )
        else:
            payments_query = payments_query.where(
                Payment.customer_identity_key == f"customer:{cust.id}"
            )
        payments = list(db.scalars(payments_query).all())

        total_amount = sum(float(inv.amount) for inv in invoices)
        total_paid = sum(float(p.amount) for p in payments)
        outstanding = max(0.0, total_amount - total_paid)

        last_inv_date = max(
            (inv.invoice_date for inv in invoices if inv.invoice_date is not None),
            default=None,
        )
        last_pmt_date = max(
            (
                p.payment_date.date() if isinstance(p.payment_date, datetime) else p.payment_date
                for p in payments
            ),
            default=None,
        )

        results.append(
            HistoricalCompanySummary(
                id=cust.id,
                display_name=cust.display_name,
                normalized_name=cust.normalized_name,
                gstin=cust.gstin,
                customer_ref=cust.customer_ref,
                historical_invoice_count=len(invoices),
                total_amount=total_amount,
                total_paid=total_paid,
                outstanding_balance=outstanding,
                last_invoice_date=last_inv_date,
                last_payment_date=last_pmt_date,
            )
        )

    return results


def get_historical_company_detail(
    db: Session,
    business_id: uuid.UUID,
    customer_id: uuid.UUID,
) -> Optional[HistoricalCompanyDetail]:
    """
    Given a company/customer ID, return its full historical records.
    Payment status for each invoice is derived strictly from real Payment rows.
    """
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.business_id == business_id,
        )
    )
    if customer is None:
        return None

    has_historical_invoice = db.scalar(
        select(Invoice.id).where(
            Invoice.business_id == business_id,
            Invoice.customer_id == customer_id,
            Invoice.origin == InvoiceOrigin.HISTORICAL.value,
        ).limit(1)
    ) is not None
    if not customer.has_historical_context and not has_historical_invoice:
        return None

    invoices = list(
        db.scalars(
            select(Invoice)
            .where(
                Invoice.business_id == business_id,
                Invoice.customer_id == customer_id,
                Invoice.origin == InvoiceOrigin.HISTORICAL.value,
            )
            .order_by(Invoice.invoice_date.desc(), Invoice.created_at.desc())
        ).all()
    )

    invoice_items: List[HistoricalInvoiceItem] = []
    for inv in invoices:
        payments = list(
            db.scalars(
                select(Payment)
                .where(
                    Payment.business_id == business_id,
                    Payment.invoice_id == inv.id,
                )
                .order_by(Payment.payment_date.desc(), Payment.created_at.desc())
            ).all()
        )

        actual_status = derive_invoice_payment_status(db, inv)
        total_paid_dec, outstanding_dec = get_invoice_payment_summary(db, inv)

        latest_payment_date: Optional[date] = None
        if payments:
            first_p = payments[0].payment_date
            latest_payment_date = first_p.date() if isinstance(first_p, datetime) else first_p

        invoice_items.append(
            HistoricalInvoiceItem(
                id=inv.id,
                invoice_number=inv.invoice_number,
                invoice_date=inv.invoice_date,
                due_date=inv.due_date,
                amount=float(inv.amount),
                currency=inv.currency,
                origin=inv.origin,
                payment_status=actual_status,
                processing_status=inv.processing_status,
                total_paid=float(total_paid_dec),
                outstanding_balance=float(outstanding_dec),
                payment_date=latest_payment_date,
                payment_count=len(payments),
                document_id=inv.document_id,
            )
        )

    total_amount = sum(item.amount for item in invoice_items)
    total_paid = sum(item.total_paid for item in invoice_items)
    outstanding_balance = max(0.0, total_amount - total_paid)

    return HistoricalCompanyDetail(
        id=customer.id,
        display_name=customer.display_name,
        normalized_name=customer.normalized_name,
        gstin=customer.gstin,
        customer_ref=customer.customer_ref,
        historical_invoice_count=len(invoice_items),
        total_amount=total_amount,
        total_paid=total_paid,
        outstanding_balance=outstanding_balance,
        invoices=invoice_items,
    )


def upload_historical_company_invoice(
    db: Session,
    business_id: uuid.UUID,
    customer_id: uuid.UUID,
    file_content: bytes,
    original_filename: str,
) -> tuple[InvoiceDocument, Task]:
    """
    Upload a historical invoice document bound authoritatively to the given customer workspace.
    Explicitly tags origin='HISTORICAL' and enqueues parsing task with customer_id context.
    """
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.business_id == business_id,
        )
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    doc = upload_invoice_document(
        db=db,
        business_id=business_id,
        file_content=file_content,
        original_filename=original_filename,
        origin=InvoiceOrigin.HISTORICAL.value,
        commit=False,
    )

    task = create_task(
        db=db,
        business_id=business_id,
        task_type="parse_invoice",
        payload={
            "invoice_document_id": str(doc.id),
            "business_id": str(business_id),
            "origin": InvoiceOrigin.HISTORICAL.value,
            "customer_id": str(customer_id),
        },
        commit=False,
    )

    try:
        db.commit()
        db.refresh(doc)
        db.refresh(task)
    except Exception:
        db.rollback()
        raise

    try:
        queue = get_task_queue()
        queue.enqueue(
            task_id=task.id,
            task_type="parse_invoice",
            business_id=business_id,
            payload={
                "invoice_document_id": str(doc.id),
                "business_id": str(business_id),
                "origin": InvoiceOrigin.HISTORICAL.value,
                "customer_id": str(customer_id),
            },
        )
    except Exception as exc:
        logger.warning(
            "Could not immediately enqueue task %s to Redis: %s",
            task.id,
            exc,
        )

    return doc, task


def upload_unassigned_historical_invoice(
    db: Session,
    business_id: uuid.UUID,
    file_content: bytes,
    original_filename: str,
) -> tuple[InvoiceDocument, Task]:
    """Upload a historical PDF and let reliable parser identity drive association."""
    doc = upload_invoice_document(
        db=db,
        business_id=business_id,
        file_content=file_content,
        original_filename=original_filename,
        origin=InvoiceOrigin.HISTORICAL.value,
        commit=False,
    )
    payload = {
        "invoice_document_id": str(doc.id),
        "business_id": str(business_id),
        "origin": InvoiceOrigin.HISTORICAL.value,
    }
    task = create_task(
        db=db, business_id=business_id, task_type="parse_invoice", payload=payload, commit=False
    )
    db.commit()
    db.refresh(doc)
    db.refresh(task)
    try:
        get_task_queue().enqueue(
            task_id=task.id,
            task_type="parse_invoice",
            business_id=business_id,
            payload=payload,
        )
    except Exception as exc:
        logger.warning("Could not immediately enqueue task %s to Redis: %s", task.id, exc)
    return doc, task


def complete_historical_invoice_review(
    db: Session,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
    *,
    customer_id: Optional[uuid.UUID] = None,
    company_name: Optional[str] = None,
    gstin: Optional[str] = None,
    due_date: Optional[date] = None,
) -> Invoice:
    invoice = db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == business_id,
            Invoice.origin == InvoiceOrigin.HISTORICAL.value,
        )
    )
    if invoice is None:
        raise LookupError("Historical invoice not found.")
    if customer_id:
        customer = db.scalar(
            select(Customer).where(
                Customer.id == customer_id, Customer.business_id == business_id
            )
        )
        if customer is None:
            raise LookupError("Company not found.")
    elif company_name:
        customer = create_customer(
            db, business_id=business_id, display_name=company_name, gstin=gstin
        )
    else:
        customer = invoice.customer
    if customer:
        customer.has_historical_context = True
        invoice.customer_id = customer.id
        invoice.unresolved_customer_name = None
    if due_date:
        invoice.due_date = due_date
    invoice.processing_status = (
        "PROCESSED" if invoice.customer_id and invoice.due_date else "NEEDS_REVIEW"
    )
    if invoice.document:
        invoice.document.processing_status = invoice.processing_status
        needs = []
        if not invoice.customer_id:
            needs.append("company selection")
        if not invoice.due_date:
            needs.append("due date")
        invoice.document.error_message = (
            "Manual review required: " + " and ".join(needs) + "." if needs else None
        )
    db.commit()
    db.refresh(invoice)
    return invoice
