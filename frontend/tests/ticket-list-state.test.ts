import assert from "node:assert/strict";
import { test } from "node:test";
import { createMemoryHistory, createRouter } from "vue-router";
import {
  readTicketListState,
  ticketListQuery,
} from "../src/ticketListState.ts";

test("list state survives details, return links and browser history", async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/tickets", component: {} },
      { path: "/tickets/:id", component: {} },
    ],
  });
  const expected = {
    scope: "attention",
    q: "采购 & 库存+中文",
    module: "MM",
    status: "pending",
    priority: "urgent",
    owner: "12",
    schedule: "overdue",
    sort: "estimate_asc",
    tab: "unread",
    page: 3,
    extra: true,
  };
  await router.push({ path: "/tickets", query: ticketListQuery(expected) });
  const original = router.currentRoute.value.fullPath;
  await router.push({
    path: "/tickets/6",
    query: ticketListQuery(
      readTicketListState(router.currentRoute.value.query),
    ),
  });
  assert.deepEqual(
    readTicketListState(router.currentRoute.value.query),
    expected,
  );
  await router.push({
    path: "/tickets",
    query: ticketListQuery(
      readTicketListState(router.currentRoute.value.query),
    ),
  });
  assert.equal(router.currentRoute.value.fullPath, original);
  const back = new Promise<void>((resolve) => {
    const remove = router.afterEach(() => {
      remove();
      resolve();
    });
  });
  router.back();
  await back;
  assert.equal(router.currentRoute.value.path, "/tickets/6");
  assert.deepEqual(
    readTicketListState(router.currentRoute.value.query),
    expected,
  );
});

test("unfiltered and legacy detail links return to their original section", () => {
  for (const scope of ["", "attention", "archive"]) {
    const state = readTicketListState(scope ? { scope } : {});
    assert.equal(state.page, 1);
    assert.equal(state.tab, "all");
    assert.equal(state.q, "");
    assert.deepEqual(ticketListQuery(state), scope ? { scope } : {});
  }
});

test("invalid page values fall back to page one and unrelated queries are not carried", () => {
  for (const page of [
    null,
    "0",
    "-2",
    "2.5",
    "nope",
    "Infinity",
    "9007199254740992",
  ]) {
    assert.equal(readTicketListState({ page }).page, 1);
  }
  assert.deepEqual(
    ticketListQuery(
      readTicketListState({
        next: "/profile",
        tab: "invalid",
        scope: "invalid",
      }),
    ),
    {},
  );
  assert.equal(
    readTicketListState({ q: ["first", "second"], page: ["3", "4"] }).page,
    3,
  );
});

test("retired active links keep the scope but never apply a hidden tab filter", () => {
  for (const scope of ["", "attention", "archive"]) {
    const state = readTicketListState({ scope, tab: "active", module: "MM" });
    assert.equal(state.tab, "all");
    assert.deepEqual(ticketListQuery(state), {
      ...(scope ? { scope } : {}),
      module: "MM",
    });
  }
  const unread = readTicketListState({ scope: "attention", tab: "unread" });
  assert.equal(unread.tab, "unread");
  assert.deepEqual(ticketListQuery(unread), {
    scope: "attention",
    tab: "unread",
  });
});

test("archive links drop hidden status and schedule filters and reset pagination", () => {
  const state = readTicketListState({
    scope: "archive",
    status: "pending",
    schedule: "overdue",
    page: "3",
    q: "交付",
    module: "MM",
    priority: "high",
    owner: "12",
    tab: "unread",
    sort: "estimate_asc",
  });
  assert.equal(state.status, "");
  assert.equal(state.schedule, "");
  assert.equal(state.page, 1);
  assert.deepEqual(ticketListQuery(state), {
    scope: "archive",
    q: "交付",
    module: "MM",
    priority: "high",
    owner: "12",
    tab: "unread",
    sort: "estimate_asc",
  });
});

test("attention links cannot retain completed-only filtering while all tickets can", () => {
  const attention = readTicketListState({
    scope: "attention",
    status: "done",
    page: "2",
  });
  assert.equal(attention.status, "");
  assert.equal(attention.page, 1);
  assert.deepEqual(ticketListQuery(attention), { scope: "attention" });
  for (const status of ["pending", "in-progress", "waiting"]) {
    assert.equal(
      readTicketListState({ scope: "attention", status }).status,
      status,
    );
  }
  const all = readTicketListState({ status: "done", page: "2" });
  assert.equal(all.status, "done");
  assert.equal(all.page, 2);
});
