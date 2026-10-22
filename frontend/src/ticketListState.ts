import type { LocationQuery } from "vue-router";

export function readTicketListState(query: LocationQuery) {
  const text = (key: string) => {
    const value = query[key];
    return (Array.isArray(value) ? value[0] : value) || "";
  };
  const page = Number(text("page"));
  const scope = ["attention", "archive"].includes(text("scope"))
    ? text("scope")
    : "";
  const status =
    scope === "archive" || (scope === "attention" && text("status") === "done")
      ? ""
      : text("status");
  const schedule =
    scope !== "archive" &&
    ["unassessed", "today", "overdue"].includes(text("schedule"))
      ? text("schedule")
      : "";
  const removedHiddenFilter =
    status !== text("status") || schedule !== text("schedule");
  return {
    scope,
    q: text("q"),
    module: text("module"),
    status,
    priority: text("priority"),
    owner: text("owner"),
    schedule,
    sort: ["estimate_asc", "estimate_desc"].includes(text("sort"))
      ? text("sort")
      : "",
    // Retire the overlapping active tab; old links must not keep an invisible filter.
    tab: text("tab") === "unread" ? "unread" : "all",
    page:
      !removedHiddenFilter && Number.isSafeInteger(page) && page > 0 ? page : 1,
    extra: text("extra") === "1",
  };
}

export type TicketListState = ReturnType<typeof readTicketListState>;

/** Carry only list state to details; omit defaults from the URL. */
export function ticketListQuery(state: TicketListState) {
  const query: Record<string, string> = {};
  for (const key of [
    "scope",
    "q",
    "module",
    "status",
    "priority",
    "owner",
    "schedule",
    "sort",
  ] as const) {
    if (state[key]) query[key] = state[key];
  }
  if (state.tab !== "all") query.tab = state.tab;
  if (state.page !== 1) query.page = String(state.page);
  if (state.extra) query.extra = "1";
  return query;
}
