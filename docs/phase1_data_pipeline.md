# Phase 1 — Canonical Data Pipeline & As-Of Feature Engineering Report

**Platform:** MSME Receivables Intelligence Platform — Version 1  
**Module:** Canonical Ingestion, Deduplication, As-Of Feature Engineering, and Temporal Partitioning  
**Authoritative Input:** `data/dataset.csv` (Phase 0 audited dataset)  
**Processed Output Location:** `data/processed/`

---

## 1. Executive Summary

Phase 1 establishes the production-grade data transformation and feature engineering pipeline for the MSME Receivables Intelligence Platform. It translates the raw ERP dataset into clean canonical domain entities, enforces strict temporal As-Of horizons to prevent future target lookahead leakage, derives calibrated targets, and partitions the data chronologically into ML-ready partitions.

```text
Raw ERP Dataset (50,000 rows)
            ↓
Deduplication (-1,161 exact duplicates)
            ↓
Document Type Filtering (-6 non-standard 'X2' credit records)
            ↓
Canonical Normalization & Target Derivation (48,833 records)
    ├── Paid / Settled Invoices: 39,152
    └── Open / Outstanding Invoices: 9,681
            ↓
As-Of Historical Feature Engineering Engine (Horizon T = Posting Date)
            ↓
Temporal Partitioning (70% Train / 15% Validation / 15% Test)
            ↓
Train-Only Imputation Transformer (Zero Validation/Test Statistics Leakage)
            ↓
ML-Ready Parquet Datasets + Feature Metadata + Manifest
```

---

## 2. Ingestion & Filtering Metrics

| Step | Operation | Resulting Rows | Difference | Rationale / Rule |
| :--- | :--- | :---: | :---: | :--- |
| **0. Raw Ingestion** | Load `data/dataset.csv` | 50,000 | Baseline | Audited Kaggle ERP export |
| **1. Deduplication** | Deterministic duplicate drop | 48,839 | -1,161 | 1,161 exact duplicate records sharing identical `doc_id` and values |
| **2. Document Filtering** | Exclude `document type == 'X2'` | 48,833 | -6 | 6 credit memo/adjustment records with null `invoice_id` filtered from trade receivables |
| **3. Paid Records** | Completed settlements (`clear_date` non-null) | 39,152 | 80.18% | Labeled supervised training population |
| **4. Open Records** | Currently outstanding (`clear_date` null) | 9,681 | 19.82% | Right-censored; operational inference population (`open_inference.parquet`) |

---

## 3. Canonical Domain Schema

Mapped directly from raw ERP fields to canonical contracts defined in [`docs/data_contract.md`](data_contract.md):

| Canonical Column | Data Type | Raw Source Field | Business Interpretation |
| :--- | :--- | :--- | :--- |
| `invoice_id` | `VARCHAR(64)` | `doc_id` | Unique subledger accounting document identifier |
| `business_code` | `VARCHAR(16)` | `business_code` | Billing corporate entity / operating subsidiary code |
| `customer_id` | `VARCHAR(64)` | `cust_number` | Customer buyer account key |
| `customer_name` | `VARCHAR(255)` | `name_customer` | Customer commercial trading name |
| `invoice_date` | `DATE` | `posting_date` | Date invoice was officially posted/issued ($T$) |
| `due_date` | `DATE` | `due_in_date` | Contractual payment due date |
| `baseline_date` | `DATE` | `baseline_create_date` | Terms baseline date |
| `clear_date` | `DATE` | `clear_date` | Settlement date (null for open records) |
| `amount` | `FLOAT` | `total_open_amount` | Billed invoice monetary value |
| `currency` | `VARCHAR(3)` | `invoice_currency` | Currency unit (`USD`, `CAD`) |
| `payment_terms` | `VARCHAR(16)` | `cust_payment_terms` | Commercial terms shorthand (`NAA8`, `CA10`, etc.) |
| `term_duration_days` | `INT` | Derived | Contractual duration `(due_date - baseline_date)` |
| `days_until_due_at_posting` | `INT` | Derived | Days between posting and due date `(due_date - invoice_date)` |
| `status` | `VARCHAR(8)` | Derived | Operational state: `'PAID'` vs `'OPEN'` |

---

## 4. Supervised Target Derivation (Paid Records Only)

For closed/settled invoices (`status == 'PAID'`), three targets are generated:

1. **Payment Delay Days (`delay_days`):**
   $$\text{delay\_days} = (\text{clear\_date} - \text{due\_date}).\text{days}$$
   - Measures exact delay in calendar days past due date (negative indicates early settlement).
2. **Payment Delay Classification (`is_late`):**
   $$\text{is\_late} = \begin{cases} 1 & \text{if } \text{delay\_days} > 0 \\ 0 & \text{if } \text{delay\_days} \le 0 \end{cases}$$
   - Primary classification target. Well-calibrated with 41.9% late across paid records.
3. **Payment Timing Regression Target (`days_until_payment`):**
   $$\text{days\_until\_payment} = (\text{clear\_date} - \text{invoice\_date}).\text{days}$$
   - Target for predicting total days from issuance to remittance arrival.

> [!IMPORTANT]
> **Zero Fabrication Rule:** Open records (`status == 'OPEN'`) have null `clear_date`. All three target fields are strictly set to `NaN` for open records. Targets are never fabricated or guessed for unobserved invoices.

---

## 5. As-Of Feature Engineering Architecture

### 5.1 The As-Of Information Horizon

Financial tabular models fail in production when features contain future information. For each invoice $I$ posted at calendar date $T = \text{invoice\_date}_I$:

