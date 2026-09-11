# V1 Architectural Decisions & Amendments

This document records explicit decisions that extend or refine the original
**MSME Receivables Intelligence Platform V1** PRD.

The original PRD remains the source of truth for the overall product,
architecture, technology stack, scope boundaries, and implementation phases.
The decisions in this document are later, explicit V1 amendments/clarifications
and must be followed by all subsequent implementation phases and coding agents.

---

## 1. Source-of-Truth Rule

The project uses the original PRD as the baseline specification.

These decisions do **not** replace the approved architecture. They clarify
data behavior, ML eligibility, payment workflows, and acceptance requirements.

When a conflict exists between a generic statement in the original PRD and one
of the explicit decisions below, the explicit decision below is the later
locked V1 decision.

Agents must not silently reinterpret, remove, or weaken these decisions.

---

# 2. Architecture Remains Unchanged

The approved V1 architecture remains:

```text
React / TypeScript
        |
        v
FastAPI
   |         \\
   v          v
PostgreSQL   Object Storage
   ^
   |
One Worker Service + Task-Type Router
   |
   v
Redis (async queue / coordination)
   |
   +--> Parser
   |
   +--> Prediction
   |
   +--> Forecast
```

### Locked architectural rules

- PostgreSQL is the source of truth for application state.
- Redis is used for asynchronous queueing and coordination only.
- There is exactly **one worker service** with task-type routing.
- There is no separate prediction microservice.
- There is no separate results microservice.
- No Kafka, Kubernetes, service mesh, event sourcing, or additional
  microservice layer may be introduced for V1.
- Existing modules, services, and pipelines must be reused rather than
  duplicated.
- Tenant isolation must be enforced at the application and database-access
  layers.
- File ingestion continues to use the existing object/file storage mechanism.
- These amendments do not justify introducing a second worker architecture.

---

# 3. Decision A — Customer Identity Foundation

## 3.1 Canonical ownership hierarchy

The canonical relationship is:

```text
Business / Tenant
      |
      v
   Customer
      |
      v
   Invoice
      |
      v
   Payment
```

Customer identity is tenant-scoped.

A customer belonging to one business must never be automatically matched to a
customer belonging to another business.

## 3.2 Unresolved customer contract

When an invoice contains a customer that cannot be matched confidently to an
existing customer:

```text
invoice.customer_id = NULL
invoice.unresolved_customer_name = extracted customer name
```

The system must **not** create a speculative Customer merely because a name
was extracted from an invoice.

The unresolved identity must remain explicit until it can be resolved safely.

## 3.3 Customer matching priority

Matching should use the following order, with conservative behavior:

1. Existing customer ID when explicitly supplied and valid.
2. Valid GSTIN / equivalent strong government/business identifier.
3. Strong existing business-specific identifier.
4. Exact normalized customer name within the same tenant.
5. Conservative secondary matching only when sufficiently reliable.

Ambiguous matches must not be silently auto-assigned.

## 3.4 Normalization

Customer-name normalization must be implemented as one canonical reusable
function and reused everywhere customer identity is compared.

The normalization behavior must be consistent between:

- invoice ingestion,
- historical payment import,
- API/business logic,
- database matching,
- tests.

## 3.5 Payment relationship

The payment relationship remains:

```text
Payment -> Invoice -> Customer
```

There must be no competing independent `payment.customer_id` relationship
unless a later explicit architecture decision changes this.

---

# 4. Decision B — Historical Payment Data Pipeline

Historical payment import is an explicit V1 capability.

Supported source formats:

- CSV
- XLSX / Excel

## 4.1 Import behavior

The importer must:

- normalize headers,
- parse dates using India-first / DD-MM-YYYY-style interpretation where
  applicable,
- reject non-positive payment amounts,
- reuse the Phase A customer identity logic,
- remain tenant-scoped,
- match payments to invoices when a reliable invoice reference exists,
- preserve valid payments even when an invoice cannot currently be found.

An unmatched payment is **not automatically a rejected payment**.

## 4.2 Customer identity reuse

Historical payment imports must reuse the canonical customer identity key
and matching logic established in Decision A.

Do not implement a second customer-matching algorithm specifically for CSV/XLSX
imports.

## 4.3 Canonical payment natural key

The database must enforce idempotency for imported payments using the canonical
natural-key behavior defined by the implementation.

The intended uniqueness is based on:

```text
business_id
customer_identity_key
normalized invoice_reference
payment_date
amount
```

The canonical database constraint/index is:

```text
uq_payment_natural_key
```

with the effective uniqueness scope:

```text
(business_id,
 customer_identity_key,
 lower(btrim(coalesce(invoice_reference, ''))),
 payment_date,
 amount)
```

