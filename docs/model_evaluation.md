# Model Evaluation Report — V1

**MSME Receivables Intelligence Platform — Version 1**

---

## 1. Payment Delay Classifier (`payment_classifier_v1`)

### Holdout Test Set Results (5,936 rows; 2019-12-10 to 2020-02-27)

| Metric | XGBoost V1 | Historical Rate Baseline | Improvement |
|:-------|:-----------|:-------------------------|:------------|
| **Accuracy** | 0.7909 | 0.7316 | +0.0593 |
| **Precision** | 0.7984 | 0.7057 | +0.0927 |
| **Recall** | 0.6308 | 0.5515 | +0.0793 |
| **F1 Score** | 0.7047 | 0.6192 | +0.0855 |
| **ROC-AUC** | 0.8456 | 0.7740 | +0.0716 |

### Confusion Matrix (Test Set)

|  | Predicted ON_TIME | Predicted LATE |
|:-|:-:|:-:|
| **Actual ON_TIME** | 3,214 | 374 |
| **Actual LATE** | 867 | 1,481 |

**Interpretation:** The classifier correctly identifies 63.1% of late invoices (recall) while maintaining 79.8% precision — meaning when it flags a late payment, it is correct ~80% of the time. 867 late invoices are missed (false negatives), and 374 on-time invoices are incorrectly flagged (false positives).

### Validation Set Results (5,813 rows; 2019-10-09 to 2019-12-09)

| Metric | XGBoost V1 | Baseline |
|:-------|:-----------|:---------|
| Accuracy | 0.8013 | 0.7382 |
| Precision | 0.7927 | 0.7286 |
| Recall | 0.6917 | 0.5663 |
| F1 | 0.7387 | 0.6373 |
| ROC-AUC | 0.8539 | 0.7784 |

**Val→Test stability:** Small metric drops are expected (out-of-time evaluation) and confirm the model generalizes.

---

## 2. Payment Timing Regressor (`payment_timing_v1`)

### Holdout Test Set Results

| Metric | XGBoost V1 | Timing Baseline | Improvement |
|:-------|:-----------|:----------------|:------------|
| **MAE** | 2.82 days | 3.41 days | −0.59 days |
| **RMSE** | 7.75 | 8.68 | −0.93 |
| **Median AE** | 0.91 days | 1.00 days | −0.09 days |

### Validation Set Results

| Metric | XGBoost V1 | Baseline |
|:-------|:-----------|:---------|
| MAE | 2.24 days | 2.74 days |
| RMSE | 4.49 | 5.43 |
| Median AE | 0.92 days | 1.00 days |

**Interpretation:** The timing model predicts payment arrival within ~2.8 days MAE on average on the test set. The median prediction error of 0.91 days means the majority of predictions are within 1 day of actual payment timing.

---

## 3. Top-10 Feature Importances

### Classifier

| Rank | Feature | Importance |
|:-----|:--------|:-----------|
| 1 | `cust_late_payment_rate` | 0.1898 |
| 2 | `is_weekend_due` | 0.1518 |
| 3 | `cust_median_delay` | 0.1403 |
| 4 | `due_day_of_week` | 0.0660 |
| 5 | `term_duration_days` | 0.0367 |
| 6 | `is_weekend_posting` | 0.0367 |
| 7 | `cust_avg_delay` | 0.0337 |
| 8 | `days_until_due_at_posting` | 0.0292 |
| 9 | `cust_prior_payment_count` | 0.0269 |
| 10 | `posting_day_of_week` | 0.0253 |

**Key insight:** Customer payment behavior history (late rate, median delay) and calendar features (weekend due dates) are the strongest predictors of late payment.

### Timing Model

| Rank | Feature | Importance |
|:-----|:--------|:-----------|
| 1 | `days_until_due_at_posting` | 0.2018 |
| 2 | `cust_median_delay` | 0.1817 |
| 3 | `cust_avg_delay` | 0.1370 |
| 4 | `term_duration_days` | 0.0697 |
| 5 | `payment_terms` | 0.0683 |
| 6 | `business_code` | 0.0646 |
| 7 | `cust_late_payment_rate` | 0.0359 |
| 8 | `cust_min_delay` | 0.0271 |
| 9 | `is_weekend_due` | 0.0157 |
| 10 | `log_amount` | 0.0152 |

**Key insight:** The timing model relies heavily on invoice structure (days until due, term duration) and customer historical payment patterns to estimate payment arrival.

---

## 4. Baseline Descriptions

### Classification Baseline
- **Method:** Historical customer late-payment rate threshold
- **Rule:** `cust_late_payment_rate >= 0.50 → LATE (1)`, else `ON_TIME (0)`
- **Cold-start:** Predict majority class ON_TIME (0)

### Timing Baseline
- **Method:** Term-adjusted customer median delay
- **Formula:** `term_duration_days + cust_median_delay`
- **Cold-start:** `term_duration_days + 0.0` (global median delay)

---

## 5. Model vs. Baseline Verdict

Both V1 models meaningfully outperform their operational baselines:

- **Classifier:** +8.6% absolute F1 improvement, +5.9% accuracy, +7.2% ROC-AUC
- **Timing:** −0.59 days MAE improvement, −0.93 RMSE improvement

The improvements confirm that XGBoost captures non-linear feature interactions beyond simple historical averages.
