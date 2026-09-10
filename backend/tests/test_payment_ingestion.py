"""
Tests for Payment history CSV ingestion endpoint.
Verifies column validation, format parsing, duplicate rejection, invoice matching, and tenant isolation.
"""

import datetime
import io
import unittest
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.payment import Payment
from backend.app.models.user import User


class TestPaymentIngestion(unittest.TestCase):
    """Test suite for payment history CSV import."""

    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()

        # Register Business A (User A)
        self.suffix_a = uuid.uuid4().hex[:8]
        res_a = self.client.post("/auth/register", json={
            "email": f"pay_a_{self.suffix_a}@corp.com",
            "password": "Password123!",
            "full_name": "Payment User A",
            "business_name": f"Payment Corp A {self.suffix_a}",
        })
        self.assertEqual(res_a.status_code, 201)
        data_a = res_a.json()
        self.token_a = data_a["access_token"]
        self.user_a_id = uuid.UUID(data_a["user"]["id"])
        self.business_a_id = uuid.UUID(data_a["business"]["id"])

        # Register Business B (User B)
        self.suffix_b = uuid.uuid4().hex[:8]
        res_b = self.client.post("/auth/register", json={
            "email": f"pay_b_{self.suffix_b}@corp.com",
            "password": "Password123!",
            "full_name": "Payment User B",
            "business_name": f"Payment Corp B {self.suffix_b}",
        })
        self.assertEqual(res_b.status_code, 201)
        data_b = res_b.json()
        self.token_b = data_b["access_token"]
        self.user_b_id = uuid.UUID(data_b["user"]["id"])
        self.business_b_id = uuid.UUID(data_b["business"]["id"])

    def tearDown(self):
        """Clean up test records."""
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

    def test_import_valid_payment_csv_with_matching(self):
        """Importing valid CSV correctly matches existing invoices and records unmatched payments."""
        # 1. Create Customer and Invoice in Business A
        cust = Customer(business_id=self.business_a_id, name="Acme Inc", customer_ref="CUST-100")
        self.db.add(cust)
        self.db.flush()

        inv = Invoice(
            business_id=self.business_a_id,
            customer_id=cust.id,
            invoice_number="INV-MATCH-01",
            invoice_date=datetime.date(2025, 1, 10),
            due_date=datetime.date(2025, 2, 10),
            amount=15000.00,
            currency="USD",
            payment_status="OPEN",
            processing_status="PROCESSED",
        )
        self.db.add(inv)
        self.db.commit()

        # 2. Prepare CSV with 1 matched invoice, 1 unmatched invoice, 1 with customer ref
        csv_content = (
            "invoice_number,payment_date,payment_amount,reference,customer_reference\n"
            "INV-MATCH-01,2025-02-08,15000.00,WIRE-001,CUST-100\n"
            "INV-UNMATCHED-99,2025-02-12,8500.50,ACH-002,CUST-NEW-200\n"
        ).encode("utf-8")

        files = {"file": ("payments.csv", io.BytesIO(csv_content), "text/csv")}
        response = self.client.post(
            "/payments/import",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total_rows"], 2)
        self.assertEqual(data["imported"], 2)   # Both valid rows were persisted
        self.assertEqual(data["matched"], 1)
        self.assertEqual(data["unmatched"], 1)  # Unmatched invoice retained
        self.assertEqual(data["duplicates"], 0)
        self.assertEqual(data["rejected"], 0)

        # Verify matched payment in DB
        matched_payment = self.db.scalar(
            select(Payment).where(
                Payment.business_id == self.business_a_id,
                Payment.invoice_reference == "INV-MATCH-01",
            )
        )
        self.assertIsNotNone(matched_payment)
        self.assertEqual(matched_payment.invoice_id, inv.id)
        self.assertEqual(float(matched_payment.amount), 15000.00)

        # Verify invoice payment_status updated to PAID
        db_inv = self.db.get(Invoice, inv.id)
        self.assertEqual(db_inv.payment_status, "PAID")

        # Verify unmatched payment retained in DB without fake invoice
        unmatched_payment = self.db.scalar(
            select(Payment).where(
                Payment.business_id == self.business_a_id,
                Payment.invoice_reference == "INV-UNMATCHED-99",
            )
        )
        self.assertIsNotNone(unmatched_payment)
        self.assertIsNone(unmatched_payment.invoice_id)
        self.assertEqual(float(unmatched_payment.amount), 8500.50)

        # Verify auto-created customer for CUST-NEW-200
        new_cust = self.db.scalar(
            select(Customer).where(
                Customer.business_id == self.business_a_id,
                Customer.customer_ref == "CUST-NEW-200",
            )
        )
        self.assertIsNotNone(new_cust)

    def test_import_missing_required_headers_rejected(self):
        """CSV missing required columns returns 400 Bad Request."""
        csv_content = "invoice_number,notes\nINV-001,Some note\n".encode("utf-8")
        files = {"file": ("bad_headers.csv", io.BytesIO(csv_content), "text/csv")}

        response = self.client.post(
            "/payments/import",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("missing required columns", response.json()["detail"].lower())

    def test_import_invalid_rows_reported(self):
        """Rows with unparseable dates or non-positive amounts are rejected and reported."""
        csv_content = (
            "invoice_number,payment_date,amount\n"
            "INV-OK,2025-03-01,1000.00\n"
            "INV-BAD-DATE,invalid-date,500.00\n"
            "INV-NEG-AMOUNT,2025-03-02,-100.00\n"
            "INV-ZERO-AMOUNT,2025-03-03,0.00\n"
            "INV-NAN-AMOUNT,2025-03-04,not-a-number\n"
        ).encode("utf-8")

        files = {"file": ("invalid_rows.csv", io.BytesIO(csv_content), "text/csv")}
        response = self.client.post(
            "/payments/import",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total_rows"], 5)
        self.assertEqual(data["unmatched"], 1)  # INV-OK retained
        self.assertEqual(data["rejected"], 4)   # 4 bad rows
        self.assertEqual(len(data["errors"]), 4)

    def test_duplicate_payments_detected_and_skipped(self):
        """Duplicate rows within CSV or across multiple uploads are not re-inserted."""
        csv_content = (
            "invoice_number,payment_date,amount\n"
            "INV-DUP-1,2025-04-01,2500.00\n"
            "INV-DUP-1,2025-04-01,2500.00\n"  # Duplicate within file
        ).encode("utf-8")

        # First upload
        files1 = {"file": ("dups.csv", io.BytesIO(csv_content), "text/csv")}
        res1 = self.client.post(
            "/payments/import",
            files=files1,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertEqual(data1["total_rows"], 2)
        self.assertEqual(data1["unmatched"], 1)
        self.assertEqual(data1["duplicates"], 1)

        # Second upload of the same file -> both should be identified as duplicates
        files2 = {"file": ("dups.csv", io.BytesIO(csv_content), "text/csv")}
        res2 = self.client.post(
            "/payments/import",
            files=files2,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2["total_rows"], 2)
        self.assertEqual(data2["unmatched"], 0)
        self.assertEqual(data2["duplicates"], 2)

    def test_tenant_isolation_on_payments(self):
        """Payments imported by Tenant A are never accessible or visible to Tenant B."""
        csv_a = "invoice_number,payment_date,amount\nINV-A-PAY,2025-05-01,3000.00\n".encode("utf-8")
        self.client.post(
            "/payments/import",
            files={"file": ("pay_a.csv", io.BytesIO(csv_a), "text/csv")},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )

        # Query database for Tenant B payments
        b_payments = self.db.scalars(
            select(Payment).where(Payment.business_id == self.business_b_id)
        ).all()
        self.assertEqual(len(b_payments), 0, "Tenant B must have 0 payments")

    def test_non_csv_upload_rejected(self):
        """Uploading a non-CSV file to /payments/import is rejected with 400 Bad Request."""
        files = {"file": ("report.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")}
        response = self.client.post(
            "/payments/import",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only CSV", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