The exact implementation must remain compatible with the approved schema and
migrations.

## 4.4 Provenance

Imported historical payments must be marked with provenance:

```text
import
```

Manual payments and payment-proof payments use their own provenance values as
specified later in this document.

## 4.5 Per-row results

The import pipeline must distinguish at least:

- imported
- duplicate
- rejected
- unmatched

Unmatched is a valid state and must not be treated as a data-validation
failure when the payment itself is structurally valid.

## 4.6 Partial success

A malformed row must not force an otherwise valid import file to roll back
completely.

Per-row processing should use the implementation's savepoint/partial-success
strategy so valid rows can still be imported.

## 4.7 Python/PostgreSQL normalization parity

The canonical normalization behavior used in Python and PostgreSQL must remain
consistent.

A parity test must prove that the same logical customer values normalize to the
same matching key in both layers.

## 4.8 No ML in this phase

Historical payment ingestion must not secretly trigger or implement a second
ML pipeline.

ML inference belongs to the existing prediction integration.

---

# 5. Decision C — Customer-History-Driven ML Inference

This is the most significant V1 business-logic amendment.

## 5.1 Minimum customer history

A customer must have at least:

```text
MIN_CUSTOMER_HISTORY_FOR_PREDICTION = 3
```

eligible prior completed payment outcomes before the current invoice date.

## 5.2 First-time / insufficient-history behavior

When the customer has fewer than 3 eligible historical payment outcomes:

```text
prediction_available = false
```

and the system must expose the reason:

```text
Insufficient customer payment history
```

In this state, the application must **not** fabricate or display:

- a risk tier,
- a risk probability,
- a predicted payment date,
- a hard-coded risk value,
- a hard-coded payment-delay value.

Examples of forbidden artificial fallback values include:

```text
risk = MEDIUM
risk_score = 0.5
predicted_days_until_payment = 15
```

These values must not be used merely to make the UI appear complete.

## 5.3 Important override to generic cold-start behavior

The original PRD describes cold-start support and allows global fallback/baseline
behavior for new customers.

For this implementation, the later locked V1 business decision is:

> **Do not generate a customer-specific payment prediction until the customer
> has at least 3 eligible prior completed payment outcomes.**

Therefore, the generic cold-start prediction fallback is intentionally
superseded for production V1 inference.

This must be treated as a documented product decision, not as an accidental
missing feature.

## 5.4 Existing feature pipeline only

When the customer has sufficient history, inference must use the existing
leakage-safe feature pipeline created during the ML phases.

Do not create a second customer-history feature builder.

Features must be constructed using information available as of the current
invoice/prediction point.

## 5.5 Leakage prevention

The current invoice's future payment outcome must never be used as an input
feature for its own prediction.

Any field that would only become known after payment must remain excluded from
inference features.

## 5.6 Real trained models only

Prediction must use the actual trained models already established by the
project:

```text
payment_classifier_v1
payment_timing_v1
```

No random outputs, hard-coded scores, placeholder probabilities, or fake model
responses are permitted.

## 5.7 Prediction outputs

For eligible customers, the application should expose/persist the actual model
outputs needed by the product:

```text
risk_score
is_late_predicted
risk_tier
predicted_days_until_payment
expected_payment_date
```

The expected payment date is derived from the prediction date and predicted
payment timing.

## 5.8 Customer-specific behavior

Two customers with materially different payment histories must be capable of
producing different feature vectors and therefore different predictions.

Tests must verify that:

- customer history is actually retrieved,
- customer-specific features are different where the underlying histories
  differ,
- the real inference code receives those feature vectors,
- predictions are not constant placeholder values.

## 5.9 Persistence and idempotency

Prediction results must be persisted against the invoice and tenant/business
context using the existing prediction model/schema.

Repeated processing of the same invoice must not create uncontrolled duplicate
prediction records.

---

# 6. Decision D — Manual Payment Received Workflow

A real payment-recording workflow is added to V1.

## 6.1 User workflow

From the invoice detail view, the user can record a payment / mark payment
received.

The form must support:

- payment date,
- payment amount,
- optional payment reference,
- optional note.

## 6.2 Database behavior

A real Payment row must be created with the appropriate:

```text
tenant/business context
invoice_id
payment_date
amount
provenance = manual
created_at
```

The exact field names must follow the existing schema.

## 6.3 Invoice status

Invoice payment status must be derived from actual recorded payments and the
existing status logic.

The system must not mark an invoice as paid simply because:

- the due date passed,
- the model predicts payment,
- a risk score is low/high.

Predictions describe expected behavior; payments are factual transaction data.

## 6.4 Partial payments

