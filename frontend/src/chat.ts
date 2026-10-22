export type TurnStatus =
  "queued" | "running" | "completed" | "blocked" | "failed" | "cancelled";
export interface ChatTurn {
  id: string;
  input_text: string;
  output_text: string;
  status: TurnStatus;
  error_code: string;
  error_message: string;
  attempt: number;
  model: string;
  created_at: string;
  context_meta: {
    sap_connected?: boolean;
    sap_evidence?: {
      query_id: string;
      system: string;
      object: string;
      collected_at: string;
    };
    system?: string;
    history_truncated?: boolean;
    ticket?: { number: string; version: number };
  };
}
export interface ChatConversation {
  id: string;
  title: string;
  system: string;
  ticket: { id: number; number: string; title: string } | null;
  updated_at: string;
  created_at: string;
  turns?: ChatTurn[];
}
export interface ChatStatus {
  provider: {
    name: string;
    configured: boolean;
    worker_online: boolean;
    ready: boolean;
    code: string;
    message: string;
    model: string;
  };
  sap: {
    connected: false;
    status: string;
    systems: { id: string; label: string }[];
  };
  max_message_length: number;
}
export const isActiveTurn = (turn?: ChatTurn) =>
  !!turn && ["queued", "running"].includes(turn.status);
export function replaceTurn(turns: ChatTurn[], incoming: ChatTurn): ChatTurn[] {
  const index = turns.findIndex((t) => t.id === incoming.id);
  if (index < 0) return [...turns, incoming];
  return turns.map((t, i) => (i === index ? incoming : t));
}
export type TextBlock = {
  kind: "text" | "code";
  content: string;
  language: string;
};
export function messageBlocks(value: string): TextBlock[] {
  const result: TextBlock[] = [];
  let code = false,
    language = "",
    lines: string[] = [];
  const flush = () => {
    if (lines.length)
      result.push({
        kind: code ? "code" : "text",
        language,
        content: lines.join("\n"),
      });
    lines = [];
  };
  for (const line of value.split("\n")) {
    if (/^\s*```/.test(line)) {
      flush();
      code = !code;
      language = code
        ? line
            .trim()
            .slice(3)
            .trim()
            .replace(/[^a-zA-Z0-9_+-]/g, "")
            .slice(0, 24)
        : "";
    } else lines.push(line);
  }
  flush();
  return result;
}
export function inlineParts(value: string) {
  return value
    .split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g)
    .filter(Boolean)
    .map((text) =>
      text.startsWith("**") && text.endsWith("**")
        ? { kind: "bold", text: text.slice(2, -2) }
        : text.startsWith("`") && text.endsWith("`")
          ? { kind: "code", text: text.slice(1, -1) }
          : { kind: "text", text },
    );
}
