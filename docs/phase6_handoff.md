# Phase 6 Handoff Document — Prediction Engine & Invoice Scoring

## 1. Executive Summary

Phase 5 has been completed and verified. The MSME Receivables Intelligence Platform now features an operational asynchronous processing pipeline:
* PDF invoices uploaded via `POST /invoices/upload` are enqueued in Redis (`msme:task:queue`).
* The Worker Service (`backend.app.workers.runtime`) claims tasks from PostgreSQL atomically, reads files from object storage, extracts structured invoice attributes using the text-first / OCR parser, validates records, handles duplicates idempotently, and reconciles unmatched historical payments.
* Structured invoices are persisted in the `invoices` table with `processing_status = 'PROCESSED'`.

Phase 6 will consume these structured invoices to build the **Prediction Engine & Invoice Scoring Layer**.

---

## 2. Available Artifacts & Contracts for Phase 6

### 2.1 Database Entities Ready for Scoring
| Table | Fields Available |
| :--- | :--- |
| `invoices` | `id`, `business_id`, `customer_id`, `invoice_number`, `invoice_date`, `due_date`, `amount`, `currency`, `payment_terms`, `payment_status` ('OPEN', 'PAID', 'OVERDUE'), `processing_status` ('PROCESSED', 'ERROR') |
| `customers` | `id`, `business_id`, `name`, `customer_ref` |
| `payments` | `id`, `business_id`, `invoice_id`, `invoice_reference`, `payment_date`, `amount`, `reference` |
| `invoice_documents` | `id`, `business_id`, `storage_key`, `processing_status`, `invoice_id` |
| `tasks` | `id`, `business_id`, `task_type`, `status`, `payload`, `invoice_id` |

### 2.2 Phase 2 ML Model Artifacts
* **Classifier Model**: `models/v1_classifier.json` (Binary classification for payment delay risk: on-time vs. delayed).
* **Regressor Model**: `models/v1_regressor.json` (Days to payment regression).
* **Metrics & Thresholds**: `models/metrics.json` (Optimal decision thresholds and cross-validation scores).
* **Feature Schema**: `data/processed/feature_metadata.json` and `backend/ml/features/build_features.py`.

### 2.3 Existing Worker Task Infrastructure
* `TaskRouter` in `backend/app/workers/router.py` can register additional handlers (e.g. `score_invoice` or batch scoring jobs) if scoring is triggered asynchronously via Redis or synchronously on invoice lookup.

---

## 3. Scope of Phase 6

Phase 6 will implement:
1. **As-of Inference Feature Builder**: Build real-time feature vector for an invoice using tenant's historical invoice and payment history (leakage-safe as-of invoice date).
2. **Scoring Service**:
   - Load Phase 2 XGBoost models in-memory with caching.
   - Run inference to calculate `probability_late`, `predicted_delay_days`, and `predicted_payment_date`.
   - Map probabilities to risk categories (`LOW`, `MEDIUM`, `HIGH`).
3. **Database Schema / Migration**:
   - Store scoring results (e.g. `risk_score`, `risk_band`, `predicted_payment_date`, `scored_at`).
4. **Scoring Endpoints**:
   - `GET /invoices/{id}/prediction` or score trigger on parsed invoices.
5. **Unit & Integration Tests**:
   - Comprehensive test suite for inference pipeline, model loading, feature calculation, and scoring isolation.

---

## 4. Phase Boundaries & Constraints

* **Do NOT modify** the Phase 5 worker runtime loop, Redis queue contracts, or parser extraction logic.
* **Do NOT retrain** Phase 2 models or alter model hyperparameter contracts.
* **Preserve strict tenant isolation**: Every invoice scored must strictly compute feature aggregates from that specific tenant's history only.
* **Maintain PostgreSQL as authoritative**: Model outputs and predictions must be persisted in PostgreSQL.
