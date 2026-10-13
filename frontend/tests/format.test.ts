import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { invoiceLabel, money, paymentRecord } from "../src/lib/format.ts";

const digits = (value: string) => value.replace(/[^\d.-]/g, "");

describe("money", () => {
  it("shows rupees with exactly two decimals", () => {
    assert.ok(money(1234.5, "INR").includes("₹"));
    assert.equal(digits(money(1234.5, "INR")), "1234.50");
    assert.equal(digits(money(0.1 + 0.2, "INR")), "0.30");
    assert.equal(digits(money(30000.75, "INR")), "30000.75");
  });

  it("falls back to rupees for missing or invalid currencies", () => {
    assert.ok(money(10, null).includes("₹"));
    assert.ok(money(10, "rupees").includes("₹"));
  });

  it("shows zero for missing amounts", () => {
    assert.equal(digits(money(undefined, "INR")), "0.00");
    assert.equal(digits(money(Number.NaN, "INR")), "0.00");
  });
});

describe("invoiceLabel", () => {
  it("replaces opaque ids with a short manual-entry label", () => {
    assert.equal(invoiceLabel("5053233d-4447-41b5-bf85-55a800d710cd"), "MAN-505323");
  });

  it("keeps real invoice numbers", () => {
    assert.equal(invoiceLabel("INV-2026-0042"), "INV-2026-0042");
    assert.equal(invoiceLabel(null), "—");
  });
});

describe("paymentRecord", () => {
  it("summarises on-time share and average lateness", () => {
    assert.equal(
      paymentRecord({ settled_invoice_count: 3, on_time_rate: 2 / 3, average_days_late: 2.3 }),
      "2 of 3 paid on time · 2.3 days late on average"
    );
  });

  it("describes early payers and customers with no history", () => {
    assert.equal(
      paymentRecord({ settled_invoice_count: 2, on_time_rate: 1, average_days_late: -1.5 }),
      "2 of 2 paid on time · 1.5 days early on average"
    );
    assert.equal(paymentRecord({ settled_invoice_count: 0 }), "No paid invoices on record");
  });
});
