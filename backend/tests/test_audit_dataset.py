"""
test_audit_dataset.py — Unit tests for Phase 0 Data Audit and Validation Utilities

Can be run via standard library:
    python -m unittest discover backend/tests
or via pytest:
    pytest backend/tests
"""

import unittest
import numpy as np
import pandas as pd

from backend.ml.data.audit_dataset import (
    analyze_customers_and_cold_start,
    analyze_numeric_and_categorical,
    analyze_payment_status,
    analyze_targets,
    compute_distribution_stats,
    derive_targets,
    detect_duplicates,
    parse_dataset_dates,
    validate_required_columns,
)


class TestAuditDataset(unittest.TestCase):
    def setUp(self):
        """Minimal synthetic dataset matching Kaggle raw schema."""
        self.sample_raw_df = pd.DataFrame(
            {
                "business_code": ["U001", "U001", "CA02", "U001"],
                "cust_number": ["CUST1", "CUST1", "CUST2", "CUST3"],
                "name_customer": ["Corp A", "Corp A", "Corp B", "Corp C"],
                "clear_date": [
                    "2/11/2020 0:00",
                    "8/8/2019 0:00",
                    None,
                    "11/25/2019 0:00",
                ],
                "buisness_year": [2020, 2019, 2020, 2019],
                "doc_id": [1001, 1002, 1003, 1004],
                "posting_date": [
                    "1/26/2020",
                    "7/22/2019",
                    "3/30/2020",
                    "11/13/2019",
                ],
                "document_create_date": [20200125, 20190722, 20200330, 20191113],
                "document_create_date.1": [20200126, 20190722, 20200330, 20191113],
                "due_in_date": [20200210, 20190811, 20200410, 20191128],
                "invoice_currency": ["USD", "USD", "CAD", "USD"],
                "document type": ["RV", "RV", "RV", "RV"],
                "posting_id": [1, 1, 1, 1],
                "area_business": [np.nan, np.nan, np.nan, np.nan],
                "total_open_amount": [54273.28, 79656.60, 3299.70, 33133.29],
                "baseline_create_date": [20200126, 20190722, 20200331, 20191113],
                "cust_payment_terms": ["NAH4", "NAD1", "CA10", "NAH4"],
                "invoice_id": [1001.0, 1002.0, 1003.0, 1004.0],
                "isOpen": [0, 0, 1, 0],
            }
        )

    def test_validate_required_columns_success(self):
        is_valid, missing = validate_required_columns(self.sample_raw_df)
        self.assertTrue(is_valid)
        self.assertEqual(len(missing), 0)

    def test_validate_required_columns_missing(self):
        df_missing = self.sample_raw_df.drop(columns=["due_in_date", "clear_date"])
        is_valid, missing = validate_required_columns(df_missing)
        self.assertFalse(is_valid)
        self.assertIn("due_in_date", missing)
        self.assertIn("clear_date", missing)

    def test_detect_duplicates(self):
        df = pd.DataFrame(
            {
                "doc_id": [101, 102, 101],
                "invoice_id": [101.0, 102.0, 101.0],
                "val": ["a", "b", "a"],
            }
        )
        dup = detect_duplicates(df)
        self.assertEqual(dup["total_rows"], 3)
        self.assertEqual(dup["exact_duplicate_rows"], 1)
        self.assertEqual(dup["doc_id_duplicates"], 1)
        self.assertEqual(dup["invoice_id_duplicates"], 1)
        self.assertEqual(dup["unique_doc_ids"], 2)

    def test_parse_dataset_dates_non_mutating(self):
        orig_cols = list(self.sample_raw_df.columns)
        parsed = parse_dataset_dates(self.sample_raw_df)

        # Input DataFrame must NOT be mutated
        self.assertEqual(list(self.sample_raw_df.columns), orig_cols)
        self.assertNotIn("clear_date_parsed", self.sample_raw_df.columns)

        # Parsed DataFrame has datetime fields
        self.assertIn("clear_date_parsed", parsed.columns)
        self.assertIn("posting_date_parsed", parsed.columns)
        self.assertIn("due_in_date_parsed", parsed.columns)
        self.assertIn("baseline_create_date_parsed", parsed.columns)

        # Check date conversions
        self.assertEqual(parsed["posting_date_parsed"].iloc[0], pd.Timestamp("2020-01-26"))
        self.assertEqual(parsed["due_in_date_parsed"].iloc[0], pd.Timestamp("2020-02-10"))
        self.assertEqual(parsed["clear_date_parsed"].iloc[0], pd.Timestamp("2020-02-11"))
        # Row 2 has null clear_date
        self.assertTrue(pd.isna(parsed["clear_date_parsed"].iloc[2]))

    def test_analyze_payment_status_null_clear_date(self):
        status = analyze_payment_status(self.sample_raw_df)
        self.assertEqual(status["total_records"], 4)
        self.assertEqual(status["paid_records"], 3)
        self.assertEqual(status["unpaid_censored_records"], 1)
        self.assertEqual(status["paid_pct"], 75.0)
        self.assertEqual(status["unpaid_censored_pct"], 25.0)
        self.assertTrue(status["is_open_clear_date_exact_alignment"])

    def test_derive_targets_late_and_ontime(self):
        parsed = parse_dataset_dates(self.sample_raw_df)
        targets_df = derive_targets(parsed)

        # Only 3 paid rows should be in targets_df
        self.assertEqual(len(targets_df), 3)
        self.assertIn("delay_days", targets_df.columns)
        self.assertIn("days_until_payment", targets_df.columns)
        self.assertIn("is_late", targets_df.columns)

        # Row 0: due 2020-02-10, clear 2020-02-11 -> delay = +1 day -> is_late = 1
        row0 = targets_df.iloc[0]
        self.assertEqual(row0["delay_days"], 1)
        self.assertEqual(row0["is_late"], 1)
        self.assertEqual(row0["days_until_payment"], 16)

        # Row 1: due 2019-08-11, clear 2019-08-08 -> delay = -3 days -> is_late = 0
        row1 = targets_df.iloc[1]
        self.assertEqual(row1["delay_days"], -3)
        self.assertEqual(row1["is_late"], 0)
        self.assertEqual(row1["days_until_payment"], 17)

        # Row 3: due 2019-11-28, clear 2019-11-25 -> delay = -3 days -> is_late = 0
        row3 = targets_df.iloc[2]
        self.assertEqual(row3["delay_days"], -3)
        self.assertEqual(row3["is_late"], 0)

    def test_compute_distribution_stats(self):
        series = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        stats = compute_distribution_stats(series)
        self.assertEqual(stats["count"], 5)
        self.assertEqual(stats["min"], 10.0)
        self.assertEqual(stats["max"], 50.0)
        self.assertEqual(stats["median"], 30.0)
        self.assertEqual(stats["mean"], 30.0)

    def test_customer_cold_start_breakdown(self):
        cust_stats = analyze_customers_and_cold_start(self.sample_raw_df)
        self.assertEqual(cust_stats["total_unique_customers"], 3)
        self.assertEqual(cust_stats["customers_with_1_invoice"], 2)
        self.assertEqual(cust_stats["customers_with_2_to_5_invoices"], 1)

        osc = cust_stats["open_set_cold_start"]
        self.assertEqual(osc["unique_customers_in_open_set"], 1)
        self.assertEqual(osc["open_customers_cold_start"], 1)
        self.assertEqual(osc["open_customers_with_prior_history"], 0)


if __name__ == "__main__":
    unittest.main()
