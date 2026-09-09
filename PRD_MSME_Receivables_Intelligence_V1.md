# PRD — MSME Receivables Intelligence Platform (Version 1)

**Status:** Product/Engineering Source of Truth  
**Version:** 1.0  
**Project type:** Resume-grade full-stack + ML product  
**Primary stack:** Python, FastAPI, PostgreSQL, Redis, React/TypeScript  
**Audience:** Product/building agents (Codex, Cursor, Antigravity, etc.) and the project owner

---

## 0. Source-of-Truth Rule

This document is the authoritative specification for Version 1.

Any agent implementing the project must:

1. Follow this PRD over assumptions in prompts or generated scaffolding.
2. Preserve the architecture unless a change is required by an explicit later decision.
3. Keep Version 1 practical and shippable.
4. Do not introduce extra microservices, research experiments, model families, or integrations unless explicitly added to this PRD.
5. When a later phase needs information not defined here, prefer the simplest implementation consistent with this PRD and record the decision in project documentation.

---

# 1. Product Overview

## 1.1 Product name

**Working name:** MSME Receivables Intelligence Platform  
**Possible product name:** FlowSense (working title; branding can be changed later)

## 1.2 One-line description

An AI/ML-powered receivables intelligence platform that helps B2B businesses understand outstanding invoices, predict whether invoices will be paid late, estimate when payment is likely to arrive, and prioritize which invoices need attention.

## 1.3 Core user problem

Many B2B businesses know that customers owe them money, but the raw invoice list does not answer the operational questions that matter most:

- Which invoices are likely to become late?
- Which customers are consistently slow to pay?
- When is a particular payment likely to arrive?
- Which outstanding invoices should the business follow up on first?
- How much receivables cash is likely to arrive soon?

Version 1 focuses on turning invoice/payment data into actionable predictions rather than trying to replace accounting software.

## 1.4 Target customer

Initial target customers are small and medium B2B businesses with recurring credit-based invoicing and meaningful outstanding receivables.

Examples:

- Small manufacturers
- Wholesalers/distributors
- B2B service companies/agencies
- Engineering/professional-service firms
- Contractors
- Other businesses where customers typically pay after invoice issuance

Out of scope for the initial customer target:

- Pure retail businesses with almost entirely instant payments
- Consumers/personal finance users
- Large enterprise accounting replacements

---

# 2. Product Promise

The product should answer, quickly and clearly:

> **Where is my money, when is it likely to arrive, and what should I act on first?**

The product is not an accounting ledger, lending platform, collections agency, or financial institution.

---

# 3. Version 1 Success Definition

Version 1 is successful when a user can:

1. Register and log in.
2. Create/use one business tenant.
3. Upload invoice PDFs.
4. Upload historical payment data as CSV/Excel.
5. Have uploaded data processed asynchronously.
6. View invoices and receivables in a polished dashboard.
7. See for an eligible invoice:
   - on-time vs late prediction,
   - late-payment probability/risk score,
   - expected payment timing/date.
8. See useful risk prioritization across invoices.
9. See processing states and failures rather than unexplained errors.
10. Run the entire stack with Docker Compose.

The ML model must be integrated into the product. A notebook-only model does not satisfy Version 1.

---

# 4. Version 1 Scope

## 4.1 In scope

### Product

- Authentication
- Tenant/business boundary
- Invoice PDF upload
- Payment-history CSV/Excel upload
- Customer records derived from invoice/payment data
- Invoice processing status
- Receivables dashboard
- Invoice list/detail views
- Risk score display
- Payment-delay prediction
- Expected payment timing/date
- Basic cash-receivable summary derived from invoice predictions
- Async processing
- Failure visibility
- Docker Compose deployment

### ML

- Dataset validation
- As-of feature engineering
- Time-based train/validation/test split
- One payment-delay classifier
- Risk score using the same classifier's probability output
- One payment-timing regression model (or aging-bucket alternative if the target proves unsuitable)
- Baseline comparison
- Metrics and evaluation documentation
- Production inference integration

