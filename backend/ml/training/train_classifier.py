"""
train_classifier.py — V1 Payment Delay Classifier (XGBClassifier)

MSME Receivables Intelligence Platform — Version 1

Trains the binary classifier for ON_TIME (0) vs LATE (1) payment prediction.
The classifier's late-class probability serves as the application risk score.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.preprocessing import OrdinalEncoder

logger = logging.getLogger(__name__)

RANDOM_SEED = 42

# V1 XGBoost classifier configuration — sensible defaults, no large search
CLASSIFIER_PARAMS: Dict[str, Any] = {
    "n_estimators": 500,
    "max_depth": 6,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "gamma": 0.1,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "random_state": RANDOM_SEED,
    "n_jobs": -1,
    "enable_categorical": False,  # We use OrdinalEncoder for stable inference
    "early_stopping_rounds": 50,
}


def load_feature_metadata(meta_path: Path) -> Dict[str, Any]:
    """Load feature metadata from Phase 1 output."""
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_categorical_encoder(
    train_df: pd.DataFrame, cat_cols: List[str]
) -> OrdinalEncoder:
    """
    Fit an OrdinalEncoder on training data categorical columns.
    Unknown categories at inference time are mapped to -1.
    """
    encoder = OrdinalEncoder(
        handle_unknown="use_encoded_value",
        unknown_value=-1,
        dtype=np.float32,
    )
    encoder.fit(train_df[cat_cols])
    return encoder


def prepare_features(
    df: pd.DataFrame,
    feature_cols: List[str],
    cat_cols: List[str],
    encoder: OrdinalEncoder,
) -> pd.DataFrame:
    """
    Prepare feature matrix: encode categoricals, ensure correct column ordering.
    """
    X = df[feature_cols].copy()
    X[cat_cols] = encoder.transform(X[cat_cols])
    return X


def evaluate_classifier(
    y_true: np.ndarray, y_pred: np.ndarray, y_prob: np.ndarray, split_name: str
) -> Dict[str, Any]:
    """Compute standard classification metrics."""
    metrics = {
        "split": split_name,
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 6),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 6),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 6),
        "roc_auc": round(float(roc_auc_score(y_true, y_prob)), 6),
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),
        "support": int(len(y_true)),
    }

    logger.info(
        "[%s] Accuracy=%.4f Precision=%.4f Recall=%.4f F1=%.4f ROC-AUC=%.4f",
        split_name,
        metrics["accuracy"],
        metrics["precision"],
        metrics["recall"],
        metrics["f1"],
        metrics["roc_auc"],
    )
    return metrics


def compute_classifier_baseline(
    df: pd.DataFrame, y_true: np.ndarray, split_name: str
) -> Dict[str, Any]:
    """
    Historical customer late-rate baseline.
    Rule: predict LATE (1) if cust_late_payment_rate >= 0.50, else ON_TIME (0).
    Cold-start (is_new_customer == 1): predict majority class ON_TIME (0).
    """
    baseline_pred = np.zeros(len(df), dtype=int)
    # Use cust_late_payment_rate threshold
    late_rate = df["cust_late_payment_rate"].values
    is_new = df["is_new_customer"].values
    baseline_pred[(late_rate >= 0.50) & (is_new == 0)] = 1
    # Cold start customers -> predict 0 (ON_TIME)

    metrics = {
        "split": split_name,
        "accuracy": round(float(accuracy_score(y_true, baseline_pred)), 6),
        "precision": round(float(precision_score(y_true, baseline_pred, zero_division=0)), 6),
        "recall": round(float(recall_score(y_true, baseline_pred, zero_division=0)), 6),
        "f1": round(float(f1_score(y_true, baseline_pred, zero_division=0)), 6),
    }
    try:
        metrics["roc_auc"] = round(float(roc_auc_score(y_true, late_rate)), 6)
    except Exception:
        metrics["roc_auc"] = None
    metrics["confusion_matrix"] = confusion_matrix(y_true, baseline_pred).tolist()

    logger.info(
        "[Baseline %s] Accuracy=%.4f Precision=%.4f Recall=%.4f F1=%.4f",
        split_name,
        metrics["accuracy"],
        metrics["precision"],
        metrics["recall"],
        metrics["f1"],
    )
    return metrics


def get_feature_importance(model: xgb.XGBClassifier, feature_names: List[str]) -> List[Dict[str, Any]]:
    """Extract and rank global feature importances from the trained model."""
    importances = model.feature_importances_
    ranked = sorted(
        zip(feature_names, importances), key=lambda x: x[1], reverse=True
    )
    return [
        {"feature": name, "importance": round(float(imp), 6)} for name, imp in ranked
    ]


def train_classifier(
    data_dir: Path,
    model_dir: Path,
) -> Dict[str, Any]:
    """
    End-to-end V1 classifier training pipeline.
    """
    logger.info("=== Training V1 Payment Delay Classifier ===")

    # 1. Load Phase 1 artifacts
    meta = load_feature_metadata(data_dir / "feature_metadata.json")
    feature_cols = meta["feature_list"]
    cat_cols = [f["feature_name"] for f in meta["features"] if f["data_type"] == "categorical"]
    num_cols = [c for c in feature_cols if c not in cat_cols]

    train_df = pd.read_parquet(data_dir / "train.parquet")
    val_df = pd.read_parquet(data_dir / "validation.parquet")
    test_df = pd.read_parquet(data_dir / "test.parquet")

    logger.info("Loaded: Train=%d, Val=%d, Test=%d", len(train_df), len(val_df), len(test_df))

    # 2. Leakage check
    prohibited = {"clear_date", "isOpen", "delay_days", "is_late", "days_until_payment"}
    found = prohibited.intersection(set(feature_cols))
    if found:
        raise ValueError(f"LEAKAGE: Prohibited columns in features: {found}")

    # 3. Fit categorical encoder on training data
    encoder = build_categorical_encoder(train_df, cat_cols)

    # 4. Prepare feature matrices
    X_train = prepare_features(train_df, feature_cols, cat_cols, encoder)
    X_val = prepare_features(val_df, feature_cols, cat_cols, encoder)
    X_test = prepare_features(test_df, feature_cols, cat_cols, encoder)

    y_train = train_df["is_late"].astype(int).values
    y_val = val_df["is_late"].astype(int).values
    y_test = test_df["is_late"].astype(int).values

    # 5. Train XGBClassifier with early stopping on validation
    model = xgb.XGBClassifier(**CLASSIFIER_PARAMS)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )
    try:
        best_iteration = model.best_iteration
        logger.info("Best iteration (early stopping): %d", best_iteration)
    except AttributeError:
        best_iteration = CLASSIFIER_PARAMS.get("n_estimators")
        logger.info("Early stopping did not engage. Used all %d rounds.", best_iteration)

    # 6. Predict
    y_pred_val = model.predict(X_val)
    y_prob_val = model.predict_proba(X_val)[:, 1]
    y_pred_test = model.predict(X_test)
    y_prob_test = model.predict_proba(X_test)[:, 1]

    # 7. Evaluate
    val_metrics = evaluate_classifier(y_val, y_pred_val, y_prob_val, "validation")
    test_metrics = evaluate_classifier(y_test, y_pred_test, y_prob_test, "holdout_test")

    # 8. Baseline comparison
    baseline_val = compute_classifier_baseline(val_df, y_val, "validation")
    baseline_test = compute_classifier_baseline(test_df, y_test, "holdout_test")

    # 9. Feature importance
    importance = get_feature_importance(model, feature_cols)

    # 10. Risk score sanity check
    assert y_prob_val.min() >= 0.0 and y_prob_val.max() <= 1.0, "Risk scores out of [0,1]"
    assert y_prob_test.min() >= 0.0 and y_prob_test.max() <= 1.0, "Risk scores out of [0,1]"

    # 11. Save model artifact
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "payment_classifier_v1.json"
    model.save_model(str(model_path))
    logger.info("Classifier saved to %s", model_path)

    # 12. Save encoder
    import joblib
    encoder_path = model_dir / "classifier_encoder_v1.joblib"
    joblib.dump(encoder, str(encoder_path))
    logger.info("Categorical encoder saved to %s", encoder_path)

    # 13. Build metadata
    model_metadata = {
        "model_name": "payment_classifier",
        "model_version": "payment_classifier_v1",
        "model_type": "XGBClassifier",
        "target": "is_late",
        "target_classes": {"0": "ON_TIME", "1": "LATE"},
        "risk_score_source": "predict_proba[:, 1]",
        "feature_schema_version": meta.get("version", "1.0"),
        "feature_count": len(feature_cols),
        "feature_columns": feature_cols,
        "categorical_columns": cat_cols,
        "numeric_columns": num_cols,
        "training_period": "2018-12-30 to 2019-10-08",
        "validation_period": "2019-10-09 to 2019-12-09",
        "test_period": "2019-12-10 to 2020-02-27",
        "training_rows": len(train_df),
        "validation_rows": len(val_df),
        "test_rows": len(test_df),
        "model_config": {k: v for k, v in CLASSIFIER_PARAMS.items()},
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

    logger.info("=== Classifier Training Complete ===")
    return result
