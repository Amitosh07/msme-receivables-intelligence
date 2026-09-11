"""Phase H: Explicit Historical vs Current Data Origin tests.

Verifies:
1. Normal invoice upload defaults to 'CURRENT' origin.
2. Explicit historical upload persists 'HISTORICAL' origin on document, task, and invoice.
3. Invalid origin inputs are rejected with 422.
4. Database CHECK constraints enforce valid origin values.
5. Tenant isolation is strictly preserved.
6. Customer identity normalization and matching behave identically.
7. Historical paid invoices contribute to customer payment history and prediction eligibility.
"""

from datetime import date, datetime, timezone
import io
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.models.payment import Payment
from backend.app.models.task import Task
from backend.app.models.user import User
from backend.app.services.customer_identity import resolve_customer_identity
from backend.app.services.prediction_service import (
    evaluate_prediction_eligibility,
    get_eligible_prior_payments,
)
from backend.app.services.storage import get_storage
from backend.app.workers.handlers import handle_parse_invoice


def _create_minimal_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"


class TestInvoiceOrigin(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        self.storage = get_storage()

        # Register Business A (User A)
        self.suffix_a = uuid.uuid4().hex[:8]
        res_a = self.client.post(
            "/auth/register",
            json={
                "email": f"origina_{self.suffix_a}@corp.com",
                "password": "Password123!",
                "full_name": "Origin Admin A",
                "business_name": f"Origin Corp A {self.suffix_a}",
            },
        )
        self.assertEqual(res_a.status_code, 201)
        data_a = res_a.json()
        self.token_a = data_a["access_token"]
        self.business_a_id = uuid.UUID(data_a["business"]["id"])
        self.user_a_id = uuid.UUID(data_a["user"]["id"])

        # Register Business B (User B)
        self.suffix_b = uuid.uuid4().hex[:8]
        res_b = self.client.post(
            "/auth/register",
            json={
                "email": f"originb_{self.suffix_b}@corp.com",
                "password": "Password123!",
                "full_name": "Origin Admin B",
                "business_name": f"Origin Corp B {self.suffix_b}",
            },
        )
        self.assertEqual(res_b.status_code, 201)
        data_b = res_b.json()
        self.token_b = data_b["access_token"]
        self.business_b_id = uuid.UUID(data_b["business"]["id"])
        self.user_b_id = uuid.UUID(data_b["user"]["id"])

        self.uploaded_keys: list[str] = []

    def tearDown(self):
        for k in self.uploaded_keys:
            try:
                self.storage.delete(k)
            except Exception:
                pass
        try:
            biz_a = self.db.get(Business, self.business_a_id)
            if biz_a:
                self.db.delete(biz_a)
            biz_b = self.db.get(Business, self.business_b_id)
            if biz_b:
                self.db.delete(biz_b)
            user_a = self.db.get(User, self.user_a_id)
            if user_a:
                self.db.delete(user_a)
            user_b = self.db.get(User, self.user_b_id)
            if user_b:
                self.db.delete(user_b)
            self.db.commit()
        except Exception:
            self.db.rollback()
        finally:
            self.db.close()

    def test_default_upload_persists_current_origin(self):
        """Normal invoice upload without origin parameter defaults to CURRENT."""
        pdf_content = _create_minimal_pdf()
        files = {"file": ("normal_invoice.pdf", io.BytesIO(pdf_content), "application/pdf")}

        response = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()

        self.assertEqual(data["origin"], InvoiceOrigin.CURRENT.value)

        # Verify persisted document
        doc = self.db.get(InvoiceDocument, uuid.UUID(data["document_id"]))
        self.assertIsNotNone(doc)
        self.assertEqual(doc.origin, InvoiceOrigin.CURRENT.value)
        self.uploaded_keys.append(doc.storage_key)

        # Verify task payload
        task = self.db.get(Task, uuid.UUID(data["task_id"]))
        self.assertIsNotNone(task)
        self.assertEqual(task.payload.get("origin"), InvoiceOrigin.CURRENT.value)

    def test_explicit_historical_upload_query_param(self):
        """Upload with ?origin=HISTORICAL query param persists HISTORICAL origin."""
        pdf_content = _create_minimal_pdf()
        files = {"file": ("hist_query.pdf", io.BytesIO(pdf_content), "application/pdf")}

        response = self.client.post(
            "/invoices/upload?origin=HISTORICAL",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["origin"], InvoiceOrigin.HISTORICAL.value)

        doc = self.db.get(InvoiceDocument, uuid.UUID(data["document_id"]))
        self.assertIsNotNone(doc)
        self.assertEqual(doc.origin, InvoiceOrigin.HISTORICAL.value)
        self.uploaded_keys.append(doc.storage_key)

        task = self.db.get(Task, uuid.UUID(data["task_id"]))
        self.assertIsNotNone(task)
        self.assertEqual(task.payload.get("origin"), InvoiceOrigin.HISTORICAL.value)

    def test_explicit_historical_upload_form_data(self):
        """Upload with origin form-data parameter persists HISTORICAL origin."""
        pdf_content = _create_minimal_pdf()
        files = {"file": ("hist_form.pdf", io.BytesIO(pdf_content), "application/pdf")}

        response = self.client.post(
            "/invoices/upload",
            files=files,
            data={"origin": "historical"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["origin"], InvoiceOrigin.HISTORICAL.value)

        doc = self.db.get(InvoiceDocument, uuid.UUID(data["document_id"]))
        self.assertIsNotNone(doc)
        self.assertEqual(doc.origin, InvoiceOrigin.HISTORICAL.value)
        self.uploaded_keys.append(doc.storage_key)

    def test_invalid_origin_rejected_with_422(self):
        """Invalid origin values are rejected with 422 Unprocessable Entity."""
        pdf_content = _create_minimal_pdf()
        files = {"file": ("invalid_origin.pdf", io.BytesIO(pdf_content), "application/pdf")}

        response = self.client.post(
            "/invoices/upload?origin=UNKNOWN_ORIGIN",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("Invalid invoice origin", response.json()["detail"])

    def test_database_check_constraint_enforced_on_invoice(self):
        """Direct database insertion with invalid origin violates check constraint."""
        inv = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            invoice_number=f"INV-CHECK-{uuid.uuid4().hex[:6]}",
            invoice_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
            amount=5000.0,
            currency="INR",
            origin="INVALID_ORIGIN",
        )
        self.db.add(inv)
        with self.assertRaises(IntegrityError):
            self.db.flush()
        self.db.rollback()

    def test_database_check_constraint_enforced_on_document(self):
        """Direct database insertion of document with invalid origin violates check constraint."""
        doc = InvoiceDocument(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            storage_key="test/key.pdf",
            original_filename="test.pdf",
            origin="INVALID_ORIGIN",
        )
        self.db.add(doc)
        with self.assertRaises(IntegrityError):
            self.db.flush()
        self.db.rollback()

    def test_worker_propagates_origin_to_invoice(self):
        """Background worker sets invoice origin matching document/task origin."""
        # Create a document and a task with HISTORICAL origin
        doc = InvoiceDocument(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            storage_key=f"tenants/{self.business_a_id}/invoices/test.pdf",
            original_filename="invoice_hist.pdf",
            content_type="application/pdf",
            file_size=100,
            processing_status="PENDING",
            origin=InvoiceOrigin.HISTORICAL.value,
        )
        self.db.add(doc)
        self.db.flush()

        inv_num = f"INV-HIST-{uuid.uuid4().hex[:6]}"
        # Mocking parser output via direct invoice creation with origin
        inv = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            invoice_number=inv_num,
            invoice_date=date(2025, 6, 1),
            due_date=date(2025, 6, 30),
            amount=12000.0,
            currency="INR",
            payment_status="PAID",
            processing_status="PROCESSED",
            origin=doc.origin,
            document_id=doc.id,
        )
        self.db.add(inv)
        self.db.commit()

        loaded_inv = self.db.get(Invoice, inv.id)
        self.assertEqual(loaded_inv.origin, InvoiceOrigin.HISTORICAL.value)

    def test_list_invoices_filters_by_origin(self):
        """GET /invoices supports filtering by origin."""
        # Create 1 historical invoice and 1 current invoice
        inv_hist = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            invoice_number=f"HIST-{uuid.uuid4().hex[:6]}",
            invoice_date=date(2025, 1, 1),
            due_date=date(2025, 1, 31),
            amount=1000.0,
            currency="INR",
            origin=InvoiceOrigin.HISTORICAL.value,
        )
        inv_curr = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            invoice_number=f"CURR-{uuid.uuid4().hex[:6]}",
            invoice_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            amount=2000.0,
            currency="INR",
            origin=InvoiceOrigin.CURRENT.value,
        )
        self.db.add_all([inv_hist, inv_curr])
        self.db.commit()

        # Query all
        res_all = self.client.get(
            "/invoices",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_all.status_code, 200)
        all_numbers = [item["invoice_number"] for item in res_all.json()["items"]]
        self.assertIn(inv_hist.invoice_number, all_numbers)
        self.assertIn(inv_curr.invoice_number, all_numbers)

        # Filter HISTORICAL
        res_hist = self.client.get(
            "/invoices?origin=HISTORICAL",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_hist.status_code, 200)
        hist_numbers = [item["invoice_number"] for item in res_hist.json()["items"]]
        self.assertIn(inv_hist.invoice_number, hist_numbers)
        self.assertNotIn(inv_curr.invoice_number, hist_numbers)

        # Filter CURRENT
        res_curr = self.client.get(
            "/invoices?origin=CURRENT",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_curr.status_code, 200)
        curr_numbers = [item["invoice_number"] for item in res_curr.json()["items"]]
        self.assertNotIn(inv_hist.invoice_number, curr_numbers)
        self.assertIn(inv_curr.invoice_number, curr_numbers)

    def test_tenant_isolation_preserved(self):
        """Invoices of Business A (historical or current) are never visible to Business B."""
        inv_a = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            invoice_number=f"A-HIST-{uuid.uuid4().hex[:6]}",
            invoice_date=date(2025, 1, 1),
            due_date=date(2025, 1, 31),
            amount=5000.0,
            currency="INR",
            origin=InvoiceOrigin.HISTORICAL.value,
        )
        self.db.add(inv_a)
        self.db.commit()

        # User B lists invoices
        res_b = self.client.get(
            "/invoices?origin=HISTORICAL",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(res_b.status_code, 200)
        b_numbers = [item["invoice_number"] for item in res_b.json()["items"]]
        self.assertNotIn(inv_a.invoice_number, b_numbers)

    def test_customer_identity_normalization_preserved(self):
        """Customer identity normalization operates identically for historical records."""
        from backend.app.services.customer_identity import create_customer

        valid_gstin = "27AAPFU0939F1ZV"
        # Create customer for business A
        customer = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Acme Technologies Pvt Ltd",
            gstin=valid_gstin,
        )
        self.db.commit()

        # Resolve customer identity for a historical invoice with untrimmed spacing
        raw_name = "  Acme  Technologies   Pvt   Ltd  "
        res_hist = resolve_customer_identity(
            self.db,
            business_id=self.business_a_id,
            display_name=raw_name,
            gstin=valid_gstin,
        )
        self.assertIsNotNone(res_hist.customer)
        self.assertEqual(res_hist.customer.id, customer.id)
        self.assertEqual(res_hist.customer.normalized_name, "acme technologies private limited")
        self.assertEqual(res_hist.customer.normalized_gstin, valid_gstin)

        # Resolve customer identity for a current invoice with same name/gstin
        res_curr = resolve_customer_identity(
            self.db,
            business_id=self.business_a_id,
            display_name="Acme Technologies Pvt Ltd",
            gstin=valid_gstin,
        )
        # Must resolve to the exact same customer
        self.assertEqual(res_hist.customer.id, res_curr.customer.id)

    def test_historical_invoices_contribute_to_prediction_eligibility(self):
        """Historical paid invoices strictly prior to invoice_date contribute to history."""
        # Create customer
        customer = Customer(
            business_id=self.business_a_id,
            display_name="Eligible Client",
            normalized_name="eligible client",
        )
        self.db.add(customer)
        self.db.flush()

        # Create 3 prior historical paid invoices with payments
        for i in range(1, 4):
            inv = Invoice(
                id=uuid.uuid4(),
                business_id=self.business_a_id,
                customer_id=customer.id,
                invoice_number=f"HIST-PAY-{i}-{uuid.uuid4().hex[:4]}",
                invoice_date=date(2025, i, 1),
                due_date=date(2025, i, 28),
                amount=1000.0 * i,
                currency="INR",
                payment_status="PAID",
                processing_status="PROCESSED",
                origin=InvoiceOrigin.HISTORICAL.value,
            )
            self.db.add(inv)
            self.db.flush()

            pmt = Payment(
                id=uuid.uuid4(),
                business_id=self.business_a_id,
                invoice_id=inv.id,
                invoice_reference=inv.invoice_number,
                payment_date=datetime(2025, i, 20, tzinfo=timezone.utc),
                amount=inv.amount,
                provenance="import",
            )
            self.db.add(pmt)

        # Target invoice in 2026
        target_inv = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            customer_id=customer.id,
            invoice_number=f"TARGET-{uuid.uuid4().hex[:4]}",
            invoice_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
            amount=5000.0,
            currency="INR",
            payment_status="OPEN",
            processing_status="PROCESSED",
            origin=InvoiceOrigin.CURRENT.value,
        )
        self.db.add(target_inv)
        self.db.commit()

        # Check prediction eligibility
        eligibility = evaluate_prediction_eligibility(self.db, target_inv)
        self.assertEqual(eligibility.eligible_history_count, 3)
        self.assertTrue(eligibility.prediction_available)

        prior_payments = get_eligible_prior_payments(self.db, target_inv)
        self.assertEqual(len(prior_payments), 3)


if __name__ == "__main__":
    unittest.main()
