/** In-page selections update URL queries without jumping to the top. */
export function navigationScroll(
  to: { path: string; query?: { scope?: unknown } },
  from: { path: string; query?: { scope?: unknown } },
  savedPosition: { left: number; top: number } | null,
) {
  if (savedPosition) return savedPosition;
  if (to.path === "/calendar" && from.path === "/calendar") return false;
  if (
    to.path === "/tickets" &&
    from.path === "/tickets" &&
    (to.query?.scope || "") === (from.query?.scope || "")
  )
    return false;
  return { top: 0 };
}
