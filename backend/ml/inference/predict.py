"""
predict.py — V1 Inference Module

MSME Receivables Intelligence Platform — Version 1

Loads trained V1 models, validates input schema, and generates predictions.
Produces:
  - risk_score (probability of late payment)
  - is_late_predicted (binary classification)
  - predicted_days_until_payment (payment timing estimate)

Usage:
    python -m backend.ml.inference.predict \
        --data-dir data/processed \
        --model-dir backend/ml/models \
        --output-path data/processed/scored_open_invoices.parquet
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.preprocessing import OrdinalEncoder

logger = logging.getLogger(__name__)


class InferenceError(Exception):
    """Raised when inference fails due to schema or model issues."""
    pass


class V1Predictor:
    """
    Encapsulates V1 model loading and prediction logic.

    Loads classifier + timing model + encoder artifacts and provides
    a unified predict() method that returns all prediction columns.
    """

    RISK_TIERS = {
        "LOW": (0.0, 0.30),
        "MEDIUM": (0.30, 0.65),
        "HIGH": (0.65, 1.01),
    }

    def __init__(
        self,
        model_dir: Path,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.model_dir = Path(model_dir)
        self.metadata = metadata
        self.classifier: Optional[xgb.XGBClassifier] = None
        self.timing_model: Optional[xgb.XGBRegressor] = None
        self.classifier_encoder: Optional[OrdinalEncoder] = None
        self.timing_encoder: Optional[OrdinalEncoder] = None
        self.feature_columns: List[str] = []
        self.categorical_columns: List[str] = []
        self._loaded = False

    def load(self) -> "V1Predictor":
        """Load all model artifacts from disk."""
        logger.info("Loading V1 models from %s", self.model_dir)

        # Load metadata if not provided
        if self.metadata is None:
            meta_path = self.model_dir / "model_metadata.json"
            if not meta_path.exists():
                raise InferenceError(f"Model metadata not found: {meta_path}")
            with open(meta_path, "r", encoding="utf-8") as f:
                self.metadata = json.load(f)

        clf_meta = self.metadata["models"]["classifier"]
        timing_meta = self.metadata["models"]["timing"]

        self.feature_columns = clf_meta["feature_columns"]
        self.categorical_columns = clf_meta["categorical_columns"]

        # Load classifier
        clf_path = self.model_dir / clf_meta["artifact_path"]
        self.classifier = xgb.XGBClassifier()
        self.classifier.load_model(str(clf_path))
        logger.info("Loaded classifier: %s", clf_path.name)

        # Load timing model
        timing_path = self.model_dir / timing_meta["artifact_path"]
        self.timing_model = xgb.XGBRegressor()
        self.timing_model.load_model(str(timing_path))
        logger.info("Loaded timing model: %s", timing_path.name)

        # Load encoders
        clf_enc_path = self.model_dir / clf_meta["encoder_path"]
        self.classifier_encoder = joblib.load(str(clf_enc_path))

        timing_enc_path = self.model_dir / timing_meta["encoder_path"]
        self.timing_encoder = joblib.load(str(timing_enc_path))
        logger.info("Loaded categorical encoders")

        self._loaded = True
        return self

    def validate_schema(self, df: pd.DataFrame) -> None:
        """Validate that the input DataFrame has all required feature columns."""
        missing = set(self.feature_columns) - set(df.columns)
        if missing:
            raise InferenceError(
                f"Missing required feature columns: {sorted(missing)}"
            )

        # Check for leakage columns
        prohibited = {"clear_date", "isOpen", "delay_days", "is_late", "days_until_payment"}
        leaked = prohibited.intersection(set(self.feature_columns))
        if leaked:
            raise InferenceError(
                f"LEAKAGE: Prohibited columns in feature list: {sorted(leaked)}"
            )

    def _prepare_features(
        self, df: pd.DataFrame, encoder: OrdinalEncoder
    ) -> pd.DataFrame:
        """Prepare feature matrix for prediction."""
        X = df[self.feature_columns].copy()
        X[self.categorical_columns] = encoder.transform(X[self.categorical_columns])
        return X

    def _assign_risk_tier(self, risk_scores: np.ndarray) -> List[str]:
        """Map risk scores to tier labels: LOW, MEDIUM, HIGH."""
        tiers = []
        for score in risk_scores:
            if score < 0.30:
                tiers.append("LOW")
            elif score < 0.65:
                tiers.append("MEDIUM")
            else:
                tiers.append("HIGH")
        return tiers

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate all V1 predictions for a DataFrame.

        Returns a copy of the input DataFrame with additional columns:
          - risk_score: P(is_late=1 | X) in [0, 1]
          - is_late_predicted: binary 0/1
          - risk_tier: LOW / MEDIUM / HIGH
          - predicted_days_until_payment: estimated days from posting to payment
        """
        if not self._loaded:
            raise InferenceError("Models not loaded. Call .load() first.")

        self.validate_schema(df)

        # Classifier predictions
        X_clf = self._prepare_features(df, self.classifier_encoder)
        risk_scores = self.classifier.predict_proba(X_clf)[:, 1]
        is_late_pred = self.classifier.predict(X_clf)

        # Timing predictions (model outputs log1p-space)
        X_timing = self._prepare_features(df, self.timing_encoder)
        timing_pred_log = self.timing_model.predict(X_timing)
        timing_pred = np.expm1(timing_pred_log)
        timing_pred = np.maximum(timing_pred, 0.0)  # Clamp non-negative

        # Build output DataFrame
        result = df.copy()
        result["risk_score"] = np.round(risk_scores, 6)
        result["is_late_predicted"] = is_late_pred.astype(int)
        result["risk_tier"] = self._assign_risk_tier(risk_scores)
        result["predicted_days_until_payment"] = np.round(timing_pred, 2)

        return result


