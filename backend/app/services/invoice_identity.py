"""Resolve an invoice's seller and buyer against the authenticated tenant.

The registered business is the tenant and the issuing business. Customers are
buyer records owned by that tenant. A selected company is a candidate that
must agree with the extracted buyer; it is never a fallback when extraction
disagrees or is missing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import String, cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.payment import Payment
from backend.app.models.task import Task
from backend.app.services.customer_identity import (
    create_customer,
    normalize_customer_name,
    normalize_gstin,
    resolve_customer_identity,
)

# Names at or above this confidence may be associated automatically.
MIN_CONFIDENT = 0.75
# Similar, non-equal names are sent to review. They are never merged.
SIMILAR_REVIEW = 0.92
AMBIGUITY_MARGIN = 0.05
_LEGAL_SUFFIXES = frozenset({
    "private limited",
    "limited",
    "llp",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "ltd",
})


@dataclass(frozen=True)
class InvoiceIdentityDecision:
    """Canonical result of seller/buyer resolution for one invoice."""

    customer: Customer | None
    matched_by: str | None
    status: str
    seller_name: str | None
    buyer_name: str | None
    seller_confidence: float
    buyer_confidence: float
    review_reason: str | None = None
    selected_customer_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "seller_name": self.seller_name,
            "buyer_name": self.buyer_name,
            "seller_confidence": round(self.seller_confidence, 3),
            "buyer_confidence": round(self.buyer_confidence, 3),
            "match_status": self.status,
            "matched_by": self.matched_by,
            "review_reason": self.review_reason,
            "matched_customer_id": str(self.customer.id) if self.customer else None,
            "selected_customer_id": str(self.selected_customer_id) if self.selected_customer_id else None,
        }


def evidence_confidence(field_confidence: dict | None, key: str, value: str | None) -> float:
    """Use a recorded score. Explicit names without a score stay usable for older fixtures."""
    if not value:
        return 0.0
    recorded = field_confidence or {}
    if key in recorded:
        return float(recorded[key])
    return 0.9


def same_organization(left: str | None, right: str | None) -> bool:
    """Exact normalized equality, plus a legal-suffix difference such as 'Pvt Ltd'."""
    if not left or not right:
        return False
    if left == right:
        return True
    longer, shorter = (left, right) if len(left) >= len(right) else (right, left)
    prefix = shorter + " "
    if not longer.startswith(prefix):
        return False
    return longer[len(prefix):].strip() in _LEGAL_SUFFIXES


def _review(
    *,
    seller_name: str | None,
    buyer_name: str | None,
    seller_confidence: float,
    buyer_confidence: float,
    reason: str,
    selected_customer_id: uuid.UUID | None = None,
) -> InvoiceIdentityDecision:
    return InvoiceIdentityDecision(
        customer=None,
        matched_by=None,
        status="review",
        seller_name=seller_name,
        buyer_name=buyer_name,
        seller_confidence=seller_confidence,
        buyer_confidence=buyer_confidence,
        review_reason=reason,
        selected_customer_id=selected_customer_id,
    )


def _seller_conflict(
    business_name: str | None,
    seller_name: str | None,
    seller_confidence: float,
) -> str | None:
    """Return a review reason when a confident seller is not the registered business."""
    if not seller_name or seller_confidence < MIN_CONFIDENT:
        return None
    seller_norm = normalize_customer_name(seller_name)
    business_norm = normalize_customer_name(business_name)
    if not seller_norm or not business_norm:
        return "Seller name could not be compared with the registered business."
    if same_organization(seller_norm, business_norm):
        return None
    ratio = SequenceMatcher(None, seller_norm, business_norm).ratio()
    if ratio >= SIMILAR_REVIEW:
        return (
            "Seller name is similar to the registered business but is not an exact match. "
            "The invoice was held for review."
        )
    return (
        "Seller does not match the registered business. "
        "The invoice was not assigned to another company."
    )


def _names_conflict_with_gstin(
    left_name: str | None,
    left_gstin: str | None,
    right_name: str | None,
    right_gstin: str | None,
) -> bool:
    if left_gstin and right_gstin and left_gstin != right_gstin:
        return True
    if left_gstin and right_gstin and left_gstin == right_gstin:
        return False
    return not same_organization(
        normalize_customer_name(left_name),
        normalize_customer_name(right_name),
    )


def _similar_customers(
    db: Session, business_id: uuid.UUID, normalized_name: str
) -> list[tuple[Customer, float]]:
    customers = list(
        db.scalars(select(Customer).where(Customer.business_id == business_id)).all()
    )
    similar: list[tuple[Customer, float]] = []
    for customer in customers:
        if not customer.normalized_name or customer.normalized_name == normalized_name:
            continue
        ratio = SequenceMatcher(None, normalized_name, customer.normalized_name).ratio()
        if ratio >= SIMILAR_REVIEW:
            similar.append((customer, ratio))
    similar.sort(key=lambda item: item[1], reverse=True)
    return similar


def resolve_invoice_identity(
    db: Session,
    *,
    business_id: uuid.UUID,
    seller_name: str | None = None,
    seller_gstin: str | None = None,
    seller_confidence: float = 0.0,
    buyer_name: str | None = None,
    buyer_gstin: str | None = None,
    buyer_confidence: float = 0.0,
    buyer_ref: str | None = None,
    selected_customer_id: uuid.UUID | None = None,
    allow_create: bool = False,
) -> InvoiceIdentityDecision:
    """Match seller to the tenant and buyer to a customer, or hold the invoice for review."""
    business = db.get(Business, business_id)
    business_name = business.name if business else None
    clean_seller_gstin = normalize_gstin(seller_gstin)
    clean_buyer_gstin = normalize_gstin(buyer_gstin)
    buyer_norm = normalize_customer_name(buyer_name)

    if clean_seller_gstin and clean_buyer_gstin and clean_seller_gstin == clean_buyer_gstin:
        return _review(
            seller_name=seller_name,
            buyer_name=buyer_name,
            seller_confidence=seller_confidence,
            buyer_confidence=buyer_confidence,
            reason="Seller and buyer share one GSTIN, so the parties could not be distinguished.",
            selected_customer_id=selected_customer_id,
        )

    seller_reason = _seller_conflict(business_name, seller_name, seller_confidence)
    if seller_reason:
        return _review(
            seller_name=seller_name,
            buyer_name=buyer_name,
            seller_confidence=seller_confidence,
            buyer_confidence=buyer_confidence,
            reason=seller_reason,
            selected_customer_id=selected_customer_id,
        )

    if (
        buyer_norm
        and buyer_confidence >= MIN_CONFIDENT
        and same_organization(buyer_norm, normalize_customer_name(business_name))
    ):
        return _review(
            seller_name=seller_name,
            buyer_name=buyer_name,
            seller_confidence=seller_confidence,
            buyer_confidence=buyer_confidence,
            reason=(
                "Extracted buyer matches the registered business. "
                "Seller and buyer could not be distinguished, so no company was assigned."
            ),
            selected_customer_id=selected_customer_id,
        )

    selected: Customer | None = None
    if selected_customer_id is not None:
        selected = db.scalar(
            select(Customer).where(
                Customer.id == selected_customer_id,
                Customer.business_id == business_id,
            )
        )
        if selected is None:
            return _review(
                seller_name=seller_name,
                buyer_name=buyer_name,
                seller_confidence=seller_confidence,
                buyer_confidence=buyer_confidence,
                reason="Selected company does not belong to this business.",
                selected_customer_id=selected_customer_id,
            )

    if not buyer_name or buyer_confidence < MIN_CONFIDENT:
        if selected is not None:
            reason = (
                "The invoice did not contain a confident buyer identity, "
                "so it was not attached to the selected company."
            )
        elif allow_create:
            reason = "Buyer identity confidence is too low to associate a company."
        else:
            return InvoiceIdentityDecision(
                customer=None,
                matched_by=None,
                status="unresolved",
                seller_name=seller_name,
                buyer_name=buyer_name,
                seller_confidence=seller_confidence,
                buyer_confidence=buyer_confidence,
                review_reason=None,
                selected_customer_id=selected_customer_id,
            )
        return _review(
            seller_name=seller_name,
            buyer_name=buyer_name,
            seller_confidence=seller_confidence,
            buyer_confidence=buyer_confidence,
            reason=reason,
            selected_customer_id=selected_customer_id,
        )

    # Never pass a caller-supplied company id into the matcher. That id is
    # checked only after the document identity is known.
    matched = resolve_customer_identity(
        db,
        business_id=business_id,
        gstin=buyer_gstin,
        customer_ref=buyer_ref,
        display_name=buyer_name,
    ).customer

    if selected is not None:
        if _names_conflict_with_gstin(
            selected.display_name, selected.normalized_gstin, buyer_name, clean_buyer_gstin
        ):
            return _review(
                seller_name=seller_name,
                buyer_name=buyer_name,
                seller_confidence=seller_confidence,
                buyer_confidence=buyer_confidence,
                reason=(
                    f"Selected company '{selected.display_name}' does not match "
                    f"extracted buyer '{buyer_name}'."
                ),
                selected_customer_id=selected.id,
            )
        if matched is not None and matched.id != selected.id:
            return _review(
                seller_name=seller_name,
                buyer_name=buyer_name,
                seller_confidence=seller_confidence,
                buyer_confidence=buyer_confidence,
                reason=(
                    f"Selected company '{selected.display_name}' conflicts with "
                    f"matched customer '{matched.display_name}'."
                ),
                selected_customer_id=selected.id,
            )
        if clean_buyer_gstin and not selected.normalized_gstin:
            selected.gstin = buyer_gstin.strip() if buyer_gstin else clean_buyer_gstin
        return InvoiceIdentityDecision(
            customer=selected,
            matched_by="selected_company",
            status="matched",
            seller_name=seller_name,
            buyer_name=buyer_name,
            seller_confidence=seller_confidence,
            buyer_confidence=buyer_confidence,
            selected_customer_id=selected.id,
        )

    if buyer_norm and matched is None:
        similar = _similar_customers(db, business_id, buyer_norm)
        if similar:
            nearest = similar[0][1]
            ambiguous = len(similar) > 1 and nearest - similar[1][1] < AMBIGUITY_MARGIN
            detail = "several customers look similar" if ambiguous or len(similar) > 1 else (
                f"'{similar[0][0].display_name}' looks similar but is not the same company"
            )
            return _review(
                seller_name=seller_name,
                buyer_name=buyer_name,
                seller_confidence=seller_confidence,
                buyer_confidence=buyer_confidence,
                reason=(
                    f"Buyer '{buyer_name}' was not linked automatically because {detail}. "
                    "No companies were merged."
                ),
                selected_customer_id=selected_customer_id,
            )

    if matched is not None:
        if clean_buyer_gstin and matched.normalized_gstin and matched.normalized_gstin != clean_buyer_gstin:
            return _review(
                seller_name=seller_name,
                buyer_name=buyer_name,
                seller_confidence=seller_confidence,
                buyer_confidence=buyer_confidence,
                reason="Buyer GSTIN conflicts with the matched customer.",
                selected_customer_id=selected_customer_id,
            )
        return InvoiceIdentityDecision(
            customer=matched,
            matched_by="buyer_identity",
            status="matched",
            seller_name=seller_name,
            buyer_name=buyer_name,
            seller_confidence=seller_confidence,
            buyer_confidence=buyer_confidence,
        )

    if not allow_create:
        return InvoiceIdentityDecision(
            customer=None,
            matched_by=None,
            status="unresolved",
            seller_name=seller_name,
            buyer_name=buyer_name,
            seller_confidence=seller_confidence,
            buyer_confidence=buyer_confidence,
            review_reason=None,
        )

    if not buyer_name or not buyer_norm:
        return _review(
            seller_name=seller_name,
            buyer_name=buyer_name,
            seller_confidence=seller_confidence,
            buyer_confidence=buyer_confidence,
            reason="Buyer name is missing, so no company was created.",
        )

    try:
        with db.begin_nested():
            created = create_customer(
                db,
                business_id=business_id,
                display_name=buyer_name.strip()[:255],
                gstin=buyer_gstin if clean_buyer_gstin else None,
                customer_ref=buyer_ref,
            )
    except IntegrityError:
        created = resolve_customer_identity(
            db,
            business_id=business_id,
            gstin=buyer_gstin,
            customer_ref=buyer_ref,
            display_name=buyer_name,
        ).customer
        if created is None:
            return _review(
                seller_name=seller_name,
                buyer_name=buyer_name,
                seller_confidence=seller_confidence,
                buyer_confidence=buyer_confidence,
                reason="A matching customer was created concurrently and could not be resolved safely.",
            )
        return InvoiceIdentityDecision(
            customer=created,
            matched_by="buyer_identity",
            status="matched",
            seller_name=seller_name,
            buyer_name=buyer_name,
            seller_confidence=seller_confidence,
            buyer_confidence=buyer_confidence,
        )

    return InvoiceIdentityDecision(
        customer=created,
        matched_by="created",
        status="created",
        seller_name=seller_name,
        buyer_name=buyer_name,
        seller_confidence=seller_confidence,
        buyer_confidence=buyer_confidence,
    )


def latest_invoice_identity(db: Session, invoice_id: uuid.UUID) -> dict:
    """Identity summary stored on the parse task. Empty when this invoice predates it."""
    task = db.scalar(
        select(Task)
        .where(Task.invoice_id == invoice_id, Task.task_type == "parse_invoice")
        .order_by(Task.created_at.desc())
    )
    if task is None or not isinstance(task.payload, dict):
        return {}
    identity = task.payload.get("identity")
    return identity if isinstance(identity, dict) else {}


def list_payment_identity_mismatches(db: Session, business_id: uuid.UUID) -> list[Invoice]:
    """Invoices whose linked payment names a different customer. Read-only."""
    rows = db.execute(
        select(Invoice)
        .join(Payment, Payment.invoice_id == Invoice.id)
        .where(
            Invoice.business_id == business_id,
            Invoice.customer_id.is_not(None),
            Payment.business_id == business_id,
            Payment.customer_identity_key.is_not(None),
            Payment.customer_identity_key != func.concat(
                "customer:", cast(Invoice.customer_id, String)
            ),
        )
        .order_by(Invoice.created_at.desc())
    ).scalars().all()
    seen: set[uuid.UUID] = set()
    unique: list[Invoice] = []
    for invoice in rows:
        if invoice.id in seen:
            continue
        seen.add(invoice.id)
        unique.append(invoice)
    return unique


def list_seller_as_customer_invoices(db: Session, business_id: uuid.UUID) -> list[Invoice]:
    """Read-only candidates whose customer name is the registered business.

    These can be seller names that were stored as customers. Nothing is changed.
    """
    business = db.get(Business, business_id)
    if business is None:
        return []
    business_norm = normalize_customer_name(business.name)
    if not business_norm:
        return []
    return list(
        db.scalars(
            select(Invoice)
            .join(Customer, Invoice.customer_id == Customer.id)
            .where(
                Invoice.business_id == business_id,
                Customer.business_id == business_id,
                Customer.normalized_name == business_norm,
            )
            .order_by(Invoice.created_at.desc())
        ).all()
    )
