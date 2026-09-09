"""
Authentication and authorization tests for registration, login, JWT tokens, and /auth/me.
"""

import unittest
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.business import Business
from backend.app.models.membership import Membership
from backend.app.models.user import User


class TestAuthEndpoints(unittest.TestCase):
    """Test suite for authentication endpoints."""

    def setUp(self):
        self.client = TestClient(app)
        self.db: Session = SessionLocal()
        self.created_user_ids = []
        self.created_business_ids = []

    def tearDown(self):
        """Clean up test records."""
        try:
            for uid in self.created_user_ids:
                user = self.db.get(User, uid)
                if user:
                    self.db.delete(user)
            for bid in self.created_business_ids:
                biz = self.db.get(Business, bid)
                if biz:
                    self.db.delete(biz)
            self.db.commit()
        except Exception:
            self.db.rollback()
        finally:
            self.db.close()

    def test_health_endpoint(self):
        """Health endpoint returns 200 with healthy database status."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["database"], "healthy")
        self.assertIn("version", data)
        self.assertIn("timestamp", data)

    def test_register_valid_tenant(self):
        """Valid registration creates Business, User, and Membership atomically."""
        unique_suffix = uuid.uuid4().hex[:8]
        email = f"owner_{unique_suffix}@example.com"
        payload = {
            "email": email,
            "password": "SecurePassword123!",
            "full_name": "Test Owner",
            "business_name": f"Enterprise {unique_suffix}",
        }

        response = self.client.post("/auth/register", json=payload)
        self.assertEqual(response.status_code, 201)
        data = response.json()

        self.assertIn("access_token", data)
        self.assertEqual(data["token_type"], "bearer")
        self.assertEqual(data["user"]["email"], email)
        self.assertEqual(data["user"]["full_name"], "Test Owner")
        self.assertEqual(data["business"]["name"], f"Enterprise {unique_suffix}")

        user_id = uuid.UUID(data["user"]["id"])
        business_id = uuid.UUID(data["business"]["id"])
        self.created_user_ids.append(user_id)
        self.created_business_ids.append(business_id)

        # Verify database state
        db_user = self.db.get(User, user_id)
        self.assertIsNotNone(db_user)
        self.assertNotEqual(db_user.password_hash, "SecurePassword123!")
        self.assertTrue(db_user.password_hash.startswith("$2b$") or db_user.password_hash.startswith("$2a$"))

        # Verify membership role is owner
        membership = self.db.scalar(
            select(Membership).where(Membership.user_id == user_id, Membership.business_id == business_id)
        )
        self.assertIsNotNone(membership)
        self.assertEqual(membership.role, "owner")

    def test_register_duplicate_email_rejected(self):
        """Duplicate email registration must be rejected with 409 Conflict."""
        unique_suffix = uuid.uuid4().hex[:8]
        email = f"dup_{unique_suffix}@example.com"
        payload = {
            "email": email,
            "password": "Password123!",
            "full_name": "First User",
            "business_name": "First Business",
        }

        res1 = self.client.post("/auth/register", json=payload)
        self.assertEqual(res1.status_code, 201)
        self.created_user_ids.append(uuid.UUID(res1.json()["user"]["id"]))
        self.created_business_ids.append(uuid.UUID(res1.json()["business"]["id"]))

        # Attempt duplicate
        payload2 = {
            "email": email.upper(),  # Verify case-insensitive check
            "password": "OtherPassword456!",
            "full_name": "Second User",
            "business_name": "Second Business",
        }
        res2 = self.client.post("/auth/register", json=payload2)
        self.assertEqual(res2.status_code, 409)
        self.assertIn("already exists", res2.json()["detail"].lower())

    def test_login_valid_credentials(self):
        """Valid login returns JWT access token."""
        unique_suffix = uuid.uuid4().hex[:8]
        email = f"login_{unique_suffix}@example.com"
        password = "ValidPassword123!"

        reg_res = self.client.post("/auth/register", json={
            "email": email,
            "password": password,
            "full_name": "Login User",
            "business_name": "Login Corp",
        })
        self.assertEqual(reg_res.status_code, 201)
        self.created_user_ids.append(uuid.UUID(reg_res.json()["user"]["id"]))
        self.created_business_ids.append(uuid.UUID(reg_res.json()["business"]["id"]))

        # Login
        login_res = self.client.post("/auth/login", json={
            "email": email,
            "password": password,
        })
        self.assertEqual(login_res.status_code, 200)
        data = login_res.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["token_type"], "bearer")
        self.assertEqual(data["user"]["email"], email)

    def test_login_invalid_password_rejected(self):
        """Invalid password returns 401 Unauthorized."""
        unique_suffix = uuid.uuid4().hex[:8]
        email = f"badpass_{unique_suffix}@example.com"

        reg_res = self.client.post("/auth/register", json={
            "email": email,
            "password": "CorrectPassword123!",
            "full_name": "Pass User",
            "business_name": "Pass Corp",
        })
        self.created_user_ids.append(uuid.UUID(reg_res.json()["user"]["id"]))
        self.created_business_ids.append(uuid.UUID(reg_res.json()["business"]["id"]))

        login_res = self.client.post("/auth/login", json={
            "email": email,
            "password": "WrongPassword!",
        })
        self.assertEqual(login_res.status_code, 401)

    def test_login_inactive_user_rejected(self):
        """Inactive user account login is rejected with 403 Forbidden."""
        unique_suffix = uuid.uuid4().hex[:8]
        email = f"inactive_{unique_suffix}@example.com"
        password = "Password123!"

        reg_res = self.client.post("/auth/register", json={
            "email": email,
            "password": password,
            "full_name": "Inactive User",
            "business_name": "Inactive Corp",
        })
        uid = uuid.UUID(reg_res.json()["user"]["id"])
        self.created_user_ids.append(uid)
        self.created_business_ids.append(uuid.UUID(reg_res.json()["business"]["id"]))

        # Deactivate user
        user = self.db.get(User, uid)
        user.is_active = False
        self.db.commit()

        login_res = self.client.post("/auth/login", json={
            "email": email,
            "password": password,
        })
        self.assertEqual(login_res.status_code, 403)

    def test_get_me_authenticated(self):
        """GET /auth/me returns current user and business context."""
        unique_suffix = uuid.uuid4().hex[:8]
        email = f"me_{unique_suffix}@example.com"
        password = "Password123!"

        reg_res = self.client.post("/auth/register", json={
            "email": email,
            "password": password,
            "full_name": "Me User",
            "business_name": f"Me Enterprise {unique_suffix}",
        })
        token = reg_res.json()["access_token"]
        self.created_user_ids.append(uuid.UUID(reg_res.json()["user"]["id"]))
        self.created_business_ids.append(uuid.UUID(reg_res.json()["business"]["id"]))

        response = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["email"], email)
        self.assertEqual(data["business_name"], f"Me Enterprise {unique_suffix}")
        self.assertEqual(data["role"], "owner")
        self.assertNotIn("password_hash", data)

    def test_get_me_unauthenticated_rejected(self):
        """GET /auth/me without token returns 401."""
        response = self.client.get("/auth/me")
        self.assertEqual(response.status_code, 401)

    def test_get_me_invalid_token_rejected(self):
        """GET /auth/me with forged token returns 401."""
        response = self.client.get(
            "/auth/me",
            headers={"Authorization": "Bearer forged.invalid.token"},
        )
        self.assertEqual(response.status_code, 401)

    def test_openapi_security_scheme_is_http_bearer(self):
        """OpenAPI spec documents HTTPBearer (not OAuth2PasswordBearer) matching JSON login contract."""
        openapi = app.openapi()
        security_schemes = openapi.get("components", {}).get("securitySchemes", {})

        # Assert HTTPBearer is declared and matches Bearer JWT
        self.assertIn("HTTPBearer", security_schemes)
        scheme = security_schemes["HTTPBearer"]
        self.assertEqual(scheme.get("type"), "http")
        self.assertEqual(scheme.get("scheme"), "bearer")
        self.assertEqual(scheme.get("bearerFormat"), "JWT")

        # Assert OAuth2PasswordBearer is removed so Swagger does not attempt password-form login
        self.assertNotIn("OAuth2PasswordBearer", security_schemes)

    def test_openapi_route_security_enforcement(self):
        """OpenAPI spec correctly tags protected routes with HTTPBearer and leaves login public."""
        openapi = app.openapi()
        paths = openapi.get("paths", {})

        # /auth/login is public
        login_security = paths.get("/auth/login", {}).get("post", {}).get("security")
        self.assertIsNone(login_security)

        # Protected routes specify HTTPBearer
        for protected_path, method in [
            ("/auth/me", "get"),
            ("/invoices", "get"),
            ("/invoices/upload", "post"),
            ("/payments/import", "post"),
            ("/predictions", "get"),
        ]:
            route_security = paths.get(protected_path, {}).get(method, {}).get("security", [])
            self.assertEqual(
                route_security,
                [{"HTTPBearer": []}],
                f"Path {method.upper()} {protected_path} should declare HTTPBearer security",
            )


if __name__ == "__main__":
    unittest.main()
