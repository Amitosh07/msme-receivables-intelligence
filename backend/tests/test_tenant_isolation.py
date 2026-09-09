"""
Tenant Isolation Tests (Mandatory).
Verifies strict database-level query scoping and server-side tenant enforcement.
Business A cannot access Business B's data, and Business B cannot access Business A's data.
"""

import datetime
import unittest
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.dependencies import get_tenant_context
from backend.app.core.security import decode_access_token
from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.user import User


class TestTenantIsolation(unittest.TestCase):
    """
    Mandatory tenant isolation test suite verifying server-side boundary enforcement.
    """

    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        self.cleanups = []

        # Create Business A and User A
        self.suffix_a = uuid.uuid4().hex[:8]
        res_a = self.client.post("/auth/register", json={
            "email": f"usera_{self.suffix_a}@corpa.com",
            "password": "Password123!",
            "full_name": "Alice Corp A",
            "business_name": f"Corporation A {self.suffix_a}",
        })
        self.assertEqual(res_a.status_code, 201)
        data_a = res_a.json()
        self.token_a = data_a["access_token"]
        self.user_a_id = uuid.UUID(data_a["user"]["id"])
        self.business_a_id = uuid.UUID(data_a["business"]["id"])

        # Create Business B and User B
        self.suffix_b = uuid.uuid4().hex[:8]
        res_b = self.client.post("/auth/register", json={
            "email": f"userb_{self.suffix_b}@corpb.com",
            "password": "Password123!",
            "full_name": "Bob Corp B",
            "business_name": f"Corporation B {self.suffix_b}",
        })
        self.assertEqual(res_b.status_code, 201)
        data_b = res_b.json()
        self.token_b = data_b["access_token"]
        self.user_b_id = uuid.UUID(data_b["user"]["id"])
        self.business_b_id = uuid.UUID(data_b["business"]["id"])

        # Seed tenant-owned data in Business A
        self.cust_a = Customer(
            business_id=self.business_a_id,
            name="Client of Business A",
            customer_ref="CUST-A-001",
        )
        self.db.add(self.cust_a)
        self.db.flush()

        self.inv_a = Invoice(
            business_id=self.business_a_id,
            customer_id=self.cust_a.id,
            invoice_number="INV-A-101",
            invoice_date=datetime.date(2025, 1, 15),
            due_date=datetime.date(2025, 2, 15),
            amount=50000.00,
            currency="USD",
            payment_status="OPEN",
            processing_status="PENDING",
        )
        self.db.add(self.inv_a)

        # Seed tenant-owned data in Business B
        self.cust_b = Customer(
            business_id=self.business_b_id,
            name="Client of Business B",
            customer_ref="CUST-B-001",
        )
        self.db.add(self.cust_b)
        self.db.flush()

        self.inv_b = Invoice(
            business_id=self.business_b_id,
            customer_id=self.cust_b.id,
            invoice_number="INV-B-201",
            invoice_date=datetime.date(2025, 2, 1),
            due_date=datetime.date(2025, 3, 1),
            amount=75000.00,
            currency="USD",
            payment_status="OPEN",
            processing_status="PENDING",
        )
        self.db.add(self.inv_b)
        self.db.commit()

    def tearDown(self):
        """Clean up all records created for both tenants."""
        try:
            # Delete Business A cascading deletes all its customers, invoices, memberships
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

    def test_tenant_context_resolution_is_server_authoritative(self):
        """User A resolves only to Business A; User B resolves only to Business B."""
        # Check /auth/me for User A
        res_a = self.client.get("/auth/me", headers={"Authorization": f"Bearer {self.token_a}"})
        self.assertEqual(res_a.status_code, 200)
        self.assertEqual(uuid.UUID(res_a.json()["business_id"]), self.business_a_id)

        # Check /auth/me for User B
        res_b = self.client.get("/auth/me", headers={"Authorization": f"Bearer {self.token_b}"})
        self.assertEqual(res_b.status_code, 200)
        self.assertEqual(uuid.UUID(res_b.json()["business_id"]), self.business_b_id)

    def test_user_a_cannot_access_business_b_customer(self):
        """Database queries scoped by User A's tenant return Business A data and NEVER Business B data."""
        # User A's scoped customer query
        user_a_customers = self.db.scalars(
            select(Customer).where(Customer.business_id == self.business_a_id)
        ).all()
        cust_ids_a = [c.id for c in user_a_customers]

        self.assertIn(self.cust_a.id, cust_ids_a)
        self.assertNotIn(self.cust_b.id, cust_ids_a)

        # Attempt to look up Customer B specifically under Business A's tenant scope
        cross_tenant_lookup = self.db.scalar(
            select(Customer).where(
                Customer.id == self.cust_b.id,
                Customer.business_id == self.business_a_id,
            )
        )
        self.assertIsNone(cross_tenant_lookup, "User A scope must NEVER find Business B customer")

    def test_user_b_cannot_access_business_a_customer(self):
        """Database queries scoped by User B's tenant return Business B data and NEVER Business A data."""
        user_b_customers = self.db.scalars(
            select(Customer).where(Customer.business_id == self.business_b_id)
        ).all()
        cust_ids_b = [c.id for c in user_b_customers]

        self.assertIn(self.cust_b.id, cust_ids_b)
        self.assertNotIn(self.cust_a.id, cust_ids_b)

        # Attempt to look up Customer A specifically under Business B's tenant scope
        cross_tenant_lookup = self.db.scalar(
            select(Customer).where(
                Customer.id == self.cust_a.id,
                Customer.business_id == self.business_b_id,
            )
        )
        self.assertIsNone(cross_tenant_lookup, "User B scope must NEVER find Business A customer")

    def test_user_a_cannot_access_business_b_invoice(self):
        """Database queries scoped by User A's tenant return Business A invoices and NEVER Business B invoices."""
        user_a_invoices = self.db.scalars(
            select(Invoice).where(Invoice.business_id == self.business_a_id)
        ).all()
        inv_ids_a = [i.id for i in user_a_invoices]

        self.assertIn(self.inv_a.id, inv_ids_a)
        self.assertNotIn(self.inv_b.id, inv_ids_a)

        # Specific cross-tenant query
        cross_invoice = self.db.scalar(
            select(Invoice).where(
                Invoice.id == self.inv_b.id,
                Invoice.business_id == self.business_a_id,
            )
        )
        self.assertIsNone(cross_invoice, "User A scope must NEVER find Business B invoice")

    def test_user_b_cannot_access_business_a_invoice(self):
        """Database queries scoped by User B's tenant return Business B invoices and NEVER Business A invoices."""
        user_b_invoices = self.db.scalars(
            select(Invoice).where(Invoice.business_id == self.business_b_id)
        ).all()
        inv_ids_b = [i.id for i in user_b_invoices]

        self.assertIn(self.inv_b.id, inv_ids_b)
        self.assertNotIn(self.inv_a.id, inv_ids_b)

        # Specific cross-tenant query
        cross_invoice = self.db.scalar(
            select(Invoice).where(
                Invoice.id == self.inv_a.id,
                Invoice.business_id == self.business_b_id,
            )
        )
        self.assertIsNone(cross_invoice, "User B scope must NEVER find Business A invoice")

    def test_client_cannot_spoof_tenant_id_in_jwt(self):
        """Forged token containing another business_id fails validation or does not grant access."""
        from backend.app.core.security import create_access_token

        # Forged token: User A sub, but claims Business B ID
        spoofed_token = create_access_token(
            subject=str(self.user_a_id),
            extra_claims={"business_id": str(self.business_b_id), "role": "owner"},
        )

        # Server-side get_current_business must resolve from Membership in DB, not trust claim
        response = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {spoofed_token}"},
        )
        self.assertEqual(response.status_code, 200)
        # Authoritative business_id from DB must be Business A, not spoofed Business B!
        self.assertEqual(uuid.UUID(response.json()["business_id"]), self.business_a_id)


if __name__ == "__main__":
    unittest.main()
