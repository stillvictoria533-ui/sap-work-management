<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { user, stats, refreshStats, refreshSession, signOut, clearLocalSession } from "./state";
import NavLink from "./components/NavLink.vue";
import Brand from "./components/Brand.vue";
import Icon from "./components/Icon.vue";
const route = useRoute(),
  router = useRouter(),
  error = ref("");
const isPublic = computed(() => !!route.meta.public);
let timer: ReturnType<typeof setInterval>;
async function poll() {
  if (!user.value || document.hidden || user.value.must_change_password) return;
  try {
    await refreshStats();
    error.value = "";
  } catch {
    try {
      await refreshSession();
      if (!user.value) error.value = "登录已过期，请重新登录后继续。";
      else error.value = "";
    } catch {
      /* Keep current work intact during temporary outages. */
    }
  }
}
onMounted(() => {
  void poll();
  timer = setInterval(poll, 30000);
});
onUnmounted(() => clearInterval(timer));
async function logout() {
  try {
    await signOut();
    error.value = "";
    await router.push("/login");
  } catch (e) {
    error.value = (e as Error).message;
  }
}
</script>
<template>
  <div id="sap-desk-design">
    <RouterView v-if="isPublic" />
    <div
      v-else
      id="sd-workspace"
      class="sd-window"
      :class="{ 'sd-ai-window': route.path.startsWith('/assistant') }"
    >
      <aside class="sd-sidebar">
        <Brand />
        <div class="sd-brand-caption">需求协作空间</div>
        <nav class="sd-nav" aria-label="工作台导航">
          <div class="sd-nav-label">WORKSPACE</div>
          <NavLink to="/tickets"
            ><Icon name="inbox" />{{
              user?.role === "admin" ? "全部工单" : "我的工单"
            }}<span class="sd-count">{{ stats.total }}</span></NavLink
          ><NavLink to="/tickets?scope=attention"
            ><Icon name="message-circle" />需要我处理<span class="sd-count">{{
              stats.attention
            }}</span></NavLink
          ><NavLink to="/notifications"
            ><Icon name="bell" />站内提醒<span
              v-if="stats.notifications"
              class="sd-count"
              >{{ stats.notifications }}</span
            ></NavLink
          ><NavLink to="/tickets?scope=archive"
            ><Icon name="archive" />已归档</NavLink
          ><NavLink to="/calendar"
            ><Icon name="calendar-days" />开发日历</NavLink
          >
          <template v-if="user?.role === 'admin'">
            <div class="sd-nav-divider"></div>
            <NavLink to="/accounts"><Icon name="users-round" />账号管理</NavLink
            ><NavLink to="/deliveries"
              ><Icon name="send" />钉钉通知</NavLink
            ></template
          >
        </nav>
        <div class="sd-sidebar-bottom">
          <RouterLink class="sd-back-home" to="/"
            ><Icon name="arrow-left" />返回首页</RouterLink
          ><RouterLink class="sd-profile-button" to="/profile"
            ><span class="sd-avatar">{{ user?.name.slice(0, 1) }}</span
            ><span class="sd-person"
              >{{ user?.name
              }}<small>{{
                user?.role === "admin" ? "管理员" : "SAP 顾问"
              }}</small></span
            ></RouterLink
          >
        </div>
      </aside>
      <main class="sd-main">
        <div class="sd-topbar">
          <span>SAP 需求协作空间</span>
          <div v-if="user" class="sd-top-actions">
            <RouterLink to="/profile">我的账号</RouterLink
            ><button @click="logout"><Icon name="log-out" />退出</button>
          </div>
        </div>
        <div v-if="error" class="sd-notice" role="alert">
          <span>{{ error }}</span
          ><RouterLink
            :to="{ path: '/login', query: { force: '1', next: route.fullPath } }"
            @click="clearLocalSession(); error = ''"
            >重新登录</RouterLink
          ><button @click="error = ''" aria-label="关闭提示">×</button>
        </div>
        <div
          class="sd-page"
          :class="{ 'sd-chat-page': route.path.startsWith('/assistant') }"
        >
          <RouterView />
        </div>
      </main>
    </div>
  </div>
</template>