### Infrastructure

- FastAPI
- PostgreSQL
- Redis
- One worker service with task-type routing
- Object storage for uploaded PDFs
- React frontend

## 4.2 Explicitly out of scope for Version 1

Do NOT add unless this PRD is explicitly updated:

- Kafka
- Kubernetes
- Multiple dedicated worker services
- Separate results microservice
- Event-sourcing architecture
- Service mesh
- TReDS integration
- Direct bank integrations
- WhatsApp integration
- Accounting-software integrations
- Mobile application
- Autonomous AI agents
- SHAP per-invoice explanations
- Multiple competing model architectures for a research study
- Complex model registry/version-management system
- Production financial advice/lending/financing

---

# 5. Core User Journey

```text
Register/Login
    ↓
Business tenant created/selected
    ↓
Upload payment history
    ↓
Upload invoice PDFs
    ↓
Files and records are stored
    ↓
Tasks are queued in Redis
    ↓
Worker processes documents/data
    ↓
Structured data saved to PostgreSQL
    ↓
ML inference runs when sufficient data is available
    ↓
Predictions saved to PostgreSQL
    ↓
Dashboard reads through FastAPI
    ↓
User sees receivable status + risk + expected payment timing
```

---

# 6. System Architecture

## 6.1 Approved Version 1 architecture

```text
React
  │
  │ HTTPS / authenticated API
  ▼
FastAPI
  │
  ├──────────────► PostgreSQL
  │                 Source of truth
  │
  ├──────────────► Object Storage
  │                 Invoice PDFs
  │
  └──────────────► Redis
                    Async job queue
                         │
                         ▼
                  Worker Service
                  Task-Type Router
                    │     │     │
                    ▼     ▼     ▼
                 Parser Prediction Forecast
                    │     │     │
                    └─────┼─────┘
                          ▼
                      PostgreSQL

Dashboard reads:
React → FastAPI → PostgreSQL
```

## 6.2 Component responsibilities

### React

- UI rendering
- User interaction
- Upload controls
- Data visualization
- Loading/error/empty states
- No direct database access

### FastAPI

- Authentication
- Authorization
- Tenant isolation
- Request validation
- Business APIs
- File-upload orchestration
- Database access
- Queue submission

FastAPI is the application's primary security boundary.

### PostgreSQL

Authoritative source of truth for:

- users
- businesses/tenants
- membership
- customers
- invoices
- payments
- predictions
- forecasts
- task metadata/status

### Object Storage

Stores original uploaded invoice files and related document artifacts.

### Redis

Used as asynchronous queue/coordination only.

Redis must not be the authoritative source of application state.

### Worker Service

A single worker process/service that routes jobs by task type.

Example task types:

- `parse_invoice`
- `run_prediction`
- `generate_forecast`

The worker may execute handlers in-process. Separate worker services are not required in V1.

---

# 7. Authentication and Tenant Isolation

The application is multi-tenant from the first version.

## 7.1 Model

```text
User
  ↓
Business/Tenant
  ↓
Customers / Invoices / Payments / Predictions / Forecasts
```

Every business-owned record must be associated with a tenant/business ID.

## 7.2 Required behavior

- User registration/login
- Authenticated requests use JWT or another secure bearer-token mechanism
- Current authenticated user determines tenant context
- API must scope business data to the authenticated tenant
- A user from Business A must never be able to retrieve Business B data by changing an ID in the request

## 7.3 Security requirement

Tenant ownership checks must occur server-side.

Do not rely on frontend hiding/filtering to enforce tenant isolation.

---

# 8. File and Data Inputs

## 8.1 Invoice inputs

Version 1 accepts invoice documents primarily as PDF.

A PDF may be:

- text-based/digital
- scanned/image-based
- from varying invoice layouts

The parser must normalize extracted information into a consistent internal schema.

