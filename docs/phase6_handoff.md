# Phase 6 Engineering Handoff: ML/Application Integration & Risk Scoring

**MSME Receivables Intelligence Platform — Version 1**  
**Authoritative Handoff Document**  

---

## 1. Executive Summary

Phase 6 has been completed and fully verified across the entire codebase. The MSME Receivables Intelligence Platform now features an operational, leakage-safe machine learning prediction pipeline connecting Phase 2 trained models (`payment_classifier_v1`, `payment_timing_v1`) to the FastAPI backend, PostgreSQL database, and asynchronous background worker:

* **Leakage-Safe As-Of Feature Pipeline:** Reproduces the exact 29-feature schema required by the V1 models. All customer payment delay and invoice history features are computed strictly as-of invoice posting date $T$ ($< T$), eliminating target leakage and time-travel bugs.
* **Cold-Start Handling:** Uses frozen training-partition medians (`COLD_START_IMPUTATION`) for customers with $< 3$ prior invoices, ensuring robust, crash-free inference for new accounts.
* **Model Inference Engine (`V1Predictor`):** In-memory cached singleton running XGBoost classification (calibrated late payment risk score $[0.00, 1.00]$ and risk tier `LOW`/`MEDIUM`/`HIGH`) and regression (non-negative predicted days until payment and derived calendar `expected_payment_date`).
* **Database Persistence & Migration:** Added `risk_tier` (indexed `varchar(16)`) to `prediction_results` table via Alembic revision `0003_add_risk_tier`. Prediction results are stored and updated idempotently.
* **Worker Task Routing:** Extended the single worker service (`WorkerService` / `TaskRouter`) to handle `predict_invoice` and `score_invoice` background tasks.
* **Tenant-Safe REST APIs:** Provides `POST /invoices/{id}/predict`, `GET /invoices/{id}/prediction`, and `GET /predictions` (with pagination and `risk_tier` filtering).
* **Test Suite:** 104 tests passing across all project phases (9 new integration tests for Phase 6).

---

## 2. Architecture Overview

```text
┌────────────────────────────────────────────────────────┐
│               FastAPI API & Endpoints                  │
│  POST /invoices/{id}/predict  GET /invoices/{id}/pred  │
└──────────────────────────┬─────────────────────────────┘
                           │
       ┌───────────────────┴───────────────────┐
       │                                       ▼
       │                          ┌──────────────────────────┐
       │                          │    Single Worker Service │
       │                          │    (predict_invoice)     │
       ▼                          └────────────┬─────────────┘
┌──────────────────────────────────────────────┴─────────────┐
│                 Prediction Service Layer                   │
│                                                            │
│ 1. Validate invoice state & enforce tenant isolation       │
│ 2. Query as-of customer history strictly < invoice_date    │
│ 3. Cold-start fallback imputation with frozen medians      │
│ 4. Build 29-feature matrix (assert zero prohibited cols)   │
│ 5. Execute cached V1Predictor (Classifier + Regressor)     │
│ 6. Derive calendar expected_payment_date                   │
│ 7. Idempotently persist PredictionResult in PostgreSQL     │
└──────────────────────────────┬─────────────────────────────┘
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
┌───────────────────────────┐         ┌───────────────────────┐
│     Trained V1 Models     │         │ PostgreSQL Database   │
│  - payment_classifier_v1  │         │  prediction_results   │
│  - payment_timing_v1      │         │  (risk_tier, scores)  │
└───────────────────────────┘         └───────────────────────┘
```

---

## 3. Database Schema Changes

### Migration `0003_add_risk_tier` (`backend/migrations/versions/0003_add_risk_tier.py`)
- **Table Alteration:** Added `risk_tier` (`VARCHAR(16)`, nullable, indexed) to `prediction_results`.
- **Entity Model:** `backend/app/models/prediction.py`:
  - Added `risk_tier: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)`.

### Current `prediction_results` Schema:
| Column | Type | Constraints / Details |
| :--- | :--- | :--- |
| `id` | `UUID` | Primary Key |
| `business_id` | `UUID` | Foreign Key (`businesses.id`), Not Null, Indexed |
| `invoice_id` | `UUID` | Foreign Key (`invoices.id`), Unique, Not Null, Indexed |
| `prediction` | `BOOLEAN` | True if predicted late, False if on-time |
| `risk_score` | `FLOAT` | Direct probability of late payment $[0.00, 1.00]$ |
| `risk_tier` | `VARCHAR(16)` | Risk tier: `LOW` ($< 0.40$), `MEDIUM` ($[0.40, 0.70)$), `HIGH` ($\ge 0.70$) |
| `predicted_days_until_payment` | `FLOAT` | Non-negative expected days from invoice date |
| `expected_payment_date` | `DATE` | Derived calendar date (`invoice_date + predicted_days`) |
| `classifier_model_version` | `VARCHAR(64)` | `'payment_classifier_v1'` |
| `timing_model_version` | `VARCHAR(64)` | `'payment_timing_v1'` |
| `created_at` | `TIMESTAMPTZ` | Timestamp of creation |
| `updated_at` | `TIMESTAMPTZ` | Timestamp of last update |

---

## 4. As-Of Feature Pipeline & Cold Start Imputation

### Exact 29 Feature Schema (`FEATURE_COLUMNS`)
1. `amount`
2. `log_amount`
3. `term_duration_days`
4. `days_until_due_at_posting`
5. `posting_month`
6. `posting_day_of_month`
7. `posting_day_of_week`
8. `posting_quarter`
9. `is_weekend_posting`
10. `due_month`
11. `due_day_of_week`
12. `is_weekend_due`
13. `cust_prior_invoice_count`
14. `cust_prior_payment_count`
15. `cust_prior_late_count`
16. `cust_late_payment_rate`
17. `cust_avg_delay`
18. `cust_median_delay`
19. `cust_std_delay`
20. `cust_max_delay`
21. `cust_min_delay`
22. `cust_recent_avg_delay_3`
23. `cust_recent_late_rate_3`
24. `days_since_last_payment`
25. `days_since_prev_invoice`
26. `is_new_customer`
27. `business_code`
28. `currency`
29. `payment_terms`

