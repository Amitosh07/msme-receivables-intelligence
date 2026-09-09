"""
test_models.py — Unit tests for V1 model training and artifacts

Tests model loading, output validity, inference schema, and leakage prevention.
"""

import json
import os
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


# Resolve project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "processed"
MODEL_DIR = PROJECT_ROOT / "backend" / "ml" / "models"


def _skip_if_no_data(path: Path) -> bool:
    """Check if a file exists for conditional test skipping."""
    return not path.exists()


class TestModelArtifacts(unittest.TestCase):
    """Tests that model artifacts exist and are well-formed."""

    def test_model_files_exist(self):
        """All required model artifacts should be present."""
        required = [
            "payment_classifier_v1.json",
            "payment_timing_v1.json",
            "model_metadata.json",
            "evaluation_report.json",
            "classifier_encoder_v1.joblib",
            "timing_encoder_v1.joblib",
        ]
        for filename in required:
            path = MODEL_DIR / filename
            self.assertTrue(
                path.exists(),
                f"Missing model artifact: {filename}",
            )

    def test_model_metadata_schema(self):
        """Model metadata should contain required keys."""
        meta_path = MODEL_DIR / "model_metadata.json"
        if _skip_if_no_data(meta_path):
            self.skipTest("model_metadata.json not found")

        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        self.assertIn("pipeline_name", meta)
        self.assertIn("pipeline_version", meta)
        self.assertIn("models", meta)
        self.assertIn("classifier", meta["models"])
        self.assertIn("timing", meta["models"])

        # Classifier metadata
        clf = meta["models"]["classifier"]
        self.assertEqual(clf["model_type"], "XGBClassifier")
        self.assertEqual(clf["target"], "is_late")
        self.assertEqual(clf["risk_score_source"], "predict_proba[:, 1]")
        self.assertIn("feature_columns", clf)
        self.assertEqual(len(clf["feature_columns"]), 29)

        # Timing metadata
        timing = meta["models"]["timing"]
        self.assertEqual(timing["model_type"], "XGBRegressor")
        self.assertEqual(timing["target"], "days_until_payment")
        self.assertIn("log1p", timing["target_transform"])

    def test_evaluation_report_schema(self):
        """Evaluation report should have metrics for both models and baselines."""
        report_path = MODEL_DIR / "evaluation_report.json"
        if _skip_if_no_data(report_path):
            self.skipTest("evaluation_report.json not found")

        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)

        self.assertIn("classifier", report)
        self.assertIn("timing_model", report)

        # Classifier metrics
        clf = report["classifier"]
        for split_key in ["validation_metrics", "test_metrics"]:
            metrics = clf[split_key]
            self.assertIn("accuracy", metrics)
            self.assertIn("precision", metrics)
            self.assertIn("recall", metrics)
            self.assertIn("f1", metrics)
            self.assertIn("roc_auc", metrics)
            self.assertIn("confusion_matrix", metrics)

        # Timing metrics
        timing = report["timing_model"]
        for split_key in ["validation_metrics", "test_metrics"]:
            metrics = timing[split_key]
            self.assertIn("mae", metrics)
            self.assertIn("rmse", metrics)
            self.assertIn("median_absolute_error", metrics)

        # Baselines present
        for key in ["baseline_validation", "baseline_test"]:
            self.assertIn(key, clf)
            self.assertIn(key, timing)


