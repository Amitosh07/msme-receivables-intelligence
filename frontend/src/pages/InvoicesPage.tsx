import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { InvoiceTable } from "../components/InvoiceTable";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { useAsync } from "../hooks/useAsync";
import { invoicesApi, normaliseError } from "../lib/api";
import { agingFilterLabel } from "../lib/aging";
import type { InvoiceSort } from "../lib/types";

const PAGE_SIZE = 10;
const SEARCH_DELAY_MS = 300;

export function InvoicesPage() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") || "");
  const [search, setSearch] = useState(query.trim());
  const [status, setStatus] = useState(params.get("status") || "");
  const [risk, setRisk] = useState(params.get("risk") || "");
  const [sort, setSort] = useState<InvoiceSort>("uploaded");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const aging = params.get("aging") || "";

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (status) next.set("status", status);
    if (risk) next.set("risk", risk);
    if (aging) next.set("aging", aging);
    if (search) next.set("q", search);
    setParams(next, { replace: true });
  }, [status, risk, aging, search]);

  const state = useAsync(
    () =>
      invoicesApi.list({
        skip: (page - 1) * PAGE_SIZE,
        limit: PAGE_SIZE,
        payment_status: status,
        risk,
        aging,
        q: search,
        sort,
        direction,
      }),
    [status, risk, aging, search, sort, direction, page]
  );

  const total = state.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => {
    if (state.data && page > pages) setPage(pages);
  }, [state.data, page, pages]);

  const filtered = Boolean(status || risk || aging || search);
  const clearFilters = () => {
    setStatus("");
    setRisk("");
    setQuery("");
    setSearch("");
    setPage(1);
    setParams({});
  };

  if (state.loading && !state.data) return <LoadingState label="Loading invoices…" />;
  if (state.error && !state.data) return <ErrorState message={normaliseError(state.error).message} retry={state.refresh} />;
  if (!total && !filtered) {
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
          <p>Invoices you are still collecting, and those already paid. Past invoices used for payment history live in Historical Data.</p>
        </div>
        <span>{filtered ? `${total} matching` : `${total} total`}</span>
      </section>
      {filtered && (
        <button className="text-button clear-filter" onClick={clearFilters}>
          {aging ? `Aging: ${agingFilterLabel(aging)} · ` : ""}Clear filter
        </button>
      )}
      {state.error && <ErrorState message={normaliseError(state.error).message} retry={state.refresh} />}
      <InvoiceTable
        invoices={state.data?.items ?? []}
        total={total}
        page={Math.min(page, pages)}
        pageSize={PAGE_SIZE}
        onPage={setPage}
        busy={state.loading}
        empty={
          <EmptyState
            title="No invoices match these filters"
            detail="Try clearing a filter or search term to see your receivables."
            action={
              <button className="button" onClick={clearFilters}>
                Clear filter
              </button>
            }
          />
        }
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
    </>
  );
}
