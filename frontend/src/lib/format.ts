const MONEY_DIGITS = { minimumFractionDigits: 2, maximumFractionDigits: 2 } as const;

export const money = (value?: number | null, currency?: string | null): string => {
  const num = typeof value === "number" && !Number.isNaN(value) ? value : 0;
  const cleanCurrency =
    typeof currency === "string" && /^[a-zA-Z]{3}$/.test(currency.trim())
      ? currency.trim().toUpperCase()
      : "INR";

  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: cleanCurrency,
      ...MONEY_DIGITS,
    }).format(num);
  } catch {
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: "INR",
        ...MONEY_DIGITS,
      }).format(num);
    } catch {
      return `₹${num.toLocaleString(undefined, MONEY_DIGITS)}`;
    }
  }
};

/** Short amount for chart labels, e.g. ₹51.8K or ₹1.2L; use `money` wherever exact values matter. */
export const moneyCompact = (value: number, currency?: string | null): string => {
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: currency && /^[a-zA-Z]{3}$/.test(currency) ? currency.toUpperCase() : "INR",
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value);
  } catch {
    return money(value, currency);
  }
};

export const shortDate = (value: string): string =>
  new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(new Date(`${value}T00:00:00`));

export const date = (value?: string | null): string => {
  if (!value) return "—";
  try {
    const trimmed = String(value).trim();
    if (!trimmed) return "—";
    const dateObj = trimmed.includes("T")
      ? new Date(trimmed)
      : new Date(`${trimmed}T00:00:00`);
    if (isNaN(dateObj.getTime())) {
      const fallback = new Date(trimmed);
      if (isNaN(fallback.getTime())) return "—";
      return new Intl.DateTimeFormat(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
      }).format(fallback);
    }
    return new Intl.DateTimeFormat(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
    }).format(dateObj);
  } catch {
    return "—";
  }
};

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Invoice number for display; older manual entries stored an opaque UUID instead of a number. */
export const invoiceLabel = (invoiceNumber?: string | null): string => {
  if (!invoiceNumber) return "—";
  return UUID_PATTERN.test(invoiceNumber) ? `MAN-${invoiceNumber.slice(0, 6).toUpperCase()}` : invoiceNumber;
};

/** One-line payment record from fully paid invoices, e.g. "2 of 3 paid on time · 2.3 days late on average". */
export const paymentRecord = (record: { settled_invoice_count: number; on_time_rate?: number | null; average_days_late?: number | null }): string => {
  const settled = record.settled_invoice_count;
  if (!settled) return "No paid invoices on record";
  const onTime = Math.round((record.on_time_rate ?? 0) * settled);
  const avg = record.average_days_late ?? 0;
  const timing = avg > 0 ? `${avg} days late on average` : avg < 0 ? `${-avg} days early on average` : "on the due date on average";
  return `${onTime} of ${settled} paid on time · ${timing}`;
};

export const titleCase = (value?: string | null): string => {
  if (!value || typeof value !== "string") return "";
  return value.toLowerCase().replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
};
