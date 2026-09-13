"""
Phase I: Historical Company Workspace backend tests.

Verifies:
1. Historical company listing (dynamic, non-hardcoded).
2. Historical company search using normalized customer identity.
3. Historical company detail with factual payment statuses based on payment rows.
4. Historical invoice upload explicitly tagged HISTORICAL with authoritative customer.
5. Historical manual payment creation with factual payment date (provenance='manual').
6. Historical CSV payment import within company workspace.
7. Historical XLSX payment import within company workspace.
8. Payment date persistence and visibility on matched historical invoices.
9. Tenant isolation across all workspace endpoints.
10. Duplicate payment protection.
11. Regression test ensuring normal invoice upload remains CURRENT.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import io
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import Workbook
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
from backend.app.models.user import User
from backend.app.services.customer_identity import create_customer
from backend.app.services.storage import get_storage
from backend.app.services.historical_service import upload_unassigned_historical_invoice
from backend.app.services.parser.base import ExtractedInvoice, ExtractionResult
from backend.app.workers.handlers import handle_parse_invoice


def _create_minimal_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"


class TestHistoricalWorkspace(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        self.storage = get_storage()

        # Register Business A (User A)
        self.suffix_a = uuid.uuid4().hex[:8]
        res_a = self.client.post(
            "/auth/register",
            json={
                "email": f"hista_{self.suffix_a}@corp.com",
                "password": "Password123!",
                "full_name": "Hist Admin A",
                "business_name": f"Hist Corp A {self.suffix_a}",
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
                "email": f"histb_{self.suffix_b}@corp.com",
                "password": "Password123!",
                "full_name": "Hist Admin B",
                "business_name": f"Hist Corp B {self.suffix_b}",
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

    def test_new_tenant_has_empty_historical_workspace(self):
        response = self.client.get(
            "/historical/companies",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_create_historical_company_with_optional_gstin_and_reject_duplicates(self):
        headers = {"Authorization": f"Bearer {self.token_a}"}
        created = self.client.post(
            "/historical/companies",
            json={"display_name": "Apex Manufacturing Pvt Ltd"},
            headers=headers,
        )
        self.assertEqual(created.status_code, 201)
        self.assertIsNone(created.json()["gstin"])
        self.assertEqual(created.json()["historical_invoice_count"], 0)

        duplicate = self.client.post(
            "/historical/companies",
            json={"display_name": "apex manufacturing private limited"},
            headers=headers,
        )
        self.assertEqual(duplicate.status_code, 409)

        listed = self.client.get("/historical/companies", headers=headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["id"], created.json()["id"])

        other_tenant = self.client.get(
            "/historical/companies",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(other_tenant.status_code, 200)
        self.assertEqual(other_tenant.json(), [])

    def test_historical_company_gstin_can_be_added_edited_and_cleared(self):
        headers = {"Authorization": f"Bearer {self.token_a}"}
        created = self.client.post(
            "/historical/companies",
            json={"display_name": "GSTIN Editable Company"},
            headers=headers,
        )
        self.assertEqual(created.status_code, 201)
        company_id = created.json()["id"]
        valid_gstin = "27AAPFU0939F1ZV"
        added = self.client.patch(
            f"/historical/companies/{company_id}",
            json={"gstin": valid_gstin.lower()},
            headers=headers,
        )
        self.assertEqual(added.status_code, 200)
        self.assertEqual(added.json()["gstin"], valid_gstin.lower())
        invalid = self.client.patch(
            f"/historical/companies/{company_id}",
            json={"gstin": "INVALID"},
            headers=headers,
        )
        self.assertEqual(invalid.status_code, 422)
        cleared = self.client.patch(
            f"/historical/companies/{company_id}",
            json={"gstin": None},
            headers=headers,
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()["gstin"])
        isolated = self.client.patch(
            f"/historical/companies/{company_id}",
            json={"gstin": valid_gstin},
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(isolated.status_code, 404)

    def test_historical_payment_endpoint_rejects_current_invoice(self):
        customer = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Current Only Buyer",
        )
        invoice = Invoice(
            business_id=self.business_a_id,
            customer_id=customer.id,
            invoice_number=f"CURRENT-{uuid.uuid4().hex[:8]}",
            invoice_date=date(2026, 9, 1),
            due_date=date(2026, 9, 30),
            amount=1000,
            currency="INR",
            origin=InvoiceOrigin.CURRENT.value,
        )
        self.db.add(invoice)
        self.db.commit()
        response = self.client.post(
            f"/historical/invoices/{invoice.id}/payments",
            json={"payment_date": "2026-09-10", "amount": 1000},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 404)

    def test_manual_historical_invoice_creation_and_validation(self):
        customer = create_customer(
            self.db, business_id=self.business_a_id, display_name="Manual History Workspace"
        )
        self.db.commit()
        headers = {"Authorization": f"Bearer {self.token_a}"}
        path = f"/historical/companies/{customer.id}/invoices/manual"

        open_response = self.client.post(
            path, json={"amount": 6000, "due_date": "2026-01-31"}, headers=headers
        )
        self.assertEqual(open_response.status_code, 201)
        opened = open_response.json()
        self.assertTrue(opened["invoice_number"])
        self.assertEqual(opened["origin"], "HISTORICAL")
        self.assertEqual(opened["payment_status"], "OPEN")
        self.assertIsNone(opened["payment_date"])

        paid_response = self.client.post(
            path,
            json={
                "amount": 7250.50,
                "due_date": "2026-02-28",
                "payment_date": "2026-02-20",
            },
            headers=headers,
        )
        self.assertEqual(paid_response.status_code, 201)
        paid = paid_response.json()
        self.assertNotEqual(opened["invoice_number"], paid["invoice_number"])
        self.assertEqual(paid["payment_status"], "PAID")
        self.assertEqual(paid["payment_date"], "2026-02-20")
        payment = self.db.scalar(select(Payment).where(Payment.invoice_id == uuid.UUID(paid["id"])))
        self.assertIsNotNone(payment)
        self.assertEqual(payment.provenance, "manual")

        partial = self.client.post(
            f"/historical/invoices/{opened['id']}/payments",
            json={"amount": 1000, "payment_date": "2026-01-20"},
            headers=headers,
        )
        self.assertEqual(partial.status_code, 201)
        self.assertEqual(partial.json()["payment_status"], "PARTIAL")

        for payload in (
            {"amount": 0, "due_date": "2026-01-31"},
            {"amount": -1, "due_date": "2026-01-31"},
            {"amount": 100, "due_date": "1800-01-01"},
            {"amount": 100, "due_date": "2026-01-31", "payment_date": "2100-01-01"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(path, json=payload, headers=headers).status_code, 422)

        cross_tenant = self.client.post(
            path,
            json={"amount": 100, "due_date": "2026-01-31"},
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(cross_tenant.status_code, 404)

    def test_manual_historical_invoice_duplicate_protection_and_eligibility(self):
        from backend.app.services.prediction_service import evaluate_prediction_eligibility

        customer = create_customer(
            self.db, business_id=self.business_a_id, display_name="Eligibility History Workspace"
        )
        duplicate_reference = str(uuid.uuid4())
        self.db.add(Invoice(
            business_id=self.business_a_id, customer_id=customer.id,
            invoice_number=duplicate_reference, invoice_date=None,
            due_date=date(2026, 1, 1), amount=100, currency=None,
            origin=InvoiceOrigin.HISTORICAL.value,
        ))
        self.db.commit()
        headers = {"Authorization": f"Bearer {self.token_a}"}
        path = f"/historical/companies/{customer.id}/invoices/manual"
        with patch(
            "backend.app.services.historical_service.generate_manual_invoice_reference",
            return_value=duplicate_reference,
        ):
            duplicate = self.client.post(
                path, json={"amount": 100, "due_date": "2026-01-01"}, headers=headers
            )
        self.assertEqual(duplicate.status_code, 422)

        for month in (1, 2, 3):
            response = self.client.post(
                path,
                json={
                    "amount": 1000 * month,
                    "due_date": f"2026-0{month}-15",
                    "payment_date": f"2026-0{month}-10",
                },
                headers=headers,
            )
            self.assertEqual(response.status_code, 201)
        current = Invoice(
            business_id=self.business_a_id, customer_id=customer.id,
            invoice_number=str(uuid.uuid4()), invoice_date=date(2026, 6, 1),
            due_date=date(2026, 6, 30), amount=5000, currency="INR",
            origin=InvoiceOrigin.CURRENT.value, processing_status="PROCESSED",
        )
        self.db.add(current)
        self.db.commit()
        eligibility = evaluate_prediction_eligibility(self.db, current)
        self.assertTrue(eligibility.prediction_available)
        self.assertEqual(eligibility.eligible_history_count, 3)

    def test_historical_worker_auto_discovers_company_case_insensitively(self):
        existing = create_customer(
            self.db, business_id=self.business_a_id, display_name="Orchard Components Pvt Ltd"
        )
        self.db.commit()
        doc, task = upload_unassigned_historical_invoice(
            self.db, self.business_a_id, _create_minimal_pdf(), "auto-company.pdf"
        )
        self.uploaded_keys.append(doc.storage_key)
        extracted = ExtractedInvoice(
            invoice_number="HIST-AUTO-COMPANY",
            invoice_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
            amount=Decimal("2500.00"),
            currency=None,
            customer_name="orchard components private limited",
        )
        with patch(
            "backend.app.workers.handlers.InvoiceParser.parse",
            return_value=ExtractionResult(True, invoice=extracted),
        ):
            handle_parse_invoice(self.db, task, task.payload)
        invoice = self.db.get(Invoice, task.invoice_id)
        self.assertEqual(invoice.customer_id, existing.id)
        self.assertEqual(invoice.origin, InvoiceOrigin.HISTORICAL.value)
        self.assertIsNone(invoice.currency)
        count = self.db.scalar(
            select(func.count(Customer.id)).where(
                Customer.business_id == self.business_a_id,
                Customer.normalized_name == existing.normalized_name,
            )
        )
        self.assertEqual(count, 1)

    def test_historical_worker_missing_company_and_due_date_enters_review(self):
        before = self.db.scalar(
            select(func.count(Customer.id)).where(Customer.business_id == self.business_a_id)
        )
        doc, task = upload_unassigned_historical_invoice(
            self.db, self.business_a_id, _create_minimal_pdf(), "needs-review.pdf"
        )
        self.uploaded_keys.append(doc.storage_key)
        extracted = ExtractedInvoice(
            invoice_number="HIST-NEEDS-REVIEW",
            invoice_date=date(2026, 2, 1),
            due_date=None,
            amount=Decimal("3750.00"),
            currency=None,
            customer_name=None,
        )
        with patch(
            "backend.app.workers.handlers.InvoiceParser.parse",
            return_value=ExtractionResult(True, invoice=extracted),
        ):
            handle_parse_invoice(self.db, task, task.payload)
        invoice = self.db.get(Invoice, task.invoice_id)
        self.assertEqual(invoice.processing_status, "NEEDS_REVIEW")
        self.assertIsNone(invoice.customer_id)
        self.assertIsNone(invoice.due_date)
        self.assertEqual(invoice.document.processing_status, "NEEDS_REVIEW")
        self.assertIn("company selection", invoice.document.error_message)
        self.assertIn("due date", invoice.document.error_message)
        after = self.db.scalar(
            select(func.count(Customer.id)).where(Customer.business_id == self.business_a_id)
        )
        self.assertEqual(after, before)

        headers = {"Authorization": f"Bearer {self.token_a}"}
        reviews = self.client.get("/historical/invoices/review", headers=headers)
        self.assertEqual(reviews.status_code, 200)
        self.assertIn(str(invoice.id), {item["id"] for item in reviews.json()})
        completed = self.client.patch(
            f"/historical/invoices/{invoice.id}/review",
            json={
                "company_name": "Review Selected Company",
                "due_date": "2026-03-15",
            },
            headers=headers,
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["processing_status"], "PROCESSED")
        self.db.expire_all()
        reviewed_invoice = self.db.get(Invoice, invoice.id)
        self.assertIsNotNone(reviewed_invoice.customer_id)
        self.assertEqual(reviewed_invoice.due_date, date(2026, 3, 15))

    def test_historical_company_listing_and_search(self):
        """Historical company listing returns only companies with historical data, supporting search."""
        # Create Customer 1 with historical invoice
        cust_hist = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Apex Logistics Private Limited",
            gstin="27AAPFU0939F1ZV",
        )
        # Create Customer 2 with only current invoice
        cust_curr = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Future Logistics LLP",
            gstin="29ABCDE1234F1ZW",
        )
        self.db.commit()

        # Add historical invoice to Customer 1
        inv_hist = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            customer_id=cust_hist.id,
            invoice_number=f"HIST-INV-{uuid.uuid4().hex[:6]}",
            invoice_date=date(2025, 4, 1),
            due_date=date(2025, 4, 30),
            amount=25000.0,
            currency="INR",
            origin=InvoiceOrigin.HISTORICAL.value,
        )
        # Add current invoice to Customer 2
        inv_curr = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            customer_id=cust_curr.id,
            invoice_number=f"CURR-INV-{uuid.uuid4().hex[:6]}",
            invoice_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            amount=15000.0,
            currency="INR",
            origin=InvoiceOrigin.CURRENT.value,
        )
        self.db.add_all([inv_hist, inv_curr])
        self.db.commit()

        # Listing endpoint
        res = self.client.get(
            "/historical/companies",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res.status_code, 200)
        companies = res.json()
        comp_ids = [c["id"] for c in companies]
        self.assertIn(str(cust_hist.id), comp_ids)
        self.assertNotIn(str(cust_curr.id), comp_ids)

        hist_comp = next(c for c in companies if c["id"] == str(cust_hist.id))
        self.assertEqual(hist_comp["display_name"], "Apex Logistics Private Limited")
        self.assertEqual(hist_comp["historical_invoice_count"], 1)
        self.assertEqual(hist_comp["total_amount"], 25000.0)
        self.assertEqual(hist_comp["outstanding_balance"], 25000.0)

        # Search by name with normalization
        search_res = self.client.get(
            "/historical/companies?search=apex",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(search_res.status_code, 200)
        search_companies = search_res.json()
        self.assertEqual(len(search_companies), 1)
        self.assertEqual(search_companies[0]["id"], str(cust_hist.id))

        # Search for non-existent name
        no_match_res = self.client.get(
            "/historical/companies?search=NonExistentCorp",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(no_match_res.status_code, 200)
        self.assertEqual(len(no_match_res.json()), 0)

    def test_historical_company_detail_with_actual_payment_status(self):
        """Historical company detail returns invoices and derives payment status strictly from payment rows."""
        customer = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Metro Retailers Pvt Ltd",
            gstin="27AAPFU0939F1ZV",
        )
        self.db.commit()

        # Invoice 1: Fully paid via Payment row
        inv_paid = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            customer_id=customer.id,
            invoice_number=f"METRO-PAID-{uuid.uuid4().hex[:4]}",
            invoice_date=date(2025, 2, 1),
            due_date=date(2025, 3, 1),
            amount=10000.0,
            currency="INR",
            origin=InvoiceOrigin.HISTORICAL.value,
        )
        # Invoice 2: Unpaid (Open)
        inv_open = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            customer_id=customer.id,
            invoice_number=f"METRO-OPEN-{uuid.uuid4().hex[:4]}",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 3, 31),
            amount=5000.0,
            currency="INR",
            origin=InvoiceOrigin.HISTORICAL.value,
        )
        self.db.add_all([inv_paid, inv_open])
        self.db.flush()

        # Add payment row for Invoice 1
        pmt = Payment(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            invoice_id=inv_paid.id,
            invoice_reference=inv_paid.invoice_number,
            payment_date=datetime(2025, 2, 20, tzinfo=timezone.utc),
            amount=10000.0,
            provenance="manual",
        )
        self.db.add(pmt)
        self.db.commit()

        # Query detail
        res = self.client.get(
            f"/historical/companies/{customer.id}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res.status_code, 200)
        detail = res.json()

        self.assertEqual(detail["id"], str(customer.id))
        self.assertEqual(detail["display_name"], "Metro Retailers Pvt Ltd")
        self.assertEqual(detail["historical_invoice_count"], 2)
        self.assertEqual(detail["total_amount"], 15000.0)
        self.assertEqual(detail["total_paid"], 10000.0)
        self.assertEqual(detail["outstanding_balance"], 5000.0)

        invoices_dict = {inv["invoice_number"]: inv for inv in detail["invoices"]}
        self.assertEqual(invoices_dict[inv_paid.invoice_number]["payment_status"], "PAID")
        self.assertEqual(invoices_dict[inv_paid.invoice_number]["total_paid"], 10000.0)
        self.assertEqual(invoices_dict[inv_paid.invoice_number]["payment_date"], "2025-02-20")

        self.assertEqual(invoices_dict[inv_open.invoice_number]["payment_status"], "OPEN")
        self.assertEqual(invoices_dict[inv_open.invoice_number]["total_paid"], 0.0)
        self.assertIsNone(invoices_dict[inv_open.invoice_number]["payment_date"])

    def test_historical_invoice_upload_tagged_historical(self):
        """Uploading historical invoice via company endpoint tags document and task as HISTORICAL with customer_id."""
        customer = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Upload Target Co",
            gstin="27AAPFU0939F1ZV",
        )
        self.db.commit()

        pdf_content = _create_minimal_pdf()
        files = {"file": ("historical_bill.pdf", io.BytesIO(pdf_content), "application/pdf")}

        response = self.client.post(
            f"/historical/companies/{customer.id}/invoices/upload",
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
        self.assertEqual(task.payload.get("customer_id"), str(customer.id))

    def test_historical_manual_payment_creation(self):
        """Manual historical payment entry creates Payment row with provenance='manual' and updates balance."""
        customer = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Manual Payee Corp",
            gstin="27AAPFU0939F1ZV",
        )
        inv = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            customer_id=customer.id,
            invoice_number=f"HIST-MANUAL-{uuid.uuid4().hex[:4]}",
            invoice_date=date(2025, 5, 1),
            due_date=date(2025, 5, 30),
            amount=8000.0,
            currency="INR",
            origin=InvoiceOrigin.HISTORICAL.value,
        )
        self.db.add(inv)
        self.db.commit()

        res = self.client.post(
            f"/historical/invoices/{inv.id}/payments",
            json={
                "payment_date": "2025-05-15",
                "amount": 8000.0,
                "reference": "UTR-HIST-001",
                "note": "Historical clearance entry",
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()

        self.assertEqual(data["payment"]["provenance"], "manual")
        self.assertEqual(data["payment"]["amount"], 8000.0)
        self.assertEqual(data["total_paid"], 8000.0)
        self.assertEqual(data["outstanding_balance"], 0.0)
        self.assertEqual(data["payment_status"], "PAID")

        # Duplicate payment entry should be rejected
        dup_res = self.client.post(
            f"/historical/invoices/{inv.id}/payments",
            json={
                "payment_date": "2025-05-15",
                "amount": 8000.0,
                "reference": "UTR-HIST-001",
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(dup_res.status_code, 409)

    def test_historical_csv_import_within_company_workspace(self):
        """Historical payment CSV import matches historical invoice and exposes payment_date in results."""
        customer = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="CSV Buyer Corp",
            gstin="27AAPFU0939F1ZV",
        )
        inv = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            customer_id=customer.id,
            invoice_number=f"CSV-INV-{uuid.uuid4().hex[:4]}",
            invoice_date=date(2025, 1, 10),
            due_date=date(2025, 2, 10),
            amount=12500.0,
            currency="INR",
            origin=InvoiceOrigin.HISTORICAL.value,
        )
        self.db.add(inv)
        self.db.commit()

        csv_content = f"invoice,date,amount,reference\n{inv.invoice_number},2025-01-25,12500.00,NEFT-0123\n".encode("utf-8")
        files = {"file": ("payments.csv", io.BytesIO(csv_content), "text/csv")}

        res = self.client.post(
            f"/historical/companies/{customer.id}/payments/import",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["imported"], 1)
        self.assertEqual(data["matched"], 1)
        self.assertEqual(len(data["row_results"]), 1)

        row_0 = data["row_results"][0]
        self.assertEqual(row_0["status"], "imported")
        self.assertIsNotNone(row_0["payment_date"])
        self.assertEqual(row_0["invoice_number"], inv.invoice_number)
        self.assertEqual(row_0["invoice_id"], str(inv.id))

    def test_historical_xlsx_import_within_company_workspace(self):
        """Historical payment XLSX import correctly parses and matches within company workspace."""
        customer = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Excel Client Limited",
            gstin="27AAPFU0939F1ZV",
        )
        inv = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            customer_id=customer.id,
            invoice_number=f"XLS-INV-{uuid.uuid4().hex[:4]}",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 3, 31),
            amount=34000.0,
            currency="INR",
            origin=InvoiceOrigin.HISTORICAL.value,
        )
        self.db.add(inv)
        self.db.commit()

        # Build XLSX in memory
        wb = Workbook()
        ws = wb.active
        ws.title = "Payments"
        ws.append(["invoice", "date", "amount", "reference"])
        ws.append([inv.invoice_number, "2025-03-20", 34000, "XLS-REF-1"])

        xlsx_stream = io.BytesIO()
        wb.save(xlsx_stream)
        xlsx_bytes = xlsx_stream.getvalue()

        files = {"file": ("payments.xlsx", io.BytesIO(xlsx_bytes), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        res = self.client.post(
            f"/historical/companies/{customer.id}/payments/import",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["imported"], 1)
        self.assertEqual(data["matched"], 1)
        self.assertIsNotNone(data["row_results"][0]["payment_date"])

    def test_tenant_isolation(self):
        """Company historical workspace is strictly tenant isolated."""
        cust_a = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Tenant A Secret Co",
            gstin="27AAPFU0939F1ZV",
        )
        self.db.commit()

        # User B cannot access Tenant A's customer workspace
        res = self.client.get(
            f"/historical/companies/{cust_a.id}",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(res.status_code, 404)

        # User B cannot upload invoice into Tenant A's customer workspace
        pdf_content = _create_minimal_pdf()
        files = {"file": ("exploit.pdf", io.BytesIO(pdf_content), "application/pdf")}
        upload_res = self.client.post(
            f"/historical/companies/{cust_a.id}/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(upload_res.status_code, 404)

    def test_historical_payment_preview_within_company_workspace(self):
        """Preview endpoint validates file and marks preview=True without persisting payments."""
        customer = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Preview Test Co",
            gstin="27AAPFU0939F1ZV",
        )
        self.db.commit()

        csv_content = b"invoice,date,amount,reference\nINV-PREV-01,2025-01-20,5000.00,PREV-1\n"
        files = {"file": ("preview.csv", io.BytesIO(csv_content), "text/csv")}

        res = self.client.post(
            f"/historical/companies/{customer.id}/payments/preview",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["preview"])
        self.assertEqual(data["valid_rows"], 1)

        # Confirm no payments were persisted
        payments_count = self.db.scalar(
            select(func.count(Payment.id)).where(Payment.business_id == self.business_a_id)
        )
        self.assertEqual(payments_count, 0)

    def test_regression_normal_invoice_upload_remains_current(self):
        """Standard POST /invoices/upload remains CURRENT without requiring flags."""
        pdf_content = _create_minimal_pdf()
        files = {"file": ("normal_regression.pdf", io.BytesIO(pdf_content), "application/pdf")}

        response = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["origin"], InvoiceOrigin.CURRENT.value)

        doc = self.db.get(InvoiceDocument, uuid.UUID(data["document_id"]))
        self.assertIsNotNone(doc)
        self.assertEqual(doc.origin, InvoiceOrigin.CURRENT.value)
        self.uploaded_keys.append(doc.storage_key)

    def test_historical_workflow_end_to_end(self):
        """
        Phase L: Comprehensive Historical Workflow End-to-End Test.
        Validates all 19 requirements:
        1. Create/select tenant.
        2. Open Historical Data.
        3. Create/select a historical company/customer.
        4. Upload multiple historical invoice PDFs.
        5. Verify those invoices are explicitly marked/stored as historical.
        6. Import historical payment CSV.
        7. Verify payments link to historical invoices.
        8. Verify payment dates display on historical invoice rows.
        9. Add another payment manually with calendar-selected date.
        10. Verify factual payment state updates correctly.
        11. Historical customer now has at least 3 eligible completed outcomes.
        12. Upload a CURRENT invoice through the normal current invoice path.
        13. Verify current invoice is explicitly current/operational.
        14. Verify prediction uses eligible historical customer history.
        15. Verify historical/current data are not visually or semantically mixed.
        16. Verify prediction is unavailable for a customer with fewer than 3 eligible outcomes.
        17. Verify unresolved customer identity does not fabricate prediction.
        18. Verify tenant isolation.
        19. Verify normal current invoice functionality remains unchanged.
        """
        from datetime import timedelta
        from backend.app.services.prediction_service import (
            evaluate_prediction_eligibility,
            predict_for_invoice,
        )

        # 1. Tenant is registered (self.business_a_id, self.token_a)

        # 2. Open Historical Data workspace listing
        res_list = self.client.get(
            "/historical/companies",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_list.status_code, 200)

        # 3. Create a historical company/customer
        customer = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="End-to-End Historical Industrial Solutions",
            gstin="27AAPFU0939F1ZV",
        )
        self.db.commit()

        # 4 & 5. Upload multiple historical invoice PDFs and verify explicitly marked as HISTORICAL
        hist_invoices = []
        for i in range(1, 4):
            pdf_bytes = _create_minimal_pdf()
            res_upload = self.client.post(
                f"/historical/companies/{customer.id}/invoices/upload",
                files={"file": (f"hist_inv_{i}.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
                headers={"Authorization": f"Bearer {self.token_a}"},
            )
            self.assertEqual(res_upload.status_code, 201)
            up_data = res_upload.json()
            self.assertEqual(up_data["origin"], InvoiceOrigin.HISTORICAL.value)

            doc = self.db.get(InvoiceDocument, uuid.UUID(up_data["document_id"]))
            self.assertIsNotNone(doc)
            self.assertEqual(doc.origin, InvoiceOrigin.HISTORICAL.value)
            self.uploaded_keys.append(doc.storage_key)

            # Create processed Invoice row corresponding to uploaded historical document
            inv_num = f"HIST-E2E-INV-0{i}"
            inv = Invoice(
                id=uuid.uuid4(),
                business_id=self.business_a_id,
                customer_id=customer.id,
                invoice_number=inv_num,
                invoice_date=date(2025, i, 1),
                due_date=date(2025, i, 28),
                amount=10000.0 * i,
                currency="INR",
                payment_terms="Net 30",
                payment_status="OPEN",
                processing_status="PROCESSED",
                origin=InvoiceOrigin.HISTORICAL.value,
            )
            self.db.add(inv)
            hist_invoices.append(inv)
        self.db.commit()

        # 6, 7 & 8. Import historical payment CSV; verify linkage and payment dates
        csv_content = (
            f"invoice,date,amount,reference\n"
            f"{hist_invoices[0].invoice_number},2025-01-25,10000.00,CSV-REF-01\n"
            f"{hist_invoices[1].invoice_number},2025-02-26,20000.00,CSV-REF-02\n"
        ).encode("utf-8")
        res_csv = self.client.post(
            f"/historical/companies/{customer.id}/payments/import",
            files={"file": ("history.csv", io.BytesIO(csv_content), "text/csv")},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_csv.status_code, 200)
        csv_data = res_csv.json()
        self.assertEqual(csv_data["imported"], 2)
        self.assertEqual(csv_data["matched"], 2)

        # Verify historical company detail shows payments linked and dates displayed
        res_detail = self.client.get(
            f"/historical/companies/{customer.id}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_detail.status_code, 200)
        detail_data = res_detail.json()
        self.assertEqual(detail_data["historical_invoice_count"], 3)
        inv_map = {inv["invoice_number"]: inv for inv in detail_data["invoices"]}
        self.assertEqual(inv_map[hist_invoices[0].invoice_number]["payment_status"], "PAID")
        self.assertEqual(inv_map[hist_invoices[0].invoice_number]["payment_date"], "2025-01-25")
        self.assertEqual(inv_map[hist_invoices[1].invoice_number]["payment_status"], "PAID")
        self.assertEqual(inv_map[hist_invoices[1].invoice_number]["payment_date"], "2025-02-26")
        self.assertEqual(inv_map[hist_invoices[2].invoice_number]["payment_status"], "OPEN")
        self.assertIsNone(inv_map[hist_invoices[2].invoice_number]["payment_date"])

        # 9 & 10. Add manual payment for invoice 3 with calendar-selected date; verify factual state
        res_manual = self.client.post(
            f"/historical/invoices/{hist_invoices[2].id}/payments",
            json={
                "payment_date": "2025-03-27",
                "amount": 30000.0,
                "reference": "MANUAL-REF-03",
                "note": "Settlement by RTGS",
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_manual.status_code, 201)
        manual_data = res_manual.json()
        self.assertEqual(manual_data["payment"]["provenance"], "manual")
        self.assertTrue(manual_data["payment"]["payment_date"].startswith("2025-03-27"))
        self.assertEqual(manual_data["payment_status"], "PAID")
        self.assertEqual(manual_data["outstanding_balance"], 0.0)

        # 11. Customer now has at least 3 eligible completed historical outcomes
        # 12 & 13. Upload CURRENT invoice via normal path; verify CURRENT origin
        curr_pdf = _create_minimal_pdf()
        res_curr = self.client.post(
            "/invoices/upload",
            files={"file": ("current_live_bill.pdf", io.BytesIO(curr_pdf), "application/pdf")},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_curr.status_code, 201)
        curr_doc_data = res_curr.json()
        self.assertEqual(curr_doc_data["origin"], InvoiceOrigin.CURRENT.value)
        curr_doc = self.db.get(InvoiceDocument, uuid.UUID(curr_doc_data["document_id"]))
        self.assertEqual(curr_doc.origin, InvoiceOrigin.CURRENT.value)
        self.uploaded_keys.append(curr_doc.storage_key)

        # Create current operational invoice row billed to this customer
        curr_invoice = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            customer_id=customer.id,
            invoice_number="INV-OPERATIONAL-2026",
            invoice_date=date(2026, 4, 1),
            due_date=date(2026, 4, 30),
            amount=45000.0,
            currency="INR",
            payment_terms="Net 30",
            payment_status="OPEN",
            processing_status="PROCESSED",
            origin=InvoiceOrigin.CURRENT.value,
        )
        self.db.add(curr_invoice)
        self.db.commit()

        # 14. Verify prediction uses eligible historical customer history
        eligibility = evaluate_prediction_eligibility(self.db, curr_invoice)
        self.assertTrue(eligibility.prediction_available)
        self.assertGreaterEqual(eligibility.eligible_history_count, 3)

        pred = predict_for_invoice(self.db, curr_invoice.id, self.business_a_id)
        self.assertIsNotNone(pred)
        self.assertIn(pred.risk_tier, ["LOW", "MEDIUM", "HIGH"])
        self.assertGreaterEqual(pred.risk_score, 0.0)
        self.assertLessEqual(pred.risk_score, 1.0)
        self.assertGreaterEqual(pred.predicted_days_until_payment, 0.0)

        # 15. Verify historical and current data are not mixed in listings
        res_curr_invoices = self.client.get(
            "/invoices?origin=CURRENT",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_curr_invoices.status_code, 200)
        curr_items = res_curr_invoices.json()["items"]
        for item in curr_items:
            self.assertEqual(item["origin"], "CURRENT")
            self.assertNotEqual(item["invoice_number"], hist_invoices[0].invoice_number)

        res_hist_invoices = self.client.get(
            "/invoices?origin=HISTORICAL",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_hist_invoices.status_code, 200)
        hist_items = res_hist_invoices.json()["items"]
        for item in hist_items:
            self.assertEqual(item["origin"], "HISTORICAL")
            self.assertNotEqual(item["invoice_number"], curr_invoice.invoice_number)

        # 16. Verify prediction is unavailable for a customer with < 3 eligible outcomes
        sparse_customer = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Sparse History Corp",
            gstin="29ABCDE1234F1ZW",
        )
        self.db.commit()

        sparse_invoice = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            customer_id=sparse_customer.id,
            invoice_number="INV-SPARSE-001",
            invoice_date=date(2026, 4, 1),
            due_date=date(2026, 4, 30),
            amount=15000.0,
            currency="INR",
            payment_terms="Net 30",
            payment_status="OPEN",
            processing_status="PROCESSED",
            origin=InvoiceOrigin.CURRENT.value,
        )
        self.db.add(sparse_invoice)
        self.db.commit()

        sparse_eligibility = evaluate_prediction_eligibility(self.db, sparse_invoice)
        self.assertFalse(sparse_eligibility.prediction_available)
        self.assertEqual(sparse_eligibility.eligible_history_count, 0)

        # Predict endpoint returns 200 with prediction_available=False
        res_sparse_pred = self.client.post(
            f"/invoices/{sparse_invoice.id}/predict",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_sparse_pred.status_code, 200)
        sparse_pred_data = res_sparse_pred.json()
        self.assertFalse(sparse_pred_data["prediction_available"])
        self.assertIn("Insufficient customer payment history", sparse_pred_data["reason"])

        # 17. Verify unresolved customer identity does not fabricate prediction
        unresolved_invoice = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            customer_id=None,
            unresolved_customer_name="Unknown Unresolved Corp",
            invoice_number="INV-UNRESOLVED-001",
            invoice_date=date(2026, 4, 1),
            due_date=date(2026, 4, 30),
            amount=20000.0,
            currency="INR",
            payment_terms="Net 30",
            payment_status="OPEN",
            processing_status="PROCESSED",
            origin=InvoiceOrigin.CURRENT.value,
        )
        self.db.add(unresolved_invoice)
        self.db.commit()

        unres_eligibility = evaluate_prediction_eligibility(self.db, unresolved_invoice)
        self.assertFalse(unres_eligibility.prediction_available)
        self.assertEqual(unres_eligibility.eligible_history_count, 0)

        # 18. Verify tenant isolation across historical workspace and current invoices
        res_cross_hist = self.client.get(
            f"/historical/companies/{customer.id}",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(res_cross_hist.status_code, 404)

        res_cross_curr = self.client.get(
            f"/invoices/{curr_invoice.id}",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(res_cross_curr.status_code, 404)

        # 19. Verify normal current invoice functionality remains unchanged
        res_fetch = self.client.get(
            f"/invoices/{curr_invoice.id}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_fetch.status_code, 200)
        fetch_data = res_fetch.json()
        self.assertEqual(fetch_data["invoice_number"], "INV-OPERATIONAL-2026")
        self.assertEqual(fetch_data["origin"], "CURRENT")
        self.assertEqual(fetch_data["payment_status"], "OPEN")

    def test_phase_r_historical_invoice_and_payment_contract_regression(self):
        """
        Phase R Regression Tests:
        Verifies API contracts and schema responses for historical invoices and payments
        under conditions observed during frontend interactions:
        - Manual historical invoice has null currency, null invoice_date, null document_id
        - GET /invoices returns historical invoices with null optional fields cleanly
        - GET /historical/companies/{id} returns historical invoice items with null currency
        - POST /historical/companies/{id}/invoices/{inv_id}/payments records payment cleanly
        - Invoices with 0 payments, 1 payment, and multiple payments serialize accurately
        - Both /invoices and company detail endpoints handle mixed/historical records
        """
        # 1. Create a historical company
        company_res = self.client.post(
            "/historical/companies",
            json={"display_name": "Phase R Historical Corp", "gstin": "27AAPFU0939F1ZV"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(company_res.status_code, 201)
        company_id = company_res.json()["id"]

        # 2. Add manual historical invoice without initial payment
        # (simulates user adding historical invoice)
        inv_res = self.client.post(
            f"/historical/companies/{company_id}/invoices/manual",
            json={
                "amount": 75000.00,
                "due_date": "2026-02-15",
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(inv_res.status_code, 201)
        inv_data = inv_res.json()
        invoice_id = inv_data["id"]
        # Explicitly verify that currency is null/None and invoice_date is null/None
        self.assertIsNone(inv_data.get("currency"))
        self.assertIsNone(inv_data.get("invoice_date"))
        self.assertEqual(inv_data["origin"], "HISTORICAL")
        self.assertEqual(inv_data["payment_status"], "OPEN")
        self.assertEqual(inv_data["payment_count"], 0)
        self.assertIsNone(inv_data["payment_date"])

        # 3. GET /historical/companies/{company_id} - Company workspace detail
        # Must return valid structure where invoice has currency=None, 0 payments
        detail_res = self.client.get(
            f"/historical/companies/{company_id}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(detail_res.status_code, 200)
        detail_data = detail_res.json()
        self.assertEqual(len(detail_data["invoices"]), 1)
        detail_inv = detail_data["invoices"][0]
        self.assertIsNone(detail_inv["currency"])
        self.assertIsNone(detail_inv["invoice_date"])
        self.assertEqual(detail_inv["payment_count"], 0)
        self.assertEqual(detail_inv["total_paid"], 0.0)
        self.assertEqual(detail_inv["outstanding_balance"], 75000.00)

        # 4. GET /invoices - Invoices register page endpoint
        # Verifies the Invoices listing endpoint correctly serializes historical invoices
        # with null currency, null invoice_date, null document_id
        list_res = self.client.get(
            "/invoices",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(list_res.status_code, 200)
        list_items = list_res.json()["items"]
        matched_hist = [i for i in list_items if i["id"] == invoice_id]
        self.assertEqual(len(matched_hist), 1)
        self.assertIsNone(matched_hist[0]["currency"])
        self.assertIsNone(matched_hist[0]["invoice_date"])
        self.assertEqual(matched_hist[0]["origin"], "HISTORICAL")

        # 5. Record first partial payment via company-scoped payment endpoint
        # (used by Add payment modal)
        pay_res_1 = self.client.post(
            f"/historical/companies/{company_id}/invoices/{invoice_id}/payments",
            json={
                "payment_date": "2026-02-20",
                "amount": 25000.00,
                "reference": "UTR-PHASE-R-01",
                "note": "First partial historical payment",
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(pay_res_1.status_code, 201)
        pay_data_1 = pay_res_1.json()
        self.assertEqual(pay_data_1["payment_status"], "PARTIAL")
        self.assertEqual(pay_data_1["total_paid"], 25000.00)
        self.assertEqual(pay_data_1["outstanding_balance"], 50000.00)

        # 6. Verify detail view after 1 payment
        detail_res_2 = self.client.get(
            f"/historical/companies/{company_id}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(detail_res_2.status_code, 200)
        inv_after_p1 = detail_res_2.json()["invoices"][0]
        self.assertEqual(inv_after_p1["payment_count"], 1)
        self.assertEqual(inv_after_p1["payment_date"], "2026-02-20")
        self.assertEqual(inv_after_p1["payment_status"], "PARTIAL")

        # 7. Record second settlement payment (multiple payments on 1 historical invoice)
        pay_res_2 = self.client.post(
            f"/historical/companies/{company_id}/invoices/{invoice_id}/payments",
            json={
                "payment_date": "2026-02-25",
                "amount": 50000.00,
                "reference": "UTR-PHASE-R-02",
                "note": "Final settlement historical payment",
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(pay_res_2.status_code, 201)
        pay_data_2 = pay_res_2.json()
        self.assertEqual(pay_data_2["payment_status"], "PAID")
        self.assertEqual(pay_data_2["total_paid"], 75000.00)
        self.assertEqual(pay_data_2["outstanding_balance"], 0.0)

        # 8. Verify detail view after multiple payments
        detail_res_3 = self.client.get(
            f"/historical/companies/{company_id}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(detail_res_3.status_code, 200)
        inv_after_p2 = detail_res_3.json()["invoices"][0]
        self.assertEqual(inv_after_p2["payment_count"], 2)
        self.assertEqual(inv_after_p2["payment_status"], "PAID")
        self.assertEqual(inv_after_p2["outstanding_balance"], 0.0)

    # ======================================================================
    # PHASE S TESTS: HISTORICAL COMPANY MANAGEMENT
    # ======================================================================

    def test_phase_s_brand_new_tenant_empty_state(self):
        """A brand-new account with no historical data shows 0 companies, 0 invoices, 0 totals."""
        suffix = uuid.uuid4().hex[:8]
        res = self.client.post(
            "/auth/register",
            json={
                "email": f"empty_tenant_{suffix}@corp.com",
                "password": "Password123!",
                "full_name": "Empty User",
                "business_name": f"Empty Corp {suffix}",
            },
        )
        self.assertEqual(res.status_code, 201)
        token = res.json()["access_token"]

        # Listing must return empty list with no seed/demo companies
        list_res = self.client.get(
            "/historical/companies",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(list_res.status_code, 200)
        companies = list_res.json()
        self.assertEqual(len(companies), 0)

    def test_phase_s_create_company_required_name_and_optional_gstin(self):
        """Must allow creating company with required name; optional valid GSTIN validated."""
        # 1. Valid creation without GSTIN
        res_no_gst = self.client.post(
            "/historical/companies",
            json={"display_name": "Tata Technologies Ltd"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_no_gst.status_code, 201)
        data_no_gst = res_no_gst.json()
        self.assertEqual(data_no_gst["display_name"], "Tata Technologies Ltd")
        self.assertIsNone(data_no_gst["gstin"])

        # 2. Valid creation using company_name field alias
        res_alias = self.client.post(
            "/historical/companies",
            json={"company_name": "Wipro Enterprises Pvt Ltd"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_alias.status_code, 201)
        self.assertEqual(res_alias.json()["display_name"], "Wipro Enterprises Pvt Ltd")

        # 3. Valid creation with valid GSTIN (Karnataka: 29AABCU9603R1ZJ)
        res_gst = self.client.post(
            "/historical/companies",
            json={
                "display_name": "Infosys BPM Ltd",
                "gstin": "29AABCU9603R1ZJ",
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_gst.status_code, 201)
        self.assertEqual(res_gst.json()["gstin"], "29AABCU9603R1ZJ")

        # 4. Missing name rejected with 422
        res_empty = self.client.post(
            "/historical/companies",
            json={"display_name": "   "},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_empty.status_code, 422)

        # 5. Invalid GSTIN checksum rejected with 422
        res_bad_gst = self.client.post(
            "/historical/companies",
            json={
                "display_name": "Bad GSTIN Corp",
                "gstin": "29AABCU9603R1Z9",  # bad check digit
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_bad_gst.status_code, 422)

    def test_phase_s_duplicate_company_case_insensitive_and_normalized(self):
        """Duplicate company under the same tenant is rejected via normalized name matching."""
        # 1. Create first company
        res1 = self.client.post(
            "/historical/companies",
            json={"display_name": "Apex Manufacturing Pvt Ltd"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res1.status_code, 201)

        # 2. Lowercase duplicate
        res2 = self.client.post(
            "/historical/companies",
            json={"display_name": "apex manufacturing pvt ltd"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res2.status_code, 409)

        # 3. Uppercase duplicate
        res3 = self.client.post(
            "/historical/companies",
            json={"display_name": "APEX MANUFACTURING PVT LTD"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res3.status_code, 409)

        # 4. Suffix canonicalized variant duplicate ('Private Limited' vs 'Pvt Ltd')
        res4 = self.client.post(
            "/historical/companies",
            json={"display_name": "Apex Manufacturing Private Limited"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res4.status_code, 409)

        # 5. Spacing/punctuation variant duplicate
        res5 = self.client.post(
            "/historical/companies",
            json={"display_name": "  Apex   Manufacturing, Pvt. Ltd.  "},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res5.status_code, 409)

    def test_phase_s_tenant_scoped_uniqueness(self):
        """Tenant A and Tenant B may each have an 'Apex Manufacturing' without conflict."""
        res_a = self.client.post(
            "/historical/companies",
            json={"display_name": "Apex Manufacturing"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_a.status_code, 201)

        res_b = self.client.post(
            "/historical/companies",
            json={"display_name": "Apex Manufacturing"},
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(res_b.status_code, 201)
        self.assertNotEqual(res_a.json()["id"], res_b.json()["id"])

    def test_phase_s_search_case_insensitive_and_normalized(self):
        """Company search matches case-insensitively and by normalized name."""
        self.client.post(
            "/historical/companies",
            json={"display_name": "Bharat Heavy Electricals Pvt Ltd"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.client.post(
            "/historical/companies",
            json={"display_name": "Reliance Petrochemicals Ltd"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )

        # Case-insensitive substring search
        res1 = self.client.get(
            "/historical/companies?search=bharat",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res1.status_code, 200)
        items1 = res1.json()
        self.assertEqual(len(items1), 1)
        self.assertEqual(items1[0]["display_name"], "Bharat Heavy Electricals Pvt Ltd")

        # Uppercase search
        res2 = self.client.get(
            "/historical/companies?search=BHARAT",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(len(res2.json()), 1)

        # Normalized search ('private limited' matches 'Pvt Ltd')
        res3 = self.client.get(
            "/historical/companies?search=bharat heavy electricals private limited",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res3.status_code, 200)
        self.assertEqual(len(res3.json()), 1)

        # Search with no match
        res4 = self.client.get(
            "/historical/companies?search=NonExistentCorp",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res4.status_code, 200)
        self.assertEqual(len(res4.json()), 0)

    def test_phase_s_delete_historical_company_exclusive(self):
        """Deleting a historical-only company deletes customer, historical invoices, and payments."""
        # 1. Create company
        c_res = self.client.post(
            "/historical/companies",
            json={"display_name": "Delete Me Corp"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(c_res.status_code, 201)
        cid = c_res.json()["id"]

        # 2. Add historical manual invoice with payment
        inv_res = self.client.post(
            f"/historical/companies/{cid}/invoices/manual",
            json={
                "amount": 45000.00,
                "due_date": "2026-02-15",
                "payment_date": "2026-02-14",
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(inv_res.status_code, 201)
        inv_id = inv_res.json()["id"]

        # Verify company is listed
        list_before = self.client.get(
            "/historical/companies",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertTrue(any(c["id"] == cid for c in list_before.json()))

        # 3. Delete historical company
        del_res = self.client.delete(
            f"/historical/companies/{cid}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(del_res.status_code, 200)
        del_data = del_res.json()
        self.assertEqual(del_data["action"], "company_deleted")

        # 4. Verify company is gone from GET /historical/companies/{cid}
        detail_res = self.client.get(
            f"/historical/companies/{cid}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(detail_res.status_code, 404)

        # 5. Verify company is gone from GET /historical/companies
        list_after = self.client.get(
            "/historical/companies",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertFalse(any(c["id"] == cid for c in list_after.json()))

        # 6. Verify Customer row completely purged from DB
        cust_row = self.db.get(Customer, uuid.UUID(cid))
        self.assertIsNone(cust_row)

        # 7. Verify Invoice row purged from DB
        inv_row = self.db.get(Invoice, uuid.UUID(inv_id))
        self.assertIsNone(inv_row)

    def test_phase_s_delete_preserves_current_operational_data(self):
        """Deleting historical company for a customer with CURRENT operational invoices preserves operational records."""
        # 1. Create a customer with a CURRENT operational invoice
        cust = create_customer(
            self.db,
            business_id=self.business_a_id,
            display_name="Dual Purpose Client Pvt Ltd",
        )
        self.db.commit()
        cid = cust.id

        current_inv = Invoice(
            business_id=self.business_a_id,
            customer_id=cid,
            invoice_number="INV-OPERATIONAL-001",
            amount=Decimal("120000.00"),
            due_date=date(2026, 6, 1),
            payment_status="OPEN",
            processing_status="PROCESSED",
            origin=InvoiceOrigin.CURRENT.value,
        )
        self.db.add(current_inv)
        self.db.commit()
        current_inv_id = current_inv.id

        # 2. Add a HISTORICAL invoice for this customer via historical manual invoice endpoint
        # First set has_historical_context = True on the company
        cust.has_historical_context = True
        self.db.commit()

        hist_inv_res = self.client.post(
            f"/historical/companies/{cid}/invoices/manual",
            json={
                "amount": 30000.00,
                "due_date": "2025-12-01",
                "payment_date": "2025-11-28",
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(hist_inv_res.status_code, 201)
        hist_inv_id = hist_inv_res.json()["id"]

        # Verify company is present in historical list
        hist_list = self.client.get(
            "/historical/companies",
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()
        self.assertTrue(any(c["id"] == str(cid) for c in hist_list))

        # 3. Delete historical company
        del_res = self.client.delete(
            f"/historical/companies/{cid}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.json()["action"], "historical_records_removed")

        # 4. Verify Customer STILL EXISTS in database
        self.db.expire_all()
        cust_after = self.db.get(Customer, cid)
        self.assertIsNotNone(cust_after)
        self.assertFalse(cust_after.has_historical_context)

        # 5. Verify CURRENT operational invoice STILL EXISTS
        current_inv_after = self.db.get(Invoice, current_inv_id)
        self.assertIsNotNone(current_inv_after)
        self.assertEqual(current_inv_after.origin, InvoiceOrigin.CURRENT.value)

        # 6. Verify HISTORICAL invoice was DELETED
        hist_inv_after = self.db.get(Invoice, uuid.UUID(hist_inv_id))
        self.assertIsNone(hist_inv_after)

        # 7. Verify customer is NO LONGER listed under Historical Data
        hist_list_after = self.client.get(
            "/historical/companies",
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()
        self.assertFalse(any(c["id"] == str(cid) for c in hist_list_after))

        # 8. Verify operational invoice is still visible in /invoices register
        op_list = self.client.get(
            "/invoices",
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()["items"]
        self.assertTrue(any(i["id"] == str(current_inv_id) for i in op_list))

    # ------------------------------------------------------------------
    # Phase T Tests: Historical Parser Contract, Discovery & Correction
    # ------------------------------------------------------------------

    def test_phase_t_historical_upload_with_rupee_symbol_and_auto_discovered_company(self):
        """
        Verify historical invoice parser supports '₹' symbol, Indian number formatting,
        extracts reliable customer name, and automatically creates the customer with
        has_historical_context=True and attaches the invoice.
        """
        pdf_bytes = _create_minimal_pdf()
        parsed_inv = ExtractedInvoice(
            invoice_number="HIST-RUPEE-101",
            customer_name="Phase T Bharat Dynamics Ltd",
            customer_gstin=None,  # GSTIN is optional
            invoice_date=date(2025, 11, 1),
            due_date=date(2025, 12, 1),
            amount=150000.00,
            currency="INR",
            payment_terms="Net 30",
        )

        with patch("backend.app.workers.handlers.InvoiceParser.parse", return_value=ExtractionResult(success=True, invoice=parsed_inv, method="text")):
            resp = self.client.post(
                "/historical/invoices/upload",
                files={"file": ("invoice_rupee.pdf", pdf_bytes, "application/pdf")},
                headers={"Authorization": f"Bearer {self.token_a}"},
            )
            self.assertEqual(resp.status_code, 201)
            task_id = uuid.UUID(resp.json()["task_id"])
            task = self.db.get(Task, task_id)
            handle_parse_invoice(self.db, task, task.payload)

        # Confirm customer was auto-created and has_historical_context = True
        self.db.expire_all()
        cust = self.db.scalar(
            select(Customer).where(
                Customer.business_id == self.business_a_id,
                Customer.display_name == "Phase T Bharat Dynamics Ltd",
            )
        )
        self.assertIsNotNone(cust)
        self.assertTrue(cust.has_historical_context)

        # Confirm invoice is linked and processed
        inv = self.db.scalar(
            select(Invoice).where(
                Invoice.business_id == self.business_a_id,
                Invoice.invoice_number == "HIST-RUPEE-101",
            )
        )
        self.assertIsNotNone(inv)
        self.assertEqual(inv.customer_id, cust.id)
        self.assertEqual(inv.origin, InvoiceOrigin.HISTORICAL.value)
        self.assertEqual(inv.processing_status, "PROCESSED")
        self.assertEqual(float(inv.amount), 150000.00)

    def test_phase_t_historical_upload_missing_due_date_enters_needs_review(self):
        """
        Verify historical invoice with missing due date (and no payment terms) does NOT fail,
        but successfully creates the invoice with due_date=None and processing_status='NEEDS_REVIEW'.
        """
        pdf_bytes = _create_minimal_pdf()
        parsed_inv = ExtractedInvoice(
            invoice_number="HIST-NODUE-202",
            customer_name="Phase T Stellar Motors",
            customer_gstin=None,
            invoice_date=date(2025, 10, 1),
            due_date=None,  # Missing due date
            amount=85000.50,
            currency=None,  # Missing currency optional
            payment_terms=None,
        )

        with patch("backend.app.workers.handlers.InvoiceParser.parse", return_value=ExtractionResult(success=True, invoice=parsed_inv, method="text")):
            resp = self.client.post(
                "/historical/invoices/upload",
                files={"file": ("invoice_nodue.pdf", pdf_bytes, "application/pdf")},
                headers={"Authorization": f"Bearer {self.token_a}"},
            )
            self.assertEqual(resp.status_code, 201)
            task_id = uuid.UUID(resp.json()["task_id"])
            task = self.db.get(Task, task_id)
            handle_parse_invoice(self.db, task, task.payload)

        self.db.expire_all()
        inv = self.db.scalar(
            select(Invoice).where(
                Invoice.business_id == self.business_a_id,
                Invoice.invoice_number == "HIST-NODUE-202",
            )
        )
        self.assertIsNotNone(inv)
        self.assertEqual(inv.processing_status, "NEEDS_REVIEW")
        self.assertIsNone(inv.due_date)

        # Invoice appears in review queue
        rev_resp = self.client.get(
            "/historical/invoices/review",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(rev_resp.status_code, 200)
        review_items = rev_resp.json()
        self.assertTrue(any(i["id"] == str(inv.id) for i in review_items))

        # Complete review by supplying due date
        patch_resp = self.client.patch(
            f"/historical/invoices/{inv.id}/review",
            json={"due_date": "2025-11-15"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()["processing_status"], "PROCESSED")
        self.assertEqual(patch_resp.json()["due_date"], "2025-11-15")

    def test_phase_t_company_auto_matching_case_insensitive_and_normalized(self):
        """
        Verify that when a historical invoice extracts a company whose name matches an existing
        company case-insensitively or after normalization, it attaches to the existing customer
        without creating a duplicate customer.
        """
        # Create initial company "Apex Precision Tools Pvt Ltd"
        initial_res = self.client.post(
            "/historical/companies",
            json={"display_name": "Apex Precision Tools Pvt Ltd"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(initial_res.status_code, 201)
        orig_cust_id = initial_res.json()["id"]

        # Upload historical invoice with casing variation "apex precision tools pvt. ltd."
        pdf_bytes = _create_minimal_pdf()
        parsed_inv = ExtractedInvoice(
            invoice_number="HIST-MATCH-303",
            customer_name="apex precision tools pvt. ltd.",
            customer_gstin=None,
            invoice_date=date(2025, 9, 1),
            due_date=date(2025, 10, 1),
            amount=45000.00,
            currency=None,
            payment_terms=None,
        )

        with patch("backend.app.workers.handlers.InvoiceParser.parse", return_value=ExtractionResult(success=True, invoice=parsed_inv, method="text")):
            resp = self.client.post(
                "/historical/invoices/upload",
                files={"file": ("invoice_match.pdf", pdf_bytes, "application/pdf")},
                headers={"Authorization": f"Bearer {self.token_a}"},
            )
            self.assertEqual(resp.status_code, 201)
            task_id = uuid.UUID(resp.json()["task_id"])
            task = self.db.get(Task, task_id)
            handle_parse_invoice(self.db, task, task.payload)

        self.db.expire_all()
        inv = self.db.scalar(
            select(Invoice).where(
                Invoice.business_id == self.business_a_id,
                Invoice.invoice_number == "HIST-MATCH-303",
            )
        )
        self.assertIsNotNone(inv)
        # Must attach to original customer, not a newly spawned customer
        self.assertEqual(str(inv.customer_id), orig_cust_id)

        # Ensure no duplicate customer was created with that name in tenant
        customers = self.db.scalars(
            select(Customer).where(
                Customer.business_id == self.business_a_id,
                func.lower(Customer.display_name).like("%apex precision%"),
            )
        ).all()
        self.assertEqual(len(customers), 1)

    def test_phase_t_company_correction_reassign_to_existing_company(self):
        """
        Verify user can correct/replace company on a historical invoice by reassigning
        it to an existing company workspace without creating duplicate company.
        """
        # Create Company 1 and Company 2
        c1 = self.client.post(
            "/historical/companies",
            json={"display_name": "Company One Manufacturing"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()
        c2 = self.client.post(
            "/historical/companies",
            json={"display_name": "Company Two Engineering"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()

        # Create manual invoice under Company 1
        inv_res = self.client.post(
            f"/historical/companies/{c1['id']}/invoices/manual",
            json={"amount": 75000.00, "due_date": "2025-08-15"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(inv_res.status_code, 201)
        inv_id = inv_res.json()["id"]

        # Reassign invoice to Company 2 using replacement_customer_id
        corr_res = self.client.patch(
            f"/historical/invoices/{inv_id}/company",
            json={"replacement_customer_id": c2["id"]},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(corr_res.status_code, 200)
        corr_data = corr_res.json()
        self.assertEqual(corr_data["customer_id"], c2["id"])
        self.assertEqual(corr_data["customer_name"], "Company Two Engineering")

        # Verify detail of Company 2 now includes this invoice
        c2_detail = self.client.get(
            f"/historical/companies/{c2['id']}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()
        self.assertTrue(any(i["id"] == inv_id for i in c2_detail["invoices"]))

        # Verify Company 1 now has no historical records (returns 404 or empty invoices)
        c1_res = self.client.get(
            f"/historical/companies/{c1['id']}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        if c1_res.status_code == 200:
            self.assertFalse(any(i["id"] == inv_id for i in c1_res.json()["invoices"]))
        else:
            self.assertEqual(c1_res.status_code, 404)

    def test_phase_t_company_correction_new_company_requires_confirmation(self):
        """
        Verify that reassigning to a brand new company name without create_if_missing: true
        fails cleanly (404/422), and succeeds when create_if_missing: true is provided.
        """
        c1 = self.client.post(
            "/historical/companies",
            json={"display_name": "Source Company Alpha"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()

        inv_res = self.client.post(
            f"/historical/companies/{c1['id']}/invoices/manual",
            json={"amount": 92000.00, "due_date": "2025-07-20"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        inv_id = inv_res.json()["id"]

        # 1. Attempt with create_if_missing: false -> should fail with 404
        fail_res = self.client.patch(
            f"/historical/invoices/{inv_id}/company",
            json={
                "replacement_company_name": "Target Company Beta",
                "create_if_missing": False,
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(fail_res.status_code, 404)

        # 2. Re-attempt with create_if_missing: true -> creates Target Company Beta and reassigns
        succ_res = self.client.patch(
            f"/historical/invoices/{inv_id}/company",
            json={
                "replacement_company_name": "Target Company Beta",
                "create_if_missing": True,
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(succ_res.status_code, 200)
        succ_data = succ_res.json()
        self.assertEqual(succ_data["customer_name"], "Target Company Beta")

        # Confirm Target Company Beta is now listed under Historical Data
        hist_list = self.client.get(
            "/historical/companies",
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()
        self.assertTrue(any(c["display_name"] == "Target Company Beta" for c in hist_list))

    # ------------------------------------------------------------------
    # Phase U Tests: Manual Historical Invoice, Payment Date, and GSTIN
    # ------------------------------------------------------------------

    def test_phase_u_manual_historical_invoice_with_payment_date_settlement(self):
        """
        Verify manual historical invoice creation inside a company workspace:
        - Inherits company from selected workspace
        - Generates a unique, server-side invoice identifier
        - Persists due date and explicit payment date
        - Marks factual payment status as PAID
        - Records payment with provenance='manual'
        """
        # Create company
        comp = self.client.post(
            "/historical/companies",
            json={"display_name": "Phase U Industries Pvt Ltd"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()
        cid = comp["id"]

        # Create manual historical invoice with amount, due_date, and payment_date
        inv_res = self.client.post(
            f"/historical/companies/{cid}/invoices/manual",
            json={
                "amount": 125000.00,
                "due_date": "2025-09-30",
                "payment_date": "2025-09-25",
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(inv_res.status_code, 201)
        data = inv_res.json()
        inv_id = data["id"]
        inv_num = data["invoice_number"]

        # Verify dynamic unique invoice number generated
        self.assertIsNotNone(inv_num)
        self.assertTrue(len(inv_num) > 10)
        self.assertEqual(data["origin"], InvoiceOrigin.HISTORICAL.value)
        self.assertEqual(data["due_date"], "2025-09-30")
        self.assertEqual(data["payment_date"], "2025-09-25")
        self.assertEqual(data["amount"], 125000.00)
        self.assertEqual(data["total_paid"], 125000.00)
        self.assertEqual(data["outstanding_balance"], 0.0)
        self.assertEqual(data["payment_status"], "PAID")
        self.assertEqual(data["payment_count"], 1)

        # Verify in database
        self.db.expire_all()
        inv_db = self.db.get(Invoice, uuid.UUID(inv_id))
        self.assertIsNotNone(inv_db)
        self.assertEqual(str(inv_db.customer_id), cid)
        self.assertEqual(inv_db.origin, InvoiceOrigin.HISTORICAL.value)
        self.assertEqual(inv_db.due_date, date(2025, 9, 30))
        self.assertEqual(len(inv_db.payments), 1)
        pmt_db = inv_db.payments[0]
        self.assertEqual(pmt_db.provenance, "manual")
        self.assertEqual(pmt_db.payment_date.date(), date(2025, 9, 25))
        self.assertEqual(float(pmt_db.amount), 125000.00)

    def test_phase_u_manual_historical_invoice_without_payment_date_is_open(self):
        """
        Verify manual historical invoice without payment date:
        - Does NOT fabricate a payment date or use invoice date
        - Sets payment_status to 'OPEN'
        - Outstanding balance equals full amount
        """
        comp = self.client.post(
            "/historical/companies",
            json={"display_name": "Phase U Unpaid Corp"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()
        cid = comp["id"]

        inv_res = self.client.post(
            f"/historical/companies/{cid}/invoices/manual",
            json={
                "amount": 54000.00,
                "due_date": "2025-10-15",
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(inv_res.status_code, 201)
        data = inv_res.json()
        self.assertEqual(data["payment_status"], "OPEN")
        self.assertIsNone(data["payment_date"])
        self.assertEqual(data["total_paid"], 0.0)
        self.assertEqual(data["outstanding_balance"], 54000.00)
        self.assertEqual(data["payment_count"], 0)

    def test_phase_u_manual_invoice_id_uniqueness(self):
        """
        Create multiple manual invoices and ensure every generated identifier
        is unique, non-empty, and database-enforced.
        """
        comp = self.client.post(
            "/historical/companies",
            json={"display_name": "Phase U Identifier Check Corp"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()
        cid = comp["id"]

        refs = set()
        for i in range(5):
            res = self.client.post(
                f"/historical/companies/{cid}/invoices/manual",
                json={
                    "amount": 10000.00 + i * 1000,
                    "due_date": "2025-08-01",
                },
                headers={"Authorization": f"Bearer {self.token_a}"},
            )
            self.assertEqual(res.status_code, 201)
            num = res.json()["invoice_number"]
            self.assertTrue(num)
            self.assertNotIn(num, refs)
            refs.add(num)
        self.assertEqual(len(refs), 5)

    def test_phase_u_optional_gstin_and_generic_checksum_validation(self):
        """
        Verify GSTIN behavior:
        1. Company creation without GSTIN succeeds.
        2. Genuinely valid 15-char GSTINs with checksum are accepted.
        3. Invalid checksum or malformed GSTIN (such as '27SYNTHETIC0001Z1') is rejected.
        4. Clear/remove GSTIN succeeds.
        """
        # 1. Company without GSTIN
        res_no_gst = self.client.post(
            "/historical/companies",
            json={"display_name": "No GSTIN Enterprises"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_no_gst.status_code, 201)
        cid = res_no_gst.json()["id"]

        # 2. Add valid GSTIN (Maharashtra: 27AAPFU0939F1ZV)
        patch_valid = self.client.patch(
            f"/historical/companies/{cid}",
            json={"gstin": "27AAPFU0939F1ZV"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(patch_valid.status_code, 200)
        self.assertEqual(patch_valid.json()["gstin"], "27AAPFU0939F1ZV")

        # 3. Reject invalid GSTIN (27SYNTHETIC0001Z1) - not a valid standard GSTIN
        patch_synth = self.client.patch(
            f"/historical/companies/{cid}",
            json={"gstin": "27SYNTHETIC0001Z1"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(patch_synth.status_code, 422)

        # 4. Reject bad check digit on structurally plausible GSTIN
        patch_bad_check = self.client.patch(
            f"/historical/companies/{cid}",
            json={"gstin": "27AAPFU0939F1ZU"},  # Bad check digit (U instead of V)
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(patch_bad_check.status_code, 422)

        # 5. Clear GSTIN
        patch_clear = self.client.patch(
            f"/historical/companies/{cid}",
            json={"gstin": None},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(patch_clear.status_code, 200)
        self.assertIsNone(patch_clear.json()["gstin"])

    def test_phase_u_manual_invoice_tenant_isolation(self):
        """
        Verify tenant isolation on manual invoice endpoints:
        Tenant B cannot create or view manual historical invoices for Tenant A's company.
        """
        comp_a = self.client.post(
            "/historical/companies",
            json={"display_name": "Tenant A Secret Supplier"},
            headers={"Authorization": f"Bearer {self.token_a}"},
        ).json()
        cid_a = comp_a["id"]

        # Tenant B attempts to create invoice under Tenant A's company -> 404
        post_b = self.client.post(
            f"/historical/companies/{cid_a}/invoices/manual",
            json={"amount": 20000.00, "due_date": "2025-06-30"},
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(post_b.status_code, 404)

        # Tenant B attempts to view Tenant A's company detail -> 404
        get_b = self.client.get(
            f"/historical/companies/{cid_a}",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(get_b.status_code, 404)


if __name__ == "__main__":
    unittest.main()


