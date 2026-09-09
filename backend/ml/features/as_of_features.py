"""
as_of_features.py — As-Of Historical Feature Engineering Engine

MSME Receivables Intelligence Platform — Version 1

CRITICAL ML CORRECTNESS RULE:
For each invoice at prediction reference time T (invoice posting date),
all customer historical features are computed strictly using information
available before T. Future payment outcomes and future invoice events
must never be included.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_customer_as_of_features(canonical_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute customer historical behavioral and relationship features strictly as-of
    each invoice's posting date.

    Parameters
    ----------
    canonical_df : pd.DataFrame
        Clean canonical dataset sorted chronologically.

    Returns
    -------
    pd.DataFrame
        DataFrame with as-of historical feature columns added.
    """
    logger.info("Computing as-of customer features for %d invoices...", len(canonical_df))
    df = canonical_df.copy()

    # Pre-allocate feature arrays
    n = len(df)
    prior_inv_count = np.zeros(n, dtype=int)
    prior_pay_count = np.zeros(n, dtype=int)
    prior_late_count = np.zeros(n, dtype=int)
    late_rate = np.full(n, np.nan, dtype=float)
    avg_delay = np.full(n, np.nan, dtype=float)
    median_delay = np.full(n, np.nan, dtype=float)
    std_delay = np.full(n, np.nan, dtype=float)
    max_delay = np.full(n, np.nan, dtype=float)
    min_delay = np.full(n, np.nan, dtype=float)
    recent_avg_delay_3 = np.full(n, np.nan, dtype=float)
    recent_late_rate_3 = np.full(n, np.nan, dtype=float)
    days_since_last_pay = np.full(n, np.nan, dtype=float)
    days_since_prev_inv = np.full(n, np.nan, dtype=float)

    # Use normalized integer timestamps in nanoseconds / days for fast searchsorted
    # Normalize dates to calendar day to prevent any intraday ambiguity
    invoice_dates_day = df["invoice_date"].dt.floor("D").values
    clear_dates_day = df["clear_date"].dt.floor("D").values
    delay_days_val = df["delay_days"].values
    is_late_val = df["is_late"].values

    # Group by customer_id
    # We get indices for each customer
    customer_groups = df.groupby("customer_id", sort=False).indices

    for cust_id, indices in customer_groups.items():
        # All invoice dates for this customer
        cust_inv_dates = invoice_dates_day[indices]

        # Invoices of this customer sorted by invoice date
        inv_sort_idx = np.argsort(cust_inv_dates)
        sorted_cust_inv_dates = cust_inv_dates[inv_sort_idx]

        # Extract settled payments for this customer
        cust_clear_dates = clear_dates_day[indices]
        settled_mask = ~pd.isna(cust_clear_dates)

        if np.any(settled_mask):
            settled_indices = indices[settled_mask]
            s_dates = clear_dates_day[settled_indices]
            s_delays = delay_days_val[settled_indices]
            s_lates = is_late_val[settled_indices]

            # Sort settled payments by clear_date
            pay_sort_idx = np.argsort(s_dates)
            s_dates_sorted = s_dates[pay_sort_idx]
            s_delays_sorted = s_delays[pay_sort_idx]
            s_lates_sorted = s_lates[pay_sort_idx]
        else:
            s_dates_sorted = np.array([], dtype="datetime64[ns]")
            s_delays_sorted = np.array([], dtype=float)
            s_lates_sorted = np.array([], dtype=float)

        # For each invoice of this customer, compute as-of features
        for local_i, global_i in enumerate(indices):
            T = cust_inv_dates[local_i]

            # 1. Prior invoices posted strictly before T
            inv_idx = np.searchsorted(sorted_cust_inv_dates, T, side="left")
            prior_inv_count[global_i] = inv_idx
            if inv_idx > 0:
                last_inv_date = sorted_cust_inv_dates[inv_idx - 1]
                days_diff = (T - last_inv_date) / np.timedelta64(1, "D")
                days_since_prev_inv[global_i] = max(0.0, float(days_diff))

            # 2. Prior payments cleared strictly before T
            if len(s_dates_sorted) > 0:
                pay_idx = np.searchsorted(s_dates_sorted, T, side="left")
            else:
                pay_idx = 0

            prior_pay_count[global_i] = pay_idx

            if pay_idx > 0:
                prior_delays = s_delays_sorted[:pay_idx]
                prior_lates = s_lates_sorted[:pay_idx]

                n_lates = int(np.sum(prior_lates))
                prior_late_count[global_i] = n_lates
                late_rate[global_i] = float(n_lates / pay_idx)

                avg_delay[global_i] = float(np.mean(prior_delays))
                median_delay[global_i] = float(np.median(prior_delays))
                max_delay[global_i] = float(np.max(prior_delays))
                min_delay[global_i] = float(np.min(prior_delays))

                if pay_idx >= 2:
                    std_delay[global_i] = float(np.std(prior_delays, ddof=1))
                else:
                    std_delay[global_i] = 0.0

                # Recent 3 settled payments
                recent_k = min(3, pay_idx)
                recent_delays = s_delays_sorted[pay_idx - recent_k : pay_idx]
                recent_lates = s_lates_sorted[pay_idx - recent_k : pay_idx]
                recent_avg_delay_3[global_i] = float(np.mean(recent_delays))
                recent_late_rate_3[global_i] = float(np.mean(recent_lates))

                # Days since most recent cleared payment
                last_pay_date = s_dates_sorted[pay_idx - 1]
                pay_diff = (T - last_pay_date) / np.timedelta64(1, "D")
                days_since_last_pay[global_i] = max(0.0, float(pay_diff))

    # Assign computed features to DataFrame
    df["cust_prior_invoice_count"] = prior_inv_count
    df["cust_prior_payment_count"] = prior_pay_count
    df["cust_prior_late_count"] = prior_late_count
    df["cust_late_payment_rate"] = late_rate
    df["cust_avg_delay"] = avg_delay
    df["cust_median_delay"] = median_delay
    df["cust_std_delay"] = std_delay
    df["cust_max_delay"] = max_delay
    df["cust_min_delay"] = min_delay
    df["cust_recent_avg_delay_3"] = recent_avg_delay_3
    df["cust_recent_late_rate_3"] = recent_late_rate_3
    df["days_since_last_payment"] = days_since_last_pay
    df["days_since_prev_invoice"] = days_since_prev_inv

    # Cold start indicator: customer has < 3 prior invoices
    df["is_new_customer"] = (df["cust_prior_invoice_count"] < 3).astype(int)

    logger.info("As-of features computed successfully.")
    return df
