"""
Integration tests for Worker Service, TaskQueue, and asynchronous invoice processing.
Tests end-to-end task execution, idempotency, retry, error handling, tenant isolation,
and historical payment reconciliation.
"""

import io
import unittest
import uuid
from datetime import date, datetime, timezone
from unittest.mock import patch
import fitz
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.models.payment import Payment
from backend.app.models.task import Task
from backend.app.models.user import User
from backend.app.services.parser.base import TransientParserError
from backend.app.services.storage import get_storage
from backend.app.services.task_service import create_task
from backend.app.workers.queue import get_task_queue
from backend.app.workers.router import TaskRouter
from backend.app.workers.runtime import WorkerService


def _create_sample_pdf(text: str) -> bytes:
    """Generate in-memory PDF with specified text."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), text, fontsize=11)
    return doc.tobytes()


class TestWorkerService(unittest.TestCase):
    """Test suite for WorkerService and invoice parsing pipeline."""

    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        self.storage = get_storage()
        self.queue = get_task_queue()
        self.worker = WorkerService(queue=self.queue)

        # Clear Redis queue before each test
        self.queue.clear()

        # Register Business A (User A)
        self.suffix_a = uuid.uuid4().hex[:8]
        res_a = self.client.post("/auth/register", json={
            "email": f"worker_a_{self.suffix_a}@corp.com",
            "password": "Password123!",
            "full_name": "Worker Admin A",
            "business_name": f"Worker Corp A {self.suffix_a}",
        })
        self.assertEqual(res_a.status_code, 201)
        data_a = res_a.json()
        self.token_a = data_a["access_token"]
        self.user_a_id = uuid.UUID(data_a["user"]["id"])
        self.business_a_id = uuid.UUID(data_a["business"]["id"])

        # Register Business B (User B)
        self.suffix_b = uuid.uuid4().hex[:8]
        res_b = self.client.post("/auth/register", json={
            "email": f"worker_b_{self.suffix_b}@corp.com",
            "password": "Password123!",
            "full_name": "Worker Admin B",
            "business_name": f"Worker Corp B {self.suffix_b}",
        })
        self.assertEqual(res_b.status_code, 201)
        data_b = res_b.json()
        self.token_b = data_b["access_token"]
        self.user_b_id = uuid.UUID(data_b["user"]["id"])
        self.business_b_id = uuid.UUID(data_b["business"]["id"])

        self.uploaded_keys = []

    def tearDown(self):
        """Clean up storage, queue, and database records."""
        self.queue.clear()

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
            self.db.commit()
        except Exception:
            self.db.rollback()
        finally:
            self.db.close()

    def test_end_to_end_invoice_processing(self):
        """
        Uploads PDF invoice -> creates Task -> enqueues to Redis ->
        Worker dequeues, claims, parses, creates Invoice, links Customer,
        and marks Task COMPLETED.
        """
        invoice_text = (
            "TAX INVOICE\n"
            "Invoice #: INV-WKR-101\n"
            "Bill To: Global Logistics Inc\n"
            "Customer ID: CUST-GL-01\n"
            "Invoice Date: 2025-05-01\n"
            "Due Date: 2025-05-31\n"
            "Payment Terms: Net 30\n"
            "Total Amount: $18,500.00\n"
        )
        pdf_bytes = _create_sample_pdf(invoice_text)
        files = {"file": ("inv_101.pdf", io.BytesIO(pdf_bytes), "application/pdf")}

        # 1. Upload through API
        response = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        doc_id = uuid.UUID(data["document_id"])
        task_id = uuid.UUID(data["task_id"])

        # Track storage key for cleanup
        doc = self.db.get(InvoiceDocument, doc_id)
        self.assertIsNotNone(doc)
        self.uploaded_keys.append(doc.storage_key)

        # 2. Verify task is enqueued
        self.assertEqual(self.queue.size(), 1)

        # 3. Worker processes the task
        processed = self.worker.process_one_task(timeout=1)
        self.assertTrue(processed)
        self.assertEqual(self.queue.size(), 0)

        # 4. Verify Task state in PostgreSQL
        self.db.expire_all()
        task = self.db.get(Task, task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.status, "COMPLETED")
        self.assertIsNotNone(task.invoice_id)
        self.assertIsNotNone(task.completed_at)

        # 5. Verify InvoiceDocument state
        doc = self.db.get(InvoiceDocument, doc_id)
        self.assertEqual(doc.processing_status, "PROCESSED")
        self.assertEqual(doc.invoice_id, task.invoice_id)

        # 6. Verify created Invoice record
        invoice = self.db.get(Invoice, task.invoice_id)
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.invoice_number, "INV-WKR-101")
        self.assertEqual(invoice.amount, 18500.00)
        self.assertEqual(invoice.currency, "USD")
        self.assertEqual(invoice.invoice_date, date(2025, 5, 1))
        self.assertEqual(invoice.due_date, date(2025, 5, 31))
        self.assertEqual(invoice.payment_status, "OPEN")
        self.assertEqual(invoice.business_id, self.business_a_id)

        # 7. Verify Customer was linked
        customer = self.db.get(Customer, invoice.customer_id)
        self.assertIsNotNone(customer)
        self.assertEqual(customer.name, "Global Logistics Inc")
        self.assertEqual(customer.customer_ref, "CUST-GL-01")

    def test_idempotent_duplicate_task(self):
        """Re-running task on already processed document does not create duplicate invoice."""
        invoice_text = (
            "TAX INVOICE\n"
            "Invoice #: INV-IDEM-001\n"
            "Bill To: Apex Corp\n"
            "Date: 2025-05-01\n"
            "Due Date: 2025-05-31\n"
            "Total Amount: $5,000.00\n"
        )
        pdf_bytes = _create_sample_pdf(invoice_text)
        files = {"file": ("inv_idem.pdf", io.BytesIO(pdf_bytes), "application/pdf")}

        res = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res.status_code, 201)
        doc_id = uuid.UUID(res.json()["document_id"])
        doc = self.db.get(InvoiceDocument, doc_id)
        self.uploaded_keys.append(doc.storage_key)

        # Process first time
        self.worker.process_one_task(timeout=1)

        # Count invoices
        invoices_count = len(list(self.db.scalars(
            select(Invoice).where(Invoice.invoice_number == "INV-IDEM-001")
        )))
        self.assertEqual(invoices_count, 1)

        # Create a second task pointing to the same document and process it
        task_2 = create_task(
            self.db,
            business_id=self.business_a_id,
            task_type="parse_invoice",
            payload={"invoice_document_id": str(doc_id)},
        )
        self.queue.enqueue(
            task_id=task_2.id,
            task_type=task_2.task_type,
            business_id=task_2.business_id,
            payload=task_2.payload,
        )
        self.worker.process_one_task(timeout=1)

        # Verify task completed and still only 1 invoice exists
        self.db.expire_all()
        t2_db = self.db.get(Task, task_2.id)
        self.assertEqual(t2_db.status, "COMPLETED")

        invoices_count_after = len(list(self.db.scalars(
            select(Invoice).where(Invoice.invoice_number == "INV-IDEM-001")
        )))
        self.assertEqual(invoices_count_after, 1)

    def test_payment_reconciliation_on_parse(self):
        """
        When an invoice is parsed and matching historical payments exist
        (by invoice_reference), they are linked and invoice marked PAID.
        """
        # 1. Pre-insert an unmatched Payment
        payment = Payment(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            invoice_reference="INV-RECON-777",
            amount=9800.00,
            payment_date=datetime.now(timezone.utc),
            reference="WIRE-9921",
            invoice_id=None,
        )
        self.db.add(payment)
        self.db.commit()

        # 2. Upload invoice matching that reference
        invoice_text = (
            "TAX INVOICE\n"
            "Invoice #: INV-RECON-777\n"
            "Bill To: Reconciled Partner Ltd\n"
            "Date: 2025-05-10\n"
            "Due Date: 2025-06-10\n"
            "Total Amount: $9,800.00\n"
        )
        pdf_bytes = _create_sample_pdf(invoice_text)
        files = {"file": ("inv_recon.pdf", io.BytesIO(pdf_bytes), "application/pdf")}

        res = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res.status_code, 201)
        doc = self.db.get(InvoiceDocument, uuid.UUID(res.json()["document_id"]))
        self.uploaded_keys.append(doc.storage_key)

        # 3. Process task
        self.worker.process_one_task(timeout=1)

        # 4. Verify Payment is reconciled to Invoice and status is PAID
        self.db.expire_all()
        pmt_db = self.db.get(Payment, payment.id)
        self.assertIsNotNone(pmt_db.invoice_id)

        inv_db = self.db.get(Invoice, pmt_db.invoice_id)
        self.assertIsNotNone(inv_db)
        self.assertEqual(inv_db.invoice_number, "INV-RECON-777")
        self.assertEqual(inv_db.payment_status, "PAID")

    def test_permanent_parser_failure_on_blank_pdf(self):
        """Unparseable blank PDF permanently marks task FAILED and document ERROR (no endless retry)."""
        doc = fitz.open()
        doc.new_page()  # blank page
        pdf_bytes = doc.tobytes()

        files = {"file": ("blank.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
        res = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        doc_id = uuid.UUID(data["document_id"])
        task_id = uuid.UUID(data["task_id"])

        inv_doc = self.db.get(InvoiceDocument, doc_id)
        self.uploaded_keys.append(inv_doc.storage_key)

        # Process task
        self.worker.process_one_task(timeout=1)

        # Verify permanent failure states
        self.db.expire_all()
        task = self.db.get(Task, task_id)
        self.assertEqual(task.status, "FAILED")
        self.assertIsNotNone(task.error_message)

        inv_doc = self.db.get(InvoiceDocument, doc_id)
        self.assertEqual(inv_doc.processing_status, "ERROR")

    def test_transient_retry_handling(self):
        """Simulated transient exception resets task to PENDING and re-enqueues."""
        invoice_text = (
            "TAX INVOICE\n"
            "Invoice #: INV-RETRY-01\n"
            "Bill To: Retry Corp\n"
            "Date: 2025-05-01\n"
            "Due Date: 2025-05-31\n"
            "Total Amount: $2,000.00\n"
        )
        pdf_bytes = _create_sample_pdf(invoice_text)
        files = {"file": ("inv_retry.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
        res = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        task_id = uuid.UUID(res.json()["task_id"])
        doc = self.db.get(InvoiceDocument, uuid.UUID(res.json()["document_id"]))
        self.uploaded_keys.append(doc.storage_key)

        # Mock TaskRouter.dispatch to simulate transient network/storage error
        with patch.object(TaskRouter, "dispatch", side_effect=TransientParserError("Simulated S3 timeout")):
            self.worker.process_one_task(timeout=1)

        # Task should remain in PENDING with attempt count 2
        self.db.expire_all()
        task = self.db.get(Task, task_id)
        self.assertEqual(task.status, "PENDING")
        self.assertEqual(task.payload.get("attempt"), 2)
        # Should be re-enqueued
        self.assertEqual(self.queue.size(), 1)

    def test_tenant_boundary_isolation(self):
        """Worker rejects processing when task business_id does not match document business_id."""
        invoice_text = (
            "TAX INVOICE\n"
            "Invoice #: INV-TENANT-01\n"
            "Bill To: Tenant Safe Corp\n"
            "Date: 2025-05-01\n"
            "Due Date: 2025-05-31\n"
            "Total Amount: $3,000.00\n"
        )
        pdf_bytes = _create_sample_pdf(invoice_text)
        files = {"file": ("inv_tenant.pdf", io.BytesIO(pdf_bytes), "application/pdf")}

        # Business A uploads document
        res = self.client.post(
            "/invoices/upload",
            files=files,
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        doc_a_id = uuid.UUID(res.json()["document_id"])
        task_a_id = uuid.UUID(res.json()["task_id"])
        doc_a = self.db.get(InvoiceDocument, doc_a_id)
        self.uploaded_keys.append(doc_a.storage_key)

        # Clear queue so we control dispatch
        self.queue.clear()

        # Malicious/buggy task: Belongs to Business B, but points to doc_a_id
        bad_task = create_task(
            self.db,
            business_id=self.business_b_id,
            task_type="parse_invoice",
            payload={"invoice_document_id": str(doc_a_id)},
        )
        self.queue.enqueue(
            task_id=bad_task.id,
            task_type=bad_task.task_type,
            business_id=bad_task.business_id,
            payload=bad_task.payload,
        )

        # Worker processes bad task
        self.worker.process_one_task(timeout=1)

        # Verify bad task failed with tenant boundary error
        self.db.expire_all()
        bad_task_db = self.db.get(Task, bad_task.id)
        self.assertEqual(bad_task_db.status, "FAILED")
        self.assertIn("Tenant boundary violation", bad_task_db.error_message)

        # Business A document must remain unaffected
        doc_a_db = self.db.get(InvoiceDocument, doc_a_id)
        self.assertEqual(doc_a_db.processing_status, "PENDING")

    def test_startup_recovery_of_pending_tasks(self):
        """Worker startup re-enqueues pending tasks from PostgreSQL that were not in Redis."""
        # Insert 2 pending tasks in DB directly without enqueueing
        task1 = create_task(self.db, self.business_a_id, "parse_invoice", {"test": 1})
        task2 = create_task(self.db, self.business_a_id, "parse_invoice", {"test": 2})

        self.queue.clear()
        self.assertEqual(self.queue.size(), 0)

        # Call startup recovery
        recovered = self.worker.recover_pending_tasks(self.db)
        self.assertGreaterEqual(recovered, 2)
        self.assertGreaterEqual(self.queue.size(), 2)


if __name__ == "__main__":
    unittest.main()