Partial payments must be supported where the existing schema and status model
permit them.

Multiple real payments may contribute to settlement of one invoice.

## 6.5 ML interaction

A recorded payment becomes part of customer history and may therefore affect
future predictions after it is eligible for the feature pipeline.

The trained model itself is not changed during this workflow.

## 6.6 Idempotency

The workflow must include reasonable idempotency protection so accidental
duplicate submission does not silently create duplicate payment records.

## 6.7 Real verification

At least one real manual-payment test must verify the complete path:

```text
UI
  -> API
  -> PostgreSQL Payment row
  -> invoice/payment status
```

---

# 7. Decision E — Payment Proof Upload & Verification

A payment-proof verification workflow is added to V1.

## 7.1 Supported proof inputs

The proof workflow may accept supported payment evidence in:

- PDF
- CSV
- XLSX

The existing storage and asynchronous-processing architecture must be reused.

## 7.2 Processing flow

The intended flow is:

```text
Upload proof
    |
    v
Store original file
    |
    v
Queue async task
    |
    v
One worker / task router
    |
    v
Parse / extract
    |
    v
Validate
    |
    v
Match invoice/customer
    |
    v
Create Payment
    |
    v
Update factual payment state
```

## 7.3 Extraction targets

Where supported by the source evidence, the parser should attempt to extract:

- invoice reference,
- payment date,
- payment amount,
- customer identity.

## 7.4 Ambiguous matches

Ambiguous payment-proof matches must not be auto-assigned.

The system should place the record into an observable review/error state
appropriate to the existing application design.

## 7.5 Provenance

Verified proof-created payment records must carry:

```text
provenance = proof_verified
```

## 7.6 Idempotency

Reprocessing the same proof must not create duplicate payment records.

## 7.7 Failure handling

Malformed or unsupported proof files must:

- fail visibly,
- preserve useful diagnostic information,
- avoid corrupting financial records,
- remain recoverable through the existing task/error model.

---

# 8. Decision F — Model Trust & Data Protection Audit

This phase is a hardening and verification stage.

It does not introduce another architecture.

## 8.1 Model evaluation

The actual classifier must be evaluated on an untouched temporal test set.

At minimum, the audit should report where applicable:

- precision,
- recall,
- F1,
- ROC-AUC,
- PR-AUC,
- confusion matrix,
- calibration behavior,
- selected risk thresholds.

The timing model should report appropriate metrics including:

- MAE,
- RMSE.

The audit must verify that evaluation respects temporal ordering and does not
introduce future-data leakage.

## 8.2 Hard-coded prediction audit

The codebase must be inspected for placeholder prediction logic, including
hard-coded values or logic that bypasses the trained models.

Such shortcuts must be removed from production inference.

## 8.3 Tenant isolation audit

Verify that:

- one business cannot read another business's customers,
- invoices,
- payments,
- prediction results,
- files,
- task metadata,
- or other tenant-owned state.

Tenant scoping must also apply to asynchronous processing and API reads.

## 8.4 Logging and observability

Task failures and important ingestion/prediction failures must be observable
through the existing logging/task metadata mechanisms.

Logs must not expose secrets or unnecessarily sensitive financial data.

## 8.5 File-access protection

Uploaded files and payment proofs must be accessible only through authorized
tenant/business flows.

No unauthenticated cross-tenant file access is acceptable.

## 8.6 Database protection

Secrets and connection strings must come from environment/configuration and
must not be committed to source control.

The repository must not contain live passwords, tokens, or other secrets.

## 8.7 Privacy note

Documentation should include an appropriately scoped India-focused privacy/data
protection note.

Do not make unsupported claims that a particular implementation is legally
compliant or that a specific law applies in a way the project has not verified.

---

# 9. Decision G — End-to-End Product & ML Acceptance Test

The final acceptance stage validates the full product journey rather than
introducing a new subsystem.

## 9.1 Required scenarios

### Scenario 1 — Customer with sufficient history

1. Import or establish at least 3 eligible prior payment outcomes.
2. Upload a new invoice for that customer.
3. Process the invoice asynchronously.
4. Run the actual prediction flow.
5. Verify a real persisted prediction exists.
6. Verify the UI displays the prediction state correctly.

### Scenario 2 — First-time customer

1. Create/upload an invoice for a customer with no sufficient history.
2. Resolve or preserve customer identity correctly.
3. Process the invoice.
4. Verify:

```text
prediction_available = false
```

5. Verify the reason indicates insufficient customer history.
6. Verify no fake risk score/tier/date is shown.

### Scenario 3 — Manual payment

