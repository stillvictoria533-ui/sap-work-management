export interface User {
  id: number;
  name: string;
  email: string;
  role: "admin" | "consultant";
  is_active: boolean;
  must_change_password: boolean;
  dingtalk_user_id: string;
  dingtalk_mobile: string;
}
export interface Attachment {
  id: number;
  original_name: string;
  size: number;
  url: string;
}
export interface TicketEvent {
  id: number;
  actor_name: string;
  actor_role: string;
  is_ai: boolean;
  kind: string;
  body: string;
  from_status: string;
  to_status: string;
  created_at: string;
  attachments: Attachment[];
}
export interface TicketListItem {
  id: number;
  number: string;
  title: string;
  system: string;
  module: string;
  kind: string;
  priority: string;
  status: string;
  due_date: string | null;
  estimated_completion_date: string | null;
  estimate_needs_review: boolean;
  owner_name: string;
  owner_id: number;
  version: number;
  next_actor: string;
  created_at: string;
  updated_at: string;
  unread: boolean;
}
export interface Ticket extends TicketListItem {
  description: string;
  events?: TicketEvent[];
  expedite_requests?: ExpediteRequest[];
  ai_followup?: { enabled: boolean; state: string; message: string };
}
export interface Notice {
  id: number;
  ticket_id: number;
  number: string;
  title: string;
  actor: string;
  kind: string;
  body: string;
  read_at: string | null;
  created_at: string;
}
export interface Page<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}
export interface TicketPage extends Page<TicketListItem> {
  tab_counts: { total: number; active: number; unread_tickets: number };
}
export type CalendarTicket = Pick<
  TicketListItem,
  | "id"
  | "number"
  | "title"
  | "module"
  | "priority"
  | "status"
  | "owner_id"
  | "owner_name"
  | "created_at"
  | "estimated_completion_date"
  | "estimate_needs_review"
>;
export interface CalendarPage extends Page<CalendarTicket> {
  month: string;
  date: string;
  timezone: string;
  mode: "submitted" | "estimated";
  unassessed_count: number;
  month_count: number;
  days: { date: string; count: number; urgent_count: number }[];
}
export interface Delivery {
  id: number;
  channel: string;
  recipient_name: string;
  ticket_number: string;
  state: string;
  attempts: number;
  last_error: string;
  created_at: string;
  accepted_at: string | null;
}
export const statuses: Record<string, string> = {
  pending: "待受理",
  "in-progress": "处理中",
  waiting: "待验收",
  done: "已完成",
};
export const priorities: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
  urgent: "紧急",
};
export const modules = ["FI", "CO", "MM", "SD", "PP", "HR", "其他"];
export const systems = [
  { id: "DS4", label: "DS4 / 110（开发）" },
  { id: "QS4", label: "QS4 / 120（测试）" },
  { id: "PS4", label: "PS4 / 100（生产）" },
];
export const systemLabel = (value: string) =>
  systems.find((system) => system.id === value)?.label || "未指定（历史工单）";
export const kinds = ["配置", "开发", "报表", "接口", "问题", "其他"];
// Keep historical status names readable without exposing them as selectable states.
export const eventStatus = (value: string) =>
  value === "closed" ? "已关闭（历史）" : statuses[value] || value;
export const dateTime = (s: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(s));
export const today = () =>
  new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
export const fileSize = (n: number) =>
  n < 1048576 ? `${Math.ceil(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`;

export interface ExpediteRequest {
  id: number;
  ticket_id: number;
  ticket_number: string;
  ticket_title: string;
  ticket_version: number;
  requester_name: string;
  requested_date: string;
  current_estimated_completion_date: string | null;
  reason: string;
  status: "pending" | "approved" | "rejected" | "cancelled";
  version: number;
  created_at: string;
  handled_at: string | null;
  handled_by_name: string | null;
  decision_note: string;
}
export interface QueueTicket extends Ticket {
  pending_expedite: ExpediteRequest | null;
}
export interface BatchChange {
  ticket_id: number;
  version: number;
  estimated_completion_date: string | null;
}
export interface SchedulePreview {
  changes: {
    ticket_id: number;
    number: string;
    title: string;
    owner_id: number;
    owner_name: string;
    version: number;
    previous_date: string | null;
    new_date: string | null;
    kind: "scheduled" | "cleared" | "advanced" | "delayed";
  }[];
  changed_count: number;
  advanced_count: number;
  delayed_count: number;
  scheduled_count: number;
  cleared_count: number;
  recipient_count: number;
  reason: string;
  expedite_request_id: number | null;
  preview_token: string;
}
export interface ScheduleBatchResult extends Omit<
  SchedulePreview,
  "preview_token"
> {
  batch_id: string;
}
