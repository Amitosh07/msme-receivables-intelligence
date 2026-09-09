"""
transformations.py — Invoice Feature Extraction & As-Of Safe Imputation

MSME Receivables Intelligence Platform — Version 1

This module handles:
1. Invoice-level feature derivation at invoice posting time T.
2. Training-derived imputation of missing customer-history features (cold starts),
   ensuring validation, test, and production inference never leak future statistics.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Features requiring cold-start imputation when customer history is missing
IMPUTABLE_FEATURES: List[str] = [
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
]


def compute_invoice_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Extract deterministic invoice-level features known at invoice posting time T.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with canonical date and financial columns.

    Returns
    -------
    pd.DataFrame
        DataFrame with invoice-level feature columns added.
    """
    out = df.copy()

    # Log-transformed invoice amount (mitigates extreme right-skew)
    out["log_amount"] = np.log1p(np.maximum(0.0, out["amount"].values))

    # Calendar features from invoice posting date
    out["posting_month"] = out["invoice_date"].dt.month.astype(int)
    out["posting_day_of_month"] = out["invoice_date"].dt.day.astype(int)
    out["posting_day_of_week"] = out["invoice_date"].dt.dayofweek.astype(int)
    out["posting_quarter"] = out["invoice_date"].dt.quarter.astype(int)
    out["is_weekend_posting"] = (out["posting_day_of_week"] >= 5).astype(int)

    # Calendar features from due date
    out["due_month"] = out["due_date"].dt.month.astype(int)
    out["due_day_of_week"] = out["due_date"].dt.dayofweek.astype(int)
    out["is_weekend_due"] = (out["due_day_of_week"] >= 5).astype(int)

    return out


class TrainingImputationTransformer:
    """
    As-of safe imputation transformer.
    Learns global medians strictly from the TRAINING partition, and applies
    those frozen medians to validation, test, and future inference datasets.
    """

    def __init__(self, imputable_features: List[str] | None = None) -> None:
        self.imputable_features = imputable_features or IMPUTABLE_FEATURES
        self.fitted_values_: Dict[str, float] = {}
        self.is_fitted: bool = False

    def fit(self, train_df: pd.DataFrame) -> "TrainingImputationTransformer":
        """
        Fit imputation values using TRAINING partition records only.
        """
        logger.info("Fitting imputation statistics on %d training records...", len(train_df))
        self.fitted_values_ = {}

        for col in self.imputable_features:
            if col in train_df.columns:
                valid_vals = train_df[col].dropna()
                if len(valid_vals) > 0:
                    median_val = float(valid_vals.median())
                else:
                    median_val = 0.0
                self.fitted_values_[col] = round(median_val, 6)
            else:
                self.fitted_values_[col] = 0.0

        self.is_fitted = True
        logger.info("Fitted imputation medians: %s", self.fitted_values_)
        return self

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Transform dataset by filling missing historical feature values with
        the frozen training medians.
        """
        if not self.is_fitted:
            raise RuntimeError("TrainingImputationTransformer must be fitted before transform")

        out = df.copy()
        for col, fill_val in self.fitted_values_.items():
            if col in out.columns:
                out[col] = out[col].fillna(fill_val)

        return out

    def to_dict(self) -> Dict[str, Any]:
        """Serialize fitted values to a JSON-safe dictionary."""
        return {
            "is_fitted": self.is_fitted,
            "imputable_features": self.imputable_features,
            "fitted_values": self.fitted_values_,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrainingImputationTransformer":
        """Deserialize from dictionary."""
        instance = cls(imputable_features=data.get("imputable_features"))
        instance.fitted_values_ = data.get("fitted_values", {})
        instance.is_fitted = data.get("is_fitted", False)
        return instance
