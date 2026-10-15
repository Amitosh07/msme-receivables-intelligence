export interface User { id: string; email: string; full_name: string; is_active: boolean; created_at: string }
export interface Business { id: string; name: string; business_code?: string | null; currency: string; created_at: string }
export interface CurrentUser extends User { business_id: string; business_name: string; role: string }
export interface AuthResponse { access_token: string; token_type: string; user: User; business: Business }
export type InvoiceOrigin = "CURRENT" | "HISTORICAL";
export interface Invoice { id: string; business_id: string; customer_id?: string | null; unresolved_customer_name?: string | null; customer_name?: string | null; invoice_number?: string | null; invoice_date?: string | null; due_date?: string | null; amount: number; currency?: string | null; payment_terms?: string | null; payment_status: string; processing_status: string; origin?: string; document_id?: string | null; total_paid?: number; outstanding_balance?: number; payments?: PaymentRecord[]; created_at: string; updated_at: string }
export interface InvoiceListItem extends Invoice { prediction?: Prediction | null }
export interface InvoiceList { items: InvoiceListItem[]; total: number; skip: number; limit: number }
export type InvoiceSort = "created" | "uploaded" | "due_date" | "amount" | "invoice_date";
export interface InvoiceQuery { skip?: number; limit?: number; origin?: InvoiceOrigin; payment_status?: string; q?: string; aging?: string; risk?: string; sort?: InvoiceSort; direction?: "asc" | "desc" }
export interface AmountCount { amount: number; count: number }
export interface AgingBucketSummary extends AmountCount { key: string; label: string }
export interface ExpectedPaymentDay extends AmountCount { expected_date: string; invoices: { invoice_id: string; invoice_number?: string | null; customer_name?: string | null; outstanding_balance: number }[] }
export interface PriorityInvoice { invoice_id: string; invoice_number?: string | null; customer_name?: string | null; due_date?: string | null; days_past_due?: number | null; amount: number; outstanding_balance: number; currency?: string | null; payment_status: string; risk_tier?: "LOW" | "MEDIUM" | "HIGH" | null; risk_score?: number | null; expected_payment_date?: string | null; reasons: string[] }
export interface DashboardSummary { as_of: string; currency: string; mixed_currency: boolean; due_soon_days: number; expected_window_days: number; current_invoice_count: number; outstanding: AmountCount; overdue: AmountCount; due_soon: AmountCount; high_risk: AmountCount; expected_payments: AmountCount; undated: AmountCount; aging: AgingBucketSummary[]; expected_schedule: ExpectedPaymentDay[]; priority: PriorityInvoice[] }
export type ForecastBasis = "predicted" | "due_date" | "past_predicted" | "overdue_unestimated" | "undated";
export interface ForecastInvoice { invoice_id: string; invoice_number?: string | null; customer_name?: string | null; outstanding_balance: number; due_date?: string | null; expected_date?: string | null; basis: ForecastBasis; risk_tier?: "LOW" | "MEDIUM" | "HIGH" | null }
export interface ForecastWeek { start: string; end: string; total: number; predicted: number; due_date_based: number; high_risk: number; count: number; invoices: ForecastInvoice[] }
export interface ForecastUnscheduled extends AmountCount { key: string; label: string; detail: string; invoices: ForecastInvoice[] }
export interface CashflowForecast { as_of: string; currency: string; mixed_currency: boolean; horizon_weeks: number; horizon_end: string; outstanding: AmountCount; scheduled: AmountCount; model_dated: AmountCount; due_date_dated: AmountCount; model_coverage: number; weeks: ForecastWeek[]; unscheduled: ForecastUnscheduled[]; assumptions: string[] }
export interface ExplanationFactor { key: string; label: string; effect: "raises" | "lowers"; contribution: number; share: number; evidence: string }
export interface PredictionExplanation { invoice_id: string; risk_score: number; stored_risk_score: number; matches_stored: boolean; risk_tier?: string | null; baseline_risk: number; factors: ExplanationFactor[]; unknown_categories: string[]; method: string }
export interface ImportanceFactor { key: string; label: string; importance: number }
export interface ModelInsights { classifier_model_version: string; timing_model_version: string; risk_drivers: ImportanceFactor[]; timing_drivers: ImportanceFactor[]; roc_auc: number; accuracy: number; precision: number; recall: number; timing_mae_days: number; timing_median_error_days: number; training_rows: number; training_period: string; test_period: string; required_history: number; risk_tiers: Record<string, string>; limitations: string[] }
export interface CustomerBalance { customer_id?: string | null; display_name: string; matched: boolean; outstanding: number; overdue: number; share_of_outstanding: number; open_invoice_count: number; overdue_invoice_count: number; oldest_days_overdue?: number | null; next_due_date?: string | null; next_expected_payment?: string | null; highest_risk_tier?: "LOW" | "MEDIUM" | "HIGH" | null; settled_invoice_count: number; on_time_rate?: number | null; average_days_late?: number | null; estimates_ready: boolean; has_history: boolean }
export interface CustomerBalances { as_of: string; currency: string; mixed_currency: boolean; total_outstanding: number; customers_owing: number; top_customer_share: number; top_five_share: number; required_history: number; customers: CustomerBalance[] }
export interface DemoStep { key: string; title: string; detail: string; history_count?: number | null; created: boolean }
export interface DemoRun { customer_id: string; customer_name: string; open_invoice_id: string; open_invoice_number: string; eligible_history_count: number; required_history: number; steps: DemoStep[]; prediction: Prediction; explanation: PredictionExplanation }
export interface DemoStatus { exists: boolean; customer_id?: string | null; customer_name?: string | null; open_invoice_id?: string | null; scored: boolean }
export interface DemoRemoval { removed_invoices: number; removed_payments: number; removed_customer: boolean }
export interface InvoiceDocument { id: string; business_id: string; invoice_id?: string | null; original_filename: string; content_type: string; file_size: number; processing_status: string; origin?: InvoiceOrigin; error_message?: string | null; created_at: string }
export interface InvoiceIdentitySummary { seller_name?: string | null; buyer_name?: string | null; seller_confidence?: number | null; buyer_confidence?: number | null; match_status?: string | null; review_reason?: string | null; matched_customer_id?: string | null; selected_customer_id?: string | null }
export interface TaskStatus { id: string; task_type: string; status: "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED"; state: "queued" | "processing" | "completed" | "failed"; invoice_id?: string | null; document_id?: string | null; processing_status?: string | null; error_message?: string | null; identity?: InvoiceIdentitySummary | null }
export interface InvoiceUpload { document_id: string; invoice_id?: string | null; original_filename: string; file_size: number; processing_status: string; origin?: InvoiceOrigin; task_id?: string | null; created_at: string }
export interface Prediction { prediction_available: true; reason?: null; id: string; business_id: string; invoice_id: string; prediction: boolean; risk_score: number; risk_tier: "LOW" | "MEDIUM" | "HIGH"; predicted_days_until_payment?: number | null; expected_payment_date?: string | null; created_at: string }
export interface PredictionUnavailable { prediction_available: false; reason: string; invoice_id: string; eligible_history_count: number; required_history_count: number }
export type PredictionState = Prediction | PredictionUnavailable
export interface PredictionList { items: Prediction[]; total: number; skip: number; limit: number }
export interface PaymentRecord { id: string; business_id: string; invoice_id?: string | null; invoice_reference?: string | null; payment_date: string; amount: number; reference?: string | null; note?: string | null; provenance?: string | null; created_at: string }
export interface ManualPaymentRequest { payment_date: string; amount: number; reference?: string; note?: string }
export interface ManualPaymentResponse { payment: PaymentRecord; invoice_id: string; invoice_amount: number; total_paid: number; outstanding_balance: number; payment_status: string; message: string }
export interface PaymentImport { total_rows: number; valid_rows: number; imported: number; matched: number; duplicates: number; duplicates_in_file: number; duplicates_existing: number; rejected: number; unmatched: number; preview: boolean; errors: { row_number: number; reason: string }[] }
export interface PaymentProof { id: string; business_id: string; invoice_id: string; original_filename: string; content_type: string; file_size: number; status: "PENDING" | "PROCESSING" | "VERIFIED" | "NEEDS_REVIEW" | "FAILED"; extracted_data?: { invoice_reference?: string | null; payment_date?: string | null; amount?: number | null; customer_name?: string | null; customer_gstin?: string | null; payment_reference?: string | null; confidence?: number; extraction_method?: string; } | null; error_message?: string | null; payment_id?: string | null; task_id?: string | null; created_at: string; updated_at?: string; }
export interface PaymentProofUpload { proof_id: string; invoice_id: string; task_id?: string | null; original_filename: string; file_size: number; status: string; created_at: string; }

