import type { InvoiceUpload } from "./types";

const STORAGE_KEY = "msme_invoice_processing_watches";
export const PROCESSING_WATCH_EVENT = "msme:processing-watch";

export interface ProcessingWatch {
  taskId: string;
  documentId: string;
  filename: string;
  origin: "CURRENT" | "HISTORICAL";
}

export interface ProcessingUpdate {
  taskId: string;
  documentId: string;
  filename: string;
  origin: "CURRENT" | "HISTORICAL";
  state: "queued" | "processing" | "completed" | "failed";
  processingStatus: string | null;
  errorMessage: string | null;
  identity: {
    seller_name?: string | null;
    buyer_name?: string | null;
    seller_confidence?: number | null;
    buyer_confidence?: number | null;
    match_status?: string | null;
    review_reason?: string | null;
  } | null;
  registered: boolean;
}

const listeners = new Set<(update: ProcessingUpdate) => void>();

export function isTerminalStatus(status: string): boolean {
  return status === "COMPLETED" || status === "FAILED";
}

/** A completed parse counts as a registered invoice only after it is fully processed. */
export function countsAsRegistered(status: string, processingStatus: string | null | undefined): boolean {
  return status === "COMPLETED" && processingStatus === "PROCESSED";
}

export function nextPollDelay(attempt: number): number {
  const step = Math.max(0, attempt);
  return Math.min(8000, Math.round(1000 * Math.pow(1.5, step)));
}

export function upsertWatch(watches: ProcessingWatch[], next: ProcessingWatch): ProcessingWatch[] {
  return [...watches.filter((watch) => watch.taskId !== next.taskId), next];
}

export function dropFinished(watches: ProcessingWatch[], finished: ReadonlySet<string>): ProcessingWatch[] {
  return watches.filter((watch) => !finished.has(watch.taskId));
}

export function readWatches(): ProcessingWatch[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as ProcessingWatch[];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((watch) => watch && typeof watch.taskId === "string" && typeof watch.documentId === "string");
  } catch {
    return [];
  }
}

export function writeWatches(watches: ProcessingWatch[]): void {
  if (!watches.length) sessionStorage.removeItem(STORAGE_KEY);
  else sessionStorage.setItem(STORAGE_KEY, JSON.stringify(watches));
}

export function clearProcessingWatches(): void {
  writeWatches([]);
}

export function subscribeProcessing(listener: (update: ProcessingUpdate) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function emitProcessing(update: ProcessingUpdate): void {
  listeners.forEach((listener) => listener(update));
}

export function watchInvoiceProcessing(upload: InvoiceUpload & { origin?: string }): void {
  if (!upload.task_id) return;
  const watch: ProcessingWatch = {
    taskId: upload.task_id,
    documentId: upload.document_id,
    filename: upload.original_filename,
    origin: upload.origin === "HISTORICAL" ? "HISTORICAL" : "CURRENT",
  };
  writeWatches(upsertWatch(readWatches(), watch));
  window.dispatchEvent(new Event(PROCESSING_WATCH_EVENT));
}
