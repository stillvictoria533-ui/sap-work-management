import { test } from "node:test";
import assert from "node:assert/strict";
import { mountPage, flush, deferred } from "./page-harness.ts";
import { ApiError } from "../src/api.ts";

test("a delayed successful creation after leaving the page does not navigate away from the current page", async (t) => {
  const previousWindow = globalThis.window;
  globalThis.window = { confirm: () => true } as any;
  t.after(() => {
    globalThis.window = previousWindow;
  });
  const response = deferred<any>();
  let statsRefreshes = 0;
  const page = await mountPage(
    "NewTicket",
    {
      "../api": { ApiError, post: async () => response.promise },
      "../state": {
        refreshStats: async () => {
          statsRefreshes++;
        },
      },
    },
    "/tickets/new",
  );
  t.after(page.unmount);
  const s = page.state();
  s.form.title = "Submitted while active";
  s.form.description = "A late response";
  const pending = s.submit();
  await page.router.push("/tickets");
  await flush();
  response.resolve({ id: 42 });
  await pending;
  assert.equal(page.router.currentRoute.value.path, "/tickets");
  assert.equal(statsRefreshes, 1);
});

test("a creation completed on the active page still opens the new ticket", async (t) => {
  const page = await mountPage(
    "NewTicket",
    {
      "../api": { ApiError, post: async () => ({ id: 42 }) },
      "../state": { refreshStats: async () => {} },
    },
    "/tickets/new",
  );
  t.after(page.unmount);
  const s = page.state();
  s.form.title = "Active creation";
  await s.submit();
  assert.equal(page.router.currentRoute.value.path, "/tickets/42");
});

test("a lost create response reuses its key; a content conflict keeps the edited draft and original-ticket link", async (t) => {
  const payloads: FormData[] = [];
  const page = await mountPage(
    "NewTicket",
    {
      "../api": {
        ApiError,
        post: async (_path: string, data: FormData) => {
          payloads.push(data);
          if (payloads.length === 1) throw new ApiError("connection lost", 0);
          throw new ApiError("此提交已创建，修改未保存", 409, {
            ticket_id: "42",
          });
        },
      },
      "../state": { refreshStats: async () => {} },
    },
    "/tickets/new",
  );
  t.after(page.unmount);
  const s = page.state();
  Object.assign(s.form, {
    title: "Original",
    description: "Original description",
    system: "DS4",
  });
  await s.submit();
  s.form.description = "Edited after response was lost";
  await s.submit();
  assert.equal(payloads[0].get("request_key"), payloads[1].get("request_key"));
  assert.equal(s.form.description, "Edited after response was lost");
  assert.equal(s.existingTicket, 42);
  assert.equal(s.saved, false);
  assert.equal(page.router.currentRoute.value.path, "/tickets/new");
});

test("mark all read on unread page two reloads page one and clears the old rows", async (t) => {
  let read = false;
  const paths: string[] = [];
  const page = await mountPage(
    "Notifications",
    {
      "../api": {
        ApiError,
        post: async () => {
          read = true;
        },
        api: async (path: string) => {
          paths.push(path);
          return {
            count: read ? 0 : 21,
            results: read ? [] : [{ id: 21, read_at: null }],
          };
        },
      },
      "../state": { refreshStats: async () => {} },
    },
    "/notifications",
  );
  t.after(page.unmount);
  const s = page.state();
  s.unread = true;
  s.turn(2);
  await flush();
  assert.equal(s.page, 2);
  await s.markAll();
  assert.equal(paths.at(-1), "/notifications?page=1&unread=true");
  assert.equal(s.page, 1);
  assert.equal(s.count, 0);
  assert.deepEqual(s.items, []);
  assert.equal(s.error, "");
});

test("a result set shrinking on another client recovers from an invalid page", async (t) => {
  const page = await mountPage(
    "Notifications",
    {
      "../api": {
        ApiError,
        api: async (path: string) => {
          if (path.includes("page=2")) throw new ApiError("Invalid page", 404);
          return { count: 0, results: [] };
        },
      },
      "../state": { refreshStats: async () => {} },
    },
    "/notifications",
  );
  t.after(page.unmount);
  const s = page.state();
  s.turn(2);
  await flush();
  assert.equal(s.page, 1);
  assert.equal(s.error, "");
  assert.equal(s.busy, false);
});
