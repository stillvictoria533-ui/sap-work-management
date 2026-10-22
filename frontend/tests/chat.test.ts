import assert from "node:assert/strict";
import { test } from "node:test";
import {
  inlineParts,
  isActiveTurn,
  messageBlocks,
  replaceTurn,
  type ChatTurn,
} from "../src/chat.ts";

test("incremental snapshots replace a turn rather than duplicate it", () => {
  const turn = {
    id: "a",
    status: "running",
    output_text: "partial",
  } as ChatTurn;
  const result = replaceTurn([turn], {
    ...turn,
    status: "completed",
    output_text: "final",
  });
  assert.equal(result.length, 1);
  assert.equal(result[0]?.output_text, "final");
  assert.equal(isActiveTurn(result[0]), false);
  assert.equal(isActiveTurn(turn), true);
});

test("code fences including unfinished streams remain text, never executable HTML", () => {
  const blocks = messageBlocks(
    "分析\n```abap\nWRITE: `<script>alert(1)</script>`.",
  );
  assert.equal(blocks.length, 2);
  assert.equal(blocks[1]?.kind, "code");
  assert.equal(blocks[1]?.language, "abap");
  assert.equal(blocks[1]?.content, "WRITE: `<script>alert(1)</script>`.");
  assert.deepEqual(inlineParts("**注意** `<img src=x>`"), [
    { kind: "bold", text: "注意" },
    { kind: "text", text: " " },
    { kind: "code", text: "<img src=x>" },
  ]);
});
