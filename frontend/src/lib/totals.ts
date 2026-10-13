const toPaise = (value: number): number => (Number.isFinite(value) ? Math.round(value * 100) : 0);

/** Sum money in whole paise so float drift (0.1 + 0.2) never reaches the screen. */
export const sumMoney = (values: number[]): number => values.reduce((sum, value) => sum + toPaise(value), 0) / 100;

/** Exact share of each amount as a percentage of their total (for bar widths); all zero when nothing is owed. */
export const barWidths = (amounts: number[]): number[] => {
  const clean = amounts.map((a) => (Number.isFinite(a) && a > 0 ? a : 0));
  const total = clean.reduce((sum, a) => sum + a, 0);
  return clean.map((a) => (total > 0 ? (a / total) * 100 : 0));
};

/** Whole percentages that always add up to exactly 100 (largest remainder), or all zero when nothing is owed. */
export const percentShares = (amounts: number[]): number[] => {
  const raw = barWidths(amounts);
  if (!raw.some((v) => v > 0)) return raw.map(() => 0);
  const floors = raw.map(Math.floor);
  let remaining = 100 - floors.reduce((sum, v) => sum + v, 0);
  const order = raw.map((v, i) => [v - floors[i], i] as const).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  for (const [, i] of order) {
    if (remaining <= 0) break;
    floors[i] += 1;
    remaining -= 1;
  }
  return floors;
};

export interface ForecastCoverage { model: number; dueDate: number; unscheduled: number }

/** How the outstanding balance splits between model dates, due dates and no usable date, in whole percent. */
export const forecastCoverage = (outstanding: number, modelDated: number, dueDateDated: number): ForecastCoverage => {
  const rest = Math.max(0, sumMoney([outstanding, -modelDated, -dueDateDated]));
  const [model, dueDate, unscheduled] = percentShares([modelDated, dueDateDated, rest]);
  return { model, dueDate, unscheduled };
};
