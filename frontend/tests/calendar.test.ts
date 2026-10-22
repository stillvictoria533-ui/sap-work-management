import assert from "node:assert/strict";
import { test } from "node:test";
import { calendarDays, shiftMonth } from "../src/calendar.ts";

test("leap February includes the 29th and starts its grid on Monday", () => {
  const days = calendarDays("2024-02");
  assert.equal(days[0]?.date, "2024-01-29");
  assert.equal(days.at(-1)?.date, "2024-03-03");
  assert.equal(days.filter((day) => day.currentMonth).length, 29);
  assert.ok(days.some((day) => day.date === "2024-02-29" && day.currentMonth));
});

test("a Sunday-starting month shows six complete weeks without duplicated dates", () => {
  const days = calendarDays("2026-03");
  assert.equal(days.length, 42);
  assert.equal(days[0]?.date, "2026-02-23");
  assert.equal(days.at(-1)?.date, "2026-04-05");
  assert.equal(new Set(days.map((day) => day.date)).size, days.length);
  assert.equal(days.filter((day) => day.currentMonth).length, 31);
});

test("historical month navigation crosses years correctly", () => {
  assert.equal(shiftMonth("2026-01", -1), "2025-12");
  assert.equal(shiftMonth("2026-12", 1), "2027-01");
  assert.deepEqual(calendarDays("2026-13"), []);
});
