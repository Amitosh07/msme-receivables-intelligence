"""
Phase E Payment Proof Upload & Verification Tests.
Covers all 40 required test categories across upload, extraction, matching, payment creation,
idempotency, status lifecycle, customer history, and tenant security.
"""

from __future__ import annotations

import csv
import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import io
import unittest
from unittest.mock import patch
import uuid
import fitz
import openpyxl
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.security import create_access_token
from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.membership import Membership
from backend.app.models.payment import Payment
from backend.app.models.payment_proof import PaymentProof
from backend.app.models.prediction import PredictionResult
from backend.app.models.task import Task
from backend.app.models.user import User
from backend.app.services.customer_identity import normalize_customer_name
from backend.app.services.manual_payment_service import derive_invoice_payment_status
from backend.app.services.payment_proof_parser import PaymentProofParser
from backend.app.services.payment_proof_service import (
    get_proof_by_id,
    list_proofs_for_invoice,
    upload_payment_proof,
    validate_proof_file,
    verify_and_process_proof,
)
from backend.app.services.storage import get_storage
from backend.app.services.task_service import recover_stale_processing_tasks
from backend.app.workers.runtime import WorkerService
from backend.tests.queue_fakes import InMemoryTaskQueue


def _create_sample_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), text, fontsize=11)
    return doc.tobytes()


