import assert from "node:assert/strict";
import test from "node:test";

import {
  checkAndRecover, dispatchCountLast24h, evaluateRuns, slotStart,
} from "./src/index.mjs";

const at = (value) => new Date(value);

test("uses fixed UTC three-hour slots", () => {
  assert.equal(slotStart(at("2026-09-07T09:36:00Z")).toISOString(),
               "2026-09-07T09:00:00.000Z");
});

test("missing slot dispatches after grace", () => {
  assert.equal(evaluateRuns([], at("2026-09-07T09:10:00Z"), 25).shouldDispatch, false);
  const missing = evaluateRuns([], at("2026-09-07T09:37:00Z"), 25);
  assert.equal(missing.shouldDispatch, true);
  assert.equal(missing.state, "trigger_missing");
});

test("active or successful run in the slot prevents overlap", () => {
  const active = evaluateRuns([{
    created_at: "2026-09-07T09:12:00Z", status: "in_progress", conclusion: null,
  }], at("2026-09-07T09:37:00Z"), 25);
  assert.equal(active.shouldDispatch, false);
  assert.equal(active.state, "workflow_active");

  const done = evaluateRuns([{
    created_at: "2026-09-07T09:12:00Z", status: "completed", conclusion: "success",
  }], at("2026-09-07T09:37:00Z"), 25);
  assert.equal(done.shouldDispatch, false);
  assert.equal(done.state, "workflow_success");
});

test("failed workflow is retried before grace ends", () => {
  const failed = evaluateRuns([{
    created_at: "2026-09-07T09:12:00Z", status: "completed", conclusion: "failure",
  }], at("2026-09-07T09:22:00Z"), 25);
  assert.equal(failed.shouldDispatch, true);
  assert.equal(failed.state, "workflow_failed");
});

test("previous-slot runs do not satisfy the current slot", () => {
  const stale = evaluateRuns([{
    created_at: "2026-09-07T06:12:00Z", status: "completed", conclusion: "success",
  }], at("2026-09-07T09:37:00Z"), 25);
  assert.equal(stale.shouldDispatch, true);
});

test("daily dispatch cap counts only recent workflow_dispatch events", () => {
  const runs = [
    { event: "workflow_dispatch", created_at: "2026-09-07T08:00:00Z" },
    { event: "workflow_dispatch", created_at: "2026-09-07T02:00:00Z" },
    { event: "workflow_dispatch", created_at: "2026-09-06T02:00:00Z" }, // >24h
    { event: "schedule", created_at: "2026-09-07T06:00:00Z" },
  ];
  assert.equal(dispatchCountLast24h(runs, at("2026-09-07T09:37:00Z")), 2);
});

test("watchdog dispatches one backup and notifies for a missing slot", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method || "GET" });
    if (String(url).includes("/runs?")) {
      return new Response(JSON.stringify({ workflow_runs: [] }), { status: 200 });
    }
    return new Response(null, { status: 204 });
  };
  try {
    const log = await checkAndRecover({ GITHUB_TOKEN: "t" }, at("2026-09-07T09:37:00Z"));
    assert.equal(log.dispatched, true);
    const dispatches = calls.filter((call) => call.method === "POST");
    assert.equal(dispatches.length, 2); // crawl 복구 1 + notify 1
    assert.ok(dispatches[0].url.includes("/workflows/crawl.yml/dispatches"));
    assert.ok(dispatches[1].url.includes("/workflows/notify.yml/dispatches"));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("watchdog stays quiet when the cap is exhausted", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  const now = at("2026-09-07T09:37:00Z");
  // 첫 실패는 현재 슬롯(09:00~) 안, 나머지는 지난 24시간 내 — 합계 4회로 상한 도달.
  const runs = [
    { event: "workflow_dispatch", status: "completed", conclusion: "failure",
      created_at: "2026-09-07T09:10:00Z" },
    ...[1, 2, 3].map((i) => ({
      event: "workflow_dispatch", status: "completed", conclusion: "failure",
      created_at: new Date(now.getTime() - i * 3 * 3_600_000).toISOString(),
    })),
  ];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method || "GET" });
    return new Response(JSON.stringify({ workflow_runs: runs }), { status: 200 });
  };
  try {
    const log = await checkAndRecover({ GITHUB_TOKEN: "t" }, now);
    assert.equal(log.dispatched, false);
    assert.equal(log.watchdog_state, "workflow_failed_cap_exceeded");
    assert.equal(calls.filter((call) => call.method === "POST").length, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
