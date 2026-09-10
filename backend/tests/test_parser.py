"""
Unit tests for invoice parser (text extraction, field parsing, validation, error handling).
"""

import unittest
from datetime import date
import fitz

from backend.app.services.parser.base import ExtractedInvoice, ExtractionResult
from backend.app.services.parser.invoice_parser import InvoiceParser


def _make_pdf(text: str) -> bytes:
    """Helper to generate a valid in-memory PDF containing the given text."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), text, fontsize=11)
    return doc.tobytes()


class TestInvoiceParser(unittest.TestCase):
    """Test suite for InvoiceParser extraction and validation logic."""

    def test_parse_valid_invoice_pdf(self):
        """Standard valid invoice text extracts all fields correctly."""
        invoice_text = (
            "TAX INVOICE\n"
            "Invoice #: INV-2025-0042\n"
            "Bill To: Acme Industrial Supplies\n"
            "Customer ID: CUST-8821\n"
            "Invoice Date: 2025-03-01\n"
            "Due Date: 2025-03-31\n"
            "Payment Terms: Net 30\n"
            "Total Amount: $12,450.00\n"
            "Thank you for your business."
        )
        pdf_bytes = _make_pdf(invoice_text)
        result = InvoiceParser.parse(pdf_bytes)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.invoice)
        inv = result.invoice
        self.assertEqual(inv.invoice_number, "INV-2025-0042")
        self.assertEqual(inv.invoice_date, date(2025, 3, 1))
        self.assertEqual(inv.due_date, date(2025, 3, 31))
        self.assertEqual(inv.amount, 12450.00)
        self.assertEqual(inv.currency, "USD")
        self.assertEqual(inv.customer_name, "Acme Industrial Supplies")
        self.assertEqual(inv.customer_ref, "CUST-8821")
        self.assertEqual(inv.payment_terms, "Net 30")
        self.assertEqual(inv.extraction_method, "text")
        self.assertGreaterEqual(inv.confidence, 0.9)

    def test_parse_inr_currency_and_derived_due_date(self):
        """Invoice with INR currency and terms derives due date when not explicitly stated."""
        invoice_text = (
            "TAX INVOICE / BILL OF SUPPLY\n"
            "Invoice No: INV-IN-991\n"
            "Bill To: Bharat Tech Solutions Pvt Ltd\n"
            "Date: 2025-04-10\n"
            "Payment Terms: Net 15\n"
            "Total: INR 45,000.00\n"
        )
        pdf_bytes = _make_pdf(invoice_text)
        result = InvoiceParser.parse(pdf_bytes)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.invoice)
        inv = result.invoice
        self.assertEqual(inv.invoice_number, "INV-IN-991")
        self.assertEqual(inv.currency, "INR")
        self.assertEqual(inv.invoice_date, date(2025, 4, 10))
        # Net 15 should derive 2025-04-10 + 15 days = 2025-04-25
        self.assertEqual(inv.due_date, date(2025, 4, 25))
        self.assertEqual(inv.amount, 45000.00)

    def test_parse_empty_bytes(self):
        """Empty byte input returns failure gracefully without crash."""
        result = InvoiceParser.parse(b"")
        self.assertFalse(result.success)
        self.assertIn("empty", result.error.lower())

    def test_parse_corrupt_pdf(self):
        """Corrupt non-PDF bytes return failure gracefully."""
        result = InvoiceParser.parse(b"This is not a PDF file format.")
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_parse_blank_pdf(self):
        """Blank PDF with no text returns failure with appropriate error."""
        doc = fitz.open()
        doc.new_page()  # Blank page
        blank_pdf = doc.tobytes()

        result = InvoiceParser.parse(blank_pdf)
        self.assertFalse(result.success)
        self.assertTrue(
            "blank" in result.error.lower()
            or "unable to extract" in result.error.lower()
            or "insufficient" in result.error.lower()
        )

    def test_parse_missing_total_amount(self):
        """PDF missing total amount fails validation safely."""
        text = (
            "INVOICE\n"
            "Invoice #: INV-2025-9999\n"
            "Bill To: Test Customer Corp\n"
            "Date: 2025-01-01\n"
            "Due Date: 2025-01-31\n"
            "No charges listed here."
        )
        pdf_bytes = _make_pdf(text)
        result = InvoiceParser.parse(pdf_bytes)

        self.assertFalse(result.success)
        self.assertIn("required", result.error.lower())

    def test_parse_missing_invoice_number(self):
        """PDF missing invoice number fails safely."""
        text = (
            "BILLING STATEMENT\n"
            "Bill To: Test Customer Corp\n"
            "Date: 2025-01-01\n"
            "Due Date: 2025-01-31\n"
            "Total Amount: $5,000.00\n"
        )
        pdf_bytes = _make_pdf(text)
        result = InvoiceParser.parse(pdf_bytes)

        self.assertFalse(result.success)
        self.assertIn("required", result.error.lower())

    def test_parse_msme_cashflow_invoice_format(self):
        """
        Regression test for MSME invoice format containing:
        - Invoice No: APC/26-27/0147
        - Invoice Date: 15/07/2026
        - Grand Total ₹159,241.00
        - Payment Terms: Net 30
        - Due Date: 14/08/2026
        - Currency: INR
        """
        invoice_text = (
            "TAX INVOICE\n"
            "ARAVIND PRECISION COMPONENTS\n"
            "Plot 18, Industrial Estate, Peenya Phase II, Bengaluru\n"
            "Invoice No: APC/26-27/0147\n"
            "Invoice Date: 15/07/2026\n"
            "Due Date: 14/08/2026\n"
            "Payment Terms: Net 30\n"
            "Currency: INR\n"
            "BILL TO\n"
            "SHIP TO\n"
            "Vardhan Industrial Solutions Pvt. Ltd.\n"
            "Unit 7, Bommasandra Industrial Area, Bengaluru\n"
            "Taxable Value\n"
            "134,950.00\n"
            "CGST @ 9%\n"
            "12,145.50\n"
            "SGST @ 9%\n"
            "12,145.50\n"
            "Grand Total ₹159,241.00\n"
            "Amount in Words: Rupees One Lakh Fifty-Nine Thousand Two Hundred Forty-One Only."
        )
        pdf_bytes = _make_pdf(invoice_text)
        result = InvoiceParser.parse(pdf_bytes)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.invoice)
        inv = result.invoice
        self.assertEqual(inv.invoice_number, "APC/26-27/0147")
        self.assertEqual(inv.invoice_date, date(2026, 7, 15))
        self.assertEqual(inv.due_date, date(2026, 8, 14))
        self.assertEqual(inv.amount, 159241.00)
        self.assertEqual(inv.currency, "INR")
        self.assertEqual(inv.payment_terms, "Net 30")
        self.assertEqual(inv.customer_name, "Vardhan Industrial Solutions Pvt. Ltd.")

    def test_parse_msme_cashflow_invoice_multiline_font_artifact(self):
        """Regression test for real PDF font glyph artifact where Rupee symbol extracts as 'I' on a newline."""
        invoice_text = (
            "TAX INVOICE\n"
            "Invoice No: APC/26-27/0147\n"
            "Invoice Date: 15/07/2026\n"
            "Due Date: 14/08/2026\n"
            "Payment Terms: Net 30\n"
            "Currency: INR\n"
            "BILL TO\n"
            "Vardhan Industrial Solutions Pvt. Ltd.\n"
            "Grand Total\n"
            "I159,241.00\n"
        )
        pdf_bytes = _make_pdf(invoice_text)
        result = InvoiceParser.parse(pdf_bytes)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.invoice)
        inv = result.invoice
        self.assertEqual(inv.invoice_number, "APC/26-27/0147")
        self.assertEqual(inv.invoice_date, date(2026, 7, 15))
        self.assertEqual(inv.due_date, date(2026, 8, 14))
        self.assertEqual(inv.amount, 159241.00)
        self.assertEqual(inv.currency, "INR")

    def test_parse_varied_realistic_layouts(self):
        """Regression corpus: labels, dates, currencies, and tables vary by template."""
        cases = [
            (
                "Modern SaaS",
                "INVOICE\nInvoice Number: SAAS-2026-101\nCustomer: Northwind Labs\n"
                "Issue Date: March 4, 2026\nPayment Due: April 3, 2026\n"
                "Amount Due: USD 1,299.50\n",
                "SAAS-2026-101", date(2026, 3, 4), date(2026, 4, 3), 1299.50, "USD",
            ),
            (
                "Indian GST",
                "TAX INVOICE\nBill No: GST/DEL/88\nBill Date: 04.03.2026\n"
                "Terms of Payment: Net 15\nTaxable Value INR 10,000.00\n"
                "CGST INR 900.00\nGrand Total INR 11,800.00\n",
                "GST/DEL/88", date(2026, 3, 4), date(2026, 3, 19), 11800.00, "INR",
            ),
            (
                "Alternate labels",
                "Bill To: Cedar Co\nDocument Number: DOC-CA-77\nDocument Date: 2026-03-04\n"
                "Pay By: 2026-03-19\nCurrency: CAD\nNet Payable: C$2,500.00\n",
                "DOC-CA-77", date(2026, 3, 4), date(2026, 3, 19), 2500.00, "CAD",
            ),
        ]
        for label, text, number, issued, due, amount, currency in cases:
            with self.subTest(layout=label):
                result = InvoiceParser.parse(_make_pdf(text))
                self.assertTrue(result.success, result.error)
                inv = result.invoice
                self.assertEqual(inv.invoice_number, number)
                self.assertEqual(inv.invoice_date, issued)
                self.assertEqual(inv.due_date, due)
                self.assertEqual(inv.amount, amount)
                self.assertEqual(inv.currency, currency)

    def test_currency_and_invoice_reference_variants(self):
        """Common Indian labels/symbols and invoice identifiers remain parseable."""
        cases = [
            ("Invoice No: SIM/26-27/0426", "Grand Total: ₹1,25,000", "SIM/26-27/0426", 125000, "INR"),
            ("Invoice #: INV-2026-001", "Total Amount: Rs. 50,000", "INV-2026-001", 50000, "INR"),
            ("Inv No. SOIS/26-27/0213", "Amount Due: INR 125000", "SOIS/26-27/0213", 125000, "INR"),
        ]
        for number_line, amount_line, expected_number, amount, currency in cases:
            with self.subTest(number=expected_number):
                result = InvoiceParser.parse(_make_pdf(
                    f"TAX INVOICE\n{number_line}\nInvoice Date: 15/07/2026\n"
                    f"Payment Terms: Net 30\n{amount_line}\n"
                ))
                self.assertTrue(result.success, result.error)
                self.assertEqual(result.invoice.invoice_number, expected_number)
                self.assertEqual(result.invoice.amount, amount)
                self.assertEqual(result.invoice.currency, currency)

    def test_invoice_number_on_next_layout_line(self):
        result = InvoiceParser.parse(_make_pdf(
            "TAX INVOICE\nInvoice No\nBILL-1042\nDate: 2026-06-01\n"
            "Due Date: 2026-06-16\nTotal: Rs 1,25,000\n"
        ))
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.invoice.invoice_number, "BILL-1042")

    def test_embedded_rupee_glyph_extracted_as_i_at_grand_total(self):
        """Regression: native PDF extraction maps an embedded ₹ glyph to I."""
        result = InvoiceParser.parse(_make_pdf(
            "TAX INVOICE\nInvoice No: NSE/26-27/0596\nInvoice Date: 2026-07-12\n"
            "Due Date: 2026-08-11\nGrand Total\nI331,860.00\nPayment Terms: Net 30\n"
        ))
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.invoice.currency, "INR")
        self.assertEqual(result.invoice.invoice_number, "NSE/26-27/0596")
        self.assertEqual(result.invoice.amount, 331860.00)


if __name__ == "__main__":
    unittest.main()
