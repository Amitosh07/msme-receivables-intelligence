import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CalendarClock,
  CheckCircle2,
  FileSpreadsheet,
  FileText,
  LoaderCircle,
  Plus,
  Search,
  UploadCloud,
  X,
} from "../components/Icons";
import { StatusBadge } from "../components/Badges";
import { historicalApi, normaliseError } from "../lib/api";
import { date, money } from "../lib/format";
import type {
  HistoricalCompanyDetail,
  HistoricalCompanySummary,
  HistoricalInvoiceItem,
  PaymentImport,
} from "../lib/types";

export function HistoricalPage() {
  const { companyId } = useParams<{ companyId?: string }>();

  if (companyId) {
    return <HistoricalCompanyDetailView companyId={companyId} />;
  }

  return <HistoricalCompanyListView />;
}

// ----------------------------------------------------------------------
// 1. Company List View
// ----------------------------------------------------------------------
function HistoricalCompanyListView() {
  const [companies, setCompanies] = useState<HistoricalCompanySummary[]>([]);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  // Debounce search query
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search.trim());
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const loadCompanies = useCallback(async (query: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await historicalApi.listCompanies(query || undefined);
      setCompanies(data);
    } catch (err) {
      setError(normaliseError(err).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCompanies(debouncedSearch);
  }, [debouncedSearch, loadCompanies]);

  // Aggregate metrics
  const stats = useMemo(() => {
    const totalCompanies = companies.length;
    const totalInvoices = companies.reduce((sum, c) => sum + c.historical_invoice_count, 0);
    const totalReceivables = companies.reduce((sum, c) => sum + c.total_amount, 0);
    const totalSettled = companies.reduce((sum, c) => sum + c.total_paid, 0);
    return { totalCompanies, totalInvoices, totalReceivables, totalSettled };
  }, [companies]);

  return (
    <>
      <section className="page-heading">
        <div>
          <p className="eyebrow">HISTORICAL DATA</p>
          <h2>Historical company workspace</h2>
          <p>
            Historical records establish baseline payment behavior, customer payment timeliness patterns,
            and machine learning training context without affecting current receivables aging.
          </p>
        </div>
      </section>

      {/* Metric Cards */}
      <div className="metric-grid">
        <div className="metric">
          <CalendarClock size={22} />
          <span>COMPANIES WITH HISTORY</span>
          <strong>{stats.totalCompanies}</strong>
          <small>Distinct customers recorded</small>
        </div>
        <div className="metric">
          <FileText size={22} />
          <span>HISTORICAL INVOICES</span>
          <strong>{stats.totalInvoices}</strong>
          <small>Total past invoices on file</small>
        </div>
        <div className="metric">
          <FileSpreadsheet size={22} />
          <span>RECEIVABLES TRACKED</span>
          <strong>{money(stats.totalReceivables, "INR")}</strong>
          <small>Cumulative historical volume</small>
        </div>
        <div className="metric">
          <CheckCircle2 size={22} />
          <span>SETTLED REVENUE</span>
          <strong>{money(stats.totalSettled, "INR")}</strong>
          <small>Recorded historical payments</small>
        </div>
      </div>

      {/* Search & Filter Controls */}
      <div className="historical-search-row">
        <div className="search" style={{ maxWidth: 460 }}>
          <Search size={16} />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search companies by name..."
            aria-label="Search companies by name"
          />
          {search && (
            <button
              className="icon-button"
              onClick={() => setSearch("")}
              aria-label="Clear search"
            >
              <X size={15} />
            </button>
          )}
        </div>
        <button
          className="button"
          onClick={() => void loadCompanies(debouncedSearch)}
          title="Refresh company listing"
        >
          Refresh
        </button>
      </div>

      {/* Content State */}
      {loading ? (
        <div className="state">
          <LoaderCircle size={32} className="spin" />
          <span>Loading historical company records…</span>
        </div>
      ) : error ? (
        <div className="state error">
          <AlertCircle size={32} />
          <div>
            <strong>Unable to load historical records</strong>
            <span>{error}</span>
          </div>
          <button className="button primary" onClick={() => void loadCompanies(debouncedSearch)}>
            Try again
          </button>
        </div>
      ) : companies.length === 0 ? (
        <div className="empty panel">
          <CalendarClock size={40} />
          <h2>No historical records found</h2>
          <p>
            {debouncedSearch
              ? `No companies matched "${debouncedSearch}". Try a different name or clear the search.`
              : "No historical company records have been ingested yet. Ingest historical invoices or payment history to start establishing intelligence baselines."}
          </p>
          {debouncedSearch && (
            <button className="button" onClick={() => setSearch("")}>
              Clear search
            </button>
          )}
        </div>
      ) : (
        <div className="company-card-grid">
          {companies.map((company) => (
            <article
              key={company.id}
              className="company-card"
              onClick={() => navigate(`/historical/${company.id}`)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  navigate(`/historical/${company.id}`);
                }
              }}
            >
              <div className="company-card-header">
                <div>
                  <h3 className="company-card-title">{company.display_name}</h3>
                  <div className="company-card-meta">
                    {company.gstin && <span className="badge">GSTIN: {company.gstin}</span>}
                    {company.customer_ref && (
                      <span className="badge">Ref: {company.customer_ref}</span>
                    )}
                  </div>
                </div>
                <span className="badge good">
                  {company.historical_invoice_count} invoice
                  {company.historical_invoice_count === 1 ? "" : "s"}
                </span>
              </div>

              <dl className="company-card-metrics">
                <div>
                  <dt>Historical Invoiced</dt>
                  <dd className="numeric">{money(company.total_amount, "INR")}</dd>
                </div>
                <div>
                  <dt>Settled Payments</dt>
                  <dd className="numeric">{money(company.total_paid, "INR")}</dd>
                </div>
                <div>
                  <dt>Outstanding</dt>
                  <dd className="numeric">{money(company.outstanding_balance, "INR")}</dd>
                </div>
                <div>
                  <dt>Last Invoice</dt>
                  <dd>{date(company.last_invoice_date)}</dd>
                </div>
              </dl>

              <div className="company-card-footer">
                <span>Last payment: {date(company.last_payment_date)}</span>
                <span className="invoice-link">Open workspace →</span>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}

// ----------------------------------------------------------------------
// 2. Company Detail Workspace View
// ----------------------------------------------------------------------
function HistoricalCompanyDetailView({ companyId }: { companyId: string }) {
  const [detail, setDetail] = useState<HistoricalCompanyDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modal states
  const [showUploadInvoiceModal, setShowUploadInvoiceModal] = useState(false);
  const [showManualPaymentModal, setShowManualPaymentModal] = useState(false);
  const [showImportPaymentModal, setShowImportPaymentModal] = useState(false);
  const [selectedInvoice, setSelectedInvoice] = useState<HistoricalInvoiceItem | null>(null);

  const loadDetail = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await historicalApi.getCompany(companyId);
      setDetail(data);
    } catch (err) {
      setError(normaliseError(err).message);
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  const openPaymentModalFor = (invoice: HistoricalInvoiceItem) => {
    setSelectedInvoice(invoice);
    setShowManualPaymentModal(true);
  };

  if (loading) {
    return (
      <div className="state">
        <LoaderCircle size={32} className="spin" />
        <span>Loading company workspace…</span>
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div>
        <Link to="/historical" className="back">
          <ArrowLeft size={16} /> Back to companies
        </Link>
        <div className="state error">
          <AlertCircle size={32} />
          <div>
            <strong>Company workspace not found</strong>
            <span>{error || "The requested historical workspace could not be loaded."}</span>
          </div>
          <button className="button primary" onClick={() => void loadDetail()}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <>
      <Link to="/historical" className="back">
        <ArrowLeft size={16} /> Back to historical companies
      </Link>

      <section className="historical-topbar">
        <div>
          <p className="eyebrow">COMPANY WORKSPACE</p>
          <h2 style={{ fontSize: 26, margin: 0 }}>{detail.display_name}</h2>
          <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
            <span className="badge">Normalized: {detail.normalized_name}</span>
            {detail.gstin && <span className="badge">GSTIN: {detail.gstin}</span>}
            {detail.customer_ref && <span className="badge">Ref: {detail.customer_ref}</span>}
          </div>
        </div>

        <div className="workspace-actions">
          <button
            className="button"
            onClick={() => setShowUploadInvoiceModal(true)}
          >
            <Plus size={16} /> Add historical invoice
          </button>
          <button
            className="button"
            onClick={() => {
              setSelectedInvoice(detail.invoices[0] || null);
              setShowManualPaymentModal(true);
            }}
            disabled={detail.invoices.length === 0}
            title={detail.invoices.length === 0 ? "Add an invoice first" : undefined}
          >
            <CalendarClock size={16} /> Add payment date manually
          </button>
          <button
            className="button primary"
            onClick={() => setShowImportPaymentModal(true)}
          >
            <UploadCloud size={16} /> Import payment history
          </button>
        </div>
      </section>

      {/* Summary Metrics */}
      <div className="metric-grid">
        <div className="metric">
          <FileText size={20} />
          <span>HISTORICAL INVOICES</span>
          <strong>{detail.historical_invoice_count}</strong>
          <small>Total recorded past invoices</small>
        </div>
        <div className="metric">
          <FileSpreadsheet size={20} />
          <span>TOTAL RECEIVABLES</span>
          <strong>{money(detail.total_amount, "INR")}</strong>
          <small>Cumulative invoiced value</small>
        </div>
        <div className="metric">
          <CheckCircle2 size={20} />
          <span>TOTAL SETTLED</span>
          <strong>{money(detail.total_paid, "INR")}</strong>
          <small>Recorded historical payments</small>
        </div>
        <div className={`metric ${detail.outstanding_balance > 0 ? "urgent" : ""}`}>
          <CalendarClock size={20} />
          <span>OUTSTANDING BALANCE</span>
          <strong>{money(detail.outstanding_balance, "INR")}</strong>
          <small>Unsettled past amount</small>
        </div>
      </div>

      {/* Historical Invoices Table */}
      <div className="table-section panel" style={{ padding: 0 }}>
        <div className="panel-heading" style={{ padding: "18px 22px", margin: 0, borderBottom: "1px solid #edf0ec" }}>
          <div>
            <h2 style={{ fontSize: 16, margin: 0 }}>Historical Invoices</h2>
            <p style={{ margin: 0 }}>
              All past invoices with factual payment timeline, paid amounts, and settlement status.
            </p>
          </div>
          <button className="text-button" onClick={() => void loadDetail()}>
            Refresh table
          </button>
        </div>

        {detail.invoices.length === 0 ? (
          <div className="empty" style={{ minHeight: 200, padding: 30 }}>
            <FileText size={32} />
            <h2>No historical invoices recorded</h2>
            <p>
              Upload a historical invoice PDF or import payment history to populate records for{" "}
              {detail.display_name}.
            </p>
            <button
              className="button primary"
              onClick={() => setShowUploadInvoiceModal(true)}
            >
              <Plus size={16} /> Add historical invoice
            </button>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Invoice #</th>
                  <th>Invoice Date</th>
                  <th>Due Date</th>
                  <th>Amount</th>
                  <th>Payment Date</th>
                  <th>Total Paid</th>
                  <th>Outstanding</th>
                  <th>Payment Status</th>
                  <th style={{ textAlign: "right" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {detail.invoices.map((inv) => (
                  <tr key={inv.id}>
                    <td>
                      <strong className="invoice-link">{inv.invoice_number}</strong>
                    </td>
                    <td>{date(inv.invoice_date)}</td>
                    <td>{date(inv.due_date)}</td>
                    <td className="numeric">{money(inv.amount, inv.currency || "INR")}</td>
                    <td>
                      {inv.payment_date ? (
                        <span style={{ color: "#1d6b3a", fontWeight: 600 }}>
                          {date(inv.payment_date)}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="numeric">{money(inv.total_paid, inv.currency || "INR")}</td>
                    <td className="numeric">{money(inv.outstanding_balance, inv.currency || "INR")}</td>
                    <td>
                      <StatusBadge value={inv.payment_status} />
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        className="text-button"
                        onClick={() => openPaymentModalFor(inv)}
                        title="Record payment for this invoice"
                      >
                        Add payment
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modals */}
      {showUploadInvoiceModal && (
        <UploadHistoricalInvoiceModal
          companyId={companyId}
          companyName={detail.display_name}
          onClose={() => setShowUploadInvoiceModal(false)}
          onSuccess={() => {
            setShowUploadInvoiceModal(false);
            void loadDetail();
          }}
        />
      )}

      {showManualPaymentModal && (
        <ManualPaymentModal
          companyId={companyId}
          invoices={detail.invoices}
          initialInvoiceId={selectedInvoice?.id}
          onClose={() => {
            setShowManualPaymentModal(false);
            setSelectedInvoice(null);
          }}
          onSuccess={() => {
            setShowManualPaymentModal(false);
            setSelectedInvoice(null);
            void loadDetail();
          }}
        />
      )}

      {showImportPaymentModal && (
        <ImportPaymentModal
          companyId={companyId}
          companyName={detail.display_name}
          onClose={() => setShowImportPaymentModal(false)}
          onSuccess={() => {
            setShowImportPaymentModal(false);
            void loadDetail();
          }}
        />
      )}
    </>
  );
}

// ----------------------------------------------------------------------
// 3. Modal: Add Historical Invoice (PDF)
// ----------------------------------------------------------------------
function UploadHistoricalInvoiceModal({
  companyId,
  companyName,
  onClose,
  onSuccess,
}: {
  companyId: string;
  companyName: string;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) {
      if (!selected.name.toLowerCase().endsWith(".pdf")) {
        setError("Only PDF files are supported for invoice uploads.");
        setFile(null);
      } else {
        setError(null);
        setFile(selected);
      }
    }
  };

  const handleUpload = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      await historicalApi.uploadInvoice(companyId, file);
      onSuccess();
    } catch (err) {
      setError(normaliseError(err).message);
      setUploading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Add Historical Invoice</h3>
          <button className="icon-button" onClick={onClose} aria-label="Close modal">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleUpload}>
          <div className="modal-body">
            <p style={{ margin: 0, fontSize: 13, color: "#60706a" }}>
              Upload an invoice PDF explicitly bound to <strong>{companyName}</strong>. The document
              origin is recorded as <code>HISTORICAL</code> and will be parsed asynchronously.
            </p>

            <div className="form-field">
              <label htmlFor="historical-invoice-file">Invoice PDF Document</label>
              <input
                id="historical-invoice-file"
                className="file-input"
                type="file"
                accept="application/pdf,.pdf"
                onChange={handleFileChange}
              />
              <label htmlFor="historical-invoice-file" className="dropzone" style={{ minHeight: 110 }}>
                <UploadCloud size={24} />
                <strong>{file ? file.name : "Choose an invoice PDF"}</strong>
                <span>PDF format only</span>
              </label>
            </div>

            {error && (
              <p className="error-text" role="alert" style={{ fontSize: 12, margin: 0 }}>
                {error}
              </p>
            )}
          </div>

          <div className="modal-footer">
            <button type="button" className="button" onClick={onClose} disabled={uploading}>
              Cancel
            </button>
            <button
              type="submit"
              className="button primary"
              disabled={!file || uploading}
            >
              {uploading ? (
                <>
                  <LoaderCircle size={16} className="spin" /> Uploading…
                </>
              ) : (
                "Upload Historical Invoice"
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// 4. Modal: Add Payment Date Manually
// ----------------------------------------------------------------------
function ManualPaymentModal({
  companyId,
  invoices,
  initialInvoiceId,
  onClose,
  onSuccess,
}: {
  companyId: string;
  invoices: HistoricalInvoiceItem[];
  initialInvoiceId?: string;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string>(
    initialInvoiceId || invoices[0]?.id || ""
  );

  const selectedInv = useMemo(
    () => invoices.find((inv) => inv.id === selectedId),
    [invoices, selectedId]
  );

  const [paymentDate, setPaymentDate] = useState<string>(
    new Date().toISOString().slice(0, 10)
  );
  const [amount, setAmount] = useState<string>(
    selectedInv ? String(selectedInv.outstanding_balance > 0 ? selectedInv.outstanding_balance : selectedInv.amount) : ""
  );
  const [reference, setReference] = useState<string>("");
  const [note, setNote] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // When selected invoice changes, default amount to outstanding or total
  const onInvoiceChange = (newId: string) => {
    setSelectedId(newId);
    const target = invoices.find((inv) => inv.id === newId);
    if (target) {
      setAmount(String(target.outstanding_balance > 0 ? target.outstanding_balance : target.amount));
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!selectedId) {
      setError("Please select an invoice.");
      return;
    }
    const numAmount = parseFloat(amount);
    if (isNaN(numAmount) || numAmount <= 0) {
      setError("Payment amount must be a positive number.");
      return;
    }
    if (!paymentDate) {
      setError("Please enter a valid payment date.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await historicalApi.recordPayment(companyId, selectedId, {
        payment_date: paymentDate,
        amount: numAmount,
        reference: reference.trim() || undefined,
        note: note.trim() || undefined,
      });
      onSuccess();
    } catch (err) {
      setError(normaliseError(err).message);
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Record Manual Payment</h3>
          <button className="icon-button" onClick={onClose} aria-label="Close modal">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <div className="form-field">
              <label htmlFor="invoice-select">Target Historical Invoice</label>
              <select
                id="invoice-select"
                value={selectedId}
                onChange={(e) => onInvoiceChange(e.target.value)}
                required
              >
                {invoices.map((inv) => (
                  <option key={inv.id} value={inv.id}>
                    Invoice #{inv.invoice_number} — Total: {money(inv.amount, inv.currency)} (Bal:{" "}
                    {money(inv.outstanding_balance, inv.currency)}) [{inv.payment_status}]
                  </option>
                ))}
              </select>
            </div>

            <div className="form-field">
              <label htmlFor="payment-date">Factual Payment Date</label>
              <input
                id="payment-date"
                type="date"
                required
                value={paymentDate}
                onChange={(e) => setPaymentDate(e.target.value)}
              />
              <small>Native calendar date picker for actual payment receipt date.</small>
            </div>

            <div className="form-field">
              <label htmlFor="payment-amount">Payment Amount (₹)</label>
              <input
                id="payment-amount"
                type="number"
                step="0.01"
                min="0.01"
                required
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="e.g. 50000.00"
              />
            </div>

            <div className="form-field">
              <label htmlFor="payment-ref">Reference / UTR (Optional)</label>
              <input
                id="payment-ref"
                type="text"
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder="e.g. NEFT-12345678"
              />
            </div>

            <div className="form-field">
              <label htmlFor="payment-note">Notes (Optional)</label>
              <textarea
                id="payment-note"
                rows={2}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Historical ledger settlement note"
              />
            </div>

            {error && (
              <p className="error-text" role="alert" style={{ fontSize: 12, margin: 0 }}>
                {error}
              </p>
            )}
          </div>

          <div className="modal-footer">
            <button type="button" className="button" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button
              type="submit"
              className="button primary"
              disabled={submitting}
            >
              {submitting ? (
                <>
                  <LoaderCircle size={16} className="spin" /> Recording…
                </>
              ) : (
                "Save Payment"
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// 5. Modal: Import Payment History (CSV / XLSX with Preview)
// ----------------------------------------------------------------------
function ImportPaymentModal({
  companyId,
  companyName,
  onClose,
  onSuccess,
}: {
  companyId: string;
  companyName: string;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [summary, setSummary] = useState<PaymentImport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isCommitted, setIsCommitted] = useState(false);

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) {
      const name = selected.name.toLowerCase();
      if (!name.endsWith(".csv") && !name.endsWith(".xlsx")) {
        setError("Only CSV and XLSX spreadsheets are supported for payment history imports.");
        setFile(null);
        setSummary(null);
      } else {
        setError(null);
        setFile(selected);
        setSummary(null);
        setIsCommitted(false);
      }
    }
  };

  const handlePreview = async () => {
    if (!file) return;
    setPreviewing(true);
    setError(null);
    try {
      const res = await historicalApi.previewPayments(companyId, file);
      setSummary(res);
    } catch (err) {
      setError(normaliseError(err).message);
    } finally {
      setPreviewing(false);
    }
  };

  const handleCommit = async () => {
    if (!file) return;
    setImporting(true);
    setError(null);
    try {
      const res = await historicalApi.importPayments(companyId, file);
      setSummary(res);
      setIsCommitted(true);
    } catch (err) {
      setError(normaliseError(err).message);
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Import Historical Payment History</h3>
          <button className="icon-button" onClick={onClose} aria-label="Close modal">
            <X size={18} />
          </button>
        </div>

        <div className="modal-body">
          <p style={{ margin: 0, fontSize: 13, color: "#60706a" }}>
            Import historical payment settlements strictly scoped to <strong>{companyName}</strong>.
            Supported formats: CSV or XLSX with <code>invoice_number</code>, <code>payment_date</code>,
            and <code>payment_amount</code> columns.
          </p>

          {!isCommitted && (
            <div className="form-field">
              <label htmlFor="history-file">Payment Spreadsheet (CSV or XLSX)</label>
              <input
                id="history-file"
                className="file-input"
                type="file"
                accept=".csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.xlsx"
                onChange={handleFileChange}
              />
              <label htmlFor="history-file" className="dropzone" style={{ minHeight: 110 }}>
                <FileSpreadsheet size={24} />
                <strong>{file ? file.name : "Choose a CSV or XLSX file"}</strong>
                <span>Spreadsheets will be validated and previewed before saving</span>
              </label>
            </div>
          )}

          {error && (
            <p className="error-text" role="alert" style={{ fontSize: 12, margin: 0 }}>
              {error}
            </p>
          )}

          {/* Validation & Preview Summary */}
          {summary && (
            <div
              className="panel"
              style={{
                background: isCommitted ? "#edf8f0" : "#f8faf9",
                border: isCommitted ? "1px solid #8dc4a0" : "1px solid #d9e1dc",
                padding: 16,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                <span style={{ color: isCommitted ? "#1d6b3a" : "#1f6051", display: "inline-flex" }}>
                  <CheckCircle2 size={18} />
                </span>
                <strong style={{ fontSize: 13, color: isCommitted ? "#1d6b3a" : "#172022" }}>
                  {isCommitted ? "Import Completed Successfully" : "Validation Preview"}
                </strong>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 16px", fontSize: 12 }}>
                <div>Total rows: <strong>{summary.total_rows}</strong></div>
                <div>Valid rows: <strong>{summary.valid_rows}</strong></div>
                <div>Matched invoices: <strong style={{ color: "#1d6b3a" }}>{summary.matched}</strong></div>
                <div>Unmatched rows: <strong>{summary.unmatched}</strong></div>
                <div>Repeated in file: <strong>{summary.duplicates_in_file}</strong></div>
                <div>Existing recorded: <strong>{summary.duplicates_existing}</strong></div>
                {summary.rejected > 0 && (
                  <div style={{ color: "#a94e2d" }}>
                    Rejected rows: <strong>{summary.rejected}</strong>
                  </div>
                )}
              </div>

              {summary.errors.length > 0 && (
                <div style={{ marginTop: 10, padding: 8, background: "#fff", borderRadius: 4, fontSize: 11, border: "1px solid #ebd3b4" }}>
                  <strong>Errors detected ({summary.errors.length}):</strong>
                  <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>
                    {summary.errors.slice(0, 3).map((err, idx) => (
                      <li key={idx}>Row {err.row_number}: {err.reason}</li>
                    ))}
                    {summary.errors.length > 3 && <li>…and {summary.errors.length - 3} more errors</li>}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="button" onClick={onClose} disabled={previewing || importing}>
            {isCommitted ? "Close" : "Cancel"}
          </button>

          {!isCommitted && !summary && (
            <button
              type="button"
              className="button primary"
              onClick={handlePreview}
              disabled={!file || previewing}
            >
              {previewing ? (
                <>
                  <LoaderCircle size={16} className="spin" /> Validating…
                </>
              ) : (
                "Validate and Preview"
              )}
            </button>
          )}

          {!isCommitted && summary && (
            <button
              type="button"
              className="button primary"
              onClick={handleCommit}
              disabled={summary.valid_rows === 0 || importing}
            >
              {importing ? (
                <>
                  <LoaderCircle size={16} className="spin" /> Ingesting…
                </>
              ) : (
                `Commit Import (${summary.valid_rows} rows)`
              )}
            </button>
          )}

          {isCommitted && (
            <button type="button" className="button primary" onClick={onSuccess}>
              Done & Refresh Workspace
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
