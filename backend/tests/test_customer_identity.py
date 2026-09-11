"""Phase A integration tests for tenant-scoped customer identity."""

import unittest
import uuid
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.invoice import Invoice
from backend.app.models.payment import Payment
from backend.app.models.user import User
from backend.app.schemas.invoice import InvoiceResponse
from backend.app.services.customer_identity import (
    create_customer,
    normalize_customer_name,
    normalize_gstin,
    resolve_customer_identity,
    resolve_unresolved_invoice,
)

VALID_GSTIN_A = "27AAPFU0939F1ZV"
VALID_GSTIN_B = "29ABCDE1234F1ZW"
INVALID_CHECKSUM_GSTIN = "27AAPFU0939F1ZU"


class TestCustomerIdentity(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()
        self.business_a = Business(name=f"Identity A {uuid.uuid4().hex[:8]}", currency="INR")
        self.business_b = Business(name=f"Identity B {uuid.uuid4().hex[:8]}", currency="INR")
        self.db.add_all([self.business_a, self.business_b])
        self.db.commit()

    def tearDown(self) -> None:
        try:
            self.db.delete(self.business_a)
            self.db.delete(self.business_b)
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
        finally:
            self.db.close()

    def _invoice(
        self,
        *,
        customer_id: uuid.UUID | None,
        unresolved_customer_name: str | None,
        number: str | None = None,
    ) -> Invoice:
        invoice = Invoice(
            business_id=self.business_a.id,
            customer_id=customer_id,
            unresolved_customer_name=unresolved_customer_name,
            invoice_number=number or f"INV-{uuid.uuid4().hex[:10]}",
            invoice_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
            amount=1000,
            currency="INR",
            payment_status="OPEN",
            processing_status="PROCESSED",
        )
        self.db.add(invoice)
        self.db.flush()
        return invoice

    def test_new_customer_creation_preserves_display_name(self) -> None:
        customer = create_customer(
            self.db,
            business_id=self.business_a.id,
            display_name="  ABC Industries Pvt. Ltd.  ",
            gstin=VALID_GSTIN_A,
        )
        self.db.commit()
        self.assertEqual(customer.display_name, "ABC Industries Pvt. Ltd.")
        self.assertEqual(customer.normalized_name, "abc industries private limited")
        self.assertEqual(customer.normalized_gstin, VALID_GSTIN_A)

    def test_existing_customer_id_has_highest_priority(self) -> None:
        customer = create_customer(
            self.db, business_id=self.business_a.id, display_name="Known Buyer"
        )
        match = resolve_customer_identity(
            self.db,
            business_id=self.business_a.id,
            customer_id=customer.id,
            gstin=VALID_GSTIN_B,
            display_name="Different Text",
        )
        self.assertEqual(match.customer.id, customer.id)
        self.assertEqual(match.matched_by, "customer_id")

    def test_same_valid_gstin_within_tenant_resolves_same_customer(self) -> None:
        customer = create_customer(
            self.db,
            business_id=self.business_a.id,
            display_name="GST Buyer",
            gstin=VALID_GSTIN_A,
        )
        match = resolve_customer_identity(
            self.db,
            business_id=self.business_a.id,
            gstin=VALID_GSTIN_A.lower(),
            display_name="A changed display spelling",
        )
        self.assertEqual(match.customer.id, customer.id)
        self.assertEqual(match.matched_by, "gstin")

    def test_invalid_checksum_gstin_falls_through_to_customer_ref(self) -> None:
        false_gstin_target = create_customer(
            self.db,
            business_id=self.business_a.id,
            display_name="Wrong GSTIN Target",
        )
        # Simulate a legacy value already persisted before checksum validation.
        false_gstin_target.normalized_gstin = INVALID_CHECKSUM_GSTIN
        fallback = create_customer(
            self.db,
            business_id=self.business_a.id,
            display_name="Reference Target",
            customer_ref="REF-CHECKSUM-01",
        )
        self.db.flush()

        match = resolve_customer_identity(
            self.db,
            business_id=self.business_a.id,
            gstin=INVALID_CHECKSUM_GSTIN,
            customer_ref="REF-CHECKSUM-01",
            display_name="Unrelated Extracted Name",
        )

        self.assertIsNone(normalize_gstin(INVALID_CHECKSUM_GSTIN))
        self.assertEqual(match.customer.id, fallback.id)
        self.assertEqual(match.matched_by, "customer_ref")
        self.assertNotEqual(match.customer.id, false_gstin_target.id)

    def test_different_gstins_do_not_merge_even_with_same_name(self) -> None:
        first = create_customer(
            self.db,
            business_id=self.business_a.id,
            display_name="Twin Trading Pvt Ltd",
            gstin=VALID_GSTIN_A,
        )
        second = create_customer(
            self.db,
            business_id=self.business_a.id,
            display_name="Twin Trading Private Limited",
            gstin=VALID_GSTIN_B,
        )
        self.assertNotEqual(first.id, second.id)

    def test_exact_normalized_name_matches_without_gstin(self) -> None:
        customer = create_customer(
            self.db,
            business_id=self.business_a.id,
            display_name="ABC INDUSTRIES PVT. LTD.",
        )
        match = resolve_customer_identity(
            self.db,
            business_id=self.business_a.id,
            display_name="abc industries private limited",
        )
        self.assertEqual(match.customer.id, customer.id)
        self.assertEqual(match.matched_by, "normalized_name")

    def test_similar_non_identical_names_do_not_merge(self) -> None:
        create_customer(
            self.db,
            business_id=self.business_a.id,
            display_name="ABC Industries",
        )
        match = resolve_customer_identity(
            self.db,
            business_id=self.business_a.id,
            display_name="ABC Industrial Supplies",
        )
        self.assertIsNone(match.customer)

    def test_invoice_links_to_resolved_customer(self) -> None:
        customer = create_customer(
            self.db, business_id=self.business_a.id, display_name="Linked Buyer"
        )
        match = resolve_customer_identity(
            self.db, business_id=self.business_a.id, display_name="linked buyer"
        )
        invoice = self._invoice(customer_id=match.customer.id, unresolved_customer_name=None)
        self.db.commit()
        self.assertEqual(self.db.get(Invoice, invoice.id).customer.id, customer.id)

    def test_cross_tenant_matching_is_impossible(self) -> None:
        customer_a = create_customer(
            self.db,
            business_id=self.business_a.id,
            display_name="Shared Buyer",
            gstin=VALID_GSTIN_A,
        )
        match = resolve_customer_identity(
            self.db,
            business_id=self.business_b.id,
            customer_id=customer_a.id,
            gstin=VALID_GSTIN_A,
            display_name="Shared Buyer",
        )
        self.assertIsNone(match.customer)

    def test_unresolved_customer_contract_and_api_shape(self) -> None:
        invoice = self._invoice(
            customer_id=None,
            unresolved_customer_name="Unmatched Buyer Pvt Ltd",
        )
        self.db.commit()
        payload = InvoiceResponse.model_validate(invoice)
        self.assertIsNone(payload.customer_id)
        self.assertEqual(payload.unresolved_customer_name, "Unmatched Buyer Pvt Ltd")

    def test_no_customer_information_contract_and_api_shape(self) -> None:
        invoice = self._invoice(customer_id=None, unresolved_customer_name=None)
        self.db.commit()
        payload = InvoiceResponse.model_validate(invoice)
        self.assertIsNone(payload.customer_id)
        self.assertIsNone(payload.unresolved_customer_name)

    def test_later_resolution_sets_customer_and_clears_name(self) -> None:
        invoice = self._invoice(customer_id=None, unresolved_customer_name="Later Buyer")
        customer = create_customer(
            self.db, business_id=self.business_a.id, display_name="Later Buyer"
        )
        resolved = resolve_unresolved_invoice(
            self.db,
            business_id=self.business_a.id,
            invoice_id=invoice.id,
            customer_id=customer.id,
        )
        self.db.commit()
        self.assertEqual(resolved.customer_id, customer.id)
        self.assertIsNone(resolved.unresolved_customer_name)

    def test_payment_reaches_customer_only_through_invoice(self) -> None:
        customer = create_customer(
            self.db, business_id=self.business_a.id, display_name="Payment Buyer"
        )
        invoice = self._invoice(customer_id=customer.id, unresolved_customer_name=None)
        payment = Payment(
            business_id=self.business_a.id,
            invoice_id=invoice.id,
            invoice_reference=invoice.invoice_number,
            payment_date=datetime.now(timezone.utc),
            amount=1000,
            provenance="legacy",
        )
        self.db.add(payment)
        self.db.commit()
        loaded = self.db.scalar(select(Payment).where(Payment.id == payment.id))
        self.assertEqual(loaded.invoice.customer.id, customer.id)
        self.assertFalse(hasattr(loaded, "customer_id"))

    def test_normalizers_reject_invalid_gstin_without_losing_name_meaning(self) -> None:
        self.assertEqual(normalize_customer_name(" A-B & Sons "), "a-b & sons")
        self.assertIsNone(normalize_gstin("GSTIN NOT AVAILABLE"))


class TestCustomerIdentityApi(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        suffix = uuid.uuid4().hex[:8]
        response = self.client.post(
            "/auth/register",
            json={
                "email": f"customer_api_{suffix}@example.com",
                "password": "Password123!",
                "full_name": "Customer API Tester",
                "business_name": f"Customer API {suffix}",
            },
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.token = body["access_token"]
        self.business_id = uuid.UUID(body["business"]["id"])
        self.user_id = uuid.UUID(body["user"]["id"])

    def tearDown(self) -> None:
        try:
            business = self.db.get(Business, self.business_id)
            if business:
                self.db.delete(business)
            user = self.db.get(User, self.user_id)
            if user:
                self.db.delete(user)
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
        finally:
            self.db.close()

    def test_customer_create_and_read_api_returns_identity_fields(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        created = self.client.post(
            "/customers",
            headers=headers,
            json={
                "display_name": "API Buyer Pvt. Ltd.",
                "gstin": VALID_GSTIN_A,
                "customer_ref": "API-001",
            },
        )
        self.assertEqual(created.status_code, 201)
        payload = created.json()
        self.assertEqual(payload["business_id"], str(self.business_id))
        self.assertEqual(payload["display_name"], "API Buyer Pvt. Ltd.")
        self.assertEqual(payload["normalized_name"], "api buyer private limited")
        self.assertEqual(payload["gstin"], VALID_GSTIN_A)

        read = self.client.get(
            f"/customers/{payload['id']}",
            headers=headers,
        )
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["id"], payload["id"])


if __name__ == "__main__":
    unittest.main()
