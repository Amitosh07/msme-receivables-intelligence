# Machine Learning Contract & Target Specification

This document establishes the machine learning contracts, mathematical target formulations, temporal evaluation requirements, baselines, and cold-start fallback specifications for Version 1 of the **MSME Receivables Intelligence Platform**.

---

## 1. Problem Formulation & Tasks

Version 1 implements two integrated predictive tasks:

```text
┌──────────────────────────────────────────────────────────────┐
│                    Invoice at Posting Date                   │
│                               T                              │
└──────────────────────────────┬───────────────────────────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
     [Task A: Classification]        [Task B: Timing Regression]
          Late Payment                      Payment Timing
        P(Delay > 0 days)                Expected Days to Pay
               │                               │
               ▼                               ▼
          Risk Score                   Expected Payment Date
         [0.00, 1.00]                    (Posting Date + Days)
```

---

## 2. Target Definitions & Mathematical Formulations

### 2.1 Task A: Payment Delay Classification (`is_late`)

- **Domain Concept:** Will this invoice be paid past its contractual due date?
- **Mathematical Definition:**
  $$\text{delay\_days} = \text{clear\_date} - \text{due\_in\_date}$$
  $$\text{is\_late} = \begin{cases} 1 & \text{if } \text{delay\_days} > 0 \\ 0 & \text{if } \text{delay\_days} \le 0 \end{cases}$$
- **Operational Classes:**
  - `0 (ON_TIME)`: Settled strictly on or before the due date (includes early payments).
  - `1 (LATE)`: Settled 1 or more calendar days after the due date.
- **Empirical Distribution (from Phase 0 Audit of 40,000 Settled Invoices):**
  - Total Settled Records: 40,000 (39,158 deduplicated)
  - `ON_TIME` (`is_late = 0`): 23,236 invoices (**58.09%**)
    - Paid strictly early (`delay_days < 0`): 14,786 invoices (36.96%)
    - Paid on exact due date (`delay_days == 0`): 8,450 invoices (21.12%)
  - `LATE` (`is_late = 1`): 16,764 invoices (**41.91%**)
- **Target Suitability:** Highly balanced (41.9% positive class). Excellent properties for gradient boosted trees (XGBoost / LightGBM) without requiring artificial re-sampling.

### 2.2 Task B: Risk Score Definition

- **Single Unified Model:** The product does NOT train a separate "credit risk" model.
- **Formulation:** The risk score is defined directly as the calibrated posterior probability of late payment emitted by the Task A classifier:
  $$\text{risk\_score} = P(\text{is\_late} = 1 \mid X) \in [0.0000, 1.0000]$$
- **Product Risk Tiers:**
  - **Low Risk:** $\text{risk\_score} < 0.30$
  - **Medium Risk:** $0.30 \le \text{risk\_score} < 0.65$
  - **High Risk:** $\text{risk\_score} \ge 0.65$

---

### 2.3 Task C: Payment Timing Target (`days_until_payment`)

- **Domain Concept:** How many calendar days from the invoice issuance/posting date will elapse before payment is cleared?
- **Mathematical Definition:**
  $$\text{days\_until\_payment} = \text{clear\_date} - \text{posting\_date}$$
- **Predicted Operational Date:**
  $$\hat{D}_{\text{payment}} = \text{posting\_date} + \widehat{\text{days\_until\_payment}}$$
- **Empirical Distribution Analysis:**
  - **Count:** 40,000 settled invoices
  - **Minimum:** 0.0 days (same-day payment)
  - **1st Percentile:** 2.0 days
  - **5th Percentile:** 10.0 days
  - **25th Percentile:** 12.0 days
  - **Median (50th Percentile):** 15.0 days
  - **Mean:** 18.06 days
  - **75th Percentile:** 17.0 days
  - **90th Percentile:** 27.0 days
  - **95th Percentile:** 42.0 days
  - **99th Percentile:** 76.0 days
  - **Maximum:** 205.0 days
  - **Standard Deviation:** 13.36 days
  - **Skewness:** 4.18 (moderate right skew driven by long-tail delayed payments)
