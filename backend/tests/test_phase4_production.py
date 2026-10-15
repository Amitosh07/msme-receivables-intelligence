"""Phase 4: secret enforcement, CORS, docs, rate limits, encrypted storage, server-side invoice list."""

import base64
import math
import secrets
import tempfile
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.core.config import (
    InsecureConfigurationError,
    Settings,
    configuration_problems,
    decode_encryption_key,
    enforce_secure_configuration,
    redact_url,
    settings,
)
from backend.app.core.rate_limit import RateLimiter, limiter
from backend.app.main import create_app
from backend.app.models.invoice import InvoiceOrigin
from backend.app.services.dashboard_service import build_dashboard_summary
from backend.app.services.invoice_service import InvoiceQueryError, list_invoices_for_tenant
from backend.app.services.manual_payment_service import business_today
from backend.app.services.prediction_service import (
    eligible_history_counts,
    evaluate_prediction_eligibility,
)
from backend.app.services.storage import EncryptedStorage, StorageError, build_storage
from backend.app.services.storage.encrypt_existing import migrate
from backend.app.services.storage.encrypted import MAGIC
from backend.app.services.storage.local import LocalFileStorage
from backend.tests.test_phase3_differentiation import Phase3TestCase

TODAY = date(2026, 10, 12)


def new_key() -> bytes:
    return secrets.token_bytes(32)


def b64(key: bytes) -> str:
    return base64.b64encode(key).decode()


