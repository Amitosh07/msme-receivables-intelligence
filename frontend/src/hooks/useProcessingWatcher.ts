import { useEffect } from "react";
import { tasksApi } from "../lib/api";
import { notifyDataChanged } from "../lib/events";
import {
  countsAsRegistered,
  dropFinished,
  emitProcessing,
  isTerminalStatus,
  nextPollDelay,
  PROCESSING_WATCH_EVENT,
  readWatches,
  writeWatches,
  type ProcessingUpdate,
  type ProcessingWatch,
} from "../lib/processing";

const NETWORK_GIVE_UP = 6;

function toUpdate(watch: ProcessingWatch, status: Awaited<ReturnType<typeof tasksApi.get>>): ProcessingUpdate {
  const state = status.state;
  return {
    taskId: watch.taskId,
    documentId: watch.documentId,
    filename: watch.filename,
    origin: watch.origin,
    state,
    processingStatus: status.processing_status ?? null,
    errorMessage: status.error_message ?? null,
    identity: status.identity ?? null,
    registered: countsAsRegistered(status.status, status.processing_status),
  };
}

/** Poll in-flight invoice tasks until they finish, including after the upload screen unmounts. */
export function useProcessingWatcher(): void {
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let generation = 0;
    let attempt = 0;
    const failures = new Map<string, number>();

    const schedule = (current: number, delay: number) => {
      if (cancelled || current !== generation) return;
      timer = window.setTimeout(() => { void tick(current); }, delay);
    };

    const tick = async (current: number) => {
      if (cancelled || current !== generation) return;
      const watches = readWatches();
      if (!watches.length) return;

      const finished = new Set<string>();
      let sawTerminal = false;
      let networkProblem = false;
      for (const watch of watches) {
        if (cancelled || current !== generation) return;
        try {
          const status = await tasksApi.get(watch.taskId);
          failures.delete(watch.taskId);
          emitProcessing(toUpdate(watch, status));
          if (isTerminalStatus(status.status)) {
            finished.add(watch.taskId);
            sawTerminal = true;
          }
        } catch {
          const count = (failures.get(watch.taskId) ?? 0) + 1;
          failures.set(watch.taskId, count);
          networkProblem = true;
          if (count >= NETWORK_GIVE_UP) {
            finished.add(watch.taskId);
            emitProcessing({
              taskId: watch.taskId,
              documentId: watch.documentId,
              filename: watch.filename,
              origin: watch.origin,
              state: "failed",
              processingStatus: null,
              errorMessage: "Could not reach the server to check processing status.",
              identity: null,
              registered: false,
            });
          }
        }
      }

      if (cancelled || current !== generation) return;
      writeWatches(dropFinished(readWatches(), finished));
      if (sawTerminal) notifyDataChanged();
      const remaining = readWatches();
      if (!remaining.length) return;
      attempt += 1;
      const delay = networkProblem ? Math.max(nextPollDelay(attempt), 4000) : nextPollDelay(attempt);
      schedule(current, delay);
    };

    const start = () => {
      if (timer) window.clearTimeout(timer);
      generation += 1;
      attempt = 0;
      const current = generation;
      if (!readWatches().length) return;
      schedule(current, 400);
    };

    start();
    window.addEventListener(PROCESSING_WATCH_EVENT, start);
    return () => {
      cancelled = true;
      generation += 1;
      if (timer) window.clearTimeout(timer);
      window.removeEventListener(PROCESSING_WATCH_EVENT, start);
    };
  }, []);
}
