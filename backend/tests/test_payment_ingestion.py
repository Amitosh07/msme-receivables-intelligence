"""Phase B historical payment ingestion integration tests."""

import datetime
import io
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.payment import Payment
from backend.app.models.user import User
from backend.app.services import payment_import_service
from backend.app.services.payment_import_service import _normalize_index_text


VALID_GSTIN = "27AAPFU0939F1ZV"


class TestPaymentIngestion(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        self.token_a, self.user_a_id, self.business_a_id = self._register("a")
        self.token_b, self.user_b_id, self.business_b_id = self._register("b")

    def _register(self, label: str) -> tuple[str, uuid.UUID, uuid.UUID]:
        suffix = uuid.uuid4().hex[:8]
        response = self.client.post("/auth/register", json={
            "email": f"pay_{label}_{suffix}@corp.com",
            "password": "Password123!",
            "full_name": f"Payment User {label.upper()}",
            "business_name": f"Payment Corp {label.upper()} {suffix}",
        })
        self.assertEqual(response.status_code, 201)
        data = response.json()
        return (
            data["access_token"],
            uuid.UUID(data["user"]["id"]),
            uuid.UUID(data["business"]["id"]),
        )

    def _customer(
        self,
        name: str,
        *,
        business_id: uuid.UUID | None = None,
        customer_ref: str | None = None,
        gstin: str | None = None,
    ) -> Customer:
        customer = Customer(
            business_id=business_id or self.business_a_id,
            name=name,
            customer_ref=customer_ref,
            gstin=gstin,
        )
        self.db.add(customer)
        self.db.flush()
        return customer

    def _invoice(
        self,
        customer: Customer,
        number: str,
        *,
        business_id: uuid.UUID | None = None,
    ) -> Invoice:
        invoice = Invoice(
            business_id=business_id or customer.business_id,
            customer_id=customer.id,
            invoice_number=number,
            invoice_date=datetime.date(2025, 1, 10),
            due_date=datetime.date(2025, 2, 10),
            amount=15000.00,
            currency="INR",
            payment_status="OPEN",
            processing_status="PROCESSED",
        )
        self.db.add(invoice)
        self.db.flush()
        return invoice

    def _post_csv(self, content: str, *, token: str | None = None, endpoint: str = "/payments/import"):
        return self.client.post(
            endpoint,
            files={"file": ("payments.csv", io.BytesIO(content.encode("utf-8")), "text/csv")},
            headers={"Authorization": f"Bearer {token or self.token_a}"},
        )

    @staticmethod
    def _xlsx_bytes(rows: list[list[object]]) -> bytes:
        workbook = Workbook()
        worksheet = workbook.active
        for row in rows:
            worksheet.append(row)
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        return output.getvalue()

    def tearDown(self):
        try:
            for business_id in (self.business_a_id, self.business_b_id):
                business = self.db.get(Business, business_id)
                if business:
                    self.db.delete(business)
            for user_id in (self.user_a_id, self.user_b_id):
                user = self.db.get(User, user_id)
                if user:
                    self.db.delete(user)
            self.db.commit()
        except Exception:
            self.db.rollback()
        finally:
            self.db.close()

    def test_existing_gstin_and_normalized_name_resolve_existing_customers(self):
        gst_customer = self._customer("GST Customer", gstin=VALID_GSTIN)
        name_customer = self._customer("ABC Industries Pvt Ltd")
        self.db.commit()
        content = (
            "customer name,gst no,invoice no,payment date,paid amount\n"
            f"Different Display,{VALID_GSTIN},GST-1,03/04/2025,1000\n"
            "  ABC Industries Pvt. Ltd.  ,,NAME-1,2025-04-04,2000\n"
        )

        response = self._post_csv(content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported"], 2)
        keys = set(self.db.scalars(select(Payment.customer_identity_key).where(
            Payment.business_id == self.business_a_id
        )).all())
        self.assertEqual(keys, {f"customer:{gst_customer.id}", f"customer:{name_customer.id}"})

    def test_new_customer_created_once_and_similar_customer_names_do_not_merge(self):
        content = (
            "customer_name,invoice_number,payment_date,payment_amount\n"
            "Acme Industries,INV-1,01/04/2025,1000\n"
            "Acme Industries,INV-2,02/04/2025,1200\n"
            "Acme Industry,INV-3,03/04/2025,1400\n"
        )

        response = self._post_csv(content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported"], 3)
        customers = self.db.scalars(select(Customer).where(
            Customer.business_id == self.business_a_id
        )).all()
        self.assertEqual(len(customers), 2)
        acme = next(customer for customer in customers if customer.display_name == "Acme Industries")
        acme_payments = self.db.scalars(select(Payment).where(
            Payment.customer_identity_key == f"customer:{acme.id}"
        )).all()
        self.assertEqual(len(acme_payments), 2)

    def test_india_first_dates_and_supported_amount_formats(self):
        content = (
            "customer,invoice,payment date,payment amount\n"
            "Date Amount Customer,A,03/04/2025,1000\n"
            "Date Amount Customer,B,2025-04-04,\"1,000.00\"\n"
            "Date Amount Customer,C,5 April 2025,\"1,00,000.00\"\n"
            "Date Amount Customer,D,06/04/2025,\"₹1,25,000\"\n"
            "Date Amount Customer,E,07/04/2025,\"Rs. 1,25,000\"\n"
        )

        response = self._post_csv(content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported"], 5)
        payments = self.db.scalars(select(Payment).where(
            Payment.business_id == self.business_a_id
        ).order_by(Payment.payment_date)).all()
        self.assertEqual(payments[0].payment_date.date(), datetime.date(2025, 4, 3))
        self.assertEqual([float(payment.amount) for payment in payments], [
            1000.0, 1000.0, 100000.0, 125000.0, 125000.0
        ])

    def test_invalid_dates_and_amounts_are_rejected_individually(self):
        content = (
            "customer_name,invoice_number,payment_date,payment_amount\n"
            "Valid Customer,OK,2025-04-01,1000\n"
            "Valid Customer,BAD-DATE,04/13/2025,500\n"
            "Valid Customer,ZERO,02/04/2025,0\n"
            "Valid Customer,NEG,03/04/2025,-1\n"
            "Valid Customer,TEXT,04/04/2025,not-a-number\n"
            "Valid Customer,NAN,05/04/2025,NaN\n"
            "Valid Customer,INF,06/04/2025,Infinity\n"
        )

        response = self._post_csv(content)

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["imported"], 1)
        self.assertEqual(data["rejected"], 6)
        self.assertTrue(all(row["status"] == "rejected" for row in data["row_results"][1:]))

    def test_invoice_matching_unmatched_history_and_provenance(self):
        customer = self._customer("History Customer")
        invoice = self._invoice(customer, "INV-MATCH-01")
        self.db.commit()
        content = (
            "customer_name,invoice_number,payment_date,payment_amount\n"
            "History Customer,INV-MATCH-01,05/04/2025,15000\n"
            "History Customer,INV-NOT-YET,03/04/2025,8500.50\n"
            "History Customer,,04/04/2025,700\n"
        )

        response = self._post_csv(content)

        data = response.json()
        self.assertEqual(data["imported"], 3)
        self.assertEqual(data["matched"], 1)
        self.assertEqual(data["unmatched"], 2)
        self.assertEqual([row["unmatched"] for row in data["row_results"]], [False, True, True])
        payments = self.db.scalars(select(Payment).where(
            Payment.business_id == self.business_a_id
        )).all()
        self.assertTrue(all(payment.provenance == "import" for payment in payments))
        self.assertEqual(next(p for p in payments if p.invoice_reference == "INV-MATCH-01").invoice_id, invoice.id)
        self.assertIsNone(next(p for p in payments if p.invoice_reference == "INV-NOT-YET").invoice_id)
        self.assertIsNone(next(p for p in payments if p.invoice_reference is None).invoice_id)

        history = self.client.get(
            f"/customers/{customer.id}/payments",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()), 3)
        self.assertEqual(
            [item["payment_date"][:10] for item in history.json()],
            ["2025-04-03", "2025-04-04", "2025-04-05"],
        )

    def test_duplicate_semantics_use_customer_invoice_date_and_amount(self):
        content = (
            "customer_name,invoice_number,payment_date,payment_amount\n"
            "Customer One,INV-A,01/04/2025,2500\n"
            "Customer One,INV-A,01/04/2025,2500\n"
            "Customer One,INV-B,01/04/2025,2500\n"
            "Customer Two,INV-A,01/04/2025,2500\n"
        )

        first = self._post_csv(content).json()
        second = self._post_csv(content).json()

        self.assertEqual(first["imported"], 3)
        self.assertEqual(first["duplicates_in_file"], 1)
        self.assertEqual(first["rejected"], 0)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["duplicates"], 4)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Payment).where(
            Payment.business_id == self.business_a_id
        )), 3)

    def test_same_date_amount_blank_invoice_different_customers_both_import(self):
        content = (
            "customer_name,invoice_number,payment_date,payment_amount\n"
            "Customer A,,01/04/2025,2500\n"
            "Customer B,,01/04/2025,2500\n"
        )
        data = self._post_csv(content).json()
        self.assertEqual(data["imported"], 2)
        self.assertEqual(data["duplicates"], 0)

    def test_invoice_only_row_is_rejected_by_identity_floor(self):
        content = (
            "customer_name,invoice_number,payment_date,payment_amount\n"
            ",INV-ONLY,01/04/2025,2500\n"
        )
        data = self._post_csv(content).json()
        self.assertEqual(data["imported"], 0)
        self.assertEqual(data["duplicates"], 0)
        self.assertEqual(data["rejected"], 1)
        self.assertIn("invoice reference alone is not sufficient", data["row_results"][0]["reason"])

    def test_customer_invoice_conflict_rejected_and_matching_identity_imports(self):
        invoice_customer = self._customer("Invoice Customer")
        self._customer("Row Customer")
        invoice = self._invoice(invoice_customer, "CONFLICT-INV")
        self.db.commit()

        conflict = self._post_csv(
            "customer_name,invoice_number,payment_date,payment_amount\n"
            "Row Customer,CONFLICT-INV,01/04/2025,1000\n"
        ).json()
        self.assertEqual(conflict["rejected"], 1)
        self.assertIn("conflicts with existing invoice's assigned customer", conflict["row_results"][0]["reason"])
        self.assertIn("Row Customer", conflict["row_results"][0]["reason"])
        self.assertIn("Invoice Customer", conflict["row_results"][0]["reason"])
        self.assertIsNone(self.db.scalar(select(Payment).where(Payment.invoice_id == invoice.id)))

        matching = self._post_csv(
            "customer_name,invoice_number,payment_date,payment_amount\n"
            "Invoice Customer,CONFLICT-INV,01/04/2025,1000\n"
        ).json()
        self.assertEqual(matching["imported"], 1)
        self.assertEqual(matching["rejected"], 0)

    def test_cross_tenant_customer_invoice_and_duplicate_isolation(self):
        customer_a = self._customer("Tenant A Customer", gstin=VALID_GSTIN)
        invoice_a = self._invoice(customer_a, "TENANT-INVOICE")
        self.db.commit()
        content = (
            "customer_name,gstin,invoice_number,payment_date,payment_amount\n"
            f"Tenant B Customer,{VALID_GSTIN},TENANT-INVOICE,01/04/2025,1000\n"
        )

        result_b = self._post_csv(content, token=self.token_b).json()

        self.assertEqual(result_b["imported"], 1)
        payment_b = self.db.scalar(select(Payment).where(Payment.business_id == self.business_b_id))
        self.assertIsNotNone(payment_b)
        self.assertIsNone(payment_b.invoice_id)
        self.assertNotEqual(payment_b.customer_identity_key, f"customer:{customer_a.id}")
        self.assertIsNone(self.db.scalar(select(Payment).where(Payment.invoice_id == invoice_a.id)))

        result_a = self._post_csv(content, token=self.token_a).json()
        self.assertEqual(result_a["imported"], 1)
        self.assertEqual(result_a["duplicates"], 0)

        forbidden_history = self.client.get(
            f"/customers/{customer_a.id}/payments",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(forbidden_history.status_code, 404)

    def test_xlsx_uses_same_pipeline_and_persists_typed_values(self):
        content = self._xlsx_bytes([
            ["Company Name", "Invoice No", "Paid Date", "Amount Received", "GSTIN"],
            ["XLSX Customer", "XLSX-INV", datetime.date(2025, 4, 3), 125000.50, None],
        ])
        response = self.client.post(
            "/payments/import",
            files={
                "file": (
                    "payments.xlsx",
                    io.BytesIO(content),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            headers={"Authorization": f"Bearer {self.token_a}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported"], 1)
        payment = self.db.scalar(select(Payment).where(
            Payment.business_id == self.business_a_id
        ))
        self.assertEqual(payment.payment_date.date(), datetime.date(2025, 4, 3))
        self.assertEqual(float(payment.amount), 125000.50)
        self.assertEqual(payment.provenance, "import")

    def test_csv_bom_quotes_whitespace_and_semicolon_delimiter(self):
        content = (
            "\ufeffCompany Name;Invoice No;Received Date;Received Amount\n"
            '" Robust Customer ";" INV;42 ";03/04/2025;"Rs. 1,25,000"\n'
        )
        response = self._post_csv(content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported"], 1)
        payment = self.db.scalar(select(Payment).where(Payment.business_id == self.business_a_id))
        self.assertEqual(payment.invoice_reference, "INV;42")
        self.assertEqual(float(payment.amount), 125000.0)

    def test_database_duplicate_savepoint_allows_later_row_to_import(self):
        customer = self._customer("Race Customer")
        self.db.commit()
        content = (
            "customer_name,invoice_number,payment_date,payment_amount\n"
            "Race Customer,RACE-DUP,01/06/2025,2500\n"
            "Race Customer,RACE-NEW,02/06/2025,2600\n"
        ).encode("utf-8")
        original_resolver = payment_import_service.resolve_customer_identity
        inserted_conflict = False

        def inject_concurrent_payment(*args, **kwargs):
            nonlocal inserted_conflict
            result = original_resolver(*args, **kwargs)
            if not inserted_conflict:
                inserted_conflict = True
                other_db = SessionLocal()
                try:
                    other_db.add(Payment(
                        business_id=self.business_a_id,
                        invoice_reference="RACE-DUP",
                        payment_date=datetime.datetime(2025, 6, 1, tzinfo=datetime.timezone.utc),
                        amount=2500.00,
                        customer_identity_key=f"customer:{customer.id}",
                        provenance="import",
                    ))
                    other_db.commit()
                finally:
                    other_db.close()
            return result

        with patch.object(
            payment_import_service,
            "resolve_customer_identity",
            side_effect=inject_concurrent_payment,
        ):
            result = payment_import_service.import_payments_csv(
                self.db, self.business_a_id, content
            )

        self.assertEqual(result.imported, 1)
        self.assertEqual(result.duplicates_existing, 1)
        self.assertEqual([row.status for row in result.row_results], ["duplicate", "imported"])
        self.assertIsNotNone(self.db.scalar(select(Payment).where(
            Payment.business_id == self.business_a_id,
            Payment.invoice_reference == "RACE-NEW",
        )))

    def test_application_normalization_matches_live_postgresql_expression(self):
        values = (
            None,
            "",
            "   ",
            " leading",
            "trailing ",
            "MiXeD Case",
            "repeat   internal",
            "ＡＢＣ-１２３",
            "INV/2025-(A)",
            "  Invoice-AbC  ",
        )
        for value in values:
            application_value = _normalize_index_text(self.db, value)
            database_value = self.db.scalar(select(
                func.lower(func.btrim(func.coalesce(literal(value), literal(""))))
            ))
            self.assertEqual(application_value, database_value)

    def test_preview_does_not_persist_customers_or_payments(self):
        content = (
            "customer_name,invoice_number,payment_date,payment_amount\n"
            "Preview Customer,PREVIEW-1,01/04/2025,1000\n"
        )
        response = self._post_csv(content, endpoint="/payments/preview")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["preview"])
        self.assertEqual(response.json()["imported"], 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Payment).where(
            Payment.business_id == self.business_a_id
        )), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Customer).where(
            Customer.business_id == self.business_a_id
        )), 0)

    def test_missing_headers_and_unsupported_file_are_rejected(self):
        missing = self._post_csv("invoice_number,notes\nINV-001,Some note\n")
        self.assertEqual(missing.status_code, 400)
        self.assertIn("missing required columns", missing.json()["detail"].lower())

        unsupported = self.client.post(
            "/payments/import",
            files={"file": ("report.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(unsupported.status_code, 400)
        self.assertIn("CSV and XLSX", unsupported.json()["detail"])


if __name__ == "__main__":
    unittest.main()
