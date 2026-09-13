"""Tenant-scoped, deterministic customer identity matching."""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.prediction import PredictionResult

GSTIN_PATTERN = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
)
_GSTIN_CHARACTERS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_SPACE_PATTERN = re.compile(r"\s+")
_HARMLESS_PUNCTUATION = re.compile(r"[.,;:()\[\]{}]+")


def normalize_customer_name(value: str | None) -> str | None:
    """Return a conservative comparison key while preserving meaningful text."""
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _HARMLESS_PUNCTUATION.sub(" ", normalized)
    tokens = _SPACE_PATTERN.sub(" ", normalized.strip()).casefold().split()
    if not tokens:
        return None

    # Canonicalize only unambiguous legal-suffix spelling variants at the end.
    if len(tokens) >= 2 and tokens[-2:] in (["pvt", "ltd"], ["pvt", "limited"], ["private", "ltd"]):
        tokens[-2:] = ["private", "limited"]
    return " ".join(tokens)


def normalize_gstin(value: str | None) -> str | None:
    """Normalize a GSTIN and validate both its structure and check digit."""
    if value is None:
        return None
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).upper()
    if not GSTIN_PATTERN.fullmatch(normalized):
        return None

    factor = 1
    total = 0
    for character in normalized[:14]:
        product = _GSTIN_CHARACTERS.index(character) * factor
        total += (product // 36) + (product % 36)
        factor = 2 if factor == 1 else 1
    check_digit = _GSTIN_CHARACTERS[(36 - (total % 36)) % 36]
    return normalized if normalized[-1] == check_digit else None


def _clean_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned[:255] or None


@dataclass(frozen=True)
class CustomerIdentityResult:
    customer: Customer | None
    matched_by: str | None = None


def resolve_customer_identity(
    db: Session,
    *,
    business_id: uuid.UUID,
    customer_id: uuid.UUID | None = None,
    gstin: str | None = None,
    customer_ref: str | None = None,
    display_name: str | None = None,
) -> CustomerIdentityResult:
    """Resolve identity in priority order, never crossing the tenant boundary."""
    if customer_id is not None:
        customer = db.scalar(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.business_id == business_id,
            )
        )
        if customer is not None:
            return CustomerIdentityResult(customer, "customer_id")

    normalized_gstin = normalize_gstin(gstin)
    if normalized_gstin:
        customer = db.scalar(
            select(Customer).where(
                Customer.business_id == business_id,
                Customer.normalized_gstin == normalized_gstin,
            )
        )
        if customer is not None:
            return CustomerIdentityResult(customer, "gstin")

    clean_ref = customer_ref.strip() if customer_ref else None
    if clean_ref:
        customers = list(
            db.scalars(
                select(Customer).where(
                    Customer.business_id == business_id,
                    func.lower(func.btrim(Customer.customer_ref)) == clean_ref.casefold(),
                )
            ).all()
        )
        if len(customers) == 1:
            customer = customers[0]
            if not normalized_gstin or not customer.normalized_gstin or customer.normalized_gstin == normalized_gstin:
                return CustomerIdentityResult(customer, "customer_ref")

    normalized_name = normalize_customer_name(display_name)
    if normalized_name:
        customers = list(
            db.scalars(
                select(Customer).where(
                    Customer.business_id == business_id,
                    Customer.normalized_name == normalized_name,
                )
            ).all()
        )
        compatible = [
            customer for customer in customers
            if not normalized_gstin
            or not customer.normalized_gstin
            or customer.normalized_gstin == normalized_gstin
        ]
        if len(compatible) == 1:
            customer = compatible[0]
            if normalized_gstin and not customer.normalized_gstin:
                customer.gstin = gstin.strip() if gstin else normalized_gstin
                customer.normalized_gstin = normalized_gstin
            return CustomerIdentityResult(customer, "normalized_name")

    return CustomerIdentityResult(None)


def create_customer(
    db: Session,
    *,
    business_id: uuid.UUID,
    display_name: str,
    gstin: str | None = None,
    customer_ref: str | None = None,
) -> Customer:
    """Create an explicitly requested customer without duplicating known identity."""
    clean_name = _clean_display_name(display_name)
    normalized_name = normalize_customer_name(clean_name)
    if not clean_name or not normalized_name:
        raise ValueError("Customer display_name is required.")
    if gstin and normalize_gstin(gstin) is None:
        raise ValueError("GSTIN structure or checksum is invalid.")

    same_name = db.scalar(
        select(Customer).where(
            Customer.business_id == business_id,
            Customer.normalized_name == normalized_name,
        )
    )
    if same_name is not None:
        supplied_gstin = normalize_gstin(gstin)
        if supplied_gstin and same_name.normalized_gstin and supplied_gstin != same_name.normalized_gstin:
            raise ValueError("Company name already exists with a different GSTIN.")
        if supplied_gstin and not same_name.normalized_gstin:
            same_name.gstin = gstin.strip() if gstin else supplied_gstin
        return same_name

    existing = resolve_customer_identity(
        db,
        business_id=business_id,
        gstin=gstin,
        customer_ref=customer_ref,
        display_name=clean_name,
    ).customer
    if existing is not None:
        return existing

    customer = Customer(
        business_id=business_id,
        display_name=clean_name,
        normalized_name=normalized_name,
        gstin=gstin.strip() if gstin else None,
        normalized_gstin=normalize_gstin(gstin),
        customer_ref=customer_ref.strip() if customer_ref else None,
    )
    db.add(customer)
    db.flush()
    return customer


