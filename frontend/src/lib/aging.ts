export type AgingBucket = "current" | "1-30" | "31-60" | "61-90" | "90+";
export type AgingFilter = AgingBucket | "overdue" | "due-soon";

/** Days ahead (inclusive of today) that count as "due soon"; mirrors the summary API default. */
export const DUE_SOON_DAYS = 7;

/** Payment states that still have money to collect. */
export const OUTSTANDING_STATUSES = ["OPEN", "PARTIAL"];
export const isOutstanding = (paymentStatus?: string | null) => OUTSTANDING_STATUSES.includes(paymentStatus ?? "");

export const AGING_BUCKETS: { key: AgingBucket; label: string }[] = [
  { key: "current", label: "Current" },
  { key: "1-30", label: "1–30 days" },
  { key: "31-60", label: "31–60 days" },
  { key: "61-90", label: "61–90 days" },
  { key: "90+", label: "90+ days" },
];

const DAY_MS = 86_400_000;
const pad = (n: number) => String(n).padStart(2, "0");

/** Calendar date (YYYY-MM-DD) in the user's local timezone, not UTC. */
export const todayISO = (now: Date = new Date()): string =>
  `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;

const toDayNumber = (iso: string): number | null => {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return null;
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) / DAY_MS;
};

export const addDaysISO = (iso: string, days: number): string => {
  const day = toDayNumber(iso);
  if (day === null) return iso;
  return new Date((day + days) * DAY_MS).toISOString().slice(0, 10);
};

/** Whole calendar days past the due date; zero or negative when not yet overdue. */
export const daysPastDue = (dueDate?: string | null, today: string = todayISO()): number | null => {
  if (!dueDate) return null;
  const due = toDayNumber(dueDate);
  const now = toDayNumber(today);
  if (due === null || now === null) return null;
  return now - due;
};

export const agingBucket = (dueDate?: string | null, today: string = todayISO()): AgingBucket | null => {
  const days = daysPastDue(dueDate, today);
  if (days === null) return null;
  if (days <= 0) return "current";
  if (days <= 30) return "1-30";
  if (days <= 60) return "31-60";
  if (days <= 90) return "61-90";
  return "90+";
};

export const matchesAging = (dueDate: string | null | undefined, filter: string, today: string = todayISO()): boolean => {
  if (filter === "due-soon") {
    const days = daysPastDue(dueDate, today);
    return days !== null && days <= 0 && days >= -DUE_SOON_DAYS;
  }
  const bucket = agingBucket(dueDate, today);
  if (!bucket) return false;
  return filter === "overdue" ? bucket !== "current" : bucket === filter;
};

export const agingFilterLabel = (filter: string): string =>
  filter === "overdue"
    ? "Overdue"
    : filter === "due-soon"
    ? `Due in the next ${DUE_SOON_DAYS} days`
    : AGING_BUCKETS.find((b) => b.key === filter)?.label ?? filter;

/** Human summary of a due date relative to today, e.g. "12 days overdue" or "Due in 3 days". */
export const dueLabel = (dueDate?: string | null, today: string = todayISO()): string | null => {
  const days = daysPastDue(dueDate, today);
  if (days === null) return null;
  const plural = (n: number) => `${n} day${n === 1 ? "" : "s"}`;
  if (days > 0) return `${plural(days)} overdue`;
  if (days === 0) return "Due today";
  return `Due in ${plural(-days)}`;
};