## 8.2 Payment history input

Primary supported format:

- CSV

Recommended secondary format:

- XLSX/Excel

Expected historical payment data should contain enough information to associate payment events with invoices/customers and observe payment timing.

## 8.3 Candidate public dataset

The project may use the Rajat Tomar "Payment Date Dataset" from Kaggle as an initial model-development dataset, subject to an actual data inspection and license/access check before implementation depends on it.

The dataset is a candidate source, not an immutable assumption. The code must be written around the project's canonical schema after the data audit.

---

# 9. Invoice Parsing Strategy

Parsing strategy for V1:

```text
PDF
 ↓
Detect whether usable text is available
 ↓
Text extraction OR OCR for scanned documents
 ↓
Structured field extraction
 ↓
Schema validation
 ↓
Normalize data
 ↓
Persist invoice fields
```

The application should store both:

- original document reference
- normalized extracted fields

Extraction output must be validated before being treated as trusted structured data.

## 9.1 Core invoice fields

At minimum, support when available:

- invoice number
- customer/buyer name
- invoice date
- due date
- total amount
- currency

Optional/extended fields can include:

- subtotal
- tax/GST
- purchase order number
- payment terms
- seller name

If a field cannot be confidently extracted, preserve a null/unknown value rather than inventing it.

---

# 10. ML Product Requirements

## 10.1 Overall objective

Use practical supervised ML to produce useful invoice-level payment predictions.

Version 1 is **not** research-oriented model comparison. The purpose is to build one strong, evaluated, integrated ML system.

## 10.2 Task A — Payment delay classification

Target:

```text
ON_TIME
LATE
```

The exact target definition must be established from the actual dataset and due-date/clear-date semantics during data preparation.

Preferred model family:

- Gradient-boosted decision tree classifier
- XGBoost or LightGBM

The final implementation should use the model that is practical and performs well on the validated data. Do not build a large model zoo.

## 10.3 Task B — Risk score

No separate risk model.

Risk score is the late-payment probability generated by the same classifier:

```python
risk_score = classifier.predict_proba(X)[:, 1]
```

UI interpretation can map probability to human-readable bands, e.g.:

- Low
- Medium
- High

The score itself remains numeric 0–1.

## 10.4 Task C — Expected payment timing

Primary target representation:

```text
days_until_payment
```

At prediction time:

```text
expected_payment_date = prediction_date + predicted_days
```

Before locking the exact model form, inspect the target distribution.

Possible V1 approaches:

1. Gradient-boosted regression on days-until-payment.
2. A simple target transformation if severe skew requires it.
3. Aging-bucket classification (for example 0–7, 8–15, 16–30, 31–60, 60+) if exact-date regression proves unsuitable.

This decision must be driven by the actual target distribution, not by a desire to maximize model complexity.

---

# 11. Critical ML Correctness Rule: As-Of Features

This is mandatory.

Historical customer features must be calculated using only information that would have been available **before the invoice being predicted**.

Examples:

- customer historical average delay
- customer historical median delay
- customer late-payment rate
- customer payment variance
- recent payment behavior
- previous invoice gap
- prior invoice count

Do NOT compute these once across the entire dataset and then split the rows by date. That leaks future information into earlier examples.

Required approach:

```text
Sort chronologically
      ↓
For each invoice at time t
      ↓
Use only records with information available before t
      ↓
Calculate historical features
      ↓
Create the training example
```

This requirement applies to training, validation, testing, and production inference.

---

# 12. Time-Based Evaluation

Random row-level train/test splitting is not acceptable for the main evaluation.

Use temporal ordering.

Conceptually:

```text
Older period  → training
Middle period → validation
Newest period → final test
```

The exact cut dates/ratios are to be decided after the dataset audit while preserving chronological integrity.

---

# 13. Unpaid/Censored Invoices

A null `clear_date` must not automatically be treated as ordinary missing data.

