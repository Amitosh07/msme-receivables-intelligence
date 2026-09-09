# Phase 3 — Backend Foundation, Local PostgreSQL, Database & Authentication

**MSME Receivables Intelligence Platform — Version 1**  
**Completed:** 2026-09-10  

---

## 1. Backend Architecture

The Phase 3 backend establishes the core FastAPI service and PostgreSQL persistence layer for the MSME Receivables Intelligence Platform:

```text
HTTP Client (Browser / API Client)
                │
                ▼
         FastAPI App (backend/app/main.py)
                │
    ┌───────────┴───────────┐
    ▼                       ▼
Health Check          Auth Endpoints
(/health)             (/auth/register, /auth/login, /auth/me)
                            │
                            ▼
                     Service Layer
                 (auth_service, business_service)
                            │
                            ▼
              SQLAlchemy ORM + Tenant Scoping
                (models, dependencies)
                            │
                            ▼
             Local PostgreSQL (msme_receivables)
                   localhost:5432
```

### Key Principles
- **PostgreSQL is the Authoritative Source of Truth:** Application state (users, tenants, customers, invoices, predictions) is stored in relational PostgreSQL tables with strict foreign key constraints and indexes.
- **Server-Side Tenant Enforcement:** The tenant context is resolved server-side from verified JWT identities and database memberships. Client-supplied tenant IDs are never trusted.
- **Layered Design:** Routes remain thin, delegating business operations to services and persistence to SQLAlchemy models.
- **No Premature Complexity:** Phase 3 strictly implements backend foundation and authentication without introducing Redis workers, PDF parsers, or frontend assets.

---

## 2. Local PostgreSQL Setup

In alignment with project infrastructure decisions, the backend connects directly to the existing local PostgreSQL installation rather than running PostgreSQL inside Docker.

### Connection Parameters
- **Host:** `localhost`
- **Port:** `5432`
- **Database:** `msme_receivables`
- **User:** `postgres`
- **Password:** Sourced exclusively from `.env` (never hard-coded)
- **Engine Driver:** `psycopg` (via `postgresql+psycopg://`)

---

## 3. Environment Configuration (`.env`)

Secrets and connection parameters are loaded via `pydantic-settings` from the root `.env` file. The root `.gitignore` explicitly prevents `.env` from being tracked.

### Template (`.env.example`)
```env
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/msme_receivables

JWT_SECRET_KEY=replace-with-a-long-random-secret
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

ENVIRONMENT=development
```

---

## 4. Authentication Flow

### Registration (`POST /auth/register`)
1. Client submits: `email`, `password`, `full_name`, `business_name`.
2. Service normalizes email (lowercase, stripped) and verifies global uniqueness.
3. Password is cryptographically hashed using **bcrypt**.
4. In a single atomic database transaction:
   - Creates `Business` record.
   - Creates `User` record.
   - Creates `Membership` record associating user to business with `role="owner"`.
5. Returns signed JWT access token and entity metadata with HTTP 201.
6. If any step fails, the entire transaction is rolled back.

### Login (`POST /auth/login`)
1. Client submits: `email`, `password`.
2. Service fetches user by normalized email and verifies password against `password_hash` via `passlib.context.CryptContext`.
3. Verifies account `is_active == True`.
4. Resolves the user's primary business tenant from `memberships`.
5. Returns signed JWT access token and user/business profile.

### Identity Verification (`GET /auth/me`)
1. Client supplies `Authorization: Bearer <token>`.
2. `get_tenant_context` dependency decodes and validates JWT token signature and expiration.
3. Retrieves database user and checks active status.
4. Queries database membership to establish authoritative tenant identity.
5. Returns profile containing `email`, `full_name`, `business_id`, `business_name`, and `role`. Sensitive fields (`password_hash`) are never returned.

---

## 5. JWT Strategy

- **Algorithm:** HMAC-SHA256 (`HS256`).
- **Secret Key:** Injected via `JWT_SECRET_KEY` in `.env`.
- **Payload Claims:**
  - `sub`: User UUID string (authoritative identity anchor)
  - `business_id`: Business UUID string
  - `role`: Membership role (`owner`, `member`)
  - `iat`: Timestamp of issuance (UTC)
  - `nbf`: Not before timestamp (UTC)
  - `exp`: Expiration timestamp (UTC, configurable via `ACCESS_TOKEN_EXPIRE_MINUTES`)
- **Server Verification:** While `business_id` is present in token claims, protected operations always verify the user's active membership directly against the PostgreSQL database.

---

## 6. Business / Tenant Model

The multi-tenant architecture organizes all enterprise data under a tenant boundary:

```text
Business (Tenant)
  │
  ├── Memberships (Users + Roles)
  │      └── User
  │
  ├── Customers
  │      └── Invoices
  │               ├── Payments
  │               ├── InvoiceDocuments
  │               └── PredictionResults
  │
  ├── CashflowForecasts
  │
  └── Tasks
```

---

## 7. Tenant Isolation

Tenant isolation is mandatory and strictly enforced server-side:

1. **Authorization Anchor:** `get_tenant_context` derives `business_id` from the database `Membership` table using the verified JWT user ID.
2. **Client Parameter Immunity:** Clients cannot supply a query parameter or body field such as `{"business_id": "other-uuid"}` to access foreign records.
3. **Query Scoping:** All future tenant-scoped database queries explicitly filter by `business_id == tenant_context.business_id`.
4. **Unique Constraints:** Natural identifiers (such as `invoice_number`) are unique **per tenant** (`UniqueConstraint('business_id', 'invoice_number')`), allowing distinct tenants to use identical invoice naming schemes.

