import assert from "node:assert/strict";
import { test } from "node:test";
import { dateDifference, estimateHint } from "../src/schedule.ts";

test("development overdue stops at acceptance and resumes on return", () => {
  const ticket = {
    status: "in-progress",
    estimated_completion_date: "2026-10-20",
    estimate_needs_review: false,
  };
  assert.deepEqual(estimateHint(ticket, "2026-10-21"), {
    label: "已逾期 1 天",
    tone: "overdue",
  });
  for (const status of ["waiting", "done", "pending"])
    assert.equal(estimateHint({ ...ticket, status }, "2026-10-21").label, "");
  assert.equal(
    estimateHint({ ...ticket, estimate_needs_review: true }, "2026-10-21")
      .label,
    "已逾期 1 天",
  );
});

test("today and missing dates are separate from overdue", () => {
  const ticket = {
    status: "in-progress",
    estimated_completion_date: "2026-10-21",
    estimate_needs_review: false,
  };
  assert.equal(estimateHint(ticket, "2026-10-21").label, "今日到期");
  assert.equal(estimateHint(ticket, "2026-10-20").label, "");
  assert.equal(
    estimateHint({ ...ticket, estimated_completion_date: null }, "2026-10-21")
      .label,
    "",
  );
  assert.equal(
    estimateHint({ ...ticket, status: "waiting" }, "2026-10-21").label,
    "",
  );
});

test("date arithmetic handles leap years and calendar boundaries", () => {
  assert.equal(dateDifference("2024-03-01", "2024-02-28"), 2);
  assert.equal(dateDifference("2027-01-01", "2026-12-31"), 1);
  assert.equal(dateDifference("2026-10-23", "2026-10-26"), -3);
});
