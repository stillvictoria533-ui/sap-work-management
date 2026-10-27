import { test } from "node:test";
import assert from "node:assert/strict";
import { mountPage } from "./page-harness.ts";

function healthy() {
  return {
    enabled: true,
    node: {
      status: "online",
      last_seen_at: "2026-10-27T02:00:00Z",
      last_scan_at: "2026-10-27T02:00:00Z",
      capabilities: {
        model_cli: true,
        sap_tools: true,
        production_identity: true,
        proxy: true,
      },
    },
    queue: {
      queued: 0,
      running: 0,
      retry_wait: 0,
      manual_required: 0,
      waiting_for_information: 0,
      needs_acceptance: 0,
      inactive_submitters: 0,
      oldest_wait_seconds: null,
    },
    failures: { consecutive: 0, by_reason: {}, last_failure_at: null },
    last_reply_at: null,
    alerts: {
      offline: false,
      readiness: false,
      backlog: false,
      failure_streak: false,
      manual: false,
    },
    thresholds: {
      heartbeat_seconds: 300,
      backlog_seconds: 600,
      failure_count: 3,
    },
    storage: {
      count: 0,
      used_bytes: 0,
      largest_ticket_bytes: 0,
      largest_owner_bytes: 0,
      free_bytes: 10_000,
      limits: {
        ticket_bytes: 100,
        owner_bytes: 500,
        global_bytes: 1000,
        min_free_bytes: 100,
      },
    },
    backup: {
      status: "healthy",
      last_success_at: "2026-10-27T01:00:00Z",
      last_drill_at: "2026-10-26T01:00:00Z",
      last_drill_ok: true,
      reported_at: "2026-10-27T02:00:00Z",
    },
  };
}

test("health highlights actionable failures and distinguishes unknown backup status", async (t) => {
  const value = healthy();
  const page = await mountPage(
    "Operations",
    { "../api": { api: async () => value } },
    "/operations",
  );
  t.after(page.unmount);
  const state = page.state();
  assert.equal(state.overall, "运行正常");
  state.health = {
    ...value,
    node: { ...value.node, status: "offline" },
    alerts: { ...value.alerts, offline: true },
  };
  assert.equal(state.overall, "需要关注");
  state.health = { ...value, backup: { ...value.backup, status: "unknown" } };
  assert.equal(state.overall, "部分状态待确认");
  state.health = { ...value, storage: { ...value.storage, used_bytes: 1000 } };
  assert.equal(state.overall, "需要关注");
  state.health = {
    ...value,
    backup: { ...value.backup, last_drill_ok: false },
  };
  assert.equal(state.overall, "需要关注");
  state.health = { ...value, backup: { ...value.backup, last_drill_ok: null } };
  assert.equal(state.overall, "部分状态待确认");
});

test("a failed refresh labels previously loaded data as stale", async (t) => {
  let calls = 0;
  const page = await mountPage(
    "Operations",
    {
      "../api": {
        api: async () => {
          if (++calls > 1) throw new Error("网络中断");
          return healthy();
        },
      },
    },
    "/operations",
  );
  t.after(page.unmount);
  const state = page.state();
  await state.load();
  assert.equal(state.error, "网络中断");
  assert.ok(state.health);
  assert.ok(state.refreshedAt);
});