$$\mathcal{H}(T) = \{ P \mid \text{customer}(P) = \text{customer}(I) \;\land\; \text{clear\_date}(P) < T \}$$

Every customer historical feature for invoice $I$ is calculated strictly using records in $\mathcal{H}(T)$.

### 5.2 Vectorized Chronological Engine
The implementation in [`backend/ml/features/as_of_features.py`](../backend/ml/features/as_of_features.py) leverages `np.searchsorted` over pre-sorted customer timeline arrays:
- Searchsorted locates index `idx = np.searchsorted(settled_dates, T, side='left')`.
- All slice elements `settled_dates[:idx]` represent payments cleared strictly before $T$.
- Executes across all 48,833 records in under **5.5 seconds** on Python 3.13.

### 5.3 Derived As-Of Customer Features

- `cust_prior_invoice_count`: Number of invoices posted for this customer before $T$.
- `cust_prior_payment_count`: Number of payments cleared for this customer before $T$.
- `cust_prior_late_count`: Count of payments with $\text{delay\_days} > 0$ cleared before $T$.
- `cust_late_payment_rate`: Historical late-payment proportion before $T$.
- `cust_avg_delay`: Historical mean delay days before $T$.
- `cust_median_delay`: Historical median delay days before $T$.
- `cust_std_delay`: Standard deviation of historical delays before $T$ ($0.0$ if count $< 2$).
- `cust_max_delay`: Longest historical payment delay before $T$.
- `cust_min_delay`: Earliest prepayment before $T$.
- `cust_recent_avg_delay_3`: Mean delay across the most recent $\le 3$ settled payments before $T$.
- `cust_recent_late_rate_3`: Late rate across the most recent $\le 3$ settled payments before $T$.
- `days_since_last_payment`: Days elapsed between previous settled payment and $T$.
- `days_since_prev_invoice`: Days elapsed between previous invoice issuance and $T$.

---

## 6. Cold-Start Handling & As-Of Safe Imputation

### 6.1 Cold-Start Indicator
$$\text{is\_new_customer} = \begin{cases} 1 & \text{if } \text{cust\_prior\_invoice\_count} < 3 \\ 0 & \text{otherwise} \end{cases}$$

### 6.2 Train-Only Imputation Transformer
When a customer has zero prior settled payments, historical behavioral metrics are unobserved (`NaN`).
- Imputation medians are fitted **strictly on the TRAINING partition**.
- The fitted transformer [`TrainingImputationTransformer`](../backend/ml/features/transformations.py) is frozen and applied to validation, test, and inference sets.
- **Fitted Training Medians:**
  - `cust_late_payment_rate`: `0.329609` (33.0%)
  - `cust_avg_delay`: `0.170732` days
  - `cust_median_delay`: `0.0` days
  - `cust_std_delay`: `4.036694` days
  - `cust_max_delay`: `15.0` days
  - `cust_min_delay`: `-7.0` days
  - `cust_recent_avg_delay_3`: `0.0` days
  - `cust_recent_late_rate_3`: `0.333333`
  - `days_since_last_payment`: `2.0` days
  - `days_since_prev_invoice`: `1.0` day

---

## 7. Temporal Partitioning (70 / 15 / 15)

Closed records are ordered chronologically by `invoice_date`. Date cutoffs are dynamically derived:

| Partition | Record Count | Percentage | Date Range (Posting Date) | Purpose |
| :--- | :---: | :---: | :--- | :--- |
| **Train** | 27,403 | 69.99% | 2018-12-30 to 2019-10-08 | Model training & baseline calibration |
| **Validation** | 5,813 | 14.85% | 2019-10-09 to 2019-12-09 | Hyperparameter tuning & threshold selection |
| **Test** | 5,936 | 15.16% | 2019-12-10 to 2020-02-27 | Final out-of-time evaluation |
| **Open Inference** | 9,681 | — | 2020-02-27 to 2020-05-22 | Operational inference population (unpaid) |

**Monotonicity Check:**
$$\max(\text{Train Date}) = \text{2019-10-08} < \min(\text{Val Date}) = \text{2019-10-09}$$
$$\max(\text{Val Date}) = \text{2019-12-09} < \min(\text{Test Date}) = \text{2019-12-10}$$
There is zero date overlap between partitions.

---

## 8. Final Feature Matrix Contract

The ML pipeline outputs **29 predictive features** (26 numeric, 3 categorical).

- **Prohibited Column Assertion:** Verified by [`assert_no_leakage()`](../backend/ml/features/build_features.py). Prohibited fields (`clear_date`, `isOpen`, `delay_days`, `is_late`, `days_until_payment`, and raw database IDs) are strictly absent from `FEATURE_COLUMNS`.
- **Zero Unhandled Missingness:** All numeric features are validated to have zero `NaN` values across all partitions after imputation.

---

## 9. Generated Artifacts

Outputs saved in `data/processed/`:
1. `canonical_dataset.parquet` (2.5 MB, 48,833 rows, full canonical dataset)
2. `train.parquet` (1.4 MB, 27,403 rows, training partition)
3. `validation.parquet` (347 KB, 5,813 rows, validation partition)
4. `test.parquet` (353 KB, 5,936 rows, test partition)
5. `open_inference.parquet` (474 KB, 9,681 rows, open invoices for scoring)
6. `feature_metadata.json` (10.6 KB, schema, feature descriptions, imputation values)
7. `manifest.json` (2.5 KB, complete execution manifest, timestamps, row counts)

---

## 10. Execution Command

To regenerate all Phase 1 artifacts deterministically:
```bash
python -m backend.ml.features.build_features --input data/dataset.csv --output-dir data/processed
```