The data audit must determine whether null `clear_date` means an invoice was still unpaid at observation time.

For initial supervised training, completed payment examples may be used for targets where required, while current/outstanding invoices remain important prediction inputs.

Do not silently drop all outstanding invoices from the application merely because their payment date is unknown.

---

# 14. Baseline

The primary timing baseline must be defined before interpreting model gains.

Recommended operational baseline:

```text
Known customer → customer's historical median payment delay
New customer   → global median payment delay
```

Classification baseline should be simple and leakage-safe, such as a historical customer late-rate rule.

The point of the baseline is to establish whether the ML system is actually useful.

Illustrative metric values in planning documents are not real results and must never be presented as achieved performance.

---

# 15. ML Evaluation Requirements

## Classification

At minimum record:

- Precision
- Recall
- F1
- ROC-AUC
- PR-AUC
- Confusion matrix
- Probability calibration check

Accuracy may be shown but is not sufficient as the primary metric.

## Timing prediction

At minimum record:

- MAE
- RMSE or another secondary error metric where appropriate
- target distribution summary

The chosen primary metric should align with expected business usefulness.

## Required evaluation artifact

The repository must contain a concise evaluation report describing:

- dataset characteristics
- target definitions
- missingness
- feature generation approach
- leakage prevention
- temporal split
- baseline definition
- final model metrics
- known limitations

---

# 16. ML Explainability

## Version 1

Do not implement SHAP-based per-invoice explanations.

Use model-level feature importance for a simple dashboard section such as:

> **Top factors associated with late payment**

This is global model information, not a claim that a single invoice was caused by a specific feature.

## Future / V2

Per-invoice SHAP explanations may be added later.

---

# 17. Prediction Pipeline

```text
Invoice + historical payment data
                ↓
        As-of feature builder
                ↓
        Feature validation
                ↓
       Payment classifier
          ↙            ↘
 P(late payment)     ON_TIME/LATE
       ↓
   Risk score

        Separate timing model
                ↓
        Predicted days
                ↓
    Expected payment date
                ↓
         Save to DB
```

---

# 18. Async Processing Workflow

## 18.1 Invoice upload

```text
React
  ↓
POST /invoices/upload
  ↓
FastAPI authenticates user
  ↓
Tenant ownership established
  ↓
Create invoice/document record
status = PENDING
  ↓
Store PDF in object storage
  ↓
Create task
  ↓
Enqueue task in Redis
  ↓
Return accepted/pending response
```

## 18.2 Worker processing

```text
Redis
  ↓
Worker
  ↓
Task Router
  ↓
Parser
  ↓
Save extracted invoice data
  ↓
Prediction
  ↓
Save prediction
  ↓
Forecast/aggregation when applicable
  ↓
Update task + invoice status
```

## 18.3 Failure behavior

Failed tasks must result in visible status and useful server-side logs.

Do not leave records permanently stuck in `PENDING` with no explanation.

A simple retry policy is allowed and encouraged for transient processing failures, but V1 does not need a sophisticated distributed retry system.

---

# 19. Task Message Contract

Redis task payload should contain enough identifiers to route and safely process work.

Recommended shape:

```json
{
  "task_id": "uuid",
  "task_type": "parse_invoice",
  "invoice_id": "uuid",
  "tenant_id": "uuid"
}
```

A task must be validated before execution.

The worker must respect tenant ownership when accessing database records.

---

# 20. PostgreSQL Data Model

A practical V1 data model should include approximately:

```text
users
businesses
business_members

customers

invoices
invoice_documents
payments

prediction_results
cashflow_forecasts

tasks
```

Optional support tables may be added when genuinely necessary, but avoid unnecessary schema proliferation.

## 20.1 Invoices

Suggested fields:

```text
id
tenant_id
customer_id
invoice_number
invoice_date
due_date
amount
currency
status
document_id
created_at
updated_at
```

## 20.2 Payments

Suggested fields:

