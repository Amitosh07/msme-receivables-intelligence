# Canonical Application Data Contract

This document defines the canonical domain data contract for the **MSME Receivables Intelligence Platform**. It acts as the immutable contract separating raw external data sources (such as ERP CSV exports, invoice PDFs, or accounting integrations) from the internal application services, PostgreSQL database schema, and machine learning pipelines.

---

## 1. Domain Entities & Canonical Schemas

```text
┌──────────────┐         1 : N         ┌──────────────┐
│   Business   │ ─────────────────────►│   Customer   │
│   (Tenant)   │                       └──────┬───────┘
└──────┬───────┘                              │ 1 : N
       │ 1 : N                                ▼
       │                               ┌──────────────┐
       └──────────────────────────────►│   Invoice    │
                                       └──────┬───────┘
                                              │ 1 : N
                                              ▼
                                       ┌──────────────┐
                                       │   Payment    │
                                       └──────────────┘
```

---

### 1.1 Tenant / Business Entity (`businesses`)

Represents the authenticated MSME organization operating the system.

| Field | Canonical Type | Nullable | Description | Source Dataset Mapping |
| :--- | :--- | :---: | :--- | :--- |
| `id` | `UUID` | No | Internal primary key | System-generated |
| `business_code` | `VARCHAR(32)` | No | Tenant business / operating division code | `business_code` (e.g. `U001`, `CA02`) |
| `name` | `VARCHAR(255)` | No | Legal business name | Tenant setup name |
| `currency` | `VARCHAR(3)` | No | Tenant functional currency (ISO 4217, e.g. `USD`, `INR`) | Default `USD` |
| `created_at` | `TIMESTAMPTZ` | No | Timestamp of tenant onboarding | System-generated |

---

### 1.2 Customer Entity (`customers`)

Represents a B2B buyer or client owing receivables to a business tenant.

| Field | Canonical Type | Nullable | Description | Source Dataset Mapping |
| :--- | :--- | :---: | :--- | :--- |
| `id` | `UUID` | No | Internal primary key | System-generated |
| `business_id` | `UUID` | No | Multi-tenant foreign key (`businesses.id`) | Tenant context |
| `customer_ref` | `VARCHAR(64)` | No | External customer account identifier from ERP/accounting | `cust_number` (e.g. `200769623`) |
| `name` | `VARCHAR(255)` | No | Customer business name | `name_customer` |
| `created_at` | `TIMESTAMPTZ` | No | Creation timestamp | Derived from first invoice posting date |
| `updated_at` | `TIMESTAMPTZ` | No | Last update timestamp | System-generated |

---

### 1.3 Invoice Entity (`invoices`)

Represents a billed commercial invoice document issued to a customer.

| Field | Canonical Type | Nullable | Description | Source Dataset Mapping |
| :--- | :--- | :---: | :--- | :--- |
| `id` | `UUID` | No | Internal primary key | System-generated |
| `business_id` | `UUID` | No | Multi-tenant foreign key (`businesses.id`) | Tenant context |
| `customer_id` | `UUID` | No | Customer foreign key (`customers.id`) | Foreign key to resolved `customers` record |
| `invoice_number` | `VARCHAR(64)` | No | External invoice reference number | `doc_id` / `invoice_id` |
| `invoice_date` | `DATE` | No | Date invoice was officially posted/issued | `posting_date` |
| `due_date` | `DATE` | No | Contractual payment due date | `due_in_date` |
| `amount` | `NUMERIC(14,2)`| No | Total invoice monetary value | `total_open_amount` |
| `currency` | `VARCHAR(3)` | No | Billing currency code (`USD`, `CAD`, `INR`) | `invoice_currency` |
| `payment_terms`| `VARCHAR(32)` | Yes | Commercial terms code (e.g. `Net 15`, `NAA8`) | `cust_payment_terms` |
| `status` | `VARCHAR(16)` | No | Operational status (`OPEN`, `PAID`, `CANCELLED`) | `isOpen == 1 ? 'OPEN' : 'PAID'` |
| `created_at` | `TIMESTAMPTZ` | No | Record creation timestamp | System-generated |

---

### 1.4 Payment Entity (`payments`)

Represents a completed cash remittance clearing an invoice balance.

| Field | Canonical Type | Nullable | Description | Source Dataset Mapping |
| :--- | :--- | :---: | :--- | :--- |
| `id` | `UUID` | No | Internal primary key | System-generated |
| `business_id` | `UUID` | No | Multi-tenant foreign key (`businesses.id`) | Tenant context |
| `invoice_id` | `UUID` | No | Invoice foreign key (`invoices.id`) | Foreign key to corresponding `invoices` record |
| `payment_date` | `TIMESTAMPTZ` | No | Settlement date when funds cleared the account | `clear_date` |
| `payment_amount`| `NUMERIC(14,2)`| No | Amount cleared by the remittance | `total_open_amount` (for fully settled invoices) |
| `reference` | `VARCHAR(128)`| Yes | Payment reference / UTR / transaction ID | Optional ERP remittance reference |
| `created_at` | `TIMESTAMPTZ` | No | Record creation timestamp | System-generated |

---

### 1.5 Prediction Entity (`predictions`)

Represents machine learning predictions produced for an invoice.

| Field | Canonical Type | Nullable | Description | Source |
| :--- | :--- | :---: | :--- | :--- |
| `id` | `UUID` | No | Internal primary key | System-generated |
| `business_id` | `UUID` | No | Multi-tenant foreign key (`businesses.id`) | Tenant context |
| `invoice_id` | `UUID` | No | Target invoice (`invoices.id`) | Linked invoice |
| `model_version`| `VARCHAR(32)` | No | Version tag of the model producing inference | e.g. `v1-xgb-delay` |
| `is_late_pred` | `BOOLEAN` | No | Predicted delay status (`True` = Late, `False` = On Time) | Classifier binary prediction |
| `risk_score` | `NUMERIC(5,4)` | No | Late-payment probability $[0.0000, 1.0000]$ | Classifier probability $P(\text{Late})$ |
| `expected_days`| `INTEGER` | Yes | Predicted days until payment from invoice date | Regression prediction |
| `expected_payment_date` | `DATE` | Yes | Calculated calendar date: `invoice_date + expected_days` | Derived |
| `predicted_at` | `TIMESTAMPTZ` | No | Timestamp when inference was executed | System-generated |

---

## 2. Ingestion Validation Rules

1. **Uniqueness:**
   `business_id + invoice_number` must be unique. Duplicate invoice rows within the same business tenant must be rejected or deduplicated upon ingestion.
2. **Date Consistency:**
   `invoice_date` must not be in the distant future ($> \text{now} + 30\text{ days}$).
   `due_date` should normally be $\ge \text{invoice_date}$, but backdated entries are tolerated and flagged with a duration indicator.
3. **Monetary Amounts:**
   `amount` must be strictly positive ($> 0.00$). Zero or negative balances are handled as credits or adjustments, not standard receivables.
4. **Currency Standardization:**
   All monetary amounts must specify a valid 3-letter ISO currency code.
5. **Censored Payment Dates:**
   If an invoice has `status == 'OPEN'`, it MUST have zero associated `payments` records. An invoice is marked `'PAID'` only when a valid `payment` event with non-null `payment_date` is ingested.
