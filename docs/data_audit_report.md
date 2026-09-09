# MSME Receivables Intelligence Platform — Data Audit Report

**Dataset:** Payment Date Dataset (Kaggle — Rajat Tomar)
**Inspected Path:** `data\dataset.csv`
**Execution Status:** Deterministic audit completed successfully

---

## 1. Dataset Overview

- **Total Rows:** 50,000
- **Total Columns:** 19
- **Expected Schema Valid:** `YES`
- **Date Range (Posting Date):** 2018-12-30 00:00:00 to 2020-05-22 00:00:00
- **Date Range (Due Date):** 2018-12-24 00:00:00 to 2020-07-10 00:00:00
- **Date Range (Clear/Payment Date):** 2019-01-03 00:00:00 to 2020-05-22 00:00:00

## 2. Column Inventory

| Column | Raw Dtype | Missing Count | Missing % | Unique Count | Operational Interpretation |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `business_code` | `object` | 0 | 0.0% | 6 | Company/entity code identifier (e.g. U001, CA02) |
| `cust_number` | `object` | 0 | 0.0% | 1,099 | Customer account identifier (categorical key) |
| `name_customer` | `object` | 0 | 0.0% | 4,197 | Customer business name |
| `clear_date` | `object` | 10,000 | 20.0% | 403 | Actual date payment was settled (Target outcome, null = unpaid) |
| `buisness_year` | `int64` | 0 | 0.0% | 2 | Fiscal year (2019, 2020) |
| `doc_id` | `int64` | 0 | 0.0% | 48,839 | Accounting document identifier |
| `posting_date` | `object` | 0 | 0.0% | 506 | Date invoice was posted to ledger (invoice reference date) |
| `document_create_date` | `int64` | 0 | 0.0% | 507 | Date invoice document was originally created in ERP |
| `document_create_date.1` | `int64` | 0 | 0.0% | 506 | ERP posting date in YYYYMMDD format |
| `due_in_date` | `int64` | 0 | 0.0% | 547 | Contractual invoice due date |
| `invoice_currency` | `object` | 0 | 0.0% | 2 | Billing currency (USD, CAD) |
| `document type` | `object` | 0 | 0.0% | 2 | Document type category (RV=Commercial invoice, X2=Credit/other) |
| `posting_id` | `int64` | 0 | 0.0% | 1 | Posting indicator constant (=1 across all rows) |
| `area_business` | `float64` | 50,000 | 100.0% | 0 | Business area code (100% missing in dataset) |
| `total_open_amount` | `float64` | 0 | 0.0% | 44,349 | Total billed invoice amount |
| `baseline_create_date` | `int64` | 0 | 0.0% | 506 | Baseline date from which payment terms begin |
| `cust_payment_terms` | `object` | 0 | 0.0% | 74 | Customer payment terms code (e.g. NAA8, NAH4) |
| `invoice_id` | `float64` | 6 | 0.01% | 48,833 | Unique invoice identifier (null in 6 credit/X2 records) |
| `isOpen` | `int64` | 0 | 0.0% | 2 | Invoice open status flag (1=Open/Unpaid, 0=Settled/Paid) |

## 3. Data Quality & Integrity

- **Exact Duplicate Rows:** 1,161 (2.32%)
- **Duplicate `doc_id`:** 1,161 (identical to exact duplicate row count)
- **Unique `doc_id`:** 48,839
- **Unique `invoice_id` (non-null):** 48,833
- **Missing `invoice_id` Rows:** 6 (all corresponding to `document type == 'X2'` non-standard documents)
- **`area_business` Missing:** 100.0% (unusable feature, must be discarded)
- **`posting_id` Variance:** Constant = 1 (zero variance feature, discard)
- **Date Parsing Failures:** 0 unparseable dates across all date fields
- **`due_in_date < posting_date`:** 140 records (backdated invoice entries where baseline date preceded posting)

## 4. Payment Status & Censored Data Analysis

- **Paid / Settled Invoices (`clear_date` populated):** 40,000 (80.0%)
- **Unpaid / Censored Invoices (`clear_date` null):** 10,000 (20.0%)
- **`isOpen` Flag Alignment:** `isOpen == 1` corresponds **100% exactly** to `clear_date.isnull() == True`

> [!IMPORTANT]
> **Operational Meaning of Null `clear_date`:**
> Null `clear_date` does NOT indicate corrupt or dirty data. It represents invoices that were **currently open / outstanding** at the time the dataset snapshot was exported.
> In the business domain, these 10,000 open invoices represent the primary operational use case for the platform: predicting when currently unpaid invoices will be settled.
> For **supervised model training**, these records are right-censored and lack ground-truth outcomes, so training and validation must use the 40,000 completed records. However, in the application inference pipeline, open invoices are the primary input to be scored.

## 5. Target Distributions (Closed / Paid Records)

### 5.1 Payment Delay Classification (`is_late = delay_days > 0`)

- **Total Settled Invoices Analyzed:** 40,000
- **On-Time Invoices (`delay_days <= 0`):** 23,236 (58.09%)
  - Paid strictly early (`delay_days < 0`): 14,786 (36.96%)
  - Paid on exact due date (`delay_days == 0`): 8,450 (21.12%)
