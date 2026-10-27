let csrf = "";
function currentCsrfToken() {
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("csrftoken="));
  return cookie ? decodeURIComponent(cookie.slice("csrftoken=".length)) : csrf;
}
export class ApiError extends Error {
  public status: number;
  public data: Record<string, unknown>;
  constructor(
    message: string,
    status: number,
    data: Record<string, unknown> = {},
  ) {
    super(message);
    this.status = status;
    this.data = data;
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET")
    headers.set("X-CSRFToken", currentCsrfToken());
  let response: Response;
  try {
    response = await fetch("/api" + path, {
      ...options,
      credentials: "same-origin",
      headers,
    });
  } catch {
    throw new ApiError("连接失败，请检查网络。你填写的内容仍在当前页面。", 0);
  }
  const data = await response.json().catch(() => ({
    detail:
      response.status === 403
        ? "请求被拒绝，请确认登录状态后重试。"
        : "服务器暂时无法处理请求。",
  }));
  if (!response.ok) {
    const errors = data.fields
      ? Object.entries(data.fields)
          .map(
            ([k, v]) => `${k}: ${Array.isArray(v) ? v.join(" ") : String(v)}`,
          )
          .join("；")
      : "";
    throw new ApiError(
      (Array.isArray(data.detail)
        ? data.detail.join(" ")
        : data.detail || "请求失败。") + (errors ? " " + errors : ""),
      response.status,
      data,
    );
  }
  if (data.csrf_token) csrf = data.csrf_token;
  return data as T;
}
export const post = <T>(path: string, data: unknown = {}) =>
  api<T>(path, {
    method: "POST",
    body: data instanceof FormData ? data : JSON.stringify(data),
  });
export const patch = <T>(path: string, data: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(data) });
export async function download(path: string, fallback: string) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(
      data.detail || "下载失败，请检查权限或登录状态。",
      response.status,
    );
  }
  const blob = await response.blob(),
    url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = fallback;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