def strong_settings(**overrides) -> Settings:
    values = dict(
        ENVIRONMENT="production",
        JWT_SECRET_KEY=secrets.token_urlsafe(48),
        DATABASE_URL="postgresql+psycopg://msme:S7rong-db-pass@postgres:5432/msme",
        REDIS_URL="redis://:S7rong-redis-pass@redis:6379/0",
        STORAGE_ENCRYPTION_KEY=b64(new_key()),
        CORS_ORIGINS="https://app.example.com",
        RATE_LIMIT_ENABLED=True,
        API_DOCS_ENABLED=None,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


class TestSecretEnforcement(unittest.TestCase):
    def test_strong_production_configuration_starts(self):
        config = strong_settings()
        self.assertEqual(configuration_problems(config), [])
        enforce_secure_configuration(config)

    def test_production_refuses_placeholders_without_echoing_them(self):
        config = strong_settings(
            JWT_SECRET_KEY="replace-with-a-long-random-secret",
            DATABASE_URL="postgresql+psycopg://postgres:postgres@db/x",
            REDIS_URL="redis://redis:6379/0",
            STORAGE_ENCRYPTION_KEY="",
        )
        with self.assertRaises(InsecureConfigurationError) as ctx:
            enforce_secure_configuration(config)
        message = str(ctx.exception)
        for setting in ("JWT_SECRET_KEY", "DATABASE_URL", "REDIS_URL", "STORAGE_ENCRYPTION_KEY"):
            self.assertIn(setting, message)
        self.assertNotIn("replace-with-a-long-random-secret", message)
        self.assertNotIn("postgres:postgres", message)

    def test_short_secrets_and_unsafe_choices_are_flagged(self):
        problems = " ".join(configuration_problems(strong_settings(
            JWT_SECRET_KEY="short", JWT_ALGORITHM="none", CORS_ORIGINS="*", RATE_LIMIT_ENABLED=False,
        )))
        for fragment in ("JWT_SECRET_KEY", "JWT_ALGORITHM", "wildcards", "RATE_LIMIT_ENABLED"):
            self.assertIn(fragment, problems)
        self.assertIn("CORS_ORIGINS", " ".join(configuration_problems(strong_settings(CORS_ORIGINS="app.example.com"))))

    def test_development_only_warns(self):
        config = strong_settings(ENVIRONMENT="development", JWT_SECRET_KEY="replace-with-a-long-random-secret")
        with self.assertLogs("backend.app.core.config", level="WARNING") as logs:
            enforce_secure_configuration(config)
        self.assertIn("JWT_SECRET_KEY", "\n".join(logs.output))

    def test_malformed_encryption_keys_fail_in_every_environment(self):
        for environment in ("development", "production"):
            with self.assertRaises(InsecureConfigurationError):
                enforce_secure_configuration(strong_settings(ENVIRONMENT=environment, STORAGE_ENCRYPTION_KEY="not-a-key"))
            with self.assertRaises(InsecureConfigurationError):
                enforce_secure_configuration(strong_settings(
                    ENVIRONMENT=environment, STORAGE_ENCRYPTION_PREVIOUS_KEYS=b64(secrets.token_bytes(16)),
                ))

    def test_app_factory_refuses_insecure_production(self):
        with self.assertRaises(InsecureConfigurationError):
            create_app(strong_settings(JWT_SECRET_KEY="changeme"))

    def test_encryption_keys_accept_standard_and_url_safe_base64(self):
        key = bytes(range(250, 256)) * 5 + b"\xfb\xff"
        self.assertEqual(decode_encryption_key(base64.b64encode(key).decode()), key)
        self.assertEqual(decode_encryption_key(base64.urlsafe_b64encode(key).decode().rstrip("=")), key)

    def test_encryption_key_validation_rejects_invalid_length_and_malformed_values(self):
        with self.assertRaisesRegex(
            InsecureConfigurationError, r"must decode to 32 bytes"
        ):
            decode_encryption_key(base64.b64encode(b"too-short").decode())
        with self.assertRaisesRegex(
            InsecureConfigurationError, r"not valid base64"
        ):
            decode_encryption_key("not-base64!")

    def test_secure_configuration_rejects_invalid_length_key(self):
        with self.assertRaisesRegex(
            InsecureConfigurationError, r"must decode to 32 bytes"
        ):
            enforce_secure_configuration(
                strong_settings(STORAGE_ENCRYPTION_KEY=base64.b64encode(b"short").decode())
            )

    def test_urls_are_redacted_for_logs(self):
        self.assertEqual(redact_url("redis://:p%40ss@redis:6379/0"), "redis://:***@redis:6379/0")
        self.assertEqual(redact_url("postgresql://u:secret@db/x"), "postgresql://u:***@db/x")
        self.assertEqual(redact_url("redis://localhost:6379/0"), "redis://localhost:6379/0")


class TestHttpSurface(unittest.TestCase):
    def test_docs_are_hidden_outside_development(self):
        client = TestClient(create_app(strong_settings()))
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(client.get(path).status_code, 404, path)

    def test_docs_are_available_in_development_or_when_enabled(self):
        dev = TestClient(create_app(strong_settings(ENVIRONMENT="development")))
        self.assertEqual(dev.get("/openapi.json").status_code, 200)
        enabled = TestClient(create_app(strong_settings(API_DOCS_ENABLED=True)))
        self.assertEqual(enabled.get("/docs").status_code, 200)

    def test_cors_allows_only_listed_origins(self):
        client = TestClient(create_app(strong_settings()))
        preflight = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
        allowed = client.options("/auth/me", headers={"Origin": "https://app.example.com", **preflight})
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), "https://app.example.com")
        self.assertNotIn("access-control-allow-credentials", allowed.headers)

        denied = client.options("/auth/me", headers={"Origin": "https://evil.example", **preflight})
        self.assertNotIn("access-control-allow-origin", denied.headers)
        simple = client.get("/health", headers={"Origin": "https://evil.example"})
        self.assertNotIn("access-control-allow-origin", simple.headers)


