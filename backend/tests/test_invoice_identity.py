"""Regression tests for seller/buyer resolution and upload context."""

from __future__ import annotations

import io
import threading
import unittest
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.models.payment import Payment
from backend.app.models.task import Task
from backend.app.services.customer_identity import create_customer
from backend.app.services.dashboard_service import build_dashboard_summary
from backend.app.services.invoice_identity import resolve_invoice_identity
from backend.app.services.parser.base import ExtractedInvoice, ExtractionResult, PermanentParserError
from backend.app.services.parser.invoice_parser import InvoiceParser
from backend.app.services.storage import get_storage
from backend.app.workers.handlers import handle_parse_invoice


def _pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"


def _extracted(
    number: str,
    buyer: str | None,
    *,
    seller: str | None = None,
    seller_confidence: float = 0.92,
    buyer_confidence: float = 0.95,
    due: date | None = date(2026, 11, 13),
    amount: str = "5000.00",
) -> ExtractedInvoice:
    confidence = {}
    if buyer is not None:
        confidence["customer_name"] = buyer_confidence
    if seller is not None:
        confidence["seller_name"] = seller_confidence
    return ExtractedInvoice(
        invoice_number=number,
        invoice_date=date(2026, 10, 14),
        due_date=due,
        amount=Decimal(amount),
        currency="INR",
        customer_name=buyer,
        seller_name=seller,
        field_confidence=confidence,
    )


