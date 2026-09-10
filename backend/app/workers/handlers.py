"""
Worker task handlers for asynchronous background processing.
"""

import logging
import uuid
from typing import Any, Dict
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.models.payment import Payment
from backend.app.models.task import Task
from backend.app.services.parser import (
    InvoiceParser,
    PermanentParserError,
    TransientParserError,
)
from backend.app.services.storage import StorageError, StorageFileNotFoundError, get_storage

logger = logging.getLogger(__name__)


def handle_parse_invoice(
    db: Session,
    task: Task,
    payload: Dict[str, Any],
) -> None:
    """
    Handler for 'parse_invoice' task type.
    Extracts structured fields from uploaded invoice PDF, resolves customer,
    creates/updates Invoice record, reconciles existing payments, and updates statuses.
    """
    doc_id_str = payload.get("invoice_document_id")
    if not doc_id_str:
        raise PermanentParserError("Missing 'invoice_document_id' in task payload.")

    try:
        doc_id = uuid.UUID(str(doc_id_str))
    except ValueError as e:
        raise PermanentParserError(f"Invalid UUID for invoice_document_id: {doc_id_str}") from e

    # 1. Retrieve document and verify tenant boundary
    doc = db.get(InvoiceDocument, doc_id)
    if not doc:
        raise PermanentParserError(f"Invoice document {doc_id} not found in database.")

    if doc.business_id != task.business_id:
        raise PermanentParserError(
            f"Tenant boundary violation: task business {task.business_id} "
            f"does not match document business {doc.business_id}."
        )

    # 2. Idempotency: if already processed, skip duplicate extraction
    if doc.processing_status == "PROCESSED" and doc.invoice_id is not None:
        logger.info("Document %s already processed (invoice_id=%s). Skipping duplicate.", doc_id, doc.invoice_id)
        task.invoice_id = doc.invoice_id
        db.commit()
        return

    if doc.processing_status == "ERROR":
        logger.info("Document %s is already in ERROR status. Skipping duplicate extraction.", doc_id)
        raise PermanentParserError(f"Document {doc_id} previously failed processing and is in ERROR status.")

    # 3. Transition document status atomically and reload it.  This avoids
    # continuing with stale ORM state after a previous attempt or commit.
    db.execute(
        update(InvoiceDocument)
        .where(
            InvoiceDocument.id == doc_id,
            InvoiceDocument.business_id == task.business_id,
            InvoiceDocument.processing_status.in_(("PENDING", "PROCESSING")),
        )
        .values(processing_status="PROCESSING", error_message=None)
    )
    db.commit()
    db.expire_all()
    doc = db.scalar(
        select(InvoiceDocument).where(
            InvoiceDocument.id == doc_id,
            InvoiceDocument.business_id == task.business_id,
        )
    )
    if not doc:
        raise PermanentParserError(f"Invoice document {doc_id} disappeared during processing.")

    # 4. Read PDF from storage & execute parser
    try:
        storage = get_storage()
        try:
            pdf_bytes = storage.read(doc.storage_key)
        except StorageFileNotFoundError as e:
            doc.processing_status = "ERROR"
            doc.error_message = "The uploaded PDF could not be found in document storage."
            db.commit()
            raise PermanentParserError(f"Storage file missing for document {doc_id}: {doc.storage_key}") from e
        except StorageError as e:
            raise TransientParserError(f"Temporary storage error reading {doc.storage_key}: {e}") from e

        # 5. Execute Invoice Parser
        result = InvoiceParser.parse(pdf_bytes)
        if not result.success or not result.invoice:
            error_msg = result.error or "Invoice could not be parsed: required invoice fields were not found."
            doc.processing_status = "ERROR"
            doc.error_message = error_msg[:500]
            db.commit()
            raise PermanentParserError(error_msg)

    except (PermanentParserError, TransientParserError):
        raise
    except Exception as e:
        doc.processing_status = "ERROR"
        doc.error_message = "An internal parser error prevented this document from being processed."
        db.commit()
        raise PermanentParserError(f"Invoice could not be parsed: {e}") from e

    extracted = result.invoice

    # 6. Customer matching / creation
    customer = None
    if extracted.customer_ref:
        customer = db.scalar(
            select(Customer).where(
                Customer.business_id == task.business_id,
                Customer.customer_ref == extracted.customer_ref,
            )
        )
    if not customer and extracted.customer_name:
        customer = db.scalar(
            select(Customer).where(
                Customer.business_id == task.business_id,
                Customer.name == extracted.customer_name,
            )
        )
    if not customer:
        customer_name = extracted.customer_name or f"Customer {extracted.invoice_number}"
        customer = Customer(
            business_id=task.business_id,
            name=customer_name,
            customer_ref=extracted.customer_ref,
        )
        db.add(customer)
        db.flush()

    # 7. Invoice creation & duplicate handling
    existing_inv = db.scalar(
        select(Invoice).where(
            Invoice.business_id == task.business_id,
            Invoice.invoice_number == extracted.invoice_number,
        )
    )

    if existing_inv:
        invoice = existing_inv
        # A retry or a second document for the same natural invoice key must
        # converge on the same row while still repairing incomplete state.
        invoice.customer_id = customer.id
        invoice.invoice_date = extracted.invoice_date
        invoice.due_date = extracted.due_date
        invoice.amount = extracted.amount
        invoice.currency = extracted.currency
        invoice.payment_terms = extracted.payment_terms
        invoice.processing_status = "PROCESSED"
        if invoice.document_id is None:
            invoice.document_id = doc.id
        logger.info(
            "Invoice with number '%s' already exists for tenant. Linking existing record.",
            extracted.invoice_number,
        )
    else:
        invoice = Invoice(
            id=uuid.uuid4(),
            business_id=task.business_id,
            customer_id=customer.id,
            invoice_number=extracted.invoice_number,
            invoice_date=extracted.invoice_date,
            due_date=extracted.due_date,
            amount=extracted.amount,
            currency=extracted.currency,
            payment_terms=extracted.payment_terms,
            payment_status="OPEN",
            processing_status="PROCESSED",
            document_id=doc.id,
        )
        db.add(invoice)
        db.flush()

    # 8. Reconcile with historical payments
    unmatched_payments = list(
        db.scalars(
            select(Payment).where(
                Payment.business_id == task.business_id,
                Payment.invoice_reference == extracted.invoice_number,
                Payment.invoice_id.is_(None),
            )
        ).all()
    )
    if unmatched_payments:
        for p in unmatched_payments:
            p.invoice_id = invoice.id
        invoice.payment_status = "PAID"
        logger.info(
            "Reconciled %d historical payment(s) with invoice '%s'.",
            len(unmatched_payments), invoice.invoice_number,
        )

    # 9. Update InvoiceDocument and Task linkages
    doc.invoice_id = invoice.id
    doc.processing_status = "PROCESSED"
    doc.error_message = None
    task.invoice_id = invoice.id
    db.commit()
    db.refresh(task)
    db.refresh(doc)

    logger.info(
        "Successfully parsed invoice document %s into invoice %s (%s %s)",
        doc.id, invoice.invoice_number, invoice.currency, invoice.amount,
    )


