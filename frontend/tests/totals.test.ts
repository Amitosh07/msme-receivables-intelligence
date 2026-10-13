import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { barWidths, forecastCoverage, percentShares, sumMoney } from "../src/lib/totals.ts";

const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);

describe("sumMoney", () => {
  it("adds in whole paise so totals never drift", () => {
    assert.equal(0.1 + 0.2 === 0.3, false);
    assert.equal(sumMoney([0.1, 0.2]), 0.3);
    assert.equal(sumMoney([12000.5, 30000.75, 56800]), 98801.25);
    assert.equal(sumMoney(Array(1000).fill(0.01)), 10);
  });

  it("ignores values that are not numbers", () => {
    assert.equal(sumMoney([100, Number.NaN, Number.POSITIVE_INFINITY, 0.5]), 100.5);
    assert.equal(sumMoney([]), 0);
  });
});

describe("percentShares", () => {
  it("always adds up to exactly 100", () => {
    assert.deepEqual(percentShares([1, 1, 1]), [34, 33, 33]);
    assert.equal(sum(percentShares([51800, 5000, 42001.25])), 100);
    for (const amounts of [[1, 2, 3, 4, 5], [0.01, 999999.99], [7, 7, 7, 7, 7, 7, 7]]) {
      assert.equal(sum(percentShares(amounts)), 100, amounts.join(","));
    }
  });

  it("is all zero when nothing is owed", () => {
    assert.deepEqual(percentShares([0, 0, 0]), [0, 0, 0]);
    assert.deepEqual(percentShares([]), []);
  });

  it("treats negative or invalid amounts as zero", () => {
    assert.deepEqual(percentShares([-50, 50]), [0, 100]);
    assert.deepEqual(percentShares([Number.NaN, 10]), [0, 100]);
  });
});

describe("aging bar widths", () => {
  const aging = [
    { key: "current", amount: 56800 },
    { key: "1-30", amount: 30000.75 },
    { key: "31-60", amount: 12000.5 },
    { key: "61-90", amount: 0 },
    { key: "90+", amount: 0 },
  ];

  it("splits the bar in proportion to each bucket's remaining balance", () => {
    const widths = barWidths(aging.map((b) => b.amount));
    assert.ok(Math.abs(sum(widths) - 100) < 1e-9);
    assert.ok(Math.abs(widths[0] - (56800 / 98801.25) * 100) < 1e-9);
    assert.equal(widths[3], 0);
  });

  it("matches the outstanding total once undated balances are added back", () => {
    const undated = 1234.56;
    const outstanding = 100035.81;
    assert.equal(sumMoney([...aging.map((b) => b.amount), undated]), outstanding);
  });

  it("is empty when there is no balance to age", () => {
    assert.deepEqual(barWidths([0, 0]), [0, 0]);
  });
});

describe("forecastCoverage", () => {
  it("splits outstanding into model dates, due dates and unscheduled", () => {
    assert.deepEqual(forecastCoverage(98801.25, 51800, 5000), { model: 52, dueDate: 5, unscheduled: 43 });
  });

  it("reports full model coverage without a remainder", () => {
    assert.deepEqual(forecastCoverage(1000, 1000, 0), { model: 100, dueDate: 0, unscheduled: 0 });
  });

  it("never reports a negative remainder", () => {
    const share = forecastCoverage(100, 70, 40);
    assert.equal(share.unscheduled, 0);
    assert.equal(share.model + share.dueDate, 100);
  });
});
