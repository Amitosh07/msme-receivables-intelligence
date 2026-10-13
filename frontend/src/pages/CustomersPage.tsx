import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Search } from "../components/Icons";
import { RiskBadge } from "../components/Badges";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { useAsync } from "../hooks/useAsync";
import { customersApi, normaliseError } from "../lib/api";
import { date, money, paymentRecord } from "../lib/format";
import type { CustomerBalance } from "../lib/types";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const pct = (value: number) => `${Math.round(value * 100)}%`;

export function CustomersPage() {
  const state = useAsync(() => customersApi.balances(), []);
  const [query, setQuery] = useState("");
  const [showSettled, setShowSettled] = useState(false);

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (state.data?.customers ?? []).filter(
      (c) => (showSettled || c.outstanding > 0) && (!needle || c.display_name.toLowerCase().includes(needle)),
    );
  }, [state.data, query, showSettled]);

  if (state.loading) return <LoadingState label="Loading customer balances…" />;
  if (state.error || !state.data) return <ErrorState message={normaliseError(state.error).message} retry={state.refresh} />;
  const data = state.data;
  const settledOnly = data.customers.length - data.customers_owing;

  if (!data.customers.length) {
    return (
      <EmptyState
        title="No customers with current invoices yet"
        detail="Customers appear here once you upload an invoice you are still collecting."
        action={<Link className="button primary" to="/upload">Upload invoice</Link>}
      />
    );
  }

  return (
    <>
      <section className="page-heading">
        <div>
          <p>
            What each customer owes now, across all their current invoices, next to how they have paid before. Payment
            records count fully paid invoices only; estimates need {data.required_history} of them.
          </p>
        </div>
        <span>{plural(data.customers_owing, "customer")} owing</span>
      </section>

      <section className="metric-grid customer-metrics">
        <div className="metric static">
          <span>Outstanding</span>
          <strong>{money(data.total_outstanding, data.currency)}</strong>
          <small>Across {plural(data.customers_owing, "customer")}</small>
        </div>
        <div className="metric static">
          <span>Largest customer</span>
          <strong>{pct(data.top_customer_share)}</strong>
          <small>Share of outstanding</small>
        </div>
        <div className="metric static">
          <span>Top five</span>
          <strong>{pct(data.top_five_share)}</strong>
          <small>Share of outstanding</small>
        </div>
        <div className="metric static">
          <span>Estimates ready</span>
          <strong>{data.customers.filter((c) => c.estimates_ready && c.outstanding > 0).length} of {data.customers_owing}</strong>
          <small>Owing customers with {data.required_history}+ paid invoices</small>
        </div>
      </section>
      {data.mixed_currency && <p className="quiet dashboard-note">Invoices use more than one currency; totals are added without conversion.</p>}

      <section className="table-section">
        <div className="table-controls">
          <label className="search">
            <Search size={17} />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search customer" aria-label="Search customers" />
          </label>
          {settledOnly > 0 && (
            <label className="toggle">
              <input type="checkbox" checked={showSettled} onChange={(e) => setShowSettled(e.target.checked)} />
              Show {plural(settledOnly, "customer")} with nothing owed
            </label>
          )}
        </div>
        <div className="table-wrap">
          <table className="customer-table">
            <thead>
              <tr>
                <th>Customer</th>
                <th>Outstanding</th>
                <th>Overdue</th>
                <th>Next due</th>
                <th>Payment record</th>
                <th>Late-payment risk</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <CustomerRow key={c.customer_id ?? c.display_name} customer={c} currency={data.currency} required={data.required_history} />
              ))}
            </tbody>
          </table>
        </div>
        <footer className="pagination">
          <span>{plural(rows.length, "customer")}</span>
        </footer>
      </section>
    </>
  );
}

function CustomerRow({ customer: c, currency, required }: { customer: CustomerBalance; currency: string; required: number }) {
  return (
    <tr>
      <td>
        <Link className="invoice-link" to={`/invoices?status=OUTSTANDING&q=${encodeURIComponent(c.display_name)}`}>
          {c.display_name}
        </Link>
        <small className="cell-note">
          {c.matched ? plural(c.open_invoice_count, "open invoice") : "Not matched to a customer record"}
          {c.has_history && c.customer_id && <> · <Link to={`/historical/${c.customer_id}`}>History</Link></>}
        </small>
      </td>
      <td className="numeric">
        {money(c.outstanding, currency)}
        {c.outstanding > 0 && (
          <div className="share-bar" title={`${pct(c.share_of_outstanding)} of total outstanding`}>
            <i style={{ width: pct(c.share_of_outstanding) }} />
          </div>
        )}
      </td>
      <td className="numeric">
        {c.overdue > 0 ? money(c.overdue, currency) : "—"}
        {c.oldest_days_overdue ? <small className="cell-note overdue">Oldest {plural(c.oldest_days_overdue, "day")} late</small> : null}
      </td>
      <td>
        {date(c.next_due_date)}
        {c.next_expected_payment && <small className="cell-note">Payment expected {date(c.next_expected_payment)}</small>}
      </td>
      <td>
        <span className="record">{paymentRecord(c)}</span>
      </td>
      <td>
        {c.highest_risk_tier ? (
          <RiskBadge tier={c.highest_risk_tier} />
        ) : c.estimates_ready || c.outstanding === 0 ? (
          <span className="muted">—</span>
        ) : (
          <span className="muted">Needs {plural(Math.max(0, required - c.settled_invoice_count), "more paid invoice")}</span>
        )}
      </td>
    </tr>
  );
}
