import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  countsAsRegistered,
  dropFinished,
  isTerminalStatus,
  nextPollDelay,
  upsertWatch,
  type ProcessingWatch,
} from "../src/lib/processing.ts";

const watch = (taskId: string, origin: ProcessingWatch["origin"] = "CURRENT"): ProcessingWatch => ({
  taskId,
  documentId: `doc-${taskId}`,
  filename: `${taskId}.pdf`,
  origin,
});

describe("invoice processing sync", () => {
  it("treats queued work as unfinished and completed or failed work as terminal", () => {
    assert.equal(isTerminalStatus("PENDING"), false);
    assert.equal(isTerminalStatus("PROCESSING"), false);
    assert.equal(isTerminalStatus("COMPLETED"), true);
    assert.equal(isTerminalStatus("FAILED"), true);
  });

  it("refreshes lists only when a completed task produced a processed invoice", () => {
    assert.equal(countsAsRegistered("COMPLETED", "PROCESSED"), true);
    assert.equal(countsAsRegistered("COMPLETED", "NEEDS_REVIEW"), false);
    assert.equal(countsAsRegistered("COMPLETED", "ERROR"), false);
    assert.equal(countsAsRegistered("FAILED", "ERROR"), false);
    assert.equal(countsAsRegistered("PENDING", "PROCESSED"), false);
  });

  it("backs off and stops growing the delay", () => {
    const delays = [0, 1, 2, 3, 4, 8].map(nextPollDelay);
    assert.deepEqual(delays.slice(0, 4), [1000, 1500, 2250, 3375]);
    assert.ok(delays.every((delay, index) => index === 0 || delay >= delays[index - 1]));
    assert.equal(nextPollDelay(20), 8000);
  });

  it("keeps each upload's task and drops it once that task finishes", () => {
    const first = upsertWatch([], watch("task-a", "HISTORICAL"));
    const both = upsertWatch(first, watch("task-b"));
    const replaced = upsertWatch(both, watch("task-a"));
    assert.equal(replaced.filter((item) => item.taskId === "task-a").length, 1);
    assert.equal(replaced.find((item) => item.taskId === "task-a")?.origin, "CURRENT");
    const remaining = dropFinished(replaced, new Set(["task-a"]));
    assert.deepEqual(remaining.map((item) => item.taskId), ["task-b"]);
  });

  it("does not let a historical company id ride along on the watch", () => {
    const saved = watch("task-c", "HISTORICAL");
    assert.equal("companyId" in saved, false);
    assert.equal(saved.origin, "HISTORICAL");
  });
});
