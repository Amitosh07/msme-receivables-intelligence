# MSME Receivables Intelligence Platform — Version 1

An AI/ML-powered receivables intelligence platform that helps B2B MSMEs predict whether outstanding invoices will be paid late, estimate when payments will arrive, and prioritize collection efforts based on risk.

---

## 1. Project Overview

For many small-and-medium B2B businesses, standard accounting software tracks what customers owe, but fails to answer critical operational questions:
- **Which invoices are at high risk of late payment?**
- **When is payment realistically expected to clear?**
- **Which customers require immediate follow-up?**

This platform bridges that gap by transforming historical ERP/invoice data into predictive insights using supervised machine learning and an asynchronous web architecture.

---

## 2. Version 1 Scope

### Core Functionality
- **Multi-Tenant Authentication:** Secure JWT-based isolation for business tenants.
- **Invoice & History Ingestion:** Upload invoice documents (PDF) and historical payment records (CSV/Excel).
- **Asynchronous Processing:** Redis-backed worker service handling document parsing and inference tasks.
- **Machine Learning Predictions:**
  - **Payment Delay Classification:** Predicts whether an invoice will be paid `ON_TIME` or `LATE`.
  - **Risk Score:** Direct late-payment probability $[0.00, 1.00]$ categorized into actionable risk tiers.
  - **Payment Timing:** Predicts expected days until payment and derived calendar payment date.
- **Receivables Dashboard:** Interactive React/TypeScript UI displaying receivables aging, cash flow forecasts, and high-risk invoice queues.

---

## 3. Technology Stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy, Alembic, Pydantic v2
- **Database:** PostgreSQL 16 (Authoritative source of truth)
- **Task Queue & Cache:** Redis 7
- **Machine Learning:** Scikit-learn, XGBoost, PyArrow, Pandas, NumPy
- **Frontend:** React 18, TypeScript, Tailwind CSS, Vite
- **Infrastructure:** Docker Compose

---

## 4. Phase 1 Data & Feature Pipeline Architecture

```text
Raw ERP Dataset (data/dataset.csv)
            ↓
Data Cleaning & Deduplication (backend/ml/data/clean_data.py)
            ↓
Canonical Normalization & Target Derivation
            ↓
As-Of Historical Feature Engineering (backend/ml/features/as_of_features.py)
            ↓
Temporal Train / Validation / Test Partitioning (70% / 15% / 15%)
            ↓
Train-Only Imputation Transformer (backend/ml/features/transformations.py)
            ↓
ML-Ready Datasets & Manifest (data/processed/)
```

### Processed Partitions in `data/processed/`
- `train.parquet` (27,403 rows, 70%): Chronological period 2018-12-30 to 2019-10-08.
- `validation.parquet` (5,813 rows, 15%): Chronological period 2019-10-09 to 2019-12-09.
- `test.parquet` (5,936 rows, 15%): Chronological period 2019-12-10 to 2020-02-27.
- `open_inference.parquet` (9,681 rows): Currently outstanding invoices (posted 2020-02-27 to 2020-05-22).
- `canonical_dataset.parquet` (48,833 rows): Unified clean canonical dataset.
- `feature_metadata.json`: Feature definitions, data types, and fitted imputation values.
- `manifest.json`: Machine-readable run manifest.

---

## 5. Documentation Directory

- **Data Source & Provenance:** [`docs/data_source.md`](docs/data_source.md)
- **Data Dictionary:** [`docs/data_dictionary.md`](docs/data_dictionary.md)
- **Canonical Data Contract:** [`docs/data_contract.md`](docs/data_contract.md)
- **ML & Target Contract:** [`docs/ml_contract.md`](docs/ml_contract.md)
- **Data Leakage & As-Of Specifications:** [`docs/leakage_risks.md`](docs/leakage_risks.md)
- **Phase 0 Data Audit Report:** [`docs/data_audit_report.md`](docs/data_audit_report.md)
- **Phase 1 Pipeline Report:** [`docs/phase1_data_pipeline.md`](docs/phase1_data_pipeline.md)
- **Feature Dictionary:** [`docs/feature_dictionary.md`](docs/feature_dictionary.md)
- **Phase 2 Engineering Handoff:** [`docs/phase2_handoff.md`](docs/phase2_handoff.md)
- **Phase 2 Model Training Report:** [`docs/phase2_model_training.md`](docs/phase2_model_training.md)
- **Model Evaluation Report:** [`docs/model_evaluation.md`](docs/model_evaluation.md)
- **Phase 3 Backend Foundation Report:** [`docs/phase3_backend_foundation.md`](docs/phase3_backend_foundation.md)
- **Phase 4 Ingestion Pipeline Report:** [`docs/phase4_ingestion.md`](docs/phase4_ingestion.md)
- **Phase 5 Engineering Handoff:** [`docs/phase5_handoff.md`](docs/phase5_handoff.md)
- **Phase 5 Worker & Parser Report:** [`docs/phase5_worker_and_parser.md`](docs/phase5_worker_and_parser.md)
- **Phase 6 Engineering Handoff:** [`docs/phase6_handoff.md`](docs/phase6_handoff.md)

