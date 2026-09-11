"""Read-only Phase F evaluation of the saved V1 artifacts on the temporal holdout."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
    roc_auc_score,
)

from backend.ml.inference.predict import V1Predictor


def evaluate_saved_models(
    data_dir: Path = Path("data/processed"),
    model_dir: Path = Path("backend/ml/models"),
) -> dict[str, Any]:
    """Evaluate unchanged artifacts against the existing chronological test split."""
    test = pd.read_parquet(data_dir / "test.parquet")
    predictor = V1Predictor(model_dir).load()
    scored = predictor.predict(test)
    y_late = test["is_late"].astype(int).to_numpy()
    y_timing = test["days_until_payment"].astype(float).to_numpy()
    probability = scored["risk_score"].astype(float).to_numpy()
    predicted_late = scored["is_late_predicted"].astype(int).to_numpy()
    predicted_timing = scored["predicted_days_until_payment"].astype(float).to_numpy()

    bucket_rows: list[dict[str, Any]] = []
    for lower in np.arange(0.0, 1.0, 0.1):
        upper = round(float(lower + 0.1), 1)
        mask = (probability >= lower) & (
            probability <= upper if upper == 1.0 else probability < upper
        )
        bucket_rows.append({
            "range": f"{lower:.1f}-{upper:.1f}",
            "count": int(mask.sum()),
            "mean_predicted_probability": (
                round(float(probability[mask].mean()), 4) if mask.any() else None
            ),
            "observed_late_rate": (
                round(float(y_late[mask].mean()), 4) if mask.any() else None
            ),
        })
    observed, predicted = calibration_curve(y_late, probability, n_bins=10, strategy="uniform")
    return {
        "test_rows": int(len(test)),
        "test_date_range": [str(test["invoice_date"].min().date()), str(test["invoice_date"].max().date())],
        "classification": {
            "precision": round(float(precision_score(y_late, predicted_late, zero_division=0)), 6),
            "recall": round(float(recall_score(y_late, predicted_late, zero_division=0)), 6),
            "f1": round(float(f1_score(y_late, predicted_late, zero_division=0)), 6),
            "roc_auc": round(float(roc_auc_score(y_late, probability)), 6),
            "pr_auc": round(float(average_precision_score(y_late, probability)), 6),
            "confusion_matrix": confusion_matrix(y_late, predicted_late).tolist(),
            "calibration_buckets": bucket_rows,
            "calibration_curve": [
                {"mean_predicted_probability": round(float(p), 4), "observed_late_rate": round(float(o), 4)}
                for p, o in zip(predicted, observed)
            ],
        },
        "timing": {
            "mae_days": round(float(mean_absolute_error(y_timing, predicted_timing)), 4),
            "rmse_days": round(float(np.sqrt(mean_squared_error(y_timing, predicted_timing))), 4),
            "target_summary_days": {
                "min": round(float(np.min(y_timing)), 2),
                "median": round(float(np.median(y_timing)), 2),
                "mean": round(float(np.mean(y_timing)), 2),
                "p95": round(float(np.quantile(y_timing, 0.95)), 2),
                "max": round(float(np.max(y_timing)), 2),
            },
        },
    }
