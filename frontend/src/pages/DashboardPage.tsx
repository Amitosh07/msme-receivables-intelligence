import { AlertCircle, AlertTriangle, ArrowRight, CalendarClock, Wallet } from "../components/Icons";
import { Link, useNavigate } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { customersApi, dashboardApi, normaliseError } from "../lib/api";
import { money, invoiceLabel, paymentRecord } from "../lib/format";
import { barWidths } from "../lib/totals";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { RiskBadge, StatusBadge } from "../components/Badges";
import { CashflowChart } from "../components/CashflowForecast";
import type { AmountCount, DashboardSummary, PriorityInvoice } from "../lib/types";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function DashboardPage() {
  const navigate = useNavigate();
  const state = useAsync(() => dashboardApi.summary(), []);
  if (state.loading) return <LoadingState label="Loading receivables overview…" />;
  if (state.error) return <ErrorState message={normaliseError(state.error).message} retry={state.refresh} />;
  const summary = state.data!;
  const total = (segment: AmountCount) => money(segment.amount, summary.currency);

  return (
    <>
      <section className="metric-grid">
        <Metric
          icon={Wallet}
          label="Outstanding"
          value={total(summary.outstanding)}
          detail={`${plural(summary.outstanding.count, "invoice")} to collect`}
          onClick={() => navigate("/invoices?status=OUTSTANDING")}
        />
        <Metric
          icon={AlertTriangle}
          label="Overdue"
          value={total(summary.overdue)}
          detail={summary.overdue.count ? `${plural(summary.overdue.count, "invoice")} past due` : "Nothing past due"}
          onClick={() => navigate("/invoices?status=OUTSTANDING&aging=overdue")}
        />
        <Metric
          icon={CalendarClock}
          label="Due soon"
          value={total(summary.due_soon)}
          detail={`${plural(summary.due_soon.count, "invoice")} due in ${summary.due_soon_days} days`}
          onClick={() => navigate("/invoices?status=OUTSTANDING&aging=due-soon")}
        />
        <Metric
          icon={AlertCircle}
          label="High risk"
          value={total(summary.high_risk)}
          detail={summary.high_risk.count ? `${plural(summary.high_risk.count, "invoice")} likely to pay late` : "No high-risk estimates"}
          onClick={() => navigate("/invoices?status=OUTSTANDING&risk=HIGH")}
        />
      </section>
      {(summary.undated.count > 0 || summary.mixed_currency) && (
        <p className="quiet dashboard-note">
          {summary.undated.count > 0 && `${plural(summary.undated.count, "invoice")} (${total(summary.undated)}) have no due date, so they are not aged. `}
          {summary.mixed_currency && "Invoices use more than one currency; totals are added without conversion."}
        </p>
      )}

      {!summary.current_invoice_count ? (
        <section style={{ marginTop: "24px" }}>
          <EmptyState
            title="No receivables yet"
            detail="Upload an invoice you are still collecting to build your receivables overview. Past, settled invoices belong in Historical Data."
            action={<Link className="button primary" to="/upload">Upload an invoice</Link>}
          />
        </section>
      ) : (
        <section className="dashboard-grid">
          <Panel
            title="Needs attention"
            subtitle="Outstanding invoices ranked by amount, lateness and late-payment risk"
            link="View outstanding"
            to="/invoices?status=OUTSTANDING"
            wide
          >
            {summary.priority.length ? (
              <PriorityList items={summary.priority} fallbackCurrency={summary.currency} />
            ) : (
              <p className="quiet">Nothing needs follow-up right now: no invoices are overdue, due in the next {summary.due_soon_days} days, or at high risk.</p>
            )}
          </Panel>
          <Panel title="A/R aging" subtitle="Remaining balance by days past due · select a segment to filter" link="View invoices" to="/invoices?status=OUTSTANDING">
            <Aging summary={summary} onSelect={(key) => navigate(`/invoices?status=OUTSTANDING&aging=${key}`)} />
          </Panel>
          <Panel title="Largest balances" subtitle="Who owes the most, and how they have paid before" link="All customers" to="/customers">
            <TopCustomers />
          </Panel>
          <Panel
            title="Cash-flow forecast"
            subtitle="Expected collections by week: expected payment date × remaining balance. Estimates, not confirmed payments."
            link="View outstanding"
            to="/invoices?status=OUTSTANDING"
            wide
          >
            <CashflowSection />
          </Panel>
        </section>
      )}
    </>
  );
}