---

## 6. Phase 2 — V1 Payment Prediction Models

### Architecture

```text
Phase 1 ML-Ready Datasets (data/processed/)
                ↓
  ┌─────────────┴─────────────┐
  │                           │
XGBClassifier              XGBRegressor
(is_late target)           (log1p(days_until_payment) target)
  │                           │
  ├→ risk_score              ├→ predicted_days_until_payment
  ├→ is_late_predicted       │
  ├→ risk_tier (LOW/MED/HI) │
  │                           │
  └─────────────┬─────────────┘
                ↓
    V1Predictor Inference Module
    (backend/ml/inference/predict.py)
                ↓
    Scored Open Invoices (data/processed/scored_open_invoices.parquet)
```

### Model Results (Holdout Test Set)

| Model | Metric | XGBoost V1 | Baseline | Δ |
|:------|:-------|:-----------|:---------|:--|
| **Classifier** | Accuracy | 0.7909 | 0.7316 | +0.0593 |
| | F1 | 0.7047 | 0.6192 | +0.0855 |
| | ROC-AUC | 0.8456 | — | — |
| | Precision | 0.7984 | 0.7057 | +0.0927 |
| | Recall | 0.6308 | 0.5515 | +0.0793 |
| **Timing** | MAE | 2.82 days | 3.41 days | −0.59 |
| | RMSE | 7.75 | 8.68 | −0.93 |
| | Median AE | 0.91 | 1.00 | −0.09 |

### Model Artifacts in `backend/ml/models/`

- `payment_classifier_v1.json` — XGBoost binary classifier
- `payment_timing_v1.json` — XGBoost timing regressor
- `classifier_encoder_v1.joblib` — Fitted OrdinalEncoder for classifier categoricals
- `timing_encoder_v1.joblib` — Fitted OrdinalEncoder for timing categoricals
- `model_metadata.json` — Combined model metadata (features, config, training details)
- `evaluation_report.json` — Machine-readable evaluation metrics with baselines

---

## 7. Phase 3 — Backend Foundation, Local PostgreSQL & Authentication

### Prerequisites
- **Python 3.11+**
- **PostgreSQL 16+** installed locally and running on `localhost:5432`
- **Node 18+** (for frontend in later phases)

### Local Database Configuration
- **Database:** `msme_receivables`
- **Host:** `localhost`
- **Port:** `5432`
- **User:** `postgres`
- **Credentials:** Loaded securely from `.env` via `DATABASE_URL` (never hard-coded or committed).

```env
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/msme_receivables
JWT_SECRET_KEY=replace-with-a-long-random-secret
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
ENVIRONMENT=development
```

### Core Domain Entities
- `businesses` — Multi-tenant organization boundary
- `users` — Authenticated accounts with bcrypt password hashes
- `memberships` — User-to-business tenant associations with authorization roles (`owner`, `member`)
- `customers` — Tenant-scoped B2B client accounts
- `invoices` — Commercial invoices tracking payment and processing statuses independently
- `payments` — Remittance settlements clearing invoices
- `invoice_documents` — Uploaded invoice file metadata (PDFs)
- `prediction_results` — Persisted ML delay risk scores and timing estimates
- `cashflow_forecasts` — Aggregated cashflow projections with bounds
- `tasks` — Asynchronous background processing jobs

---

## 8. Phase 4 — Invoice & Payment Data Ingestion

### Core Ingestion Components
- **Invoice PDF Upload (`POST /invoices/upload`):** Accepts `application/pdf` multipart uploads. Performs strict content validation (non-empty, magic bytes `%PDF`, size limits up to `MAX_INVOICE_FILE_SIZE_MB`).
- **Object Storage Abstraction (`StorageService`):** Provides a clean storage interface decoupled from cloud/local providers. In development, `LocalFileStorage` stores documents under `./storage` with atomic replacement and strict path traversal protection.
- **Tenant-Scoped Storage Keys:** Files are organized strictly as `tenants/{business_id}/invoices/{document_id}.pdf`. Client-provided filenames are never used as filesystem paths.
- **Document Metadata & Status:** Persists `InvoiceDocument` records initialized with `processing_status = 'PENDING'`. No synchronous PDF parsing or ML predictions occur in Phase 4.
- **Payment History CSV Ingestion (`POST /payments/import`):** Normalizes canonical headers (`invoice_number`, `payment_date`, `payment_amount`), parses multi-format dates, validates positive amounts, filters duplicates deterministically, links matching invoices, and safely retains unmatched payment records.
- **Document & Invoice APIs:** Provides tenant-protected endpoints to list/download documents (`GET /invoices/documents`, `GET /invoices/documents/{id}`) and query invoices (`GET /invoices`, `GET /invoices/{id}`).

---

---

## 9. Phase 5 — Redis Queue, Single Worker Service & Invoice Parser