class RateLimitTestCase(Phase3TestCase):
    """Rate limits on, counters kept in memory so tests never touch a shared Redis."""

    def setUp(self):
        super().setUp()
        limiter.reset_memory()
        self.patches = [
            mock.patch.object(settings, "RATE_LIMIT_ENABLED", True),
            mock.patch.object(limiter, "_redis_down_until", math.inf),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        limiter.reset_memory()
        super().tearDown()

    def _limits(self, **values):
        patch = mock.patch.multiple(settings, **values)
        patch.start()
        self.addCleanup(patch.stop)

    def _account(self) -> tuple[str, str]:
        email = f"p4_{uuid.uuid4().hex[:8]}@corp.com"
        response = self.client.post("/auth/register", json={
            "email": email, "password": "Password123!", "full_name": "Phase Four", "business_name": f"P4 {email}",
        })
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.tenants.append((uuid.UUID(data["business"]["id"]), uuid.UUID(data["user"]["id"])))
        return email, data["access_token"]


class TestRateLimits(RateLimitTestCase):
    def test_login_attempts_per_ip_are_capped(self):
        email, _ = self._account()
        self._limits(RATE_LIMIT_LOGIN_PER_MINUTE=3)
        codes = [
            self.client.post("/auth/login", json={"email": email, "password": "Password123!"}).status_code
            for _ in range(4)
        ]
        self.assertEqual(codes, [200, 200, 200, 429])
        blocked = self.client.post("/auth/login", json={"email": email, "password": "Password123!"})
        self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)
        self.assertIn("Too many requests", blocked.json()["detail"])

    def test_failed_logins_lock_only_that_account(self):
        email, _ = self._account()
        other, _ = self._account()
        self._limits(RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_HOUR=2)
        wrong = {"email": email, "password": "WrongPass999!"}
        self.assertEqual(self.client.post("/auth/login", json=wrong).status_code, 401)
        self.assertEqual(self.client.post("/auth/login", json=wrong).status_code, 401)
        locked = self.client.post("/auth/login", json={"email": email.upper(), "password": "Password123!"})
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(
            self.client.post("/auth/login", json={"email": other, "password": "Password123!"}).status_code, 200
        )

    def test_successful_logins_do_not_count_toward_the_account_lock(self):
        email, _ = self._account()
        self._limits(RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_HOUR=1)
        for _ in range(3):
            self.assertEqual(
                self.client.post("/auth/login", json={"email": email, "password": "Password123!"}).status_code, 200
            )

    def test_registration_per_ip_is_capped(self):
        self._limits(RATE_LIMIT_REGISTER_PER_HOUR=1)
        self._account()
        response = self.client.post("/auth/register", json={
            "email": f"p4_{uuid.uuid4().hex[:8]}@corp.com", "password": "Password123!",
            "full_name": "Blocked", "business_name": "Blocked Co",
        })
        self.assertEqual(response.status_code, 429)

    def test_global_ceiling_exempts_health_checks(self):
        self._limits(RATE_LIMIT_PER_MINUTE=3)
        codes = [self.client.get("/auth/me", headers=self._auth()).status_code for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_upload_limit_is_per_tenant(self):
        self._limits(RATE_LIMIT_UPLOADS_PER_MINUTE=1)
        upload = lambda token: self.client.post(
            "/payments/preview", headers=self._auth(token),
            files={"file": ("payments.csv", b"invoice_number,payment_date,amount\n", "text/csv")},
        )
        self.assertNotEqual(upload(self.token).status_code, 429)
        self.assertEqual(upload(self.token).status_code, 429)
        self.assertNotEqual(upload(self.other_token).status_code, 429)


class TestRateLimiterUnit(unittest.TestCase):
    def setUp(self):
        self.patch = mock.patch.object(settings, "RATE_LIMIT_ENABLED", True)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_falls_back_to_memory_when_redis_is_unreachable(self):
        offline = RateLimiter("redis://127.0.0.1:1/0")
        results = [offline.hit("unit", "203.0.113.9", 2, 60) for _ in range(3)]
        self.assertEqual([r.allowed for r in results], [True, True, False])
        self.assertEqual(offline.hit("unit", "203.0.113.10", 2, 60).allowed, True)

    def test_reading_does_not_consume(self):
        local = RateLimiter("redis://127.0.0.1:1/0")
        for _ in range(3):
            self.assertTrue(local.hit("peek", "a", 1, 60, cost=0).allowed)
        local.hit("peek", "a", 1, 60)
        self.assertFalse(local.hit("peek", "a", 1, 60, cost=0).allowed)

    def test_disabled_limiter_allows_everything(self):
        with mock.patch.object(settings, "RATE_LIMIT_ENABLED", False):
            local = RateLimiter("redis://127.0.0.1:1/0")
            self.assertTrue(all(local.hit("off", "a", 1, 60).allowed for _ in range(5)))


class TestEncryptedStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.local = LocalFileStorage(self.tmp.name)
        self.key = new_key()
        self.storage = EncryptedStorage(self.local, self.key)
        self.content = b"%PDF-1.4 GSTIN 27ABCDE1234F1Z5 total 51800.00"

    def tearDown(self):
        self.tmp.cleanup()

    def _raw(self, key: str) -> bytes:
        return (Path(self.tmp.name) / key).read_bytes()

    def test_round_trip_and_nothing_readable_on_disk(self):
        key = "tenants/a/invoices/doc.pdf"
        self.storage.save(self.content, key)
        raw = self._raw(key)
        self.assertTrue(raw.startswith(MAGIC))
        self.assertNotIn(b"GSTIN", raw)
        self.assertEqual(self.storage.read(key), self.content)
        self.assertEqual(self.storage.get_size(key), len(self.content))
        with self.storage.open(key) as stream:
            self.assertEqual(stream.read(), self.content)
        self.assertNotEqual(raw, self._raw_after_resave(key))

    def _raw_after_resave(self, key: str) -> bytes:
        self.storage.save(self.content, key)
        return self._raw(key)

    def test_ciphertext_moved_to_another_path_is_rejected(self):
        self.storage.save(self.content, "tenants/a/invoices/doc.pdf")
        self.local.save(self._raw("tenants/a/invoices/doc.pdf"), "tenants/b/invoices/doc.pdf")
        with self.assertRaises(StorageError):
            self.storage.read("tenants/b/invoices/doc.pdf")

    def test_tampering_is_detected(self):
        self.storage.save(self.content, "doc.pdf")
        raw = bytearray(self._raw("doc.pdf"))
        raw[-1] ^= 0x01
        self.local.save(bytes(raw), "doc.pdf")
        with self.assertRaises(StorageError):
            self.storage.read("doc.pdf")

    def test_rotation_keeps_old_objects_readable(self):
        self.storage.save(self.content, "old.pdf")
        rotated = EncryptedStorage(self.local, new_key(), previous_keys=[self.key])
        self.assertEqual(rotated.read("old.pdf"), self.content)
        self.assertTrue(rotated.needs_rewrite(self._raw("old.pdf")))
        with self.assertRaises(StorageError):
            EncryptedStorage(self.local, new_key()).read("old.pdf")

    def test_legacy_plaintext_reads_can_be_disabled(self):
        self.local.save(b"legacy plaintext", "legacy.pdf")
        self.assertEqual(self.storage.read("legacy.pdf"), b"legacy plaintext")
        strict = EncryptedStorage(self.local, self.key, allow_plaintext_reads=False)
        with self.assertRaises(StorageError):
            strict.read("legacy.pdf")

    def test_factory_encrypts_when_a_key_is_configured(self):
        config = strong_settings(STORAGE_ROOT=self.tmp.name, STORAGE_ENCRYPTION_KEY=b64(self.key))
        self.assertIsInstance(build_storage(config), EncryptedStorage)
        plain = strong_settings(STORAGE_ROOT=self.tmp.name, STORAGE_ENCRYPTION_KEY="")
        self.assertIsInstance(build_storage(plain), LocalFileStorage)

    def test_migration_encrypts_plaintext_and_reseals_retired_keys(self):
        old_key = new_key()
        self.local.save(b"plain one", "tenants/a/one.pdf")
        EncryptedStorage(self.local, old_key).save(b"old two", "tenants/a/two.pdf")
        self.storage.save(b"current three", "tenants/a/three.pdf")
        storage = EncryptedStorage(self.local, self.key, previous_keys=[old_key])

        dry = migrate(storage, apply=False)
        self.assertEqual((dry.scanned, dry.rewritten, dry.already_current, dry.failed), (3, 2, 1, 0))
        self.assertFalse(self._raw("tenants/a/one.pdf").startswith(MAGIC))

        applied = migrate(storage, apply=True)
        self.assertEqual((applied.rewritten, applied.failed), (2, 0))
        strict = EncryptedStorage(self.local, self.key, allow_plaintext_reads=False)
        self.assertEqual(strict.read("tenants/a/one.pdf"), b"plain one")
        self.assertEqual(strict.read("tenants/a/two.pdf"), b"old two")
        self.assertEqual(migrate(storage, apply=True).rewritten, 0)


class InvoiceListTestCase(Phase3TestCase):
    """Tenant A: an eligible customer (3 paid), an ineligible one, and assorted states."""

    def setUp(self):
        super().setUp()
        acme = self._customer("Acme Buyers")
        beta = self._customer("Beta Mills")
        self._history(acme)
        d = lambda days: TODAY + timedelta(days=days)
        self.inv = {
            "high": self._invoice("A-HIGH", "5000", d(-10), customer_id=acme.id),
            "low": self._invoice("A-LOW", "2000", d(3), customer_id=acme.id),
            "gated": self._invoice("B-GATED", "7000", d(-45), customer_id=beta.id),
            "paid": self._invoice("PAID-1", "900", d(-100), customer_id=acme.id, payment_status="PAID"),
            "partial": self._invoice("PART-1", "3000", d(-95), unresolved_customer_name="Gamma Traders",
                                     payment_status="PARTIAL"),
            "undated": self._invoice("NODUE-1", "100", None, unresolved_customer_name="Delta Co"),
            "legacy": self._invoice("5053233d-4447-41b5-bf85-55a800d710cd", "400", d(40),
                                    unresolved_customer_name="Echo Ltd"),
        }
        self._pay(self.inv["paid"], "900", d(-99))
        self._pay(self.inv["partial"], "1000", d(-90))
        self._predict(self.inv["high"], d(5), tier="HIGH", score="0.91")
        self._predict(self.inv["low"], d(4), tier="LOW", score="0.12")
        self._predict(self.inv["gated"], d(2), tier="HIGH", score="0.95")
        other_acme = self._customer("Acme Buyers", business_id=self.other_business_id)
        self._invoice("A-HIGH", "5000", d(-10), customer_id=other_acme.id, business_id=self.other_business_id)
        self.db.commit()

    def numbers(self, **filters) -> set[str]:
        page = list_invoices_for_tenant(
            self.db, self.business_id, limit=100, origin="CURRENT", today=TODAY, **filters
        )
        return {invoice.invoice_number for invoice in page.items}

    def n(self, *keys: str) -> set[str]:
        return {self.inv[key].invoice_number for key in keys}


class TestInvoiceListQuery(InvoiceListTestCase):
    def test_counts_only_current_invoices_of_this_tenant(self):
        page = list_invoices_for_tenant(self.db, self.business_id, limit=100, origin="CURRENT", today=TODAY)
        self.assertEqual(page.total, 7)
        self.assertEqual(len(page.items), 7)

    def test_payment_status_filters(self):
        self.assertEqual(self.numbers(payment_status="OUTSTANDING"), self.n("high", "low", "gated", "partial", "undated", "legacy"))
        self.assertEqual(self.numbers(payment_status="partial"), self.n("partial"))
        self.assertEqual(self.numbers(payment_status="PAID"), self.n("paid"))

    def test_aging_filters_ignore_paid_and_undated_invoices(self):
        self.assertEqual(self.numbers(aging="overdue"), self.n("high", "gated", "partial"))
        self.assertEqual(self.numbers(aging="due-soon"), self.n("low"))
        self.assertEqual(self.numbers(aging="current"), self.n("low", "legacy"))
        self.assertEqual(self.numbers(aging="1-30"), self.n("high"))
        self.assertEqual(self.numbers(aging="31-60"), self.n("gated"))
        self.assertEqual(self.numbers(aging="61-90"), set())
        self.assertEqual(self.numbers(aging="90+"), self.n("partial"))

    def test_aging_counts_match_the_dashboard(self):
        summary = build_dashboard_summary(self.db, self.business_id, today=TODAY)
        for bucket in summary.aging:
            self.assertEqual(len(self.numbers(aging=bucket.key)), bucket.count, bucket.key)
        self.assertEqual(len(self.numbers(aging="overdue")), summary.overdue.count)
        self.assertEqual(len(self.numbers(aging="due-soon")), summary.due_soon.count)

    def test_risk_filters_respect_the_history_gate(self):
        self.assertEqual(self.numbers(risk="HIGH"), self.n("high"))
        self.assertEqual(self.numbers(risk="LOW"), self.n("low"))
        self.assertEqual(self.numbers(risk="MEDIUM"), set())
        self.assertEqual(
            self.numbers(risk="UNAVAILABLE"), self.n("gated", "paid", "partial", "undated", "legacy")
        )
        summary = build_dashboard_summary(self.db, self.business_id, today=TODAY)
        self.assertEqual(len(self.numbers(risk="HIGH", payment_status="OUTSTANDING")), summary.high_risk.count)

    def test_page_carries_only_gated_predictions(self):
        page = list_invoices_for_tenant(self.db, self.business_id, limit=100, origin="CURRENT", today=TODAY)
        self.assertEqual(page.predictions[self.inv["high"].id].risk_tier, "HIGH")
        self.assertNotIn(self.inv["gated"].id, page.predictions)

    def test_search_matches_numbers_customers_and_legacy_labels(self):
        self.assertEqual(self.numbers(q="acme"), self.n("high", "low", "paid"))
        self.assertEqual(self.numbers(q="GAMMA"), self.n("partial"))
        self.assertEqual(self.numbers(q="a-h"), self.n("high"))
        self.assertEqual(self.numbers(q="MAN-505323"), self.n("legacy"))
        self.assertEqual(self.numbers(q="%"), set())
        self.assertEqual(self.numbers(q="_"), set())

    def test_sorting_puts_missing_values_last_and_pages_are_stable(self):
        for direction in ("asc", "desc"):
            page = list_invoices_for_tenant(
                self.db, self.business_id, limit=100, origin="CURRENT", sort="due_date", direction=direction, today=TODAY,
            )
            self.assertEqual(page.items[-1].invoice_number, "NODUE-1", direction)
        by_amount = list_invoices_for_tenant(
            self.db, self.business_id, limit=100, origin="CURRENT", sort="amount", direction="asc", today=TODAY,
        )
        amounts = [float(i.amount) for i in by_amount.items]
        self.assertEqual(amounts, sorted(amounts))

        seen: list[str] = []
        for skip in (0, 3, 6):
            page = list_invoices_for_tenant(
                self.db, self.business_id, skip=skip, limit=3, origin="CURRENT", sort="uploaded", today=TODAY,
            )
            self.assertEqual(page.total, 7)
            seen.extend(i.invoice_number for i in page.items)
        self.assertEqual(len(seen), 7)
        self.assertEqual(len(set(seen)), 7)

    def test_unknown_filters_are_rejected(self):
        for bad in ({"aging": "120+"}, {"risk": "SEVERE"}, {"payment_status": "VOID"}, {"sort": "customer"}, {"direction": "up"}):
            with self.assertRaises(InvoiceQueryError, msg=str(bad)):
                list_invoices_for_tenant(self.db, self.business_id, today=TODAY, **bad)


class TestInvoiceListApi(InvoiceListTestCase):
    def get(self, token=None, **params):
        return self.client.get("/invoices", params={"origin": "CURRENT", **params}, headers=self._auth(token))

    def test_returns_one_page_with_total_and_embedded_estimates(self):
        response = self.get(limit=2, sort="amount", direction="desc")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["total"], len(body["items"])), (7, 2))
        self.assertEqual([i["invoice_number"] for i in body["items"]], ["B-GATED", "A-HIGH"])
        self.assertIsNone(body["items"][0]["prediction"])
        self.assertEqual(body["items"][1]["prediction"]["risk_tier"], "HIGH")

    def test_risk_and_search_filters(self):
        high = self.get(risk="HIGH").json()
        self.assertEqual([i["invoice_number"] for i in high["items"]], ["A-HIGH"])
        partial = self.get(q="gamma").json()["items"][0]
        self.assertEqual((partial["total_paid"], partial["outstanding_balance"]), (1000.0, 2000.0))

    def test_aging_uses_the_business_date(self):
        today = business_today()
        invoice = self._invoice("LIVE-OVERDUE", "10", today - timedelta(days=1))
        self.db.commit()
        numbers = {i["invoice_number"] for i in self.get(aging="1-30", limit=100).json()["items"]}
        self.assertIn(invoice.invoice_number, numbers)

    def test_validation_and_tenant_isolation(self):
        self.assertEqual(self.get(aging="120+").status_code, 422)
        self.assertEqual(self.get(limit=101).status_code, 422)
        other = self.get(token=self.other_token, limit=100).json()
        self.assertEqual(other["total"], 1)
        self.assertIsNone(other["items"][0]["prediction"])
        self.assertEqual(self.client.get("/invoices").status_code, 401)


