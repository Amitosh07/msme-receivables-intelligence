# Phase 2 — V1 Model Training Report

**MSME Receivables Intelligence Platform — Version 1**

**Completed:** 2026-09-10

---

## 1. Overview

Phase 2 implements two XGBoost-based V1 payment prediction models using the temporal feature-engineered datasets from Phase 1:

1. **Payment Delay Classifier** (`XGBClassifier`): Binary prediction of ON_TIME (0) vs LATE (1)
2. **Payment Timing Regressor** (`XGBRegressor`): Predicts `days_until_payment` with `log1p` target transform

Both models are trained on the chronological training partition (2018-12-30 to 2019-10-08), validated on the out-of-time validation set (2019-10-09 to 2019-12-09), and evaluated on the held-out test set (2019-12-10 to 2020-02-27).

---

## 2. Training Configuration

### Classifier (`XGBClassifier`)

| Parameter | Value |
|:----------|:------|
| n_estimators | 500 (early stopping engaged at iteration 149) |
| max_depth | 6 |
| learning_rate | 0.05 |
| subsample | 0.8 |
| colsample_bytree | 0.8 |
| min_child_weight | 5 |
| gamma | 0.1 |
| reg_alpha | 0.1 |
| reg_lambda | 1.0 |
| objective | binary:logistic |
| eval_metric | logloss |
| early_stopping_rounds | 50 |

### Timing Regressor (`XGBRegressor`)

| Parameter | Value |
|:----------|:------|
| n_estimators | 500 (early stopping engaged at iteration 335) |
| max_depth | 6 |
| learning_rate | 0.05 |
| subsample | 0.8 |
| colsample_bytree | 0.8 |
| min_child_weight | 5 |
| gamma | 0.1 |
| reg_alpha | 0.1 |
| reg_lambda | 1.0 |
| objective | reg:absoluteerror |
| eval_metric | mae |
| early_stopping_rounds | 50 |
| Target Transform | `log1p(days_until_payment)` → `expm1` at inference |

---

## 3. Feature Encoding

All 29 Phase 1 features are used (26 numeric + 3 categorical).

Categorical features (`business_code`, `currency`, `payment_terms`) are encoded using scikit-learn `OrdinalEncoder`:
- **Fitted on training data only** to prevent leakage
- Unknown categories at inference time mapped to `-1`
- Encoders serialized as `.joblib` artifacts for inference consistency

---

## 4. Baselines

### Classification Baseline
- **Rule:** If `cust_late_payment_rate >= 0.50` → predict LATE (1), else ON_TIME (0)
- **Cold-start (new customers):** Predict majority class ON_TIME (0)
- **Purpose:** Simple historical customer behavior rule

### Timing Baseline
- **Rule:** `term_duration_days + cust_median_delay`
- **Cold-start:** `term_duration_days + 0.0` (global training median delay)
- **Clamp:** Non-negative (≥ 0)

---

## 5. Risk Score

The classifier's late-class probability IS the risk score:

```
risk_score = model.predict_proba(X)[:, 1]
```

Mapped to tiers per the ML contract:
- **LOW Risk:** risk_score < 0.30
- **MEDIUM Risk:** 0.30 ≤ risk_score < 0.65
- **HIGH Risk:** risk_score ≥ 0.65

---

## 6. Leakage Prevention

- All temporal splits are strict (train < val < test, no overlap)
- Prohibited columns verified absent from all feature matrices
- Target columns (`is_late`, `delay_days`, `days_until_payment`) verified absent from feature set
- Categorical encoders fit on training data only
- As-of features computed using only information available at each invoice's posting date

---

## 7. Artifacts Produced

| Artifact | Path | Description |
|:---------|:-----|:------------|
| Classifier model | `backend/ml/models/payment_classifier_v1.json` | XGBoost JSON serialization |
| Timing model | `backend/ml/models/payment_timing_v1.json` | XGBoost JSON serialization |
| Classifier encoder | `backend/ml/models/classifier_encoder_v1.joblib` | OrdinalEncoder for categoricals |
| Timing encoder | `backend/ml/models/timing_encoder_v1.joblib` | OrdinalEncoder for categoricals |
| Model metadata | `backend/ml/models/model_metadata.json` | Combined config & schema |
| Evaluation report | `backend/ml/models/evaluation_report.json` | Metrics for both models + baselines |
| Scored invoices | `data/processed/scored_open_invoices.parquet` | 9,681 open invoices scored |

---

## 8. Reproduction

### Train Models
```bash
python -m backend.ml.training.train_all --data-dir data/processed --model-dir backend/ml/models
```

### Score Open Invoices
```bash
python -m backend.ml.inference.predict --data-dir data/processed --model-dir backend/ml/models
```

### Run Tests
```bash
python -m unittest backend.tests.test_models -v
```

---

## 9. Scored Open Invoices Summary

9,681 currently outstanding invoices were scored:

| Metric | Value |
|:-------|:------|
| Mean risk score | 0.3890 |
| Median risk score | 0.2576 |
| Risk score std | 0.2878 |
| LOW risk count | 5,515 (57.0%) |
| MEDIUM risk count | 1,950 (20.1%) |
| HIGH risk count | 2,216 (22.9%) |
| Mean predicted days | 15.35 |
| Median predicted days | 15.18 |
| P95 predicted days | 24.65 |

---

## 10. Code Modules

| Module | Purpose |
|:-------|:--------|
| `backend/ml/training/train_classifier.py` | Classifier training, baseline, evaluation |
| `backend/ml/training/train_timing.py` | Timing model training, baseline, evaluation |
| `backend/ml/training/train_all.py` | Orchestrator: trains both models, saves metadata |
| `backend/ml/inference/predict.py` | `V1Predictor` class for schema validation & prediction |
| `backend/tests/test_models.py` | 23 unit tests for models, inference, leakage, baselines |