### Core Asynchronous Processing Components
- **Redis Task Queue (`TaskQueue`):** Manages a FIFO queue (`msme:task:queue`) using Redis `LPUSH` / `BRPOP` lists. Stores decoupled task envelopes with message deserialization and connection retry.
- **PostgreSQL Task Tracking (`Task` Entity):** PostgreSQL serves as the authoritative source of truth. Tasks are created in `PENDING` state and atomically claimed using SQL `RETURNING` queries to prevent race conditions.
- **Single Worker Service (`WorkerService`):** Background process listening on Redis queue. On startup, recovers all pending tasks from PostgreSQL. Implements graceful shutdown (`SIGINT` / `SIGTERM`), exponential retry handling for transient errors (up to 3 attempts), and immediate marking of permanent errors.
- **Invoice Parser (`InvoiceParser`):** Two-tier extraction strategy:
  1. *Text-first extraction:* Fast in-memory layout parsing with PyMuPDF (`fitz`) and `pypdf`.
  2. *OCR fallback:* Pixmap rendering and OCR extraction via `pytesseract` for scanned/image PDFs.
  3. *Deterministic extraction & validation:* Regex extraction for invoice numbers, issue dates, commercial payment terms, derived due dates, amounts, and currencies. No fabrication of missing fields.
- **Reconciliation & Idempotency:** Automatically matches newly parsed invoices against historical unmatched payments (marking them `PAID`), and handles duplicate tasks idempotently.

---

## 10. Phase 6 — ML/Application Integration & Risk Scoring

### Core Machine Learning Integration Components
- **Leakage-Safe As-Of Feature Builder (`build_inference_features`):** Constructs the exact 29-feature schema (`FEATURE_COLUMNS`) required by Phase 2 trained models strictly as-of invoice posting date $T$. Prior customer payment delays and invoice history strictly filter out any events occurring on or after $T$.
- **Cold-Start Imputation:** New customers ($< 3$ prior invoices) are imputed with frozen training partition medians derived during Phase 1 (`COLD_START_IMPUTATION`), preventing test-time crashes or NaN drift.
- **V1Predictor Singleton (`get_predictor`):** In-memory cached inference runner executing:
  - `payment_classifier_v1`: XGBoost binary classifier generating calibrated late payment risk scores $[0.00, 1.00]$, mapped to risk tiers (`LOW`, `MEDIUM`, `HIGH`).
  - `payment_timing_v1`: XGBoost regressor estimating non-negative days until payment, projecting the calendar `expected_payment_date = invoice_date + round(predicted_days)`.
- **Database Persistence & Idempotency:** Predictions are persisted into the PostgreSQL `prediction_results` table. Re-scoring an existing invoice updates the existing row rather than generating duplicates.
- **Single Worker Service Routing:** Extended `TaskRouter` with handlers for `predict_invoice` and `score_invoice` task types, maintaining single-worker architecture with PostgreSQL state management.
- **REST APIs:**
  - `POST /invoices/{id}/predict`: Trigger ML prediction for an invoice.
  - `GET /invoices/{id}/prediction`: Retrieve existing prediction for an invoice with tenant isolation.
  - `GET /predictions`: List predictions for tenant with pagination and `risk_tier` filtering.

---

## 11. Execution Commands

### Start Redis Service (Docker Compose):
```bash
docker compose up -d redis
```

### Apply Database Migrations (Alembic):
```bash
alembic upgrade head
```

### Start FastAPI Backend Server:
```bash
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```
Interactive OpenAPI documentation is accessible at `http://localhost:8000/docs`.

### Start Asynchronous Worker Service:
```bash
python -m backend.app.workers.runtime
```

### Run Complete Test Suite (104 tests across Phases 0, 1, 2, 3, 4, 5, and 6):
```bash
python -m unittest discover -s backend/tests -v
```

---

## 12. Phase 7 — Frontend Product

The React/TypeScript application lives directly in `frontend/` and communicates only with FastAPI. It provides registration and login, authenticated receivables views, invoice search/filtering/detail, prediction presentation, PDF invoice upload, and CSV payment-history import.

### Start the frontend

```bash
cd frontend
npm install
npm run dev
```

By default, the app calls `http://localhost:8000`. To point it at another FastAPI deployment, copy `frontend/.env.example` to `frontend/.env` and set `VITE_API_BASE_URL`.

Run the production build with:

```bash
cd frontend
npm run build
```

### Local Phase 7 workflow

1. Start PostgreSQL/Redis, FastAPI, and the worker using the commands above.
2. Start the frontend Vite server.
3. Register a business owner, then upload payment history as a CSV (`invoice_number`, `payment_date`, `payment_amount`).
4. Upload invoice PDFs. The import screen shows the server-provided document lifecycle: Pending, Processing, Ready, or Failed.
5. Review actual outstanding/overdue totals, risk prioritisation, and clearly labelled payment estimates in the dashboard and invoice detail views.

The current backend implements CSV payment imports only and does not expose `/dashboard/summary` or `/dashboard/cashflow`; the frontend therefore derives its displayed totals and prioritisation from authenticated invoice and prediction API responses. It never fabricates data.



