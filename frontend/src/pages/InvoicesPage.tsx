import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { InvoiceTable } from "../components/InvoiceTable";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { useAsync } from "../hooks/useAsync";
import { invoicesApi, normaliseError, predictionsApi } from "../lib/api";
export function InvoicesPage() {
  const [params, setParams] = useSearchParams();
  const state = useAsync(async () => {
    const [invoices, predictions, documents] = await Promise.all([
      invoicesApi.listAll(),
      predictionsApi.listAll(),
      invoicesApi.documents(),
    ]);
    return {
      invoices: invoices?.items ?? [],
      predictions: predictions?.items ?? [],
      documents: documents ?? [],
    };
  }, []);

  const [query, setQuery] = useState("");
  const [status, setStatus] = useState(params.get("status") || "");
  const [risk, setRisk] = useState(params.get("risk") || "");
  const [sort, setSort] = useState<"due_date" | "amount" | "invoice_date" | "uploaded">("uploaded");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const pageSize = 10;

  useEffect(() => {
    const next = new URLSearchParams();
    if (status) next.set("status", status);
    if (risk) next.set("risk", risk);
    if (params.get("aging")) next.set("aging", params.get("aging")!);
    setParams(next, { replace: true });
  }, [status, risk]);

  const aging = params.get("aging");
  const clearFilters = () => {
    setStatus("");
    setRisk("");
    setParams({});
  };

  const filtered = useMemo(() => {
    if (!state.data?.invoices) return [];
    const predictions = state.data.predictions ?? [];
    const documents = state.data.documents ?? [];
    const byInvoice = new Map(predictions.map((p) => [p.invoice_id, p]));
    const docByInvoice = new Map(
      documents.filter((d) => d && d.invoice_id).map((d) => [d.invoice_id!, d])
    );
    const today = new Date().toISOString().slice(0, 10);
    const age = (due?: string | null) => {
      if (!due) return -1;
      return Math.max(0, Math.floor((new Date(today).getTime() - new Date(due).getTime()) / 86400000));
    };

    return state.data.invoices
      .filter((invoice) => {
        if (!invoice) return false;
        const numMatch =
          !query ||
          (Boolean(invoice.invoice_number) &&
            invoice.invoice_number!.toLowerCase().includes(query.toLowerCase()));
        const statusMatch = !status || invoice.payment_status === status;
        const riskMatch =
          !risk ||
          (risk === "UNAVAILABLE"
            ? !byInvoice.has(invoice.id)
            : byInvoice.get(invoice.id)?.risk_tier === risk);

        let agingMatch = true;
        if (aging) {
          if (invoice.payment_status !== "OPEN" || !invoice.due_date) {
            agingMatch = false;
          } else if (aging === "current") {
            agingMatch = invoice.due_date >= today;
          } else {
            const invoiceAge = age(invoice.due_date);
            if (invoiceAge < 0) {
              agingMatch = false;
            } else if (aging === "1-30") {
              agingMatch = invoiceAge <= 30;
            } else if (aging === "31-60") {
              agingMatch = invoiceAge >= 31 && invoiceAge <= 60;
            } else if (aging === "61-90") {
              agingMatch = invoiceAge >= 61 && invoiceAge <= 90;
            } else if (aging === "90+") {
              agingMatch = invoiceAge > 90;
            }
          }
        }

        return numMatch && statusMatch && riskMatch && agingMatch;
      })
      .sort((a, b) => {
        const av =
          sort === "amount"
            ? (a.amount ?? 0)
            : sort === "uploaded"
            ? docByInvoice.get(a.id)?.created_at || a.created_at || ""
            : a[sort] || "";
        const bv =
          sort === "amount"
            ? (b.amount ?? 0)
            : sort === "uploaded"
            ? docByInvoice.get(b.id)?.created_at || b.created_at || ""
            : b[sort] || "";
        return (av < bv ? -1 : av > bv ? 1 : 0) * (direction === "asc" ? 1 : -1);
      });
  }, [state.data, query, status, risk, sort, direction, aging]);

  if (state.loading) return <LoadingState label="Loading invoices…" />;
  if (state.error) return <ErrorState message={normaliseError(state.error).message} retry={state.refresh} />;
  if (!state.data?.invoices?.length) {
    return (
      <EmptyState
        title="No invoices yet"
        detail="Upload your first invoice to start tracking receivables."
        action={
          <Link className="button primary" to="/upload">
            Upload invoice
          </Link>
        }
      />
    );
  }

  return (
    <>
      <section className="page-heading">
        <div>
          <p className="eyebrow">Receivables register</p>
          <h2>Invoices</h2>
          <p>Search, review payment status, and focus collection work.</p>
        </div>
        <span>{state.data.invoices.length} total</span>
      </section>
      {(status || risk || aging) && (
        <button className="text-button clear-filter" onClick={clearFilters}>
          Clear filter
        </button>
      )}
      {filtered.length ? (
        <InvoiceTable
          invoices={filtered.slice((page - 1) * pageSize, page * pageSize)}
          predictions={state.data.predictions ?? []}
          total={filtered.length}
          page={page}
          pageSize={pageSize}
          onPage={setPage}
          query={query}
          setQuery={setQuery}
          status={status}
          setStatus={setStatus}
          risk={risk}
          setRisk={setRisk}
          sort={sort}
          setSort={setSort}
          direction={direction}
          setDirection={setDirection}
        />
      ) : (
        <EmptyState
          title="No invoices match these filters"
          detail="Try clearing a filter or search term to see your receivables."
          action={
            <button className="button" onClick={clearFilters}>
              Clear filter
            </button>
          }
        />
      )}
    </>
  );
}
