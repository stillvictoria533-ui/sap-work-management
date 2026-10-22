import { ref, reactive } from "vue";
import { api, post, ApiError } from "./api";
import type { User } from "./types";
export const user = ref<User | null>(null);
export const sessionError = ref("");
export const stats = reactive({
  total: 0,
  active: 0,
  unread_tickets: 0,
  attention: 0,
  notifications: 0,
  expedite_pending: 0,
});
export async function refreshSession() {
  const data = await api<{ user: User | null }>("/auth/session");
  user.value = data.user;
  sessionError.value = "";
  return data.user;
}
export const ready = refreshSession().catch((e) => {
  sessionError.value = e.message;
});
export async function refreshStats() {
  if (user.value && !user.value.must_change_password)
    Object.assign(stats, await api("/stats"));
}
export async function signIn(email: string, password: string) {
  const data = await post<{ user: User }>("/auth/login", { email, password });
  user.value = data.user;
  await refreshStats();
}
export async function signOut() {
  try {
    await post("/auth/logout");
  } catch (error) {
    if (!(error instanceof ApiError && [401, 403].includes(error.status)))
      throw error;
    // An expired session is already signed out. A live session may only need
    // a fresh CSRF token before retrying logout once.
    if (await refreshSession()) await post("/auth/logout");
  }
  clearLocalSession();
}
export function clearLocalSession() {
  user.value = null;
  Object.assign(stats, {
    total: 0,
    active: 0,
    unread_tickets: 0,
    attention: 0,
    notifications: 0,
    expedite_pending: 0,
  });
}
