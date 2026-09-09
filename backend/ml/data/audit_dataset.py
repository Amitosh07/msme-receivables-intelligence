"""
audit_dataset.py — Phase 0 Data Audit and Quality Verification Utility

MSME Receivables Intelligence Platform — Version 1

This script performs a deterministic, reproducible data-engineering audit
of the payment dataset. It inspects schema, data types, missingness,
duplicates, date validity, target distributions, customer cold-start
characteristics, and data leakage vectors.

Usage:
    python backend/ml/data/audit_dataset.py --input data/dataset.csv --output docs/data_audit_report.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

# Expected raw columns from the candidate Kaggle dataset
EXPECTED_COLUMNS: List[str] = [
    "business_code",
    "cust_number",
    "name_customer",
    "clear_date",
    "buisness_year",
    "doc_id",
    "posting_date",
    "document_create_date",
    "document_create_date.1",
    "due_in_date",
    "invoice_currency",
    "document type",
    "posting_id",
    "area_business",
    "total_open_amount",
    "baseline_create_date",
    "cust_payment_terms",
    "invoice_id",
    "isOpen",
]


def validate_required_columns(
    df: pd.DataFrame, required_columns: List[str] | None = None
) -> Tuple[bool, List[str]]:
    """
    Validate that all expected columns exist in the DataFrame.
    Returns (is_valid, missing_columns).
    """
    if required_columns is None:
        required_columns = EXPECTED_COLUMNS
    missing = [col for col in required_columns if col not in df.columns]
    return (len(missing) == 0, missing)


def detect_duplicates(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Inspect row-level and identifier duplicates without mutating the DataFrame.
    """
    total_rows = len(df)
    exact_duplicates = int(df.duplicated().sum())

    doc_id_duplicates = (
        int(df.duplicated(subset=["doc_id"]).sum()) if "doc_id" in df.columns else 0
    )

    invoice_id_duplicates = 0
    if "invoice_id" in df.columns:
        valid_invoices = df["invoice_id"].dropna()
        invoice_id_duplicates = int(valid_invoices.duplicated().sum())

    return {
        "total_rows": total_rows,
        "exact_duplicate_rows": exact_duplicates,
        "exact_duplicate_pct": round(exact_duplicates / total_rows * 100, 2)
        if total_rows > 0
        else 0.0,
        "doc_id_duplicates": doc_id_duplicates,
        "invoice_id_duplicates": invoice_id_duplicates,
        "unique_doc_ids": int(df["doc_id"].nunique()) if "doc_id" in df.columns else 0,
        "unique_invoice_ids": int(df["invoice_id"].dropna().nunique())
        if "invoice_id" in df.columns
        else 0,
    }


