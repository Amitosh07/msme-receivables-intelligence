# Data Source & Provenance Record

## Dataset Identification

- **Dataset Name:** Payment Date Dataset (also referred to as `payment-date-prediction`)
- **Primary Source:** Kaggle
- **Source URL:** [https://www.kaggle.com/datasets/rajattomar132/payment-date-dataset](https://www.kaggle.com/datasets/rajattomar132/payment-date-dataset)
- **Dataset Author / Contributor:** Rajat Tomar (Kaggle handle: `rajattomar132`)
- **Local File Path:** `data/dataset.csv`
- **File Size:** 6,932,606 bytes (~6.9 MB)
- **Total Records:** 50,000 rows, 19 columns
- **Observation Date Range:** Posting dates from December 30, 2018 through May 22, 2020

---

## Licensing & Usage Rights

```text
License status: NOT VERIFIED
```

### Licensing Assessment & Uncertainty

1. **No Explicit Open-Source License:** The Kaggle dataset repository does not include a standardized open-source license file (such as MIT, Apache 2.0, or CC-BY-4.0). Under Kaggle's default terms, user-uploaded datasets without explicit licensing are governed by general platform community terms.
2. **Usage Boundary:** This dataset is utilized strictly for **offline research, development, feature engineering, and model benchmarking** for Version 1 of the MSME Receivables Intelligence Platform.
3. **Not Proprietary / Production Business Data:** The dataset is not deployed as production user data. In production, tenant data will be uploaded directly by authenticated users through the application's ingestion pipelines.
4. **Data Privacy & Synthetic Characteristics:** The customer names (e.g., WAL-MAR, SYSC) and business codes appear to be derived from an enterprise ERP export (e.g. SAP R/3 or S/4HANA) with customer names and transaction IDs truncated or masked.

---

## Technical Context & Provenance

The dataset represents accounts receivable (A/R) subledger records:
- **ERP System Signatures:** Column conventions (`doc_id`, `posting_id`, `document type = RV`, `baseline_create_date`, `cust_payment_terms`) strongly indicate an SAP Accounts Receivable table export (specifically structures related to `BSID`/`BSAD`).
- **Snapshot Nature:** The dataset contains 40,000 historical cleared invoices (`isOpen = 0`, with non-null `clear_date`) and 10,000 open/unsettled invoices (`isOpen = 1`, with null `clear_date`) at snapshot time (cutoff: May 2020).
- **Currencies:** Two primary currencies are represented: USD (92.16%) and CAD (7.84%).