- **Feasibility Assessment for Phase 2:**
  - The interquartile range is compact ($12\text{ to }17\text{ days}$), with median of $15\text{ days}$ matching standard Net 15 credit terms.
  - Direct regression on `days_until_payment` using **Mean Absolute Error (MAE / Huber loss)** is viable and recommended for Phase 2.
  - **Contingency / Alternative:** If regression residuals on the extreme 99th percentile ($> 76\text{ days}$) degrade point estimates, an aging-bucket classification formulation can be introduced as a secondary representation:
    - Bucket 1: $0\text{–}7$ days
    - Bucket 2: $8\text{–}15$ days
    - Bucket 3: $16\text{–}30$ days
    - Bucket 4: $31\text{–}60$ days
    - Bucket 5: $60+$ days

---

## 3. Temporal Split Contract (Zero Future Data Leakage)

Random k-fold row-level cross-validation is **strictly prohibited** for model evaluation because receivables data is inherently longitudinal and auto-correlated.

### 3.1 Chronological Partitioning Rules

Evaluation must use a strict time-based split ordered by `posting_date`:

```text
2018-12-30                                                         2020-02-27
├──────────────────────────────┬──────────────────┬─────────────────┤
│        Training Set          │ Validation Set   │    Test Set     │
│           (70%)              │      (15%)       │      (15%)      │
└──────────────────────────────┴──────────────────┴─────────────────┘
```

1. **Training Set ($70\%$):** Earliest chronological period ($T_0 \le t < T_1$). Used for model parameter learning.
2. **Validation Set ($15\%$):** Middle chronological period ($T_1 \le t < T_2$). Used for hyperparameter tuning, early stopping, and threshold calibration.
3. **Holdout Test Set ($15\%$):** Most recent chronological period ($T_2 \le t < T_3$). Evaluated exactly once to report true out-of-time generalization performance.

---

## 4. Benchmark Baselines Contract

To verify whether machine learning delivers genuine business value over simple operational heuristics, Phase 2 must evaluate and compare models against two mandatory baselines:

### 4.1 Timing Baseline: Historical Customer Median Delay

For an invoice with customer $C$, credit term $\text{terms}$, and posting date $T$:
$$\widehat{\text{days}}_{\text{baseline}} = \text{term\_duration\_days} + \text{median\_historical\_delay}(C, t < T)$$

- If customer $C$ has $\ge 3$ historical cleared invoices prior to $T$: use customer's historical median payment delay.
- **Cold-Start Fallback:** If customer $C$ has $< 3$ historical invoices prior to $T$, use the global median delay ($0\text{ days}$).

### 4.2 Classification Baseline: Historical Customer Late-Rate Rule

$$\hat{y}_{\text{baseline}} = \begin{cases} 1 & \text{if } \text{late\_rate}(C, t < T) \ge 0.50 \\ 0 & \text{otherwise} \end{cases}$$
- For cold-start customers with no prior history: predict the global majority class ($0 = \text{ON\_TIME}$).

---

## 5. Cold-Start Handling Strategy

### 5.1 Audit Findings
- **27.4%** of all customers in the dataset have only 1 recorded invoice.
- **59.4%** of customers have 5 or fewer invoices.
- In the open/unsettled invoice snapshot, **7.78%** of customers represent true cold starts with zero historical closed records.
- When computing features chronologically as-of posting date, **100% of customers start with zero history** at their initial transaction.

### 5.2 Mandatory Cold-Start Requirements for Phase 1 & 2
1. **Fallback Imputation:**
   All historical customer rolling features (e.g. `customer_avg_delay`, `customer_late_rate`) must have default fallback values:
   - `customer_prior_invoice_count = 0`
   - `customer_avg_delay_fallback = global_training_median_delay` ($0.0$)
   - `customer_late_rate_fallback = global_training_late_rate` ($0.419$)
2. **Indicator Feature:**
   Include a boolean feature:
   $$\text{is\_new\_customer} = \begin{cases} 1 & \text{if } \text{prior\_invoice\_count} < 3 \\ 0 & \text{otherwise} \end{cases}$$
3. **Invoice-Intrinsic Features:**
   Models must leverage customer-independent invoice features that are always available at invoice generation:
   - `amount` / $\log(1 + \text{amount})$
   - `payment_terms_duration_days`
   - `currency`
   - `business_code`
   - Calendar attributes: `posting_day_of_week`, `posting_day_of_month`, `posting_month`