function Metric({ icon: Icon, label, value, detail, onClick }: { icon: typeof Wallet; label: string; value: string; detail: string; onClick: () => void }) {
  return (
    <button className="metric" onClick={onClick}>
      <Icon size={19} />
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </button>
  );
}

function Panel({ title, subtitle, link, to, wide, children }: { title: string; subtitle: string; link: string; to: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <section className={wide ? "panel panel-wide" : "panel"}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        <Link to={to}>
          {link}
          <ArrowRight size={15} />
        </Link>
      </div>
      {children}
    </section>
  );
}

function PriorityList({ items, fallbackCurrency }: { items: PriorityInvoice[]; fallbackCurrency: string }) {
  return (
    <ol className="priority-list">
      {items.map((item) => (
        <li key={item.invoice_id}>
          <div>
            <Link to={`/invoices/${item.invoice_id}`}>
              {invoiceLabel(item.invoice_number)}
              {item.customer_name ? ` · ${item.customer_name}` : ""}
            </Link>
            <span>{item.reasons.join(" · ")}</span>
          </div>
          <div>
            <strong className="numeric">{money(item.outstanding_balance, item.currency || fallbackCurrency)}</strong>
            {item.risk_tier ? (
              <RiskBadge tier={item.risk_tier} score={item.risk_score ?? undefined} />
            ) : (
              <StatusBadge value={item.payment_status} />
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Aging({ summary, onSelect }: { summary: DashboardSummary; onSelect: (key: string) => void }) {
  const widths = barWidths(summary.aging.map((bucket) => bucket.amount));
  return (
    <>
      <div className="aging-bar">
        {summary.aging.map((bucket, i) => (
          <button
            key={bucket.key}
            onClick={() => onSelect(bucket.key)}
            style={{ width: `${widths[i]}%` }}
            aria-label={`${bucket.label}: ${plural(bucket.count, "invoice")}, ${money(bucket.amount, summary.currency)}`}
          >
            {bucket.count || ""}
          </button>
        ))}
      </div>
      <div className="aging-legend">
        {summary.aging.map((bucket) => (
          <button key={bucket.key} onClick={() => onSelect(bucket.key)}>
            <i /> {bucket.label} <b>{bucket.count}</b> <span className="numeric">{money(bucket.amount, summary.currency)}</span>
          </button>
        ))}
      </div>
    </>
  );
}

function CashflowSection() {
  const state = useAsync(() => dashboardApi.cashflow(), []);
  if (state.loading) return <LoadingState label="Building forecast…" />;
  if (state.error || !state.data) return <ErrorState message={normaliseError(state.error).message} retry={state.refresh} />;
  return <CashflowChart forecast={state.data} />;
}

function TopCustomers() {
  const state = useAsync(() => customersApi.balances(), []);
  if (state.loading) return <LoadingState label="Loading customers…" />;
  if (state.error || !state.data) return <ErrorState message={normaliseError(state.error).message} retry={state.refresh} />;
  const { customers, currency, top_customer_share, top_five_share } = state.data;
  const owing = customers.filter((c) => c.outstanding > 0).slice(0, 5);
  if (!owing.length) return <p className="quiet">No customer owes anything right now.</p>;
  return (
    <>
      <ol className="balance-list">
        {owing.map((c) => (
          <li key={c.customer_id ?? c.display_name}>
            <div>
              <Link to={`/invoices?status=OUTSTANDING&q=${encodeURIComponent(c.display_name)}`} title={c.display_name}>{c.display_name}</Link>
              <span>
                {c.overdue > 0 ? `${money(c.overdue, currency)} overdue · ` : ""}
                {paymentRecord(c)}
              </span>
            </div>
            <div>
              <strong className="numeric">{money(c.outstanding, currency)}</strong>
              <span>{Math.round(c.share_of_outstanding * 100)}% of total</span>
            </div>
          </li>
        ))}
      </ol>
      <p className="quiet balance-note">
        Largest customer is {Math.round(top_customer_share * 100)}% of outstanding
        {state.data.customers_owing > 5 ? `; the top five are ${Math.round(top_five_share * 100)}%.` : "."}
      </p>
    </>
  );
}
