"""
Worker task handlers for asynchronous background processing.
"""

import logging
import uuid
from typing import Any, Dict
from sqlalchemy import null, select, update
from sqlalchemy.orm import Session

from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.models.payment import Payment
from backend.app.models.task import Task
from backend.app.services.parser import (
    InvoiceParser,
    PermanentParserError,
    TransientParserError,
)
from backend.app.services.customer_identity import (
    CustomerIdentityResult,
    resolve_customer_identity,
    resolve_or_create_historical_customer,
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
        is_historical = doc.origin == InvoiceOrigin.HISTORICAL.value
        result = InvoiceParser.parse(pdf_bytes, historical=is_historical)
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

    # 6. Locate the natural invoice key before identity resolution so an
    # already-confirmed customer_id remains the strongest evidence on retries.
    existing_inv = None
    if extracted.invoice_number:
        existing_inv = db.scalar(
            select(Invoice).where(
                Invoice.business_id == task.business_id,
                Invoice.invoice_number == extracted.invoice_number,
            )
        )

    payload_customer_id = None
    if payload.get("customer_id"):
        try:
            payload_customer_id = uuid.UUID(str(payload["customer_id"]))
        except ValueError:
            payload_customer_id = None

    identity_args = dict(
        business_id=task.business_id,
        customer_id=existing_inv.customer_id if existing_inv else payload_customer_id,
        gstin=extracted.customer_gstin,
        customer_ref=extracted.customer_ref,
        display_name=extracted.customer_name,
    )
    if is_historical and extracted.customer_name and payload_customer_id is None:
        try:
            identity = resolve_or_create_historical_customer(db, **identity_args)
        except ValueError:
            # Conflicting or ambiguous extracted identity requires human choice;
            # it must not turn an otherwise usable historical invoice into ERROR.
            identity = CustomerIdentityResult(None)
        if identity.customer:
            identity.customer.has_historical_context = True
    else:
        identity = resolve_customer_identity(db, **identity_args)
    customer = identity.customer
    unresolved_customer_name = None if customer else extracted.customer_name

    # 7. Invoice creation & duplicate handling

    if existing_inv:
        invoice = existing_inv
        # A retry or a second document for the same natural invoice key must
        # converge on the same row while still repairing incomplete state.
        invoice.customer_id = customer.id if customer else None
        invoice.unresolved_customer_name = unresolved_customer_name
        invoice.invoice_date = extracted.invoice_date
        invoice.due_date = extracted.due_date
        invoice.amount = extracted.amount
        invoice.currency = extracted.currency if is_historical else (extracted.currency or "INR")
        invoice.payment_terms = extracted.payment_terms
        invoice.processing_status = (
            "NEEDS_REVIEW" if is_historical and (not customer or not extracted.due_date) else "PROCESSED"
        )
        if invoice.document_id is None:
            invoice.document_id = doc.id
        logger.info(
            "Invoice with number '%s' already exists for tenant. Linking existing record.",
            extracted.invoice_number,
        )
    else:
        origin_val = (doc.origin if doc and doc.origin else None) or payload.get("origin") or "CURRENT"
        invoice = Invoice(
            id=uuid.uuid4(),
            business_id=task.business_id,
            customer_id=customer.id if customer else None,
            unresolved_customer_name=unresolved_customer_name,
            invoice_number=extracted.invoice_number,
            invoice_date=extracted.invoice_date,
            due_date=extracted.due_date,
            amount=extracted.amount,
            currency=(
                extracted.currency if extracted.currency
                else (null() if is_historical else "INR")
            ),
            payment_terms=extracted.payment_terms,
            payment_status="OPEN",
            processing_status=(
                "NEEDS_REVIEW" if is_historical and (not customer or not extracted.due_date) else "PROCESSED"
            ),
            origin=origin_val,
            document_id=doc.id,
        )
        db.add(invoice)
        db.flush()

    # 8. Reconcile with historical payments
    unmatched_payments = []
    if extracted.invoice_number:
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
    doc.processing_status = invoice.processing_status
    review_needs = []
    if is_historical and not customer:
        review_needs.append("company selection")
    if is_historical and not extracted.due_date:
        review_needs.append("due date")
    doc.error_message = (
        "Manual review required: " + " and ".join(review_needs) + "."
        if review_needs else None
    )
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
        InsufficientCustomerHistoryError,
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
    except InsufficientCustomerHistoryError as e:
        task.invoice_id = invoice.id
        db.commit()
        logger.info(
            "Task %s: Prediction unavailable for invoice %s: %s "
            "(eligible_history_count=%d, required=%d)",
            task.id,
            invoice.id,
            e,
            e.eligible_history_count,
            e.required_history_count,
        )
    except (InvoiceNotReadyError, PredictionServiceError) as e:
        raise PermanentParserError(f"Cannot score invoice {invoice_id}: {e}") from e
    except Exception as e:
        logger.error("Error scoring invoice %s: %s", invoice_id, e)
        raise


def handle_parse_payment_proof(
    db: Session,
    task: Task,
    payload: Dict[str, Any],
) -> None:
    """
    Handler for 'parse_payment_proof' task type.
    Extracts structured payment proof data, verifies against target invoice & tenant customer,
    creates real Payment with provenance='proof_verified' if unambiguous, or transitions to NEEDS_REVIEW/FAILED.
    """
    proof_id_str = payload.get("proof_id")
    invoice_id_str = payload.get("invoice_id")
    business_id_str = payload.get("business_id")
    if not proof_id_str:
        raise PermanentParserError("Missing 'proof_id' in task payload.")
    if not invoice_id_str or not business_id_str:
        raise PermanentParserError("Payment proof task is missing tenant or target-invoice context.")

    try:
        proof_id = uuid.UUID(str(proof_id_str))
    except ValueError as e:
        raise PermanentParserError(f"Invalid UUID for proof_id: {proof_id_str}") from e
    try:
        payload_invoice_id = uuid.UUID(str(invoice_id_str))
        payload_business_id = uuid.UUID(str(business_id_str))
    except ValueError as e:
        raise PermanentParserError("Invalid UUID in payment proof task context.") from e

    from backend.app.models.payment_proof import PaymentProof
    from backend.app.services.payment_proof_service import verify_and_process_proof

    proof = db.get(PaymentProof, proof_id)
    if not proof:
        raise PermanentParserError(f"Payment proof {proof_id} not found in database.")

    if proof.business_id != task.business_id:
        raise PermanentParserError(
            f"Tenant boundary violation: task business {task.business_id} "
            f"does not match proof business {proof.business_id}."
        )
    if payload_business_id != task.business_id or proof.invoice_id != payload_invoice_id:
        raise PermanentParserError("Payment proof task target does not match its persisted tenant context.")

    # Process and verify
    result = verify_and_process_proof(db, proof_id=proof.id)
    task.invoice_id = result.invoice_id
    if result.status == "FAILED":
        raise PermanentParserError(result.error_message or "Payment proof verification failed.")
    db.commit()
    logger.info(
        "Task %s: Completed processing proof %s with status %s",
        task.id, proof.id, result.status,
    )
