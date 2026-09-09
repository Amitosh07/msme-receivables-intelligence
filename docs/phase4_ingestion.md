# Phase 4 — Invoice & Payment Data Ingestion

**MSME Receivables Intelligence Platform — Version 1**  
**Completed:** 2026-09-10  

---

## 1. Overview & Architecture

Phase 4 establishes the application ingestion layer. It enables authenticated business tenants to securely upload invoice PDF documents and import historical payment records via CSV exports without triggering premature asynchronous workers, OCR, or ML inference.

```text
HTTP Client (Browser / API Client)
                │
                ▼
        FastAPI Ingestion Layer
  (backend/app/api/invoices.py & payments.py)
                │
    ┌───────────┴───────────┐
    ▼                       ▼
Invoice Upload API     Payment CSV Import
(POST /invoices/upload)  (POST /payments/import)
    │                       │
    ▼                       ▼
PDF Validation         CSV Normalization & Validation
(Magic Bytes, Size)    (Dates, Amounts, Duplicates)
    │                       │
    ▼                       ▼
Object Storage         Invoice Matching Engine
(LocalFileStorage)     (Match or Retain Unmatched)
    │                       │
    ▼                       ▼
InvoiceDocument        PostgreSQL (msme_receivables)
(Status: PENDING)      (invoices, payments, customers)
```

---

## 2. Invoice Upload Flow

1. **Request:** `POST /invoices/upload` with `multipart/form-data`.
2. **Authentication:** Enforced via `get_tenant_context` dependency using JWT Bearer token.
3. **Tenant Resolution:** The business tenant context is derived server-side from PostgreSQL `memberships`. Client-supplied tenant IDs are completely ignored.
4. **Validation:** PDF format, size, and header signature are verified before storage.
5. **Key Generation:** A safe tenant-scoped storage key is constructed (`tenants/{business_id}/invoices/{document_id}.pdf`).
6. **Storage:** The raw PDF is saved to disk via `StorageService`.
7. **Persistence:** An `InvoiceDocument` record is created with `processing_status="PENDING"` and `invoice_id=None`.
8. **Compensation Cleanup:** If database persistence fails, the saved PDF file is deleted to prevent orphan storage leakage.
9. **Response:** Returns `InvoiceUploadResponse` (document ID, filename, file size, status `PENDING`, upload timestamp).

---

## 3. PDF Validation

Uploaded documents are treated as untrusted binary input. Four levels of validation are enforced in `backend/app/services/invoice_service.py`:
1. **Non-Empty Check:** Rejects 0-byte uploads (HTTP 400).
2. **Size Limit:** Enforces `MAX_INVOICE_FILE_SIZE_MB` (default 10 MB). Rejects larger files with HTTP 413.
3. **Extension Check:** Requires `.pdf` extension (case-insensitive) (HTTP 400).
4. **Magic Bytes Signature:** Inspects the first 1024 bytes for the standard `%PDF` magic signature (HTTP 400).

---

## 4. Object Storage Abstraction

The storage architecture is decoupled from concrete local/cloud filesystems using `StorageService`:

```python
class StorageService(ABC):
    def save(self, content: bytes, key: str) -> str: ...
    def read(self, key: str) -> bytes: ...
    def open(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> bool: ...
    def exists(self, key: str) -> bool: ...
    def get_size(self, key: str) -> int: ...
```

### LocalFileStorage
For development, `LocalFileStorage` persists files under `STORAGE_ROOT` (default `./storage`).
- **Path Traversal Protection:** Every key is sanitized, backslashes are normalized, and the resolved absolute path is checked against the configured root directory. Keys attempting to escape (e.g. `../../etc/passwd`) raise `StorageSecurityError`.
- **Atomic Writes:** Files are written to a process-unique temporary file first and atomically renamed to prevent partial writes.

---

## 5. Storage Key Strategy

Files are organized strictly by tenant boundary:
```text
tenants/{business_id}/invoices/{document_id}.pdf
```
- Original client filenames are **never** used as filesystem paths.
- Filenames are stored only in database metadata (`InvoiceDocument.original_filename`).
- Direct filesystem paths are never returned in API responses.

---

## 6. Tenant Isolation

All ingestion endpoints strictly enforce tenant isolation:
- User A cannot retrieve or download documents belonging to User B (`GET /invoices/documents/{id}` returns 404).
- User A cannot see invoices or payment records belonging to User B.
- Storage directories are segregated per tenant ID.

---

## 7. Processing-Status Lifecycle

Following PRD requirements, operational payment state is kept strictly separate from asynchronous document processing state:

* **Document Processing Status:** `PENDING` $\rightarrow$ `PROCESSING` $\rightarrow$ `PROCESSED` $\rightarrow$ `ERROR`
* **Invoice Payment Status:** `OPEN` $\rightarrow$ `PAID` $\rightarrow$ `CANCELLED`

An uploaded invoice document starts in `PENDING` status upon upload. Phase 5 background workers will advance it to `PROCESSING` and `PROCESSED`.

---

## 8. Payment CSV Ingestion

Endpoint: `POST /payments/import` (multipart/form-data).

### Canonical CSV Columns
| Canonical Field | Recognized Header Synonyms | Type | Required | Description |
| :--- | :--- | :---: | :---: | :--- |
| `invoice_number` | `invoice_reference`, `doc_id`, `invoice_id` | String | **Yes** | Referenced invoice identifier |
| `payment_date` | `clear_date`, `date` | Date | **Yes** | Settlement / clearance date |
| `amount` | `payment_amount`, `total_open_amount` | Decimal | **Yes** | Settled payment amount (> 0) |
| `reference` | `payment_reference`, `ref`, `utr` | String | No | Bank / UTR remittance reference |
| `customer_reference`| `cust_number`, `customer_id` | String | No | Buyer account identifier |

### Supported Date Formats
Supports `YYYY-MM-DD`, `YYYY-MM-DD HH:MM:SS`, `DD-MM-YYYY`, `DD/MM/YYYY`, `YYYY/MM/DD`, `MM/DD/YYYY`, and ISO-8601.

---

## 9. Duplicate Prevention & Invoice Matching

### Duplicate Policy
A payment remittance is identified by the tuple:
$$\text{Natural Key} = (\text{business\_id}, \text{invoice\_reference}, \text{payment\_date}, \text{amount})$$
If an identical row was already processed in the current batch or already exists in the database for this tenant, it is counted under `duplicates` and skipped.

### Invoice Matching Policy
1. **Matched:** If an `Invoice` exists for the business with matching `invoice_number`, `payment.invoice_id` is linked to `invoice.id`, and `invoice.payment_status` is updated to `PAID`. Counted under `imported`.
2. **Unmatched:** If no matching invoice exists yet (e.g. payment history was uploaded prior to invoices), the valid payment is **retained** with `invoice_id = None` and `invoice_reference = row_invoice_number`. It is counted under `unmatched`. No fake invoice records are created.

### Customer Creation Policy
If `customer_reference` is provided and no matching customer exists for the tenant, a `Customer` record is deterministically created (`name="Customer <ref>"`).

---

## 10. Ingestion Summary & Error Reporting

The payment import returns an exact summary with structured row-level errors:
```json
{
  "total_rows": 100,
  "imported": 85,
  "duplicates": 5,
  "rejected": 2,
  "unmatched": 8,
  "errors": [
    {
      "row_number": 12,
      "reason": "Invalid payment date format: 'invalid-date'",
      "raw_data": {"invoice_number": "INV-12", "payment_date": "invalid-date", "amount": "500.00"}
    }
  ]
}
```

---

## 11. API Endpoints Summary

| Method | Path | Summary | Auth Required | Status |
| :--- | :--- | :--- | :---: | :---: |
| `POST` | `/invoices/upload` | Upload invoice PDF document | Yes | 201 |
| `GET` | `/invoices/documents` | List uploaded invoice documents | Yes | 200 |
| `GET` | `/invoices/documents/{id}` | Download / stream invoice PDF | Yes | 200 |
| `GET` | `/invoices` | List invoices for tenant (paginated) | Yes | 200 |
| `GET` | `/invoices/{id}` | Get invoice details | Yes | 200 |
| `POST` | `/payments/import` | Batch import payment history CSV | Yes | 200 |

---

## 12. Testing & Verification

Unit and integration tests are organized under `backend/tests/`:
- `test_storage.py` (8 tests): File save, read, stream, delete, existence, path traversal rejection (`..`, backslash, empty keys).
- `test_invoice_ingestion.py` (8 tests): Valid PDF upload, PENDING state, non-PDF rejection, missing header signature rejection, empty file rejection, oversized file rejection (413), document listing and download, cross-tenant isolation.
- `test_payment_ingestion.py` (6 tests): Valid import with matching, missing required headers rejection (400), invalid rows error reporting, duplicate detection, unmatched payment retention, non-CSV rejection (400), tenant isolation.

### Full Regression Suite
```bash
python -m unittest discover -s backend/tests -v
```
**78 passed, 0 failures, 0 errors** across Phases 0, 1, 2, 3, and 4.
