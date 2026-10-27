import { test } from "node:test";
import assert from "node:assert/strict";
import { ref } from "vue";
import { deferred, flush, mountPage } from "./page-harness.ts";

function ticket(id: number, version = 1) {
  return {
    id,
    version,
    title: `Ticket ${id}`,
    status: "in-progress",
    events: [],
    estimated_completion_date: null,
  };
}
async function setup(
  t: any,
  post = async (..._args: any[]) => ({}),
  api = async (path: string) => ticket(Number(path.split("/")[2])),
) {
  const previousWindow = globalThis.window;
  globalThis.window = { confirm: () => true } as any;
  t.after(() => {
    globalThis.window = previousWindow;
  });
  const page = await mountPage("TicketDetail", {
    "../api": {
      api,
      post,
      download: async () => {},
      ApiError: class ApiError extends Error {},
    },
    "../state": { user: ref({ role: "admin" }), refreshStats: async () => {} },
  });
  t.after(page.unmount);
  return page;
}

test("switching ticket IDs asks about the draft and clears every editor only after confirmation", async (t) => {
  const page = await setup(t);
  const s = page.state();
  s.body = "A-only financial analysis";
  s.files = [new File(["A-only"], "A-only.txt")];
  s.editingSchedule = true;
  s.estimateDate = "2027-01-01";
  s.estimateReason = "A schedule";
  s.selectedAction = "complete";
  s.reason = "A status";
  s.expediteDirty = true;
  let confirmations = 0;
  window.confirm = () => {
    confirmations++;
    return false;
  };
  await page.router.push("/tickets/2");
  assert.equal(page.router.currentRoute.value.params.id, "1");
  assert.equal(s.body, "A-only financial analysis");
  window.confirm = () => {
    confirmations++;
    return true;
  };
  await page.router.push("/tickets/2");
  await flush();
  assert.equal(confirmations, 2);
  assert.equal(s.ticket.id, 2);
  for (const key of [
    "body",
    "reason",
    "estimateDate",
    "estimateReason",
    "selectedAction",
  ])
    assert.equal(s[key], "", key);
  assert.deepEqual(s.files, []);
  assert.equal(s.editingSchedule, false);
  assert.equal(s.expediteDirty, false);
});

test("edits and new attachments made during upload survive the old reply's success", async (t) => {
  const reply = deferred<any>();
  const calls: any[] = [];
  const page = await setup(t, async (...args: any[]) => {
    calls.push(args);
    return reply.promise;
  });
  const s = page.state();
  const oldFile = new File(["old"], "old.txt"),
    newFile = new File(["new"], "new.txt");
  s.body = "submitted";
  s.files = [oldFile];
  const pending = s.submit();
  s.body = "next reply";
  s.files = [oldFile, newFile];
  reply.resolve(ticket(1, 2));
  await pending;
  assert.equal(calls[0][0], "/tickets/1/reply");
  assert.equal(calls[0][1].get("body"), "submitted");
  assert.equal(s.body, "next reply");
  assert.deepEqual(
    s.files.map((f: File) => f.name),
    ["new.txt"],
  );
});

test("a late reply for A cannot replace ticket B or clear B's new draft", async (t) => {
  const reply = deferred<any>();
  const page = await setup(t, async () => reply.promise);
  const s = page.state();
  s.body = "A reply";
  const pending = s.submit();
  await page.router.push("/tickets/2");
  await flush();
  s.body = "B draft";
  reply.resolve(ticket(1, 2));
  await pending;
  assert.equal(s.ticket.id, 2);
  assert.equal(s.body, "B draft");
  assert.equal(s.success, "");
});

test("background updates are announced and refreshing retains every unsent editor", async (t) => {
  let version = 1;
  const page = await setup(t, undefined, async (path: string) =>
    ticket(Number(path.split("/")[2]), version),
  );
  const s = page.state();
  s.body = "Draft";
  s.files = [new File(["draft"], "draft.txt")];
  s.selectedAction = "complete";
  s.reason = "Not yet submitted";
  s.startEstimate = "2027-01-01";
  version = 2;
  await s.checkForUpdates();
  assert.equal(s.ticket.version, 1);
  assert.equal(s.newVersion, 2);
  await s.load();
  assert.equal(s.ticket.version, 2);
  assert.equal(s.newVersion, 0);
  assert.equal(s.body, "Draft");
  assert.equal(s.files[0].name, "draft.txt");
  assert.equal(s.selectedAction, "complete");
  assert.equal(s.reason, "Not yet submitted");
  assert.equal(s.startEstimate, "2027-01-01");
});

for (const operation of ["changeStatus", "saveSchedule"]) {
  test(`a late ${operation} response cannot overwrite a different ticket`, async (t) => {
    const response = deferred<any>();
    const page = await setup(t, async () => response.promise);
    const s = page.state();
    s.selectedAction = "complete";
    s.reason = "A reason";
    s.estimateDate = "2027-01-01";
    const pending = s[operation]();
    await page.router.push("/tickets/2");
    await flush();
    s.reason = "B reason";
    response.resolve(ticket(1, 2));
    await pending;
    assert.equal(s.ticket.id, 2);
    assert.equal(s.reason, "B reason");
    assert.equal(s.success, "");
  });
}

test("a failed GET from a previous ticket cannot display its error on the current ticket", async (t) => {
  const old = deferred<any>();
  let requests = 0;
  const page = await setup(t, undefined, async (path: string) => {
    requests++;
    return requests === 2 ? old.promise : ticket(Number(path.split("/")[2]));
  });
  const s = page.state();
  const pending = s.load();
  await page.router.push("/tickets/2");
  await flush();
  old.reject(new Error("A failed to load"));
  await pending;
  assert.equal(s.ticket.id, 2);
  assert.equal(s.error, "");
});

