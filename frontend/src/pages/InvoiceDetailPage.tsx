import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  CalendarClock,
  CheckCircle2,
  Clock,
  FileSpreadsheet,
  FileText,
  IndianRupee,
  LoaderCircle,
  Plus,
  Sparkles,
  UploadCloud,
  X,
} from "../components/Icons";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { invoicesApi, normaliseError, paymentsApi, predictionsApi } from "../lib/api";
import { date, money, titleCase } from "../lib/format";
import { RiskBadge, StatusBadge } from "../components/Badges";
import { ErrorState, LoadingState } from "../components/States";
import type { ManualPaymentRequest, PaymentProof, PaymentRecord } from "../lib/types";

export function InvoiceDetailPage() {
  const { id = "" } = useParams();
  const [generating, setGenerating] = useState(false);
  const [predictionError, setPredictionError] = useState("");
  const [showPaymentForm, setShowPaymentForm] = useState(false);
  const [showProofUpload, setShowProofUpload] = useState(false);
  const [proofs, setProofs] = useState<PaymentProof[]>([]);

  const state = useAsync(async () => {
    const invoice = await invoicesApi.get(id);
    let prediction;
    try {
      prediction = await predictionsApi.get(id);
    } catch (error) {
      if (normaliseError(error).status !== 404) throw error;
    }
    try {
      const proofList = await paymentsApi.getProofs(id);
      setProofs(proofList);
    } catch {
      // Non-blocking
    }
    return { invoice, prediction };
  }, [id]);

  const refreshProofs = useCallback(async () => {
    try {
      const proofList = await paymentsApi.getProofs(id);
      setProofs(proofList);
    } catch {
      // Non-blocking
    }
  }, [id]);

  if (state.loading) return <LoadingState label="Loading invoice…" />;
  if (state.error) return <ErrorState message={normaliseError(state.error).message} retry={state.refresh} />;
  const { invoice, prediction: predictionState } = state.data!;
  const prediction = predictionState?.prediction_available ? predictionState : undefined;
  const unavailableReason = predictionState && !predictionState.prediction_available
    ? predictionState.reason
    : "";
  const eligible = invoice.processing_status === "PROCESSED";

  const generate = async () => {
    setGenerating(true);
    setPredictionError("");
    try {
      await predictionsApi.create(invoice.id);
      await state.refresh(false);
      window.dispatchEvent(new Event("msme:data-changed"));
    } catch (error) {
      setPredictionError(normaliseError(error).message);
    } finally {
      setGenerating(false);
    }
  };

  const totalPaid = invoice.total_paid ?? 0;
  const outstanding = invoice.outstanding_balance ?? invoice.amount;
  const payments = invoice.payments ?? [];

  return <>
    <Link className="back" to="/invoices"><ArrowLeft size={16} />All invoices</Link>
    <section className="detail-head">
      <div>
        <p className="eyebrow">Invoice</p>
        <h2>{invoice.invoice_number || "—"}</h2>
        <p>Created {date(invoice.created_at.slice(0, 10))}</p>
      </div>
      <div className="detail-value">
        <strong>{money(invoice.amount, invoice.currency)}</strong>
        <StatusBadge value={invoice.payment_status} />
      </div>
    </section>

    <div className="detail-grid">
      <section className="detail-section">
        <h2>Invoice facts</h2>
        <dl>
          <Fact label="Invoice date" value={date(invoice.invoice_date)} />
          <Fact label="Due date" value={date(invoice.due_date)} />
          <Fact label="Payment terms" value={invoice.payment_terms || "Not available"} />
          <Fact label="Payment status" value={<StatusBadge value={invoice.payment_status} />} />
          <Fact label="Processing" value={<StatusBadge value={invoice.processing_status} />} />
          <Fact label="Currency" value={invoice.currency || "INR"} />
        </dl>

        {/* Payment balance summary */}
        <div className="payment-summary">
          <h3><IndianRupee size={16} /> Payment summary</h3>
          <dl>
            <Fact label="Invoice amount" value={money(invoice.amount, invoice.currency)} />
            <Fact label="Total paid" value={money(totalPaid, invoice.currency)} />
            <Fact label="Outstanding" value={money(outstanding, invoice.currency)} />
          </dl>
        </div>

        {/* Payment actions */}
        {invoice.payment_status !== "PAID" && (
          <div className="payment-action-buttons">
            <button
              className="button primary"
              onClick={() => {
                setShowPaymentForm(true);
                setShowProofUpload(false);
              }}
            >
              <Plus size={16} /> Record payment
            </button>
            <button
              className="button secondary"
              onClick={() => {
                setShowProofUpload(true);
                setShowPaymentForm(false);
              }}
            >
              <UploadCloud size={16} /> Upload payment proof
            </button>
          </div>
        )}

        {/* Payment form */}
        {showPaymentForm && (
          <PaymentForm
            invoiceId={invoice.id}
            invoiceAmount={invoice.amount}
            outstandingBalance={outstanding}
            currency={invoice.currency}
            onSuccess={async () => {
              setShowPaymentForm(false);
              await state.refresh(false);
              window.dispatchEvent(new Event("msme:data-changed"));
            }}
            onCancel={() => setShowPaymentForm(false)}
          />
        )}

        {/* Proof upload workflow modal/panel */}
        {showProofUpload && (
          <ProofUploadPanel
            invoiceId={invoice.id}
            currency={invoice.currency}
            onVerified={async () => {
              await state.refresh(false);
              await refreshProofs();
              window.dispatchEvent(new Event("msme:data-changed"));
            }}
            onCancel={() => setShowProofUpload(false)}
          />
        )}

        {/* Payment history */}
        {payments.length > 0 && (
          <div className="payment-history">
            <h3><Clock size={16} /> Payment history</h3>
            <ul className="payment-list">
              {payments.map((p) => (
                <PaymentRow key={p.id} payment={p} currency={invoice.currency} />
              ))}
            </ul>
          </div>
        )}

        {/* Uploaded payment proofs */}
        {proofs.length > 0 && (
          <div className="proof-history">
            <h3><FileText size={16} /> Payment proof documents</h3>
            <ul className="proof-list">
              {proofs.map((proof) => (
                <ProofRow key={proof.id} proof={proof} currency={invoice.currency} />
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className="prediction-panel">
        <div className="panel-title">
          <Sparkles size={18} />
          <div>
            <h2>Payment estimate</h2>
            <p>Predicted values, based on available invoice and payment history.</p>
          </div>
        </div>
        {prediction ? (
          <div className="prediction-content">
            <RiskBadge tier={prediction.risk_tier} score={prediction.risk_score} />
            <strong>{Math.round(prediction.risk_score * 100)}% probability of late payment</strong>
            <div className="expected">
              <CalendarClock size={19} />
              <div>
                <span>Expected payment</span>
                <b>{date(prediction.expected_payment_date)}</b>
                {prediction.predicted_days_until_payment !== null &&
                  prediction.predicted_days_until_payment !== undefined && (
                    <small>
                      Estimated {Math.round(prediction.predicted_days_until_payment)} days from invoice date
                    </small>
                  )}
              </div>
            </div>
            <small className="disclaimer">This is a model estimate, not a confirmed payment date.</small>
            <button className="button secondary" onClick={generate} disabled={generating}>
              {generating ? "Refreshing estimate…" : "Re-generate prediction"}
            </button>
            {predictionError && <p className="error-text" role="alert">{predictionError}</p>}
          </div>
        ) : (
          <div className="unavailable">
            <FileText size={20} />
            <div>
              <h3>{generating ? "Generating prediction…" : "Prediction unavailable"}</h3>
              <p>
                {unavailableReason ||
                  (eligible
                    ? "No prediction has been generated for this invoice yet."
                    : `Predictions are available once invoice processing is complete. Current status: ${titleCase(invoice.processing_status)}.`)}
              </p>
              {eligible && !unavailableReason && (
                <button className="button primary" onClick={generate} disabled={generating}>
                  {generating ? "Generating…" : predictionError ? "Retry prediction" : "Generate prediction"}
                </button>
              )}
              {predictionError && <p className="error-text" role="alert">{predictionError}</p>}
            </div>
          </div>
        )}
      </section>
    </div>
  </>;
}

/* ──────────────────────────────────────────────────── */

function ProofUploadPanel({
  invoiceId,
  currency,
  onVerified,
  onCancel,
}: {
  invoiceId: string;
  currency?: string | null;
  onVerified: () => Promise<void>;
  onCancel: () => void;
}) {
  const [file, setFile] = useState<File>();
  const [status, setStatus] = useState<"idle" | "uploading" | "processing" | "verified" | "needs_review" | "failed">("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [verifiedPayment, setVerifiedPayment] = useState<{ amount: number; reference?: string } | null>(null);
  const [extractedCandidate, setExtractedCandidate] = useState<PaymentProof["extracted_data"]>(null);
  const pollTimerRef = useRef<number | undefined>();

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) window.clearTimeout(pollTimerRef.current);
    };
  }, []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0];
    setFile(picked);
    setStatus("idle");
    setErrorMessage("");
  };

  const uploadAndVerify = async () => {
    if (!file) {
      setErrorMessage("Please select a payment proof document (PDF, CSV, or XLSX).");
      return;
    }

    const lower = file.name.toLowerCase();
    if (!lower.endsWith(".pdf") && !lower.endsWith(".csv") && !lower.endsWith(".xlsx")) {
      setErrorMessage("Unsupported file format. Please choose a PDF, CSV, or XLSX file.");
      return;
    }

    setStatus("uploading");
    setErrorMessage("");

    try {
      const uploadRes = await paymentsApi.uploadProof(invoiceId, file);
      setStatus("processing");

      // Begin polling proof status
      const pollStart = Date.now();
      const poll = async () => {
        try {
          const proof = await paymentsApi.getProof(invoiceId, uploadRes.proof_id);
          if (proof.status === "VERIFIED") {
            setStatus("verified");
            setVerifiedPayment({
              amount: proof.extracted_data?.amount ?? 0,
              reference: proof.extracted_data?.payment_reference || undefined,
            });
            await onVerified();
          } else if (proof.status === "NEEDS_REVIEW") {
            setStatus("needs_review");
            setErrorMessage(proof.error_message || "Evidence could not be confidently matched to this invoice.");
            setExtractedCandidate(proof.extracted_data);
          } else if (proof.status === "FAILED") {
            setStatus("failed");
            setErrorMessage(proof.error_message || "Verification failed to extract necessary payment data.");
          } else if (Date.now() - pollStart < 60000) {
            pollTimerRef.current = window.setTimeout(poll, 1500);
          } else {
            setStatus("failed");
            setErrorMessage("Verification timed out. You can check proof status below.");
          }
        } catch (pollErr) {
          setStatus("failed");
          setErrorMessage(normaliseError(pollErr).message);
        }
      };
      pollTimerRef.current = window.setTimeout(poll, 1200);
    } catch (err) {
      setStatus("failed");
      setErrorMessage(normaliseError(err).message);
    }
  };

  return (
    <div className="payment-form-panel proof-panel">
      <div className="payment-form-header">
        <h3>Upload payment proof</h3>
        <button type="button" className="icon-button" onClick={onCancel} aria-label="Cancel">
          <X size={18} />
        </button>
      </div>

      <p className="quiet">
        Upload bank receipt, NEFT confirmation, or proof spreadsheet (PDF, CSV, XLSX).
        The system verifies evidence before recording payment.
      </p>

      {status === "idle" && (
        <>
          <div className="proof-dropzone">
            <input
              id="proof-file-input"
              type="file"
              accept=".pdf,.csv,.xlsx,application/pdf,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={handleFileChange}
              style={{ display: "none" }}
            />
            <label htmlFor="proof-file-input" className="dropzone-label">
              <UploadCloud size={28} />
              <strong>{file ? file.name : "Choose a payment proof file"}</strong>
              <span>PDF receipt, CSV, or XLSX payment confirmation</span>
            </label>
          </div>
          {errorMessage && <p className="error-text" role="alert">{errorMessage}</p>}
          <button className="button primary full" onClick={uploadAndVerify} disabled={!file}>
            Upload and verify proof
          </button>
        </>
      )}

      {status === "uploading" && (
        <div className="proof-status-card">
          <LoaderCircle className="spin" size={24} />
          <div>
            <strong>Uploading document…</strong>
            <span>Saving securely in tenant storage</span>
          </div>
        </div>
      )}

      {status === "processing" && (
        <div className="proof-status-card">
          <LoaderCircle className="spin" size={24} />
          <div>
            <strong>Verifying payment proof…</strong>
            <span>Extracting date, amount, reference, and verifying against invoice facts</span>
          </div>
        </div>
      )}

      {status === "verified" && (
        <div className="proof-status-card verified">
          <CheckCircle2 size={24} />
          <div>
            <strong>Proof verified & payment recorded!</strong>
            <span>
              Recorded payment of {verifiedPayment ? money(verifiedPayment.amount, currency) : "the invoice amount"} with provenance &ldquo;Proof verified&rdquo;.
            </span>
          </div>
        </div>
      )}

      {status === "needs_review" && (
        <div className="proof-status-card review">
          <AlertTriangle size={24} />
          <div>
            <strong>Payment proof requires review</strong>
            <p className="error-text" style={{ margin: "4px 0" }}>{errorMessage}</p>
            {extractedCandidate && (
              <div className="candidate-box">
                <small>Extracted evidence:</small>
                <ul>
                  {extractedCandidate.invoice_reference && (
                    <li>Invoice ref: <b>{extractedCandidate.invoice_reference}</b></li>
                  )}
                  {extractedCandidate.amount && (
                    <li>Amount: <b>{money(extractedCandidate.amount, currency)}</b></li>
                  )}
                  {extractedCandidate.payment_date && (
                    <li>Date: <b>{extractedCandidate.payment_date.slice(0, 10)}</b></li>
                  )}
                  {extractedCandidate.customer_name && (
                    <li>Payer: <b>{extractedCandidate.customer_name}</b></li>
                  )}
                </ul>
              </div>
            )}
            <small className="quiet">No payment record was automatically created.</small>
          </div>
        </div>
      )}

      {status === "failed" && (
        <div className="proof-status-card failed">
          <AlertCircle size={24} />
          <div>
            <strong>Verification failed</strong>
            <p className="error-text" style={{ margin: "4px 0" }}>{errorMessage}</p>
            <button
              className="text-button"
              style={{ marginTop: "6px" }}
              onClick={() => {
                setStatus("idle");
                setFile(undefined);
              }}
            >
              Try another file
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ──────────────────────────────────────────────────── */

function PaymentForm({
  invoiceId,
  invoiceAmount,
  outstandingBalance,
  currency,
  onSuccess,
  onCancel,
}: {
  invoiceId: string;
  invoiceAmount: number;
  outstandingBalance: number;
  currency?: string | null;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const [paymentDate, setPaymentDate] = useState(today);
  const [amount, setAmount] = useState(outstandingBalance > 0 ? String(outstandingBalance) : "");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    const numAmount = Number(amount);
    if (!amount || isNaN(numAmount) || numAmount <= 0) {
      setError("Please enter a valid payment amount greater than zero.");
      return;
    }
    if (!paymentDate) {
      setError("Please enter a valid payment date.");
      return;
    }
    if (paymentDate > today) {
      setError("Payment date cannot be in the future.");
      return;
    }

    setSubmitting(true);
    try {
      const data: ManualPaymentRequest = {
        payment_date: paymentDate,
        amount: numAmount,
      };
      if (reference.trim()) data.reference = reference.trim();
      if (note.trim()) data.note = note.trim();

      await paymentsApi.recordManual(invoiceId, data);
      setSuccess(true);
      setTimeout(onSuccess, 800);
    } catch (err: any) {
      const apiErr = normaliseError(err);
      const raw = err?.response?.data?.detail;
      setError(typeof raw === "string" ? raw : apiErr.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (success) {
    return (
      <div className="payment-form-panel success-state">
        <CheckCircle2 size={22} />
        <strong>Payment recorded</strong>
      </div>
    );
  }

  return (
    <form className="payment-form-panel" onSubmit={submit}>
      <div className="payment-form-header">
        <h3>Record manual payment</h3>
        <button type="button" className="icon-button" onClick={onCancel} aria-label="Cancel">
          <X size={18} />
        </button>
      </div>
      <p className="quiet">Outstanding: {money(outstandingBalance, currency)} of {money(invoiceAmount, currency)}</p>
      <label>
        <span>Payment date</span>
        <input type="date" value={paymentDate} max={today} onChange={(e) => setPaymentDate(e.target.value)} required />
      </label>
      <label>
        <span>Amount</span>
        <input type="number" step="0.01" min="0.01" placeholder="0.00" value={amount} onChange={(e) => setAmount(e.target.value)} required />
      </label>
      <label>
        <span>Payment reference <small>(optional)</small></span>
        <input type="text" maxLength={128} placeholder="UTR, cheque no., etc." value={reference} onChange={(e) => setReference(e.target.value)} />
      </label>
      <label>
        <span>Note <small>(optional)</small></span>
        <input type="text" maxLength={512} placeholder="Additional details" value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {error && <p className="error-text" role="alert">{error}</p>}
      <button type="submit" className="button primary full" disabled={submitting}>
        {submitting ? "Recording…" : "Confirm payment"}
      </button>
    </form>
  );
}

function PaymentRow({ payment, currency }: { payment: PaymentRecord; currency?: string | null }) {
  const provenanceLabel = payment.provenance === "proof_verified"
    ? "Proof verified"
    : payment.provenance === "manual"
    ? "Manual"
    : payment.provenance === "import"
    ? "Imported"
    : titleCase(payment.provenance || "unknown");

  const badgeKind = payment.provenance === "proof_verified"
    ? "good"
    : payment.provenance === "manual"
    ? "pending"
    : "neutral";

  return (
    <li className="payment-row">
      <div>
        <strong>{money(payment.amount, currency)}</strong>
        <span>{date(payment.payment_date.slice(0, 10))}</span>
      </div>
      <div>
        <span className={`badge ${badgeKind}`}>
          <i />{provenanceLabel}
        </span>
        {payment.reference && <small>{payment.reference}</small>}
        {payment.note && <small className="quiet">{payment.note}</small>}
      </div>
    </li>
  );
}

function ProofRow({ proof }: { proof: PaymentProof; currency?: string | null }) {
  const isDoc = proof.original_filename.toLowerCase().endsWith(".pdf");
  const Icon = isDoc ? FileText : FileSpreadsheet;

  return (
    <li className="proof-row">
      <div>
        <Icon size={16} />
        <div>
          <strong>{proof.original_filename}</strong>
          <span>Uploaded {date(proof.created_at.slice(0, 10))}</span>
          {proof.error_message && (
            <small className="error-text">{proof.error_message}</small>
          )}
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <StatusBadge value={proof.status} />
      </div>
    </li>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}
