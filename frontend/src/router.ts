import { createRouter, createWebHashHistory } from "vue-router";
import { ready, user } from "./state";
import {
  navigationScroll,
  restoreWorkspaceScroll,
  type ScrollPosition,
} from "./scroll";
const workspacePositions = new Map<number, ScrollPosition>();
let currentEntry: number | undefined = window.history.state?.position;
let cancelScrollRestore = () => {};
function workspaceScroller() {
  const main = document.querySelector<HTMLElement>("#sd-workspace .sd-main");
  return main && getComputedStyle(main).overflowY === "auto" ? main : null;
}
export const router = createRouter({
  history: createWebHashHistory(),
  scrollBehavior(to, from, savedPosition) {
    currentEntry = window.history.state?.position;
    const main = workspaceScroller();
    const saved =
      savedPosition && main && currentEntry !== undefined
        ? workspacePositions.get(currentEntry) || savedPosition
        : savedPosition;
    const position = navigationScroll(to, from, saved);
    if (!main) return position;
    if (position)
      cancelScrollRestore = restoreWorkspaceScroll(main, {
        left: "left" in position ? position.left : 0,
        top: position.top,
      });
    return false;
  },
  routes: [
    {
      path: "/",
      component: () => import("./pages/Home.vue"),
      meta: { public: true },
    },
    {
      path: "/login",
      component: () => import("./pages/Login.vue"),
      meta: { public: true },
    },
    { path: "/tickets", component: () => import("./pages/Tickets.vue") },
    {
      path: "/assistant/:id?",
      redirect: "/tickets",
    },
    {
      path: "/calendar",
      component: () => import("./pages/Calendar.vue"),
    },
    { path: "/tickets/new", component: () => import("./pages/NewTicket.vue") },
    {
      path: "/tickets/:id(\\d+)",
      component: () => import("./pages/TicketDetail.vue"),
    },
    {
      path: "/scheduling",
      component: () => import("./pages/Scheduling.vue"),
      meta: { admin: true },
    },
    { path: "/profile", component: () => import("./pages/Profile.vue") },
    {
      path: "/notifications",
      component: () => import("./pages/Notifications.vue"),
    },
    {
      path: "/accounts",
      component: () => import("./pages/Accounts.vue"),
      meta: { admin: true },
    },
    {
      path: "/deliveries",
      component: () => import("./pages/Deliveries.vue"),
      meta: { admin: true },
    },
    {
      path: "/operations",
      component: () => import("./pages/Operations.vue"),
      meta: { admin: true },
    },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});
router.beforeEach(async (to) => {
  cancelScrollRestore();
  const main = workspaceScroller();
  if (main && currentEntry !== undefined)
    workspacePositions.set(currentEntry, {
      left: main.scrollLeft,
      top: main.scrollTop,
    });
  if (to.meta.public) return true;
  await ready;
  if (!to.meta.public && !user.value)
    return { path: "/login", query: { next: to.fullPath } };
  if (
    user.value?.must_change_password &&
    !to.meta.public &&
    to.path !== "/profile"
  )
    return { path: "/profile", query: { next: to.fullPath } };
  if (to.meta.admin && user.value?.role !== "admin") return "/tickets";
  if (to.path === "/tickets/new" && user.value?.role !== "consultant")
    return "/tickets";
});