def score_open_invoices(
    data_dir: Path,
    model_dir: Path,
    output_path: Path,
) -> pd.DataFrame:
    """
    Score open (unlabeled) invoices and save results.
    """
    logger.info("=== Scoring Open Invoices ===")

    # Load open invoices
    open_path = data_dir / "open_inference.parquet"
    if not open_path.exists():
        raise FileNotFoundError(f"Open invoice dataset not found: {open_path}")

    df = pd.read_parquet(open_path)
    logger.info("Loaded %d open invoices", len(df))

    # Load and run predictor
    predictor = V1Predictor(model_dir)
    predictor.load()
    scored = predictor.predict(df)

    # Summary stats
    logger.info("Risk score distribution:")
    logger.info("  Mean:   %.4f", scored["risk_score"].mean())
    logger.info("  Median: %.4f", scored["risk_score"].median())
    logger.info("  Std:    %.4f", scored["risk_score"].std())

    tier_counts = scored["risk_tier"].value_counts()
    for tier in ["LOW", "MEDIUM", "HIGH"]:
        count = tier_counts.get(tier, 0)
        pct = 100.0 * count / len(scored)
        logger.info("  %s Risk: %d (%.1f%%)", tier, count, pct)

    logger.info("Predicted days_until_payment:")
    logger.info("  Mean:   %.2f", scored["predicted_days_until_payment"].mean())
    logger.info("  Median: %.2f", scored["predicted_days_until_payment"].median())
    logger.info("  P95:    %.2f", scored["predicted_days_until_payment"].quantile(0.95))

    # Save output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scored.to_parquet(str(output_path), index=False)
    logger.info("Scored invoices saved to %s (%d rows)", output_path, len(scored))

    return scored


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Score open invoices with V1 models")
    parser.add_argument(
        "--data-dir", type=str, default="data/processed",
        help="Path to processed data directory",
    )
    parser.add_argument(
        "--model-dir", type=str, default="backend/ml/models",
        help="Path to trained model artifacts",
    )
    parser.add_argument(
        "--output-path", type=str, default="data/processed/scored_open_invoices.parquet",
        help="Path to save scored open invoices",
    )
    args = parser.parse_args()

    score_open_invoices(
        data_dir=Path(args.data_dir),
        model_dir=Path(args.model_dir),
        output_path=Path(args.output_path),
    )


if __name__ == "__main__":
    main()