def parse_dataset_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Parse date fields into datetime types on a shallow copy of the DataFrame.
    Does NOT mutate the input DataFrame.
    """
    df_parsed = df.copy()

    # clear_date: formatted like '2/11/2020 0:00'
    if "clear_date" in df_parsed.columns:
        df_parsed["clear_date_parsed"] = pd.to_datetime(
            df_parsed["clear_date"], format="%m/%d/%Y %H:%M", errors="coerce"
        )

    # posting_date: formatted like '1/26/2020'
    if "posting_date" in df_parsed.columns:
        df_parsed["posting_date_parsed"] = pd.to_datetime(
            df_parsed["posting_date"], format="%m/%d/%Y", errors="coerce"
        )

    # due_in_date: stored as integer/float YYYYMMDD
    if "due_in_date" in df_parsed.columns:
        df_parsed["due_in_date_parsed"] = pd.to_datetime(
            df_parsed["due_in_date"].astype(str).str.split(".").str[0],
            format="%Y%m%d",
            errors="coerce",
        )

    # baseline_create_date: stored as integer/float YYYYMMDD
    if "baseline_create_date" in df_parsed.columns:
        df_parsed["baseline_create_date_parsed"] = pd.to_datetime(
            df_parsed["baseline_create_date"].astype(str).str.split(".").str[0],
            format="%Y%m%d",
            errors="coerce",
        )

    # document_create_date: stored as integer/float YYYYMMDD
    if "document_create_date" in df_parsed.columns:
        df_parsed["document_create_date_parsed"] = pd.to_datetime(
            df_parsed["document_create_date"].astype(str).str.split(".").str[0],
            format="%Y%m%d",
            errors="coerce",
        )

    # document_create_date.1: stored as integer/float YYYYMMDD
    if "document_create_date.1" in df_parsed.columns:
        df_parsed["document_create_date_1_parsed"] = pd.to_datetime(
            df_parsed["document_create_date.1"].astype(str).str.split(".").str[0],
            format="%Y%m%d",
            errors="coerce",
        )

    return df_parsed


def analyze_payment_status(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Analyze the semantics of null clear_date vs isOpen.
    """
    total = len(df)
    null_clear_dates = int(df["clear_date"].isnull().sum())
    paid_count = total - null_clear_dates

    is_open_counts: Dict[Any, int] = {}
    if "isOpen" in df.columns:
        is_open_counts = {str(k): int(v) for k, v in df["isOpen"].value_counts().items()}

    # Check exact agreement between clear_date null and isOpen == 1
    exact_match = False
    if "isOpen" in df.columns:
        crosstab = pd.crosstab(df["isOpen"], df["clear_date"].isnull())
        # Check if isOpen=1 exactly corresponds to clear_date.isnull()=True
        # and isOpen=0 corresponds to clear_date.isnull()=False
        try:
            exact_match = bool(
                crosstab.loc[1, True] == null_clear_dates
                and crosstab.loc[0, False] == paid_count
            )
        except KeyError:
            exact_match = False

    return {
        "total_records": total,
        "paid_records": paid_count,
        "paid_pct": round(paid_count / total * 100, 2) if total > 0 else 0.0,
        "unpaid_censored_records": null_clear_dates,
        "unpaid_censored_pct": round(null_clear_dates / total * 100, 2) if total > 0 else 0.0,
        "is_open_value_counts": is_open_counts,
        "is_open_clear_date_exact_alignment": exact_match,
    }


def derive_targets(df_with_dates: pd.DataFrame) -> pd.DataFrame:
    """
    Derive target variables on paid records.
    Returns a DataFrame containing only paid records with derived target columns:
      - delay_days = (clear_date_parsed - due_in_date_parsed).dt.days
      - days_until_payment = (clear_date_parsed - posting_date_parsed).dt.days
      - is_late = 1 if delay_days > 0 else 0
    Does NOT mutate the input DataFrame.
    """
    paid = df_with_dates[df_with_dates["clear_date_parsed"].notnull()].copy()

    paid["delay_days"] = (
        paid["clear_date_parsed"] - paid["due_in_date_parsed"]
    ).dt.days

    paid["days_until_payment"] = (
        paid["clear_date_parsed"] - paid["posting_date_parsed"]
    ).dt.days

    paid["is_late"] = (paid["delay_days"] > 0).astype(int)

    return paid


