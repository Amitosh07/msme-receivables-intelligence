import type { ReactNode } from "react";
import { ArrowUpDown, ChevronLeft, ChevronRight, Search } from "./Icons";
import { Link, useNavigate } from "react-router-dom";
import type { InvoiceListItem, InvoiceSort } from "../lib/types";
import { date, invoiceLabel, money } from "../lib/format";
import { dueLabel, isOutstanding } from "../lib/aging";
import { RiskBadge, StatusBadge } from "./Badges";
type Props = { invoices: InvoiceListItem[]; page: number; pageSize: number; total: number; onPage: (page: number) => void; busy?: boolean; empty?: ReactNode; query: string; setQuery: (value: string) => void; status: string; setStatus: (value: string) => void; risk: string; setRisk: (value: string) => void; sort: InvoiceSort; setSort: (value: InvoiceSort) => void; direction: "asc" | "desc"; setDirection: (value: "asc" | "desc") => void };
export function InvoiceTable({
  invoices = [],
  page,
  pageSize,
  total,
  onPage,
  busy = false,
  empty,
  query,
  setQuery,
  status,
  setStatus,
  risk,
  setRisk,
  sort,
  setSort,
  direction,
  setDirection,
}: Props) {
  const navigate = useNavigate();
  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <section className={`table-section${busy ? " busy" : ""}`} aria-busy={busy}>
      <div className="table-controls">
        <label className="search">
          <Search size={17} />
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              onPage(1);
            }}
            placeholder="Search invoice or customer"
            aria-label="Search invoices"
          />
        </label>
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            onPage(1);
          }}
          aria-label="Filter payment status"
        >
          <option value="">All payment states</option>
          <option value="OUTSTANDING">Outstanding (unpaid + partial)</option>
          <option value="OPEN">Unpaid</option>
          <option value="PARTIAL">Partially paid</option>
          <option value="PAID">Paid</option>
        </select>
        <select
          value={risk}
          onChange={(e) => {
            setRisk(e.target.value);
            onPage(1);
          }}
          aria-label="Filter risk"
        >
          <option value="">All risk levels</option>
          <option value="HIGH">High risk</option>
          <option value="MEDIUM">Medium risk</option>
          <option value="LOW">Low risk</option>
          <option value="UNAVAILABLE">Prediction unavailable</option>
        </select>
        <label className="sort">
          Sort{" "}
          <select
            value={sort}
            onChange={(e) => {
              setSort(e.target.value as Props["sort"]);
              onPage(1);
            }}
          >
            <option value="uploaded">Recently uploaded</option>
            <option value="due_date">Due date</option>
            <option value="amount">Amount</option>
            <option value="invoice_date">Invoice date</option>
          </select>
          <button
            className="direction"
            type="button"
            onClick={() => {
              setDirection(direction === "desc" ? "asc" : "desc");
              onPage(1);
            }}
            aria-label={direction === "desc" ? "Sort descending" : "Sort ascending"}
          >
            <ArrowUpDown size={14} />
          </button>
        </label>
      </div>
      {!invoices.length && !busy && empty ? empty : (
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Invoice</th>
              <th>Customer</th>
              <th>Amount</th>
              <th>Issued</th>
              <th>Due</th>
              <th>Payment</th>
              <th>Processing</th>
              <th>Late-payment risk</th>
              <th>Expected payment</th>
            </tr>
          </thead>
          <tbody>
            {(invoices || []).map((invoice) => {
              const prediction = invoice.prediction;
              const open = () => navigate(`/invoices/${invoice.id}`);
              const invoiceDisplay = invoiceLabel(invoice.invoice_number);
              const partial = invoice.payment_status === "PARTIAL" && invoice.outstanding_balance !== undefined;
              const dueNote = isOutstanding(invoice.payment_status) ? dueLabel(invoice.due_date) : null;
              return (
                <tr
                  key={invoice.id}
                  className="invoice-row"
                  role="link"
                  tabIndex={0}
                  aria-label={`Open invoice ${invoiceDisplay}${invoice.customer_name ? ` for ${invoice.customer_name}` : ""}`}
                  onClick={open}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      open();
                    }
                  }}
                >
                  <td>
                    <Link
                      className="invoice-link"
                      to={`/invoices/${invoice.id}`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {invoiceDisplay}
                    </Link>
                  </td>
                  <td className="wrap">{invoice.customer_name || <span className="muted">Not identified</span>}</td>
                  <td className="numeric">
                    {money(invoice.amount, invoice.currency)}
                    {partial && <small className="cell-note">{money(invoice.outstanding_balance, invoice.currency)} left</small>}
                  </td>
                  <td>{date(invoice.invoice_date)}</td>
                  <td>
                    {date(invoice.due_date)}
                    {dueNote && <small className={`cell-note${dueNote.endsWith("overdue") ? " overdue" : ""}`}>{dueNote}</small>}
                  </td>
                  <td>
                    <StatusBadge value={invoice.payment_status || "OPEN"} />
                  </td>
                  <td>
                    <StatusBadge value={invoice.processing_status || "PROCESSED"} />
                  </td>
                  <td>
                    {prediction ? (
                      <RiskBadge tier={prediction.risk_tier} score={prediction.risk_score} />
                    ) : (
                      <span className="muted">Unavailable</span>
                    )}
                  </td>
                  <td>
                    {prediction?.expected_payment_date ? (
                      <span>
                        <span className="predicted-label">Predicted</span>
                        {date(prediction.expected_payment_date)}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      )}
      <footer className="pagination">
        <span>
          {total} invoice{total === 1 ? "" : "s"}
        </span>
        <div>
          <button disabled={page === 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
            <ChevronLeft size={17} />
          </button>
          <span>
            Page {page} of {pages}
          </span>
          <button disabled={page === pages} onClick={() => onPage(page + 1)} aria-label="Next page">
            <ChevronRight size={17} />
          </button>
        </div>
      </footer>
    </section>
  );
}