def handle_predict_invoice(
    db: Session,
    task: Task,
    payload: Dict[str, Any],
) -> None:
    """
    Handler for 'predict_invoice' / 'score_invoice' task types.
    Constructs as-of features strictly at invoice posting date, executes V1 ML inference,
    and idempotently records the prediction result.
    """
    invoice_id_str = payload.get("invoice_id") or (str(task.invoice_id) if task.invoice_id else None)
    if not invoice_id_str:
        raise PermanentParserError("Missing 'invoice_id' in task payload.")

    try:
        invoice_id = uuid.UUID(str(invoice_id_str))
    except ValueError as e:
        raise PermanentParserError(f"Invalid UUID for invoice_id: {invoice_id_str}") from e

    # Verify invoice exists and tenant isolation
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise PermanentParserError(f"Invoice {invoice_id} not found in database.")

    if invoice.business_id != task.business_id:
        raise PermanentParserError(
            f"Tenant boundary violation: task business {task.business_id} "
            f"does not match invoice business {invoice.business_id}."
        )

    from backend.app.services.prediction_service import (
        InvoiceNotReadyError,
        PredictionServiceError,
        predict_for_invoice,
    )

    try:
        prediction = predict_for_invoice(
            db=db,
            invoice_id=invoice.id,
            business_id=task.business_id,
        )
        task.invoice_id = invoice.id
        db.commit()
        logger.info(
            "Task %s: Successfully scored invoice %s (risk_score=%.4f, tier=%s, days=%.1f)",
            task.id,
            invoice.id,
            prediction.risk_score,
            prediction.risk_tier,
            prediction.predicted_days_until_payment,
        )
    except (InvoiceNotReadyError, PredictionServiceError) as e:
        raise PermanentParserError(f"Cannot score invoice {invoice_id}: {e}") from e
    except Exception as e:
        logger.error("Error scoring invoice %s: %s", invoice_id, e)
        raise