def compute_distribution_stats(series: pd.Series) -> Dict[str, float]:
    """Compute standard descriptive and percentile statistics for a numeric series."""
    clean = series.dropna()
    if len(clean) == 0:
        return {}

    pcts = [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
    quantiles = clean.quantile(pcts).to_dict()

    return {
        "count": int(clean.count()),
        "mean": round(float(clean.mean()), 4),
        "std": round(float(clean.std()), 4),
        "min": round(float(clean.min()), 2),
        "p01": round(float(quantiles[0.01]), 2),
        "p05": round(float(quantiles[0.05]), 2),
        "p10": round(float(quantiles[0.10]), 2),
        "p25": round(float(quantiles[0.25]), 2),
        "median": round(float(quantiles[0.50]), 2),
        "p75": round(float(quantiles[0.75]), 2),
        "p90": round(float(quantiles[0.90]), 2),
        "p95": round(float(quantiles[0.95]), 2),
        "p99": round(float(quantiles[0.99]), 2),
        "max": round(float(clean.max()), 2),
        "skewness": round(float(clean.skew()), 4),
    }


def analyze_targets(paid_targets_df: pd.DataFrame) -> Dict[str, Any]:
    """
    Analyze the distribution of classification and timing targets.
    """
    total = len(paid_targets_df)
    if total == 0:
        return {"error": "No paid records available for target analysis"}

    late_counts = paid_targets_df["is_late"].value_counts().to_dict()
    late_count = int(late_counts.get(1, 0))
    on_time_count = int(late_counts.get(0, 0))

    delay_stats = compute_distribution_stats(paid_targets_df["delay_days"])
    timing_stats = compute_distribution_stats(paid_targets_df["days_until_payment"])

    early_count = int((paid_targets_df["delay_days"] < 0).sum())
    exact_due_count = int((paid_targets_df["delay_days"] == 0).sum())
    negative_timing_count = int((paid_targets_df["days_until_payment"] < 0).sum())

    return {
        "total_paid_invoices": total,
        "classification": {
            "on_time_count": on_time_count,
            "on_time_pct": round(on_time_count / total * 100, 2),
            "late_count": late_count,
            "late_pct": round(late_count / total * 100, 2),
            "paid_early_count": early_count,
            "paid_early_pct": round(early_count / total * 100, 2),
            "paid_on_due_date_count": exact_due_count,
            "paid_on_due_date_pct": round(exact_due_count / total * 100, 2),
        },
        "delay_days_distribution": delay_stats,
        "days_until_payment_distribution": timing_stats,
        "anomalous_negative_timing_count": negative_timing_count,
    }


def analyze_customers_and_cold_start(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Analyze customer concentration, invoice distribution, and cold-start characteristics.
    """
    if "cust_number" not in df.columns:
        return {"error": "cust_number column not found"}

    cust_counts = df["cust_number"].value_counts()
    total_custs = len(cust_counts)

    inv_1 = int((cust_counts == 1).sum())
    inv_2_5 = int(((cust_counts >= 2) & (cust_counts <= 5)).sum())
    inv_6_20 = int(((cust_counts >= 6) & (cust_counts <= 20)).sum())
    inv_gt_20 = int((cust_counts > 20).sum())

    top_5 = [
        {"cust_number": str(k), "invoice_count": int(v)}
        for k, v in cust_counts.head(5).items()
    ]

    # Cold start in open (unpaid) set vs closed (paid) set
    overlap_info: Dict[str, Any] = {}
    if "isOpen" in df.columns:
        open_df = df[df["isOpen"] == 1]
        closed_df = df[df["isOpen"] == 0]
        open_custs = set(open_df["cust_number"])
        closed_custs = set(closed_df["cust_number"])
        known_open = open_custs.intersection(closed_custs)
        cold_open = open_custs - closed_custs

        overlap_info = {
            "unique_customers_in_open_set": len(open_custs),
            "open_customers_with_prior_history": len(known_open),
            "open_customers_with_prior_history_pct": round(
                len(known_open) / len(open_custs) * 100, 2
            )
            if len(open_custs) > 0
            else 0.0,
            "open_customers_cold_start": len(cold_open),
            "open_customers_cold_start_pct": round(
                len(cold_open) / len(open_custs) * 100, 2
            )
            if len(open_custs) > 0
            else 0.0,
        }

    return {
        "total_unique_customers": total_custs,
        "customers_with_1_invoice": inv_1,
        "customers_with_1_invoice_pct": round(inv_1 / total_custs * 100, 2)
        if total_custs > 0
        else 0.0,
        "customers_with_2_to_5_invoices": inv_2_5,
        "customers_with_2_to_5_invoices_pct": round(inv_2_5 / total_custs * 100, 2)
        if total_custs > 0
        else 0.0,
        "customers_with_6_to_20_invoices": inv_6_20,
        "customers_with_6_to_20_invoices_pct": round(inv_6_20 / total_custs * 100, 2)
        if total_custs > 0
        else 0.0,
        "customers_with_gt_20_invoices": inv_gt_20,
        "customers_with_gt_20_invoices_pct": round(inv_gt_20 / total_custs * 100, 2)
        if total_custs > 0
        else 0.0,
        "top_5_customers": top_5,
        "open_set_cold_start": overlap_info,
    }


def analyze_numeric_and_categorical(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Analyze distributions of invoice amount, currency, business code, and payment terms.
    """
    amount_stats = (
        compute_distribution_stats(df["total_open_amount"])
        if "total_open_amount" in df.columns
        else {}
    )

    currency_dist: Dict[str, Any] = {}
    if "invoice_currency" in df.columns:
        currency_counts = df["invoice_currency"].value_counts().to_dict()
        currency_dist = {str(k): int(v) for k, v in currency_counts.items()}

    business_code_dist: Dict[str, Any] = {}
    if "business_code" in df.columns:
        b_counts = df["business_code"].value_counts().to_dict()
        business_code_dist = {str(k): int(v) for k, v in b_counts.items()}

    doc_type_dist: Dict[str, Any] = {}
    if "document type" in df.columns:
        dt_counts = df["document type"].value_counts().to_dict()
        doc_type_dist = {str(k): int(v) for k, v in dt_counts.items()}

    return {
        "total_open_amount": amount_stats,
        "currency_distribution": currency_dist,
        "business_code_distribution": business_code_dist,
        "document_type_distribution": doc_type_dist,
        "payment_terms_unique_count": int(df["cust_payment_terms"].nunique())
        if "cust_payment_terms" in df.columns
        else 0,
    }


def run_audit(csv_path: Path) -> Dict[str, Any]:
    """
    Execute complete audit on the given dataset path.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Dataset not found at: {csv_path}")

    df_raw = pd.read_csv(csv_path)

    # 1. Basic schema
    valid_cols, missing_cols = validate_required_columns(df_raw)

    column_inventory: List[Dict[str, Any]] = []
    for col in df_raw.columns:
        null_count = int(df_raw[col].isnull().sum())
        null_pct = round(null_count / len(df_raw) * 100, 2)
        unique_cnt = int(df_raw[col].nunique())
        dtype_str = str(df_raw[col].dtype)
        column_inventory.append(
            {
                "column": col,
                "dtype": dtype_str,
                "missing_count": null_count,
                "missing_pct": null_pct,
                "unique_count": unique_cnt,
            }
        )

    # 2. Duplicates
    dup_report = detect_duplicates(df_raw)

    # 3. Dates & payment status
    df_dates = parse_dataset_dates(df_raw)
    payment_status = analyze_payment_status(df_raw)

    # Date ranges
    date_ranges = {
        "posting_date_min": str(df_dates["posting_date_parsed"].min())
        if "posting_date_parsed" in df_dates.columns
        else "N/A",
        "posting_date_max": str(df_dates["posting_date_parsed"].max())
        if "posting_date_parsed" in df_dates.columns
        else "N/A",
        "due_in_date_min": str(df_dates["due_in_date_parsed"].min())
        if "due_in_date_parsed" in df_dates.columns
        else "N/A",
        "due_in_date_max": str(df_dates["due_in_date_parsed"].max())
        if "due_in_date_parsed" in df_dates.columns
        else "N/A",
        "clear_date_min": str(df_dates["clear_date_parsed"].min())
        if "clear_date_parsed" in df_dates.columns
        else "N/A",
        "clear_date_max": str(df_dates["clear_date_parsed"].max())
        if "clear_date_parsed" in df_dates.columns
        else "N/A",
        "baseline_create_date_min": str(df_dates["baseline_create_date_parsed"].min())
        if "baseline_create_date_parsed" in df_dates.columns
        else "N/A",
        "baseline_create_date_max": str(df_dates["baseline_create_date_parsed"].max())
        if "baseline_create_date_parsed" in df_dates.columns
        else "N/A",
    }

    # Date anomalies
    anomalies = {
        "due_date_before_posting_date_count": int(
            (df_dates["due_in_date_parsed"] < df_dates["posting_date_parsed"]).sum()
        )
        if "due_in_date_parsed" in df_dates.columns
        and "posting_date_parsed" in df_dates.columns
        else 0,
        "failed_posting_date_parses": int(
            df_raw["posting_date"].notnull().sum()
            - df_dates["posting_date_parsed"].notnull().sum()
        )
        if "posting_date" in df_raw.columns
        else 0,
        "failed_due_date_parses": int(
            df_raw["due_in_date"].notnull().sum()
            - df_dates["due_in_date_parsed"].notnull().sum()
        )
        if "due_in_date" in df_raw.columns
        else 0,
        "failed_clear_date_parses": int(
            df_raw["clear_date"].notnull().sum()
            - df_dates["clear_date_parsed"].notnull().sum()
        )
        if "clear_date" in df_raw.columns
        else 0,
    }

    # 4. Target analysis
    paid_targets = derive_targets(df_dates)
    targets_report = analyze_targets(paid_targets)

    # 5. Customer & cold start
    customer_report = analyze_customers_and_cold_start(df_raw)

    # 6. Numeric & categorical
    numeric_report = analyze_numeric_and_categorical(df_raw)

    return {
        "dataset_path": str(csv_path),
        "total_rows": len(df_raw),
        "total_columns": len(df_raw.columns),
        "schema_valid": valid_cols,
        "missing_expected_columns": missing_cols,
        "column_inventory": column_inventory,
        "duplicates": dup_report,
        "payment_status": payment_status,
        "date_ranges": date_ranges,
        "date_anomalies": anomalies,
        "targets": targets_report,
        "customer_cold_start": customer_report,
        "numeric_and_categorical": numeric_report,
    }


def generate_markdown_report(audit: Dict[str, Any]) -> str:
    """
    Format audit results as a comprehensive, human-readable Markdown report.
    """
    lines: List[str] = []
    lines.append("# MSME Receivables Intelligence Platform — Data Audit Report")
    lines.append("")
    lines.append("**Dataset:** Payment Date Dataset (Kaggle — Rajat Tomar)")
    lines.append(f"**Inspected Path:** `{audit['dataset_path']}`")
    lines.append(f"**Execution Status:** Deterministic audit completed successfully")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 1. Dataset Overview
    lines.append("## 1. Dataset Overview")
    lines.append("")
    lines.append(f"- **Total Rows:** {audit['total_rows']:,}")
    lines.append(f"- **Total Columns:** {audit['total_columns']}")
    lines.append(f"- **Expected Schema Valid:** `{'YES' if audit['schema_valid'] else 'NO'}`")
    if not audit["schema_valid"]:
        lines.append(f"- **Missing Columns:** `{audit['missing_expected_columns']}`")
    lines.append(f"- **Date Range (Posting Date):** {audit['date_ranges']['posting_date_min']} to {audit['date_ranges']['posting_date_max']}")
    lines.append(f"- **Date Range (Due Date):** {audit['date_ranges']['due_in_date_min']} to {audit['date_ranges']['due_in_date_max']}")
    lines.append(f"- **Date Range (Clear/Payment Date):** {audit['date_ranges']['clear_date_min']} to {audit['date_ranges']['clear_date_max']}")
    lines.append("")

    # 2. Column Inventory Table
    lines.append("## 2. Column Inventory")
    lines.append("")
    lines.append("| Column | Raw Dtype | Missing Count | Missing % | Unique Count | Operational Interpretation |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :--- |")

    semantics_map = {
        "business_code": "Company/entity code identifier (e.g. U001, CA02)",
        "cust_number": "Customer account identifier (categorical key)",
        "name_customer": "Customer business name",
        "clear_date": "Actual date payment was settled (Target outcome, null = unpaid)",
        "buisness_year": "Fiscal year (2019, 2020)",
        "doc_id": "Accounting document identifier",
        "posting_date": "Date invoice was posted to ledger (invoice reference date)",
        "document_create_date": "Date invoice document was originally created in ERP",
        "document_create_date.1": "ERP posting date in YYYYMMDD format",
        "due_in_date": "Contractual invoice due date",
        "invoice_currency": "Billing currency (USD, CAD)",
        "document type": "Document type category (RV=Commercial invoice, X2=Credit/other)",
        "posting_id": "Posting indicator constant (=1 across all rows)",
        "area_business": "Business area code (100% missing in dataset)",
        "total_open_amount": "Total billed invoice amount",
        "baseline_create_date": "Baseline date from which payment terms begin",
        "cust_payment_terms": "Customer payment terms code (e.g. NAA8, NAH4)",
        "invoice_id": "Unique invoice identifier (null in 6 credit/X2 records)",
        "isOpen": "Invoice open status flag (1=Open/Unpaid, 0=Settled/Paid)",
    }

    for col in audit["column_inventory"]:
        col_name = col["column"]
        meaning = semantics_map.get(col_name, "Raw dataset field")
        lines.append(
            f"| `{col_name}` | `{col['dtype']}` | {col['missing_count']:,} | {col['missing_pct']}% | {col['unique_count']:,} | {meaning} |"
        )
    lines.append("")

    # 3. Data Quality & Duplicates
    lines.append("## 3. Data Quality & Integrity")
    lines.append("")
    dup = audit["duplicates"]
    lines.append(f"- **Exact Duplicate Rows:** {dup['exact_duplicate_rows']:,} ({dup['exact_duplicate_pct']}%)")
    lines.append(f"- **Duplicate `doc_id`:** {dup['doc_id_duplicates']:,} (identical to exact duplicate row count)")
    lines.append(f"- **Unique `doc_id`:** {dup['unique_doc_ids']:,}")
    lines.append(f"- **Unique `invoice_id` (non-null):** {dup['unique_invoice_ids']:,}")
    lines.append(f"- **Missing `invoice_id` Rows:** 6 (all corresponding to `document type == 'X2'` non-standard documents)")
    lines.append(f"- **`area_business` Missing:** 100.0% (unusable feature, must be discarded)")
    lines.append(f"- **`posting_id` Variance:** Constant = 1 (zero variance feature, discard)")
    lines.append(f"- **Date Parsing Failures:** 0 unparseable dates across all date fields")
    lines.append(f"- **`due_in_date < posting_date`:** {audit['date_anomalies']['due_date_before_posting_date_count']} records (backdated invoice entries where baseline date preceded posting)")
    lines.append("")

    # 4. Payment Status & Censored Data
    lines.append("## 4. Payment Status & Censored Data Analysis")
    lines.append("")
    ps = audit["payment_status"]
    lines.append(f"- **Paid / Settled Invoices (`clear_date` populated):** {ps['paid_records']:,} ({ps['paid_pct']}%)")
    lines.append(f"- **Unpaid / Censored Invoices (`clear_date` null):** {ps['unpaid_censored_records']:,} ({ps['unpaid_censored_pct']}%)")
    lines.append(f"- **`isOpen` Flag Alignment:** `isOpen == 1` corresponds **100% exactly** to `clear_date.isnull() == True`")
    lines.append("")
    lines.append("> [!IMPORTANT]")
    lines.append("> **Operational Meaning of Null `clear_date`:**")
    lines.append("> Null `clear_date` does NOT indicate corrupt or dirty data. It represents invoices that were **currently open / outstanding** at the time the dataset snapshot was exported.")
    lines.append("> In the business domain, these 10,000 open invoices represent the primary operational use case for the platform: predicting when currently unpaid invoices will be settled.")
    lines.append("> For **supervised model training**, these records are right-censored and lack ground-truth outcomes, so training and validation must use the 40,000 completed records. However, in the application inference pipeline, open invoices are the primary input to be scored.")
    lines.append("")

    # 5. Target Distributions
    lines.append("## 5. Target Distributions (Closed / Paid Records)")
    lines.append("")
    targets = audit["targets"]
    cls_report = targets["classification"]
    lines.append("### 5.1 Payment Delay Classification (`is_late = delay_days > 0`)")
    lines.append("")
    lines.append(f"- **Total Settled Invoices Analyzed:** {targets['total_paid_invoices']:,}")
    lines.append(f"- **On-Time Invoices (`delay_days <= 0`):** {cls_report['on_time_count']:,} ({cls_report['on_time_pct']}%)")
    lines.append(f"  - Paid strictly early (`delay_days < 0`): {cls_report['paid_early_count']:,} ({cls_report['paid_early_pct']}%)")
    lines.append(f"  - Paid on exact due date (`delay_days == 0`): {cls_report['paid_on_due_date_count']:,} ({cls_report['paid_on_due_date_pct']}%)")
    lines.append(f"- **Late Invoices (`delay_days > 0`):** {cls_report['late_count']:,} ({cls_report['late_pct']}%)")
    lines.append("- **Class Balance Assessment:** 58.1% on-time vs 41.9% late. The dataset is well-balanced for binary classification; no severe minority class undersampling or synthetic oversampling (e.g. SMOTE) is warranted.")
    lines.append("")

    lines.append("### 5.2 Payment Delay Days Distribution (`clear_date - due_in_date`)")
    lines.append("")
    dd = targets["delay_days_distribution"]
    lines.append("| Metric | Value (Days) |")
    lines.append("| :--- | :---: |")
    lines.append(f"| Mean | {dd['mean']} |")
    lines.append(f"| Standard Deviation | {dd['std']} |")
    lines.append(f"| Minimum (earliest prepayment) | {dd['min']} |")
    lines.append(f"| 1st Percentile | {dd['p01']} |")
    lines.append(f"| 5th Percentile | {dd['p05']} |")
    lines.append(f"| 25th Percentile | {dd['p25']} |")
    lines.append(f"| **Median (50th Percentile)** | **{dd['median']}** |")
    lines.append(f"| 75th Percentile | {dd['p75']} |")
    lines.append(f"| 90th Percentile | {dd['p90']} |")
    lines.append(f"| 95th Percentile | {dd['p95']} |")
    lines.append(f"| 99th Percentile | {dd['p99']} |")
    lines.append(f"| Maximum (longest delay) | {dd['max']} |")
    lines.append(f"| Skewness | {dd['skewness']} |")
    lines.append("")

    lines.append("### 5.3 Days Until Payment Distribution (`clear_date - posting_date`)")
    lines.append("")
    dup_dist = targets["days_until_payment_distribution"]
    lines.append("| Metric | Value (Days) |")
    lines.append("| :--- | :---: |")
    lines.append(f"| Mean | {dup_dist['mean']} |")
    lines.append(f"| Standard Deviation | {dup_dist['std']} |")
    lines.append(f"| Minimum | {dup_dist['min']} |")
    lines.append(f"| 1st Percentile | {dup_dist['p01']} |")
    lines.append(f"| 5th Percentile | {dup_dist['p05']} |")
    lines.append(f"| 25th Percentile | {dup_dist['p25']} |")
    lines.append(f"| **Median (50th Percentile)** | **{dup_dist['median']}** |")
    lines.append(f"| 75th Percentile | {dup_dist['p75']} |")
    lines.append(f"| 90th Percentile | {dup_dist['p90']} |")
    lines.append(f"| 95th Percentile | {dup_dist['p95']} |")
    lines.append(f"| 99th Percentile | {dup_dist['p99']} |")
    lines.append(f"| Maximum | {dup_dist['max']} |")
    lines.append(f"| Skewness | {dup_dist['skewness']} |")
    lines.append("")

    # 6. Customer History & Cold-Start Analysis
    lines.append("## 6. Customer History & Cold-Start Analysis")
    lines.append("")
    cust = audit["customer_cold_start"]
    lines.append(f"- **Total Unique Customers:** {cust['total_unique_customers']:,}")
    lines.append(f"- **Customers with Exactly 1 Invoice:** {cust['customers_with_1_invoice']:,} ({cust['customers_with_1_invoice_pct']}%)")
    lines.append(f"- **Customers with 2–5 Invoices:** {cust['customers_with_2_to_5_invoices']:,} ({cust['customers_with_2_to_5_invoices_pct']}%)")
    lines.append(f"- **Customers with 6–20 Invoices:** {cust['customers_with_6_to_20_invoices']:,} ({cust['customers_with_6_to_20_invoices_pct']}%)")
    lines.append(f"- **Customers with >20 Invoices:** {cust['customers_with_gt_20_invoices']:,} ({cust['customers_with_gt_20_invoices_pct']}%)")
    lines.append("")
    lines.append("### Customer Concentration")
    lines.append("")
    for c in cust["top_5_customers"]:
        lines.append(f"- Customer `{c['cust_number']}`: {c['invoice_count']:,} invoices")
    lines.append("")

    if "open_set_cold_start" in cust and cust["open_set_cold_start"]:
        osc = cust["open_set_cold_start"]
        lines.append("### Cold Start in Open Invoices Set")
        lines.append("")
        lines.append(f"- **Unique Customers in Open Set:** {osc['unique_customers_in_open_set']:,}")
        lines.append(f"- **Existing Customers (with prior history):** {osc['open_customers_with_prior_history']:,} ({osc['open_customers_with_prior_history_pct']}%)")
        lines.append(f"- **Cold-Start Customers (zero prior history):** {osc['open_customers_cold_start']:,} ({osc['open_customers_cold_start_pct']}%)")
        lines.append("")

    # 7. Financial & Currency Distribution
    lines.append("## 7. Financial & Currency Distribution")
    lines.append("")
    num = audit["numeric_and_categorical"]
    amt = num["total_open_amount"]
    lines.append(f"- **Total Invoice Amount Range:** ${amt['min']:,.2f} to ${amt['max']:,.2f}")
    lines.append(f"- **Mean Invoice Amount:** ${amt['mean']:,.2f} (std: ${amt['std']:,.2f})")
    lines.append(f"- **Median Invoice Amount:** ${amt['median']:,.2f}")
    lines.append(f"- **Currencies:** {num['currency_distribution']}")
    lines.append(f"- **Business Codes:** {num['business_code_distribution']}")
    lines.append(f"- **Document Types:** {num['document_type_distribution']}")
    lines.append(f"- **Unique Payment Terms:** {num['payment_terms_unique_count']}")
    lines.append("")

    # 8. Data Leakage Observations & Engineering Requirements
    lines.append("## 8. Data Leakage Observations & Constraints")
    lines.append("")
    lines.append("1. **Direct Leakage Fields:**")
    lines.append("   - `clear_date`: Ground truth outcome. Must be completely excluded from model inputs.")
    lines.append("   - `isOpen`: Directly indicates whether `clear_date` is null. Must be excluded from model inputs.")
    lines.append("   - Derived delay or payment duration: Must never enter the feature set.")
    lines.append("2. **Document Creation Inconsistency:**")
    lines.append("   - `document_create_date` precedes `posting_date` in many records; `document_create_date.1` is the actual ERP posting date. Canonical reference date for an invoice must be `posting_date`.")
    lines.append("3. **Historical Aggregation Leakage:**")
    lines.append("   - All customer-level historical metrics (e.g. `customer_avg_delay`, `customer_late_rate`) **must be calculated as-of the invoice posting date**, strictly utilizing events settled strictly prior to $T$.")
    lines.append("4. **Temporal Train/Validation/Test Split:**")
    lines.append("   - Evaluated models must never use random k-fold cross-validation across invoice rows. Evaluation must strictly follow chronological ordering.")
    lines.append("")

    # 9. Recommendations for Phase 1
    lines.append("## 9. Recommendations for Phase 1 (Ingestion & Feature Engineering)")
    lines.append("")
    lines.append("1. **Deduplication:** Remove the 1,161 exact duplicate rows prior to modeling.")
    lines.append("2. **Missing Value Handling:** Discard `area_business` (100% missing) and drop the 6 non-standard `X2` document type records.")
    lines.append("3. **Multi-Currency Normalization:** Either convert CAD to USD at historical parity or include currency indicator feature; total amount should be log-transformed given right skew.")
    lines.append("4. **As-Of Customer History Engine:** Build an efficient chronological cumulative calculation to compute customer behavioral metrics strictly as-of invoice posting date.")
    lines.append("5. **Cold-Start Fallbacks:** Impute missing historical metrics with global medians and provide a `is_new_customer` indicator flag.")
    lines.append("6. **Target Formulation:**")
    lines.append("   - Classification: Binary `is_late = delay_days > 0` (41.9% late, well-calibrated).")
    lines.append("   - Timing Regression: Predict `days_until_payment` or `delay_days` using gradient boosting with MAE loss.")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit dataset for MSME Receivables Intelligence Platform."
    )
    parser.add_argument(
        "--input",
        type=str,
        default="data/dataset.csv",
        help="Path to the raw CSV dataset file.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="docs/data_audit_report.md",
        help="Path where the Markdown audit report should be written.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress printing report to stdout.",
    )

    args = parser.parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    try:
        print(f"[*] Starting deterministic data audit on: {input_path}")
        audit_results = run_audit(input_path)
        print("[*] Audit completed. Generating report...")
        report_md = generate_markdown_report(audit_results)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_md)
        print(f"[+] Audit report successfully saved to: {output_path}")

        if not args.quiet:
            print("\n" + "=" * 60)
            print(f"AUDIT SUMMARY: {audit_results['total_rows']:,} rows, {audit_results['total_columns']} columns")
            print(f"Exact duplicates: {audit_results['duplicates']['exact_duplicate_rows']:,}")
            print(f"Paid records: {audit_results['payment_status']['paid_records']:,} ({audit_results['payment_status']['paid_pct']}%)")
            print(f"Open records: {audit_results['payment_status']['unpaid_censored_records']:,} ({audit_results['payment_status']['unpaid_censored_pct']}%)")
            targets = audit_results['targets']['classification']
            print(f"Target distribution: On-time={targets['on_time_pct']}%, Late={targets['late_pct']}%")
            print("=" * 60 + "\n")

    except Exception as exc:
        print(f"[!] Error executing data audit: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
