"""
build_features.py — End-to-End Feature Engineering & Temporal Split Orchestrator

MSME Receivables Intelligence Platform — Version 1

This module orchestrates the complete Phase 1 pipeline:
1. Cleans and normalizes raw dataset into canonical representation.
2. Computes leakage-safe as-of historical customer features.
3. Derives invoice-level features known at invoice posting time T.
4. Partitions closed invoices into chronological Train / Validation / Test sets (70/15/15).
5. Fits imputation statistics on the TRAINING partition only and applies them downstream.
6. Asserts zero target or prohibited column leakage into the feature matrix.
7. Persists ML-ready datasets (Parquet) and comprehensive metadata (JSON).

Usage:
    python -m backend.ml.features.build_features --input data/dataset.csv --output-dir data/processed
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from backend.ml.data.clean_data import construct_canonical_dataset, load_raw_dataset
from backend.ml.features.as_of_features import compute_customer_as_of_features
from backend.ml.features.transformations import (
    TrainingImputationTransformer,
    compute_invoice_features,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("build_features")

# Formal feature definitions for Version 1
NUMERIC_FEATURES: List[str] = [
    "amount",
    "log_amount",
    "term_duration_days",
    "days_until_due_at_posting",
    "posting_month",
    "posting_day_of_month",
    "posting_day_of_week",
    "posting_quarter",
    "is_weekend_posting",
    "due_month",
    "due_day_of_week",
    "is_weekend_due",
    "cust_prior_invoice_count",
    "cust_prior_payment_count",
    "cust_prior_late_count",
    "cust_late_payment_rate",
    "cust_avg_delay",
    "cust_median_delay",
    "cust_std_delay",
    "cust_max_delay",
    "cust_min_delay",
    "cust_recent_avg_delay_3",
    "cust_recent_late_rate_3",
    "days_since_last_payment",
    "days_since_prev_invoice",
    "is_new_customer",
]

CATEGORICAL_FEATURES: List[str] = [
    "business_code",
    "currency",
    "payment_terms",
]

FEATURE_COLUMNS: List[str] = NUMERIC_FEATURES + CATEGORICAL_FEATURES

TARGET_COLUMNS: List[str] = [
    "is_late",
    "delay_days",
    "days_until_payment",
]

METADATA_COLUMNS: List[str] = [
    "invoice_id",
    "business_code",
    "customer_id",
    "customer_name",
    "invoice_date",
    "due_date",
    "baseline_date",
    "clear_date",
    "status",
    "split",
]

PROHIBITED_COLUMNS: List[str] = [
    "clear_date",
    "isOpen",
    "delay_days",
    "is_late",
    "days_until_payment",
    "document type",
    "area_business",
    "posting_id",
    "doc_id",
    "cust_number",
    "name_customer",
]


def assert_no_leakage(feature_cols: List[str]) -> None:
    """
    Assert that no prohibited target or raw ERP column is present in FEATURE_COLUMNS.
    """
    prohibited_set = set(PROHIBITED_COLUMNS)
    feature_set = set(feature_cols)
    intersection = feature_set.intersection(prohibited_set)
    if intersection:
        raise ValueError(
            f"CRITICAL LEAKAGE DETECTED: Prohibited columns found in FEATURE_COLUMNS: {intersection}"
        )


def partition_temporal_splits(
    df: pd.DataFrame, train_ratio: float = 0.70, val_ratio: float = 0.15
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Partition closed records chronologically by invoice_date into 70% Train,
    15% Validation, and 15% Holdout Test. Open records are assigned to 'open_inference'.

    Derives exact date cutoff boundaries dynamically from the closed dataset.
    """
    out = df.copy()

    # Filter closed records sorted by invoice_date
    closed = out[out["status"] == "PAID"].sort_values("invoice_date").reset_index(drop=True)
    n_closed = len(closed)

    # Compute cutoffs by chronological index
    train_idx = int(n_closed * train_ratio)
    val_idx = int(n_closed * (train_ratio + val_ratio))

    val_cutoff_date = closed.iloc[train_idx]["invoice_date"]
    test_cutoff_date = closed.iloc[val_idx]["invoice_date"]

    # Assign split labels strictly based on date cutoffs
    # train: invoice_date < val_cutoff_date
    # val: val_cutoff_date <= invoice_date < test_cutoff_date
    # test: invoice_date >= test_cutoff_date
    is_paid = out["status"] == "PAID"
    is_open = out["status"] == "OPEN"

    train_mask = is_paid & (out["invoice_date"] < val_cutoff_date)
    val_mask = is_paid & (out["invoice_date"] >= val_cutoff_date) & (out["invoice_date"] < test_cutoff_date)
    test_mask = is_paid & (out["invoice_date"] >= test_cutoff_date)

    out["split"] = "unassigned"
    out.loc[train_mask, "split"] = "train"
    out.loc[val_mask, "split"] = "validation"
    out.loc[test_mask, "split"] = "test"
    out.loc[is_open, "split"] = "open_inference"

    split_info = {
        "closed_records_count": n_closed,
        "train_count": int(train_mask.sum()),
        "train_pct": round(float(train_mask.sum() / n_closed * 100), 2),
        "validation_count": int(val_mask.sum()),
        "validation_pct": round(float(val_mask.sum() / n_closed * 100), 2),
        "test_count": int(test_mask.sum()),
        "test_pct": round(float(test_mask.sum() / n_closed * 100), 2),
        "open_inference_count": int(is_open.sum()),
        "val_cutoff_date": str(val_cutoff_date),
        "test_cutoff_date": str(test_cutoff_date),
        "train_min_date": str(out.loc[train_mask, "invoice_date"].min()),
        "train_max_date": str(out.loc[train_mask, "invoice_date"].max()),
        "val_min_date": str(out.loc[val_mask, "invoice_date"].min()),
        "val_max_date": str(out.loc[val_mask, "invoice_date"].max()),
        "test_min_date": str(out.loc[test_mask, "invoice_date"].min()),
        "test_max_date": str(out.loc[test_mask, "invoice_date"].max()),
        "open_min_date": str(out.loc[is_open, "invoice_date"].min()),
        "open_max_date": str(out.loc[is_open, "invoice_date"].max()),
    }

    logger.info("Temporal split boundaries: Train max=%s, Val min=%s, Val max=%s, Test min=%s",
                split_info["train_max_date"], split_info["val_min_date"],
                split_info["val_max_date"], split_info["test_min_date"])

    return out, split_info