- **Late Invoices (`delay_days > 0`):** 16,764 (41.91%)
- **Class Balance Assessment:** 58.1% on-time vs 41.9% late. The dataset is well-balanced for binary classification; no severe minority class undersampling or synthetic oversampling (e.g. SMOTE) is warranted.

### 5.2 Payment Delay Days Distribution (`clear_date - due_in_date`)

| Metric | Value (Days) |
| :--- | :---: |
| Mean | 0.8377 |
| Standard Deviation | 10.8317 |
| Minimum (earliest prepayment) | -89.0 |
| 1st Percentile | -19.0 |
| 5th Percentile | -7.0 |
| 25th Percentile | -3.0 |
| **Median (50th Percentile)** | **0.0** |
| 75th Percentile | 2.0 |
| 90th Percentile | 5.0 |
| 95th Percentile | 10.0 |
| 99th Percentile | 50.0 |
| Maximum (longest delay) | 204.0 |
| Skewness | 3.8277 |

### 5.3 Days Until Payment Distribution (`clear_date - posting_date`)

| Metric | Value (Days) |
| :--- | :---: |
| Mean | 18.0628 |
| Standard Deviation | 13.3593 |
| Minimum | 0.0 |
| 1st Percentile | 2.0 |
| 5th Percentile | 10.0 |
| 25th Percentile | 12.0 |
| **Median (50th Percentile)** | **15.0** |
| 75th Percentile | 17.0 |
| 90th Percentile | 27.0 |
| 95th Percentile | 42.0 |
| 99th Percentile | 76.0 |
| Maximum | 205.0 |
| Skewness | 4.1791 |

## 6. Customer History & Cold-Start Analysis

- **Total Unique Customers:** 1,099
- **Customers with Exactly 1 Invoice:** 301 (27.39%)
- **Customers with 2–5 Invoices:** 352 (32.03%)
- **Customers with 6–20 Invoices:** 227 (20.66%)
- **Customers with >20 Invoices:** 219 (19.93%)

### Customer Concentration

- Customer `200769623`: 12,268 invoices
- Customer `200726979`: 2,029 invoices
- Customer `200762301`: 1,631 invoices
- Customer `200759878`: 1,484 invoices
- Customer `200794332`: 1,255 invoices

### Cold Start in Open Invoices Set

- **Unique Customers in Open Set:** 553
- **Existing Customers (with prior history):** 510 (92.22%)
- **Cold-Start Customers (zero prior history):** 43 (7.78%)

## 7. Financial & Currency Distribution

- **Total Invoice Amount Range:** $0.72 to $668,593.36
- **Mean Invoice Amount:** $32,337.02 (std: $39,205.98)
- **Median Invoice Amount:** $17,609.01
- **Currencies:** {'USD': 46081, 'CAD': 3919}
- **Business Codes:** {'U001': 45359, 'CA02': 3917, 'U013': 573, 'U002': 135, 'U005': 11, 'U007': 5}
- **Document Types:** {'RV': 49994, 'X2': 6}
- **Unique Payment Terms:** 74

## 8. Data Leakage Observations & Constraints

1. **Direct Leakage Fields:**
   - `clear_date`: Ground truth outcome. Must be completely excluded from model inputs.
   - `isOpen`: Directly indicates whether `clear_date` is null. Must be excluded from model inputs.
   - Derived delay or payment duration: Must never enter the feature set.
2. **Document Creation Inconsistency:**
   - `document_create_date` precedes `posting_date` in many records; `document_create_date.1` is the actual ERP posting date. Canonical reference date for an invoice must be `posting_date`.
3. **Historical Aggregation Leakage:**
   - All customer-level historical metrics (e.g. `customer_avg_delay`, `customer_late_rate`) **must be calculated as-of the invoice posting date**, strictly utilizing events settled strictly prior to $T$.
4. **Temporal Train/Validation/Test Split:**
   - Evaluated models must never use random k-fold cross-validation across invoice rows. Evaluation must strictly follow chronological ordering.

## 9. Recommendations for Phase 1 (Ingestion & Feature Engineering)

1. **Deduplication:** Remove the 1,161 exact duplicate rows prior to modeling.
2. **Missing Value Handling:** Discard `area_business` (100% missing) and drop the 6 non-standard `X2` document type records.
3. **Multi-Currency Normalization:** Either convert CAD to USD at historical parity or include currency indicator feature; total amount should be log-transformed given right skew.
4. **As-Of Customer History Engine:** Build an efficient chronological cumulative calculation to compute customer behavioral metrics strictly as-of invoice posting date.
5. **Cold-Start Fallbacks:** Impute missing historical metrics with global medians and provide a `is_new_customer` indicator flag.
6. **Target Formulation:**
   - Classification: Binary `is_late = delay_days > 0` (41.9% late, well-calibrated).
   - Timing Regression: Predict `days_until_payment` or `delay_days` using gradient boosting with MAE loss.
