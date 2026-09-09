# Phase 5 Engineering Handoff: Asynchronous Processing & Worker Infrastructure

**MSME Receivables Intelligence Platform — Version 1**  
**Authoritative Handoff Document**  

---

## 1. Context & Purpose

Phase 4 successfully created the synchronous ingestion surface for invoice PDFs and historical payment CSVs. Phase 5 will introduce the **asynchronous worker infrastructure** using Redis and background job routers to parse uploaded invoice PDFs and update application state.

---

## 2. What Phase 5 Receives from Phase 4

### A. Document Storage & Metadata
* **`InvoiceDocument` Database Records:**
  - Located in table `invoice_documents`.
  - Initialized with `processing_status = 'PENDING'`.
  - Contains `storage_key` formatted as `tenants/{business_id}/invoices/{document_id}.pdf`.
  - Links to `business_id` for strict multi-tenant scoping.
  - `invoice_id` is initially `None` until parsed.
* **`StorageService` Abstraction:**
  - Ready to open and read stored invoice PDFs:
    ```python
    from backend.app.services.storage import get_storage
    storage = get_storage()
    pdf_bytes = storage.read(doc.storage_key)
    # or streaming
    with storage.open(doc.storage_key) as pdf_stream:
        ...
    ```

### B. Core Application Persistence
* **`Task` Database Model:**
  - Exists in table `tasks` from Phase 3.
  - Fields: `id`, `business_id`, `task_type` (`parse_invoice`), `status` (`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`), `payload` (JSON), `error_message`, `started_at`, `completed_at`.
* **`Invoice` Database Model:**
  - Exists in table `invoices`.
  - Phase 5 parser will create or populate `Invoice` records (`invoice_number`, `customer_id`, `invoice_date`, `due_date`, `amount`, `currency`, `payment_terms`, `payment_status='OPEN'`, `processing_status='PROCESSED'`).
* **Persisted Historical Payments:**
  - Exists in table `payments`.
  - Ingested payments maintain `invoice_reference` and `payment_date`.
  - When an invoice is created/parsed with a matching `invoice_number`, Phase 5 can link `payment.invoice_id = invoice.id` and advance invoice `payment_status = 'PAID'`.

---

## 3. Recommended Phase 5 Architecture

```text
FastAPI Ingestion Endpoint (POST /invoices/upload)
               │
               ├─► Store PDF in StorageService
               ├─► Create InvoiceDocument (PENDING)
               ├─► Create Task record in PostgreSQL (PENDING)
               └─► Enqueue task payload to Redis list/queue
                        │
                        ▼
                Redis Task Queue
                        │
                        ▼
            Background Worker Service
             (backend/app/workers/)
                        │
                        ├─► Update Task status: PROCESSING
                        ├─► Update InvoiceDocument status: PROCESSING
                        ├─► Retrieve PDF via StorageService
                        ├─► Execute PDF Extraction / Parsing
                        ├─► Create/Update Invoice entity in PostgreSQL
                        ├─► Link any existing Payments for this invoice
                        ├─► Update InvoiceDocument status: PROCESSED
                        └─► Update Task status: COMPLETED
```

---

## 4. Key Constraints for Phase 5

1. **Tenant Isolation:** The worker must respect tenant ownership (`business_id`) when parsing and updating records.
2. **Error Handling:** Failed jobs must record `status = 'FAILED'`, store `error_message` on both `Task` and `InvoiceDocument`, and log server-side diagnostics without dropping work silently.
3. **No ML Inference Yet:** Prediction scoring is handled in Phase 6. Phase 5 focuses exclusively on Redis queueing, worker process orchestration, and invoice parsing.
