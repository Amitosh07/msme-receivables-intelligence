# Data Dictionary — Raw Dataset & Field Semantics

This document defines the semantics, data types, nullability, relevance for Version 1, data leakage risks, and canonical application mappings for every column in the initial Kaggle development dataset (`data/dataset.csv`).

---

## 1. Raw Column Inventory & Semantics

### `business_code`
- **Source Column:** `business_code`
- **Meaning:** Identifier for the billing corporate entity or operating subsidiary (e.g. `U001` for US subsidiary 1, `CA02` for Canadian subsidiary 2).
- **Expected Data Type:** `str` / `VARCHAR(16)`
- **Can Be Null?:** No (0 nulls in 50,000 records)
- **Relevant for V1?:** Yes. Represents company / business subdivision. Used as a high-cardinality categorical feature in ML and maps to `tenant_business_code`.
- **Potential Leakage Risk?:** No. Available at invoice creation time.
- **Canonical Field Mapping:** `invoices.business_code`

### `cust_number`
- **Source Column:** `cust_number`
- **Meaning:** Unique customer/buyer account identifier within the ERP system (e.g., `200769623`).
- **Expected Data Type:** `str` / `VARCHAR(64)`
- **Can Be Null?:** No (0 nulls in 50,000 records)
- **Relevant for V1?:** Yes. Core entity identifier used for computing customer-level historical behavior and grouping receivables.
- **Potential Leakage Risk?:** No (the identifier itself is safe). Note: Any historical aggregates computed on this customer key must follow strict as-of temporal ordering.
- **Canonical Field Mapping:** `customers.customer_ref` / `invoices.customer_id`

### `name_customer`
- **Source Column:** `name_customer`
- **Meaning:** Business name of the customer organization (e.g., `WAL-MAR corp`, `SYSC llc`).
- **Expected Data Type:** `str` / `VARCHAR(255)`
- **Can Be Null?:** No (0 nulls in 50,000 records)
- **Relevant for V1?:** Yes. Displayed in UI dashboards and customer summaries.
- **Potential Leakage Risk?:** No. Available at invoice issuance.
- **Canonical Field Mapping:** `customers.name`

### `clear_date`
- **Source Column:** `clear_date`
- **Meaning:** Timestamp when payment was settled and cleared through the bank ledger.
- **Expected Data Type:** `TIMESTAMP` / `DATETIME` (stored raw as `MM/DD/YYYY HH:MM`)
- **Can Be Null?:** Yes (10,000 nulls, 20.0% of dataset). Null values represent currently open/unpaid invoices (`isOpen == 1`).
- **Relevant for V1?:** Yes, as the **ground truth target outcome** for historical training. Populates `payments.payment_date`.
- **Potential Leakage Risk?:** **CRITICAL LEAKAGE RISK.** Must NEVER be used as an input feature for prediction. Must be strictly isolated to target derivation.
- **Canonical Field Mapping:** `payments.payment_date` (Target outcome only)

### `buisness_year`
- **Source Column:** `buisness_year`
- **Meaning:** Fiscal accounting year of the invoice (e.g., `2019`, `2020`).
- **Expected Data Type:** `int` / `SMALLINT`
- **Can Be Null?:** No
- **Relevant for V1?:** Low. Redundant with calendar year extracted from `posting_date`.
- **Potential Leakage Risk?:** Low.
- **Canonical Field Mapping:** Extracted dynamically from `invoices.invoice_date`.

### `doc_id`
- **Source Column:** `doc_id`
- **Meaning:** ERP accounting document number identifying the subledger transaction.
- **Expected Data Type:** `int64` / `VARCHAR(64)`
- **Can Be Null?:** No (0 nulls)
- **Relevant for V1?:** Yes. Secondary system reference identifier.
- **Potential Leakage Risk?:** No.
- **Canonical Field Mapping:** `invoices.document_ref`

### `posting_date`
- **Source Column:** `posting_date`
- **Meaning:** The official accounting date when the invoice was posted to the general ledger and sent to the customer.
- **Expected Data Type:** `DATE` (stored raw as `MM/DD/YYYY`)
- **Can Be Null?:** No (0 nulls)
- **Relevant for V1?:** Yes. The primary chronological reference point ($T_0$) for the invoice. Used for computing payment terms, tenure, and as-of historical feature boundaries.
- **Potential Leakage Risk?:** No. Available at invoice creation.
- **Canonical Field Mapping:** `invoices.invoice_date` / `invoices.posting_date`

### `document_create_date`
- **Source Column:** `document_create_date`
- **Meaning:** The timestamp when the draft document was initially initiated in the ERP system.
- **Expected Data Type:** `DATE` (stored raw as integer `YYYYMMDD`)
- **Can Be Null?:** No
- **Relevant for V1?:** Low. In ERPs, draft creation can precede posting by 1–2 days. `posting_date` is the authoritative legal invoice date.
- **Potential Leakage Risk?:** Low.
- **Canonical Field Mapping:** Discarded or mapped to `invoices.metadata->>'draft_create_date'`.

### `document_create_date.1`
- **Source Column:** `document_create_date.1`
- **Meaning:** Duplicate ERP posting date field stored in `YYYYMMDD` format. Identical to `posting_date` in 49,994 out of 50,000 records.
- **Expected Data Type:** `DATE` (stored raw as integer `YYYYMMDD`)
- **Can Be Null?:** No
- **Relevant for V1?:** No (redundant duplicate of `posting_date`).
- **Potential Leakage Risk?:** No.
- **Canonical Field Mapping:** Discarded during ingestion.

