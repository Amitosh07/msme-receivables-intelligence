"""
train_all.py — V1 Model Training Orchestrator

MSME Receivables Intelligence Platform — Version 1

Orchestrates training of both the classifier and timing models,
saves combined model metadata, and generates the evaluation report.

Usage:
    python -m backend.ml.training.train_all \
        --data-dir data/processed \
        --model-dir backend/ml/models
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.ml.training.train_classifier import train_classifier
from backend.ml.training.train_timing import train_timing_model

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train V1 MSME payment prediction models")
    parser.add_argument(
        "--data-dir", type=str, default="data/processed",
        help="Path to processed Phase 1 data directory",
    )
    parser.add_argument(
        "--model-dir", type=str, default="backend/ml/models",
        help="Path to save model artifacts",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    model_dir = Path(args.model_dir)

    if not data_dir.exists():
        logger.error("Data directory not found: %s", data_dir)
        sys.exit(1)

    required_files = ["train.parquet", "validation.parquet", "test.parquet", "feature_metadata.json"]
    for f in required_files:
        if not (data_dir / f).exists():
            logger.error("Required file missing: %s", data_dir / f)
            sys.exit(1)

    logger.info("=" * 70)
    logger.info("MSME Receivables Intelligence — V1 Model Training")
    logger.info("=" * 70)

    # 1. Train classifier
    logger.info("")
    classifier_result = train_classifier(data_dir, model_dir)

    # 2. Train timing model
    logger.info("")
    timing_result = train_timing_model(data_dir, model_dir)

    # 3. Save combined model metadata
    combined_metadata = {
        "pipeline_name": "MSME Receivables Intelligence V1 — Payment Prediction Models",
        "pipeline_version": "1.0",
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "models": {
            "classifier": classifier_result["model_metadata"],
            "timing": timing_result["model_metadata"],
        },
    }

    meta_path = model_dir / "model_metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(combined_metadata, f, indent=2, default=str)
    logger.info("Combined model metadata saved to %s", meta_path)

    # 4. Save evaluation report
    eval_report = {
        "report_name": "V1 Model Evaluation Report",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "classifier": {
            "model_version": "payment_classifier_v1",
            "validation_metrics": classifier_result["validation_metrics"],
            "test_metrics": classifier_result["test_metrics"],
            "baseline_validation": classifier_result["baseline_validation"],
            "baseline_test": classifier_result["baseline_test"],
            "feature_importance_top10": classifier_result["feature_importance"][:10],
        },
        "timing_model": {
            "model_version": "payment_timing_v1",
            "validation_metrics": timing_result["validation_metrics"],
            "test_metrics": timing_result["test_metrics"],
            "baseline_validation": timing_result["baseline_validation"],
            "baseline_test": timing_result["baseline_test"],
            "feature_importance_top10": timing_result["feature_importance"][:10],
        },
    }

    eval_path = model_dir / "evaluation_report.json"
    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump(eval_report, f, indent=2, default=str)
    logger.info("Evaluation report saved to %s", eval_path)

    # 5. Print summary
    logger.info("")
    logger.info("=" * 70)
    logger.info("TRAINING SUMMARY")
    logger.info("=" * 70)

    cr = classifier_result
    tr = timing_result

    logger.info("")
    logger.info("--- Classifier (is_late) ---")
    logger.info("  Validation: Accuracy=%.4f F1=%.4f ROC-AUC=%.4f",
                cr["validation_metrics"]["accuracy"],
                cr["validation_metrics"]["f1"],
                cr["validation_metrics"]["roc_auc"])
    logger.info("  Test:       Accuracy=%.4f F1=%.4f ROC-AUC=%.4f",
                cr["test_metrics"]["accuracy"],
                cr["test_metrics"]["f1"],
                cr["test_metrics"]["roc_auc"])
    logger.info("  Baseline Val: Accuracy=%.4f F1=%.4f",
                cr["baseline_validation"]["accuracy"],
                cr["baseline_validation"]["f1"])
    logger.info("  Baseline Test: Accuracy=%.4f F1=%.4f",
                cr["baseline_test"]["accuracy"],
                cr["baseline_test"]["f1"])

    logger.info("")
    logger.info("--- Timing Model (days_until_payment) ---")
    logger.info("  Validation: MAE=%.4f RMSE=%.4f MedianAE=%.4f",
                tr["validation_metrics"]["mae"],
                tr["validation_metrics"]["rmse"],
                tr["validation_metrics"]["median_absolute_error"])
    logger.info("  Test:       MAE=%.4f RMSE=%.4f MedianAE=%.4f",
                tr["test_metrics"]["mae"],
                tr["test_metrics"]["rmse"],
                tr["test_metrics"]["median_absolute_error"])
    logger.info("  Baseline Val: MAE=%.4f MedianAE=%.4f",
                tr["baseline_validation"]["mae"],
                tr["baseline_validation"]["median_absolute_error"])
    logger.info("  Baseline Test: MAE=%.4f MedianAE=%.4f",
                tr["baseline_test"]["mae"],
                tr["baseline_test"]["median_absolute_error"])

    logger.info("")
    logger.info("Top-5 Classifier Feature Importances:")
    for i, fi in enumerate(cr["feature_importance"][:5], 1):
        logger.info("  %d. %s (%.4f)", i, fi["feature"], fi["importance"])

    logger.info("")
    logger.info("Top-5 Timing Feature Importances:")
    for i, fi in enumerate(tr["feature_importance"][:5], 1):
        logger.info("  %d. %s (%.4f)", i, fi["feature"], fi["importance"])

    logger.info("")
    logger.info("Model artifacts saved to: %s", model_dir.resolve())
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