test("a delayed response cannot roll back a newer accepted version of the same ticket", async (t) => {
  const response = deferred<any>();
  const page = await setup(t, async () => response.promise);
  const s = page.state();
  s.body = "Sent before another update";
  const pending = s.submit();
  s.acceptUpdate(ticket(1, 3));
  response.resolve(ticket(1, 2));
  await pending;
  assert.equal(s.ticket.version, 3);
  s.acceptUpdate(ticket(1, 2));
  assert.equal(s.ticket.version, 3);
});

for (const [initial, latest] of [
  [null, "2026-12-20"],
  ["2026-12-10", "2026-12-20"],
  ["2026-12-10", null],
]) {
  test(`refreshing an untouched acceptance date ${initial} -> ${latest} sends no date change`, async (t) => {
    let current = {
      ...ticket(1),
      status: "pending",
      estimated_completion_date: initial,
    };
    const requests: any[] = [];
    const page = await setup(
      t,
      async (_path: string, data: any) => {
        requests.push(data);
        return { ...current, status: "in-progress", version: 3 };
      },
      async () => ({ ...current }),
    );
    const s = page.state();
    s.selectedAction = "start";
    s.startEstimate = initial || "";
    s.reason = "按已有计划受理";
    current = {
      ...current,
      version: 2,
      estimated_completion_date: latest,
    };
    await s.load();
    await s.changeStatus();
    assert.equal(requests.length, 1);
    assert.equal(requests[0].version, 2);
    assert.equal(
      Object.hasOwn(requests[0], "estimated_completion_date"),
      false,
    );
    assert.equal(s.ticket.estimated_completion_date, latest);
  });
}

test("an edited acceptance date needs explicit resolution when another administrator changes the plan", async (t) => {
  let current = { ...ticket(1), status: "pending" };
  const requests: any[] = [];
  const page = await setup(
    t,
    async (_path: string, data: any) => {
      requests.push(data);
      return {
        ...current,
        status: "in-progress",
        version: 3,
        estimated_completion_date: data.estimated_completion_date,
      };
    },
    async () => ({ ...current }),
  );
  const s = page.state();
  s.selectedAction = "start";
  s.startEstimate = "2026-12-25";
  s.reason = "保留我的调整原因";
  current = {
    ...current,
    version: 2,
    estimated_completion_date: "2026-12-20" as any,
  };
  await s.load();
  await s.changeStatus();
  assert.equal(requests.length, 0);
  assert.equal(s.startEstimate, "2026-12-25");
  assert.equal(s.reason, "保留我的调整原因");
  s.resolveEstimateConflict("start", true);
  await s.changeStatus();
  assert.equal(requests[0].estimated_completion_date, "2026-12-25");
  assert.equal(requests[0].version, 2);
});

test("an untouched schedule editor follows refresh; an edited one cannot overwrite a changed plan without confirmation", async (t) => {
  let current = {
    ...ticket(1),
    estimated_completion_date: "2026-12-10" as any,
  };
  const requests: any[] = [];
  const page = await setup(
    t,
    async (_path: string, data: any) => {
      requests.push(data);
      return current;
    },
    async () => ({ ...current }),
  );
  const s = page.state();
  s.editSchedule();
  s.estimateReason = "说明保留";
  current = { ...current, version: 2, estimated_completion_date: "2026-12-20" };
  await s.load();
  assert.equal(s.estimateDate, "2026-12-20");
  assert.equal(s.estimateReason, "说明保留");
  s.estimateDate = "2026-12-25";
  current = { ...current, version: 3, estimated_completion_date: "2026-12-22" };
  await s.load();
  await s.saveSchedule();
  assert.equal(requests.length, 0);
  assert.equal(s.estimateDate, "2026-12-25");
  s.resolveEstimateConflict("schedule", false);
  assert.equal(s.estimateDate, "2026-12-22");
  assert.equal(s.estimateConflict, false);
});

test("explicitly clearing a changed acceptance date remains possible after confirmation", async (t) => {
  let current = {
    ...ticket(1),
    status: "pending",
    estimated_completion_date: "2026-12-10" as any,
  };
  const requests: any[] = [];
  const page = await setup(
    t,
    async (_path: string, data: any) => {
      requests.push(data);
      return {
        ...current,
        status: "in-progress",
        version: 3,
        estimated_completion_date: data.estimated_completion_date,
      };
    },
    async () => ({ ...current }),
  );
  const s = page.state();
  s.selectAction("start");
  s.startEstimate = "";
  s.reason = "明确清空已有排期";
  current = { ...current, version: 2, estimated_completion_date: "2026-12-20" };
  await s.load();
  await s.changeStatus();
  assert.equal(requests.length, 0);
  s.resolveEstimateConflict("start", true);
  await s.changeStatus();
  assert.equal(requests[0].estimated_completion_date, null);
});

test("manual AI failure is visible and clears when the round is no longer blocked", async (t) => {
  const page = await setup(t, undefined, async () => ({
    ...ticket(1),
    ai_followup: {
      enabled: true,
      state: "manual_required",
      message: "AI 分析已达到 45 分钟上限；最近受阻：身份或范围校验受阻。",
    },
  }));
  const s = page.state();
  assert.match(s.aiFollowupMessage, /最近受阻/);
  s.body = "keep this draft";
  s.ticket = {
    ...s.ticket,
    ai_followup: { enabled: true, state: "running", message: "AI 正在分析" },
  };
  await flush();
  assert.equal(s.aiFollowupMessage, "");
  assert.equal(s.body, "keep this draft");
});