### `due_in_date`
- **Source Column:** `due_in_date`
- **Meaning:** Contractual payment deadline as agreed in payment terms.
- **Expected Data Type:** `DATE` (stored raw as integer `YYYYMMDD`)
- **Can Be Null?:** No (0 nulls)
- **Relevant for V1?:** Yes. Crucial for computing credit terms (`due_date - posting_date`) and defining payment delay (`clear_date > due_date`).
- **Potential Leakage Risk?:** No. Contractually fixed at invoice generation.
- **Canonical Field Mapping:** `invoices.due_date`

### `invoice_currency`
- **Source Column:** `invoice_currency`
- **Meaning:** Currency unit in which the invoice is billed (`USD` or `CAD`).
- **Expected Data Type:** `str` / `VARCHAR(3)`
- **Can Be Null?:** No
- **Relevant for V1?:** Yes. Multi-currency handling is required for accurate receivables reporting and feature standardization.
- **Potential Leakage Risk?:** No.
- **Canonical Field Mapping:** `invoices.currency`

### `document type`
- **Source Column:** `document type`
- **Meaning:** Subledger document classification (`RV` = standard commercial customer billing; `X2` = credit memo / internal adjustment).
- **Expected Data Type:** `str` / `VARCHAR(4)`
- **Can Be Null?:** No
- **Relevant for V1?:** Filtering only. V1 focuses on standard B2B trade receivables (`RV`). The 6 records with `X2` should be excluded from model training.
- **Potential Leakage Risk?:** No.
- **Canonical Field Mapping:** `invoices.document_type`

### `posting_id`
- **Source Column:** `posting_id`
- **Meaning:** SAP posting status flag. Holds the constant value `1` across all 50,000 records.
- **Expected Data Type:** `int`
- **Can Be Null?:** No
- **Relevant for V1?:** No (zero variance feature).
- **Potential Leakage Risk?:** No.
- **Canonical Field Mapping:** Discarded.

### `area_business`
- **Source Column:** `area_business`
- **Meaning:** Operating business area code. 100.0% missing (all 50,000 values are `NaN`).
- **Expected Data Type:** `float`
- **Can Be Null?:** Yes (100% null)
- **Relevant for V1?:** No (completely empty).
- **Potential Leakage Risk?:** No.
- **Canonical Field Mapping:** Discarded.

### `total_open_amount`
- **Source Column:** `total_open_amount`
- **Meaning:** Total billed invoice monetary balance.
- **Expected Data Type:** `float` / `DECIMAL(14,2)`
- **Can Be Null?:** No (range: \$0.72 to \$668,593.36)
- **Relevant for V1?:** Yes. Fundamental for receivables tracking, exposure weighting, risk prioritization, and prediction.
- **Potential Leakage Risk?:** No. Known at invoice issuance.
- **Canonical Field Mapping:** `invoices.amount`

### `baseline_create_date`
- **Source Column:** `baseline_create_date`
- **Meaning:** Date from which payment terms begin calculating (e.g., date of goods delivery or bill of lading).
- **Expected Data Type:** `DATE` (stored raw as integer `YYYYMMDD`)
- **Can Be Null?:** No
- **Relevant for V1?:** Yes. The difference `(due_in_date - baseline_create_date)` represents the agreed credit term duration in days (typically 15, 30, 45 days).
- **Potential Leakage Risk?:** No.
- **Canonical Field Mapping:** `invoices.metadata->>'baseline_date'`

### `cust_payment_terms`
- **Source Column:** `cust_payment_terms`
- **Meaning:** ERP payment terms shorthand code (e.g. `NAA8` = Net 15, `CA10` = Net 10 days, `NAD1` = Net 20 days).
- **Expected Data Type:** `str` / `VARCHAR(16)`
- **Can Be Null?:** No (74 unique codes)
- **Relevant for V1?:** Yes. Encodes credit agreement structure. Can be represented as categorical or decomposed into numeric term days.
- **Potential Leakage Risk?:** No. Fixed at invoice creation.
- **Canonical Field Mapping:** `invoices.payment_terms`

### `invoice_id`
- **Source Column:** `invoice_id`
- **Meaning:** Unique commercial invoice reference identifier.
- **Expected Data Type:** `int64` / `VARCHAR(64)`
- **Can Be Null?:** Yes (6 null records, perfectly coincident with `document type == 'X2'`).
- **Relevant for V1?:** Yes. Primary invoice identifier.
- **Potential Leakage Risk?:** No.
- **Canonical Field Mapping:** `invoices.invoice_number`

### `isOpen`
- **Source Column:** `isOpen`
- **Meaning:** Operational status flag indicating whether the invoice is currently unpaid/open (`1`) or closed/settled (`0`).
- **Expected Data Type:** `int` / `BOOLEAN`
- **Can Be Null?:** No
- **Relevant for V1?:** Used to partition dataset into training/validation set (`isOpen == 0`, 40,000 records) and inference test set (`isOpen == 1`, 10,000 records).
- **Potential Leakage Risk?:** **DIRECT TARGET LEAKAGE.** Perfect proxy for whether `clear_date` is null. Must NEVER be provided as an input feature during model training.
- **Canonical Field Mapping:** `invoices.status` (`'OPEN'` vs `'PAID'`)

---

## 2. Target Variables (Derived for Training Only)

| Derived Target | Formula | Meaning | Data Type | Usage |
| :--- | :--- | :--- | :--- | :--- |
| `is_late` | `1 if (clear_date - due_in_date).days > 0 else 0` | Binary classification target: paid past contractual due date | `int` (0 or 1) | Payment delay classifier & Risk Score |
| `delay_days` | `(clear_date - due_in_date).days` | Difference in calendar days between settlement and due date | `int` | Delay regression / bucket analysis |
| `days_until_payment` | `(clear_date - posting_date).days` | Total duration from invoice issuance to settlement | `int` | Payment timing regression |
