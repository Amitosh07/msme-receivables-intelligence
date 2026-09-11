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


if __name__ == "__main__":
    unittest.main()