def generate_feature_metadata(imputer: TrainingImputationTransformer) -> Dict[str, Any]:
    """
    Generate structured metadata describing all features in the ML feature matrix.
    """
    metadata_list = []
    for col in FEATURE_COLUMNS:
        if col in CATEGORICAL_FEATURES:
            ftype = "categorical"
            desc = f"Categorical attribute: {col}"
            as_of_rule = "Available at invoice posting time T"
            impute_strategy = "Native categorical / string"
        elif col.startswith("cust_") or col.startswith("days_since_") or col == "is_new_customer":
            ftype = "numeric_float" if "rate" in col or "delay" in col else "numeric_int"
            desc = f"As-of customer historical metric: {col}"
            as_of_rule = "Derived strictly from payment/invoice events occurring before posting date T"
            if col in imputer.fitted_values_:
                impute_strategy = f"Train median ({imputer.fitted_values_[col]})"
            else:
                impute_strategy = "None (zero default)"
        else:
            ftype = "numeric_float" if "amount" in col else "numeric_int"
            desc = f"Invoice-intrinsic feature: {col}"
            as_of_rule = "Available at invoice posting time T"
            impute_strategy = "Directly observed (no nulls)"

        metadata_list.append(
            {
                "feature_name": col,
                "data_type": ftype,
                "description": desc,
                "as_of_rule": as_of_rule,
                "imputation_strategy": impute_strategy,
            }
        )

    return {
        "version": "1.0",
        "total_features": len(FEATURE_COLUMNS),
        "numeric_features_count": len(NUMERIC_FEATURES),
        "categorical_features_count": len(CATEGORICAL_FEATURES),
        "feature_list": FEATURE_COLUMNS,
        "target_list": TARGET_COLUMNS,
        "metadata_columns": METADATA_COLUMNS,
        "prohibited_columns": PROHIBITED_COLUMNS,
        "imputation_fitted_values": imputer.fitted_values_,
        "features": metadata_list,
    }