def resolve_or_create_historical_customer(
    db: Session,
    *,
    business_id: uuid.UUID,
    display_name: str | None,
    gstin: str | None = None,
    customer_ref: str | None = None,
    customer_id: uuid.UUID | None = None,
) -> CustomerIdentityResult:
    """Resolve through the canonical matcher or create one defensible customer."""
    result = resolve_customer_identity(
        db,
        business_id=business_id,
        customer_id=customer_id,
        gstin=gstin,
        customer_ref=customer_ref,
        display_name=display_name,
    )
    if result.customer is not None:
        return result

    clean_name = _clean_display_name(display_name)
    normalized_name = normalize_customer_name(clean_name)
    if clean_name is None or normalized_name is None:
        raise ValueError(
            "Cannot import: no resolvable customer identity found for this row. "
            "An invoice reference alone is not sufficient — historical payment "
            "data must map to a concrete customer."
        )

    clean_ref = customer_ref.strip() if customer_ref else None
    if clean_ref:
        ref_candidates = list(
            db.scalars(
                select(Customer).where(
                    Customer.business_id == business_id,
                    func.lower(func.btrim(Customer.customer_ref)) == clean_ref.casefold(),
                )
            ).all()
        )
        if ref_candidates:
            raise ValueError(
                "Cannot import: customer reference is ambiguous or conflicts with "
                "existing customer identity. Manual review required."
            )

    valid_gstin = normalize_gstin(gstin)
    name_candidates = list(
        db.scalars(
            select(Customer).where(
                Customer.business_id == business_id,
                Customer.normalized_name == normalized_name,
            )
        ).all()
    )
    compatible_candidates = [
        customer
        for customer in name_candidates
        if not valid_gstin
        or not customer.normalized_gstin
        or customer.normalized_gstin == valid_gstin
    ]
    if compatible_candidates:
        raise ValueError(
            "Cannot import: customer identity is ambiguous. Manual review required."
        )

    customer = create_customer(
        db,
        business_id=business_id,
        display_name=clean_name,
        gstin=gstin if valid_gstin else None,
        customer_ref=clean_ref,
    )
    return CustomerIdentityResult(customer, "created")


def resolve_unresolved_invoice(
    db: Session,
    *,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
    customer_id: uuid.UUID,
    recalculate_prediction: bool = True,
) -> Invoice:
    """Apply a later confirmed match, clear the unresolved display value, and update predictions."""
    invoice = db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == business_id,
        )
    )
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.business_id == business_id,
        )
    )
    if invoice is None or customer is None:
        raise ValueError("Invoice and customer must exist in the same tenant.")
    invoice.customer_id = customer.id
    invoice.unresolved_customer_name = None

    # Invalidate any stale prediction for this invoice
    db.execute(
        delete(PredictionResult).where(
            PredictionResult.business_id == business_id,
            PredictionResult.invoice_id == invoice_id,
        )
    )
    db.flush()

    if recalculate_prediction:
        from backend.app.services.prediction_service import (
            evaluate_prediction_eligibility,
            predict_for_invoice,
        )
        eligibility = evaluate_prediction_eligibility(db, invoice)
        if (
            eligibility.prediction_available
            and invoice.processing_status == "PROCESSED"
            and invoice.amount
            and float(invoice.amount) > 0
            and invoice.invoice_date
            and invoice.due_date
        ):
            try:
                predict_for_invoice(db, invoice_id, business_id)
            except Exception:
                pass

    return invoice


def correct_invoice_customer(
    db: Session,
    *,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
    replacement_customer_id: uuid.UUID | None = None,
    replacement_company_name: str | None = None,
    create_if_missing: bool = False,
    gstin: str | None = None,
    recalculate_prediction: bool = True,
) -> Invoice:
    """
    Correct or reassign the customer associated with an invoice (CURRENT or HISTORICAL).
    - Uses normalized & case-insensitive matching.
    - Invalidates any stale prediction for this invoice.
    - If recalculate_prediction is True, recalculates prediction eligibility for the corrected customer
      and executes genuine XGBoost inference if eligible.
    - Preserves origin strictly (CURRENT remains CURRENT).
    """
    invoice = db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == business_id,
        )
    )
    if invoice is None:
        raise LookupError(f"Invoice {invoice_id} not found.")

    target_customer: Customer | None = None
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

    invoice.customer_id = target_customer.id
    invoice.unresolved_customer_name = None

    # Invalidate any stale prediction for this invoice
    db.execute(
        delete(PredictionResult).where(
            PredictionResult.business_id == business_id,
            PredictionResult.invoice_id == invoice_id,
        )
    )
    db.flush()

    if recalculate_prediction:
        from backend.app.services.prediction_service import (
            evaluate_prediction_eligibility,
            predict_for_invoice,
        )
        eligibility = evaluate_prediction_eligibility(db, invoice)
        if (
            eligibility.prediction_available
            and invoice.processing_status == "PROCESSED"
            and invoice.amount
            and float(invoice.amount) > 0
            and invoice.invoice_date
            and invoice.due_date
        ):
            try:
                predict_for_invoice(db, invoice_id, business_id)
            except Exception:
                pass

    db.commit()
    db.refresh(invoice)
    return invoice