1. Open an invoice.
2. Record a real payment from the UI.
3. Verify the Payment row in PostgreSQL.
4. Verify factual invoice payment status updates correctly.

### Scenario 4 — History growth

1. Start with insufficient customer history.
2. Record/import additional valid historical payments.
3. Reach the configured minimum history threshold.
4. Process a subsequent invoice.
5. Verify prediction becomes eligible only after the threshold is satisfied.

### Scenario 5 — Payment proof

1. Upload a supported payment proof.
2. Confirm the file enters the asynchronous processing flow.
3. Verify parsing/validation/matching.
4. Verify a real Payment record is created only when the evidence is
   sufficiently reliable.
5. Verify provenance is `proof_verified`.
6. Verify ambiguous evidence is not silently assigned.

### Scenario 6 — Leakage

Verify that the current invoice's future payment outcome is never used when
building features for that invoice.

### Scenario 7 — Model authenticity

Verify the production prediction path calls:

```text
payment_classifier_v1
payment_timing_v1
```

and does not substitute hard-coded/random outputs.

### Scenario 8 — Tenant isolation

Verify that a user/business can access only its own:

- customers,
- invoices,
- payments,
- prediction results,
- uploaded files,
- relevant task metadata.

### Scenario 9 — Real UI path

The acceptance test should exercise the actual application screens and APIs,
not only isolated unit functions.

---

# 10. Explicit Non-Changes

The decisions in this document do **not** authorize the following:

- Kafka
- Kubernetes
- multiple worker services
- a separate model-serving microservice
- a separate prediction-results microservice
- event sourcing
- service mesh
- direct bank/accounting integrations
- TReDS integration
- WhatsApp integration
- mobile application
- autonomous AI agents
- per-invoice SHAP/explanation system
- a model zoo
- a complex model registry
- unrelated V2 features

The purpose of A-G is to strengthen V1 data quality, payment workflows,
customer-history correctness, model trust, and end-to-end acceptance while
keeping the original architecture intact.

---

# 11. Implementation Rules for Future Agents

Every future coding-agent prompt must follow these rules:

1. Treat the original PRD plus this document as the current V1 specification.
2. Preserve the approved architecture.
3. Reuse existing modules and pipelines whenever possible.
4. Do not create parallel customer identity logic.
5. Do not create a parallel ML feature pipeline.
6. Do not bypass the real trained models.
7. Do not use future information for current predictions.
8. Preserve tenant isolation across synchronous and asynchronous paths.
9. Do not invent invoice/payment facts when source evidence is missing.
10. Do not silently create speculative customers from ambiguous names.
11. Do not delete existing financial/history data to make tests pass.
12. Use proper database migrations for schema changes.
13. Keep important failures observable and recoverable.
14. Avoid unrelated refactors or V2 scope.
15. Do not perform Git operations unless explicitly instructed outside the
    agent prompt.

---

# 12. Decision Priority

For future implementation, use this priority:

```text
Explicit later V1 decision in this file
        >
Original PRD generic rule where they conflict
        >
Agent assumption
```

Agents must never treat their own assumptions as specification.

Where implementation details remain unspecified, prefer the smallest change
that preserves:

- the original architecture,
- tenant isolation,
- data integrity,
- leakage safety,
- idempotency,
- recoverability,
- and the explicit decisions above.

---

# 13. Change Summary

| Area | Original V1 | Locked V1 Decision |
|---|---|---|
| Architecture | Approved one-worker architecture | Unchanged |
| Customer identity | Customer/invoice/payment relationships | Explicit unresolved-customer contract + canonical matching |
| Historical payments | CSV/XLSX ingestion | Formal tenant-scoped, idempotent import with unmatched-state handling |
| Cold start | Generic fallback/baseline supported | **No prediction below 3 eligible prior completed outcomes** |
| ML inference | Actual trained models | Customer-history-driven inference using existing feature pipeline |
| Manual payments | Not a core original workflow | Added V1 feature |
| Payment proof | Not a core original workflow | Added V1 feature |
| Model/data audit | Testing and quality requirements | Formalized trust + protection audit |
| E2E acceptance | Definition of done | Expanded into explicit business/ML scenarios |

---

# 14. Final Locked Statement

**The project remains the original MSME Receivables Intelligence Platform V1
architecture, with A-G treated as explicit later V1 amendments and locked
implementation decisions.**

The architecture is unchanged.

The biggest business-logic amendment is the customer-history requirement:

> A prediction is available only when the customer has at least 3 eligible
> prior completed payment outcomes before the current invoice date.

Manual payment recording and payment-proof verification are explicit additional
V1 product workflows.

All subsequent implementation, testing, and acceptance work must respect these
decisions unless a later documented decision explicitly supersedes them.