```text
id
tenant_id
invoice_id
payment_date
amount
created_at
```

## 20.3 Predictions

Suggested fields:

```text
id
invoice_id
prediction
risk_score
predicted_days_until_payment
expected_payment_date
created_at
```

No formal model-version registry is required in Version 1.

## 20.4 Tasks

Suggested fields:

```text
id
tenant_id
task_type
status
invoice_id
error_message
created_at
started_at
completed_at
```

---

# 21. API Surface

The API should remain compact.

## Authentication

```text
POST /auth/register
POST /auth/login
GET  /auth/me
```

## Business

```text
GET /business
```

## Customers

```text
GET  /customers
POST /customers
GET  /customers/{id}
```

## Invoices

```text
POST /invoices/upload
GET  /invoices
GET  /invoices/{id}
```

## Predictions

```text
GET /invoices/{id}/prediction
GET /risk/high
```

## Dashboard

```text
GET /dashboard/summary
GET /dashboard/cashflow
```

Exact request/response schemas are to be implemented with Pydantic and documented in OpenAPI.

---

# 22. Frontend Product & Aesthetic Requirements

This is a major requirement, not a cosmetic afterthought.

The frontend must **not look like a generic AI-generated SaaS dashboard**.

## 22.1 Design direction

Aim for:

- minimal
- calm
- premium
- business-focused
- information-dense without feeling crowded
- excellent typography
- generous spacing
- restrained use of color
- clear hierarchy
- polished micro-interactions

Avoid:

- excessive gradients
- glowing AI effects
- generic glassmorphism everywhere
- oversized hero text inside the app
- decorative illustrations that don't help the workflow
- excessive rounded cards
- too many colors
- fake-looking "AI" badges everywhere
- charts used only for decoration

## 22.2 Interaction quality

Every meaningful action must have clear:

- hover state
- focus state
- loading state
- success state
- empty state
- error state

Uploads should visibly progress from:

```text
Uploading → Processing → Ready / Failed
```

Tables should support useful interaction rather than simply displaying static rows.

Examples:

- sorting
- filtering
- search
- pagination
- row hover
- clickable invoice detail
- risk filtering
- date filtering

## 22.3 Dashboard priorities

The dashboard should immediately answer:

1. How much is outstanding?
2. How much is overdue?
3. What is high risk?
4. What should I look at first?
5. What payments are likely to arrive soon?

The first viewport should communicate those answers without requiring a tour or modal.

## 22.4 Recommended visual structure

### Header

- business identity
- date/context
- account menu

### Summary row

Use a small number of high-value metrics:

- Outstanding receivables
- Overdue
- Due soon
- High risk

### Forecast visualization

A restrained, highly legible chart for expected incoming receivables over a configurable near-term period.

### Priority section

A ranked table/list of invoices needing attention.

### Invoice table

Strong table typography, clear risk indicators, concise data density.

## 22.5 Color usage

Do not turn the entire interface into traffic-light colors.

Use color sparingly for semantic meaning:

- neutral for ordinary values
- one accent for primary actions
- restrained warning/high-risk colors only where useful

## 22.6 Responsive behavior

The app should work cleanly on desktop and reasonable tablet widths.

Desktop is the primary V1 environment.

## 22.7 Accessibility

At minimum:

- keyboard navigable controls
- visible focus indicators
- sufficient contrast
- semantic form labels
- accessible table headings
- descriptive status text

---

# 23. Required Frontend Screens

## 23.1 Authentication

- Login
- Registration

## 23.2 Main dashboard

Displays:

- receivable summary
- overdue summary
- risk summary
- expected collections chart
- priority invoices

## 23.3 Invoices

Features:

- searchable table
- filters
- pagination
- status/risk indicators
- invoice upload action

## 23.4 Invoice detail

Displays:

- customer
- invoice amount
- issue/due dates
- processing state
- prediction
- risk score
- predicted payment timing/date
- source document metadata

