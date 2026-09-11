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
from sqlalchemy import String, func, select
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
from backend.app.services.customer_identity import normalize_customer_name
from backend.app.services.invoice_service import upload_invoice_document
from backend.app.services.manual_payment_service import (
    derive_invoice_payment_status,
    get_invoice_payment_summary,
)
from backend.app.services.task_service import create_task
from backend.app.workers.queue import get_task_queue

logger = logging.getLogger(__name__)


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
        Customer.id.in_(eligible_subq),
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

        last_inv_date = max((inv.invoice_date for inv in invoices), default=None)
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
