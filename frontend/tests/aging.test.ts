import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  addDaysISO,
  agingBucket,
  daysPastDue,
  dueLabel,
  isOutstanding,
  matchesAging,
  todayISO,
} from "../src/lib/aging.ts";

const TODAY = "2026-10-12";
const daysAgo = (n: number) => addDaysISO(TODAY, -n);

describe("daysPastDue", () => {
  it("counts whole calendar days after the due date", () => {
    assert.equal(daysPastDue(TODAY, TODAY), 0);
    assert.equal(daysPastDue(daysAgo(1), TODAY), 1);
    assert.equal(daysPastDue(addDaysISO(TODAY, 5), TODAY), -5);
  });

  it("uses only the date part of timestamps", () => {
    assert.equal(daysPastDue("2026-10-01T23:59:59Z", TODAY), 11);
  });

  it("returns null when there is no usable due date", () => {
    assert.equal(daysPastDue(null, TODAY), null);
    assert.equal(daysPastDue(undefined, TODAY), null);
    assert.equal(daysPastDue("not a date", TODAY), null);
  });

  it("crosses month, year and leap-day boundaries", () => {
    assert.equal(addDaysISO("2028-02-28", 1), "2028-02-29");
    assert.equal(addDaysISO("2026-12-31", 1), "2027-01-01");
    assert.equal(daysPastDue("2028-02-28", "2028-03-01"), 2);
    assert.equal(daysPastDue("2026-12-31", "2027-01-30"), 30);
  });
});

describe("agingBucket", () => {
  const cases: [number, string][] = [
    [-30, "current"],
    [0, "current"],
    [1, "1-30"],
    [30, "1-30"],
    [31, "31-60"],
    [60, "31-60"],
    [61, "61-90"],
    [90, "61-90"],
    [91, "90+"],
    [400, "90+"],
  ];
  for (const [days, bucket] of cases) {
    it(`puts an invoice ${days} days past due in ${bucket}`, () => {
      assert.equal(agingBucket(daysAgo(days), TODAY), bucket);
    });
  }

  it("does not age invoices without a due date", () => {
    assert.equal(agingBucket(null, TODAY), null);
  });

  it("assigns every day to exactly one bucket", () => {
    for (let days = -10; days <= 200; days += 1) {
      const filters = ["current", "1-30", "31-60", "61-90", "90+"].filter((f) => matchesAging(daysAgo(days), f, TODAY));
      assert.equal(filters.length, 1, `day ${days} matched ${filters.join(",")}`);
    }
  });
});

describe("matchesAging filters", () => {
  it("overdue starts the day after the due date", () => {
    assert.equal(matchesAging(TODAY, "overdue", TODAY), false);
    assert.equal(matchesAging(daysAgo(1), "overdue", TODAY), true);
  });

  it("due soon covers today through seven days ahead", () => {
    assert.equal(matchesAging(TODAY, "due-soon", TODAY), true);
    assert.equal(matchesAging(addDaysISO(TODAY, 7), "due-soon", TODAY), true);
    assert.equal(matchesAging(addDaysISO(TODAY, 8), "due-soon", TODAY), false);
    assert.equal(matchesAging(daysAgo(1), "due-soon", TODAY), false);
  });

  it("never matches an undated invoice", () => {
    for (const filter of ["current", "overdue", "due-soon", "90+"]) {
      assert.equal(matchesAging(null, filter, TODAY), false);
    }
  });
});

describe("labels and status", () => {
  it("describes the due date relative to today", () => {
    assert.equal(dueLabel(daysAgo(1), TODAY), "1 day overdue");
    assert.equal(dueLabel(daysAgo(12), TODAY), "12 days overdue");
    assert.equal(dueLabel(TODAY, TODAY), "Due today");
    assert.equal(dueLabel(addDaysISO(TODAY, 3), TODAY), "Due in 3 days");
    assert.equal(dueLabel(null, TODAY), null);
  });

  it("treats unpaid and part-paid invoices as outstanding", () => {
    assert.equal(isOutstanding("OPEN"), true);
    assert.equal(isOutstanding("PARTIAL"), true);
    assert.equal(isOutstanding("PAID"), false);
    assert.equal(isOutstanding(null), false);
  });

  it("uses the local calendar date, not UTC", () => {
    assert.equal(todayISO(new Date(2026, 9, 12, 23, 30)), "2026-10-12");
    assert.equal(todayISO(new Date(2026, 9, 12, 0, 15)), "2026-10-12");
  });
});
