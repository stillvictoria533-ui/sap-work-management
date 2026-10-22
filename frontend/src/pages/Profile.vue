<script setup lang="ts">
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { post } from "../api";
import { user, refreshStats } from "../state";
import type { User } from "../types";
const router = useRouter(),
  route = useRoute(),
  old = ref(""),
  password = ref(""),
  confirmation = ref(""),
  error = ref(""),
  busy = ref(false);
async function submit() {
  error.value = "";
  if (password.value !== confirmation.value) {
    error.value = "两次输入的新密码不一致。";
    return;
  }
  busy.value = true;
  try {
    const data = await post<{ user: User }>("/auth/password", {
      current_password: old.value,
      password: password.value,
    });
    user.value = data.user;
    old.value = password.value = confirmation.value = "";
    await refreshStats();
    await router.push(
      typeof route.query.next === "string" &&
        route.query.next.startsWith("/") &&
        !route.query.next.startsWith("//")
        ? route.query.next
        : "/tickets",
    );
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="sd-profile">
    <div class="sd-heading">
      <h1>{{ user?.must_change_password ? "设置新密码" : "我的账号" }}</h1>
    </div>
    <div class="sd-profile-identity">
      <div class="sd-avatar">{{ user?.name.slice(0, 1) }}</div>
      <div class="sd-person">
        {{ user?.name }} · {{ user?.role === "admin" ? "管理员" : "顾问"
        }}<small>{{ user?.email }}</small>
      </div>
    </div>
    <p class="sd-small sd-muted page-note">
      {{
        user?.must_change_password
          ? "首次登录或密码已重置，请设置新密码后继续。"
          : "修改密码后，其他设备的登录将失效。"
      }}
    </p>
    <form @submit.prevent="submit">
      <label v-if="!user?.must_change_password" class="sd-field"
        >当前密码<input
          v-model="old"
          type="password"
          required
          autocomplete="current-password" /></label
      ><label class="sd-field"
        >新密码<input
          v-model="password"
          type="password"
          minlength="12"
          maxlength="256"
          required
          autocomplete="new-password"
          placeholder="至少 12 个字符" /></label
      ><label class="sd-field"
        >确认新密码<input
          v-model="confirmation"
          type="password"
          required
          autocomplete="new-password"
      /></label>
      <p v-if="error" class="sd-error" role="alert">{{ error }}</p>
      <button class="sd-button sd-primary" :disabled="busy">
        {{ busy ? "正在保存…" : "保存并进入工作台" }}
      </button>
    </form>
  </div>
</template>
