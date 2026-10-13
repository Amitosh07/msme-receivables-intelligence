import { Link } from "react-router-dom";
import { invoiceLabel, money, moneyCompact, shortDate } from "../lib/format";
import { forecastCoverage } from "../lib/totals";
import type { CashflowForecast, ForecastInvoice, ForecastWeek } from "../lib/types";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const BASIS_LABEL: Record<ForecastInvoice["basis"], string> = {
  predicted: "model date",
  due_date: "due date",
  past_predicted: "predicted date passed",
  overdue_unestimated: "overdue, no estimate",
  undated: "no due date",
};

function coverageText(forecast: CashflowForecast) {
  if (forecast.outstanding.amount <= 0) return "";
  const share = forecastCoverage(forecast.outstanding.amount, forecast.model_dated.amount, forecast.due_date_dated.amount);
  const parts = [`model dates cover ${share.model}% of the outstanding balance`];
  if (forecast.due_date_dated.count) parts.push(`due dates are used for ${share.dueDate}%`);
  if (share.unscheduled > 0) parts.push(`${share.unscheduled}% has no usable date and is listed below`);
  const sentence = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
  return `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}.`;
}

export function CashflowChart({ forecast }: { forecast: CashflowForecast }) {
  const max = Math.max(...forecast.weeks.map((w) => w.total), 1);
  const currency = forecast.currency;
  return (
    <div className="cashflow">
      <p className="cashflow-summary">
        <b>{money(forecast.scheduled.amount, currency)}</b> expected over the next {forecast.horizon_weeks} weeks from{" "}
        {plural(forecast.scheduled.count, "invoice")}. {coverageText(forecast)}
      </p>
      <div className="cashflow-chart" role="list" aria-label={`Expected collections by week for the next ${forecast.horizon_weeks} weeks`}>
        {forecast.weeks.map((week) => <WeekBar key={week.start} week={week} max={max} currency={currency} />)}
      </div>
      <div className="cashflow-legend">
        <span><i className="predicted" /> Model-predicted date</span>
        <span><i className="due" /> Due date (assumes on-time payment)</span>
      </div>
      {forecast.unscheduled.length > 0 && (
        <ul className="cashflow-unscheduled">
          {forecast.unscheduled.map((bucket) => (
            <li key={bucket.key}>
              <div>
                <strong>{bucket.label}</strong>
                <span>{bucket.detail}</span>
              </div>
              <b className="numeric">{money(bucket.amount, currency)}</b>
              <small>{plural(bucket.count, "invoice")}</small>
            </li>
          ))}
        </ul>
      )}
      <details className="assumptions" open>
        <summary>Assumptions behind this forecast</summary>
        <ol>
          {forecast.assumptions.map((line) => <li key={line}>{line}</li>)}
        </ol>
      </details>
    </div>
  );
}

function WeekBar({ week, max, currency }: { week: ForecastWeek; max: number; currency: string }) {
  const label = `${shortDate(week.start)} – ${shortDate(week.end)}`;
  const height = (value: number) => `${(value / max) * 100}%`;
  return (
    <div className="cashflow-week" role="listitem" tabIndex={0} aria-label={`${label}: ${money(week.total, currency)} from ${plural(week.count, "invoice")}`}>
      <span className="cashflow-amount">{week.total ? moneyCompact(week.total, currency) : "—"}</span>
      <div className="cashflow-bar">
        <i className="due" style={{ height: height(week.due_date_based) }} />
        <i className="predicted" style={{ height: height(week.predicted) }} />
      </div>
      <span className="cashflow-label">{shortDate(week.start)}</span>
      {week.count > 0 && (
        <div className="cashflow-tip">
          <b>{label}</b>
          <span>{money(week.total, currency)} · {plural(week.count, "invoice")}</span>
          {week.high_risk > 0 && <span>{money(week.high_risk, currency)} of it is high late-payment risk</span>}
          <ul>
            {week.invoices.map((i) => (
              <li key={i.invoice_id}>
                <Link to={`/invoices/${i.invoice_id}`}>{invoiceLabel(i.invoice_number)}</Link>
                {i.customer_name ? ` · ${i.customer_name}` : ""} · {money(i.outstanding_balance, currency)} ({BASIS_LABEL[i.basis]})
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
