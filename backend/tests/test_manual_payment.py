"""Phase D manual payment recording tests."""

import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.db.session import SessionLocal
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.payment import Payment
from backend.app.models.prediction import PredictionResult
from backend.app.services.manual_payment_service import (
    derive_invoice_payment_status,
    get_invoice_payments,
    record_manual_payment,
)


def _create_test_fixtures(db):
    """Create a business, customer, and invoice for testing."""
    business = Business(name=f"Test Business {uuid.uuid4()}", currency="INR")
    db.add(business)
    db.flush()
    customer = Customer(
        business_id=business.id,
        display_name=f"Test Customer {uuid.uuid4()}",
    )
    db.add(customer)
    db.flush()
    invoice = Invoice(
        business_id=business.id,
        customer_id=customer.id,
        invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
        invoice_date=date(2026, 1, 15),
        due_date=date(2026, 2, 15),
        amount=Decimal("100000.00"),
        currency="INR",
        payment_status="OPEN",
        processing_status="PROCESSED",
    )
    db.add(invoice)
    db.flush()
    return business, customer, invoice


class TestManualPaymentDatabase(unittest.TestCase):
    """Tests 1-6: Payment row creation and field correctness."""

    def test_01_manual_payment_creates_real_row(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            result = record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=50000,
            )
            self.assertIsNotNone(result.payment.id)
            # Verify row exists in database
            row = db.scalar(select(Payment).where(Payment.id == result.payment.id))
            self.assertIsNotNone(row)
        finally:
            db.rollback()
            db.close()

    def test_02_provenance_is_manual(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            result = record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=50000,
            )
            self.assertEqual(result.payment.provenance, "manual")
        finally:
            db.rollback()
            db.close()

    def test_03_correct_invoice_id(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            result = record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=50000,
            )
            self.assertEqual(result.payment.invoice_id, invoice.id)
        finally:
            db.rollback()
            db.close()

    def test_04_correct_business_id(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            result = record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=50000,
            )
            self.assertEqual(result.payment.business_id, business.id)
        finally:
            db.rollback()
            db.close()

    def test_05_correct_date(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            result = record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=50000,
            )
            self.assertEqual(result.payment.payment_date.date(), date(2026, 2, 10))
        finally:
            db.rollback()
            db.close()

    def test_06_correct_amount(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            result = record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=50000,
            )
            self.assertAlmostEqual(float(result.payment.amount), 50000.00, places=2)
        finally:
            db.rollback()
            db.close()

    def test_07_note_persisted(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            result = record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=50000,
                note="Wire transfer received",
            )
            self.assertEqual(result.payment.note, "Wire transfer received")
        finally:
            db.rollback()
            db.close()


class TestInvoicePaymentStatus(unittest.TestCase):
    """Tests 8-11: Invoice payment status behavior."""

    def test_08_unpaid_invoice_remains_open(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            status = derive_invoice_payment_status(db, invoice)
            self.assertEqual(status, "OPEN")
        finally:
            db.rollback()
            db.close()

    def test_09_full_payment_results_in_paid(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=100000,  # Full amount
            )
            self.assertEqual(invoice.payment_status, "PAID")
        finally:
            db.rollback()
            db.close()

    def test_10_partial_payment_results_in_partial(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=40000,  # Partial
            )
            self.assertEqual(invoice.payment_status, "PARTIAL")
            # Second payment completes it
            record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 15),
                amount=60000,
            )
            self.assertEqual(invoice.payment_status, "PAID")
        finally:
            db.rollback()
            db.close()

    def test_11_prediction_never_changes_payment_status(self):
        """Creating a prediction must NOT change invoice payment_status."""
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            # Invoice is OPEN
            self.assertEqual(invoice.payment_status, "OPEN")
            # Create a prediction result
            prediction = PredictionResult(
                business_id=business.id,
                invoice_id=invoice.id,
                prediction=True,
                risk_score=0.85,
                risk_tier="HIGH",
                predicted_days_until_payment=45.0,
            )
            db.add(prediction)
            db.flush()
            # Invoice must still be OPEN — prediction doesn't change status
            db.refresh(invoice)
            self.assertEqual(invoice.payment_status, "OPEN")
            # Derive status must also be OPEN (no payments)
            self.assertEqual(derive_invoice_payment_status(db, invoice), "OPEN")
        finally:
            db.rollback()
            db.close()


