import { useState } from "react";
import { Link } from "react-router-dom";
import { CalendarClock, CheckCircle2, LoaderCircle, Sparkles } from "../components/Icons";
import { RiskBadge } from "../components/Badges";
import { EstimateDrivers } from "../components/EstimateExplanation";
import { ErrorState, LoadingState } from "../components/States";
import { useAsync } from "../hooks/useAsync";
import { demoApi, normaliseError } from "../lib/api";
import { notifyDataChanged } from "../lib/events";
import { date } from "../lib/format";
import type { DemoRun } from "../lib/types";

const SCRIPT = [
  { title: "Three paid invoices", detail: "A demo customer, Sharma Textiles (Demo), gets three past invoices that were paid 4, 7 and 2 days after their due dates." },
  { title: "One open invoice", detail: "A ₹51,800 invoice issued 10 days ago, due in 20 days, is added as money still to collect." },
  { title: "History check", detail: "The model only scores a customer with at least 3 invoices fully paid before the new invoice’s date." },
  { title: "Score and explain", detail: "The real models estimate the chance of late payment and the likely payment date, and show what drove the result." },
];

export function DemoPage() {
  const status = useAsync(() => demoApi.status(), []);
  const [run, setRun] = useState<DemoRun>();
  const [busy, setBusy] = useState<"run" | "remove" | null>(null);
  const [error, setError] = useState("");
  const [removedNote, setRemovedNote] = useState("");

  if (status.loading) return <LoadingState label="Checking demo data…" />;
  if (status.error || !status.data) return <ErrorState message={normaliseError(status.error).message} retry={status.refresh} />;
  const exists = status.data.exists;

  const start = async () => {
    setBusy("run");
    setError("");
    setRemovedNote("");
    try {
      setRun(await demoApi.run());
      notifyDataChanged();
    } catch (err) {
      setError(normaliseError(err).message);
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!window.confirm("Remove the demo customer and its 4 DEMO-ST invoices? Nothing else is touched.")) return;
    setBusy("remove");
    setError("");
    try {
      const result = await demoApi.remove();
      setRun(undefined);
      setRemovedNote(`Removed ${result.removed_invoices} demo invoices and ${result.removed_payments} payments.`);
      notifyDataChanged();
    } catch (err) {
      setError(normaliseError(err).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <section className="page-heading">
        <div>
          <p>
            See the whole path in under a minute: three paid invoices teach the model how a customer pays, and a fourth,
            still open, gets scored and explained. It uses the same steps and models as real data.
          </p>
        </div>
      </section>

      <div className="demo-grid">
        <section className="panel demo-script">
          <div className="panel-title">
            <Sparkles size={18} />
            <div>
              <h2>The script</h2>
              <p>Dates are relative to today, so the story is the same whenever you run it.</p>
            </div>
          </div>
          <ol className="demo-steps">
            {(run?.steps ?? SCRIPT.map((s, i) => ({ key: String(i), title: s.title, detail: s.detail, created: false, history_count: null }))).map((step) => (
              <li key={step.key} className={run ? "done" : ""}>
                {run ? <CheckCircle2 size={17} /> : <i />}
                <div>
                  <strong>{step.title}</strong>
                  <span>{step.detail}</span>
                  {run && step.history_count != null && step.key.startsWith("paid") && (
                    <small>Paid invoices on record: {step.history_count} of {run.required_history}</small>
                  )}
                </div>
              </li>
            ))}
          </ol>
          <p className="demo-warning">
            This adds demo records to <b>this account</b>. The open ₹51,800 invoice counts in your Overview totals until you
            remove the demo. Everything is labelled DEMO-ST and can be removed in one step.
          </p>
          {error && <p className="error-text" role="alert">{error}</p>}
          {removedNote && <p className="quiet" role="status">{removedNote}</p>}
          <div className="demo-actions">
            <button className="button primary" onClick={start} disabled={busy !== null}>
              {busy === "run" ? <><LoaderCircle className="spin" size={16} /> Running…</> : exists || run ? "Run again" : "Run the demo"}
            </button>
            {(exists || run) && (
              <button className="button secondary" onClick={remove} disabled={busy !== null}>
                {busy === "remove" ? "Removing…" : "Remove demo data"}
              </button>
            )}
          </div>
        </section>

        <section className="panel demo-result">
          {run ? (
            <>
              <div className="panel-title">
                <CalendarClock size={18} />
                <div>
                  <h2>{run.open_invoice_number} · {run.customer_name}</h2>
                  <p>{run.eligible_history_count} of {run.required_history} required paid invoices found before the invoice date.</p>
                </div>
              </div>
              <div className="demo-score">
                <RiskBadge tier={run.prediction.risk_tier} score={run.prediction.risk_score} />
                <strong>{Math.round(run.prediction.risk_score * 100)}% chance of late payment</strong>
                <span>Expected payment {date(run.prediction.expected_payment_date)} · a model estimate, not a promise</span>
              </div>
              <h3 className="demo-why">Why</h3>
              <EstimateDrivers explanation={run.explanation} limit={4} />
              <div className="demo-actions">
                <Link className="button primary" to={`/invoices/${run.open_invoice_id}`}>Open the scored invoice</Link>
                <Link className="button secondary" to={`/historical/${run.customer_id}`}>See the paid history</Link>
                <Link className="text-button" to="/">See it on the Overview</Link>
              </div>
            </>
          ) : (
            <div className="demo-placeholder">
              <Sparkles size={26} />
              <h2>{exists ? "Demo data is already in this account" : "Ready when you are"}</h2>
              <p>
                {exists
                  ? "Run it again to re-score the same invoices and see the explanation, or remove the demo data."
                  : "Run the demo to create the records and score the open invoice. It takes a few seconds."}
              </p>
              {exists && status.data.open_invoice_id && (
                <Link className="text-button" to={`/invoices/${status.data.open_invoice_id}`}>Open the demo invoice</Link>
              )}
            </div>
          )}
        </section>
      </div>
    </>
  );
}