export interface HistoricalCompanySummary {
  id: string;
  display_name: string;
  normalized_name: string;
  gstin?: string | null;
  customer_ref?: string | null;
  historical_invoice_count: number;
  total_amount: number;
  total_paid: number;
  outstanding_balance: number;
  last_invoice_date?: string | null;
  last_payment_date?: string | null;
}

export interface HistoricalCompanyCreate {
  display_name: string;
  gstin?: string | null;
}

export interface HistoricalReviewInvoice {
  id: string;
  customer_id?: string | null;
  customer_name?: string | null;
  unresolved_customer_name?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  due_date?: string | null;
  amount: number;
  currency?: string | null;
  processing_status: string;
  seller_name?: string | null;
  buyer_name?: string | null;
  seller_confidence?: number | null;
  buyer_confidence?: number | null;
  match_status?: string | null;
  review_reason?: string | null;
}

export interface HistoricalInvoiceItem {
  id: string;
  invoice_number?: string | null;
  invoice_date?: string | null;
  due_date?: string | null;
  amount: number;
  currency?: string | null;
  origin: string;
  payment_status: string;
  processing_status: string;
  total_paid: number;
  outstanding_balance: number;
  payment_date?: string | null;
  payment_count: number;
  document_id?: string | null;
  customer_id?: string | null;
  customer_name?: string | null;
}

export interface HistoricalInvoiceCompanyCorrection {
  replacement_customer_id?: string | null;
  replacement_company_name?: string | null;
  create_if_missing?: boolean;
  gstin?: string | null;
}

export interface HistoricalCompanyDetail {
  id: string;
  display_name: string;
  normalized_name: string;
  gstin?: string | null;
  customer_ref?: string | null;
  historical_invoice_count: number;
  total_amount: number;
  total_paid: number;
  outstanding_balance: number;
  invoices: HistoricalInvoiceItem[];
}
