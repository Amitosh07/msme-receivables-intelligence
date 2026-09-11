"""Phase B.1 payment provenance migration and persistence tests."""

import importlib.util
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from backend.app.db.session import SessionLocal, engine
from backend.app.models.business import Business
from backend.app.models.payment import Payment


def _load_migration_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0008_backfill_payment_provenance.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0008", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestPaymentProvenance(unittest.TestCase):
    def test_database_requires_explicit_payment_provenance(self):
        db = SessionLocal()
        try:
            business = Business(name=f"Provenance required {uuid.uuid4()}", currency="INR")
            db.add(business)
            db.flush()
            db.add(Payment(
                business_id=business.id,
                invoice_reference="PROVENANCE-REQUIRED",
                payment_date=datetime(2026, 9, 11, tzinfo=timezone.utc),
                amount=100,
                customer_identity_key=f"customer:{uuid.uuid4()}",
            ))
            with self.assertRaises(IntegrityError):
                db.flush()
        finally:
            db.rollback()
            db.close()

    def test_query_distinguishes_import_and_legacy(self):
        db = SessionLocal()
        business = Business(name=f"Provenance query {uuid.uuid4()}", currency="INR")
        try:
            db.add(business)
            db.flush()
            db.add_all([
                Payment(
                    business_id=business.id,
                    invoice_reference="LEGACY-PAYMENT",
                    payment_date=datetime(2026, 9, 10, tzinfo=timezone.utc),
                    amount=100,
                    customer_identity_key=f"customer:{uuid.uuid4()}",
                    provenance="legacy",
                ),
                Payment(
                    business_id=business.id,
                    invoice_reference="IMPORT-PAYMENT",
                    payment_date=datetime(2026, 9, 11, tzinfo=timezone.utc),
                    amount=200,
                    customer_identity_key=f"customer:{uuid.uuid4()}",
                    provenance="import",
                ),
            ])
            db.flush()
            legacy = list(db.scalars(select(Payment).where(
                Payment.business_id == business.id,
                Payment.provenance == "legacy",
            )))
            imported = list(db.scalars(select(Payment).where(
                Payment.business_id == business.id,
                Payment.provenance == "import",
            )))
            self.assertEqual([row.invoice_reference for row in legacy], ["LEGACY-PAYMENT"])
            self.assertEqual([row.invoice_reference for row in imported], ["IMPORT-PAYMENT"])
        finally:
            db.rollback()
            db.close()

    def test_migration_backfills_only_null_and_has_conservative_downgrade(self):
        migration = _load_migration_module()
        schema = f"provenance_test_{uuid.uuid4().hex}"
        null_id = uuid.uuid4()
        imported_id = uuid.uuid4()
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                connection.exec_driver_sql(
                    "CREATE TABLE payments (id uuid PRIMARY KEY, provenance varchar(32) NULL)"
                )
                connection.execute(
                    text("INSERT INTO payments (id, provenance) VALUES (:null_id, NULL), (:imported_id, 'import')"),
                    {"null_id": null_id, "imported_id": imported_id},
                )
                operations = Operations(MigrationContext.configure(connection))
                with patch.object(migration, "op", operations):
                    migration.upgrade()

                    values = dict(connection.execute(
                        text("SELECT id, provenance FROM payments ORDER BY id")
                    ).all())
                    self.assertEqual(values[null_id], "legacy")
                    self.assertEqual(values[imported_id], "import")
                    self.assertEqual(connection.scalar(text(
                        "SELECT count(*) FROM payments WHERE provenance IS NULL"
                    )), 0)
                    self.assertEqual(connection.scalar(text(
                        "SELECT count(*) FROM _migration_0008_payment_provenance_legacy"
                    )), 1)
                    nullable = connection.scalar(text("""
                        SELECT is_nullable FROM information_schema.columns
                        WHERE table_schema = :schema
                          AND table_name = 'payments'
                          AND column_name = 'provenance'
                    """), {"schema": schema})
                    self.assertEqual(nullable, "NO")

                    migration.downgrade()

                values = dict(connection.execute(
                    text("SELECT id, provenance FROM payments ORDER BY id")
                ).all())
                self.assertIsNone(values[null_id])
                self.assertEqual(values[imported_id], "import")
            finally:
                transaction.rollback()


if __name__ == "__main__":
    unittest.main()