### Zero Target Leakage Enforcement
The inference pipeline asserts that no prohibited target or future-outcome columns are present:
`PROHIBITED_COLUMNS = {"clear_date", "isOpen", "delay_days", "is_late", "days_until_payment"}`

### Frozen Training Partition Medians (`COLD_START_IMPUTATION`)
Extracted directly from Phase 1 `data/processed/feature_metadata.json`:
```python
COLD_START_IMPUTATION = {
    "cust_late_payment_rate": 0.329609,
    "cust_avg_delay": 0.170732,
    "cust_median_delay": 0.0,
    "cust_std_delay": 4.036694,
    "cust_max_delay": 15.0,
    "cust_min_delay": -7.0,
    "cust_recent_avg_delay_3": 0.0,
    "cust_recent_late_rate_3": 0.333333,
    "days_since_last_payment": 2.0,
    "days_since_prev_invoice": 1.0,
}
```

---

## 5. REST API Specifications

### 1. `POST /invoices/{invoice_id}/predict`
* **Description:** Triggers on-demand scoring for an invoice. Generates as-of feature vector, runs V1 ML models, and saves result.
* **Auth:** Bearer JWT token required. Scoped to authenticated tenant.
* **Response:** `200 OK`
```json
{
  "id": "7ec4009a-da4d-4cb4-aa7c-a0103bd43347",
  "business_id": "a2d99aee-c563-409a-95bc-c95ebf52f28a",
  "invoice_id": "508a04a1-b1e0-47b0-a0ba-ff1670332e7f",
  "prediction": true,
  "risk_score": 0.6095,
  "risk_tier": "MEDIUM",
  "predicted_days_until_payment": 20.3,
  "expected_payment_date": "2024-04-21",
  "classifier_model_version": "payment_classifier_v1",
  "timing_model_version": "payment_timing_v1",
  "created_at": "2026-09-10T03:13:52Z",
  "updated_at": "2026-09-10T03:13:52Z"
}
```

### 2. `GET /invoices/{invoice_id}/prediction`
* **Description:** Retrieves the existing prediction for an invoice.
* **Auth:** Bearer JWT token required. Returns `404 Not Found` if invoice does not belong to tenant or has no prediction.

### 3. `GET /predictions`
* **Description:** Lists all predictions for the authenticated tenant.
* **Query Parameters:**
  - `skip` (int, default 0)
  - `limit` (int, default 50, max 100)
  - `risk_tier` (string, optional: `LOW`, `MEDIUM`, `HIGH`)
* **Response:** `200 OK`
```json
{
  "items": [ ... ],
  "total": 12,
  "skip": 0,
  "limit": 50
}
```

---

## 6. Worker Task Routing

`TaskRouter` in `backend/app/workers/router.py` registers handlers:
* `"parse_invoice"`: `handle_parse_invoice`
* `"predict_invoice"`: `handle_predict_invoice`
* `"score_invoice"`: `handle_predict_invoice` (alias)

When a prediction task is enqueued:
1. `WorkerService.process_one_task()` atomically claims the task in PostgreSQL.
2. Dispatches to `handle_predict_invoice(db, task, payload)`.
3. Verifies invoice exists and enforces `invoice.business_id == task.business_id`.
4. Invokes `predict_for_invoice()`, updating or creating the `PredictionResult` row.
5. Marks the task as `COMPLETED` and sets `task.invoice_id`.

---

## 7. Verification & Test Suite

All 104 tests pass cleanly across the regression test suite:
```bash
python -m unittest discover -s backend/tests -v
# Output: Ran 104 tests in 29.635s - OK
```

Phase 6 Specific Tests (`backend/tests/test_prediction.py`):
1. `test_feature_row_schema_and_no_leakage`: Confirms 29 exact columns, correct types, and zero leakage columns.
2. `test_classifier_and_timing_inference_bounds`: Validates probability bounds $[0, 1]$, risk tier values, non-negative days, and correct date math.
3. `test_cold_start_customer_imputation`: Validates new customer flag ($< 3$ invoices) and fallback to frozen training partition medians.
4. `test_as_of_historical_isolation`: Validates that subsequent invoices and payments after posting date $T$ are strictly excluded from customer delay statistics.
5. `test_prediction_idempotency`: Confirms scoring the same invoice repeatedly updates the existing row without duplicate key collisions.
6. `test_failed_or_incomplete_invoice_rejected`: Ensures invoices in `ERROR` status or with non-positive amounts raise `InvoiceNotReadyError` (HTTP 400).
7. `test_tenant_boundary_isolation`: Confirms Business B cannot score, view, or list Business A's invoice predictions (HTTP 404).
8. `test_worker_task_routing_predict_invoice`: Verifies end-to-end background task execution via Redis queue for both `predict_invoice` and `score_invoice`.
9. `test_prediction_api_endpoints`: Verifies all REST API routes and `risk_tier` query filtering.

---

## 8. Handoff to Phase 7 (Frontend Application)

With Phase 6 complete, the backend is fully prepared for Phase 7 (Receivables Dashboard UI):
* Prediction data is readily accessible via `GET /predictions?risk_tier=HIGH` for the collections priority queue.
* Invoice details can fetch risk scores, tiers, and expected payment dates via `GET /invoices/{id}/prediction`.
* Cash flow forecasting widgets can aggregate `expected_payment_date` and invoice amounts.
