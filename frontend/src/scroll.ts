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

export type ScrollPosition = { left: number; top: number };

/** Restore after asynchronously loaded rows make the scroll position reachable. */
export function restoreWorkspaceScroll(
  container: HTMLElement,
  position: ScrollPosition,
) {
  let observer: ResizeObserver | undefined;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const interactions = ["wheel", "touchstart", "pointerdown", "keydown"];
  const cancel = () => {
    observer?.disconnect();
    clearTimeout(timeout);
    for (const event of interactions)
      container.removeEventListener(event, cancel);
  };
  const restore = () => {
    container.scrollTo({ ...position, behavior: "instant" });
    if (Math.abs(container.scrollTop - position.top) < 1) cancel();
  };
  const content = container.querySelector(".sd-page");
  if (content && position.top > 0) {
    observer = new ResizeObserver(restore);
    observer.observe(content);
    timeout = setTimeout(cancel, 5000);
    for (const event of interactions)
      container.addEventListener(event, cancel, { passive: true });
  }
  restore();
  return cancel;
}
