"""
Tests for Phase 6: ML/Application Integration.
Tests feature engineering (as-of isolation, cold-start imputation, schema validation),
V1Predictor model inference, prediction persistence, tenant isolation,
worker task routing ('predict_invoice', 'score_invoice'), and prediction API endpoints.
"""

from datetime import date, datetime, timedelta, timezone
import unittest
import uuid

from fastapi.testclient import TestClient
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.payment import Payment
from backend.app.models.prediction import PredictionResult
from backend.app.models.task import Task
from backend.app.services.prediction_service import (
    COLD_START_IMPUTATION,
    FEATURE_COLUMNS,
    PROHIBITED_COLUMNS,
    InvoiceNotReadyError,
    PredictionServiceError,
    build_inference_features,
    get_prediction_for_invoice,
    list_predictions_for_tenant,
    predict_for_invoice,
)
from backend.app.services.task_service import create_task
from backend.app.workers.queue import get_task_queue
from backend.app.workers.runtime import WorkerService


class TestPredictionIntegration(unittest.TestCase):
    """Full test suite for ML inference, feature pipeline, API and worker integration."""

    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        self.queue = get_task_queue()
        self.worker = WorkerService(queue=self.queue)
        self.queue.clear()

        # Register Business A
        self.suffix_a = uuid.uuid4().hex[:8]
        res_a = self.client.post("/auth/register", json={
            "email": f"ml_user_a_{self.suffix_a}@corp.com",
            "password": "Password123!",
            "full_name": "ML Admin A",
            "business_name": f"ML Enterprise A {self.suffix_a}",
        })
        self.assertEqual(res_a.status_code, 201)
        data_a = res_a.json()
        self.token_a = data_a["access_token"]
        self.headers_a = {"Authorization": f"Bearer {self.token_a}"}
        self.business_a_id = uuid.UUID(data_a["business"]["id"])

        # Register Business B
        self.suffix_b = uuid.uuid4().hex[:8]
        res_b = self.client.post("/auth/register", json={
            "email": f"ml_user_b_{self.suffix_b}@corp.com",
            "password": "Password123!",
            "full_name": "ML Admin B",
            "business_name": f"ML Enterprise B {self.suffix_b}",
        })
        self.assertEqual(res_b.status_code, 201)
        data_b = res_b.json()
        self.token_b = data_b["access_token"]
        self.headers_b = {"Authorization": f"Bearer {self.token_b}"}
        self.business_b_id = uuid.UUID(data_b["business"]["id"])

        # Create a sample customer for Business A
        self.customer_a = Customer(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            name="Acme Industrial",
            customer_ref="CUST-ACME-001",
        )
        self.db.add(self.customer_a)
        self.db.commit()

    def tearDown(self):
        self.queue.clear()
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

    def _create_invoice(
        self,
        business_id: uuid.UUID,
        customer_id: uuid.UUID,
        invoice_number: str | None = None,
        amount: float = 25000.0,
        invoice_date: date = date(2024, 4, 1),
        due_date: date = date(2024, 4, 21),
        payment_terms: str = "NAA8",
        currency: str = "USD",
        processing_status: str = "PROCESSED",
    ) -> Invoice:
        if invoice_number is None:
            invoice_number = f"INV-{uuid.uuid4().hex[:8].upper()}"
        inv = Invoice(
            id=uuid.uuid4(),
            business_id=business_id,
            customer_id=customer_id,
            invoice_number=invoice_number,
            amount=amount,
            currency=currency,
            invoice_date=invoice_date,
            due_date=due_date,
            payment_terms=payment_terms,
            payment_status="OPEN",
            processing_status=processing_status,
        )
        self.db.add(inv)
        self.db.commit()
        self.db.refresh(inv)
        return inv

    def test_feature_row_schema_and_no_leakage(self):
        """Verify feature row has exactly 29 columns and zero prohibited leakage columns."""
        inv = self._create_invoice(self.business_a_id, self.customer_a.id)
        df = build_inference_features(self.db, inv)

        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 1)
        self.assertEqual(list(df.columns), FEATURE_COLUMNS)
        self.assertEqual(len(df.columns), 29)

        # Assert no prohibited leakage columns
        for col in PROHIBITED_COLUMNS:
            self.assertNotIn(col, df.columns)

        # Check basic feature values
        self.assertEqual(df["amount"].iloc[0], 25000.0)
        self.assertEqual(df["payment_terms"].iloc[0], "NAA8")
        self.assertEqual(df["currency"].iloc[0], "USD")

    def test_classifier_and_timing_inference_bounds(self):
        """Verify ML models output valid risk scores, tiers, non-negative days, and dates."""
        inv = self._create_invoice(self.business_a_id, self.customer_a.id, amount=15000.0)
        pred = predict_for_invoice(self.db, inv.id, self.business_a_id)

        self.assertIsNotNone(pred)
        self.assertEqual(pred.invoice_id, inv.id)
        self.assertEqual(pred.business_id, self.business_a_id)

        # Check classifier bounds
        self.assertGreaterEqual(pred.risk_score, 0.0)
        self.assertLessEqual(pred.risk_score, 1.0)
        self.assertIn(pred.risk_tier, ["LOW", "MEDIUM", "HIGH"])
        self.assertIn(pred.prediction, [True, False])

        # Check timing bounds & derived expected date
        self.assertGreaterEqual(pred.predicted_days_until_payment, 0.0)
        expected_date = inv.invoice_date + timedelta(days=int(round(pred.predicted_days_until_payment)))
        self.assertEqual(pred.expected_payment_date, expected_date)

    def test_cold_start_customer_imputation(self):
        """New customer with 0 prior history correctly gets frozen training medians."""
        new_cust = Customer(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            name="Brand New Customer",
            customer_ref="CUST-NEW-999",
        )
        self.db.add(new_cust)
        self.db.commit()

        inv = self._create_invoice(self.business_a_id, new_cust.id)
        df = build_inference_features(self.db, inv)

        self.assertEqual(df["is_new_customer"].iloc[0], 1)
        self.assertEqual(df["cust_prior_invoice_count"].iloc[0], 0)
        self.assertEqual(df["cust_prior_payment_count"].iloc[0], 0)
        self.assertEqual(df["cust_prior_late_count"].iloc[0], 0)

        # Frozen training partition medians
        self.assertAlmostEqual(df["cust_late_payment_rate"].iloc[0], COLD_START_IMPUTATION["cust_late_payment_rate"], places=4)
        self.assertAlmostEqual(df["cust_avg_delay"].iloc[0], COLD_START_IMPUTATION["cust_avg_delay"], places=4)
        self.assertAlmostEqual(df["days_since_last_payment"].iloc[0], COLD_START_IMPUTATION["days_since_last_payment"], places=4)
        self.assertAlmostEqual(df["days_since_prev_invoice"].iloc[0], COLD_START_IMPUTATION["days_since_prev_invoice"], places=4)

        # Prediction runs smoothly without NaN
        pred = predict_for_invoice(self.db, inv.id, self.business_a_id)
        self.assertIsNotNone(pred.risk_score)
        self.assertIsNotNone(pred.risk_tier)

    def test_as_of_historical_isolation(self):
        """Future invoices and payments strictly after invoice_date (T) are not leaked into features."""
        # Prior invoice: posted 2024-01-01, due 2024-01-15, paid 2024-01-20 (late by 5 days)
        inv_prior = self._create_invoice(
            self.business_a_id, self.customer_a.id,
            invoice_number="INV-PRIOR",
            amount=5000.0,
            invoice_date=date(2024, 1, 1),
            due_date=date(2024, 1, 15),
        )
        pay_prior = Payment(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            invoice_id=inv_prior.id,
            payment_date=datetime(2024, 1, 20, tzinfo=timezone.utc),
            amount=5000.0,
            reference="PAY-PRIOR",
        )
        self.db.add(pay_prior)

        # Target invoice: posted 2024-02-01
        inv_target = self._create_invoice(
            self.business_a_id, self.customer_a.id,
            invoice_number="INV-TARGET",
            amount=10000.0,
            invoice_date=date(2024, 2, 1),
            due_date=date(2024, 2, 20),
        )

        # Future invoice & payment: posted 2024-03-01, paid 2024-03-25 (late by 10 days)
        inv_future = self._create_invoice(
            self.business_a_id, self.customer_a.id,
            invoice_number="INV-FUTURE",
            amount=12000.0,
            invoice_date=date(2024, 3, 1),
            due_date=date(2024, 3, 15),
        )
        pay_future = Payment(
            id=uuid.uuid4(),
            business_id=self.business_a_id,
            invoice_id=inv_future.id,
            payment_date=datetime(2024, 3, 25, tzinfo=timezone.utc),
            amount=12000.0,
            reference="PAY-FUTURE",
        )
        self.db.add(pay_future)
        self.db.commit()

        # Build features for TARGET invoice (as-of 2024-02-01)
        df = build_inference_features(self.db, inv_target)

        # Should only see 1 prior invoice and 1 prior payment, not 2!
        self.assertEqual(df["cust_prior_invoice_count"].iloc[0], 1)
        self.assertEqual(df["cust_prior_payment_count"].iloc[0], 1)
        self.assertEqual(df["cust_prior_late_count"].iloc[0], 1)
        self.assertEqual(df["cust_late_payment_rate"].iloc[0], 1.0)
        self.assertEqual(df["cust_avg_delay"].iloc[0], 5.0)
        # With 1 prior invoice (< 3), is_new_customer is 1 per Phase 1 contract
        self.assertEqual(df["is_new_customer"].iloc[0], 1)

        # Add 2 more prior invoices to reach 3 prior invoices (cust_prior_invoice_count >= 3 -> is_new_customer = 0)
        self._create_invoice(
            self.business_a_id, self.customer_a.id,
            invoice_number="INV-PRIOR-2",
            amount=3000.0,
            invoice_date=date(2024, 1, 5),
            due_date=date(2024, 1, 25),
        )
        self._create_invoice(
            self.business_a_id, self.customer_a.id,
            invoice_number="INV-PRIOR-3",
            amount=4000.0,
            invoice_date=date(2024, 1, 10),
            due_date=date(2024, 1, 30),
        )
        df3 = build_inference_features(self.db, inv_target)
        self.assertEqual(df3["cust_prior_invoice_count"].iloc[0], 3)
        self.assertEqual(df3["is_new_customer"].iloc[0], 0)

    def test_prediction_idempotency(self):
        """Scoring the same invoice multiple times updates the existing row without duplicate keys."""
        inv = self._create_invoice(self.business_a_id, self.customer_a.id, amount=8000.0)

        # First prediction
        pred1 = predict_for_invoice(self.db, inv.id, self.business_a_id)
        first_id = pred1.id
        first_risk = pred1.risk_score

        # Second prediction
        pred2 = predict_for_invoice(self.db, inv.id, self.business_a_id)
        self.assertEqual(pred1.id, pred2.id)
        self.assertEqual(first_id, pred2.id)

        # Total rows in DB for this invoice must be exactly 1
        all_preds = list(
            self.db.scalars(
                select(PredictionResult).where(PredictionResult.invoice_id == inv.id)
            ).all()
        )
        self.assertEqual(len(all_preds), 1)

    def test_failed_or_incomplete_invoice_rejected(self):
        """Invoices with ERROR status or invalid amount cannot be scored."""
        inv_error = self._create_invoice(
            self.business_a_id, self.customer_a.id,
            processing_status="ERROR",
        )
        with self.assertRaises(InvoiceNotReadyError):
            predict_for_invoice(self.db, inv_error.id, self.business_a_id)

        inv_bad_amount = self._create_invoice(
            self.business_a_id, self.customer_a.id,
            amount=-100.0,
        )
        with self.assertRaises(InvoiceNotReadyError):
            predict_for_invoice(self.db, inv_bad_amount.id, self.business_a_id)

    def test_tenant_boundary_isolation(self):
        """Tenant B cannot score or read Tenant A's invoice prediction."""
        inv_a = self._create_invoice(self.business_a_id, self.customer_a.id, amount=30000.0)

        # 1. Service level check
        with self.assertRaises(PredictionServiceError):
            predict_for_invoice(self.db, inv_a.id, self.business_b_id)

        # Generate valid prediction for Tenant A
        pred_a = predict_for_invoice(self.db, inv_a.id, self.business_a_id)

        # 2. API level check: Tenant B GET invoice prediction -> 404
        res_get_b = self.client.get(f"/invoices/{inv_a.id}/prediction", headers=self.headers_b)
        self.assertEqual(res_get_b.status_code, 404)

        # 3. API level check: Tenant B POST invoice predict -> 404
        res_post_b = self.client.post(f"/invoices/{inv_a.id}/predict", headers=self.headers_b)
        self.assertEqual(res_post_b.status_code, 404)

        # 4. API level check: Tenant B list predictions returns 0 items
        res_list_b = self.client.get("/predictions", headers=self.headers_b)
        self.assertEqual(res_list_b.status_code, 200)
        self.assertEqual(res_list_b.json()["total"], 0)

        # Tenant A can read it fine
        res_get_a = self.client.get(f"/invoices/{inv_a.id}/prediction", headers=self.headers_a)
        self.assertEqual(res_get_a.status_code, 200)
        self.assertEqual(res_get_a.json()["risk_tier"], pred_a.risk_tier)

    def test_worker_task_routing_predict_invoice(self):
        """Worker routes and executes 'predict_invoice' and 'score_invoice' background tasks."""
        inv = self._create_invoice(self.business_a_id, self.customer_a.id, amount=45000.0)

        # 1. Test predict_invoice task type
        payload1 = {"invoice_id": str(inv.id)}
        task = create_task(
            db=self.db,
            business_id=self.business_a_id,
            task_type="predict_invoice",
            payload=payload1,
        )
        self.assertEqual(task.status, "PENDING")
        self.queue.enqueue(
            task_id=task.id,
            task_type=task.task_type,
            business_id=task.business_id,
            payload=payload1,
        )

        processed = self.worker.process_one_task(timeout=1)
        self.assertTrue(processed)

        self.db.refresh(task)
        self.assertEqual(task.status, "COMPLETED")
        self.assertEqual(task.invoice_id, inv.id)

        pred = get_prediction_for_invoice(self.db, self.business_a_id, inv.id)
        self.assertIsNotNone(pred)
        self.assertIn(pred.risk_tier, ["LOW", "MEDIUM", "HIGH"])

        # 2. Test score_invoice task type (alias)
        payload2 = {"invoice_id": str(inv.id)}
        task2 = create_task(
            db=self.db,
            business_id=self.business_a_id,
            task_type="score_invoice",
            payload=payload2,
        )
        self.queue.enqueue(
            task_id=task2.id,
            task_type=task2.task_type,
            business_id=task2.business_id,
            payload=payload2,
        )
        processed2 = self.worker.process_one_task(timeout=1)
        self.assertTrue(processed2)
        self.db.refresh(task2)
        self.assertEqual(task2.status, "COMPLETED")

    def test_prediction_api_endpoints(self):
        """Test POST /invoices/{id}/predict, GET /invoices/{id}/prediction, and GET /predictions."""
        inv = self._create_invoice(self.business_a_id, self.customer_a.id, amount=18000.0)

        # 1. POST /invoices/{id}/predict
        res_post = self.client.post(f"/invoices/{inv.id}/predict", headers=self.headers_a)
        self.assertEqual(res_post.status_code, 200)
        data = res_post.json()
        self.assertEqual(data["invoice_id"], str(inv.id))
        self.assertIn(data["risk_tier"], ["LOW", "MEDIUM", "HIGH"])
        self.assertGreaterEqual(data["risk_score"], 0.0)
        self.assertLessEqual(data["risk_score"], 1.0)
        self.assertGreaterEqual(data["predicted_days_until_payment"], 0.0)
        self.assertIsNotNone(data["expected_payment_date"])

        # 2. GET /invoices/{id}/prediction
        res_get = self.client.get(f"/invoices/{inv.id}/prediction", headers=self.headers_a)
        self.assertEqual(res_get.status_code, 200)
        self.assertEqual(res_get.json()["id"], data["id"])

        # 3. GET /predictions
        res_list = self.client.get("/predictions", headers=self.headers_a)
        self.assertEqual(res_list.status_code, 200)
        list_data = res_list.json()
        self.assertGreaterEqual(list_data["total"], 1)
        self.assertEqual(list_data["items"][0]["invoice_id"], str(inv.id))

        # 4. Filter by risk tier
        tier = data["risk_tier"]
        res_filtered = self.client.get(f"/predictions?risk_tier={tier}", headers=self.headers_a)
        self.assertEqual(res_filtered.status_code, 200)
        self.assertGreaterEqual(res_filtered.json()["total"], 1)

        other_tier = "HIGH" if tier != "HIGH" else "LOW"
        res_other = self.client.get(f"/predictions?risk_tier={other_tier}", headers=self.headers_a)
        self.assertEqual(res_other.status_code, 200)
        # Should either be 0 or other predictions
        for item in res_other.json()["items"]:
            self.assertEqual(item["risk_tier"], other_tier)


if __name__ == "__main__":
    unittest.main()