## 23.5 Upload flow

Must support:

- invoice PDF upload
- payment-history CSV/XLSX upload
- client-side file validation
- upload progress
- processing state
- errors

---

# 24. Dashboard Data Semantics

The UI must clearly distinguish between:

- actual historical/payment data
- predicted information
- estimates/ranges

Never visually present model predictions as guaranteed payment dates.

Use wording such as:

- Expected payment
- Predicted
- Estimated
- Probability of late payment

rather than:

- Guaranteed payment date
- Certain

---

# 25. Error and Empty States

The product must handle:

- no invoices uploaded
- no payment history uploaded
- invoice parsing failure
- invalid file format
- malformed CSV
- missing required columns
- insufficient historical data for a prediction
- prediction failure
- worker unavailable
- expired/invalid authentication
- cross-tenant access attempt

Empty states should explain the next useful action.

Example:

> **No receivables yet**  
> Upload your first invoice to start building your receivables view.

---

# 26. Business Logic Rules

1. An invoice cannot be treated as successfully processed until required extraction/validation succeeds.
2. Predictions require sufficient feature data. If a prediction cannot be reliably generated, show a clear unavailable state instead of a fabricated prediction.
3. New customers must be supported using cold-start-safe fallback features/baselines.
4. All tenant-owned records must be tenant-scoped.
5. Original uploaded PDFs must remain available through object storage references.
6. PostgreSQL is the source of truth for application state.
7. Redis is only the async queue/coordination layer.

---

# 27. Cold-Start Handling

New customers with no payment history are expected.

The feature pipeline must distinguish between:

```text
Customer with history
        vs
New customer
```

For new customers:

- avoid invented historical statistics
- use null-safe/default features
- use global or otherwise appropriate baseline information
- make prediction availability/uncertainty explicit where required

This is required for V1 correctness.

---

# 28. Deployment

V1 must run with Docker Compose.

## Minimum services

```text
frontend
api
worker
postgres
redis
object-storage
```

A locally runnable object-storage service is preferred for development if needed (for example, an S3-compatible service).

Production cloud deployment can be added later and is not required to declare V1 complete.

## Required developer experience

A developer should be able to get the project running through documented commands without manually assembling undocumented infrastructure.

---

# 29. Observability for V1

Keep this practical.

Required:

- structured application logging
- task processing logs
- error logs
- request correlation/log identifiers where reasonable
- worker status visibility in logs

Heavy observability infrastructure is not required for V1.

---

# 30. Testing Requirements

## Backend

Test at minimum:

- authentication
- tenant isolation
- invoice upload validation
- payment upload validation
- invoice state transitions
- prediction persistence
- task routing
- failure handling

## ML

Test at minimum:

- data validation
- as-of feature logic
- no future-data leakage in feature calculations
- temporal split behavior
- baseline generation
- inference schema

## Frontend

Test critical user journeys where practical:

- login
- invoice upload
- payment upload
- dashboard loading
- invoice detail
- error state

## End-to-end smoke test

At least one documented flow must prove:

```text
Upload invoice
   ↓
FastAPI
   ↓
PostgreSQL + Object Storage
   ↓
Redis
   ↓
Worker
   ↓
Parser
   ↓
Prediction
   ↓
PostgreSQL
   ↓
Dashboard
```

---

# 31. Project Documentation Requirements

The repository should contain, at minimum:

```text
README.md

/docs
  architecture.md
  api.md
  ml.md
  data.md
  deployment.md
  decisions.md
```

Documentation should emphasize the actual engineering choices and tradeoffs made for V1.

---

# 32. Recommended Repository Structure

A practical structure is:

```text
msme-receivables-intelligence/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── db/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── workers/
│   │   └── main.py
│   │
│   ├── ml/
│   │   ├── data/
│   │   ├── features/
│   │   ├── models/
│   │   ├── training/
│   │   ├── evaluation/
│   │   └── inference/
│   │
│   ├── tests/
│   └── pyproject.toml
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── features/
│   │   ├── hooks/
│   │   ├── lib/
│   │   └── styles/
│   └── package.json
│
├── docs/
├── docker-compose.yml
├── .env.example
└── README.md
```

