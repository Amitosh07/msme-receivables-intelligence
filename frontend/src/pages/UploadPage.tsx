import { CheckCircle2, FileText, UploadCloud } from "../components/Icons";
import { ChangeEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { invoicesApi, normaliseError } from "../lib/api";
import type { InvoiceDocument } from "../lib/types";
import { StatusBadge } from "../components/Badges";
import { date } from "../lib/format";
import { subscribeProcessing, watchInvoiceProcessing, type ProcessingUpdate } from "../lib/processing";

type UploadStatus = "idle" | "uploading" | "processing" | "success" | "error";

function describeProcessing(update: ProcessingUpdate): { status: UploadStatus; message: string } {
  if (update.state === "queued" || update.state === "processing") {
    return { status: "processing", message: `Processing ${update.filename}…` };
  }
  if (update.registered) {
    return { status: "success", message: "Invoice processed. Overview, invoices and customers are up to date." };
  }
  const seller = update.identity?.seller_name;
  const buyer = update.identity?.buyer_name;
  const parties = [seller ? `Seller: ${seller}` : "", buyer ? `Buyer: ${buyer}` : ""].filter(Boolean).join(" · ");
  const reason = update.identity?.review_reason || update.errorMessage || "Processing failed. The invoice was not registered.";
  return { status: "error", message: parties ? `${reason} ${parties}` : reason };
}

export function UploadPage() {
  const [invoiceFile, setInvoiceFile] = useState<File>();
  const [invoiceStatus, setInvoiceStatus] = useState<UploadStatus>("idle");
  const [invoiceMessage, setInvoiceMessage] = useState("");
  const [documents, setDocuments] = useState<InvoiceDocument[]>([]);
  const [documentError, setDocumentError] = useState("");
  const activeTask = useRef<string | null>(null);

  const loadDocs = useCallback(async () => {
    const next = await invoicesApi.documents();
    setDocuments(next);
    setDocumentError("");
    return next;
  }, []);

  useEffect(() => {
    let cancelled = false;
    void loadDocs().catch((error) => {
      if (!cancelled) setDocumentError(normaliseError(error).message);
    });
    return () => { cancelled = true; };
  }, [loadDocs]);

  useEffect(() => subscribeProcessing((update) => {
    if (update.origin !== "CURRENT") return;
    if (activeTask.current && update.taskId !== activeTask.current) return;
    const next = describeProcessing(update);
    setInvoiceStatus(next.status);
    setInvoiceMessage(next.message);
    void loadDocs().catch((error) => setDocumentError(normaliseError(error).message));
  }), [loadDocs]);

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
      const uploaded = await invoicesApi.upload(invoiceFile);
      activeTask.current = uploaded.task_id ?? null;
      watchInvoiceProcessing({ ...uploaded, origin: uploaded.origin || "CURRENT" });
      setInvoiceStatus("processing");
      setInvoiceMessage("File received. Reading the invoice…");
      setInvoiceFile(undefined);
      void loadDocs();
    } catch (error) {
      setInvoiceStatus("error");
      setInvoiceMessage(normaliseError(error).message);
    }
  };

  return <>
    <section className="intake-note">
      <p>
        <b>For invoices you are still collecting.</b> Once processed they appear in Invoices and count toward
        Outstanding, Overdue and Due soon on the Overview.
      </p>
      <p>
        Adding past, already-settled invoices or payment history so the model can learn how each customer pays?
        Use <Link to="/historical">Historical Data</Link> instead — those records are never counted as money to collect.
      </p>
    </section>
    <div className="upload-grid" style={{ gridTemplateColumns: "1fr" }}>
      <UploadCard icon={FileText} title="Current invoice PDF" note="Customer, invoice number, amount and dates are read from the PDF; anything unclear is flagged for review.">

        <input id="invoice-file" className="file-input" type="file" accept="application/pdf,.pdf" onChange={pickInvoice} />
        <label htmlFor="invoice-file" className="dropzone">
          <UploadCloud size={25} />
          <strong>{invoiceFile?.name || "Choose an invoice PDF"}</strong>
          <span>PDF, up to the server’s configured size limit</span>
        </label>
        {invoiceMessage && <UploadMessage status={invoiceStatus} message={invoiceMessage} />}
        <button className="button primary full" onClick={sendInvoice} disabled={!invoiceFile || invoiceStatus === "uploading"}>
          {invoiceStatus === "uploading" ? "Uploading…" : invoiceStatus === "processing" ? "Processing…" : "Upload invoice"}
        </button>
      </UploadCard>
    </div>
    <section className="document-status">
      <div className="panel-heading">
        <div>
          <h2>Recent document processing</h2>
        </div>
        <button className="text-button" onClick={() => { void loadDocs().catch((error) => setDocumentError(normaliseError(error).message)); }}>Refresh</button>
      </div>
      {documentError && <p className="error-text" role="alert">{documentError}</p>}
      {documents.length ? (
        <ul>
          {documents.slice(0, 8).map((doc) => (
            <li key={doc.id}>
              <div>
                <strong>{doc.original_filename}</strong>
                <span>Uploaded {date(doc.created_at.slice(0, 10))}</span>
                {doc.processing_status === "ERROR" && doc.error_message && <small className="error-text">{doc.error_message}</small>}
              </div>
              <StatusBadge value={doc.processing_status} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="quiet">No invoice documents uploaded yet.</p>
      )}
    </section>
  </>;
}

function UploadCard({ icon: Icon, title, note, children }: { icon: typeof FileText; title: string; note: string; children: React.ReactNode }) {
  return <section className="upload-card"><Icon size={21} /><h2>{title}</h2><p>{note}</p>{children}</section>;
}
function UploadMessage({ status, message }: { status: UploadStatus; message: string }) {
  return <p className={status === "error" ? "upload-message error-text" : "upload-message"} role={status === "error" ? "alert" : "status"}>{status === "success" && <CheckCircle2 size={16} />}{message}</p>;
}
