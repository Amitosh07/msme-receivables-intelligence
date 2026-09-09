"""
clean_data.py — Phase 1 Data Cleaning & Canonical Normalization Pipeline

MSME Receivables Intelligence Platform — Version 1

This module transforms the raw ERP invoice dataset into a clean, normalized
canonical dataset adhering to docs/data_contract.md and docs/ml_contract.md.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Expected raw columns from Phase 0 audit
RAW_REQUIRED_COLUMNS = [
    "business_code",
    "cust_number",
    "name_customer",
    "clear_date",
    "doc_id",
    "posting_date",
    "due_in_date",
    "invoice_currency",
    "document type",
    "total_open_amount",
    "baseline_create_date",
    "cust_payment_terms",
    "isOpen",
]


def load_raw_dataset(input_path: Path | str) -> pd.DataFrame:
    """
    Load the raw CSV dataset safely without mutation.
    """
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"Raw dataset file does not exist: {path}")

    logger.info("Loading raw dataset from %s", path)
    df = pd.read_csv(path)
    return df


def validate_raw_schema(df: pd.DataFrame) -> None:
    """
    Ensure all required raw columns exist.
    """
    missing = [col for col in RAW_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Raw dataset missing required columns: {missing}")


def deduplicate_records(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Deterministically deduplicate exact rows.
    Returns (deduplicated_df, duplicate_count).
    """
    initial_count = len(df)
    # Stably drop exact duplicate rows
    deduped = df.drop_duplicates().copy()
    dup_count = initial_count - len(deduped)
    logger.info("Removed %d exact duplicate rows (%d -> %d)", dup_count, initial_count, len(deduped))
    return deduped, dup_count


def filter_non_trade_documents(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Exclude non-standard credit / adjustment records (document type == 'X2')
    as established in Phase 0 contract.
    Returns (filtered_df, excluded_count).
    """
    initial_count = len(df)
    filtered = df[df["document type"] != "X2"].copy()
    excluded_count = initial_count - len(filtered)
    logger.info("Filtered %d non-trade documents with type 'X2' (%d -> %d)", excluded_count, initial_count, len(filtered))
    return filtered, excluded_count


def normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Parse and validate date representations into standard datetime64[ns].
    Does NOT mutate input DataFrame.
    """
    df_out = df.copy()

    # invoice_date = posting_date (MM/DD/YYYY)
    df_out["invoice_date"] = pd.to_datetime(
        df_out["posting_date"], format="%m/%d/%Y", errors="coerce"
    )
    if df_out["invoice_date"].isnull().any():
        failed = int(df_out["invoice_date"].isnull().sum())
        raise ValueError(f"Failed to parse posting_date for {failed} records")

    # due_date = due_in_date (YYYYMMDD integer/float)
    df_out["due_date"] = pd.to_datetime(
        df_out["due_in_date"].astype(str).str.split(".").str[0],
        format="%Y%m%d",
        errors="coerce",
    )
    if df_out["due_date"].isnull().any():
        failed = int(df_out["due_date"].isnull().sum())
        raise ValueError(f"Failed to parse due_in_date for {failed} records")

    # baseline_date = baseline_create_date (YYYYMMDD integer/float)
    df_out["baseline_date"] = pd.to_datetime(
        df_out["baseline_create_date"].astype(str).str.split(".").str[0],
        format="%Y%m%d",
        errors="coerce",
    )

    # clear_date = clear_date (MM/DD/YYYY HH:MM)
    # May be null for open/unpaid invoices
    df_out["clear_date"] = pd.to_datetime(
        df_out["clear_date"], format="%m/%d/%Y %H:%M", errors="coerce"
    )

    return df_out


def construct_canonical_dataset(raw_df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    """
    End-to-end cleaning and canonical schema construction.
    Returns (canonical_df, cleaning_stats).
    """
    validate_raw_schema(raw_df)

    # 1. Deduplicate
    deduped_df, dup_count = deduplicate_records(raw_df)

    # 2. Filter document type X2
    filtered_df, x2_count = filter_non_trade_documents(deduped_df)

    # 3. Normalize dates
    normalized_df = normalize_dates(filtered_df)

    # 4. Canonical column construction
    canonical = pd.DataFrame()

    # Identifiers & Keys
    # Use doc_id as authoritative unique transaction identifier
    canonical["invoice_id"] = normalized_df["doc_id"].astype(str)
    canonical["business_code"] = normalized_df["business_code"].astype(str).str.strip()
    canonical["customer_id"] = normalized_df["cust_number"].astype(str).str.strip()
    canonical["customer_name"] = normalized_df["name_customer"].astype(str).str.strip()

    # Dates
    canonical["invoice_date"] = normalized_df["invoice_date"]
    canonical["due_date"] = normalized_df["due_date"]
    canonical["baseline_date"] = normalized_df["baseline_date"]
    canonical["clear_date"] = normalized_df["clear_date"]

    # Monetary & Commercial Terms
    canonical["amount"] = normalized_df["total_open_amount"].astype(float)
    canonical["currency"] = normalized_df["invoice_currency"].astype(str).str.strip()
    canonical["payment_terms"] = normalized_df["cust_payment_terms"].astype(str).str.strip()

    # Credit term durations
    canonical["term_duration_days"] = (
        canonical["due_date"] - canonical["baseline_date"]
    ).dt.days.fillna((canonical["due_date"] - canonical["invoice_date"]).dt.days).astype(int)

    canonical["days_until_due_at_posting"] = (
        canonical["due_date"] - canonical["invoice_date"]
    ).dt.days.astype(int)

    # Operational status
    is_open = normalized_df["clear_date"].isnull()
    canonical["status"] = np.where(is_open, "OPEN", "PAID")

    # Supervised Targets (strictly for paid records; NaN for open records)
    delay = (canonical["clear_date"] - canonical["due_date"]).dt.days
    timing = (canonical["clear_date"] - canonical["invoice_date"]).dt.days

    canonical["delay_days"] = np.where(is_open, np.nan, delay)
    canonical["is_late"] = np.where(is_open, np.nan, (delay > 0).astype(float))
    canonical["days_until_payment"] = np.where(is_open, np.nan, timing)

    # Deterministic chronological sort
    canonical = canonical.sort_values(
        by=["invoice_date", "invoice_id"], ascending=[True, True]
    ).reset_index(drop=True)

    cleaning_stats = {
        "raw_rows": len(raw_df),
        "deduplicated_rows_removed": dup_count,
        "x2_records_removed": x2_count,
        "canonical_rows": len(canonical),
        "paid_records": int((canonical["status"] == "PAID").sum()),
        "open_records": int((canonical["status"] == "OPEN").sum()),
        "unique_customers": int(canonical["customer_id"].nunique()),
        "min_invoice_date": str(canonical["invoice_date"].min()),
        "max_invoice_date": str(canonical["invoice_date"].max()),
    }

    logger.info("Canonical dataset successfully created: %d rows (%d paid, %d open)",
                len(canonical), cleaning_stats["paid_records"], cleaning_stats["open_records"])

    return canonical, cleaning_stats
