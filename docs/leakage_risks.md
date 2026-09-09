# Data Leakage Risks & As-Of Feature Engineering Architecture

This document formalizes data leakage prevention rules for the **MSME Receivables Intelligence Platform**. Machine learning models in accounts receivable and credit risk are exceptionally vulnerable to subtle temporal and target leakage that artificially inflate offline performance while causing silent failures in production.

---

## 1. Categorization of Leakage Risks

### 1.1 Direct Target Leakage

Direct leakage occurs when the target variable, or an exact mathematical proxy of the target variable, is included in the feature set $X$.

| Field / Feature | Nature of Leakage | Risk Severity | Mandatory Prevention Rule |
| :--- | :--- | :---: | :--- |
| `clear_date` | Actual settlement date. The prediction target is derived directly from `clear_date`. | **CATASTROPHIC** | **Strictly excluded from model inputs.** Only exists in target generation pipeline. |
| `isOpen` | Boolean flag (`0` if paid, `1` if open). In the dataset, `isOpen == 1` is 100% collinear with `clear_date.isnull()`. | **CATASTROPHIC** | **Strictly excluded from model inputs.** Used solely for dataset partitioning. |
| `delay_days` | Derived as `(clear_date - due_in_date)`. Direct target. | **CATASTROPHIC** | **Excluded from model inputs.** |
| `days_until_payment` | Derived as `(clear_date - posting_date)`. Direct target. | **CATASTROPHIC** | **Excluded from model inputs.** |

---

### 1.2 ERP Ingestion & Document Date Inversion Leakage

In standard ERP systems (like SAP), internal draft document creation dates and formal posting dates can diverge:

- `document_create_date`: Date the draft invoice record was created in the database.
- `posting_date` (`document_create_date.1`): Official accounting date the invoice was posted.

**Risk:** In 140 dataset records, `due_in_date < posting_date` due to backdated entries. Furthermore, `document_create_date` occasionally occurs after `posting_date` due to batch ledger posting.
**Rule:** The canonical timestamp defining the information horizon for an invoice is strictly `posting_date` ($T$). No system events occurring after `posting_date` may be used when scoring that invoice.

---

### 1.3 Historical Aggregate & Lookahead Leakage

This is the most common and dangerous form of leakage in tabular financial ML.

#### The Flawed Approach (Common Mistake)
Computing customer behavioral aggregates (e.g. mean payment delay, late payment rate) across the entire dataset (or computing them prior to a temporal split):

```python
# DANGEROUS: LEAKS FUTURE BEHAVIOR INTO PAST INVOICES
customer_mean_delay = df.groupby('cust_number')['delay_days'].mean()
df['customer_avg_delay'] = df['cust_number'].map(customer_mean_delay)
# Then splitting into train/test leaks 2020 behavior into 2019 predictions!
```

If Customer X was a reliable payer in 2019 but experienced insolvency and defaulted on 10 consecutive invoices in late 2020, calculating a global late rate across all records back-propagates their 2020 distress into their 2019 predictions.

---

## 2. Mandatory Solution: As-Of Feature Engineering

Phase 1 feature engineering must strictly follow the **As-Of Information Horizon Principle**.

### 2.1 The As-Of Horizon Principle

For an invoice $I_k$ belonging to customer $C$ posted at timestamp $T_k$:

$$\mathcal{H}(T_k) = \{ I_j \mid \text{customer}(I_j) = C \;\land\; \text{clear\_date}(I_j) < T_k \}$$

Every customer feature for invoice $I_k$ must be calculated **strictly using records in $\mathcal{H}(T_k)$**:

```text
Time Horizon: ----------------------------------------------------->
                                                Invoice I_k
                                             Posted at Time T_k
                                                    ▼
Past Events:  [Invoice 1 Settled]   [Invoice 2 Settled]    |   [Invoice 3 Settled]
                       │                     │             |           │
                       └───────────┬─────────┘             |           │
                                   ▼                       |           ▼
                      Eligible for History (H)             |     Future Event!
                   Used to compute features for I_k        |   (STRICTLY FORBIDDEN)
```

### 2.2 Crucial Settlement Timing Nuance

Notice that an invoice $I_j$ is only eligible to inform $I_k$ if **its payment cleared before $T_k$** ($\text{clear\_date}(I_j) < T_k$).
If invoice $I_j$ was posted at $T_k - 5\text{ days}$ but did not clear until $T_k + 10\text{ days}$, its outcome was **unknown** at time $T_k$. Using its payment delay for $I_k$ would be lookahead leakage.

### 2.3 Features Requiring As-Of Computation

The following features must be computed via chronological expanding windows or cumulative historical joins:

1. `customer_historical_invoice_count`: Number of invoices settled by this customer prior to $T_k$.
2. `customer_historical_late_rate`: Percentage of settled invoices where $\text{delay\_days} > 0$ prior to $T_k$.
3. `customer_historical_avg_delay`: Mean payment delay on settled invoices prior to $T_k$.
4. `customer_historical_median_delay`: Median payment delay on settled invoices prior to $T_k$.
5. `customer_historical_std_delay`: Standard deviation of payment delays prior to $T_k$.
6. `customer_recent_delay_last_3`: Mean delay across the last 3 settled invoices prior to $T_k$.
7. `customer_days_since_last_settlement`: Calendar days between the most recent settled payment and $T_k$.

---

## 3. Implementation Blueprint for Phase 1

To ensure scalable, leakage-free feature computation:

```python
def compute_as_of_customer_features(df_invoices: pd.DataFrame, df_payments: pd.DataFrame) -> pd.DataFrame:
    """
    Chronological As-Of feature computation.
    1. Sort historical payment events by payment_date.
    2. Join onto invoices strictly matching customer_id and payment_date < invoice_date.
    3. Aggregate cumulative statistics up to invoice_date.
    4. Fill cold-start customers (count == 0) with global training medians.
    """
    pass
```

### 4. Automated Leakage Assertions in Phase 1 & 2

The pipeline must enforce automated unit and integration tests:

1. **Feature Column Blacklist Assertion:**
   Assert that `clear_date`, `isOpen`, `delay_days`, and `days_until_payment` are not present in the input feature matrix $X$.
2. **Temporal Integrity Assertion:**
   For any synthetic invoice inserted with posting date $T$, assert that modifying payment outcomes of events occurring after $T$ produces **zero change** in the computed feature vector for $T$.