class TestLeakagePrevention(unittest.TestCase):
    """Verify that prohibited columns are not used as model features."""

    PROHIBITED = {
        "clear_date", "isOpen", "delay_days", "is_late",
        "days_until_payment", "document type", "area_business",
        "posting_id", "doc_id", "cust_number", "name_customer",
    }

    def test_no_prohibited_in_feature_columns(self):
        """Feature columns in model metadata must not contain prohibited targets/identifiers."""
        meta_path = MODEL_DIR / "model_metadata.json"
        if _skip_if_no_data(meta_path):
            self.skipTest("model_metadata.json not found")

        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        for model_name in ["classifier", "timing"]:
            features = set(meta["models"][model_name]["feature_columns"])
            leaked = self.PROHIBITED.intersection(features)
            self.assertEqual(
                len(leaked), 0,
                f"LEAKAGE in {model_name}: features contain prohibited columns: {leaked}",
            )

    def test_targets_not_in_features(self):
        """Classification/timing targets must not be in feature lists."""
        meta_path = MODEL_DIR / "model_metadata.json"
        if _skip_if_no_data(meta_path):
            self.skipTest("model_metadata.json not found")

        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        targets = {"is_late", "delay_days", "days_until_payment"}
        for model_name in ["classifier", "timing"]:
            features = set(meta["models"][model_name]["feature_columns"])
            leaked = targets.intersection(features)
            self.assertEqual(
                len(leaked), 0,
                f"TARGET LEAKAGE in {model_name}: {leaked}",
            )


class TestModelLoading(unittest.TestCase):
    """Tests that trained models can be loaded and produce predictions."""

    def setUp(self):
        """Load models once for all tests in this class."""
        if _skip_if_no_data(MODEL_DIR / "payment_classifier_v1.json"):
            self.skipTest("Model artifacts not found")

        import xgboost as xgb
        import joblib

        self.classifier = xgb.XGBClassifier()
        self.classifier.load_model(str(MODEL_DIR / "payment_classifier_v1.json"))

        self.timing_model = xgb.XGBRegressor()
        self.timing_model.load_model(str(MODEL_DIR / "payment_timing_v1.json"))

        self.clf_encoder = joblib.load(str(MODEL_DIR / "classifier_encoder_v1.joblib"))
        self.timing_encoder = joblib.load(str(MODEL_DIR / "timing_encoder_v1.joblib"))

    def test_classifier_loads(self):
        """XGBClassifier should load without errors."""
        self.assertIsNotNone(self.classifier)

    def test_timing_model_loads(self):
        """XGBRegressor should load without errors."""
        self.assertIsNotNone(self.timing_model)

    def test_classifier_has_feature_importances(self):
        """Classifier should have feature importance after training."""
        importances = self.classifier.feature_importances_
        self.assertEqual(len(importances), 29)
        self.assertTrue(np.all(importances >= 0))

    def test_timing_model_has_feature_importances(self):
        """Timing model should have feature importance after training."""
        importances = self.timing_model.feature_importances_
        self.assertEqual(len(importances), 29)
        self.assertTrue(np.all(importances >= 0))


class TestInferenceModule(unittest.TestCase):
    """Tests for the V1Predictor inference pipeline."""

    def setUp(self):
        """Set up predictor and sample data."""
        if _skip_if_no_data(MODEL_DIR / "model_metadata.json"):
            self.skipTest("Model artifacts not found")
        if _skip_if_no_data(DATA_DIR / "test.parquet"):
            self.skipTest("Test data not found")

        from backend.ml.inference.predict import V1Predictor
        self.predictor = V1Predictor(MODEL_DIR).load()
        self.test_df = pd.read_parquet(DATA_DIR / "test.parquet").head(50)

    def test_predict_returns_expected_columns(self):
        """Predictions should include risk_score, is_late_predicted, risk_tier, predicted_days."""
        result = self.predictor.predict(self.test_df)
        for col in ["risk_score", "is_late_predicted", "risk_tier", "predicted_days_until_payment"]:
            self.assertIn(col, result.columns, f"Missing prediction column: {col}")

    def test_risk_scores_bounded(self):
        """Risk scores should be in [0, 1]."""
        result = self.predictor.predict(self.test_df)
        scores = result["risk_score"].values
        self.assertTrue(np.all(scores >= 0.0), "Risk scores below 0")
        self.assertTrue(np.all(scores <= 1.0), "Risk scores above 1")

    def test_is_late_predicted_binary(self):
        """is_late_predicted should be 0 or 1."""
        result = self.predictor.predict(self.test_df)
        unique = set(result["is_late_predicted"].unique())
        self.assertTrue(unique.issubset({0, 1}), f"Non-binary predictions: {unique}")

    def test_risk_tier_valid(self):
        """Risk tiers should be LOW, MEDIUM, or HIGH."""
        result = self.predictor.predict(self.test_df)
        unique = set(result["risk_tier"].unique())
        self.assertTrue(
            unique.issubset({"LOW", "MEDIUM", "HIGH"}),
            f"Unexpected risk tiers: {unique}",
        )

    def test_timing_predictions_non_negative(self):
        """predicted_days_until_payment should be >= 0."""
        result = self.predictor.predict(self.test_df)
        self.assertTrue(
            np.all(result["predicted_days_until_payment"].values >= 0),
            "Negative timing predictions found",
        )

    def test_schema_validation_missing_column(self):
        """Predictor should raise InferenceError on missing feature columns."""
        from backend.ml.inference.predict import InferenceError
        bad_df = self.test_df.drop(columns=["amount"])
        with self.assertRaises(InferenceError):
            self.predictor.predict(bad_df)

    def test_output_row_count_preserved(self):
        """Output should have same number of rows as input."""
        result = self.predictor.predict(self.test_df)
        self.assertEqual(len(result), len(self.test_df))


