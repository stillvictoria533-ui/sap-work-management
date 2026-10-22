<script setup lang="ts">
import { ref, onMounted } from "vue";
import { api, post } from "../api";
import { refreshStats } from "../state";
import { dateTime, type Notice, type Page } from "../types";
const items = ref<Notice[]>([]),
  count = ref(0),
  page = ref(1),
  unread = ref(false),
  error = ref(""),
  busy = ref(false);
async function load() {
  error.value = "";
  busy.value = true;
  try {
    const data = await api<Page<Notice>>(
      `/notifications?page=${page.value}&unread=${unread.value}`,
    );
    items.value = data.results;
    count.value = data.count;
    await refreshStats();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function markAll() {
  busy.value = true;
  try {
    await post("/notifications");
    await load();
    await refreshStats();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
function turn(n: number) {
  page.value = n;
  void load();
}
onMounted(load);
</script>
<template>
  <div class="sd-heading">
    <h1>站内提醒</h1>
    <button class="sd-button" :disabled="busy" @click="markAll">
      全部标记已读
    </button>
  </div>
  <label class="checkbox-line"
    ><input
      type="checkbox"
      v-model="unread"
      @change="
        page = 1;
        load();
      "
    />只看未读</label
  >
  <p v-if="error" class="sd-error" role="alert">{{ error }}</p>
  <p v-if="busy" class="sd-muted" role="status">正在加载…</p>
  <div v-for="n in items" :key="n.id" class="notice-row">
    <span v-if="!n.read_at" class="sd-dot unread-dot"></span>
    <div>
      <RouterLink :to="'/tickets/' + n.ticket_id" class="sd-ticket-title">{{
        n.title
      }}</RouterLink>
      <p class="sd-small sd-muted">
        {{ n.number }} · {{ n.actor }}
        {{
          n.kind === "created"
            ? "提交了需求"
            : n.kind === "schedule"
              ? "调整了开发排期"
              : n.kind === "expedite"
                ? "更新了加急申请"
                : "更新了工单"
        }}
      </p>
      <p
        v-if="['schedule', 'expedite'].includes(n.kind) && n.body"
        class="sd-notice-detail"
      >
        {{ n.body }}
      </p>
      <time class="sd-small sd-muted">{{ dateTime(n.created_at) }}</time>
    </div>
  </div>
  <div v-if="!items.length && !busy" class="sd-empty">
    暂无{{ unread ? "未读" : "" }}提醒。
  </div>
  <div class="sd-list-footer">
    <span>共 {{ count }} 条</span>
    <div class="sd-flex">
      <button
        class="sd-button"
        :disabled="page <= 1 || busy"
        @click="turn(page - 1)"
      >
        上一页</button
      ><span>{{ page }}</span
      ><button
        class="sd-button"
        :disabled="page * 20 >= count || busy"
        @click="turn(page + 1)"
      >
        下一页
      </button>
    </div>
  </div>
</template>

<style>
#sap-desk-design .sd-notice-detail {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 1.8;
  margin: 9px 0;
}
</style>
