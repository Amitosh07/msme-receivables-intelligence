# Feature Dictionary — Version 1 Model Features

This document provides the definitive specification for all **29 machine learning features** produced by the Phase 1 feature pipeline in `data/processed/`.

---

## 1. Feature Overview Summary

- **Total Features:** 29
  - **Invoice-Intrinsic Numeric Features:** 12
  - **Customer Historical As-Of Features:** 14
  - **Categorical Features:** 3
- **Classification Target:** `is_late`
- **Regression Target:** `days_until_payment`
- **Secondary Delay Target:** `delay_days`

---

## 2. Comprehensive Feature Inventory

| # | Feature Name | Data Type | Family | Source Fields | Transformation / Derivation | As-Of Horizon Rule | Missing-Value Imputation Strategy | Allowed Model Use |
| :-: | :--- | :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | `amount` | `float64` | Invoice Financial | `total_open_amount` | None (raw monetary value) | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **2** | `log_amount` | `float64` | Invoice Financial | `total_open_amount` | $\log(1 + \text{amount})$ | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **3** | `term_duration_days` | `int64` | Credit Terms | `due_in_date`, `baseline_create_date` | $(\text{due\_date} - \text{baseline\_date}).\text{days}$ | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **4** | `days_until_due_at_posting` | `int64` | Credit Terms | `due_in_date`, `posting_date` | $(\text{due\_date} - \text{invoice\_date}).\text{days}$ | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **5** | `posting_month` | `int64` | Calendar | `posting_date` | $\text{Month}(\text{invoice\_date}) \in [1, 12]$ | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **6** | `posting_day_of_month` | `int64` | Calendar | `posting_date` | $\text{Day}(\text{invoice\_date}) \in [1, 31]$ | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **7** | `posting_day_of_week` | `int64` | Calendar | `posting_date` | $\text{DayOfWeek}(\text{invoice\_date}) \in [0, 6]$ | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **8** | `posting_quarter` | `int64` | Calendar | `posting_date` | $\text{Quarter}(\text{invoice\_date}) \in [1, 4]$ | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **9** | `is_weekend_posting` | `int64` | Calendar | `posting_date` | $\mathbb{I}(\text{posting\_day\_of\_week} \ge 5)$ | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **10** | `due_month` | `int64` | Calendar | `due_in_date` | $\text{Month}(\text{due\_date}) \in [1, 12]$ | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **11** | `due_day_of_week` | `int64` | Calendar | `due_in_date` | $\text{DayOfWeek}(\text{due\_date}) \in [0, 6]$ | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **12** | `is_weekend_due` | `int64` | Calendar | `due_in_date` | $\mathbb{I}(\text{due\_day\_of\_week} \ge 5)$ | Available at posting $T$ | Directly observed (no nulls) | Classifier & Regressor |
| **13** | `cust_prior_invoice_count` | `int64` | Customer History | `cust_number`, `posting_date` | Count of invoices posted strictly prior to $T$ | Strictly $< T$ | Zero default | Classifier & Regressor |
| **14** | `cust_prior_payment_count` | `int64` | Customer History | `cust_number`, `clear_date` | Count of payments cleared strictly prior to $T$ | Strictly $< T$ | Zero default | Classifier & Regressor |
| **15** | `cust_prior_late_count` | `int64` | Customer History | `cust_number`, `clear_date`, `is_late` | Count of late payments cleared prior to $T$ | Strictly $< T$ | Zero default | Classifier & Regressor |
| **16** | `cust_late_payment_rate` | `float64` | Customer History | Derived | $\frac{\text{prior\_late\_count}}{\text{prior\_payment\_count}}$ | Strictly $< T$ | Train median (`0.329609`) | Classifier & Regressor |
| **17** | `cust_avg_delay` | `float64` | Customer History | `cust_number`, `delay_days` | Mean of payment delays cleared prior to $T$ | Strictly $< T$ | Train median (`0.170732`) | Classifier & Regressor |
| **18** | `cust_median_delay` | `float64` | Customer History | `cust_number`, `delay_days` | Median of payment delays cleared prior to $T$ | Strictly $< T$ | Train median (`0.000000`) | Classifier & Regressor |
| **19** | `cust_std_delay` | `float64` | Customer History | `cust_number`, `delay_days` | Sample std of payment delays cleared prior to $T$ | Strictly $< T$ | Train median (`4.036694`) | Classifier & Regressor |
| **20** | `cust_max_delay` | `float64` | Customer History | `cust_number`, `delay_days` | Maximum payment delay cleared prior to $T$ | Strictly $< T$ | Train median (`15.000000`) | Classifier & Regressor |
| **21** | `cust_min_delay` | `float64` | Customer History | `cust_number`, `delay_days` | Minimum payment delay cleared prior to $T$ | Strictly $< T$ | Train median (`-7.000000`) | Classifier & Regressor |
| **22** | `cust_recent_avg_delay_3` | `float64` | Customer Recent | `cust_number`, `delay_days` | Mean delay across last $\le 3$ payments cleared before $T$ | Strictly $< T$ | Train median (`0.000000`) | Classifier & Regressor |
| **23** | `cust_recent_late_rate_3` | `float64` | Customer Recent | `cust_number`, `is_late` | Late rate across last $\le 3$ payments cleared before $T$ | Strictly $< T$ | Train median (`0.333333`) | Classifier & Regressor |
| **24** | `days_since_last_payment` | `float64` | Recency | `cust_number`, `clear_date` | $(T - \text{last\_clear\_date}).\text{days}$ | Strictly $< T$ | Train median (`2.000000`) | Classifier & Regressor |
| **25** | `days_since_prev_invoice` | `float64` | Recency | `cust_number`, `posting_date` | $(T - \text{last\_invoice\_date}).\text{days}$ | Strictly $< T$ | Train median (`1.000000`) | Classifier & Regressor |
| **26** | `is_new_customer` | `int64` | Cold Start | Derived | $\mathbb{I}(\text{cust\_prior\_invoice\_count} < 3)$ | Strictly $< T$ | Binary indicator (no nulls) | Classifier & Regressor |
| **27** | `business_code` | `category` / `str` | Tenant Context | `business_code` | Categorical corporate entity code (`U001`, `CA02`, etc.) | Available at posting $T$ | Native categorical | Classifier & Regressor |
| **28** | `currency` | `category` / `str` | Financial | `invoice_currency` | Categorical billing currency (`USD`, `CAD`) | Available at posting $T$ | Native categorical | Classifier & Regressor |
| **29** | `payment_terms` | `category` / `str` | Commercial Terms | `cust_payment_terms` | ERP terms code (`NAA8`, `NAH4`, `CA10`, etc.) | Available at posting $T$ | Native categorical | Classifier & Regressor |

---

## 3. Target and Metadata Columns (Excluded from $X$)

### Target Columns (Supervised labels only)
- `is_late`: Binary classification label ($1 = \text{Late}, 0 = \text{On Time}$).
- `delay_days`: Integer payment delay in days $(\text{clear\_date} - \text{due\_date})$.
- `days_until_payment`: Integer payment timing duration $(\text{clear\_date} - \text{invoice\_date})$.

### Metadata & Identification Columns
- `invoice_id`: External document number (`doc_id`).
- `business_code`: Operating entity identifier.
- `customer_id`: Unique customer ERP number (`cust_number`).
- `customer_name`: Clean customer trading name.
- `invoice_date`: Canonical posting timestamp.
- `due_date`: Contractual due timestamp.
- `baseline_date`: ERP terms baseline timestamp.
- `clear_date`: Payment settlement timestamp (null for open records).
- `status`: Operational state (`'PAID'` vs `'OPEN'`).
- `split`: Partition assignment (`'train'`, `'validation'`, `'test'`, `'open_inference'`).