class TestScoredOpenInvoices(unittest.TestCase):
    """Tests for the scored open invoices output."""

    def setUp(self):
        self.scored_path = DATA_DIR / "scored_open_invoices.parquet"
        if _skip_if_no_data(self.scored_path):
            self.skipTest("Scored open invoices not found")
        self.df = pd.read_parquet(self.scored_path)

    def test_row_count(self):
        """Should have 9,681 scored open invoices."""
        self.assertEqual(len(self.df), 9681)

    def test_prediction_columns_present(self):
        """Scored output should have all prediction columns."""
        for col in ["risk_score", "is_late_predicted", "risk_tier", "predicted_days_until_payment"]:
            self.assertIn(col, self.df.columns)

    def test_risk_scores_valid(self):
        """Risk scores should be valid probabilities."""
        self.assertTrue(self.df["risk_score"].between(0, 1).all())

    def test_risk_tier_distribution_reasonable(self):
        """All three risk tiers should have non-zero counts."""
        tiers = self.df["risk_tier"].value_counts()
        for tier in ["LOW", "MEDIUM", "HIGH"]:
            self.assertGreater(tiers.get(tier, 0), 0, f"Zero {tier} risk invoices")


class TestModelBeatBaseline(unittest.TestCase):
    """Tests that models outperform their baselines on the test set."""

    def setUp(self):
        report_path = MODEL_DIR / "evaluation_report.json"
        if _skip_if_no_data(report_path):
            self.skipTest("evaluation_report.json not found")
        with open(report_path, "r", encoding="utf-8") as f:
            self.report = json.load(f)

    def test_classifier_beats_baseline_f1(self):
        """Classifier F1 on test should beat baseline F1."""
        model_f1 = self.report["classifier"]["test_metrics"]["f1"]
        baseline_f1 = self.report["classifier"]["baseline_test"]["f1"]
        self.assertGreater(
            model_f1, baseline_f1,
            f"Classifier F1 ({model_f1:.4f}) does not beat baseline ({baseline_f1:.4f})",
        )

    def test_classifier_beats_baseline_accuracy(self):
        """Classifier accuracy on test should beat baseline accuracy."""
        model_acc = self.report["classifier"]["test_metrics"]["accuracy"]
        baseline_acc = self.report["classifier"]["baseline_test"]["accuracy"]
        self.assertGreater(
            model_acc, baseline_acc,
            f"Classifier Accuracy ({model_acc:.4f}) does not beat baseline ({baseline_acc:.4f})",
        )

    def test_timing_model_beats_baseline_mae(self):
        """Timing model MAE on test should be lower than baseline MAE."""
        model_mae = self.report["timing_model"]["test_metrics"]["mae"]
        baseline_mae = self.report["timing_model"]["baseline_test"]["mae"]
        self.assertLess(
            model_mae, baseline_mae,
            f"Timing MAE ({model_mae:.4f}) does not beat baseline ({baseline_mae:.4f})",
        )


if __name__ == "__main__":
    unittest.main()
