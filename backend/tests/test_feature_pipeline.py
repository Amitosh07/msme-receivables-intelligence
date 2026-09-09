"""
test_feature_pipeline.py — Unit Tests for Phase 1 Data Pipeline & As-Of Feature Engineering

Tests verify:
1. Deduplication
2. Target calculation (late, on-time, days_until_payment)
3. Open invoices (unpaid records have no fabricated targets)
4. As-of feature engineering & zero future leakage
5. Cold-start indicator and train-only median imputation
6. Temporal split monotonicity
7. Prohibited column leakage prevention
"""

import unittest
from datetime import datetime
import numpy as np
import pandas as pd

from backend.ml.data.clean_data import construct_canonical_dataset, deduplicate_records
from backend.ml.features.as_of_features import compute_customer_as_of_features
from backend.ml.features.build_features import (
    FEATURE_COLUMNS,
    PROHIBITED_COLUMNS,
    assert_no_leakage,
    partition_temporal_splits,
)
from backend.ml.features.transformations import (
    TrainingImputationTransformer,
    compute_invoice_features,
)


class TestFeaturePipeline(unittest.TestCase):
    def setUp(self):
        """Build a minimal synthetic dataset matching the Kaggle raw schema."""
        self.raw_sample = pd.DataFrame(
            {
                "business_code": ["U001", "U001", "U001", "CA02", "U001"],
                "cust_number": ["CUST_A", "CUST_A", "CUST_A", "CUST_B", "CUST_A"],
                "name_customer": ["Corp A", "Corp A", "Corp A", "Corp B", "Corp A"],
                "clear_date": [
                    "1/10/2019 0:00",
                    "3/1/2019 0:00",
                    None,
                    "5/1/2019 0:00",
                    "1/10/2019 0:00",  # Duplicate row
                ],
                "buisness_year": [2019, 2019, 2019, 2019, 2019],
                "doc_id": [101, 102, 103, 104, 101],
                "posting_date": [
                    "1/1/2019",
                    "2/1/2019",
                    "4/1/2019",
                    "4/15/2019",
                    "1/1/2019",
                ],
                "document_create_date": [20190101, 20190201, 20190401, 20190415, 20190101],
                "document_create_date.1": [20190101, 20190201, 20190401, 20190415, 20190101],
                "due_in_date": [20190115, 20190215, 20190415, 20190430, 20190115],
                "invoice_currency": ["USD", "USD", "USD", "CAD", "USD"],
                "document type": ["RV", "RV", "RV", "RV", "RV"],
                "posting_id": [1, 1, 1, 1, 1],
                "area_business": [np.nan, np.nan, np.nan, np.nan, np.nan],
                "total_open_amount": [1000.0, 2000.0, 3000.0, 4000.0, 1000.0],
                "baseline_create_date": [20190101, 20190201, 20190401, 20190415, 20190101],
                "cust_payment_terms": ["NAA8", "NAA8", "NAA8", "CA10", "NAA8"],
                "invoice_id": [101.0, 102.0, 103.0, 104.0, 101.0],
                "isOpen": [0, 0, 1, 0, 0],
            }
        )

    def test_deduplication(self):
        """Verify exact duplicate rows are removed deterministically."""
        deduped, count = deduplicate_records(self.raw_sample)
        self.assertEqual(count, 1)
        self.assertEqual(len(deduped), 4)

    def test_target_calculation(self):
        """Verify delay_days, is_late, and days_until_payment logic."""
        canonical, _ = construct_canonical_dataset(self.raw_sample)

        # Row 101: invoice Jan 1, due Jan 15, clear Jan 10
        # Paid early: delay = -5 days -> is_late = 0, days_until_payment = 9
        row101 = canonical[canonical["invoice_id"] == "101"].iloc[0]
        self.assertEqual(row101["delay_days"], -5)
        self.assertEqual(row101["is_late"], 0)
        self.assertEqual(row101["days_until_payment"], 9)

        # Row 102: invoice Feb 1, due Feb 15, clear Mar 1 (28 days in Feb)
        # Paid late: delay = 14 days -> is_late = 1, days_until_payment = 28
        row102 = canonical[canonical["invoice_id"] == "102"].iloc[0]
        self.assertEqual(row102["delay_days"], 14)
        self.assertEqual(row102["is_late"], 1)
        self.assertEqual(row102["days_until_payment"], 28)

        # Exact on-time test: clear_date == due_date -> is_late == 0
        test_df = self.raw_sample.iloc[[0]].copy()
        test_df["clear_date"] = "1/15/2019 0:00"
        canonical_ontime, _ = construct_canonical_dataset(test_df)
        self.assertEqual(canonical_ontime.iloc[0]["delay_days"], 0)
        self.assertEqual(canonical_ontime.iloc[0]["is_late"], 0)

    def test_open_invoices_have_no_fabricated_targets(self):
        """Verify open records have null targets."""
        canonical, _ = construct_canonical_dataset(self.raw_sample)
        open_row = canonical[canonical["invoice_id"] == "103"].iloc[0]
        self.assertEqual(open_row["status"], "OPEN")
        self.assertTrue(np.isnan(open_row["is_late"]))
        self.assertTrue(np.isnan(open_row["delay_days"]))
        self.assertTrue(np.isnan(open_row["days_until_payment"]))

    def test_as_of_feature_engineering_and_leakage(self):
        """
        Critical test: Verify customer features strictly use past information.
        Sequence for CUST_A:
        - Invoice 101: Jan 1 (paid Jan 10)
        - Invoice 102: Feb 1 (paid Mar 1)
        - Invoice 103: Apr 1 (open)
        """
        canonical, _ = construct_canonical_dataset(self.raw_sample)
        features_df = compute_customer_as_of_features(canonical)

        # Invoice 101 (Jan 1): Zero prior invoices, zero prior payments
        f101 = features_df[features_df["invoice_id"] == "101"].iloc[0]
        self.assertEqual(f101["cust_prior_invoice_count"], 0)
        self.assertEqual(f101["cust_prior_payment_count"], 0)
        self.assertTrue(np.isnan(f101["cust_avg_delay"]))
        self.assertEqual(f101["is_new_customer"], 1)

        # Invoice 102 (Feb 1): Should see Invoice 101 (paid Jan 10), delay = -5
        f102 = features_df[features_df["invoice_id"] == "102"].iloc[0]
        self.assertEqual(f102["cust_prior_invoice_count"], 1)
        self.assertEqual(f102["cust_prior_payment_count"], 1)
        self.assertEqual(f102["cust_avg_delay"], -5.0)
        self.assertEqual(f102["cust_late_payment_rate"], 0.0)
        self.assertEqual(f102["is_new_customer"], 1)  # count < 3

        # Invoice 103 (Apr 1): Should see Invoice 101 (paid Jan 10) and Invoice 102 (paid Mar 1)
        # Prior delays: -5 and +14 -> avg = 4.5, late_rate = 0.5
        f103 = features_df[features_df["invoice_id"] == "103"].iloc[0]
        self.assertEqual(f103["cust_prior_invoice_count"], 2)
        self.assertEqual(f103["cust_prior_payment_count"], 2)
        self.assertAlmostEqual(f103["cust_avg_delay"], 4.5, places=4)
        self.assertAlmostEqual(f103["cust_late_payment_rate"], 0.5, places=4)
        self.assertEqual(f103["is_new_customer"], 1)  # count = 2 < 3

        # Leakage Assertion: Modifying Invoice 103 must NOT affect Invoice 101 or 102
        raw_modified = self.raw_sample.copy()
        raw_modified.loc[raw_modified["doc_id"] == 103, "clear_date"] = "12/31/2019 0:00"
        canonical_mod, _ = construct_canonical_dataset(raw_modified)
        features_mod = compute_customer_as_of_features(canonical_mod)

        f101_mod = features_mod[features_mod["invoice_id"] == "101"].iloc[0]
        f102_mod = features_mod[features_mod["invoice_id"] == "102"].iloc[0]
        self.assertEqual(f101["cust_prior_payment_count"], f101_mod["cust_prior_payment_count"])
        self.assertEqual(f102["cust_avg_delay"], f102_mod["cust_avg_delay"])

    def test_cold_start_and_train_only_imputation(self):
        """Verify cold start classification and train-only median imputation."""
        canonical, _ = construct_canonical_dataset(self.raw_sample)
        features = compute_customer_as_of_features(canonical)

        # CUST_B on row 104 has zero prior history -> is_new_customer = 1
        f104 = features[features["invoice_id"] == "104"].iloc[0]
        self.assertEqual(f104["is_new_customer"], 1)
        self.assertTrue(np.isnan(f104["cust_avg_delay"]))

        # Train imputer
        imputer = TrainingImputationTransformer()
        train_df = features[features["invoice_id"].isin(["101", "102"])]
        imputer.fit(train_df)

        imputed = imputer.transform(features)
        # After imputation, cust_avg_delay should no longer be NaN
        f104_imp = imputed[imputed["invoice_id"] == "104"].iloc[0]
        self.assertFalse(np.isnan(f104_imp["cust_avg_delay"]))
        # Imputed value matches train median
        self.assertEqual(f104_imp["cust_avg_delay"], imputer.fitted_values_["cust_avg_delay"])

    def test_temporal_split_monotonicity(self):
        """Verify strict chronological ordering between train, val, and test partitions."""
        dates = pd.date_range("2019-01-01", periods=100, freq="D")
        mock_df = pd.DataFrame(
            {
                "invoice_id": [str(i) for i in range(100)],
                "invoice_date": dates,
                "status": ["PAID"] * 90 + ["OPEN"] * 10,
            }
        )
        partitioned, info = partition_temporal_splits(mock_df, train_ratio=0.70, val_ratio=0.15)

        train_max = partitioned[partitioned["split"] == "train"]["invoice_date"].max()
        val_min = partitioned[partitioned["split"] == "validation"]["invoice_date"].min()
        val_max = partitioned[partitioned["split"] == "validation"]["invoice_date"].max()
        test_min = partitioned[partitioned["split"] == "test"]["invoice_date"].min()

        self.assertLess(train_max, val_min)
        self.assertLess(val_max, test_min)

    def test_prohibited_columns_rejection(self):
        """Verify assert_no_leakage fails if target/prohibited columns are passed as features."""
        # Safe feature list should pass
        assert_no_leakage(FEATURE_COLUMNS)

        # Passing clear_date should raise ValueError
        with self.assertRaises(ValueError):
            assert_no_leakage(["amount", "clear_date"])

        # Passing is_late should raise ValueError
        with self.assertRaises(ValueError):
            assert_no_leakage(["amount", "is_late"])

        # Passing isOpen should raise ValueError
        with self.assertRaises(ValueError):
            assert_no_leakage(["amount", "isOpen"])


if __name__ == "__main__":
    unittest.main()
