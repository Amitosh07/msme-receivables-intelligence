"""Dashboard summary: CURRENT-only, remaining balances, due-soon, ranked follow-up."""

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.models.payment import Payment
from backend.app.models.prediction import PredictionResult
from backend.app.models.user import User
from backend.app.services.dashboard_service import (
    aging_bucket,
    build_dashboard_summary,
    priority_weight,
)

TODAY = date(2026, 10, 12)


def _paid_on(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


class TestDashboardSummary(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        self.tenants: list[tuple[uuid.UUID, uuid.UUID, str]] = []
        self.business_id, self.token = self._register("dash_a")
        self.other_business_id, self.other_token = self._register("dash_b")

    def tearDown(self):
        try:
            for business_id, user_id, _ in self.tenants:
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
        response = self.client.post(
            "/auth/register",
            json={
                "email": f"{prefix}_{suffix}@corp.com",
                "password": "Password123!",
                "full_name": "Dashboard Tester",
                "business_name": f"Dashboard Corp {prefix} {suffix}",
            },
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        business_id = uuid.UUID(data["business"]["id"])
        self.tenants.append((business_id, uuid.UUID(data["user"]["id"]), data["access_token"]))
        return business_id, data["access_token"]

    def _invoice(self, number: str, amount: str, due: date | None, **extra) -> Invoice:
        invoice = Invoice(
            business_id=extra.pop("business_id", self.business_id),
            invoice_number=number,
            invoice_date=extra.pop("invoice_date", None),
            due_date=due,
            amount=Decimal(amount),
            currency="INR",
            origin=extra.pop("origin", InvoiceOrigin.CURRENT.value),
            processing_status="PROCESSED",
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
            payment_date=_paid_on(day),
            amount=Decimal(amount),
            customer_identity_key=f"customer:{invoice.customer_id}" if invoice.customer_id else None,
            provenance="manual",
        ))
        self.db.flush()

    def _seed(self) -> dict[str, Invoice]:
        acme = Customer(business_id=self.business_id, display_name="Acme Buyers", normalized_name="acme buyers")
        self.db.add(acme)
        self.db.flush()
        for month in (1, 2, 3):
            past = self._invoice(
                f"HIST-{month}", "1000", date(2026, month, 28),
                customer_id=acme.id, invoice_date=date(2026, month, 1),
                origin=InvoiceOrigin.HISTORICAL.value, payment_status="PAID",
            )
            self._pay(past, "1000", date(2026, month + 1, 5))

        overdue = self._invoice(
            "CUR-OVERDUE", "10000", date(2026, 9, 1),
            customer_id=acme.id, invoice_date=date(2026, 8, 1), payment_status="PARTIAL",
        )
        self._pay(overdue, "4000", date(2026, 9, 20))
        self.db.add(PredictionResult(
            business_id=self.business_id, invoice_id=overdue.id, prediction=True,
            risk_score=Decimal("0.8"), risk_tier="HIGH", expected_payment_date=date(2026, 10, 20),
        ))
        due_soon = self._invoice(
            "CUR-SOON", "2500.50", date(2026, 10, 15), unresolved_customer_name="Beta Traders",
        )
        later = self._invoice("CUR-LATER", "1000", date(2026, 11, 30))
        paid = self._invoice("CUR-PAID", "500", date(2026, 9, 1), payment_status="PAID")
        self._pay(paid, "500", date(2026, 9, 2))
        undated = self._invoice("CUR-UNDATED", "300", None)
        self._invoice("HIST-OPEN", "99999", date(2026, 8, 1), origin=InvoiceOrigin.HISTORICAL.value)
        self._invoice("OTHER-TENANT", "77777", date(2026, 9, 1), business_id=self.other_business_id)
        self.db.commit()
        return {"overdue": overdue, "due_soon": due_soon, "later": later, "undated": undated}

    def test_aging_buckets_do_not_overlap(self):
        self.assertEqual(aging_bucket(-5), "current")
        self.assertEqual(aging_bucket(0), "current")
        self.assertEqual(aging_bucket(1), "1-30")
        self.assertEqual(aging_bucket(30), "1-30")
        self.assertEqual(aging_bucket(31), "31-60")
        self.assertEqual(aging_bucket(61), "61-90")
        self.assertEqual(aging_bucket(91), "90+")

    def test_priority_weight_favours_lateness_and_risk(self):
        self.assertGreater(priority_weight(45, None), priority_weight(5, None))
        self.assertGreater(priority_weight(5, None), priority_weight(-3, None))
        self.assertGreater(priority_weight(-1, None), priority_weight(-6, None))
        self.assertGreater(priority_weight(10, 0.9), priority_weight(10, 0.1))

    def test_summary_uses_current_remaining_balances(self):
        seeded = self._seed()
        summary = build_dashboard_summary(self.db, self.business_id, today=TODAY)

        self.assertEqual(summary.current_invoice_count, 5)
        self.assertEqual((summary.outstanding.amount, summary.outstanding.count), (9800.50, 4))
        self.assertEqual((summary.overdue.amount, summary.overdue.count), (6000.0, 1))
        self.assertEqual((summary.due_soon.amount, summary.due_soon.count), (2500.50, 1))
        self.assertEqual((summary.high_risk.amount, summary.high_risk.count), (6000.0, 1))
        self.assertEqual((summary.undated.amount, summary.undated.count), (300.0, 1))
        self.assertEqual((summary.expected_payments.amount, summary.expected_payments.count), (6000.0, 1))
        self.assertEqual(summary.expected_schedule[0].expected_date, date(2026, 10, 20))
        self.assertFalse(summary.mixed_currency)

        aging = {bucket.key: (bucket.amount, bucket.count) for bucket in summary.aging}
        self.assertEqual(aging["current"], (3500.50, 2))
        self.assertEqual(aging["31-60"], (6000.0, 1))
        self.assertEqual(aging["1-30"], (0.0, 0))

        self.assertEqual(
            [item.invoice_number for item in summary.priority], ["CUR-OVERDUE", "CUR-SOON"]
        )
        top = summary.priority[0]
        self.assertEqual(top.invoice_id, seeded["overdue"].id)
        self.assertEqual(top.customer_name, "Acme Buyers")
        self.assertEqual(top.outstanding_balance, 6000.0)
        self.assertEqual(
            top.reasons, ["41 days overdue", "High late-payment risk (80%)", "Partially paid"]
        )
        self.assertEqual(summary.priority[1].reasons, ["Due in 3 days"])
        self.assertEqual(summary.priority[1].customer_name, "Beta Traders")

    def test_summary_endpoint_is_tenant_scoped(self):
        self._seed()
        mine = self.client.get(
            "/dashboard/summary", headers={"Authorization": f"Bearer {self.token}"}
        )
        self.assertEqual(mine.status_code, 200)
        self.assertEqual(mine.json()["outstanding"]["count"], 4)

        theirs = self.client.get(
            "/dashboard/summary", headers={"Authorization": f"Bearer {self.other_token}"}
        )
        self.assertEqual(theirs.status_code, 200)
        body = theirs.json()
        self.assertEqual(body["outstanding"], {"amount": 77777.0, "count": 1})
        self.assertNotIn("CUR-OVERDUE", [item["invoice_number"] for item in body["priority"]])

        self.assertEqual(self.client.get("/dashboard/summary").status_code, 401)

    def test_invoice_list_exposes_remaining_balance_for_partial(self):
        seeded = self._seed()
        response = self.client.get(
            "/invoices?origin=CURRENT", headers={"Authorization": f"Bearer {self.token}"}
        )
        self.assertEqual(response.status_code, 200)
        by_number = {item["invoice_number"]: item for item in response.json()["items"]}
        partial = by_number[seeded["overdue"].invoice_number]
        self.assertEqual(partial["payment_status"], "PARTIAL")
        self.assertEqual(partial["total_paid"], 4000.0)
        self.assertEqual(partial["outstanding_balance"], 6000.0)
        self.assertEqual(by_number["CUR-LATER"]["outstanding_balance"], 1000.0)


if __name__ == "__main__":
    unittest.main()