The exact organization may evolve slightly, but responsibilities should remain separated.

---

# 33. Development Phases

These phases define the intended implementation sequence for Version 1.

## Phase 0 — Data Audit & Contract

Deliverables:

- dataset acquisition/access
- schema inventory
- data-quality report
- target semantics
- clear-date/null analysis
- duplicate analysis
- date-range analysis
- canonical internal data schema
- initial leakage risks documented

## Phase 1 — ML Data Pipeline

Deliverables:

- cleaned dataset pipeline
- chronological sorting
- as-of feature builder
- leakage-safe feature tests
- temporal train/validation/test split
- baseline implementation

## Phase 2 — Payment Prediction Model

Deliverables:

- payment-delay classifier
- risk probability output
- timing prediction model
- target-distribution decision
- evaluation metrics
- training/inference scripts
- saved model artifacts used by the app

The objective is one practical final modeling approach, not a research benchmark across many algorithms.

## Phase 3 — Backend Foundation

Deliverables:

- FastAPI application
- PostgreSQL connection
- SQLAlchemy/Alembic
- core data models
- authentication
- tenant boundary
- initial APIs

## Phase 4 — Document & Data Ingestion

Deliverables:

- object-storage integration
- invoice upload
- payment-history upload
- parsing pipeline
- validation
- invoice statuses

## Phase 5 — Async Worker System

Deliverables:

- Redis queue
- task model
- single worker service
- task router
- parser task
- prediction task
- forecast task
- failure/status handling

## Phase 6 — ML/Application Integration

Deliverables:

- production inference from worker
- predictions persisted to PostgreSQL
- expected payment date persisted
- dashboard-ready queries
- cold-start handling

## Phase 7 — Frontend Product

Deliverables:

- authentication UI
- dashboard
- invoice table
- invoice detail
- upload flows
- prediction visualizations
- interactive filtering/sorting
- polished design system
- loading/error/empty states

Frontend quality is a first-class deliverable.

## Phase 8 — Integration & Quality

Deliverables:

- API tests
- tenant-isolation tests
- ML leakage tests
- worker tests
- upload-to-prediction smoke test
- frontend critical-path tests
- usability polish

## Phase 9 — Docker Deployment

Deliverables:

- Dockerfiles
- Docker Compose
- environment configuration
- database migrations
- startup instructions
- production-like local run

## Phase 10 — Final Documentation & Resume Readiness

Deliverables:

- architecture documentation
- ML evaluation report
- product screenshots
- setup instructions
- demo dataset/sample workflow
- known limitations
- resume bullet candidates
- interview explanation notes

---

# 34. Definition of Done — Version 1

Version 1 is complete only when all of the following are true.

## Product

- [ ] User can register/login
- [ ] Business/tenant is created
- [ ] User data is isolated by tenant
- [ ] Invoice PDFs can be uploaded
- [ ] Payment CSV/XLSX can be uploaded
- [ ] Processing happens asynchronously
- [ ] Dashboard displays invoices/receivables
- [ ] Invoice detail shows prediction results
- [ ] UI contains polished loading/error/empty states
- [ ] Main workflows are genuinely interactive and usable

## ML

- [ ] Dataset validated
- [ ] As-of features implemented
- [ ] Leakage tests implemented
- [ ] Time-based train/validation/test split implemented
- [ ] Baseline implemented
- [ ] Payment classifier trained
- [ ] Risk probability generated from classifier
- [ ] Payment timing model implemented
- [ ] Expected payment date generated where supported
- [ ] Metrics documented
- [ ] Final model artifacts saved for inference

## Integration

