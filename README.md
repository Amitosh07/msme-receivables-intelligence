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

## 7. Execution Commands

### Run Phase 1 Feature Pipeline:
```bash
python -m backend.ml.features.build_features --input data/dataset.csv --output-dir data/processed
```

### Run Phase 2 Model Training:
```bash
python -m backend.ml.training.train_all --data-dir data/processed --model-dir backend/ml/models
```

### Score Open Invoices:
```bash
python -m backend.ml.inference.predict --data-dir data/processed --model-dir backend/ml/models
```

### Run Phase 0 Audit:
```bash
python backend/ml/data/audit_dataset.py --input data/dataset.csv --output docs/data_audit_report.md
```

### Run Full Test Suite:
```bash
python -m unittest discover -s backend/tests
```

