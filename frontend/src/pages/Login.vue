<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRouter, useRoute } from "vue-router";
import { signIn, user, sessionError, refreshSession, ready } from "../state";
import Brand from "../components/Brand.vue";
import Icon from "../components/Icon.vue";
const router = useRouter(),
  route = useRoute(),
  email = ref(""),
  password = ref(""),
  visible = ref(false),
  help = ref(false),
  error = ref(""),
  busy = ref(false);
onMounted(async () => {
  await ready;
  if (route.query.force === "1" || !user.value) return;
  try {
    await refreshSession();
  } catch (e) {
    error.value = (e as Error).message;
    return;
  }
  if (user.value) {
    const next =
      typeof route.query.next === "string" &&
      route.query.next.startsWith("/") &&
      !route.query.next.startsWith("//")
        ? route.query.next
        : "/tickets";
    await router.replace(
      user.value.must_change_password
        ? { path: "/profile", query: { next } }
        : next,
    );
  }
});
async function submit() {
  busy.value = true;
  error.value = "";
  try {
    await refreshSession();
    await signIn(email.value, password.value);
    password.value = "";
    const next =
      typeof route.query.next === "string" &&
      route.query.next.startsWith("/") &&
      !route.query.next.startsWith("//")
        ? route.query.next
        : "/tickets";
    await router.push(
      user.value?.must_change_password
        ? { path: "/profile", query: { next } }
        : next,
    );
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="sd-home-login">
    <header class="sd-simple-login-header">
      <Brand /><RouterLink to="/" class="sd-quiet"
        ><Icon name="arrow-left" />返回首页</RouterLink
      >
    </header>
    <div class="sd-simple-login-content">
      <section class="sd-login-panel">
        <h2>欢迎回来。</h2>
        <p>登录你的 SAP 需求协作空间。</p>
        <form @submit.prevent="submit">
          <div class="sd-field">
            <label for="email">登录账号</label
            ><input
              id="email"
              v-model="email"
              type="email"
              required
              maxlength="254"
              autocomplete="username"
              placeholder="请输入账号邮箱"
            />
          </div>
          <div class="sd-field">
            <label for="password">密码</label>
            <div class="sd-password-field">
              <input
                id="password"
                v-model="password"
                :type="visible ? 'text' : 'password'"
                required
                maxlength="256"
                autocomplete="current-password"
                placeholder="请输入密码"
              /><button
                type="button"
                @click="visible = !visible"
                :aria-label="visible ? '隐藏密码' : '显示密码'"
                :aria-pressed="visible"
              >
                <Icon :name="visible ? 'eye-off' : 'eye'" />
              </button>
            </div>
          </div>
          <div class="sd-form-meta">
            <span>账号由管理员统一开通</span
            ><button
              class="sd-text-link"
              type="button"
              @click="help = !help"
              :aria-expanded="help"
            >
              忘记密码？
            </button>
          </div>
          <p v-if="help" class="sd-login-help">
            请联系平台管理员重置密码。重置后首次登录需设置新密码。
          </p>
          <p v-if="error || sessionError" class="sd-error" role="alert">
            {{ error || sessionError }}
          </p>
          <button class="sd-button sd-primary sd-login-submit" :disabled="busy">
            {{ busy ? "正在登录…" : "登录工作台" }}<Icon name="arrow-right" />
          </button>
        </form>
      </section>
    </div>
  </section>
</template>
