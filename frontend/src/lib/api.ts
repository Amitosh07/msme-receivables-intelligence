import axios, { AxiosError } from "axios";
import type { AuthResponse, CashflowForecast, CurrentUser, CustomerBalances, DashboardSummary, DemoRemoval, DemoRun, DemoStatus, ModelInsights, PredictionExplanation, HistoricalCompanyCreate, HistoricalCompanyDetail, HistoricalCompanySummary, HistoricalInvoiceItem, HistoricalReviewInvoice, Invoice, InvoiceDocument, InvoiceList, InvoiceOrigin, InvoiceQuery, InvoiceUpload, ManualPaymentRequest, ManualPaymentResponse, PaymentImport, PaymentProof, PaymentProofUpload, PaymentRecord, PredictionList, PredictionState, TaskStatus } from "./types";

const TOKEN_KEY = "msme_receivables_token";
const SESSION_KEY = "msme_receivables_session_token";
export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(SESSION_KEY),
  set: (token: string, remember = false) => { sessionStorage.removeItem(SESSION_KEY); localStorage.removeItem(TOKEN_KEY); (remember ? localStorage : sessionStorage).setItem(remember ? TOKEN_KEY : SESSION_KEY, token); },
  clear: () => { localStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(SESSION_KEY); },
};
export const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || "http://localhost:8000" });
api.interceptors.request.use((config) => { const token = tokenStore.get(); if (token) config.headers.Authorization = `Bearer ${token}`; return config; });
const CREDENTIAL_PATHS = ["/auth/login", "/auth/register"];
const isCredentialRequest = (url?: string) => Boolean(url && CREDENTIAL_PATHS.some((path) => url.endsWith(path)));
api.interceptors.response.use((response) => response, (error) => {
  if (error.response?.status === 401 && !isCredentialRequest(error.config?.url) && tokenStore.get()) {
    tokenStore.clear();
    window.dispatchEvent(new CustomEvent("msme:session-expired"));
  }
  return Promise.reject(error);
});
export class ApiError extends Error { constructor(message: string, public status?: number) { super(message); } }
export function normaliseError(error: unknown): ApiError {
  if (error instanceof AxiosError) { const detail = error.response?.data?.detail; const message = error.response?.status === 401 ? (isCredentialRequest(error.config?.url) ? (typeof detail === "string" ? detail : "Invalid email or password.") : "Your session has expired. Please sign in again.") : error.response?.status === 403 ? "You do not have access to this business data." : error.response?.status === 404 ? "The requested record could not be found." : error.response?.status === 409 ? (typeof detail === "string" ? detail : "This record already exists.") : error.response?.status === 422 ? (typeof detail === "string" ? detail : "Please check the information and try again.") : error.response?.status === 429 ? (typeof detail === "string" ? detail : "Too many requests. Please wait a minute and try again.") : error.response?.status && error.response.status >= 500 ? "We couldn't load this right now. Please try again." : error.response ? "We couldn't complete that request. Please try again." : "We couldn't reach the service. Check your connection and try again."; return new ApiError(message, error.response?.status); }
  return new ApiError("Something went wrong. Please try again.");
}
export const authApi = { register: (data: { email: string; password: string; full_name: string; business_name: string }) => api.post<AuthResponse>("/auth/register", data).then((r) => r.data), login: (data: { email: string; password: string }) => api.post<AuthResponse>("/auth/login", data).then((r) => r.data), me: () => api.get<CurrentUser>("/auth/me").then((r) => r.data) };
const uploadFile = (path: string, file: File) => { const body = new FormData(); body.append("file", file); return api.post<PaymentImport>(path, body).then((r) => r.data); };
export const tasksApi = {
  get: (taskId: string) => api.get<TaskStatus>(`/tasks/${taskId}`).then((r) => r.data),
};
export const invoicesApi = {
  list: (query: InvoiceQuery = {}) => {
    const params = Object.fromEntries(Object.entries({ origin: "CURRENT", ...query }).filter(([, value]) => value !== undefined && value !== ""));
    return api.get<InvoiceList>("/invoices", { params }).then((r) => r.data);
  },
  get: (id: string) => api.get<Invoice>(`/invoices/${id}`).then((r) => r.data),
  getPayments: (id: string) => api.get<PaymentRecord[]>(`/invoices/${id}/payments`).then((r) => r.data),
  documents: (origin: InvoiceOrigin = "CURRENT") => api.get<InvoiceDocument[]>("/invoices/documents", { params: { limit: 100 } }).then((r) => r.data.filter((doc) => (doc.origin ?? "CURRENT") === origin)),
  upload: (file: File) => { const body = new FormData(); body.append("file", file); return api.post<InvoiceUpload>("/invoices/upload", body).then((r) => r.data); },
};
export const dashboardApi = {
  summary: (priorityLimit = 6) => api.get<DashboardSummary>("/dashboard/summary", { params: { priority_limit: priorityLimit } }).then((r) => r.data),
  cashflow: (weeks = 8) => api.get<CashflowForecast>("/dashboard/cashflow", { params: { weeks } }).then((r) => r.data),
};
export const customersApi = {
  balances: () => api.get<CustomerBalances>("/customers/balances").then((r) => r.data),
};
export const demoApi = {
  status: () => api.get<DemoStatus>("/demo/scripted-path").then((r) => r.data),
  run: () => api.post<DemoRun>("/demo/scripted-path").then((r) => r.data),
  remove: () => api.delete<DemoRemoval>("/demo/scripted-path").then((r) => r.data),
};
export const predictionsApi = {
  list: (skip = 0) => api.get<PredictionList>("/predictions", { params: { skip, limit: 100 } }).then((r) => r.data),
  get: (id: string) => api.get<PredictionState>(`/invoices/${id}/prediction`).then((r) => r.data),
  create: (id: string) => api.post<PredictionState>(`/invoices/${id}/predict`).then((r) => r.data),
  explain: (id: string) => api.get<PredictionExplanation>(`/invoices/${id}/prediction/explanation`).then((r) => r.data),
  insights: () => api.get<ModelInsights>("/predictions/model-insights").then((r) => r.data),
};
export const paymentsApi = {
  preview: (file: File) => uploadFile("/payments/preview", file),
  upload: (file: File) => uploadFile("/payments/import", file),
  recordManual: (invoiceId: string, data: ManualPaymentRequest) => api.post<ManualPaymentResponse>(`/invoices/${invoiceId}/payments`, data).then((r) => r.data),
  uploadProof: (invoiceId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return api.post<PaymentProofUpload>(`/invoices/${invoiceId}/payment-proof`, body).then((r) => r.data);
  },
  getProofs: (invoiceId: string) => api.get<PaymentProof[]>(`/invoices/${invoiceId}/payment-proofs`).then((r) => r.data),
  getProof: (invoiceId: string, proofId: string) => api.get<PaymentProof>(`/invoices/${invoiceId}/payment-proofs/${proofId}`).then((r) => r.data),
};
export const historicalApi = {
  listReviews: () => api.get<HistoricalReviewInvoice[]>("/historical/invoices/review").then((r) => r.data),
  createCompany: (data: HistoricalCompanyCreate) =>
    api.post<HistoricalCompanySummary>("/historical/companies", data).then((r) => r.data),
  updateCompanyGstin: (companyId: string, gstin: string | null) =>
    api.patch<HistoricalCompanyDetail>(`/historical/companies/${companyId}`, { gstin }).then((r) => r.data),
  deleteCompany: (companyId: string) =>
    api.delete<{ message: string; id: string; action: string }>(`/historical/companies/${companyId}`).then((r) => r.data),
  listCompanies: (search?: string) =>
    api.get<HistoricalCompanySummary[]>("/historical/companies", { params: search ? { search } : undefined }).then((r) => r.data),
  getCompany: (id: string) =>
    api.get<HistoricalCompanyDetail>(`/historical/companies/${id}`).then((r) => r.data),
  uploadUnassignedInvoice: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return api.post<InvoiceUpload>("/historical/invoices/upload", body).then((r) => r.data);
  },
  reviewInvoice: (invoiceId: string, data: { customer_id?: string; company_name?: string; gstin?: string; due_date?: string }) =>
    api.patch<HistoricalInvoiceItem>(`/historical/invoices/${invoiceId}/review`, data).then((r) => r.data),
  correctInvoiceCompany: (invoiceId: string, data: { replacement_customer_id?: string; replacement_company_name?: string; create_if_missing?: boolean; gstin?: string }) =>
    api.patch<HistoricalInvoiceItem>(`/historical/invoices/${invoiceId}/company`, data).then((r) => r.data),
  uploadInvoice: (companyId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return api.post<InvoiceUpload>(`/historical/companies/${companyId}/invoices/upload`, body).then((r) => r.data);
  },
  createManualInvoice: (companyId: string, data: { amount: number; due_date: string; payment_date?: string; invoice_number?: string; invoice_date?: string }) =>
    api.post<HistoricalInvoiceItem>(`/historical/companies/${companyId}/invoices/manual`, data).then((r) => r.data),
  recordPayment: (companyId: string, invoiceId: string, data: ManualPaymentRequest) =>
    api.post<ManualPaymentResponse>(`/historical/companies/${companyId}/invoices/${invoiceId}/payments`, data).then((r) => r.data),
  previewPayments: (companyId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return api.post<PaymentImport>(`/historical/companies/${companyId}/payments/preview`, body).then((r) => r.data);
  },
  importPayments: (companyId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return api.post<PaymentImport>(`/historical/companies/${companyId}/payments/import`, body).then((r) => r.data);
  },
};
