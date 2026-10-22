type Schedule = {
  status: string;
  estimated_completion_date: string | null;
  estimate_needs_review: boolean;
};

// ISO dates are calendar days; UTC arithmetic avoids browser timezone / DST drift.
export function dateDifference(later: string, earlier: string) {
  return Math.round(
    (Date.parse(later + "T00:00:00Z") - Date.parse(earlier + "T00:00:00Z")) /
      86400000,
  );
}

export function estimateHint(ticket: Schedule, currentDate: string) {
  const value = ticket.estimated_completion_date;
  if (!value || !["pending", "in-progress"].includes(ticket.status))
    return { label: "", tone: "" };
  if (value === currentDate) return { label: "今日到期", tone: "today" };
  if (ticket.status === "in-progress" && value < currentDate)
    return {
      label: `已逾期 ${dateDifference(currentDate, value)} 天`,
      tone: "overdue",
    };
  return { label: "", tone: "" };
}