def run_pipeline(
    input_path: Path | str, output_dir: Path | str
) -> Dict[str, Any]:
    """
    Execute complete Phase 1 pipeline and persist artifacts.
    """
    in_path = Path(input_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=== Starting Phase 1 Data & Feature Pipeline ===")

    # 1. Load & clean raw dataset
    raw_df = load_raw_dataset(in_path)
    canonical_df, clean_stats = construct_canonical_dataset(raw_df)

    # 2. Compute as-of historical customer features
    canonical_as_of = compute_customer_as_of_features(canonical_df)

    # 3. Compute invoice-level features
    full_df = compute_invoice_features(canonical_as_of)

    # 4. Temporal partitioning (70% train, 15% val, 15% test; open -> open_inference)
    partitioned_df, split_info = partition_temporal_splits(full_df)

    # 5. Fit imputation transformer strictly on the TRAINING split
    train_mask = partitioned_df["split"] == "train"
    imputer = TrainingImputationTransformer()
    imputer.fit(partitioned_df[train_mask])

    # 6. Apply imputation transformer across all splits
    imputed_df = imputer.transform(partitioned_df)

    # 7. Leakage check on feature columns
    assert_no_leakage(FEATURE_COLUMNS)

    # Verify no unexpected NaNs exist in numeric feature columns
    for col in NUMERIC_FEATURES:
        nan_cnt = int(imputed_df[col].isnull().sum())
        if nan_cnt > 0:
            raise ValueError(f"Feature column '{col}' contains {nan_cnt} unhandled NaNs after imputation")

    # 8. Separate datasets for persistence
    train_df = imputed_df[imputed_df["split"] == "train"].copy().reset_index(drop=True)
    val_df = imputed_df[imputed_df["split"] == "validation"].copy().reset_index(drop=True)
    test_df = imputed_df[imputed_df["split"] == "test"].copy().reset_index(drop=True)
    open_df = imputed_df[imputed_df["split"] == "open_inference"].copy().reset_index(drop=True)

    # 9. Persist Parquet files
    canonical_path = out_dir / "canonical_dataset.parquet"
    train_path = out_dir / "train.parquet"
    val_path = out_dir / "validation.parquet"
    test_path = out_dir / "test.parquet"
    open_path = out_dir / "open_inference.parquet"

    logger.info("Writing Parquet files to %s ...", out_dir)
    imputed_df.to_parquet(canonical_path, index=False)
    train_df.to_parquet(train_path, index=False)
    val_df.to_parquet(val_path, index=False)
    test_df.to_parquet(test_path, index=False)
    open_df.to_parquet(open_path, index=False)

    # 10. Persist metadata & manifest
    feature_meta = generate_feature_metadata(imputer)
    meta_path = out_dir / "feature_metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(feature_meta, f, indent=2)

    manifest = {
        "pipeline_name": "Phase 1 Canonical Data Pipeline & As-Of Feature Engineering",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_dataset": str(in_path),
        "source_rows": clean_stats["raw_rows"],
        "cleaned_canonical_rows": clean_stats["canonical_rows"],
        "artifacts": {
            "canonical_dataset": str(canonical_path.name),
            "train_dataset": str(train_path.name),
            "validation_dataset": str(val_path.name),
            "test_dataset": str(test_path.name),
            "open_inference_dataset": str(open_path.name),
            "feature_metadata": str(meta_path.name),
        },
        "row_counts": {
            "canonical_total": len(imputed_df),
            "train": len(train_df),
            "validation": len(val_df),
            "test": len(test_df),
            "open_inference": len(open_df),
        },
        "temporal_splits": split_info,
        "features": {
            "total_count": len(FEATURE_COLUMNS),
            "numeric_count": len(NUMERIC_FEATURES),
            "categorical_count": len(CATEGORICAL_FEATURES),
            "list": FEATURE_COLUMNS,
        },
        "targets": {
            "classification_target": "is_late",
            "delay_target": "delay_days",
            "timing_regression_target": "days_until_payment",
        },
    }

    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    logger.info("=== Phase 1 Pipeline Completed Successfully ===")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Phase 1 Canonical Data Pipeline & As-Of Feature Engineering."
    )
    parser.add_argument(
        "--input",
        type=str,
        default="data/dataset.csv",
        help="Path to raw CSV dataset.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/processed",
        help="Directory to write processed parquet and metadata files.",
    )

    args = parser.parse_args()
    manifest = run_pipeline(args.input, args.output_dir)

    print("\n" + "=" * 60)
    print("PHASE 1 EXECUTION SUMMARY:")
    print(f"  Canonical Rows: {manifest['row_counts']['canonical_total']:,}")
    print(f"  Train:          {manifest['row_counts']['train']:,} ({manifest['temporal_splits']['train_pct']}%)")
    print(f"  Validation:     {manifest['row_counts']['validation']:,} ({manifest['temporal_splits']['validation_pct']}%)")
    print(f"  Test:           {manifest['row_counts']['test']:,} ({manifest['temporal_splits']['test_pct']}%)")
    print(f"  Open Inference: {manifest['row_counts']['open_inference']:,}")
    print(f"  Total Features: {manifest['features']['total_count']}")
    print(f"  Output Dir:     {args.output_dir}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
