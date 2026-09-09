# MSME Receivables Intelligence Platform — Version 1

An AI/ML-powered receivables intelligence platform that helps B2B MSMEs predict whether outstanding invoices will be paid late, estimate when payments will arrive, and prioritize collection efforts based on risk.

---

## 1. Project Overview

For many small-and-medium B2B businesses, standard accounting software tracks what customers owe, but fails to answer critical cash-flow questions:
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
- **Machine Learning:** Scikit-learn, XGBoost, Pandas, NumPy
- **Frontend:** React 18, TypeScript, Tailwind CSS, Vite
- **Infrastructure:** Docker Compose

---

## 4. Current Phase: Phase 0 Completed

Phase 0 establishes the data engineering foundation, dataset quality audit, canonical data contracts, and leakage prevention rules.

Key Phase 0 deliverables:
- **Data Source Record:** [`docs/data_source.md`](docs/data_source.md)
- **Data Dictionary:** [`docs/data_dictionary.md`](docs/data_dictionary.md)
- **Canonical Application Data Contract:** [`docs/data_contract.md`](docs/data_contract.md)
- **ML Target Contract:** [`docs/ml_contract.md`](docs/ml_contract.md)
- **Leakage Prevention & As-Of Specifications:** [`docs/leakage_risks.md`](docs/leakage_risks.md)
- **Deterministic Data Audit Report:** [`docs/data_audit_report.md`](docs/data_audit_report.md)
- **Audit Tooling:** [`backend/ml/data/audit_dataset.py`](backend/ml/data/audit_dataset.py)
- **Validation Suite:** [`backend/tests/test_audit_dataset.py`](backend/tests/test_audit_dataset.py)

---

## 5. Running the Data Audit & Validation Tests

### Run Deterministic Data Audit:
```bash
python backend/ml/data/audit_dataset.py --input data/dataset.csv --output docs/data_audit_report.md
```

### Run Phase 0 Validation Suite:
```bash
python -m unittest discover -s backend/tests
```