class TestPaymentValidation(unittest.TestCase):
    """Tests 12-16: Input validation."""

    def test_12_zero_amount_rejected(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            with self.assertRaises(ValueError):
                record_manual_payment(
                    db,
                    business_id=business.id,
                    invoice_id=invoice.id,
                    payment_date=date(2026, 2, 10),
                    amount=0,
                )
        finally:
            db.rollback()
            db.close()

    def test_13_negative_amount_rejected(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            with self.assertRaises(ValueError):
                record_manual_payment(
                    db,
                    business_id=business.id,
                    invoice_id=invoice.id,
                    payment_date=date(2026, 2, 10),
                    amount=-5000,
                )
        finally:
            db.rollback()
            db.close()

    def test_14_invalid_amount_rejected(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            for bad in ["abc", "NaN", "Infinity", float("inf"), float("nan")]:
                with self.assertRaises(ValueError, msg=f"Should reject {bad}"):
                    record_manual_payment(
                        db,
                        business_id=business.id,
                        invoice_id=invoice.id,
                        payment_date=date(2026, 2, 10),
                        amount=bad,
                    )
        finally:
            db.rollback()
            db.close()

    def test_15_future_date_rejected(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            future = date.today() + timedelta(days=30)
            with self.assertRaises(ValueError):
                record_manual_payment(
                    db,
                    business_id=business.id,
                    invoice_id=invoice.id,
                    payment_date=future,
                    amount=50000,
                )
        finally:
            db.rollback()
            db.close()

    def test_16_nonexistent_invoice_rejected(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            with self.assertRaises(LookupError):
                record_manual_payment(
                    db,
                    business_id=business.id,
                    invoice_id=uuid.uuid4(),  # Doesn't exist
                    payment_date=date(2026, 2, 10),
                    amount=50000,
                )
        finally:
            db.rollback()
            db.close()


class TestPaymentIdempotency(unittest.TestCase):
    """Tests 17-18: Duplicate submission handling."""

    def test_17_duplicate_manual_submission_blocked(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            # First payment succeeds
            record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=50000,
            )
            # Identical submission raises ValueError (duplicate)
            with self.assertRaises(ValueError) as ctx:
                record_manual_payment(
                    db,
                    business_id=business.id,
                    invoice_id=invoice.id,
                    payment_date=date(2026, 2, 10),
                    amount=50000,
                )
            self.assertIn("already exists", str(ctx.exception))
        finally:
            db.rollback()
            db.close()

    def test_18_natural_key_allows_different_amounts(self):
        """Two payments with same date but different amounts are distinct."""
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=40000,
            )
            result2 = record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=60000,
            )
            self.assertIsNotNone(result2.payment.id)
        finally:
            db.rollback()
            db.close()


class TestCustomerHistory(unittest.TestCase):
    """Tests 19-21: Customer history interaction."""

    def test_19_manual_payment_in_customer_history(self):
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=100000,
            )
            # Query payments via the invoice -> customer path
            payments = get_invoice_payments(db, business.id, invoice.id)
            self.assertEqual(len(payments), 1)
            self.assertEqual(payments[0].provenance, "manual")
            # customer_identity_key should reference the customer
            self.assertEqual(payments[0].customer_identity_key, f"customer:{customer.id}")
        finally:
            db.rollback()
            db.close()

    def test_20_manual_provenance_eligible_for_predictions(self):
        """Verify 'manual' provenance is in the eligible set."""
        from backend.app.services.prediction_service import ELIGIBLE_PAYMENT_PROVENANCE
        self.assertIn("manual", ELIGIBLE_PAYMENT_PROVENANCE)

    def test_21_own_payment_not_in_own_features(self):
        """Conceptual leakage test: a payment for an invoice should not
        influence its own prediction. This is enforced by the as-of date
        filtering in prediction_service, not by this service, but we verify
        the payment date relationship."""
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            # Payment date is AFTER invoice date — cannot be used as prior history
            # for this invoice's own prediction
            result = record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),  # After invoice_date 2026-01-15
                amount=100000,
            )
            # The payment's date is after the invoice_date, confirming
            # temporal ordering prevents self-referential features
            self.assertGreater(
                result.payment.payment_date.date(),
                invoice.invoice_date,
            )
        finally:
            db.rollback()
            db.close()


class TestTenantIsolation(unittest.TestCase):
    """Test 22: Cross-tenant payment creation blocked."""

    def test_22_cross_tenant_payment_blocked(self):
        db = SessionLocal()
        try:
            # Create tenant A fixtures
            business_a, customer_a, invoice_a = _create_test_fixtures(db)
            # Create tenant B
            business_b = Business(name=f"Other Business {uuid.uuid4()}", currency="INR")
            db.add(business_b)
            db.flush()
            # Try to record payment as tenant B against tenant A's invoice
            with self.assertRaises(LookupError):
                record_manual_payment(
                    db,
                    business_id=business_b.id,  # Wrong tenant
                    invoice_id=invoice_a.id,  # Belongs to tenant A
                    payment_date=date(2026, 2, 10),
                    amount=50000,
                )
        finally:
            db.rollback()
            db.close()


class TestAuthRequirements(unittest.TestCase):
    """Tests 23-24: Auth enforcement is tested at the API endpoint level.
    These verify the service layer's tenant verification."""

    def test_23_missing_business_id_fails(self):
        """Providing a non-existent business_id should fail."""
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            with self.assertRaises(LookupError):
                record_manual_payment(
                    db,
                    business_id=uuid.uuid4(),  # Non-existent business
                    invoice_id=invoice.id,
                    payment_date=date(2026, 2, 10),
                    amount=50000,
                )
        finally:
            db.rollback()
            db.close()

    def test_24_reference_and_note_optional(self):
        """Payment with no reference or note should succeed."""
        db = SessionLocal()
        try:
            business, customer, invoice = _create_test_fixtures(db)
            result = record_manual_payment(
                db,
                business_id=business.id,
                invoice_id=invoice.id,
                payment_date=date(2026, 2, 10),
                amount=50000,
            )
            self.assertIsNone(result.payment.reference)
            self.assertIsNone(result.payment.note)
        finally:
            db.rollback()
            db.close()


if __name__ == "__main__":
    unittest.main()
