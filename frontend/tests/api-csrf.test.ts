import { test } from "node:test";
import assert from "node:assert/strict";
import { api, post } from "../src/api.ts";

test("a second tab's login rotates the cookie without leaving this tab's write token stale", async () => {
  const previousFetch = globalThis.fetch;
  const previousDocument = globalThis.document;
  const requests: Headers[] = [];
  globalThis.document = { cookie: "csrftoken=first-secret" } as Document;
  globalThis.fetch = async (_input, init) => {
    requests.push(new Headers(init?.headers));
    return Response.json(requests.length === 1 ? { csrf_token: "first-masked-token" } : { ok: true });
  };
  try {
    await api("/auth/session");
    globalThis.document.cookie = "csrftoken=rotated-secret";
    await post("/tickets/10/read", { through_event: 1 });
    assert.equal(requests[1].get("X-CSRFToken"), "rotated-secret");
  } finally {
    globalThis.fetch = previousFetch;
    globalThis.document = previousDocument;
  }
});
