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
from sqlalchemy import String, delete, func, null, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.models.payment import Payment
from backend.app.models.payment_proof import PaymentProof
from backend.app.models.prediction import PredictionResult
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
from backend.app.services.storage import get_storage


def create_historical_company(
    db: Session,
    business_id: uuid.UUID,
    display_name: str,
    gstin: Optional[str] = None,
) -> HistoricalCompanySummary:
    """Create one explicit historical workspace, rejecting normalized duplicates."""
    clean_name = display_name.strip() if display_name else ""
    normalized_name = normalize_customer_name(clean_name)
    if not clean_name or not normalized_name:
        raise ValueError("Company name is required.")

    clean_gstin = gstin.strip() if gstin and gstin.strip() else None
    if clean_gstin and normalize_gstin(clean_gstin) is None:
        raise ValueError("GSTIN structure or checksum is invalid.")

    existing = db.scalar(
        select(Customer).where(
            Customer.business_id == business_id,
            Customer.normalized_name == normalized_name,
        )
    )
    if existing is not None:
        raise FileExistsError("A company with this name already exists.")

    if clean_gstin:
        norm_gstin = normalize_gstin(clean_gstin)
        existing_gstin = db.scalar(
            select(Customer).where(
                Customer.business_id == business_id,
                Customer.normalized_gstin == norm_gstin,
            )
        )
        if existing_gstin is not None:
            raise FileExistsError("A company with this GSTIN already exists.")

    customer = create_customer(
        db,
        business_id=business_id,
        display_name=clean_name,
        gstin=clean_gstin,
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

    paid_current_subq = (
        select(Invoice.customer_id)
        .join(Payment, Payment.invoice_id == Invoice.id)
        .where(
            Invoice.business_id == business_id,
            Invoice.customer_id.isnot(None),
        )
    )

    eligible_subq = hist_invoice_subq.union(hist_payment_subq, paid_current_subq)

    query = select(Customer).where(
        Customer.business_id == business_id,
        (Customer.has_historical_context.is_(True)) | Customer.id.in_(eligible_subq),
    )

    if search and search.strip():
        clean_search = search.strip()
        norm_search = normalize_customer_name(clean_search)
        raw_search = f"%{clean_search.lower()}%"
        search_conditions = [
            func.lower(Customer.display_name).like(raw_search),
            func.lower(Customer.normalized_name).like(raw_search),
            (Customer.gstin.isnot(None) & func.lower(Customer.gstin).like(raw_search)),
        ]
        if norm_search:
            norm_pattern = f"%{norm_search}%"
            search_conditions.append(Customer.normalized_name.contains(norm_pattern))
        query = query.where(or_(*search_conditions))

    customers = list(db.scalars(query.order_by(Customer.display_name.asc())).all())

    results: List[HistoricalCompanySummary] = []
    for cust in customers:
        # Load invoices contributing to customer receivables / payment history
        invoices = list(
            db.scalars(
                select(Invoice).where(
                    Invoice.business_id == business_id,
                    Invoice.customer_id == cust.id,
                    or_(
                        Invoice.origin == InvoiceOrigin.HISTORICAL.value,
                        Invoice.id.in_(
                            select(Payment.invoice_id).where(
                                Payment.business_id == business_id
                            )
                        ),
                    ),
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

    has_history = (
        customer.has_historical_context
        or db.scalar(
            select(Invoice.id).where(
                Invoice.business_id == business_id,
                Invoice.customer_id == customer_id,
                or_(
                    Invoice.origin == InvoiceOrigin.HISTORICAL.value,
                    Invoice.id.in_(
                        select(Payment.invoice_id).where(Payment.business_id == business_id)
                    ),
                ),
            ).limit(1)
        ) is not None
    )
    if not has_history:
        return None

    invoices = list(
        db.scalars(
            select(Invoice)
            .where(
                Invoice.business_id == business_id,
                Invoice.customer_id == customer_id,
                or_(
                    Invoice.origin == InvoiceOrigin.HISTORICAL.value,
                    Invoice.id.in_(
                        select(Payment.invoice_id).where(Payment.business_id == business_id)
                    ),
                ),
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
                customer_id=customer.id,
                customer_name=customer.display_name,
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
    elif company_name and company_name.strip():
        clean_name = company_name.strip()
        norm_name = normalize_customer_name(clean_name)
        existing = db.scalar(
            select(Customer).where(
                Customer.business_id == business_id,
                (Customer.normalized_name == norm_name)
                | (func.lower(Customer.display_name) == clean_name.lower()),
            )
        )
        if existing is not None:
            customer = existing
        else:
            customer = create_customer(
                db, business_id=business_id, display_name=clean_name, gstin=gstin
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


def correct_historical_invoice_company(
    db: Session,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
    *,
    replacement_customer_id: Optional[uuid.UUID] = None,
    replacement_company_name: Optional[str] = None,
    create_if_missing: bool = False,
    gstin: Optional[str] = None,
) -> Invoice:
    """
    Correct or change the company associated with a historical invoice.
    - Tenant-scoped
    - Uses existing company/customer identity matching (case-insensitive & normalized)
    - If company exists: attaches without creating duplicate
    - If company does not exist: creates only after explicit user confirmation (create_if_missing=True)
    - Preserves historical status
    - Safely updates historical invoice association
    """
    invoice = db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == business_id,
            Invoice.origin == InvoiceOrigin.HISTORICAL.value,
        )
    )
    if invoice is None:
        raise LookupError("Historical invoice not found.")

    current_customer = invoice.customer

    if replacement_customer_id:
        target_customer = db.scalar(
            select(Customer).where(
                Customer.id == replacement_customer_id,
                Customer.business_id == business_id,
            )
        )
        if target_customer is None:
            raise LookupError("Replacement company not found.")
    elif replacement_company_name and replacement_company_name.strip():
        clean_name = replacement_company_name.strip()
        norm_name = normalize_customer_name(clean_name)
        if not clean_name or not norm_name:
            raise ValueError("Replacement company name cannot be empty.")

        # 1. Search existing company case-insensitively and by normalized name within tenant
        target_customer = db.scalar(
            select(Customer).where(
                Customer.business_id == business_id,
                (Customer.normalized_name == norm_name)
                | (func.lower(Customer.display_name) == clean_name.lower()),
            )
        )
        if target_customer is None and gstin:
            clean_gstin = normalize_gstin(gstin)
            if clean_gstin:
                target_customer = db.scalar(
                    select(Customer).where(
                        Customer.business_id == business_id,
                        Customer.normalized_gstin == clean_gstin,
                    )
                )

        if target_customer is None:
            if not create_if_missing:
                raise LookupError(
                    f"Company '{clean_name}' does not exist in this workspace. Please confirm to create a new company."
                )
            clean_gstin = gstin.strip() if gstin and gstin.strip() else None
            if clean_gstin and normalize_gstin(clean_gstin) is None:
                raise ValueError("GSTIN structure or checksum is invalid.")
            target_customer = create_customer(
                db,
                business_id=business_id,
                display_name=clean_name,
                gstin=clean_gstin,
            )
    else:
        raise ValueError("Either replacement_customer_id or replacement_company_name must be provided.")

    target_customer.has_historical_context = True
    invoice.customer_id = target_customer.id
    invoice.unresolved_customer_name = None

    if invoice.due_date:
        invoice.processing_status = "PROCESSED"
    else:
        invoice.processing_status = "NEEDS_REVIEW"

    if invoice.document:
        invoice.document.processing_status = invoice.processing_status
        if not invoice.due_date:
            invoice.document.error_message = "Manual review required: due date."
        else:
            invoice.document.error_message = None

    # If previous customer had historical context and no longer has any historical data:
    if current_customer and current_customer.id != target_customer.id:
        remaining_hist_inv = db.scalar(
            select(func.count(Invoice.id)).where(
                Invoice.business_id == business_id,
                Invoice.customer_id == current_customer.id,
                Invoice.origin == InvoiceOrigin.HISTORICAL.value,
                Invoice.id != invoice.id,
            )
        )
        remaining_hist_pmt = db.scalar(
            select(func.count(Payment.id)).where(
                Payment.business_id == business_id,
                Payment.customer_identity_key == f"customer:{current_customer.id}",
                Payment.provenance.in_(["legacy", "import"]),
            )
        )
        if (remaining_hist_inv or 0) == 0 and (remaining_hist_pmt or 0) == 0:
            current_customer.has_historical_context = False

    # Invalidate any stale prediction for this invoice
    db.execute(
        delete(PredictionResult).where(
            PredictionResult.business_id == business_id,
            PredictionResult.invoice_id == invoice.id,
        )
    )

    db.commit()
    db.refresh(invoice)
    return invoice


def delete_historical_company(
    db: Session,
    business_id: uuid.UUID,
    customer_id: uuid.UUID,
) -> dict:
    """
    Transactionally delete historical company data.
    - If company has operational (CURRENT) invoices:
      Keep the Customer and CURRENT data, but remove all HISTORICAL invoices,
      their payments, predictions, payment proofs, tasks, and document files,
      and set customer.has_historical_context = False.
    - If company has ONLY historical data:
      Safely remove the Customer row completely, along with all historical records.
    """
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.business_id == business_id,
        )
    )
    if customer is None:
        raise LookupError("Historical company not found.")

    current_invoices = list(
        db.scalars(
            select(Invoice).where(
                Invoice.business_id == business_id,
                Invoice.customer_id == customer_id,
                Invoice.origin == InvoiceOrigin.CURRENT.value,
            )
        ).all()
    )
    has_current_invoices = len(current_invoices) > 0

    historical_invoices = list(
        db.scalars(
            select(Invoice).where(
                Invoice.business_id == business_id,
                Invoice.customer_id == customer_id,
                Invoice.origin == InvoiceOrigin.HISTORICAL.value,
            )
        ).all()
    )
    hist_inv_ids = [inv.id for inv in historical_invoices]

    storage_keys_to_delete: list[str] = []

    # Collect documents associated with historical invoices
    docs: list[InvoiceDocument] = []
    if hist_inv_ids:
        docs = list(
            db.scalars(
                select(InvoiceDocument).where(
                    InvoiceDocument.business_id == business_id,
                    (InvoiceDocument.invoice_id.in_(hist_inv_ids))
                    | (InvoiceDocument.id.in_([inv.document_id for inv in historical_invoices if inv.document_id is not None])),
                )
            ).all()
        )
    for doc in docs:
        if doc.storage_key:
            storage_keys_to_delete.append(doc.storage_key)

    # Collect payment proofs for historical invoices
    if hist_inv_ids:
        proofs = list(
            db.scalars(
                select(PaymentProof).where(
                    PaymentProof.business_id == business_id,
                    PaymentProof.invoice_id.in_(hist_inv_ids),
                )
            ).all()
        )
        for proof in proofs:
            if proof.storage_key:
                storage_keys_to_delete.append(proof.storage_key)

    # Unlink circular invoice <-> document references
    for inv in historical_invoices:
        inv.document_id = None
    for doc in docs:
        doc.invoice_id = None
    db.flush()

    # Delete documents
    for doc in docs:
        db.delete(doc)
    db.flush()

    # Delete historical invoices (cascades payments, predictions, payment proofs, tasks)
    for inv in historical_invoices:
        db.delete(inv)
    db.flush()

    # Delete standalone historical payments for this customer (with no invoice_id)
    standalone_payments = list(
        db.scalars(
            select(Payment).where(
                Payment.business_id == business_id,
                Payment.customer_identity_key == f"customer:{customer_id}",
                Payment.invoice_id.is_(None),
            )
        ).all()
    )
    for sp in standalone_payments:
        db.delete(sp)
    db.flush()

    if has_current_invoices:
        customer.has_historical_context = False
        db.add(customer)
        db.flush()
        action = "historical_records_removed"
    else:
        db.delete(customer)
        db.flush()
        action = "company_deleted"

    db.commit()

    # Post-commit: delete physical storage files
    storage = get_storage()
    for key in storage_keys_to_delete:
        try:
            storage.delete(key)
        except Exception as exc:
            logger.warning("Could not delete storage file %s during company deletion: %s", key, exc)

    return {
        "message": "Historical company deleted successfully." if action == "company_deleted" else "Historical records removed from operational company.",
        "id": str(customer_id),
        "action": action,
    }
