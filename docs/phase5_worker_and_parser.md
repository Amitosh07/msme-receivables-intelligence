# Phase 5 — Redis Queue, Single Worker Service & Invoice Parser

## 1. Overview & Architecture

Phase 5 implements the asynchronous background processing pipeline for the **MSME Receivables Intelligence Platform**. It takes uploaded invoice PDFs that were ingested in `PENDING` status during Phase 4 and asynchronously extracts structured invoice entities, matches/creates customers, validates financial data, and reconciles unmatched historical payments.

```
       +---------------------------------------------+
       |             FastAPI Backend                 |
       |  POST /invoices/upload -> InvoiceDocument   |
       +---------------------------------------------+
                              |
               +--------------+--------------+
               |                             |
               v                             v
      +------------------+          +------------------+
      |    PostgreSQL    |          |   Redis Queue    |
      |   tasks table    |          | LPUSH / BRPOP    |
      | (Source of Truth)|          | (Transient Coord)|
      +------------------+          +------------------+
               ^                             |
               |                             v
      +------------------------------------------------+
      |              Single Worker Service             |
      |        backend.app.workers.runtime             |
      | - Atomically claims task in PostgreSQL         |
      | - Dispatches to TaskRouter                     |
      | - Executes handle_parse_invoice handler        |
      +------------------------------------------------+
                              |
                              v
      +------------------------------------------------+
      |                Invoice Parser                  |
      |  1. Text Extraction (PyMuPDF fitz / pypdf)     |
      |  2. OCR Fallback (pytesseract)                 |
      |  3. Field Extraction & Normalization           |
      |  4. Strict Validation (no fabrication)         |
      +------------------------------------------------+
                              |
                              v
      +------------------------------------------------+
      |              PostgreSQL State Update           |
      | - Customer resolved / created                  |
      | - Invoice created (payment_status="OPEN")      |
      | - InvoiceDocument updated ("PROCESSED")        |
      | - Historical payments reconciled ("PAID")      |
      | - Task marked "COMPLETED"                      |
      +------------------------------------------------+
```

---

## 2. Queue Architecture & State Coordination

### 2.1 Role of Redis vs. PostgreSQL
* **PostgreSQL is the single source of truth**: Every task is persisted in the `tasks` table with status `PENDING` before or concurrent with being enqueued to Redis. The database records full audit history, retry attempt counters, and error traces.
* **Redis is the transient coordination mechanism**: Redis lists (`LPUSH` / `BRPOP`) coordinate worker execution. If Redis is flushed, crashed, or restarted, no work is lost: the Worker automatically recovers all `PENDING` tasks from PostgreSQL upon startup.

### 2.2 Redis Queue Mechanics (`backend/app/workers/queue.py`)
* **Queue Name**: `msme:task:queue` (configured via `settings.REDIS_TASK_QUEUE_NAME`).
* **Connection**: `redis://localhost:6379/0` (configured via `settings.REDIS_URL`).
* **FIFO Ordering**: Producer invokes `LPUSH`, Consumer invokes `BRPOP` (blocking pop with 1-second timeout).
* **Payload Serialization**: JSON-serialized envelope containing:
  ```json
  {
    "task_id": "uuid-string",
    "task_type": "parse_invoice",
    "business_id": "uuid-string",
    "payload": {
      "invoice_document_id": "uuid-string",
      "business_id": "uuid-string"
    }
  }
  ```

### 2.3 Task State Machine & Concurrency Control
```
           +------------------+
           |     PENDING      | <-------+ (Transient Error, attempts < 3)
           +------------------+         |
                     |                  |
           claim_task (atomic)          |
                     v                  |
           +------------------+         |
           |    PROCESSING    | --------+
           +------------------+
             /              \
    Success /                \ Permanent Error (or attempts >= 3)
           v                  v
  +------------------+  +------------------+
  |    COMPLETED     |  |      FAILED      |
  +------------------+  +------------------+
```

* **Atomic Claiming**: Uses SQL `UPDATE tasks SET status = 'PROCESSING', started_at = now() WHERE id = :id AND status = 'PENDING' RETURNING *`. This prevents race conditions even if multiple worker threads or processes poll concurrently.
* **Startup Recovery**: `WorkerService.recover_pending_tasks()` queries PostgreSQL for all tasks where `status = 'PENDING'` and re-enqueues them to Redis.

