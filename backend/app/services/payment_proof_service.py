"""
Payment Proof business service for Phase E.
Handles upload validation, tenant-isolated object storage, asynchronous verification,
strict matching against invoices and customer identity, idempotency, and Payment creation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import logging
import uuid
from typing import List, Optional, Tuple
from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.invoice import Invoice
from backend.app.models.payment import Payment
from backend.app.models.payment_proof import PaymentProof
from backend.app.models.task import Task
from backend.app.services.customer_identity import resolve_customer_identity
from backend.app.services.manual_payment_service import (
    derive_invoice_payment_status,
    _validate_amount,
    _validate_payment_date,
)
from backend.app.services.payment_import_service import _normalize_reference_for_invoice_match
from backend.app.services.payment_proof_parser import PaymentProofParser
from backend.app.services.storage import get_storage
from backend.app.services.task_service import create_task

logger = logging.getLogger(__name__)

# This is an operational evidence score, not a calibrated probability.  It is
# derived by the parser from the presence of invoice reference (+0.20), amount
# (+0.20), and payment date (+0.10) on a 0.50 base; structured single-row
# CSV/XLSX evidence yields 0.90.  Verification additionally checks the
# server-bound target invoice, customer consistency, date and amount.
PROOF_VERIFICATION_CONFIDENCE_THRESHOLD = 0.85

ALLOWED_PROOF_EXTENSIONS = {".pdf", ".csv", ".xlsx"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/octet-stream",
    "text/plain",
}


def validate_proof_file(content: bytes, filename: str, content_type: str = "") -> None:
    """Validate uploaded proof file format, size, and header signature."""
    if not content or len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded payment proof file is empty.",
        )

    # Size limit
    max_bytes = settings.MAX_INVOICE_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File size ({len(content) / (1024 * 1024):.2f} MB) exceeds "
                f"maximum allowed limit of {settings.MAX_INVOICE_FILE_SIZE_MB} MB."
            ),
        )

    # Extension check
    dot_idx = filename.rfind(".")
    if dot_idx == -1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Accepted extensions are .pdf, .csv, and .xlsx.",
        )
    ext = filename[dot_idx:].lower()
    if ext not in ALLOWED_PROOF_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid file extension '{ext}'. Accepted formats are PDF, CSV, and XLSX."
            ),
        )

    # Magic byte check for PDF
    if ext == ".pdf":
        if b"%PDF" not in content[:1024]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid PDF file: Missing standard '%PDF' header signature.",
            )
    # Magic byte check for XLSX
    elif ext == ".xlsx":
        if len(content) < 4 or content[:4] != b"PK\x03\x04":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid XLSX file: Corrupted or missing standard zip archive signature.",
            )


def upload_payment_proof(
    db: Session,
    *,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
    file_content: bytes,
    original_filename: str,
    content_type: str = "",
    commit: bool = True,
) -> Tuple[PaymentProof, Task]:
    """Store proof in object storage, create PaymentProof and Task records."""
    # 1. Validate file format and integrity
    validate_proof_file(file_content, original_filename, content_type)

    # 2. Check invoice existence and tenant boundary
    invoice = db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == business_id,
        )
    )
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target invoice not found in this business tenant.",
        )

    proof_id = uuid.uuid4()
    dot_idx = original_filename.rfind(".")
    ext = original_filename[dot_idx:].lower() if dot_idx != -1 else ".pdf"
    storage_key = f"tenants/{business_id}/payment_proofs/{proof_id}{ext}"

    # 3. Store file safely via storage abstraction
    storage = get_storage()
    try:
        saved_key = storage.save(file_content, storage_key)
    except Exception as e:
        logger.error("Failed to store payment proof %s: %s", proof_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store payment proof in object storage.",
        ) from e

    # 4. Create database records
    proof = PaymentProof(
        id=proof_id,
        business_id=business_id,
        invoice_id=invoice_id,
        storage_key=saved_key,
        original_filename=original_filename,
        content_type=content_type or "application/octet-stream",
        file_size=len(file_content),
        file_hash=hashlib.sha256(file_content).hexdigest(),
        status="PENDING",
    )
    db.add(proof)
    db.flush()

    task = create_task(
        db=db,
        business_id=business_id,
        task_type="parse_payment_proof",
        payload={
            "proof_id": str(proof.id),
            "invoice_id": str(invoice.id),
            "business_id": str(business_id),
        },
        invoice_id=invoice.id,
        commit=False,
    )
    proof.task_id = task.id
    db.flush()

    if commit:
        db.commit()
        db.refresh(proof)
        db.refresh(task)

    logger.info(
        "Payment proof registered: proof_id=%s, invoice_id=%s, task_id=%s",
        proof.id, invoice.id, task.id,
    )
    return proof, task


def verify_and_process_proof(
    db: Session,
    *,
    proof_id: uuid.UUID,
) -> PaymentProof:
    """Execute proof extraction, validation, matching, and payment creation."""
    proof = db.get(PaymentProof, proof_id)
    if not proof:
        raise LookupError(f"Payment proof record {proof_id} not found.")

    # Idempotency: if already VERIFIED with payment, nothing to do
    if proof.status == "VERIFIED" and proof.payment_id is not None:
        logger.info("Payment proof %s already VERIFIED. Skipping duplicate execution.", proof_id)
        return proof

    # Transition to PROCESSING
    proof.status = "PROCESSING"
    proof.error_message = None
    db.commit()

    # Retrieve target invoice
    invoice = db.scalar(
        select(Invoice).where(
            Invoice.id == proof.invoice_id,
            Invoice.business_id == proof.business_id,
        )
    )
    if not invoice:
        proof.status = "FAILED"
        proof.error_message = "Associated invoice was not found or tenant mismatch."
        db.commit()
        return proof

    # Read storage bytes
    storage = get_storage()
    try:
        file_bytes = storage.read(proof.storage_key)
    except Exception as e:
        logger.error("Could not read proof storage key %s: %s", proof.storage_key, e)
        proof.status = "FAILED"
        proof.error_message = "Proof file could not be read from document storage."
        db.commit()
        return proof

    # Parse proof
    try:
        extracted = PaymentProofParser.parse(
            file_bytes=file_bytes,
            filename=proof.original_filename,
            content_type=proof.content_type,
        )
    except Exception as e:
        logger.warning("Proof parsing failed for %s: %s", proof.id, e)
        proof.status = "FAILED"
        proof.error_message = f"Failed to extract text from payment proof: {e}"
        db.commit()
        return proof

    # Store candidate extracted data
    proof.extracted_data = {
        "invoice_reference": extracted.invoice_reference,
        "payment_date": extracted.payment_date.isoformat() if extracted.payment_date else None,
        "amount": float(extracted.amount) if extracted.amount is not None else None,
        "customer_name": extracted.customer_name,
        "customer_gstin": extracted.customer_gstin,
        "payment_reference": extracted.payment_reference,
        "confidence": extracted.confidence,
        "candidate_count": extracted.candidate_count,
        "extraction_method": extracted.extraction_method,
    }

    # Validation: Amount
    if extracted.amount is None:
        proof.status = "FAILED"
        proof.error_message = "Could not extract a valid payment amount from payment proof."
        db.commit()
        return proof

    try:
        validated_amount = _validate_amount(extracted.amount)
    except ValueError as exc:
        proof.status = "FAILED"
        proof.error_message = f"Invalid payment amount extracted: {exc}"
        db.commit()
        return proof

    # Validation: Date
    if extracted.payment_date is None:
        proof.status = "FAILED"
        proof.error_message = "Could not extract a valid payment date from payment proof."
        db.commit()
        return proof

    try:
        validated_date = _validate_payment_date(extracted.payment_date)
    except ValueError as exc:
        proof.status = "NEEDS_REVIEW"
        proof.error_message = f"Payment date requires review: {exc}"
        db.commit()
        return proof

    # Match Validation: Invoice Reference (if present in proof)
    if extracted.invoice_reference:
        normalized_extracted = _normalize_reference_for_invoice_match(extracted.invoice_reference)
        normalized_target = _normalize_reference_for_invoice_match(invoice.invoice_number)
        if normalized_extracted != normalized_target:
            # Conflicting invoice number -> NEEDS_REVIEW, do not guess
            proof.status = "NEEDS_REVIEW"
            proof.error_message = (
                f"Proof contains invoice reference '{extracted.invoice_reference}' "
                f"which does not match target invoice '{invoice.invoice_number}'."
            )
            db.commit()
            logger.warning(
                "Proof %s invoice mismatch: extracted '%s' vs target '%s'",
                proof.id, extracted.invoice_reference, invoice.invoice_number,
            )
            return proof

    # Match Validation: Customer Identity (if present in proof)
    if extracted.customer_gstin or extracted.customer_name:
        identity_res = resolve_customer_identity(
            db,
            business_id=proof.business_id,
            gstin=extracted.customer_gstin,
            display_name=extracted.customer_name,
        )
        if identity_res.customer is not None and invoice.customer_id is not None:
            if identity_res.customer.id != invoice.customer_id:
                proof.status = "NEEDS_REVIEW"
                proof.error_message = (
                    f"Extracted customer '{extracted.customer_name or extracted.customer_gstin}' "
                    f"conflicts with target invoice's customer."
                )
                db.commit()
                logger.warning(
                    "Proof %s customer mismatch: resolved %s vs invoice customer %s",
                    proof.id, identity_res.customer.id, invoice.customer_id,
                )
                return proof

    # The server route's invoice_id is authoritative.  Extracted information
    # corroborates that target; it is never used to locate or reassign another
    # invoice.  More than one parsed candidate is ambiguous by definition.
    if extracted.candidate_count != 1:
        proof.status = "NEEDS_REVIEW"
        proof.error_message = "Payment proof contains multiple candidate payment records."
        db.commit()
        return proof

    if extracted.confidence < PROOF_VERIFICATION_CONFIDENCE_THRESHOLD:
        proof.status = "NEEDS_REVIEW"
        proof.error_message = (
            "Payment proof evidence confidence is below the verification threshold."
        )
        db.commit()
        return proof

    # Build customer_identity_key
    customer_identity_key: str | None = None
    if invoice.customer_id is not None:
        customer_identity_key = f"customer:{invoice.customer_id}"

    # Idempotency / Duplicate Check against uq_payment_natural_key
    # Check if a matching payment already exists in database
    existing_payment = db.scalar(
        select(Payment).where(
            Payment.business_id == proof.business_id,
            Payment.customer_identity_key == customer_identity_key,
            func.lower(func.btrim(func.coalesce(Payment.invoice_reference, "")))
            == (invoice.invoice_number or "").strip().lower(),
            Payment.payment_date == validated_date,
            Payment.amount == validated_amount,
        )
    )
    if existing_payment:
        # Reconciled to existing payment without duplicate creation
        logger.info(
            "Proof %s matched existing payment %s (provenance=%s). Linking without duplicate.",
            proof.id, existing_payment.id, existing_payment.provenance,
        )
        proof.payment_id = existing_payment.id
        proof.status = "VERIFIED"
        proof.error_message = None
        db.commit()
        return proof

    # Payment status has no overpayment/credit-balance state in V1.  Check
    # this only after the natural-key duplicate path, so the same factual
    # payment may be linked to its existing import/manual row.
    already_paid = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.invoice_id == invoice.id,
            Payment.business_id == proof.business_id,
        )
    ) or Decimal("0")
    outstanding = Decimal(str(invoice.amount)) - Decimal(str(already_paid))
    if validated_amount > max(outstanding, Decimal("0")):
        proof.status = "NEEDS_REVIEW"
        proof.error_message = "Payment amount exceeds the target invoice's outstanding balance."
        db.commit()
        return proof

    # Create real Payment row with provenance='proof_verified'
    clean_ref = (extracted.payment_reference or f"PROOF-{proof.id.hex[:8]}").strip()[:128]
    payment = Payment(
        business_id=proof.business_id,
        invoice_id=invoice.id,
        invoice_reference=invoice.invoice_number,
        payment_date=validated_date,
        amount=validated_amount,
        reference=clean_ref,
        customer_identity_key=customer_identity_key,
        provenance="proof_verified",
        note=f"Verified payment proof: {proof.original_filename}"[:512],
    )

    try:
        with db.begin_nested():
            db.add(payment)
            db.flush()
    except IntegrityError as exc:
        db.rollback()
        diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name == "uq_payment_natural_key":
            # Idempotency collision during concurrent creation
            existing = db.scalar(
                select(Payment).where(
                    Payment.business_id == proof.business_id,
                    Payment.customer_identity_key == customer_identity_key,
                    Payment.payment_date == validated_date,
                    Payment.amount == validated_amount,
                )
            )
            if existing:
                proof.payment_id = existing.id
                proof.status = "VERIFIED"
                proof.error_message = None
                db.commit()
                return proof
        proof.status = "FAILED"
        proof.error_message = "Database integrity conflict while recording verified payment."
        db.commit()
        return proof

    # Update factual invoice payment status
    invoice.payment_status = derive_invoice_payment_status(db, invoice)
    proof.payment_id = payment.id
    proof.status = "VERIFIED"
    proof.error_message = None

    db.commit()
    db.refresh(proof)
    db.refresh(payment)
    db.refresh(invoice)

    logger.info(
        "Proof %s successfully verified: created Payment %s (provenance=proof_verified, amount=%s, status=%s)",
        proof.id, payment.id, validated_amount, invoice.payment_status,
    )
    return proof


def get_proof_by_id(
    db: Session,
    *,
    business_id: uuid.UUID,
    proof_id: uuid.UUID,
) -> Optional[PaymentProof]:
    """Retrieve a single payment proof within authenticated tenant context."""
    return db.scalar(
        select(PaymentProof).where(
            PaymentProof.id == proof_id,
            PaymentProof.business_id == business_id,
        )
    )


def list_proofs_for_invoice(
    db: Session,
    *,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> List[PaymentProof]:
    """List all payment proofs for an invoice within authenticated tenant context."""
    return list(
        db.scalars(
            select(PaymentProof).where(
                PaymentProof.invoice_id == invoice_id,
                PaymentProof.business_id == business_id,
            ).order_by(PaymentProof.created_at.desc())
        ).all()
    )