---

## 8. Database Schema

The initial migration (`0001_initial_phase3_schema.py`) establishes 10 core relational tables in `msme_receivables`:

| Table Name | Description | Key Indexes / Constraints |
| :--- | :--- | :--- |
| `businesses` | Tenant organization accounts | Primary key `id` (UUID) |
| `users` | Authenticated user credentials | Unique index on `email` |
| `memberships` | User-to-business tenant associations | Unique constraint `(user_id, business_id)`; FKs to `users`, `businesses` |
| `customers` | B2B buyer client accounts | Index on `business_id`; FK to `businesses` |
| `invoices` | Commercial billed invoices | Unique constraint `(business_id, invoice_number)`; indexes on `business_id`, `customer_id`, `invoice_number` |
| `payments` | Cash remittances clearing invoices | Indexes on `business_id`, `invoice_id`; FKs to `businesses`, `invoices` |
| `invoice_documents` | Uploaded document metadata | Indexes on `business_id`, `invoice_id`; FK to `businesses` |
| `prediction_results` | ML delay risk and timing predictions | Indexes on `business_id`, `invoice_id`; FKs to `businesses`, `invoices` |
| `cashflow_forecasts` | Aggregated cashflow projections | Index on `business_id`; FK to `businesses` |
| `tasks` | Background job execution tracking | Indexes on `business_id`, `status`; FK to `businesses` |

### Payment Status vs. Processing Status
In accordance with PRD specifications, the `invoices` table strictly separates operational payment state from asynchronous system processing state:
- `payment_status`: `OPEN`, `PAID`, `CANCELLED`
- `processing_status`: `PENDING`, `PROCESSING`, `PROCESSED`, `ERROR`

---

## 9. Alembic Migration Workflow

Database schema evolution is managed through Alembic. Application tables are never created by raw ad-hoc SQL.

- **Configuration:** `alembic.ini` located at project root.
- **Script Location:** `backend/migrations/`
- **Dynamic Database URL:** `backend/migrations/env.py` reads `settings.DATABASE_URL` dynamically from `.env` to prevent hard-coding credentials.
- **Migration Command:**
  ```bash
  alembic upgrade head
  ```

---

## 10. Local Development Commands

### 1. Apply Migrations
```bash
alembic upgrade head
```

### 2. Launch FastAPI Development Server
```bash
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```
- API Root: `http://localhost:8000/`
- Swagger UI Docs: `http://localhost:8000/docs`
- ReDoc Docs: `http://localhost:8000/redoc`

### 3. Check System Health
```bash
curl http://localhost:8000/health
```

---

## 11. Security Decisions

1. **Password Hashing:** Passwords are never stored in plaintext. Bcrypt hashing with random salts is enforced via `passlib[bcrypt]`.
2. **Credential Redaction:** Database credentials, passwords, and JWT secret keys are never included in error responses, log outputs, or repository files.
3. **No Foreign Tenant Access:** Even if a user knows the UUID of another business or another business's invoice, queries filtered by the authenticated `tenant_id` return `None` (404/403).
4. **Timezone Standardization:** All timestamps use PostgreSQL `TIMESTAMPTZ` with timezone-aware Python `datetime` (UTC).

---

## 12. Test Strategy

Testing verifies connectivity, authentication, schema integrity, and tenant isolation:

- `backend/tests/test_database.py`:
  - Validates active connection to `localhost:5432/msme_receivables`.
  - Verifies presence of all 10 domain tables + `alembic_version`.
  - Tests basic CRUD operations with transactional teardown.
- `backend/tests/test_auth.py`:
  - Tests `/health` endpoint readiness.
  - Tests valid tenant registration (atomic creation of Business + User + Membership).
  - Tests duplicate email rejection (HTTP 409).
  - Tests valid login and JWT issuance.
  - Tests invalid password rejection (HTTP 401).
  - Tests inactive user rejection (HTTP 403).
  - Tests `/auth/me` profile retrieval and token validation.
- `backend/tests/test_tenant_isolation.py`:
  - Creates distinct tenants: Business A (User A) and Business B (User B).
  - Seeds tenant-owned customers and invoices for both.
  - Asserts User A queries return only Business A data and cannot retrieve Business B data.
  - Asserts User B queries return only Business B data and cannot retrieve Business A data.
  - Asserts spoofed tenant IDs in tokens fail server-side database validation.

### Full Regression Results
```text
Ran 56 tests in 14.117s

OK
```
All Phase 0 data audit tests (8), Phase 1 feature pipeline tests (7), Phase 2 ML model tests (23), and Phase 3 backend tests (18) pass without error.

---

## 13. What Later Phases Will Consume

The Phase 3 foundation provides immediate integration anchors for upcoming phases:

- **Phase 4 (Document Ingestion & Storage):**
  - Uses `InvoiceDocument` and `Invoice` database models.
  - Consumes authenticated `TenantContext` dependency to associate uploaded invoice PDFs with the current business.
- **Phase 5 (Asynchronous Job Queue & Workers):**
  - Uses `Task` database model to store job states (`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`).
- **Phase 6 (Prediction Service Integration):**
  - Persists Phase 2 inference outputs into the `PredictionResult` table.
- **Phase 7 (React Frontend):**
  - Interacts with `/auth/register`, `/auth/login`, and `/auth/me` endpoints using JWT Bearer authentication.
