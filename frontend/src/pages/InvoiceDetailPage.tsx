import { ArrowLeft, CalendarClock, FileText, Sparkles } from "../components/Icons";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { invoicesApi, normaliseError, predictionsApi } from "../lib/api";
import { date, money, titleCase } from "../lib/format";
import { RiskBadge, StatusBadge } from "../components/Badges";
import { ErrorState, LoadingState } from "../components/States";

export function InvoiceDetailPage() {
  const { id = "" } = useParams();
  const [generating, setGenerating] = useState(false);
  const [predictionError, setPredictionError] = useState("");
  const state = useAsync(async () => {
    const invoice = await invoicesApi.get(id);
    let prediction;
    try { prediction = await predictionsApi.get(id); }
    catch (error) { if (normaliseError(error).status !== 404) throw error; }
    return { invoice, prediction };
  }, [id]);

  if (state.loading) return <LoadingState label="Loading invoice…" />;
  if (state.error) return <ErrorState message={normaliseError(state.error).message} retry={state.refresh} />;
  const { invoice, prediction } = state.data!;
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

  return <>
    <Link className="back" to="/invoices"><ArrowLeft size={16} />All invoices</Link>
    <section className="detail-head"><div><p className="eyebrow">Invoice</p><h2>{invoice.invoice_number}</h2><p>Created {date(invoice.created_at.slice(0, 10))}</p></div><div className="detail-value"><strong>{money(invoice.amount, invoice.currency)}</strong><StatusBadge value={invoice.payment_status} /></div></section>
    <div className="detail-grid">
      <section className="detail-section"><h2>Invoice facts</h2><dl><Fact label="Invoice date" value={date(invoice.invoice_date)} /><Fact label="Due date" value={date(invoice.due_date)} /><Fact label="Payment terms" value={invoice.payment_terms || "Not available"} /><Fact label="Payment status" value={<StatusBadge value={invoice.payment_status} />} /><Fact label="Processing" value={<StatusBadge value={invoice.processing_status} />} /><Fact label="Currency" value={invoice.currency} /></dl></section>
      <section className="prediction-panel">
        <div className="panel-title"><Sparkles size={18} /><div><h2>Payment estimate</h2><p>Predicted values, based on available invoice and payment history.</p></div></div>
        {prediction ? <div className="prediction-content"><RiskBadge tier={prediction.risk_tier} score={prediction.risk_score} /><strong>{Math.round(prediction.risk_score * 100)}% probability of late payment</strong><div className="expected"><CalendarClock size={19} /><div><span>Expected payment</span><b>{date(prediction.expected_payment_date)}</b>{prediction.predicted_days_until_payment !== null && prediction.predicted_days_until_payment !== undefined && <small>Estimated {Math.round(prediction.predicted_days_until_payment)} days from invoice date</small>}</div></div><small className="disclaimer">This is a model estimate, not a confirmed payment date.</small><button className="button secondary" onClick={generate} disabled={generating}>{generating ? "Refreshing estimate…" : "Re-generate prediction"}</button>{predictionError && <p className="error-text" role="alert">{predictionError}</p>}</div>
          : <div className="unavailable"><FileText size={20} /><div><h3>{generating ? "Generating prediction…" : "Prediction unavailable"}</h3><p>{eligible ? "No prediction has been generated for this invoice yet." : `Predictions are available once invoice processing is complete. Current status: ${titleCase(invoice.processing_status)}.`}</p>{eligible && <button className="button primary" onClick={generate} disabled={generating}>{generating ? "Generating…" : predictionError ? "Retry prediction" : "Generate prediction"}</button>}{predictionError && <p className="error-text" role="alert">{predictionError}</p>}</div></div>}
      </section>
    </div>
  </>;
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) { return <><dt>{label}</dt><dd>{value}</dd></>; }
