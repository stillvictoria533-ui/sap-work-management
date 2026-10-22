import { createRouter, createWebHashHistory } from "vue-router";
import { ready, user } from "./state";
import { navigationScroll } from "./scroll";
export const router = createRouter({
  history: createWebHashHistory(),
  scrollBehavior: navigationScroll,
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
      meta: { admin: true },
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
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});
router.beforeEach(async (to) => {
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
