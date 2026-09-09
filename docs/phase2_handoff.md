# Phase 2 Engineering Handoff: Model Training & Evaluation

This document serves as the authoritative interface between **Phase 1 (Feature Engineering)** and **Phase 2 (Model Development, Baseline Comparison, and Evaluation)**.

---

## 1. Processed Datasets Quick-Reference

All datasets are persisted in Apache Parquet format under `data/processed/`:

| Artifact | File Path | Row Count | Date Range (Posting Date) | Purpose for Phase 2 |
| :--- | :--- | :---: | :--- | :--- |
| **Train Dataset** | `data/processed/train.parquet` | 27,403 | 2018-12-30 to 2019-10-08 | Fit gradient boosting models, baselines, and encoders |
| **Validation Dataset** | `data/processed/validation.parquet` | 5,813 | 2019-10-09 to 2019-12-09 | Hyperparameter tuning, early stopping, probability calibration |
| **Test Dataset** | `data/processed/test.parquet` | 5,936 | 2019-12-10 to 2020-02-27 | Final out-of-time benchmark evaluation against baselines |
| **Open Inference Dataset**| `data/processed/open_inference.parquet`| 9,681 | 2020-02-27 to 2020-05-22 | Currently outstanding invoices (unlabeled; future scoring) |
| **Full Canonical** | `data/processed/canonical_dataset.parquet` | 48,833 | 2018-12-30 to 2020-05-22 | Full unified dataset with split assignments |
| **Manifest** | `data/processed/manifest.json` | — | — | Machine-readable execution manifest |
| **Feature Metadata** | `data/processed/feature_metadata.json` | — | — | Complete feature schema and fitted imputation values |

---

## 2. Standard Consumption Code Snippet

Phase 2 scripts can load the datasets and separate features $X$, targets $y$, and metadata directly:

```python
import json
import pandas as pd

# 1. Load manifest and metadata
with open("data/processed/feature_metadata.json", "r") as f:
    meta = json.load(f)

FEATURE_COLS = meta["feature_list"]
NUMERIC_COLS = [f["feature_name"] for f in meta["features"] if "numeric" in f["data_type"]]
CATEGORICAL_COLS = [f["feature_name"] for f in meta["features"] if f["data_type"] == "categorical"]

# 2. Load Parquet partitions
train_df = pd.read_parquet("data/processed/train.parquet")
val_df = pd.read_parquet("data/processed/validation.parquet")
test_df = pd.read_parquet("data/processed/test.parquet")

# 3. Extract Feature Matrices
X_train = train_df[FEATURE_COLS]
X_val = val_df[FEATURE_COLS]
X_test = test_df[FEATURE_COLS]

# 4. Extract Targets
# Task A: Payment Delay Classifier & Risk Score
y_train_clf = train_df["is_late"].astype(int)
y_val_clf = val_df["is_late"].astype(int)
y_test_clf = test_df["is_late"].astype(int)

# Task B: Payment Timing Regressor
y_train_reg = train_df["days_until_payment"].astype(float)
y_val_reg = val_df["days_until_payment"].astype(float)
y_test_reg = test_df["days_until_payment"].astype(float)
```

---

## 3. Mandatory Model Contracts for Phase 2

### 3.1 Task A: Payment Delay Classifier & Risk Score
- **Target:** `is_late` ($1 = \text{Late}, 0 = \text{On Time}$).
- **Recommended Algorithm:** XGBoost Classifier (`XGBClassifier`) or LightGBM (`LGBMClassifier`).
- **Handling Categoricals:** Use native categorical support (`enable_categorical=True` in XGBoost) or target/ordinal encoding fit strictly on `train_df`.
- **Risk Score Formulation:**
  $$\text{risk\_score} = \text{model.predict\_proba}(X)[:, 1]$$
  *(Do NOT build a separate risk model).*
- **Primary Evaluation Metrics:** Precision, Recall, F1, ROC-AUC, PR-AUC, Brier score / Calibration curve.

### 3.2 Task B: Expected Payment Timing Regressor
- **Target:** `days_until_payment`.
- **Predicted Calendar Date:**
  $$\hat{D}_{\text{expected}} = \text{invoice\_date} + \widehat{\text{days\_until\_payment}}$$
- **Loss Function:** Mean Absolute Error (`reg:absoluteerror` / Huber loss).
- **Primary Evaluation Metrics:** MAE, Median Absolute Error, RMSE.

### 3.3 Mandatory Operational Baselines (Benchmark Targets)
Phase 2 must benchmark trained models against the established baselines:
1. **Timing Baseline:**
   - If customer has $\ge 3$ prior settled invoices before $T$: use customer's historical median delay (`cust_median_delay`).
   - If cold-start customer: use global training median delay (`0.0` days).
   $$\widehat{\text{days}}_{\text{baseline}} = \text{term\_duration\_days} + \text{cust\_median\_delay}$$
2. **Classification Baseline:**
   - If $\text{cust\_late\_payment\_rate} \ge 0.50 \implies \hat{y} = 1$, else $0$.
   - Cold start: predict majority class ($0 = \text{ON\_TIME}$).

---

## 4. Prohibited Columns & Critical Rules

### 4.1 Prohibited Columns in $X$
The following columns MUST NEVER be used as model inputs:
```text
clear_date
isOpen
delay_days
is_late
days_until_payment
doc_id
cust_number
name_customer
document type
area_business
posting_id
```

### 4.2 Handling Identifiers
Identifiers (`invoice_id`, `customer_id`, `business_code`) are strictly metadata. Do NOT convert `customer_id` into an arbitrary integer feature to prevent spurious memorization.

---

## 5. Cold-Start Protocol

When scoring new customers (`is_new_customer == 1`):
1. Historical features are automatically imputed with training-derived medians recorded in `feature_metadata.json`:
   - `cust_late_payment_rate`: `0.329609`
   - `cust_avg_delay`: `0.170732`
   - `cust_median_delay`: `0.000000`
   - `cust_std_delay`: `4.036694`
   - `cust_max_delay`: `15.000000`
   - `cust_min_delay`: `-7.000000`
   - `cust_recent_avg_delay_3`: `0.000000`
   - `cust_recent_late_rate_3`: `0.333333`
   - `days_since_last_payment`: `2.000000`
   - `days_since_prev_invoice`: `1.000000`
2. The model relies on invoice-intrinsic signals (`amount`, `log_amount`, `term_duration_days`, `payment_terms`, `currency`, calendar attributes).
