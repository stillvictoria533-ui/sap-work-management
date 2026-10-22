import assert from "node:assert/strict";
import { test } from "node:test";
import { navigationScroll } from "../src/scroll.ts";

test("calendar date, month and page queries preserve the current scroll position", () => {
  for (const query of [
    { date: "2026-10-12" },
    { month: "2025-12" },
    { page: "2" },
  ]) {
    const to = { path: "/calendar", query };
    assert.equal(navigationScroll(to, { path: "/calendar" }, null), false);
  }
});

test("browser history restores the saved position when returning to the calendar", () => {
  const saved = { left: 0, top: 260 };
  assert.deepEqual(
    navigationScroll({ path: "/calendar" }, { path: "/tickets/4" }, saved),
    saved,
  );
});

test("opening a different page starts at the top", () => {
  assert.deepEqual(
    navigationScroll({ path: "/tickets/4" }, { path: "/calendar" }, null),
    { top: 0 },
  );
  assert.deepEqual(
    navigationScroll({ path: "/calendar" }, { path: "/tickets" }, null),
    { top: 0 },
  );
});

test("editing ticket filters or page preserves scroll, but changing sections starts at the top", () => {
  const list = { path: "/tickets", query: { scope: "attention" } };
  for (const query of [
    { q: "库存" },
    { module: "MM" },
    { page: "3" },
    { extra: "1" },
  ]) {
    assert.equal(
      navigationScroll(
        { path: "/tickets", query: { ...query, scope: "attention" } },
        list,
        null,
      ),
      false,
    );
  }
  assert.deepEqual(
    navigationScroll(
      { path: "/tickets", query: { scope: "archive" } },
      list,
      null,
    ),
    { top: 0 },
  );
  assert.deepEqual(
    navigationScroll(list, { path: "/tickets/6" }, { left: 0, top: 400 }),
    { left: 0, top: 400 },
  );
});
