"""
Comprehensive Phase V Test Suite: Genuine XGBoost Prediction from Historical Customer Payment Outcomes.

Verifies:
1. Strict V1 eligibility gate: 0, 1, 2 completed prior payments -> False; 3, 4+ -> True.
2. Genuine XGBoost inference: distinct customer behaviors (prompt vs chronic late) produce
   distinct feature vectors and distinct model-derived probabilities and timing estimates (non-hardcoded).
3. Multi-origin history: paid CURRENT operational invoices accumulate payment history and enable
   predictions on subsequent invoices without converting origin to HISTORICAL.
4. Historical company workspace: companies with paid operational invoices are surfaced in listing.
5. Company correction & resolution: normalized/case-insensitive matching, stale prediction invalidation,
   and automatic recalculation for eligible customers.
6. Temporal leakage protection: payments on or after invoice_date (T) and target invoice's own payments
   are never included in features.
7. Missing/broken model artifacts raise explicit errors and never fall back to static fixtures.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import unittest
import uuid

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.models.payment import Payment
from backend.app.models.prediction import PredictionResult
from backend.app.services.customer_identity import (
    correct_invoice_customer,
    create_customer,
    resolve_unresolved_invoice,
)
from backend.app.services.historical_service import (
    list_historical_companies,
    get_historical_company_detail,
)
from backend.app.services.prediction_service import (
    CLASSIFIER_MODEL_VERSION,
    INSUFFICIENT_HISTORY_REASON,
    MIN_CUSTOMER_HISTORY_FOR_PREDICTION,
    TIMING_MODEL_VERSION,
    InsufficientCustomerHistoryError,
    build_inference_features,
    evaluate_prediction_eligibility,
    get_prediction_for_invoice,
    get_predictor,
    predict_for_invoice,
    reset_predictor,
)
from backend.ml.inference.predict import InferenceError, V1Predictor


class TestPhaseVPrediction(unittest.TestCase):
    """Phase V test suite for genuine XGBoost inference and historical payment outcomes."""

    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()

        # Register Business Tenant
        self.suffix = uuid.uuid4().hex[:8]
        res = self.client.post("/auth/register", json={
            "email": f"ml_phase_v_{self.suffix}@example.com",
            "password": "Password123!",
            "full_name": "ML Phase V Admin",
            "business_name": f"Phase V Enterprise {self.suffix}",
        })
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.token = data["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.business_id = uuid.UUID(data["business"]["id"])

    def tearDown(self):
        try:
            biz = self.db.get(Business, self.business_id)
            if biz:
                self.db.delete(biz)
            self.db.commit()
        except Exception:
            self.db.rollback()
        finally:
            self.db.close()
            reset_predictor()

    def _create_customer(self, name: str, gstin: str | None = None) -> Customer:
        customer = create_customer(
            self.db,
            business_id=self.business_id,
            display_name=name,
            gstin=gstin,
        )
        self.db.commit()
        self.db.refresh(customer)
        return customer

    def _create_invoice(
        self,
        customer_id: uuid.UUID | None,
        *,
        amount: float = 25000.0,
        invoice_date: date = date(2024, 6, 1),
        due_date: date = date(2024, 6, 21),
        currency: str = "USD",
        payment_terms: str = "NAA8",
        origin: InvoiceOrigin = InvoiceOrigin.CURRENT,
        processing_status: str = "PROCESSED",
        unresolved_customer_name: str | None = None,
    ) -> Invoice:
        inv = Invoice(
            id=uuid.uuid4(),
            business_id=self.business_id,
            customer_id=customer_id,
            unresolved_customer_name=unresolved_customer_name,
            invoice_number=f"INV-{uuid.uuid4().hex[:8].upper()}",
            amount=amount,
            currency=currency,
            invoice_date=invoice_date,
            due_date=due_date,
            payment_terms=payment_terms,
            payment_status="OPEN",
            processing_status=processing_status,
            origin=origin.value,
        )
        self.db.add(inv)
        self.db.commit()
        self.db.refresh(inv)
        return inv

    def _record_completed_payment(
        self,
        invoice: Invoice,
        *,
        payment_date: date,
        provenance: str = "legacy",
    ) -> Payment:
        payment = Payment(
            id=uuid.uuid4(),
            business_id=self.business_id,
            invoice_id=invoice.id,
            invoice_reference=invoice.invoice_number,
            payment_date=datetime.combine(payment_date, datetime.min.time(), tzinfo=timezone.utc),
            amount=invoice.amount,
            customer_identity_key=f"customer:{invoice.customer_id}" if invoice.customer_id else None,
            provenance=provenance,
        )
        invoice.payment_status = "PAID"
        self.db.add(payment)
        self.db.commit()
        self.db.refresh(payment)
        return payment

    def test_01_eligibility_threshold_strictly_enforced(self):
        """Verify strict V1 threshold: 0, 1, 2 prior payments -> unavailable; 3+ -> available."""
        customer = self._create_customer("Threshold Test Corp")
        target_inv = self._create_invoice(customer.id, invoice_date=date(2024, 5, 1))

        # 0 payments
        elig_0 = evaluate_prediction_eligibility(self.db, target_inv)
        self.assertEqual(elig_0.eligible_history_count, 0)
        self.assertFalse(elig_0.prediction_available)
        with self.assertRaises(InsufficientCustomerHistoryError) as cm:
            predict_for_invoice(self.db, target_inv.id, self.business_id)
        self.assertEqual(cm.exception.eligible_history_count, 0)

        # GET API endpoint returns 200 with PredictionUnavailableResponse
        res_0 = self.client.get(f"/invoices/{target_inv.id}/prediction", headers=self.headers)
        self.assertEqual(res_0.status_code, 200)
        self.assertEqual(res_0.json()["reason"], INSUFFICIENT_HISTORY_REASON)
        self.assertEqual(res_0.json()["eligible_history_count"], 0)

        # 1 payment
        inv_1 = self._create_invoice(customer.id, invoice_date=date(2024, 1, 1), due_date=date(2024, 1, 20), origin=InvoiceOrigin.HISTORICAL)
        self._record_completed_payment(inv_1, payment_date=date(2024, 1, 18))
        elig_1 = evaluate_prediction_eligibility(self.db, target_inv)
        self.assertEqual(elig_1.eligible_history_count, 1)
        self.assertFalse(elig_1.prediction_available)

        # 2 payments
        inv_2 = self._create_invoice(customer.id, invoice_date=date(2024, 2, 1), due_date=date(2024, 2, 20), origin=InvoiceOrigin.HISTORICAL)
        self._record_completed_payment(inv_2, payment_date=date(2024, 2, 19))
        elig_2 = evaluate_prediction_eligibility(self.db, target_inv)
        self.assertEqual(elig_2.eligible_history_count, 2)
        self.assertFalse(elig_2.prediction_available)

        # 3 payments -> Gate unlocks!
        inv_3 = self._create_invoice(customer.id, invoice_date=date(2024, 3, 1), due_date=date(2024, 3, 20), origin=InvoiceOrigin.HISTORICAL)
        self._record_completed_payment(inv_3, payment_date=date(2024, 3, 20))
        elig_3 = evaluate_prediction_eligibility(self.db, target_inv)
        self.assertEqual(elig_3.eligible_history_count, 3)
        self.assertTrue(elig_3.prediction_available)

        # Prediction succeeds and produces genuine model output
        pred = predict_for_invoice(self.db, target_inv.id, self.business_id)
        self.assertIsNotNone(pred)
        self.assertEqual(pred.classifier_model_version, CLASSIFIER_MODEL_VERSION)
        self.assertEqual(pred.timing_model_version, TIMING_MODEL_VERSION)
        self.assertGreaterEqual(pred.risk_score, 0.0)
        self.assertLessEqual(pred.risk_score, 1.0)
        self.assertIn(pred.risk_tier, ["LOW", "MEDIUM", "HIGH"])
        self.assertGreaterEqual(pred.predicted_days_until_payment, 0.0)

        # 4 payments -> Remains eligible
        inv_4 = self._create_invoice(customer.id, invoice_date=date(2024, 4, 1), due_date=date(2024, 4, 20), origin=InvoiceOrigin.HISTORICAL)
        self._record_completed_payment(inv_4, payment_date=date(2024, 4, 20))
        elig_4 = evaluate_prediction_eligibility(self.db, target_inv)
        self.assertEqual(elig_4.eligible_history_count, 4)
        self.assertTrue(elig_4.prediction_available)

    def test_02_distinct_behavioral_profiles_produce_distinct_xgboost_outputs(self):
        """Prompt customer vs chronic late customer receive distinct feature vectors and genuine XGBoost outputs."""
        prompt_cust = self._create_customer("Prompt Payers Ltd")
        late_cust = self._create_customer("Chronic Late Corp")

        # Set up 3 prompt payments for prompt_cust (paid 3 days early: delay = -3)
        for i in range(3):
            inv_date = date(2024, 1 + i, 1)
            due_date = date(2024, 1 + i, 20)
            pay_date = date(2024, 1 + i, 17)
            inv = self._create_invoice(prompt_cust.id, invoice_date=inv_date, due_date=due_date, origin=InvoiceOrigin.HISTORICAL)
            self._record_completed_payment(inv, payment_date=pay_date)

        # Set up 3 very late payments for late_cust (paid 35 days late: delay = +35)
        for i in range(3):
            inv_date = date(2024, 1 + i, 1)
            due_date = date(2024, 1 + i, 15)
            pay_date = due_date + timedelta(days=35)
            inv = self._create_invoice(late_cust.id, invoice_date=inv_date, due_date=due_date, origin=InvoiceOrigin.HISTORICAL)
            self._record_completed_payment(inv, payment_date=pay_date)

        # Target invoices for both customers with identical amount, dates, terms
        target_prompt = self._create_invoice(
            prompt_cust.id,
            amount=50000.0,
            invoice_date=date(2024, 5, 1),
            due_date=date(2024, 5, 21),
            currency="USD",
            payment_terms="NAA8",
        )
        target_late = self._create_invoice(
            late_cust.id,
            amount=50000.0,
            invoice_date=date(2024, 5, 1),
            due_date=date(2024, 5, 21),
            currency="USD",
            payment_terms="NAA8",
        )

        # Verify features are distinct and reflect genuine customer behavior
        feat_prompt = build_inference_features(self.db, target_prompt)
        feat_late = build_inference_features(self.db, target_late)

        self.assertLess(feat_prompt["cust_avg_delay"].iloc[0], 0.0)
        self.assertEqual(feat_prompt["cust_late_payment_rate"].iloc[0], 0.0)

        self.assertGreater(feat_late["cust_avg_delay"].iloc[0], 30.0)
        self.assertEqual(feat_late["cust_late_payment_rate"].iloc[0], 1.0)

        # Run genuine XGBoost inference
        pred_prompt = predict_for_invoice(self.db, target_prompt.id, self.business_id)
        pred_late = predict_for_invoice(self.db, target_late.id, self.business_id)

        # Chronic late customer should have a significantly higher risk score than prompt customer
        self.assertGreater(pred_late.risk_score, pred_prompt.risk_score)
        self.assertEqual(pred_prompt.risk_tier, "LOW")
        self.assertIn(pred_late.risk_tier, ["MEDIUM", "HIGH"])

        # Chronic late customer should have higher predicted days until payment
        self.assertGreater(pred_late.predicted_days_until_payment, pred_prompt.predicted_days_until_payment)

        # Verify predictions are not hardcoded static fixtures
        self.assertNotEqual(pred_prompt.risk_score, pred_late.risk_score)
        self.assertNotEqual(pred_prompt.predicted_days_until_payment, pred_late.predicted_days_until_payment)

    def test_03_paid_operational_invoices_accumulate_history_and_enable_prediction(self):
        """Paid CURRENT invoices contribute to history and enable predictions on subsequent CURRENT invoices."""
        op_cust = self._create_customer("Operational Dynamics Inc")

        # 1. Create 3 CURRENT invoices and pay them off with factual payments
        op_invoices = []
        for i in range(3):
            inv = self._create_invoice(
                op_cust.id,
                amount=12000.0,
                invoice_date=date(2024, 1 + i, 1),
                due_date=date(2024, 1 + i, 20),
                origin=InvoiceOrigin.CURRENT,
            )
            self._record_completed_payment(
                inv,
                payment_date=date(2024, 1 + i, 18),
                provenance="manual",
            )
            op_invoices.append(inv)

        # Assert invoices remain origin == CURRENT (never converted to HISTORICAL)
        for inv in op_invoices:
            reloaded = self.db.get(Invoice, inv.id)
            self.assertEqual(reloaded.origin, InvoiceOrigin.CURRENT.value)

        # 2. Create 4th CURRENT invoice after the 3 payments
        target_op = self._create_invoice(
            op_cust.id,
            amount=15000.0,
            invoice_date=date(2024, 4, 1),
            due_date=date(2024, 4, 21),
            origin=InvoiceOrigin.CURRENT,
        )

        elig = evaluate_prediction_eligibility(self.db, target_op)
        self.assertEqual(elig.eligible_history_count, 3)
        self.assertTrue(elig.prediction_available)

        # Prediction executes successfully on the 4th CURRENT invoice
        pred = predict_for_invoice(self.db, target_op.id, self.business_id)
        self.assertIsNotNone(pred)
        self.assertGreaterEqual(float(pred.risk_score), 0.0)

        # 3. Verify the company is surfaced in list_historical_companies with its payments
        hist_companies = list_historical_companies(self.db, self.business_id)
        matching = [c for c in hist_companies if c.id == op_cust.id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].display_name, "Operational Dynamics Inc")
        self.assertGreater(matching[0].total_paid, 0.0)

        # 4. Detail view displays the invoices with their true CURRENT origin
        detail = get_historical_company_detail(self.db, self.business_id, op_cust.id)
        self.assertIsNotNone(detail)
        self.assertGreaterEqual(len(detail.invoices), 3)
        self.assertTrue(any(item.origin == "CURRENT" for item in detail.invoices))

    def test_04_company_correction_invalidates_and_recalculates_prediction(self):
        """Invoice company correction clears stale prediction and recalculates genuine XGBoost prediction."""
        cust_empty = self._create_customer("Zero History Logistics")
        cust_ready = self._create_customer("Three History Industries")

        # Give cust_ready 3 completed payments
        for i in range(3):
            inv = self._create_invoice(
                cust_ready.id,
                amount=10000.0,
                invoice_date=date(2024, 1 + i, 1),
                due_date=date(2024, 1 + i, 20),
                origin=InvoiceOrigin.HISTORICAL,
            )
            self._record_completed_payment(inv, payment_date=date(2024, 1 + i, 20))

        # Target invoice initially linked to cust_empty
        target_inv = self._create_invoice(
            cust_empty.id,
            amount=30000.0,
            invoice_date=date(2024, 5, 1),
            due_date=date(2024, 5, 21),
            origin=InvoiceOrigin.CURRENT,
        )

        # Initially ineligible
        self.assertFalse(evaluate_prediction_eligibility(self.db, target_inv).prediction_available)
        self.assertIsNone(get_prediction_for_invoice(self.db, self.business_id, target_inv.id))

        # Correct company to cust_ready via PATCH /invoices/{invoice_id}/company using normalized name
        res = self.client.patch(
            f"/invoices/{target_inv.id}/company",
            json={"replacement_company_name": "three history industries"},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["customer_id"], str(cust_ready.id))

        self.db.expire_all()

        # Since cust_ready has 3 completed outcomes, prediction was automatically recalculated!
        reloaded_pred = get_prediction_for_invoice(self.db, self.business_id, target_inv.id)
        self.assertIsNotNone(reloaded_pred)
        self.assertGreaterEqual(float(reloaded_pred.risk_score), 0.0)
        self.assertEqual(reloaded_pred.classifier_model_version, CLASSIFIER_MODEL_VERSION)

        # And GET /invoices/{id}/prediction API returns the recalculated prediction
        res_pred = self.client.get(f"/invoices/{target_inv.id}/prediction", headers=self.headers)
        self.assertEqual(res_pred.status_code, 200)
        self.assertIn("risk_score", res_pred.json())

        # Now correct company back to cust_empty
        res_back = self.client.patch(
            f"/invoices/{target_inv.id}/company",
            json={"replacement_customer_id": str(cust_empty.id)},
            headers=self.headers,
        )
        self.assertEqual(res_back.status_code, 200)
        self.assertEqual(res_back.json()["customer_id"], str(cust_empty.id))

        self.db.expire_all()

        # The stale prediction is completely gone (invalidated)
        stale_pred = get_prediction_for_invoice(self.db, self.business_id, target_inv.id)
        self.assertIsNone(stale_pred)
        persisted_raw = self.db.scalar(
            select(PredictionResult).where(
                PredictionResult.business_id == self.business_id,
                PredictionResult.invoice_id == target_inv.id,
            )
        )
        self.assertIsNone(persisted_raw)

        # GET API now returns PredictionUnavailableResponse
        res_unavail = self.client.get(f"/invoices/{target_inv.id}/prediction", headers=self.headers)
        self.assertEqual(res_unavail.status_code, 200)
        self.assertEqual(res_unavail.json()["reason"], INSUFFICIENT_HISTORY_REASON)

    def test_05_strict_temporal_leakage_protection(self):
        """Future outcomes and target invoice's own payments never leak into feature computation."""
        cust = self._create_customer("Temporal Test Corp")

        # 3 completed outcomes before T = 2024-05-01
        for i in range(3):
            inv = self._create_invoice(
                cust.id,
                amount=8000.0,
                invoice_date=date(2024, 1 + i, 1),
                due_date=date(2024, 1 + i, 20),
                origin=InvoiceOrigin.HISTORICAL,
            )
            self._record_completed_payment(inv, payment_date=date(2024, 1 + i, 15))

        # Target invoice posted on 2024-05-01
        target_inv = self._create_invoice(
            cust.id,
            amount=20000.0,
            invoice_date=date(2024, 5, 1),
            due_date=date(2024, 5, 20),
            origin=InvoiceOrigin.CURRENT,
        )

        # Add a future invoice & payment after T (posted 2024-06-01, paid 2024-06-25, late by 10 days)
        future_inv = self._create_invoice(
            cust.id,
            amount=5000.0,
            invoice_date=date(2024, 6, 1),
            due_date=date(2024, 6, 15),
            origin=InvoiceOrigin.CURRENT,
        )
        self._record_completed_payment(future_inv, payment_date=date(2024, 6, 25))

        # Record a payment on target_inv itself (settled later on 2024-05-25)
        self._record_completed_payment(target_inv, payment_date=date(2024, 5, 25))

        # Feature matrix for target_inv as-of 2024-05-01
        df = build_inference_features(self.db, target_inv)

        # Only the 3 invoices before 2024-05-01 should be counted
        self.assertEqual(df["cust_prior_payment_count"].iloc[0], 3)
        self.assertEqual(df["cust_prior_invoice_count"].iloc[0], 3)

        # Late payment rate must be 0.0 (the future late payment is excluded)
        self.assertEqual(df["cust_late_payment_rate"].iloc[0], 0.0)

        # Target invoice payment is excluded (not counted as its own history)
        elig = evaluate_prediction_eligibility(self.db, target_inv)
        self.assertEqual(elig.eligible_history_count, 3)

    def test_06_missing_or_broken_model_artifacts_fail_explicitly(self):
        """Missing or corrupted model artifacts raise explicit InferenceError and do not use static fixtures."""
        bad_dir = Path("non_existent_model_dir_xyz")
        predictor = V1Predictor(model_dir=bad_dir)
        with self.assertRaises(InferenceError) as cm:
            predictor.load()
        self.assertIn("Model metadata not found", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
