"""Database verification for the widened Payment natural key migration."""

from pathlib import Path
import unittest
import uuid

from sqlalchemy import text

from backend.app.db.session import engine


class TestPaymentNaturalKeyMigration(unittest.TestCase):
    LEGACY_UNMATCHED_PAYMENT_IDS = (
        "3cb0044e-0bc7-43e1-9065-3674943cb0f6",
        "580aa43b-6511-4bbb-8dc6-5cf4e235deef",
        "7e114580-a84b-4606-a9cc-cc903bdb8163",
        "8a83f1d2-da7e-4733-ba32-fee6f668c8fa",
        "8c1e6e85-d36d-4267-aa53-8595444a9f09",
        "e7838d6c-0a69-4b0a-a171-004c5eb2cf4b",
        "ff715152-28b2-4cf4-b41e-85279b45f678",
    )

    def test_final_schema_has_only_valid_widened_canonical_index(self):
        with engine.connect() as connection:
            row = connection.execute(text(
                """
                SELECT pg_get_indexdef(i.indexrelid) AS definition,
                       i.indisunique, i.indisvalid, i.indisready
                FROM pg_index AS i
                JOIN pg_class AS c ON c.oid = i.indexrelid
                WHERE c.relname = 'uq_payment_natural_key'
                """
            )).mappings().one()
            staged_count = connection.execute(text(
                """
                SELECT count(*)
                FROM pg_class
                WHERE relname IN (
                    'uq_payment_natural_key_v2',
                    'uq_payment_natural_key_v1_restore'
                )
                """
            )).scalar_one()

        self.assertTrue(row["indisunique"])
        self.assertTrue(row["indisvalid"])
        self.assertTrue(row["indisready"])
        self.assertIn("customer_identity_key", row["definition"])
        self.assertIn("lower(btrim", row["definition"])
        self.assertEqual(staged_count, 0)

    def test_provenance_column_exists_without_changing_natural_key(self):
        with engine.connect() as connection:
            provenance = connection.execute(text(
                """
                SELECT is_nullable, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'payments'
                  AND column_name = 'provenance'
                """
            )).mappings().one()
            definition = connection.execute(text(
                """
                SELECT pg_get_indexdef(i.indexrelid)
                FROM pg_index AS i
                JOIN pg_class AS c ON c.oid = i.indexrelid
                WHERE c.relname = 'uq_payment_natural_key'
                """
            )).scalar_one()

        self.assertEqual(provenance["is_nullable"], "NO")
        self.assertEqual(provenance["data_type"], "character varying")
        self.assertNotIn("provenance", definition)

    def test_null_customer_identity_key_retains_postgresql_null_distinct_semantics(self):
        business_id = uuid.uuid4()
        payment_one = uuid.uuid4()
        payment_two = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(text(
                """
                INSERT INTO businesses (id, name, currency)
                VALUES (:id, :name, 'INR')
                """
            ), {"id": business_id, "name": f"NULL key test {business_id}"})
            values = {
                "business_id": business_id,
                "payment_date": "2025-04-03T00:00:00+00:00",
                "amount": "1250.00",
            }
            connection.execute(text(
                """
                INSERT INTO payments
                    (id, business_id, customer_identity_key, invoice_reference,
                     payment_date, amount, provenance)
                VALUES
                    (:id_one, :business_id, NULL, '', :payment_date, :amount, 'legacy'),
                    (:id_two, :business_id, NULL, '', :payment_date, :amount, 'legacy')
                """
            ), {**values, "id_one": payment_one, "id_two": payment_two})
            count = connection.execute(text(
                """
                SELECT count(*) FROM payments
                WHERE id IN (:id_one, :id_two)
                """
            ), {"id_one": payment_one, "id_two": payment_two}).scalar_one()
            self.assertEqual(count, 2)
            connection.execute(
                text("DELETE FROM businesses WHERE id = :id"),
                {"id": business_id},
            )

    def test_known_legacy_unmatched_payments_remain_untouched_when_present(self):
        with engine.connect() as connection:
            rows = connection.execute(text(
                """
                SELECT id, invoice_id, customer_identity_key, provenance
                FROM payments
                WHERE id = ANY(CAST(:ids AS uuid[]))
                """
            ), {"ids": list(self.LEGACY_UNMATCHED_PAYMENT_IDS)}).mappings().all()

        # These production-development rows may not exist in a fresh test database.
        # When this suite runs against the migrated Phase A database, require all
        # seven and verify that Phase B migration did not rewrite any of them.
        if rows:
            self.assertEqual(len(rows), 7)
            self.assertTrue(all(row["invoice_id"] is None for row in rows))
            self.assertTrue(all(row["customer_identity_key"] is None for row in rows))
            self.assertTrue(all(row["provenance"] == "legacy" for row in rows))

    def test_resolvable_legacy_payments_have_customer_identity_backfill(self):
        with engine.connect() as connection:
            incorrect = connection.execute(text(
                """
                SELECT count(*)
                FROM payments AS payment
                JOIN invoices AS invoice ON invoice.id = payment.invoice_id
                WHERE invoice.customer_id IS NOT NULL
                  AND payment.customer_identity_key IS DISTINCT FROM
                      'customer:' || invoice.customer_id::text
                """
            )).scalar_one()
        self.assertEqual(incorrect, 0)

    def test_migration_validates_replacement_before_dropping_old_index(self):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "versions"
            / "0006_widen_payment_natural_key.py"
        )
        source = migration_path.read_text(encoding="utf-8")
        create_position = source.index("CREATE UNIQUE INDEX {_STAGED_INDEX}")
        validate_position = source.index("_validate_unique_index(_STAGED_INDEX)")
        drop_position = source.index('op.drop_index(_CANONICAL_INDEX, table_name="payments")')
        self.assertLess(create_position, validate_position)
        self.assertLess(validate_position, drop_position)


if __name__ == "__main__":
    unittest.main()