class TestBatchedEligibility(Phase3TestCase):
    def test_matches_the_single_invoice_gate(self):
        customer = self._customer("Gate Parity")
        hist = lambda num, **kw: self._invoice(num, "1000", kw.pop("due"), customer_id=customer.id,
                                               origin=InvoiceOrigin.HISTORICAL.value, payment_status="PAID", **kw)
        h1 = hist("GP-1", due=date(2026, 1, 28), invoice_date=date(2026, 1, 1))
        self._pay(h1, "1000", date(2026, 1, 30))
        h2 = hist("GP-2", due=date(2026, 2, 28), invoice_date=date(2026, 2, 1))
        self._pay(h2, "500", date(2026, 2, 20))
        self._pay(h2, "500", date(2026, 3, 5))
        h3 = hist("GP-3", due=date(2026, 3, 10), invoice_date=None)
        self._pay(h3, "1000", date(2026, 3, 12))
        h4 = hist("GP-4", due=date(2026, 3, 20), invoice_date=date(2026, 3, 1))
        self._pay(h4, "2000", date(2026, 6, 1))

        targets = [
            self._invoice("GP-T1", "10", date(2026, 4, 1), customer_id=customer.id, invoice_date=date(2026, 3, 1)),
            self._invoice("GP-T2", "10", date(2026, 5, 1), customer_id=customer.id, invoice_date=date(2026, 4, 1)),
            self._invoice("GP-T3", "10", date(2026, 7, 1), customer_id=customer.id, invoice_date=date(2026, 7, 1)),
            self._invoice("GP-T4", "10", date(2026, 7, 1), customer_id=customer.id, invoice_date=None),
            self._invoice("GP-T5", "10", date(2026, 7, 1), invoice_date=date(2026, 7, 1)),
        ]
        self.db.commit()
        everything = targets + [h1, h2, h3, h4]
        batched = eligible_history_counts(self.db, self.business_id, everything)
        for invoice in everything:
            single = evaluate_prediction_eligibility(self.db, invoice).eligible_history_count
            self.assertEqual(batched[invoice.id], single, invoice.invoice_number)
        self.assertEqual([batched[t.id] for t in targets], [1, 3, 4, 0, 0])

    def test_ignores_invoices_of_other_tenants(self):
        customer = self._customer("Other Owner", business_id=self.other_business_id)
        invoice = self._invoice("FOREIGN", "10", date(2026, 7, 1), customer_id=customer.id,
                                business_id=self.other_business_id)
        self.db.commit()
        self.assertEqual(eligible_history_counts(self.db, self.business_id, [invoice]), {invoice.id: 0})


if __name__ == "__main__":
    unittest.main()
