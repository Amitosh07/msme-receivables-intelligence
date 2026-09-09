"""
Unit tests for database connection, schema verification, and CRUD operations.
"""

import unittest
import uuid
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal, engine
from backend.app.models.business import Business
from backend.app.models.user import User
from backend.app.models.membership import Membership


class TestDatabaseConnection(unittest.TestCase):
    """Verify local PostgreSQL connectivity and configuration."""

    def test_database_connection(self):
        """Database connection to localhost:5432/msme_receivables succeeds."""
        with engine.connect() as conn:
            row = conn.execute(text("SELECT current_database(), current_user;")).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "msme_receivables")
            self.assertEqual(row[1], "postgres")

    def test_schema_tables_exist(self):
        """All 10 Core application tables must exist in the database."""
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected = {
            "businesses",
            "users",
            "memberships",
            "customers",
            "invoices",
            "payments",
            "invoice_documents",
            "prediction_results",
            "cashflow_forecasts",
            "tasks",
            "alembic_version",
        }
        missing = expected - tables
        self.assertEqual(len(missing), 0, f"Missing tables in database: {missing}")

    def test_basic_crud_operations(self):
        """Test basic model persistence and query with cleanup."""
        db: Session = SessionLocal()
        test_email = f"test_crud_{uuid.uuid4().hex[:8]}@example.com"
        try:
            # Create business
            business = Business(name="Test CRUD Corp", currency="USD")
            db.add(business)
            db.flush()
            self.assertIsNotNone(business.id)

            # Create user
            user = User(
                email=test_email,
                full_name="CRUD Tester",
                password_hash="fake_hash_123",
                is_active=True,
            )
            db.add(user)
            db.flush()
            self.assertIsNotNone(user.id)

            # Create membership
            membership = Membership(
                user_id=user.id,
                business_id=business.id,
                role="owner",
            )
            db.add(membership)
            db.commit()

            # Read back
            found_user = db.query(User).filter(User.email == test_email).first()
            self.assertIsNotNone(found_user)
            self.assertEqual(found_user.full_name, "CRUD Tester")

            # Verify relationship
            self.assertEqual(len(found_user.memberships), 1)
            self.assertEqual(found_user.memberships[0].business_id, business.id)

            # Cleanup
            db.delete(membership)
            db.delete(user)
            db.delete(business)
            db.commit()

        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
