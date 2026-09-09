"""
train_timing.py — V1 Payment Timing Regressor (XGBRegressor)

MSME Receivables Intelligence Platform — Version 1

Trains the payment timing model that predicts days_until_payment.
Uses log1p target transformation: model predicts log1p(days_until_payment),
inference inverts with expm1.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    median_absolute_error,
)
from sklearn.preprocessing import OrdinalEncoder

logger = logging.getLogger(__name__)

RANDOM_SEED = 42

# V1 XGBoost regressor configuration — sensible defaults
REGRESSOR_PARAMS: Dict[str, Any] = {
    "n_estimators": 500,
    "max_depth": 6,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "gamma": 0.1,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "objective": "reg:absoluteerror",
    "eval_metric": "mae",
    "random_state": RANDOM_SEED,
    "n_jobs": -1,
    "enable_categorical": False,
    "early_stopping_rounds": 50,
}


def prepare_features(
    df: pd.DataFrame,
    feature_cols: List[str],
    cat_cols: List[str],
    encoder: OrdinalEncoder,
) -> pd.DataFrame:
    """Prepare feature matrix: encode categoricals."""
    X = df[feature_cols].copy()
    X[cat_cols] = encoder.transform(X[cat_cols])
    return X


def evaluate_timing_model(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    split_name: str,
) -> Dict[str, Any]:
    """Compute regression metrics for the timing model."""
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    medae = float(median_absolute_error(y_true, y_pred))

    metrics = {
        "split": split_name,
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "median_absolute_error": round(medae, 4),
    }

    logger.info(
        "[%s] MAE=%.4f RMSE=%.4f MedianAE=%.4f",
        split_name, mae, rmse, medae,
    )
    return metrics


def compute_timing_baseline(
    df: pd.DataFrame,
    y_true: np.ndarray,
    split_name: str,
) -> Dict[str, Any]:
    """
    Timing baseline per ml_contract:
      - If customer has >= 3 prior settled invoices: term_duration_days + cust_median_delay
      - Cold-start: term_duration_days + global training median delay (0.0)
    """
    is_new = df["is_new_customer"].values.astype(bool)
    term_days = df["term_duration_days"].values.astype(float)
    cust_median = df["cust_median_delay"].values.astype(float)

    baseline_pred = np.where(
        is_new,
        term_days + 0.0,   # Cold-start: global training median delay = 0
        term_days + cust_median,
    )
    # Clamp to non-negative
    baseline_pred = np.maximum(baseline_pred, 0.0)

    return evaluate_timing_model(y_true, baseline_pred, f"baseline_{split_name}")


def train_timing_model(
    data_dir: Path,
    model_dir: Path,
) -> Dict[str, Any]:
    """End-to-end V1 timing model training pipeline."""
    logger.info("=== Training V1 Payment Timing Regressor ===")

    # 1. Load metadata and data
    with open(data_dir / "feature_metadata.json", "r", encoding="utf-8") as f:
        meta = json.load(f)

    feature_cols = meta["feature_list"]
    cat_cols = [f["feature_name"] for f in meta["features"] if f["data_type"] == "categorical"]

    train_df = pd.read_parquet(data_dir / "train.parquet")
    val_df = pd.read_parquet(data_dir / "validation.parquet")
    test_df = pd.read_parquet(data_dir / "test.parquet")

    logger.info("Loaded: Train=%d, Val=%d, Test=%d", len(train_df), len(val_df), len(test_df))

    # 2. Fit encoder (reused from classifier or can be shared)
    from backend.ml.training.train_classifier import build_categorical_encoder
    encoder = build_categorical_encoder(train_df, cat_cols)

    # 3. Prepare features
    X_train = prepare_features(train_df, feature_cols, cat_cols, encoder)
    X_val = prepare_features(val_df, feature_cols, cat_cols, encoder)
    X_test = prepare_features(test_df, feature_cols, cat_cols, encoder)

    # 4. Target: log1p transform of days_until_payment
    y_train_raw = train_df["days_until_payment"].values.astype(float)
    y_val_raw = val_df["days_until_payment"].values.astype(float)
    y_test_raw = test_df["days_until_payment"].values.astype(float)

    y_train = np.log1p(y_train_raw)
    y_val = np.log1p(y_val_raw)

    # 5. Train XGBRegressor with early stopping
    model = xgb.XGBRegressor(**REGRESSOR_PARAMS)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )
    try:
        best_iteration = model.best_iteration
        logger.info("Best iteration (early stopping): %d", best_iteration)
    except AttributeError:
        best_iteration = REGRESSOR_PARAMS.get("n_estimators")
        logger.info("Early stopping did not engage. Used all %d rounds.", best_iteration)

    # 6. Predict (model outputs log1p-space; expm1 to original space)
    y_pred_val_log = model.predict(X_val)
    y_pred_val = np.expm1(y_pred_val_log)
    y_pred_val = np.maximum(y_pred_val, 0.0)  # Clamp non-negative

    y_pred_test_log = model.predict(X_test)
    y_pred_test = np.expm1(y_pred_test_log)
    y_pred_test = np.maximum(y_pred_test, 0.0)

    # 7. Evaluate
    val_metrics = evaluate_timing_model(y_val_raw, y_pred_val, "validation")
    test_metrics = evaluate_timing_model(y_test_raw, y_pred_test, "holdout_test")

    # 8. Baseline comparison
    baseline_val = compute_timing_baseline(val_df, y_val_raw, "validation")
    baseline_test = compute_timing_baseline(test_df, y_test_raw, "holdout_test")

    # 9. Feature importance
    importances = model.feature_importances_
    importance = [
        {"feature": name, "importance": round(float(imp), 6)}
        for name, imp in sorted(zip(feature_cols, importances), key=lambda x: x[1], reverse=True)
    ]

    # 10. Save model
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "payment_timing_v1.json"
    model.save_model(str(model_path))
    logger.info("Timing model saved to %s", model_path)

    # Save encoder
    import joblib
    encoder_path = model_dir / "timing_encoder_v1.joblib"
    joblib.dump(encoder, str(encoder_path))

    # 11. Model metadata
    model_metadata = {
        "model_name": "payment_timing",
        "model_version": "payment_timing_v1",
        "model_type": "XGBRegressor",
        "target": "days_until_payment",
        "target_transform": "log1p (model predicts in log1p-space; expm1 for inference)",
        "loss_function": "reg:absoluteerror",
        "feature_schema_version": meta.get("version", "1.0"),
        "feature_count": len(feature_cols),
        "feature_columns": feature_cols,
        "categorical_columns": cat_cols,
        "training_period": "2018-12-30 to 2019-10-08",
        "validation_period": "2019-10-09 to 2019-12-09",
        "test_period": "2019-12-10 to 2020-02-27",
        "training_rows": len(train_df),
        "validation_rows": len(val_df),
        "test_rows": len(test_df),
        "model_config": {k: v for k, v in REGRESSOR_PARAMS.items()},
        "best_iteration": best_iteration,
        "random_seed": RANDOM_SEED,
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_path": str(model_path.name),
        "encoder_path": str(encoder_path.name),
    }

    result = {
        "model_metadata": model_metadata,
        "validation_metrics": val_metrics,
        "test_metrics": test_metrics,
        "baseline_validation": baseline_val,
        "baseline_test": baseline_test,
        "feature_importance": importance,
    }

    logger.info("=== Timing Model Training Complete ===")
    return result