class TestInvoiceIdentity(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        self.storage = get_storage()
        self.uploaded_keys: list[str] = []
        self.business_a_id, self.token_a, self.business_a_name = self._register("north")
        self.business_b_id, self.token_b, self.business_b_name = self._register("south")

    def tearDown(self):
        for key in self.uploaded_keys:
            try:
                self.storage.delete(key)
            except Exception:
                pass
        try:
            for business_id in (self.business_a_id, self.business_b_id):
                business = self.db.get(Business, business_id)
                if business is not None:
                    self.db.delete(business)
            self.db.commit()
        except Exception:
            self.db.rollback()
        finally:
            self.db.close()

    def _register(self, slug: str) -> tuple[uuid.UUID, str, str]:
        suffix = uuid.uuid4().hex[:8]
        name = f"{slug.title()} Fabrication Pvt Ltd {suffix}"
        response = self.client.post("/auth/register", json={
            "email": f"{slug}_{suffix}@example.com",
            "password": "Password123!",
            "full_name": f"{slug} owner",
            "business_name": name,
        })
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        return uuid.UUID(payload["business"]["id"]), payload["access_token"], name

    def _parse(self, token: str, extracted: ExtractedInvoice, *, historical: bool = False, customer_id: uuid.UUID | None = None) -> Invoice:
        if historical and customer_id is not None:
            path = f"/historical/companies/{customer_id}/invoices/upload"
        elif historical:
            path = "/historical/invoices/upload"
        else:
            path = "/invoices/upload"
        with patch(
            "backend.app.workers.handlers.InvoiceParser.parse",
            return_value=ExtractionResult(True, invoice=extracted),
        ):
            response = self.client.post(
                path,
                files={"file": (f"{extracted.invoice_number}.pdf", io.BytesIO(_pdf()), "application/pdf")},
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            task_id = uuid.UUID(response.json()["task_id"])
            task = self.db.get(Task, task_id)
            document = self.db.get(InvoiceDocument, uuid.UUID(response.json()["document_id"]))
            self.uploaded_keys.append(document.storage_key)
            handle_parse_invoice(self.db, task, dict(task.payload or {}))
        self.db.expire_all()
        invoice = self.db.scalar(select(Invoice).where(
            Invoice.business_id == self.business_a_id,
            Invoice.invoice_number == extracted.invoice_number,
        ))
        self.assertIsNotNone(invoice)
        return invoice

    def _customer(self, business_id: uuid.UUID, name: str) -> Customer:
        customer = create_customer(self.db, business_id=business_id, display_name=name)
        self.db.commit()
        return customer

    def test_seller_and_buyer_are_extracted_separately(self):
        text = (
            "TAX INVOICE\n"
            "Seller: Northwind Fabrication Pvt Ltd\n"
            "Invoice No: INV-SPLIT-1\n"
            "Invoice Date: 2026-10-14\n"
            "Due Date: 2026-11-13\n"
            "Bill To: Customer X Traders\n"
            "Total Amount: INR 5,000.00\n"
        )
        invoice, missing = InvoiceParser._extract_fields(text, "text")
        self.assertEqual(missing, [])
        self.assertEqual(invoice.customer_name, "Customer X Traders")
        self.assertEqual(invoice.seller_name, "Northwind Fabrication Pvt Ltd")
        self.assertGreaterEqual(invoice.field_confidence["seller_name"], 0.75)
        self.assertGreaterEqual(invoice.field_confidence["customer_name"], 0.75)
        self.assertNotEqual(invoice.seller_name, invoice.customer_name)

    def test_registered_seller_and_buyer_associate_with_tenant_and_customer(self):
        buyer = self._customer(self.business_a_id, "Customer X Traders")
        invoice = self._parse(self.token_a, _extracted(
            "INV-AX-1", "Customer X Traders", seller=self.business_a_name,
        ))
        self.assertEqual(invoice.business_id, self.business_a_id)
        self.assertEqual(invoice.customer_id, buyer.id)
        self.assertEqual(invoice.processing_status, "PROCESSED")
        self.assertIsNone(invoice.unresolved_customer_name)

    def test_later_buyer_is_not_attached_to_the_earlier_customer(self):
        first = self._customer(self.business_a_id, "Customer X Traders")
        second = self._customer(self.business_a_id, "Customer Y Traders")
        self._parse(self.token_a, _extracted("INV-AX-2", "Customer X Traders", seller=self.business_a_name))
        later = self._parse(self.token_a, _extracted("INV-AY-2", "Customer Y Traders", seller=self.business_a_name))
        self.assertEqual(later.customer_id, second.id)
        self.assertNotEqual(later.customer_id, first.id)

    def test_customers_match_by_identity_not_insert_order(self):
        zulu = self._customer(self.business_a_id, "Zulu Components")
        acme = self._customer(self.business_a_id, "Acme Components")
        invoice = self._parse(self.token_a, _extracted("INV-ORDER-1", "Acme Components", seller=self.business_a_name))
        self.assertNotEqual(invoice.customer_id, zulu.id)
        self.assertEqual(invoice.customer_id, acme.id)

    def test_new_historical_buyer_is_created_under_the_tenant(self):
        invoice = self._parse(
            self.token_a,
            _extracted("INV-NEW-1", "Fresh Buyer Industries", seller=self.business_a_name),
            historical=True,
        )
        customer = self.db.get(Customer, invoice.customer_id)
        self.assertEqual(customer.business_id, self.business_a_id)
        self.assertEqual(customer.display_name, "Fresh Buyer Industries")
        self.assertTrue(customer.has_historical_context)
        current = resolve_invoice_identity(
            self.db,
            business_id=self.business_a_id,
            seller_name=self.business_a_name,
            seller_confidence=0.92,
            buyer_name="Another Unsaved Buyer",
            buyer_confidence=0.95,
            allow_create=False,
        )
        self.assertIsNone(current.customer)
        self.assertEqual(current.status, "unresolved")

    def test_selected_company_is_accepted_only_when_it_matches_the_buyer(self):
        selected = self._customer(self.business_a_id, "Matched Workshop")
        invoice = self._parse(
            self.token_a,
            _extracted("INV-SEL-OK", "Matched Workshop", seller=self.business_a_name),
            historical=True,
            customer_id=selected.id,
        )
        self.assertEqual(invoice.customer_id, selected.id)
        self.assertEqual(invoice.processing_status, "PROCESSED")

    def test_selected_company_conflict_is_held_for_review(self):
        selected = self._customer(self.business_a_id, "Company Alpha")
        other = self._customer(self.business_a_id, "Company Beta")
        invoice = self._parse(
            self.token_a,
            _extracted("INV-SEL-BAD", "Company Beta", seller=self.business_a_name),
            historical=True,
            customer_id=selected.id,
        )
        self.assertIsNone(invoice.customer_id)
        self.assertEqual(invoice.processing_status, "NEEDS_REVIEW")
        self.assertNotEqual(invoice.customer_id, selected.id)
        self.assertNotEqual(invoice.customer_id, other.id)
        self.assertIn("does not match", invoice.document.error_message)
        task = self.db.scalar(select(Task).where(Task.invoice_id == invoice.id))
        identity = (task.payload or {}).get("identity") or {}
        self.assertEqual(identity.get("buyer_name"), "Company Beta")
        self.assertEqual(identity.get("match_status"), "review")

    def test_current_upload_does_not_inherit_historical_company(self):
        historical_company = self._customer(self.business_a_id, "Historical Only Co")
        historical = self._parse(
            self.token_a,
            _extracted("INV-HIST-CTX", "Historical Only Co", seller=self.business_a_name),
            historical=True,
            customer_id=historical_company.id,
        )
        self.assertEqual(historical.customer_id, historical_company.id)
        current_buyer = self._customer(self.business_a_id, "Operational Buyer")
        response = self.client.post(
            "/invoices/upload",
            files={"file": ("current.pdf", io.BytesIO(_pdf()), "application/pdf")},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        task = self.db.get(Task, uuid.UUID(response.json()["task_id"]))
        self.assertNotIn("customer_id", task.payload or {})
        document = self.db.get(InvoiceDocument, uuid.UUID(response.json()["document_id"]))
        self.uploaded_keys.append(document.storage_key)
        extracted = _extracted("INV-CURRENT-AFTER", "Operational Buyer", seller=self.business_a_name)
        with patch(
            "backend.app.workers.handlers.InvoiceParser.parse",
            return_value=ExtractionResult(True, invoice=extracted),
        ):
            handle_parse_invoice(self.db, task, dict(task.payload or {}))
        self.db.expire_all()
        invoice = self.db.scalar(select(Invoice).where(Invoice.invoice_number == "INV-CURRENT-AFTER"))
        self.assertEqual(invoice.customer_id, current_buyer.id)
        self.assertNotEqual(invoice.customer_id, historical_company.id)
        self.assertEqual(invoice.origin, InvoiceOrigin.CURRENT.value)

    def test_switching_selected_companies_does_not_reuse_the_previous_choice(self):
        first = self._customer(self.business_a_id, "Selected One")
        second = self._customer(self.business_a_id, "Selected Two")
        first_invoice = self._parse(
            self.token_a,
            _extracted("INV-SWITCH-1", "Selected One", seller=self.business_a_name),
            historical=True,
            customer_id=first.id,
        )
        second_invoice = self._parse(
            self.token_a,
            _extracted("INV-SWITCH-2", "Selected Two", seller=self.business_a_name),
            historical=True,
            customer_id=second.id,
        )
        self.assertEqual(first_invoice.customer_id, first.id)
        self.assertEqual(second_invoice.customer_id, second.id)

    def test_third_buyer_is_not_assigned_to_the_first_customer(self):
        first = self._customer(self.business_a_id, "Buyer One")
        second = self._customer(self.business_a_id, "Buyer Two")
        invoice = self._parse(
            self.token_a,
            _extracted("INV-THIRD", "Buyer Three", seller=self.business_a_name),
            historical=True,
        )
        self.assertNotEqual(invoice.customer_id, first.id)
        self.assertNotEqual(invoice.customer_id, second.id)
        created = self.db.get(Customer, invoice.customer_id)
        self.assertEqual(created.display_name, "Buyer Three")
        self.assertEqual(created.business_id, self.business_a_id)

    def test_same_name_in_another_tenant_is_not_matched(self):
        foreign = self._customer(self.business_b_id, "Shared Buyer Ltd")
        invoice = self._parse(
            self.token_a,
            _extracted("INV-TENANT", "Shared Buyer Ltd", seller=self.business_a_name),
            historical=True,
        )
        self.assertNotEqual(invoice.customer_id, foreign.id)
        local = self.db.get(Customer, invoice.customer_id)
        self.assertEqual(local.business_id, self.business_a_id)
        self.assertEqual(foreign.business_id, self.business_b_id)

    def test_similar_names_are_not_merged(self):
        existing = self._customer(self.business_a_id, "Harbor Logistics")
        invoice = self._parse(
            self.token_a,
            _extracted("INV-SIMILAR", "Harbor Logistic", seller=self.business_a_name),
            historical=True,
        )
        self.assertIsNone(invoice.customer_id)
        self.assertEqual(invoice.processing_status, "NEEDS_REVIEW")
        self.assertNotEqual(invoice.customer_id, existing.id)
        count = self.db.scalar(select(func.count(Customer.id)).where(
            Customer.business_id == self.business_a_id,
            Customer.display_name == "Harbor Logistic",
        ))
        self.assertEqual(count, 0)
        self.assertIn("similar", (invoice.document.error_message or "").lower())

    def test_low_confidence_buyer_is_not_associated(self):
        existing = self._customer(self.business_a_id, "Precise Buyers")
        invoice = self._parse(
            self.token_a,
            _extracted(
                "INV-LOW", "Precise Buyers", seller=self.business_a_name, buyer_confidence=0.4,
            ),
            historical=True,
        )
        self.assertIsNone(invoice.customer_id)
        self.assertNotEqual(invoice.customer_id, existing.id)
        self.assertEqual(invoice.processing_status, "NEEDS_REVIEW")

    def test_foreign_seller_is_not_assigned_to_a_customer(self):
        buyer = self._customer(self.business_a_id, "Customer X Traders")
        invoice = self._parse(self.token_a, _extracted(
            "INV-FOREIGN", "Customer X Traders", seller="Unrelated Exports Pvt Ltd",
        ))
        self.assertIsNone(invoice.customer_id)
        self.assertNotEqual(invoice.customer_id, buyer.id)
        self.assertEqual(invoice.processing_status, "NEEDS_REVIEW")
        self.assertIn("registered business", invoice.document.error_message.lower())

    def test_concurrent_duplicate_invoice_numbers_converge(self):
        extracted = _extracted("INV-RACE-1", "Race Buyer", seller=self.business_a_name)
        task_ids = []
        for index in range(2):
            response = self.client.post(
                "/historical/invoices/upload",
                files={"file": (f"race-{index}.pdf", io.BytesIO(_pdf()), "application/pdf")},
                headers={"Authorization": f"Bearer {self.token_a}"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            document = self.db.get(InvoiceDocument, uuid.UUID(response.json()["document_id"]))
            self.uploaded_keys.append(document.storage_key)
            task_ids.append(uuid.UUID(response.json()["task_id"]))
        self.db.commit()

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def _parse_side_effect(*_args, **_kwargs):
            barrier.wait(timeout=10)
            return ExtractionResult(True, invoice=extracted)

        def _run(task_id: uuid.UUID):
            db = SessionLocal()
            try:
                task = db.get(Task, task_id)
                handle_parse_invoice(db, task, dict(task.payload or {}))
            except BaseException as exc:  # noqa: BLE001 - collected for the assertion
                errors.append(exc)
            finally:
                db.close()

        with patch("backend.app.workers.handlers.InvoiceParser.parse", side_effect=_parse_side_effect):
            threads = [threading.Thread(target=_run, args=(task_id,)) for task_id in task_ids]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)

        self.assertEqual(errors, [])
        self.db.expire_all()
        invoices = list(self.db.scalars(select(Invoice).where(
            Invoice.business_id == self.business_a_id,
            Invoice.invoice_number == "INV-RACE-1",
        )).all())
        self.assertEqual(len(invoices), 1)
        customers = list(self.db.scalars(select(Customer).where(
            Customer.business_id == self.business_a_id,
            Customer.display_name == "Race Buyer",
        )).all())
        self.assertEqual(len(customers), 1)

    def test_failed_processing_does_not_count_as_a_successful_invoice(self):
        before = build_dashboard_summary(self.db, self.business_a_id).current_invoice_count
        response = self.client.post(
            "/invoices/upload",
            files={"file": ("blank.pdf", io.BytesIO(_pdf()), "application/pdf")},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        task = self.db.get(Task, uuid.UUID(response.json()["task_id"]))
        document = self.db.get(InvoiceDocument, uuid.UUID(response.json()["document_id"]))
        self.uploaded_keys.append(document.storage_key)
        with (
            patch(
                "backend.app.workers.handlers.InvoiceParser.parse",
                return_value=ExtractionResult(False, error="Could not reliably extract required fields: customer name."),
            ),
            self.assertRaises(PermanentParserError),
        ):
            handle_parse_invoice(self.db, task, dict(task.payload or {}))
        self.db.expire_all()
        document = self.db.get(InvoiceDocument, document.id)
        self.assertEqual(document.processing_status, "ERROR")
        self.assertIsNone(document.invoice_id)
        held = Invoice(
            business_id=self.business_a_id,
            invoice_number=f"INV-HELD-{uuid.uuid4().hex[:6]}",
            invoice_date=date(2026, 10, 1),
            due_date=date(2026, 11, 1),
            amount=Decimal("9000.00"),
            currency="INR",
            origin=InvoiceOrigin.CURRENT.value,
            processing_status="NEEDS_REVIEW",
            payment_status="OPEN",
        )
        self.db.add(held)
        self.db.commit()
        summary = build_dashboard_summary(self.db, self.business_a_id)
        self.assertEqual(summary.current_invoice_count, before)
        self.assertEqual(summary.outstanding.count, 0)

    def test_identity_diagnostic_flags_a_payment_customer_mismatch_without_changing_it(self):
        assigned = self._customer(self.business_a_id, "Assigned Buyer")
        recorded = self._customer(self.business_a_id, "Recorded Buyer")
        invoice = Invoice(
            business_id=self.business_a_id,
            customer_id=assigned.id,
            invoice_number=f"INV-DIAG-{uuid.uuid4().hex[:6]}",
            invoice_date=date(2026, 1, 1),
            due_date=date(2026, 2, 1),
            amount=Decimal("1000.00"),
            currency="INR",
            origin=InvoiceOrigin.HISTORICAL.value,
            processing_status="PROCESSED",
            payment_status="PAID",
        )
        self.db.add(invoice)
        self.db.flush()
        self.db.add(Payment(
            business_id=self.business_a_id,
            invoice_id=invoice.id,
            invoice_reference=invoice.invoice_number,
            payment_date=datetime(2026, 2, 2, tzinfo=timezone.utc),
            amount=Decimal("1000.00"),
            customer_identity_key=f"customer:{recorded.id}",
            provenance="manual",
        ))
        self.db.commit()
        response = self.client.get(
            "/historical/identity-diagnostics",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        match = next(item for item in response.json() if item["invoice_id"] == str(invoice.id))
        self.assertEqual(match["customer_name"], "Assigned Buyer")
        self.assertIn("different customer", match["reason"])
        self.db.expire_all()
        self.assertEqual(self.db.get(Invoice, invoice.id).customer_id, assigned.id)

    def test_task_status_is_tenant_scoped(self):
        response = self.client.post(
            "/invoices/upload",
            files={"file": ("status.pdf", io.BytesIO(_pdf()), "application/pdf")},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["task_id"]
        document = self.db.get(InvoiceDocument, uuid.UUID(response.json()["document_id"]))
        self.uploaded_keys.append(document.storage_key)
        own = self.client.get(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {self.token_a}"})
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.json()["state"], "queued")
        self.assertEqual(own.json()["status"], "PENDING")
        other = self.client.get(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {self.token_b}"})
        self.assertEqual(other.status_code, 404)


if __name__ == "__main__":
    unittest.main()
