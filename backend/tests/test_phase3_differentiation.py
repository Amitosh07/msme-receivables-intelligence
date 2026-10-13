"""Phase 3: estimate explanations, cash-flow forecast, customer balances, demo path."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import math
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.models.payment import Payment
from backend.app.models.prediction import PredictionResult
from backend.app.models.user import User
from backend.app.services.cashflow_service import build_cashflow_forecast
from backend.app.services.customer_balance_service import list_customer_balances
from backend.app.services.demo_service import DEMO_CUSTOMER_NAME
from backend.app.services.prediction_service import predict_for_invoice

TODAY = date(2026, 10, 12)


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


class Phase3TestCase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        self.tenants: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.business_id, self.token = self._register("p3_a")
        self.other_business_id, self.other_token = self._register("p3_b")

    def tearDown(self):
        try:
            self.db.rollback()
            for business_id, user_id in self.tenants:
                business = self.db.get(Business, business_id)
                if business:
                    self.db.delete(business)
                user = self.db.get(User, user_id)
                if user:
                    self.db.delete(user)
            self.db.commit()
        except Exception:
            self.db.rollback()
        finally:
            self.db.close()

    def _register(self, prefix: str) -> tuple[uuid.UUID, str]:
        suffix = uuid.uuid4().hex[:8]
        response = self.client.post("/auth/register", json={
            "email": f"{prefix}_{suffix}@corp.com",
            "password": "Password123!",
            "full_name": "Phase Three Tester",
            "business_name": f"Phase3 Corp {prefix} {suffix}",
        })
        self.assertEqual(response.status_code, 201)
        data = response.json()
        business_id = uuid.UUID(data["business"]["id"])
        self.tenants.append((business_id, uuid.UUID(data["user"]["id"])))
        return business_id, data["access_token"]

    def _auth(self, token: str | None = None) -> dict:
        return {"Authorization": f"Bearer {token or self.token}"}

    def _customer(self, name: str, business_id: uuid.UUID | None = None) -> Customer:
        customer = Customer(business_id=business_id or self.business_id, display_name=name)
        self.db.add(customer)
        self.db.flush()
        return customer

    def _invoice(self, number: str, amount: str, due: date | None, **extra) -> Invoice:
        invoice = Invoice(
            business_id=extra.pop("business_id", self.business_id),
            invoice_number=number,
            invoice_date=extra.pop("invoice_date", date(2026, 8, 1)),
            due_date=due,
            amount=Decimal(amount),
            currency="INR",
            origin=extra.pop("origin", InvoiceOrigin.CURRENT.value),
            processing_status="PROCESSED",
            payment_status=extra.pop("payment_status", "OPEN"),
            **extra,
        )
        self.db.add(invoice)
        self.db.flush()
        return invoice

    def _pay(self, invoice: Invoice, amount: str, day: date) -> None:
        self.db.add(Payment(
            business_id=invoice.business_id,
            invoice_id=invoice.id,
            invoice_reference=invoice.invoice_number,
            payment_date=_at(day),
            amount=Decimal(amount),
            customer_identity_key=f"customer:{invoice.customer_id}" if invoice.customer_id else None,
            provenance="manual",
        ))
        self.db.flush()

    def _history(self, customer: Customer, delays=(3, -2, 6)) -> None:
        """Three fully paid historical invoices, settled `delays` days after due."""
        for month, delay in zip((1, 2, 3), delays):
            due = date(2026, month, 28)
            past = self._invoice(
                f"H-{customer.display_name[:4]}-{month}", "1000", due,
                customer_id=customer.id, invoice_date=date(2026, month, 1),
                origin=InvoiceOrigin.HISTORICAL.value, payment_status="PAID",
            )
            self._pay(past, "1000", due + timedelta(days=delay))

    def _predict(self, invoice: Invoice, expected: date, tier: str = "MEDIUM", score: str = "0.5") -> None:
        self.db.add(PredictionResult(
            business_id=invoice.business_id, invoice_id=invoice.id, prediction=tier == "HIGH",
            risk_score=Decimal(score), risk_tier=tier, expected_payment_date=expected,
        ))
        self.db.flush()


class TestPredictionExplanation(Phase3TestCase):
    def _scored_invoice(self) -> Invoice:
        acme = self._customer("Acme Buyers")
        self._history(acme)
        invoice = self._invoice("CUR-EXPLAIN", "25000", date(2026, 10, 1), customer_id=acme.id,
                                invoice_date=date(2026, 9, 1), payment_terms="Net 30")
        self.db.commit()
        predict_for_invoice(self.db, invoice.id, self.business_id)
        return invoice

    def test_explanation_reproduces_the_stored_score(self):
        invoice = self._scored_invoice()
        response = self.client.get(f"/invoices/{invoice.id}/prediction/explanation", headers=self._auth())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        logit = math.log(body["baseline_risk"] / (1 - body["baseline_risk"]))
        logit += sum(factor["contribution"] for factor in body["factors"])
        self.assertAlmostEqual(1 / (1 + math.exp(-logit)), body["risk_score"], places=3)
        self.assertTrue(body["matches_stored"])
        self.assertAlmostEqual(body["risk_score"], body["stored_risk_score"], places=3)

        self.assertEqual(len(body["factors"]), 8)
        self.assertAlmostEqual(sum(f["share"] for f in body["factors"]), 1.0, places=2)
        effects = [abs(f["contribution"]) for f in body["factors"]]
        self.assertEqual(effects, sorted(effects, reverse=True))
        by_key = {f["key"]: f for f in body["factors"]}
        self.assertEqual(by_key["late_history"]["evidence"], "Paid late on 2 of 3 settled invoices")
        self.assertIn("3 days after the due date (median)", by_key["typical_delay"]["evidence"])
        self.assertIn("30 days from invoice date to due date (terms: Net 30)", by_key["credit_terms"]["evidence"])
        self.assertEqual(by_key["amount"]["evidence"], "₹25,000.00")
        self.assertIn("currency", body["unknown_categories"])

    def test_explanation_is_tenant_scoped_and_gated(self):
        invoice = self._scored_invoice()
        self.assertEqual(
            self.client.get(f"/invoices/{invoice.id}/prediction/explanation", headers=self._auth(self.other_token)).status_code,
            404,
        )
        newcomer = self._customer("Newco Retail")
        fresh = self._invoice("CUR-NEW", "5000", date(2026, 10, 30), customer_id=newcomer.id)
        self._predict(fresh, date(2026, 11, 2))
        self.db.commit()
        response = self.client.get(f"/invoices/{fresh.id}/prediction/explanation", headers=self._auth())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(f"/invoices/{invoice.id}/prediction/explanation").status_code, 401)

    def test_model_insights_report_grouped_importance_and_limits(self):
        response = self.client.get("/predictions/model-insights", headers=self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertAlmostEqual(sum(d["importance"] for d in body["risk_drivers"]), 1.0, places=3)
        self.assertAlmostEqual(sum(d["importance"] for d in body["timing_drivers"]), 1.0, places=3)
        self.assertEqual(
            {d["key"] for d in body["risk_drivers"][:3]},
            {"typical_delay", "late_history", "due_date_timing"},
        )
        self.assertAlmostEqual(body["roc_auc"], 0.84558, places=4)
        self.assertEqual(body["required_history"], 3)
        self.assertEqual(len(body["limitations"]), 4)
        self.assertEqual(self.client.get("/predictions/model-insights").status_code, 401)


class TestForecastAndBalances(Phase3TestCase):
    def _seed(self) -> dict[str, Invoice]:
        acme = self._customer("Acme Buyers")
        self._history(acme)
        newco = self._customer("Newco Retail")
        settled = self._customer("Settled Co")

        seeded = {
            "predicted_week0": self._invoice("A", "1000", TODAY + timedelta(days=2), customer_id=acme.id),
            "past_predicted": self._invoice("B", "2000", TODAY - timedelta(days=10), customer_id=acme.id),
            "partial_week1": self._invoice("G", "10000", TODAY + timedelta(days=5), customer_id=acme.id,
                                           payment_status="PARTIAL"),
            "due_week1": self._invoice("C", "300", TODAY + timedelta(days=10), unresolved_customer_name="Beta Traders"),
            "overdue_unestimated": self._invoice("D", "400", TODAY - timedelta(days=5),
                                                 unresolved_customer_name="Beta Traders"),
            "undated": self._invoice("E", "50", None, unresolved_customer_name="Gamma Stores"),
            "beyond": self._invoice("F", "700", TODAY + timedelta(days=100), customer_id=newco.id),
            "ineligible_week0": self._invoice("H", "800", TODAY + timedelta(days=1), customer_id=newco.id),
        }
        self._predict(seeded["predicted_week0"], TODAY + timedelta(days=3), "HIGH", "0.8")
        self._predict(seeded["past_predicted"], TODAY - timedelta(days=2))
        self._pay(seeded["partial_week1"], "4000", date(2026, 9, 20))
        self._predict(seeded["partial_week1"], TODAY + timedelta(days=8), "LOW", "0.2")
        self._predict(seeded["ineligible_week0"], TODAY + timedelta(days=40), "HIGH", "0.9")
        paid = self._invoice("PAID", "999", TODAY - timedelta(days=30), customer_id=settled.id,
                             payment_status="PAID")
        self._pay(paid, "999", TODAY - timedelta(days=31))
        self._invoice("HIST-OPEN", "88888", TODAY, origin=InvoiceOrigin.HISTORICAL.value)
        self._invoice("OTHER", "77777", TODAY, business_id=self.other_business_id)
        self.db.commit()
        return seeded

    def test_forecast_places_each_balance_once_with_explicit_basis(self):
        self._seed()
        forecast = build_cashflow_forecast(self.db, self.business_id, today=TODAY)

        self.assertEqual((forecast.outstanding.amount, forecast.outstanding.count), (11250.0, 8))
        week0, week1 = forecast.weeks[0], forecast.weeks[1]
        self.assertEqual((week0.start, week0.end), (TODAY, TODAY + timedelta(days=6)))
        self.assertEqual((week0.predicted, week0.due_date_based, week0.high_risk), (1000.0, 800.0, 1000.0))
        self.assertEqual((week1.predicted, week1.due_date_based, week1.total), (6000.0, 300.0, 6300.0))
        h = next(i for i in week0.invoices if i.invoice_number == "H")
        self.assertEqual((h.basis, h.expected_date, h.risk_tier), ("due_date", TODAY + timedelta(days=1), None))

        unscheduled = {u.key: (u.amount, u.count) for u in forecast.unscheduled}
        self.assertEqual(unscheduled, {
            "past_predicted": (2000.0, 1),
            "overdue_unestimated": (400.0, 1),
            "beyond_horizon": (700.0, 1),
            "undated": (50.0, 1),
        })
        placed = forecast.scheduled.amount + sum(u.amount for u in forecast.unscheduled)
        self.assertAlmostEqual(placed, forecast.outstanding.amount, places=2)
        self.assertEqual((forecast.model_dated.amount, forecast.model_dated.count), (7000.0, 2))
        self.assertEqual((forecast.due_date_dated.amount, forecast.due_date_dated.count), (1800.0, 3))
        self.assertAlmostEqual(forecast.model_coverage, 9000 / 11250, places=4)

        text = " ".join(forecast.assumptions)
        self.assertIn("8 current invoices still being collected (₹11,250.00)", text)
        self.assertIn("(2 invoices, ₹7,000.00)", text)
        self.assertIn("Otherwise the due date is used (3 invoices, ₹1,800.00)", text)
        self.assertIn("paid in full, in a single payment", text)
        self.assertIn("not subtracted", text)

    def test_forecast_endpoint_is_tenant_scoped(self):
        self._seed()
        mine = self.client.get("/dashboard/cashflow?weeks=4", headers=self._auth())
        self.assertEqual(mine.status_code, 200)
        self.assertEqual(len(mine.json()["weeks"]), 4)
        theirs = self.client.get("/dashboard/cashflow", headers=self._auth(self.other_token)).json()
        self.assertEqual(theirs["outstanding"], {"amount": 77777.0, "count": 1})
        self.assertEqual(self.client.get("/dashboard/cashflow?weeks=27", headers=self._auth()).status_code, 422)

    def test_customer_balances_combine_balances_and_payment_record(self):
        self._seed()
        result = list_customer_balances(self.db, self.business_id, today=TODAY)
        rows = {r.display_name: r for r in result.customers}

        acme = rows["Acme Buyers"]
        self.assertEqual((acme.outstanding, acme.overdue), (9000.0, 2000.0))
        self.assertEqual((acme.open_invoice_count, acme.overdue_invoice_count, acme.oldest_days_overdue), (3, 1, 10))
        self.assertEqual(acme.next_due_date, TODAY + timedelta(days=2))
        self.assertEqual(acme.next_expected_payment, TODAY + timedelta(days=3))
        self.assertEqual(acme.highest_risk_tier, "HIGH")
        self.assertEqual((acme.settled_invoice_count, acme.on_time_rate, acme.average_days_late), (3, 0.3333, 2.3))
        self.assertTrue(acme.estimates_ready and acme.has_history and acme.matched)

        newco = rows["Newco Retail"]
        self.assertIsNone(newco.highest_risk_tier)
        self.assertEqual((newco.settled_invoice_count, newco.estimates_ready), (0, False))

        beta = rows["Beta Traders"]
        self.assertFalse(beta.matched)
        self.assertIsNone(beta.customer_id)
        self.assertEqual((beta.outstanding, beta.overdue, beta.oldest_days_overdue), (700.0, 400.0, 5))

        settled = rows["Settled Co"]
        self.assertEqual((settled.outstanding, settled.settled_invoice_count, settled.on_time_rate), (0.0, 1, 1.0))

        self.assertEqual(result.total_outstanding, 11250.0)
        self.assertEqual(result.customers_owing, 4)
        self.assertEqual(result.customers[0].display_name, "Acme Buyers")
        self.assertAlmostEqual(result.top_customer_share, 9000 / 11250, places=3)
        self.assertAlmostEqual(sum(r.share_of_outstanding for r in result.customers), 1.0, places=3)

    def test_customer_balances_endpoint_is_tenant_scoped(self):
        self._seed()
        mine = self.client.get("/customers/balances", headers=self._auth())
        self.assertEqual(mine.status_code, 200, mine.text)
        self.assertIn("Acme Buyers", [c["display_name"] for c in mine.json()["customers"]])
        theirs = self.client.get("/customers/balances", headers=self._auth(self.other_token)).json()
        self.assertNotIn("Acme Buyers", [c["display_name"] for c in theirs["customers"]])
        self.assertEqual(theirs["total_outstanding"], 77777.0)


class TestScriptedDemoPath(Phase3TestCase):
    def _demo_invoice_count(self, business_id: uuid.UUID) -> int:
        return self.db.scalar(select(func.count()).select_from(Invoice).where(
            Invoice.business_id == business_id, Invoice.invoice_number.like("DEMO-ST-%")
        ))

    def test_demo_runs_reuses_and_removes_only_its_records(self):
        real = self._customer("Real Buyer")
        self._invoice("REAL-1", "1234", TODAY + timedelta(days=5), customer_id=real.id)
        self.db.commit()

        first = self.client.post("/demo/scripted-path", headers=self._auth())
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertEqual(
            [s["key"] for s in body["steps"]],
            ["customer", "paid_1", "paid_2", "paid_3", "open", "gate", "score"],
        )
        self.assertEqual([s["history_count"] for s in body["steps"][1:4]], [1, 2, 3])
        self.assertTrue(all(s["created"] for s in body["steps"][:5]))
        self.assertEqual((body["eligible_history_count"], body["required_history"]), (3, 3))
        self.assertTrue(0 <= body["prediction"]["risk_score"] <= 1)
        self.assertEqual(body["prediction"]["invoice_id"], body["open_invoice_id"])
        self.assertTrue(body["explanation"]["matches_stored"])
        self.assertIn("4 days late", body["steps"][1]["detail"])

        summary = self.client.get("/dashboard/summary", headers=self._auth()).json()
        self.assertEqual(summary["outstanding"]["amount"], 1234.0 + 51800.0)

        again = self.client.post("/demo/scripted-path", headers=self._auth()).json()
        self.assertEqual(again["open_invoice_id"], body["open_invoice_id"])
        self.assertFalse(any(s["created"] for s in again["steps"][:5]))
        self.assertEqual(self._demo_invoice_count(self.business_id), 4)

        status = self.client.get("/demo/scripted-path", headers=self._auth()).json()
        self.assertEqual((status["exists"], status["scored"]), (True, True))
        self.assertFalse(self.client.get("/demo/scripted-path", headers=self._auth(self.other_token)).json()["exists"])

        removed = self.client.delete("/demo/scripted-path", headers=self._auth())
        self.assertEqual(removed.json(), {"removed_invoices": 4, "removed_payments": 3, "removed_customer": True})
        self.db.expire_all()
        self.assertEqual(self._demo_invoice_count(self.business_id), 0)
        self.assertIsNotNone(self.db.get(Customer, real.id))
        self.assertEqual(self.client.get("/demo/scripted-path", headers=self._auth()).json()["exists"], False)
        self.assertEqual(
            self.client.get("/dashboard/summary", headers=self._auth()).json()["outstanding"]["amount"], 1234.0
        )

    def test_demo_refuses_to_touch_a_real_customer_with_the_demo_name(self):
        self._customer(DEMO_CUSTOMER_NAME)
        self.db.commit()
        response = self.client.post("/demo/scripted-path", headers=self._auth())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._demo_invoice_count(self.business_id), 0)
        self.assertEqual(self.client.delete("/demo/scripted-path", headers=self._auth()).json()["removed_invoices"], 0)


if __name__ == "__main__":
    unittest.main()
