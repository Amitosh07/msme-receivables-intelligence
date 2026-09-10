import { CheckCircle2, FileSpreadsheet, FileText, UploadCloud } from "../components/Icons";
import { ChangeEvent, useCallback, useEffect, useRef, useState } from "react";
import { invoicesApi, normaliseError, paymentsApi } from "../lib/api";
import type { InvoiceDocument, PaymentImport } from "../lib/types";
import { StatusBadge } from "../components/Badges";
import { date } from "../lib/format";

type UploadStatus = "idle" | "uploading" | "success" | "error";
type PaymentStatus = "idle" | "previewing" | "ready" | "uploading" | "success" | "error";
const ACTIVE = new Set(["PENDING", "PROCESSING"]);
const POLL_LIMIT_MS = 120_000;

export function UploadPage() {
  const [invoiceFile, setInvoiceFile] = useState<File>();
  const [invoiceStatus, setInvoiceStatus] = useState<UploadStatus>("idle");
  const [invoiceMessage, setInvoiceMessage] = useState("");
  const [documents, setDocuments] = useState<InvoiceDocument[]>([]);
  const [documentError, setDocumentError] = useState("");
  const [pollGeneration, setPollGeneration] = useState(0);
  const previousStatuses = useRef(new Map<string, string>());

  const [paymentFile, setPaymentFile] = useState<File>();
  const [paymentStatus, setPaymentStatus] = useState<PaymentStatus>("idle");
  const [paymentError, setPaymentError] = useState("");
  const [summary, setSummary] = useState<PaymentImport>();

  const loadDocs = useCallback(async () => {
    const next = await invoicesApi.documents();
    const completed = next.some((doc) => {
      const before = previousStatuses.current.get(doc.id);
      return before && ACTIVE.has(before) && !ACTIVE.has(doc.processing_status);
    });
    previousStatuses.current = new Map(next.map((doc) => [doc.id, doc.processing_status]));
    setDocuments(next);
    setDocumentError("");
    if (completed) window.dispatchEvent(new Event("msme:data-changed"));
    return next;
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const started = Date.now();
    const poll = async () => {
      try {
        const docs = await loadDocs();
        if (!cancelled && docs.some((doc) => ACTIVE.has(doc.processing_status)) && Date.now() - started < POLL_LIMIT_MS) {
          timer = window.setTimeout(poll, 3000);
        }
      } catch (error) {
        if (cancelled) return;
        setDocumentError(normaliseError(error).message);
        if (Date.now() - started < POLL_LIMIT_MS) timer = window.setTimeout(poll, 8000);
      }
    };
    void poll();
    return () => { cancelled = true; if (timer) window.clearTimeout(timer); };
  }, [loadDocs, pollGeneration]);

  const pickInvoice = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    setInvoiceFile(file);
    setInvoiceStatus("idle");
    setInvoiceMessage(file && file.type && file.type !== "application/pdf" ? "Please select a PDF invoice." : "");
  };

  const sendInvoice = async () => {
    if (!invoiceFile || !invoiceFile.name.toLowerCase().endsWith(".pdf")) {
      setInvoiceMessage("The uploaded file is not a valid PDF.");
      setInvoiceStatus("error");
      return;
    }
    setInvoiceStatus("uploading");
    setInvoiceMessage("");
    try {
      await invoicesApi.upload(invoiceFile);
      setInvoiceStatus("success");
      setInvoiceMessage("Uploaded. Processing will update automatically below.");
      setInvoiceFile(undefined);
      setPollGeneration((value) => value + 1);
    } catch (error) {
      setInvoiceStatus("error");
      setInvoiceMessage(normaliseError(error).message);
    }
  };

  const pickPayment = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    setPaymentFile(file);
    setPaymentError(file && !file.name.toLowerCase().endsWith(".csv") ? "Payment history imports currently accept CSV files only." : "");
    setPaymentStatus("idle");
    setSummary(undefined);
  };

  const previewPayment = async () => {
    if (!paymentFile || !paymentFile.name.toLowerCase().endsWith(".csv")) {
      setPaymentError("Please select a CSV payment history file.");
      setPaymentStatus("error");
      return;
    }
    setPaymentStatus("previewing");
    setPaymentError("");
    try {
      setSummary(await paymentsApi.preview(paymentFile));
      setPaymentStatus("ready");
    } catch (error) {
      setPaymentStatus("error");
      setPaymentError(normaliseError(error).message);
    }
  };

  const sendPayment = async () => {
    if (!paymentFile || paymentStatus !== "ready") return;
    setPaymentStatus("uploading");
    setPaymentError("");
    try {
      setSummary(await paymentsApi.upload(paymentFile));
      setPaymentStatus("success");
      window.dispatchEvent(new Event("msme:data-changed"));
    } catch (error) {
      setPaymentStatus("error");
      setPaymentError(normaliseError(error).message);
    }
  };

  return <>
    <section className="page-heading"><div><p className="eyebrow">Data intake</p><h2>Import records</h2><p>Invoice documents process asynchronously. Payment history is previewed before import.</p></div></section>
    <div className="upload-grid">
      <UploadCard icon={FileText} title="Invoice PDF" note="PDF only. Status changes from Pending to Processing, then Ready or Failed.">
        <input id="invoice-file" className="file-input" type="file" accept="application/pdf,.pdf" onChange={pickInvoice} />
        <label htmlFor="invoice-file" className="dropzone"><UploadCloud size={25} /><strong>{invoiceFile?.name || "Choose an invoice PDF"}</strong><span>PDF, up to the server’s configured size limit</span></label>
        {invoiceMessage && <UploadMessage status={invoiceStatus} message={invoiceMessage} />}
        <button className="button primary full" onClick={sendInvoice} disabled={!invoiceFile || invoiceStatus === "uploading"}>{invoiceStatus === "uploading" ? "Uploading…" : "Upload invoice"}</button>
      </UploadCard>
      <UploadCard icon={FileSpreadsheet} title="Payment history" note="CSV columns: invoice_number, payment_date, and payment_amount. Common header and value formats are accepted.">
        <input id="payment-file" className="file-input" type="file" accept=".csv,text/csv" onChange={pickPayment} />
        <label htmlFor="payment-file" className="dropzone"><UploadCloud size={25} /><strong>{paymentFile?.name || "Choose a payment history CSV"}</strong><span>Validate and preview before anything is saved</span></label>
        {paymentError && <UploadMessage status="error" message={paymentError} />}
        {summary && <ImportSummary summary={summary} />}
        {paymentStatus === "ready"
          ? <button className="button primary full" onClick={sendPayment}>Import {summary?.imported || 0} valid payment{summary?.imported === 1 ? "" : "s"}</button>
          : <button className="button primary full" onClick={previewPayment} disabled={!paymentFile || paymentStatus === "previewing" || paymentStatus === "uploading"}>{paymentStatus === "previewing" ? "Validating…" : paymentStatus === "success" ? "Imported" : "Validate and preview"}</button>}
      </UploadCard>
    </div>
    <section className="document-status">
      <div className="panel-heading"><div><h2>Recent document processing</h2><p>Updates automatically while work is active, for up to two minutes.</p></div><button className="text-button" onClick={() => { void loadDocs(); setPollGeneration((value) => value + 1); }}>Refresh</button></div>
      {documentError && <p className="error-text" role="alert">{documentError}</p>}
      {documents.length ? <ul>{documents.slice(0, 8).map((doc) => <li key={doc.id}><div><strong>{doc.original_filename}</strong><span>Uploaded {date(doc.created_at.slice(0, 10))}</span>{doc.processing_status === "ERROR" && doc.error_message && <small className="error-text">{doc.error_message}</small>}</div><StatusBadge value={doc.processing_status} /></li>)}</ul> : <p className="quiet">No invoice documents uploaded yet.</p>}
    </section>
  </>;
}

function ImportSummary({ summary }: { summary: PaymentImport }) {
  return <div className="import-summary"><CheckCircle2 size={19} /><span><strong>{summary.preview ? "Preview" : `${summary.imported} imported`}</strong> · {summary.total_rows} rows · {summary.valid_rows} valid · {summary.matched} matched · {summary.unmatched} unmatched · {summary.duplicates_in_file} repeated in file · {summary.duplicates_existing} already imported · {summary.rejected} rejected</span>{summary.errors.length > 0 && <small>Row {summary.errors[0].row_number}: {summary.errors[0].reason}</small>}</div>;
}
function UploadCard({ icon: Icon, title, note, children }: { icon: typeof FileText; title: string; note: string; children: React.ReactNode }) { return <section className="upload-card"><Icon size={21} /><h2>{title}</h2><p>{note}</p>{children}</section>; }
function UploadMessage({ status, message }: { status: UploadStatus; message: string }) { return <p className={status === "error" ? "upload-message error-text" : "upload-message"} role={status === "error" ? "alert" : "status"}>{status === "success" && <CheckCircle2 size={16} />}{message}</p>; }