- [ ] Uploaded invoice reaches parser
- [ ] Parsed invoice reaches PostgreSQL
- [ ] Prediction worker can consume invoice data
- [ ] Prediction saved to PostgreSQL
- [ ] Dashboard displays prediction
- [ ] Failed jobs have visible status
- [ ] End-to-end smoke flow passes

## Deployment

- [ ] Entire V1 runs through Docker Compose
- [ ] Database migration works from a clean environment
- [ ] Environment configuration is documented
- [ ] README provides complete startup instructions

---

# 35. Non-Functional Requirements

## Performance

The UI should feel responsive for normal MVP datasets.

Long-running processing must occur asynchronously.

## Reliability

- A failed parsing job should not corrupt the invoice record.
- Worker failures should leave recoverable state.
- Database writes should be transactional where appropriate.

## Security

- Never commit secrets.
- Passwords must never be stored in plaintext.
- Tenant isolation is mandatory.
- Uploaded files must not be publicly exposed by default.

## Maintainability

- Type-safe API schemas
- migrations under version control
- clear service boundaries inside the monolith
- tests around ML correctness and tenant isolation

---

# 36. Product Language / UI Copy Principles

The product should sound like practical business software.

Prefer:

- Expected payment
- Late-payment risk
- Outstanding
- Due soon
- Overdue
- Needs attention
- Processing
- Prediction

Avoid excessive AI language such as:

- AI magic
- AI-powered everything
- intelligent neural engine
- autonomous finance agent

The product should communicate **usefulness**, not novelty.

---

# 37. Future V2 Ideas — Not Required for V1

Potential future additions:

- SHAP per-invoice explanations
- bank/accounting integrations
- email/WhatsApp follow-up automation
- more advanced cash-flow forecasting
- industry benchmarking
- accounting integrations
- customer behavior segmentation
- survival-analysis/time-to-event modeling
- scalable specialized workers
- cloud-native deployment

These are intentionally excluded from the first release so that Version 1 can actually ship.

---

# 38. Agent Implementation Rules

Any coding agent working on this project should:

1. Read this PRD before modifying architecture or scope.
2. Implement only the current phase unless explicitly instructed otherwise.
3. Reuse existing project code before introducing new frameworks.
4. Keep Python/FastAPI as the primary backend approach.
5. Avoid adding dependencies without a clear reason.
6. Preserve tenant isolation in every API and data access path.
7. Never bypass FastAPI from the React app to reach PostgreSQL directly.
8. Never use future payment information when generating as-of features.
9. Never fabricate missing invoice fields.
10. Make failures observable and recoverable.
11. Keep the frontend visually polished and minimal, not generic AI-dashboard styled.
12. Do not add V2 features while V1 acceptance criteria remain incomplete.
13. Prefer simple architecture with clear responsibilities over premature distributed complexity.

---

# 39. Interview-Level Architecture Narrative

The project should be explainable in approximately one minute:

> The system is a multi-tenant SaaS application for B2B receivables intelligence. React communicates only with a FastAPI backend, which handles authentication, tenant isolation, validation and business APIs. Invoice PDFs are stored in object storage, structured application state is stored in PostgreSQL, and Redis is used only for asynchronous job dispatch. A single worker service routes tasks for document parsing, payment prediction and forecasting. The ML pipeline uses leakage-safe as-of historical features and time-based evaluation. A gradient-boosted classifier predicts on-time versus late payment and its probability becomes the risk score, while a separate timing model predicts days until payment so the application can estimate an expected payment date. The dashboard reads all final state through FastAPI and PostgreSQL.

---

# 40. Final Product Principle

Do not optimize for maximum technical complexity.

Optimize for a complete, credible and usable product:

```text
Real business problem
        ↓
Real data
        ↓
Leakage-safe ML
        ↓
Useful prediction
        ↓
Asynchronous production workflow
        ↓
Secure multi-tenant application
        ↓
Excellent UX
        ↓
Deployable V1
```

**This is the Version 1 target.**
