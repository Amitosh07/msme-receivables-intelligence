"""
Unit tests for invoice parser (text extraction, field parsing, validation, error handling, contract hardening).
Phase K: Tests for required fields, optional fields, varied Indian B2B layouts, and prediction separation.
"""

from datetime import date, timedelta
import io
import unittest
from unittest.mock import patch
import fitz

from backend.app.services.parser.base import ExtractedInvoice, ExtractionResult
from backend.app.services.parser.invoice_parser import InvoiceParser


def _make_pdf(text: str) -> bytes:
    """Helper to generate a valid in-memory PDF containing the given text."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), text, fontsize=11)
    return doc.tobytes()


def _make_scanned_pdf(text: str) -> bytes:
    """Helper to generate an image-only (scanned) PDF with no selectable text."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), text, fontsize=11)
    pix = page.get_pixmap(dpi=150)
    img_bytes = pix.tobytes("png")
    doc.close()

    # Create a new PDF with only the raster image
    img_doc = fitz.open()
    img_page = img_doc.new_page(width=pix.width, height=pix.height)
    img_page.insert_image(img_page.rect, stream=img_bytes)
    return img_doc.tobytes()


class TestInvoiceParser(unittest.TestCase):
    """Test suite for InvoiceParser extraction, contract hardening, and validation logic."""

    # ------------------------------------------------------------------
    # 1. Existing GSTIN / Buyer Block Tests
    # ------------------------------------------------------------------

    def test_extracts_valid_customer_gstin_from_buyer_block(self):
        text = (
            "TAX INVOICE\n"
            "Invoice #: INV-GSTIN-01\n"
            "Bill To: Acme Components Pvt. Ltd.\n"
            "GSTIN/UIN: 27AAPFU0939F1ZV\n"
            "Invoice Date: 2026-01-01\n"
            "Due Date: 2026-01-31\n"
            "Total Amount: INR 1,250.00\n"
        )
        invoice, missing = InvoiceParser._extract_fields(text, "text")
        self.assertEqual(missing, [])
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.customer_gstin, "27AAPFU0939F1ZV")
        self.assertEqual(invoice.customer_name, "Acme Components Pvt. Ltd.")

    def test_invalid_customer_gstin_is_not_returned(self):
        text = (
            "TAX INVOICE\n"
            "Invoice #: INV-GSTIN-02\n"
            "Buyer: Acme Components\n"
            "GST No.: NOT-AVAILABLE\n"
            "Invoice Date: 2026-01-01\n"
            "Due Date: 2026-01-31\n"
            "Total Amount: INR 1,250.00\n"
        )
        invoice, missing = InvoiceParser._extract_fields(text, "text")
        self.assertEqual(missing, [])
        self.assertIsNotNone(invoice)
        self.assertIsNone(invoice.customer_gstin)

    def test_structurally_valid_gstin_with_bad_checksum_is_not_returned(self):
        text = (
            "TAX INVOICE\n"
            "Invoice #: INV-GSTIN-CHK-01\n"
            "Buyer: Acme Components\n"
            "GSTIN: 27AAPFU0939F1ZU\n"
            "Invoice Date: 2026-01-01\n"
            "Due Date: 2026-01-31\n"
            "Total Amount: INR 1,250.00\n"
        )
        invoice, missing = InvoiceParser._extract_fields(text, "text")
        self.assertEqual(missing, [])
        self.assertIsNotNone(invoice)
        self.assertIsNone(invoice.customer_gstin)

    def test_customer_gstin_label_variants_and_split_value(self):
        for label in ("GSTIN", "GST No", "GST No.", "GST Identification Number"):
            with self.subTest(label=label):
                text = (
                    "TAX INVOICE\n"
                    "Invoice #: INV-GSTIN-03\n"
                    "Sold To: Acme Components\n"
                    f"{label}:\n"
                    "27AAPFU0939F1ZV\n"
                    "Invoice Date: 2026-01-01\n"
                    "Due Date: 2026-01-31\n"
                    "Total Amount: INR 1,250.00\n"
                )
                invoice, missing = InvoiceParser._extract_fields(text, "text")
                self.assertEqual(missing, [])
                self.assertIsNotNone(invoice)
                self.assertEqual(invoice.customer_gstin, "27AAPFU0939F1ZV")

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
        self.assertEqual(inv.due_date, date(2025, 4, 25))
        self.assertEqual(inv.amount, 45000.00)
        self.assertEqual(inv.customer_name, "Bharat Tech Solutions Pvt Ltd")

    def test_parse_empty_bytes(self):
        result = InvoiceParser.parse(b"")
        self.assertFalse(result.success)
        self.assertIn("empty", result.error.lower())

    def test_parse_corrupt_pdf(self):
        result = InvoiceParser.parse(b"This is not a PDF file format.")
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_parse_blank_pdf(self):
        doc = fitz.open()
        doc.new_page()
        blank_pdf = doc.tobytes()
        result = InvoiceParser.parse(blank_pdf)
        self.assertFalse(result.success)
        self.assertTrue(
            "blank" in result.error.lower()
            or "unable to extract" in result.error.lower()
            or "insufficient" in result.error.lower()
        )

    def test_parse_msme_cashflow_invoice_format(self):
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
        cases = [
            (
                "Modern SaaS",
                "INVOICE\nInvoice Number: SAAS-2026-101\nCustomer: Northwind Labs\n"
                "Issue Date: March 4, 2026\nPayment Due: April 3, 2026\n"
                "Amount Due: USD 1,299.50\n",
                "SAAS-2026-101", date(2026, 3, 4), date(2026, 4, 3), 1299.50, "USD",
                "Northwind Labs",
            ),
            (
                "Indian GST",
                "TAX INVOICE\nBill No: GST/DEL/88\nBill Date: 04.03.2026\n"
                "Buyer: Metro Infotech Pvt Ltd\n"
                "Terms of Payment: Net 15\nTaxable Value INR 10,000.00\n"
                "CGST INR 900.00\nGrand Total INR 11,800.00\n",
                "GST/DEL/88", date(2026, 3, 4), date(2026, 3, 19), 11800.00, "INR",
                "Metro Infotech Pvt Ltd",
            ),
            (
                "Alternate labels",
                "Bill To: Cedar Co\nDocument Number: DOC-CA-77\nDocument Date: 2026-03-04\n"
                "Pay By: 2026-03-19\nCurrency: CAD\nNet Payable: C$2,500.00\n",
                "DOC-CA-77", date(2026, 3, 4), date(2026, 3, 19), 2500.00, "CAD",
                "Cedar Co",
            ),
        ]
        for label, text, number, issued, due, amount, currency, customer in cases:
            with self.subTest(layout=label):
                result = InvoiceParser.parse(_make_pdf(text))
                self.assertTrue(result.success, result.error)
                inv = result.invoice
                self.assertEqual(inv.invoice_number, number)
                self.assertEqual(inv.invoice_date, issued)
                self.assertEqual(inv.due_date, due)
                self.assertEqual(inv.amount, amount)
                self.assertEqual(inv.currency, currency)
                self.assertEqual(inv.customer_name, customer)

    def test_currency_and_invoice_reference_variants(self):
        cases = [
            ("Invoice No: SIM/26-27/0426", "Grand Total: ₹1,25,000", "SIM/26-27/0426", 125000, "INR"),
            ("Invoice #: INV-2026-001", "Total Amount: Rs. 50,000", "INV-2026-001", 50000, "INR"),
            ("Inv No. SOIS/26-27/0213", "Amount Due: INR 125000", "SOIS/26-27/0213", 125000, "INR"),
        ]
        for number_line, amount_line, expected_number, amount, currency in cases:
            with self.subTest(number=expected_number):
                result = InvoiceParser.parse(_make_pdf(
                    f"TAX INVOICE\n{number_line}\nBill To: Zenith Technologies Ltd\n"
                    f"Invoice Date: 15/07/2026\nPayment Terms: Net 30\n{amount_line}\n"
                ))
                self.assertTrue(result.success, result.error)
                self.assertEqual(result.invoice.invoice_number, expected_number)
                self.assertEqual(result.invoice.amount, amount)
                self.assertEqual(result.invoice.currency, currency)
                self.assertEqual(result.invoice.customer_name, "Zenith Technologies Ltd")

    def test_invoice_number_on_next_layout_line(self):
        result = InvoiceParser.parse(_make_pdf(
            "TAX INVOICE\nInvoice No\nBILL-1042\nCustomer: Apex Engineering\n"
            "Date: 2026-06-01\nDue Date: 2026-06-16\nTotal: Rs 1,25,000\n"
        ))
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.invoice.invoice_number, "BILL-1042")
        self.assertEqual(result.invoice.customer_name, "Apex Engineering")

    def test_embedded_rupee_glyph_extracted_as_i_at_grand_total(self):
        result = InvoiceParser.parse(_make_pdf(
            "TAX INVOICE\nInvoice No: NSE/26-27/0596\nBill To: National Switchgears Ltd\n"
            "Invoice Date: 2026-07-12\nDue Date: 2026-08-11\n"
            "Grand Total\nI331,860.00\nPayment Terms: Net 30\n"
        ))
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.invoice.currency, "INR")
        self.assertEqual(result.invoice.invoice_number, "NSE/26-27/0596")
        self.assertEqual(result.invoice.amount, 331860.00)

    # ------------------------------------------------------------------
    # 2. Phase K Required & Optional Field Contract Tests
    # ------------------------------------------------------------------

    def test_parse_invoice_with_inr_symbol(self):
        """Invoice with ₹ symbol extracts amount and INR currency."""
        text = (
            "TAX INVOICE\n"
            "Invoice No: INV-INR-100\n"
            "Buyer: Reliance Retail Solutions\n"
            "Invoice Date: 2026-05-10\n"
            "Due Date: 2026-06-09\n"
            "Grand Total: ₹2,45,000.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertTrue(result.success, result.error)
        self.assertIsNotNone(result.invoice)
        self.assertEqual(result.invoice.amount, 245000.00)
        self.assertEqual(result.invoice.currency, "INR")
        self.assertEqual(result.invoice.customer_name, "Reliance Retail Solutions")

    def test_parse_invoice_without_currency_symbol(self):
        """
        IMPORTANT: Currency is NOT required.
        An invoice without any currency symbol/code must parse successfully.
        """
        text = (
            "TAX INVOICE\n"
            "Invoice No: INV-NOCURR-501\n"
            "Customer: Zenith Electronics\n"
            "Invoice Date: 2026-05-12\n"
            "Due Date: 2026-06-11\n"
            "Total Amount: 1,75,000.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertTrue(result.success, result.error)
        self.assertIsNotNone(result.invoice)
        self.assertEqual(result.invoice.amount, 175000.00)
        self.assertIsNone(result.invoice.currency)
        self.assertEqual(result.invoice.customer_name, "Zenith Electronics")

    def test_parse_invoice_with_grand_total(self):
        """Invoice with 'Grand Total' extracts amount accurately."""
        text = (
            "TAX INVOICE\n"
            "Invoice Number: GT-909\n"
            "Billed To: Supreme Plastics Corp\n"
            "Date: 2026-06-01\n"
            "Due Date: 2026-06-30\n"
            "Grand Total: 85,500.50\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.invoice.amount, 85500.50)

    def test_parse_invoice_with_total_amount(self):
        """Invoice with 'Total Amount' extracts amount accurately."""
        text = (
            "INVOICE\n"
            "Inv #: TA-404\n"
            "Client: Delta Pharma Ltd\n"
            "Invoice Date: 2026-06-15\n"
            "Payment Due: 2026-07-15\n"
            "Total Amount: INR 94,200.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.invoice.amount, 94200.00)

    def test_parse_invoice_with_explicit_due_date(self):
        """Invoice where due date is explicitly stated."""
        text = (
            "TAX INVOICE\n"
            "Invoice No: EXP-DUE-01\n"
            "Bill To: Alpha Industries\n"
            "Invoice Date: 2026-04-01\n"
            "Due Date: 2026-04-21\n"
            "Total Amount: 50,000.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.invoice.due_date, date(2026, 4, 21))

    def test_parse_invoice_with_derived_due_date_from_payment_terms(self):
        """Invoice where due date is derived from valid payment terms."""
        text = (
            "TAX INVOICE\n"
            "Invoice No: DER-TERMS-02\n"
            "Bill To: Beta Automations\n"
            "Invoice Date: 2026-05-01\n"
            "Payment Terms: Net 45\n"
            "Total Amount: 75,000.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertTrue(result.success, result.error)
        # 2026-05-01 + 45 days = 2026-06-15
        self.assertEqual(result.invoice.due_date, date(2026, 6, 15))

    def test_parse_invoice_optional_fields_absent(self):
        """Invoice parsing does NOT fail merely because optional fields are absent."""
        minimal_text = (
            "TAX INVOICE\n"
            "Invoice No: MIN-REQ-001\n"
            "Customer: Horizon Cables\n"
            "Invoice Date: 2026-02-15\n"
            "Due Date: 2026-03-15\n"
            "Total Amount: 30000.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(minimal_text))
        self.assertTrue(result.success, result.error)
        inv = result.invoice
        self.assertIsNotNone(inv)
        self.assertEqual(inv.invoice_number, "MIN-REQ-001")
        self.assertEqual(inv.customer_name, "Horizon Cables")
        self.assertIsNone(inv.customer_gstin)
        self.assertIsNone(inv.customer_ref)
        self.assertIsNone(inv.seller_gstin)
        self.assertIsNone(inv.purchase_order_number)
        self.assertIsNone(inv.cgst)
        self.assertIsNone(inv.sgst)
        self.assertIsNone(inv.igst)
        self.assertIsNone(inv.subtotal)

    def test_parse_invoice_optional_fields_extracted_when_present(self):
        """Optional fields (PO, GST breakdown, seller details) are cleanly extracted when present."""
        rich_text = (
            "HIND INDUSTRIAL TOOLS LTD\n"
            "GSTIN: 29ABCDE1234F1ZW\n"
            "TAX INVOICE\n"
            "Invoice No: HIT/26-27/0088\n"
            "PO No: PO-998822\n"
            "Invoice Date: 2026-07-01\n"
            "Due Date: 2026-07-31\n"
            "Details of Receiver | Billed to:\n"
            "Omega Engineering Works\n"
            "GSTIN: 27AAPFU0939F1ZV\n"
            "Taxable Value: 100,000.00\n"
            "CGST @ 9%: 9,000.00\n"
            "SGST @ 9%: 9,000.00\n"
            "Total Amount: ₹1,18,000.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(rich_text))
        self.assertTrue(result.success, result.error)
        inv = result.invoice
        self.assertEqual(inv.customer_name, "Omega Engineering Works")
        self.assertEqual(inv.customer_gstin, "27AAPFU0939F1ZV")
        self.assertEqual(inv.seller_gstin, "29ABCDE1234F1ZW")
        self.assertEqual(inv.purchase_order_number, "PO-998822")
        self.assertEqual(inv.taxable_amount, 100000.00)
        self.assertEqual(inv.cgst, 9000.00)
        self.assertEqual(inv.sgst, 9000.00)
        self.assertEqual(inv.amount, 118000.00)
        self.assertEqual(inv.currency, "INR")

    def test_parse_varied_seller_and_customer_arrangements(self):
        """Varied Indian B2B layout arrangements correctly distinguish customer from seller."""
        layouts = [
            (
                "Party Name label",
                "KAVERI FORGINGS PRIVATE LIMITED\n"
                "TAX INVOICE\n"
                "Invoice #: KF-102\n"
                "Invoice Date: 2026-08-01\n"
                "Due Date: 2026-08-31\n"
                "Party Name: Surya Solar Systems\n"
                "Total Amount: 55,000.00\n",
                "Surya Solar Systems",
            ),
            (
                "M/s Prefix in Buyer line",
                "TAX INVOICE\n"
                "Invoice No: MS-881\n"
                "Invoice Date: 2026-08-05\n"
                "Due Date: 2026-09-04\n"
                "Buyer: M/s Ananya Chemicals Pvt Ltd\n"
                "Total Amount: 62,000.00\n",
                "Ananya Chemicals Pvt Ltd",
            ),
            (
                "Buyer (Bill to) with address stack",
                "GLOBAL BEARINGS CORP\n"
                "TAX INVOICE\n"
                "Invoice Number: GBC-900\n"
                "Invoice Date: 2026-08-10\n"
                "Due Date: 2026-09-09\n"
                "Buyer (Bill to):\n"
                "Pinnacle Motors LLP\n"
                "Sector 18, Gurugram, Haryana\n"
                "Total Amount: 88,000.00\n",
                "Pinnacle Motors LLP",
            ),
        ]
        for name, text, expected_customer in layouts:
            with self.subTest(layout=name):
                result = InvoiceParser.parse(_make_pdf(text))
                self.assertTrue(result.success, result.error)
                self.assertEqual(result.invoice.customer_name, expected_customer)

    # ------------------------------------------------------------------
    # 3. Parsing Failure Tests (Enforcing Extraction Contract)
    # ------------------------------------------------------------------

    def test_malformed_missing_invoice_number_fails(self):
        """PARSING MUST FAIL when invoice_number is missing or unreliable."""
        text = (
            "TAX INVOICE\n"
            "Bill To: Acme Industrial Supplies\n"
            "Invoice Date: 2026-03-01\n"
            "Due Date: 2026-03-31\n"
            "Total Amount: $12,450.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "REQUIRED_FIELDS_MISSING")
        self.assertIn("invoice number", result.error.lower())

    def test_malformed_missing_customer_name_fails(self):
        """PARSING MUST FAIL when customer_name is missing or unreliable."""
        text = (
            "TAX INVOICE\n"
            "Invoice #: INV-2026-999\n"
            "Invoice Date: 2026-03-01\n"
            "Due Date: 2026-03-31\n"
            "Total Amount: $12,450.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "REQUIRED_FIELDS_MISSING")
        self.assertIn("customer name", result.error.lower())

    def test_malformed_missing_invoice_date_fails(self):
        """PARSING MUST FAIL when invoice_date is missing or unreliable."""
        text = (
            "TAX INVOICE\n"
            "Invoice #: INV-2026-888\n"
            "Bill To: Acme Industrial Supplies\n"
            "Due Date: 2026-03-31\n"
            "Total Amount: $12,450.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "REQUIRED_FIELDS_MISSING")
        self.assertIn("invoice date", result.error.lower())

    def test_malformed_missing_total_amount_fails(self):
        """PARSING MUST FAIL when total_amount is missing or unreliable."""
        text = (
            "TAX INVOICE\n"
            "Invoice #: INV-2026-777\n"
            "Bill To: Acme Industrial Supplies\n"
            "Invoice Date: 2026-03-01\n"
            "Due Date: 2026-03-31\n"
            "Thank you for your business.\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "REQUIRED_FIELDS_MISSING")
        self.assertIn("invoice total", result.error.lower())

    def test_malformed_missing_due_date_and_payment_terms_fails(self):
        """PARSING MUST FAIL when due_date cannot be explicitly extracted OR safely derived."""
        text = (
            "TAX INVOICE\n"
            "Invoice #: INV-2026-666\n"
            "Bill To: Acme Industrial Supplies\n"
            "Invoice Date: 2026-03-01\n"
            "Total Amount: $12,450.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "REQUIRED_FIELDS_MISSING")
        self.assertIn("due date", result.error.lower())

    def test_ocr_based_invoice_extraction(self):
        """
        Verify OCR tier handles scanned/raster documents when OCR is available.
        Uses mocked OCR output to test deterministic extraction contract across all test environments.
        """
        scanned_text = (
            "TAX INVOICE\n"
            "Invoice No: OCR-INV-555\n"
            "Buyer: Scanned Logistics Ltd\n"
            "Invoice Date: 2026-08-01\n"
            "Due Date: 2026-08-31\n"
            "Grand Total: INR 50,000.00\n"
        )
        # Create a PDF with raster image only (zero selectable text)
        raster_pdf = _make_scanned_pdf("placeholder")

        with patch("backend.app.services.parser.invoice_parser.ocr_availability", return_value=(True, "available")), \
             patch("backend.app.services.parser.invoice_parser.extract_text_via_ocr", return_value=scanned_text):
            result = InvoiceParser.parse(raster_pdf)

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.method, "ocr")
        self.assertEqual(result.invoice.invoice_number, "OCR-INV-555")
        self.assertEqual(result.invoice.customer_name, "Scanned Logistics Ltd")
        self.assertEqual(result.invoice.amount, 50000.00)
        self.assertEqual(result.invoice.currency, "INR")

    def test_prediction_separation_from_parser(self):
        """
        PREDICTION IS A SEPARATE CONCERN:
        Successful invoice parsing returns an ExtractedInvoice and does NOT encode prediction availability.
        Prediction availability is determined solely by the ML service layer.
        """
        text = (
            "TAX INVOICE\n"
            "Invoice No: INV-SEP-001\n"
            "Bill To: Brand New First Time Customer\n"
            "Invoice Date: 2026-09-01\n"
            "Due Date: 2026-09-30\n"
            "Total Amount: 10,000.00\n"
        )
        result = InvoiceParser.parse(_make_pdf(text))
        self.assertTrue(result.success)
        # The parser result strictly deals with extracted invoice data
        self.assertIsInstance(result.invoice, ExtractedInvoice)
        self.assertFalse(hasattr(result.invoice, "prediction_available"))


if __name__ == "__main__":
    unittest.main()