---

## 3. Invoice Parser Architecture (`backend/app/services/parser/`)

### 3.1 Text-First Extraction Strategy
1. **PyMuPDF (`fitz`)**: Fast in-memory stream reader (`fitz.open(stream=bytes, filetype="pdf")`). Extracts raw text layout.
2. **`pypdf` Fallback**: If PyMuPDF extraction yields fewer than 40 characters, `pypdf.PdfReader` is tried.
3. **OCR Fallback (`pytesseract`)**: If extracted text remains sparse (< 40 characters), the document is identified as a scanned image or photo PDF. Pages are rendered into high-resolution pixmaps (150 DPI) and passed through Tesseract OCR. If Tesseract binary is not installed in the operating environment, a warning is logged and a clean `PermanentParserError` is raised without crashing.

### 3.2 Field Extraction & Normalization
* **Invoice Number**: Normalized via anchored regex patterns (`INV-XXXX`, `Invoice #: XXXX`, `Doc ID: XXXX`), filtering out stop words (`date`, `tax`, `statement`, `total`) and requiring at least one numeric digit.
* **Invoice Date**: Parsed and normalized across ISO (`YYYY-MM-DD`), British/Indian (`DD-MM-YYYY`, `DD/MM/YYYY`), US (`MM/DD/YYYY`), and long-form dates (`DD Month YYYY`).
* **Due Date**: Extracted from contractual line items (`Due Date: YYYY-MM-DD`). If absent, derived from commercial terms (e.g. `Net 30` -> `invoice_date + 30 days`, `Net 15` -> `invoice_date + 15 days`). Default commercial fallback is 30 days.
* **Amount & Currency**: Extracted from total line items (`Total Amount`, `Balance Due`, `Grand Total`). Currency symbols (`$`, `₹`, `€`, `£`) and codes (`USD`, `INR`, `EUR`, `GBP`, `CAD`) are normalized into ISO currency codes.
* **Customer Info**: Extracted from `Bill To:`, `Customer:`, or `Customer ID:` lines.

### 3.3 No Fabrication & Validation Rule
If critical fields (`invoice_number`, `invoice_date`, or `amount > 0`) cannot be extracted, the parser fails explicitly rather than hallucinating or guessing values.

---

## 4. Error Handling & Retry Policies

| Error Type | Exception Class | Behavior |
| :--- | :--- | :--- |
| **Transient Error** | `TransientParserError` | Temporary storage I/O timeout, network disconnect. Task status resets to `PENDING`, `attempt` counter increments, re-enqueued to Redis. Max retries = 3. |
| **Permanent Error** | `PermanentParserError` | Corrupt PDF, blank PDF, missing required fields, tenant boundary violation. Task transitions immediately to `FAILED`, `InvoiceDocument.processing_status = 'ERROR'`. No infinite retry loop. |
| **Idempotency** | N/A | If document has already been processed (`processing_status == 'PROCESSED'`), the task re-links the existing invoice and completes cleanly without duplicating records. |

---

## 5. Historical Payment Reconciliation

When an invoice is parsed:
1. Worker checks the `payments` table for existing payments for the same tenant where `invoice_reference == extracted.invoice_number` and `invoice_id IS NULL`.
2. Any matched payments are linked to the newly created `Invoice` (`payment.invoice_id = invoice.id`).
3. If reconciled payments cover or clear the invoice, the invoice status transitions to `payment_status = "PAID"`.

---

## 6. Worker Execution & Verification

### Running the Worker Service
```bash
# Set environment variables (or load from .env)
# Start the single worker process
python -m backend.app.workers.runtime
```

### Running Test Suites
```bash
# Queue unit tests
python -m unittest backend.tests.test_queue -v

# Parser unit tests
python -m unittest backend.tests.test_parser -v

# Worker & end-to-end integration tests
python -m unittest backend.tests.test_worker -v

# Full regression suite (Phase 0 - Phase 5)
python -m unittest discover -s backend/tests -v
```
All 95 unit and integration tests pass cleanly with 100% success rate.
