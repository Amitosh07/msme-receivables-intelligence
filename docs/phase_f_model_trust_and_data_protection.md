# Phase F — Model Trust, Calibration, and Data Protection Audit

## Scope and method

This audit evaluated the existing `payment_classifier_v1` and
`payment_timing_v1` artifacts without retraining or changing the temporal
split. The evaluator loads the saved production artifacts and runs them on the
untouched `data/processed/test.parquet` holdout (5,936 invoices, 2019-12-10 to
2020-02-27). Production inference uses the same 29-feature schema and the
same `V1Predictor` instance; it has no fallback scores or timing values.

## Model results

| Metric | Result |
| --- | ---: |
| Precision | 0.7984 |
| Recall | 0.6308 |
| F1 | 0.7047 |
| ROC-AUC | 0.8456 |
| PR-AUC | 0.8073 |
| Confusion matrix (TN, FP / FN, TP) | `[[3214, 374], [867, 1481]]` |
| Timing MAE | 2.8206 days |
| Timing RMSE | 7.7475 days |

Timing target distribution: min 0, median 15, mean 17.33, p95 39, max 136
days. These are estimates, not payment facts.

### Probability calibration

`risk_score` is the classifier's `predict_proba[:, 1]`: an estimated
probability of late payment, never a guarantee. The holdout calibration bins
are below. They show useful ordering, close alignment at the extremes, and
under-prediction through several middle bands. No calibration transform was
applied in Phase F because that would change the saved-model architecture and
requires a separately governed validation decision.

| Probability bucket | N | Mean predicted | Observed late rate |
| --- | ---: | ---: | ---: |
| 0.0–0.1 | 730 | 0.0637 | 0.0507 |
| 0.1–0.2 | 2,034 | 0.1465 | 0.1794 |
| 0.2–0.3 | 682 | 0.2385 | 0.2830 |
| 0.3–0.4 | 321 | 0.3522 | 0.3551 |
| 0.4–0.5 | 314 | 0.4483 | 0.5032 |
| 0.5–0.6 | 294 | 0.5499 | 0.6190 |
| 0.6–0.7 | 346 | 0.6483 | 0.6908 |
| 0.7–0.8 | 309 | 0.7571 | 0.8058 |
| 0.8–0.9 | 382 | 0.8445 | 0.8089 |
| 0.9–1.0 | 524 | 0.9575 | 0.9580 |

The existing V1 risk tiers remain defensible as operational prioritisation
bands, not guarantees: LOW `<0.30`, MEDIUM `0.30–<0.65`, HIGH `>=0.65`.

## Leakage, customer history, and isolation

Production eligibility requires three completed outcomes strictly before the
invoice date. The shared feature builder filters by business, customer, prior
invoice date, and prior payment date; it excludes the target invoice, its
eventual clear/payment outcome, and later invoice/payment outcomes. Existing
integration tests verify distinct histories produce distinct feature values
received by the real predictor, and that an identically named customer in a
different tenant receives no history or prediction.

## Proof verification trust model

The authenticated route invoice ID is the authoritative target. Extraction
only corroborates it: invoice or customer conflicts are `NEEDS_REVIEW`, and
the service never searches for a different invoice to attach the proof to.

`0.85` is an operational evidence score, **not a statistically calibrated 85%
probability**. Text extraction starts at 0.50 and adds 0.20 for an invoice
reference, 0.20 for an amount, and 0.10 for a date; a single structured
CSV/XLSX row scores 0.90. Verification additionally requires exactly one
candidate, amount/date validation, the target association, no customer or
invoice conflict, and no overpayment beyond V1's factual outstanding balance.
Only then can a new payment be created with `proof_verified`.

Natural-key collisions link to the existing factual payment rather than
creating another row, preserving its existing provenance. `legacy` means
migrated historical data; `import` means CSV/XLSX historical data; `manual`
means a user-entered payment fact; and `proof_verified` means a fact created
after this evidence validation. All four remain eligible historical outcomes
when otherwise valid; Phase F introduces no weighting model.

## Date, storage, and privacy policy

V1's factual payment date policy is `Asia/Kolkata`. Manual and proof paths use
the same validation and normalize date-only facts to a stable midnight-UTC
database representation. This avoids a false "future" date near India/UTC
midnight without introducing per-user timezone behavior.

Proof keys are generated server-side as `tenants/{business_id}/payment_proofs/{proof_id}.{ext}`;
the local storage service rejects traversal and file download checks both
authenticated tenant context and the invoice/proof association. Files have no
public URL. Local development storage is a plain filesystem and is **not
encrypted at rest**. Production must use encrypted object storage and managed
key/access controls. Each newly uploaded proof records a SHA-256 `file_hash`
as an integrity/evidence identifier; hashing is not encryption. Legacy proof
rows that predate the field retain an empty digest rather than a fabricated
integrity claim.

Logs use identifiers and bounded operational diagnostics, not document bodies,
tokens, passwords, or raw proof text. Runtime configuration is environment
based; production deployments must replace development example values and
protect database/JWT configuration outside source control. These controls do
not by themselves establish legal compliance.
