<script setup lang="ts">
import { ref, reactive, computed, onMounted } from "vue";
import { api, post, patch } from "../api";
import { user, refreshSession } from "../state";
import type { User } from "../types";
import Icon from "../components/Icon.vue";
const users = ref<User[]>([]),
  q = ref(""),
  role = ref(""),
  enabled = ref(""),
  editId = ref<number | "new" | null>(null),
  error = ref(""),
  busy = ref(false),
  temp = ref(""),
  message = ref("");
const form = reactive({
  name: "",
  email: "",
  role: "consultant",
  dingtalk_mobile: "",
});
const shown = computed(() =>
  users.value.filter(
    (u) =>
      (!q.value ||
        (u.name + " " + u.email)
          .toLowerCase()
          .includes(q.value.toLowerCase())) &&
      (!role.value || u.role === role.value) &&
      (!enabled.value || u.is_active === (enabled.value === "yes")),
  ),
);
async function load() {
  try {
    users.value = (await api<{ results: User[] }>("/accounts")).results;
  } catch (e) {
    error.value = (e as Error).message;
  }
}
function edit(u?: User) {
  error.value = "";
  temp.value = "";
  message.value = "";
  editId.value = u?.id || "new";
  Object.assign(
    form,
    u
      ? {
          name: u.name,
          email: u.email,
          role: u.role,
          dingtalk_mobile: u.dingtalk_mobile,
        }
      : { name: "", email: "", role: "consultant", dingtalk_mobile: "" },
  );
}
async function save() {
  busy.value = true;
  error.value = "";
  try {
    const result =
      editId.value === "new"
        ? await post<{ temporary_password?: string }>("/accounts", form)
        : await patch<{ temporary_password?: string }>(
            "/accounts/" + editId.value,
            form,
          );
    temp.value = result.temporary_password || "";
    message.value =
      editId.value === "new"
        ? "账号已创建。请通过安全方式将初始密码交给本人。"
        : "账号信息已更新。";
    editId.value = null;
    await load();
    await refreshSession();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function action(u: User, operation: string) {
  if (
    !window.confirm(
      operation === "reset"
        ? `重置 ${u.name} 的密码？原密码与已有登录会失效。`
        : `${operation === "disable" ? "停用" : "启用"} ${u.name} 的账号？历史工单会保留。`,
    )
  )
    return;
  busy.value = true;
  error.value = "";
  temp.value = "";
  try {
    const result = await patch<{ temporary_password?: string }>(
      "/accounts/" + u.id,
      { operation },
    );
    temp.value = result.temporary_password || "";
    message.value =
      operation === "reset"
        ? "密码已重置，首次登录需修改。"
        : "账号状态已更新。";
    await load();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>
<template>
  <div class="sd-heading">
    <h1>账号管理</h1>
    <button class="sd-button sd-primary" @click="edit()">
      <Icon name="user-plus" />新增账号
    </button>
  </div>
  <p v-if="error" class="sd-error" role="alert">{{ error }}</p>
  <div v-if="message" class="sd-inline-confirm" role="status">
    <p>{{ message }}</p>
    <template v-if="temp"
      ><p>初始密码（仅本次显示）：</p>
      <code class="temporary-password">{{ temp }}</code></template
    ><button
      class="sd-button"
      @click="
        temp = '';
        message = '';
      "
    >
      已记录，关闭
    </button>
  </div>
  <section v-if="editId !== null" class="sd-account-editor">
    <div class="sd-editor-heading">
      <h3>{{ editId === "new" ? "新增账号" : "编辑账号" }}</h3>
      <button class="sd-quiet" @click="editId = null">取消</button>
    </div>
    <form @submit.prevent="save">
      <div class="sd-form-grid">
        <label class="sd-field"
          >姓名 *<input v-model="form.name" required maxlength="60" /></label
        ><label class="sd-field"
          >登录邮箱 *<input
            v-model="form.email"
            type="email"
            required
            maxlength="254" /></label
        ><label class="sd-field"
          >角色<select v-model="form.role" :disabled="editId === user?.id">
            <option value="consultant">顾问</option>
            <option value="admin">管理员</option>
          </select></label
        ><label class="sd-field"
          >钉钉绑定手机号<input
            v-model="form.dingtalk_mobile"
            type="tel"
            maxlength="11"
            pattern="1[3-9][0-9]{9}"
            placeholder="11 位中国大陆手机号"
          /><small>用于群内 @，需本人同意且已加入协作群；手机号会传给钉钉，群消息对全群可见。</small></label
        >
      </div>
      <div class="sd-form-bottom">
        <p v-if="editId === 'new'">系统生成随机初始密码，首次登录必须修改。</p>
        <button class="sd-button sd-primary" :disabled="busy">
          {{ busy ? "正在保存…" : "保存账号" }}
        </button>
      </div>
    </form>
  </section>
  <div class="sd-filter-row">
    <label class="sd-search"
      ><Icon name="search" /><input
        v-model="q"
        type="search"
        aria-label="搜索账号"
        placeholder="搜索姓名或邮箱" /></label
    ><select v-model="role" aria-label="账号角色">
      <option value="">全部角色</option>
      <option value="admin">管理员</option>
      <option value="consultant">顾问</option></select
    ><select v-model="enabled" aria-label="账号状态">
      <option value="">全部状态</option>
      <option value="yes">已启用</option>
      <option value="no">已停用</option>
    </select>
  </div>
  <table class="sd-accounts-table">
    <thead>
      <tr>
        <th>成员 / 登录邮箱</th>
        <th>角色</th>
        <th>状态</th>
        <th>操作</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="u in shown" :key="u.id">
        <td>
          <div class="sd-person">
            {{ u.name }}<small>{{ u.email }}</small
            ><small>{{
              u.dingtalk_mobile ? "已绑定钉钉" : "未绑定钉钉"
            }}</small>
          </div>
        </td>
        <td>
          <span class="sd-role-chip">{{
            u.role === "admin" ? "管理员" : "顾问"
          }}</span>
        </td>
        <td>
          <span class="sd-account-status" :class="u.is_active ? '' : 'disabled'"
            ><span class="sd-dot"></span
            >{{ u.is_active ? "已启用" : "已停用" }}</span
          ><small v-if="u.must_change_password" class="sd-account-next"
            >首次登录需改密</small
          >
        </td>
        <td>
          <div class="account-actions">
            <button class="sd-text-link" @click="edit(u)">编辑</button
            ><template v-if="u.id !== user?.id"
              ><button
                class="sd-text-link"
                :disabled="busy"
                @click="action(u, 'reset')"
              >
                重置密码</button
              ><button
                class="sd-danger-link"
                :disabled="busy"
                @click="action(u, u.is_active ? 'disable' : 'enable')"
              >
                {{ u.is_active ? "停用" : "启用" }}
              </button></template
            >
          </div>
        </td>
      </tr>
    </tbody>
  </table>
  <p v-if="!shown.length" class="sd-empty">没有符合条件的账号。</p>
</template>