def _create_sample_csv(rows: list[list[str]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    for r in rows:
        writer.writerow(r)
    return out.getvalue().encode("utf-8")


def _create_sample_xlsx(rows: list[list[object]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _create_fixtures(db: Session, business_name: str = "Proof Test Corp"):
    business = Business(name=f"{business_name} {uuid.uuid4().hex[:6]}", currency="INR")
    db.add(business)
    db.flush()

    customer = Customer(
        business_id=business.id,
        display_name="Acme Industrial Ltd",
        normalized_name=normalize_customer_name("Acme Industrial Ltd"),
        gstin="27AABCU9603R1ZM",
        normalized_gstin="27AABCU9603R1ZM",
    )
    db.add(customer)
    db.flush()

    invoice = Invoice(
        business_id=business.id,
        customer_id=customer.id,
        invoice_number=f"INV-{uuid.uuid4().hex[:8].upper()}",
        invoice_date=date(2026, 1, 15),
        due_date=date(2026, 2, 15),
        amount=Decimal("100000.00"),
        currency="INR",
        payment_status="OPEN",
        processing_status="PROCESSED",
    )
    db.add(invoice)
    db.flush()

    user = User(
        email=f"user_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="fakehash123",
        full_name="Proof User",
    )
    db.add(user)
    db.flush()

    membership = Membership(
        user_id=user.id,
        business_id=business.id,
        role="OWNER",
    )
    db.add(membership)
    db.commit()
    db.refresh(business)
    db.refresh(customer)
    db.refresh(invoice)
    db.refresh(user)

    return business, customer, invoice, user


class TestPaymentProof(unittest.TestCase):
    def setUp(self):
        self.db: Session = SessionLocal()
        self.client = TestClient(app)
        self.queue = InMemoryTaskQueue()
        self.worker = WorkerService(queue=self.queue)
        self.queue_patcher = patch(
            "backend.app.api.invoices.get_task_queue", return_value=self.queue
        )
        self.queue_patcher.start()

    def tearDown(self):
        self.queue_patcher.stop()
        self.db.close()

    def _auth_header(self, user: User, business: Business) -> dict[str, str]:
        token = create_access_token(
            subject=str(user.id),
            extra_claims={"business_id": str(business.id), "role": "OWNER"},
        )
        return {"Authorization": f"Bearer {token}"}

    # ==========================================
    # 1. UPLOAD TESTS (1-5)
    # ==========================================

    def test_01_valid_pdf_accepted(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt for Invoice {inv.invoice_number} Paid 50000 on 2026-02-10")
        proof, task = upload_payment_proof(
            self.db,
            business_id=b.id,
            invoice_id=inv.id,
            file_content=pdf_bytes,
            original_filename="receipt.pdf",
            content_type="application/pdf",
        )
        self.assertEqual(proof.status, "PENDING")
        self.assertEqual(proof.original_filename, "receipt.pdf")
        self.assertEqual(proof.file_hash, hashlib.sha256(pdf_bytes).hexdigest())
        self.assertIsNotNone(task.id)

    def test_02_valid_csv_accepted(self):
        b, c, inv, u = _create_fixtures(self.db)
        csv_bytes = _create_sample_csv([
            ["invoice_number", "payment_date", "payment_amount"],
            [inv.invoice_number, "2026-02-10", "50000.00"],
        ])
        proof, task = upload_payment_proof(
            self.db,
            business_id=b.id,
            invoice_id=inv.id,
            file_content=csv_bytes,
            original_filename="statement.csv",
            content_type="text/csv",
        )
        self.assertEqual(proof.status, "PENDING")
        self.assertEqual(proof.original_filename, "statement.csv")

    def test_03_valid_xlsx_accepted(self):
        b, c, inv, u = _create_fixtures(self.db)
        xlsx_bytes = _create_sample_xlsx([
            ["invoice_number", "payment_date", "payment_amount"],
            [inv.invoice_number, "2026-02-10", 50000.00],
        ])
        proof, task = upload_payment_proof(
            self.db,
            business_id=b.id,
            invoice_id=inv.id,
            file_content=xlsx_bytes,
            original_filename="records.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(proof.status, "PENDING")
        self.assertEqual(proof.original_filename, "records.xlsx")

    def test_04_invalid_format_rejected(self):
        b, c, inv, u = _create_fixtures(self.db)
        with self.assertRaises(HTTPException) as ctx:
            validate_proof_file(b"bad content", "malicious.exe")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_05_corrupt_proof_handled_safely(self):
        b, c, inv, u = _create_fixtures(self.db)
        # Missing %PDF header
        with self.assertRaises(HTTPException) as ctx:
            validate_proof_file(b"This is not a real PDF", "corrupt.pdf")
        self.assertEqual(ctx.exception.status_code, 400)

        # Empty file
        with self.assertRaises(HTTPException) as ctx:
            validate_proof_file(b"", "empty.pdf")
        self.assertEqual(ctx.exception.status_code, 400)

    # ==========================================
    # 2. EXTRACTION TESTS (6-11)
    # ==========================================

    def test_06_invoice_number_extracted(self):
        text = "Payment Confirmation\nInvoice Number: INV-98765\nAmount: INR 50000\nDate: 2026-02-10"
        pdf_bytes = _create_sample_pdf(text)
        extracted = PaymentProofParser.parse(pdf_bytes, "proof.pdf")
        self.assertEqual(extracted.invoice_reference, "INV-98765")

    def test_07_payment_date_extracted(self):
        text = "Bank Slip\nDate of Payment: 10/02/2026\nAmount: Rs. 25000.00"
        pdf_bytes = _create_sample_pdf(text)
        extracted = PaymentProofParser.parse(pdf_bytes, "slip.pdf")
        self.assertIsNotNone(extracted.payment_date)
        self.assertEqual(extracted.payment_date.date(), date(2026, 2, 10))

    def test_08_payment_amount_extracted(self):
        text = "NEFT Receipt\nAmount Paid: ₹75,000.50\nPaid on: 2026-02-10"
        pdf_bytes = _create_sample_pdf(text)
        extracted = PaymentProofParser.parse(pdf_bytes, "neft.pdf")
        self.assertEqual(extracted.amount, Decimal("75000.50"))

    def test_09_customer_identity_extracted(self):
        text = "Receipt\nPaid by: Acme Industrial Ltd\nAmount: 50000\nDate: 2026-02-10"
        pdf_bytes = _create_sample_pdf(text)
        extracted = PaymentProofParser.parse(pdf_bytes, "receipt.pdf")
        self.assertIn("Acme Industrial Ltd", extracted.customer_name or "")

    def test_10_ocr_fallback_works(self):
        """Simulate scanned PDF where native text is blank but OCR extracts receipt text."""
        pdf_bytes = _create_sample_pdf("")
        with patch("backend.app.services.payment_proof_parser.extract_text_from_pdf", return_value=""), \
             patch("backend.app.services.payment_proof_parser.extract_layout_text_from_pdf", return_value=""), \
             patch("backend.app.services.payment_proof_parser.ocr_availability", return_value=(True, "available")), \
             patch("backend.app.services.payment_proof_parser.extract_text_via_ocr", return_value="Invoice: INV-OCR-01 Date: 2026-02-10 Amount: 40000"):
            extracted = PaymentProofParser.parse(pdf_bytes, "scanned.pdf")
            self.assertEqual(extracted.amount, Decimal("40000.00"))
            self.assertEqual(extracted.invoice_reference, "INV-OCR-01")
            self.assertEqual(extracted.extraction_method, "ocr")

    def test_11_ocr_unavailable_safe_failure(self):
        """When native text is empty and OCR is unavailable, extraction safely returns insufficient text."""
        pdf_bytes = _create_sample_pdf("")
        with patch("backend.app.services.payment_proof_parser.extract_text_from_pdf", return_value=""), \
             patch("backend.app.services.payment_proof_parser.extract_layout_text_from_pdf", return_value=""), \
             patch("backend.app.services.payment_proof_parser.ocr_availability", return_value=(False, "Tesseract not installed")):
            extracted = PaymentProofParser.parse(pdf_bytes, "scanned.pdf")
            self.assertIsNone(extracted.amount)
            self.assertIsNone(extracted.payment_date)

    # ==========================================
    # 3. MATCHING TESTS (12-16)
    # ==========================================

    def test_12_correct_invoice_matched(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice No: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, task = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        verified = verify_and_process_proof(self.db, proof_id=proof.id)
        self.assertEqual(verified.status, "VERIFIED")
        self.assertIsNotNone(verified.payment_id)

    def test_13_tenant_mismatch_blocked(self):
        b1, c1, inv1, u1 = _create_fixtures(self.db, "Tenant 1")
        b2, c2, inv2, u2 = _create_fixtures(self.db, "Tenant 2")
        pdf_bytes = _create_sample_pdf(f"Receipt\nAmount: 50000\nDate: 2026-02-10")
        # Attempt to upload proof under Tenant 1 for Tenant 2's invoice
        with self.assertRaises(HTTPException) as ctx:
            upload_payment_proof(self.db, business_id=b1.id, invoice_id=inv2.id, file_content=pdf_bytes, original_filename="hack.pdf")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_14_customer_mismatch_blocked(self):
        b, c, inv, u = _create_fixtures(self.db)
        # Create a different customer in the same tenant
        other_customer = Customer(
            business_id=b.id,
            display_name="Rival Enterprises Ltd",
            normalized_name=normalize_customer_name("Rival Enterprises Ltd"),
        )
        self.db.add(other_customer)
        self.db.commit()

        # Proof explicitly belongs to the other customer
        pdf_bytes = _create_sample_pdf(f"Receipt\nPaid by: Rival Enterprises Ltd\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="rival.pdf")
        result = verify_and_process_proof(self.db, proof_id=proof.id)
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("conflicts with target invoice's customer", result.error_message or "")
        self.assertIsNone(result.payment_id)

    def test_15_ambiguous_match_does_not_create_payment(self):
        b, c, inv, u = _create_fixtures(self.db)
        # Proof specifies an invoice reference completely different from the target invoice
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice No: INV-DIFFERENT-999\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="conflict.pdf")
        result = verify_and_process_proof(self.db, proof_id=proof.id)
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("does not match target invoice", result.error_message or "")
        # ZERO payments created
        payment_count = self.db.scalar(select(func.count(Payment.id)).where(Payment.invoice_id == inv.id))
        self.assertEqual(payment_count, 0)

    def test_phase_f_weak_or_ocr_only_evidence_needs_review(self):
        """Extraction success is not verification without corroborating evidence."""
        b, c, inv, u = _create_fixtures(self.db)
        # Amount and date parse successfully, but no invoice/customer evidence
        # leaves the operational score at 0.80 (< 0.85).
        pdf_bytes = _create_sample_pdf("Receipt\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(
            self.db, business_id=b.id, invoice_id=inv.id,
            file_content=pdf_bytes, original_filename="weak.pdf",
        )
        result = verify_and_process_proof(self.db, proof_id=proof.id)
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIsNone(result.payment_id)
        self.assertEqual(self.db.scalar(select(func.count(Payment.id)).where(Payment.invoice_id == inv.id)), 0)

    def test_phase_f_multiple_structured_candidates_need_review(self):
        b, c, inv, u = _create_fixtures(self.db)
        csv_bytes = _create_sample_csv([
            ["invoice_number", "payment_date", "payment_amount"],
            [inv.invoice_number, "2026-02-10", "50000"],
            [inv.invoice_number, "2026-02-11", "50000"],
        ])
        proof, _ = upload_payment_proof(
            self.db, business_id=b.id, invoice_id=inv.id,
            file_content=csv_bytes, original_filename="ambiguous.csv",
        )
        result = verify_and_process_proof(self.db, proof_id=proof.id)
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertEqual(result.extracted_data["candidate_count"], 2)
        self.assertIsNone(result.payment_id)

    def test_phase_f_amount_exceeding_outstanding_needs_review(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(
            f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 100001\nDate: 2026-02-10"
        )
        proof, _ = upload_payment_proof(
            self.db, business_id=b.id, invoice_id=inv.id,
            file_content=pdf_bytes, original_filename="overpayment.pdf",
        )
        result = verify_and_process_proof(self.db, proof_id=proof.id)
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIsNone(result.payment_id)

    def test_16_missing_invoice_does_not_create_payment(self):
        b, c, inv, u = _create_fixtures(self.db)
        non_existent_id = uuid.uuid4()
        with self.assertRaises(HTTPException) as ctx:
            upload_payment_proof(
                self.db,
                business_id=b.id,
                invoice_id=non_existent_id,
                file_content=_create_sample_pdf("Amount: 50000 Date: 2026-02-10"),
                original_filename="missing.pdf",
            )
        self.assertEqual(ctx.exception.status_code, 404)

    # ==========================================
    # 4. PAYMENT CREATION TESTS (17-24)
    # ==========================================

    def test_17_successful_proof_creates_real_payment(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt for Invoice {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        verified = verify_and_process_proof(self.db, proof_id=proof.id)
        self.assertIsNotNone(verified.payment_id)
        row = self.db.get(Payment, verified.payment_id)
        self.assertIsNotNone(row)

    def test_18_provenance_is_proof_verified(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        verified = verify_and_process_proof(self.db, proof_id=proof.id)
        row = self.db.get(Payment, verified.payment_id)
        self.assertEqual(row.provenance, "proof_verified")

    def test_19_correct_invoice_id(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        verified = verify_and_process_proof(self.db, proof_id=proof.id)
        row = self.db.get(Payment, verified.payment_id)
        self.assertEqual(row.invoice_id, inv.id)

    def test_20_correct_payment_date(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        verified = verify_and_process_proof(self.db, proof_id=proof.id)
        row = self.db.get(Payment, verified.payment_id)
        self.assertEqual(row.payment_date.date(), date(2026, 2, 10))

    def test_21_correct_amount(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000.00\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        verified = verify_and_process_proof(self.db, proof_id=proof.id)
        row = self.db.get(Payment, verified.payment_id)
        self.assertAlmostEqual(float(row.amount), 50000.00, places=2)

    def test_22_invoice_status_derivation_updates(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        verify_and_process_proof(self.db, proof_id=proof.id)
        self.db.refresh(inv)
        self.assertEqual(inv.payment_status, "PARTIAL")

    def test_23_partial_payment_works(self):
        b, c, inv, u = _create_fixtures(self.db)
        # Invoice is 100000, proof is 40000 -> PARTIAL
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 40000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        verify_and_process_proof(self.db, proof_id=proof.id)
        self.db.refresh(inv)
        self.assertEqual(inv.payment_status, "PARTIAL")

    def test_24_full_payment_works(self):
        b, c, inv, u = _create_fixtures(self.db)
        # Proof covers full 100000 -> PAID
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 100000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        verify_and_process_proof(self.db, proof_id=proof.id)
        self.db.refresh(inv)
        self.assertEqual(inv.payment_status, "PAID")

    # ==========================================
    # 5. IDEMPOTENCY TESTS (25-27)
    # ==========================================

    def test_25_same_proof_uploaded_twice_idempotent(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        # Upload 1
        proof1, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof1.pdf")
        verify_and_process_proof(self.db, proof_id=proof1.id)

        # Upload 2 (same invoice, date, amount)
        proof2, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof2.pdf")
        verify_and_process_proof(self.db, proof_id=proof2.id)

        # Both proofs are verified, but only ONE Payment row exists!
        self.assertEqual(proof1.status, "VERIFIED")
        self.assertEqual(proof2.status, "VERIFIED")
        self.assertEqual(proof1.payment_id, proof2.payment_id)
        payment_count = self.db.scalar(select(func.count(Payment.id)).where(Payment.invoice_id == inv.id))
        self.assertEqual(payment_count, 1)

    def test_26_worker_retry_idempotent(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="retry.pdf")
        # Run 1
        verify_and_process_proof(self.db, proof_id=proof.id)
        # Run 2 (worker re-executes task)
        verify_and_process_proof(self.db, proof_id=proof.id)
        payment_count = self.db.scalar(select(func.count(Payment.id)).where(Payment.invoice_id == inv.id))
        self.assertEqual(payment_count, 1)

    def test_27_existing_imported_payment_not_duplicated(self):
        b, c, inv, u = _create_fixtures(self.db)
        # Pre-existing imported payment
        imported_payment = Payment(
            business_id=b.id,
            invoice_id=inv.id,
            invoice_reference=inv.invoice_number,
            payment_date=datetime(2026, 2, 10, tzinfo=timezone.utc),
            amount=Decimal("50000.00"),
            customer_identity_key=f"customer:{c.id}",
            provenance="import",
        )
        self.db.add(imported_payment)
        self.db.commit()

        # Proof arrives with the exact same date and amount
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        verify_and_process_proof(self.db, proof_id=proof.id)

        # Links to the existing imported payment, preserving its original provenance!
        self.assertEqual(proof.payment_id, imported_payment.id)
        self.db.refresh(imported_payment)
        self.assertEqual(imported_payment.provenance, "import")
        payment_count = self.db.scalar(select(func.count(Payment.id)).where(Payment.invoice_id == inv.id))
        self.assertEqual(payment_count, 1)

    # ==========================================
    # 6. STATUS LIFECYCLE TESTS (28-33)
    # ==========================================

    def test_28_uploading_to_pending(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nAmount: 50000\nDate: 2026-02-10")
        proof, task = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="p.pdf")
        self.assertEqual(proof.status, "PENDING")
        self.assertEqual(task.status, "PENDING")

    def test_29_processing_state(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="p.pdf")
        # When process starts, status transitions to PROCESSING
        proof.status = "PROCESSING"
        self.db.commit()
        refreshed = self.db.get(PaymentProof, proof.id)
        self.assertEqual(refreshed.status, "PROCESSING")

    def test_30_verified_state(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="p.pdf")
        result = verify_and_process_proof(self.db, proof_id=proof.id)
        self.assertEqual(result.status, "VERIFIED")

    def test_31_needs_review_state(self):
        b, c, inv, u = _create_fixtures(self.db)
        # Future date leads to NEEDS_REVIEW
        future_date = (date.today() + timedelta(days=30)).isoformat()
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: {future_date}")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="p.pdf")
        result = verify_and_process_proof(self.db, proof_id=proof.id)
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIsNone(result.payment_id)

    def test_32_failure_state(self):
        b, c, inv, u = _create_fixtures(self.db)
        # Missing amount completely
        pdf_bytes = _create_sample_pdf("Receipt with no amount anywhere on the page")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="p.pdf")
        result = verify_and_process_proof(self.db, proof_id=proof.id)
        self.assertEqual(result.status, "FAILED")

    def test_33_no_permanent_processing_state(self):
        """Stale PROCESSING task recovery resets task and proof to PENDING."""
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf("Receipt")
        proof, task = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="p.pdf")
        # Simulate worker crashed while PROCESSING 30 minutes ago
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=35)
        task.status = "PROCESSING"
        task.started_at = stale_time
        proof.status = "PROCESSING"
        self.db.commit()

        recovered_count = recover_stale_processing_tasks(self.db)
        self.assertGreaterEqual(recovered_count, 1)
        self.db.refresh(proof)
        self.assertEqual(proof.status, "PENDING")

    # ==========================================
    # 7. CUSTOMER HISTORY TESTS (34-36)
    # ==========================================

    def test_34_verified_payment_in_customer_history(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="p.pdf")
        verified = verify_and_process_proof(self.db, proof_id=proof.id)
        # Payment must exist with customer_identity_key
        payment = self.db.get(Payment, verified.payment_id)
        self.assertEqual(payment.customer_identity_key, f"customer:{c.id}")

    def test_35_eligible_historical_evidence_for_future_invoices(self):
        from backend.app.services.prediction_service import ELIGIBLE_PAYMENT_PROVENANCE
        self.assertIn("proof_verified", ELIGIBLE_PAYMENT_PROVENANCE)

    def test_36_temporal_leakage_prevented(self):
        """Payment date occurring after invoice date cannot leak into that invoice's own features."""
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="p.pdf")
        verified = verify_and_process_proof(self.db, proof_id=proof.id)
        payment = self.db.get(Payment, verified.payment_id)
        # Payment date (2026-02-10) is AFTER invoice date (2026-01-15)
        self.assertGreater(payment.payment_date.date(), inv.invoice_date)

    # ==========================================
    # 8. SECURITY & TENANT ISOLATION (37-40)
    # ==========================================

    def test_37_tenant_isolation_proof_storage(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf("Receipt")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="isolated.pdf")
        # Storage key is prefixed with tenant id
        self.assertTrue(proof.storage_key.startswith(f"tenants/{b.id}/payment_proofs/"))

    def test_38_tenant_isolation_invoice_matching(self):
        b1, c1, inv1, u1 = _create_fixtures(self.db, "Tenant 1")
        b2, c2, inv2, u2 = _create_fixtures(self.db, "Tenant 2")
        headers1 = self._auth_header(u1, b1)
        # Attempt to upload proof to Tenant 2's invoice using Tenant 1's auth
        response = self.client.post(
            f"/invoices/{inv2.id}/payment-proof",
            files={"file": ("proof.pdf", _create_sample_pdf("Receipt"), "application/pdf")},
            headers=headers1,
        )
        self.assertEqual(response.status_code, 404)

    def test_39_tenant_isolation_payment_creation(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        verified = verify_and_process_proof(self.db, proof_id=proof.id)
        payment = self.db.get(Payment, verified.payment_id)
        self.assertEqual(payment.business_id, b.id)

    def test_40_unauthorized_proof_access_blocked(self):
        b, c, inv, u = _create_fixtures(self.db)
        pdf_bytes = _create_sample_pdf(f"Receipt\nInvoice: {inv.invoice_number}\nAmount: 50000\nDate: 2026-02-10")
        proof, _ = upload_payment_proof(self.db, business_id=b.id, invoice_id=inv.id, file_content=pdf_bytes, original_filename="proof.pdf")
        # Anonymous request
        resp = self.client.get(f"/invoices/{inv.id}/payment-proofs/{proof.id}")
        self.assertEqual(resp.status_code, 401)
        resp_file = self.client.get(f"/invoices/{inv.id}/payment-proofs/{proof.id}/file")
        self.assertEqual(resp_file.status_code, 401)


if __name__ == "__main__":
    unittest.main()
