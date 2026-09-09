"""
Tests for Invoice and InvoiceDocument ingestion endpoints.
Verifies PDF upload, file validation, storage integration, and strict tenant isolation.
"""

import datetime
import io
import unittest
import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.models.user import User
from backend.app.services.storage import get_storage


class TestInvoiceIngestion(unittest.TestCase):
    """Test suite for invoice PDF upload and invoice listing endpoints."""

    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        self.storage = get_storage()

        # Register Business A (User A)
        self.suffix_a = uuid.uuid4().hex[:8]
        res_a = self.client.post("/auth/register", json={
            "email": f"inva_{self.suffix_a}@corp.com",
            "password": "Password123!",
            "full_name": "Invoice Admin A",
            "business_name": f"Invoice Corp A {self.suffix_a}",
        })
        self.assertEqual(res_a.status_code, 201)
        data_a = res_a.json()
        self.token_a = data_a["access_token"]
        self.user_a_id = uuid.UUID(data_a["user"]["id"])
        self.business_a_id = uuid.UUID(data_a["business"]["id"])

        # Register Business B (User B)
        self.suffix_b = uuid.uuid4().hex[:8]
        res_b = self.client.post("/auth/register", json={
            "email": f"invb_{self.suffix_b}@corp.com",
            "password": "Password123!",
            "full_name": "Invoice Admin B",
            "business_name": f"Invoice Corp B {self.suffix_b}",
        })
        self.assertEqual(res_b.status_code, 201)
        data_b = res_b.json()
        self.token_b = data_b["access_token"]
        self.user_b_id = uuid.UUID(data_b["user"]["id"])
        self.business_b_id = uuid.UUID(data_b["business"]["id"])

        self.uploaded_keys = []

    def tearDown(self):
        """Clean up database records and stored files."""
        # Clean storage
        for k in self.uploaded_keys:
            try:
                self.storage.delete(k)
            except Exception:
                pass

        # Clean DB
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

    def test_upload_valid_pdf_invoice(self):
        """Authenticated upload of a valid PDF succeeds and persists InvoiceDocument in PENDING state."""
        pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"
        files = {"file": ("test_invoice.pdf", io.BytesIO(pdf_content), "application/pdf")}

        response = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()

        self.assertIn("document_id", data)
        self.assertEqual(data["original_filename"], "test_invoice.pdf")
        self.assertEqual(data["file_size"], len(pdf_content))
        self.assertEqual(data["processing_status"], "PENDING")
        self.assertIsNone(data["invoice_id"])

        doc_id = uuid.UUID(data["document_id"])

        # Verify database record
        doc = self.db.get(InvoiceDocument, doc_id)
        self.assertIsNotNone(doc)
        self.assertEqual(doc.business_id, self.business_a_id)
        self.assertEqual(doc.processing_status, "PENDING")
        self.uploaded_keys.append(doc.storage_key)

        # Verify storage content
        self.assertTrue(self.storage.exists(doc.storage_key))
        stored_bytes = self.storage.read(doc.storage_key)
        self.assertEqual(stored_bytes, pdf_content)

    def test_upload_unauthenticated_rejected(self):
        """Uploading without authentication returns 401 Unauthorized."""
        pdf_content = b"%PDF-1.4 test content"
        files = {"file": ("invoice.pdf", io.BytesIO(pdf_content), "application/pdf")}
        response = self.client.post("/invoices/upload", files=files)
        self.assertEqual(response.status_code, 401)

    def test_upload_non_pdf_rejected(self):
        """Uploading a non-PDF file extension is rejected with 400 Bad Request."""
        text_content = b"Not a PDF file at all"
        files = {"file": ("malicious.exe", io.BytesIO(text_content), "application/octet-stream")}

        response = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only PDF", response.json()["detail"])

    def test_upload_corrupted_pdf_missing_header_rejected(self):
        """Uploading a .pdf file without '%PDF' magic bytes is rejected with 400."""
        fake_content = b"This file has a .pdf extension but lacks the magic PDF signature."
        files = {"file": ("corrupt.pdf", io.BytesIO(fake_content), "application/pdf")}

        response = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing standard '%PDF' header", response.json()["detail"])

    def test_upload_empty_file_rejected(self):
        """Uploading an empty file is rejected with 400 Bad Request."""
        files = {"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")}

        response = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.json()["detail"].lower())

    def test_upload_oversized_file_rejected(self):
        """Uploading a file exceeding configured max size returns 413 Payload Too Large."""
        # 11 MB content (exceeds default 10MB limit)
        oversized = b"%PDF-1.4" + (b"0" * (11 * 1024 * 1024))
        files = {"file": ("huge.pdf", io.BytesIO(oversized), "application/pdf")}

        response = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("exceeds maximum allowed limit", response.json()["detail"])

    def test_list_and_download_documents(self):
        """User can list uploaded document metadata and download file contents."""
        pdf_content = b"%PDF-1.4 sample invoice"
        files = {"file": ("sample.pdf", io.BytesIO(pdf_content), "application/pdf")}

        upload_res = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(upload_res.status_code, 201)
        doc_id = upload_res.json()["document_id"]
        doc = self.db.get(InvoiceDocument, uuid.UUID(doc_id))
        self.uploaded_keys.append(doc.storage_key)

        # List documents
        list_res = self.client.get(
            "/invoices/documents",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(list_res.status_code, 200)
        docs = list_res.json()
        self.assertGreaterEqual(len(docs), 1)
        self.assertEqual(docs[0]["id"], doc_id)
        self.assertNotIn("storage_key", docs[0])  # Must not expose raw storage path

        # Download document
        download_res = self.client.get(
            f"/invoices/documents/{doc_id}",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(download_res.status_code, 200)
        self.assertEqual(download_res.content, pdf_content)
        self.assertEqual(download_res.headers["content-type"], "application/pdf")

    def test_tenant_isolation_on_documents_and_invoices(self):
        """Tenant A documents/invoices cannot be viewed or downloaded by Tenant B."""
        # User A uploads document
        pdf_a = b"%PDF-1.4 Business A confidential invoice"
        upload_a = self.client.post(
            "/invoices/upload",
            files={"file": ("doc_a.pdf", io.BytesIO(pdf_a), "application/pdf")},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        doc_a_id = upload_a.json()["document_id"]
        doc_a = self.db.get(InvoiceDocument, uuid.UUID(doc_a_id))
        self.uploaded_keys.append(doc_a.storage_key)

        # Seed Invoice in Business A
        cust_a = Customer(business_id=self.business_a_id, name="Client A", customer_ref="C-A")
        self.db.add(cust_a)
        self.db.flush()
        inv_a = Invoice(
            business_id=self.business_a_id,
            customer_id=cust_a.id,
            invoice_number="INV-A-999",
            invoice_date=datetime.date(2025, 1, 1),
            due_date=datetime.date(2025, 2, 1),
            amount=12000.00,
            currency="USD",
            payment_status="OPEN",
            processing_status="PENDING",
        )
        self.db.add(inv_a)
        self.db.commit()

        # 1. User B attempts to download User A's document
        download_b = self.client.get(
            f"/invoices/documents/{doc_a_id}",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(download_b.status_code, 404, "User B must receive 404 for User A document")

        # 2. User B lists documents -> must not include doc_a
        list_b = self.client.get(
            "/invoices/documents",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(list_b.status_code, 200)
        doc_ids_b = [d["id"] for d in list_b.json()]
        self.assertNotIn(doc_a_id, doc_ids_b)

        # 3. User B attempts to get User A's invoice
        inv_b_res = self.client.get(
            f"/invoices/{inv_a.id}",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(inv_b_res.status_code, 404, "User B must receive 404 for User A invoice")

        # 4. User B lists invoices -> must not include inv_a
        inv_list_b = self.client.get(
            "/invoices",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(inv_list_b.status_code, 200)
        inv_ids_b = [i["id"] for i in inv_list_b.json()["items"]]
        self.assertNotIn(str(inv_a.id), inv_ids_b)


if __name__ == "__main__":
    unittest.main()
