<script setup lang="ts">
import { ref, onMounted } from "vue";
import { api, post } from "../api";
import { dateTime, type Delivery, type Page } from "../types";
const items = ref<Delivery[]>([]),
  count = ref(0),
  page = ref(1),
  error = ref(""),
  busy = ref(false),
  config = ref<{
    enabled: boolean;
    public_url_ready: boolean;
    group_ready: boolean;
    unbound_admins: number;
    unbound_consultants: number;
  } | null>(null);
const states: Record<string, string> = {
  pending: "待发送",
  sending: "发送中",
  accepted: "钉钉已接收",
  blocked: "待配置 / 受阻",
  failed: "发送失败",
};
async function load() {
  busy.value = true;
  error.value = "";
  try {
    const data = await api<
      Page<Delivery> & { configuration: NonNullable<typeof config.value> }
    >("/deliveries?page=" + page.value);
    items.value = data.results;
    count.value = data.count;
    config.value = data.configuration;
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function retry(id: number) {
  try {
    await post("/deliveries/" + id + "/retry");
    await load();
  } catch (e) {
    error.value = (e as Error).message;
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
    <h1>钉钉通知</h1>
    <button class="sd-button" :disabled="busy" @click="load">刷新状态</button>
  </div>
  <div v-if="config" class="sd-inline-panel">
    <h3>{{ config.enabled ? "通知通道已启用" : "钉钉通知尚未启用" }}</h3>
    <p>
      HTTPS 访问地址：{{ config.public_url_ready ? "已配置" : "待配置" }} ·
      协作群机器人：{{ config.group_ready ? "已配置" : "待配置" }}
    </p>
    <p>
      尚有 {{ config.unbound_admins }} 位管理员、{{ config.unbound_consultants }} 位顾问未绑定钉钉手机号。请在账号管理中补充，并确认已入群。
    </p>
  </div>
  <p class="sd-small sd-muted page-note">
    群内定向 @ 的消息对全群可见，仅发送更新提醒和登录链接。“钉钉已接收”不代表 @ 成功、送达或已读；站内提醒独立生效。
  </p>
  <p v-if="error" class="sd-error" role="alert">{{ error }}</p>
  <div v-for="d in items" :key="d.id" class="delivery-row">
    <div>
      <strong>{{ d.ticket_number }}</strong
      ><span class="sd-status">{{ states[d.state] || d.state }}</span>
      <p class="sd-small sd-muted">
        {{
          d.channel === "group_admin" ? "群内 @ 管理员" : d.channel === "group_consultant" ? d.recipient_name + " · 群内 @" : "旧版通知记录"
        }}
        · 尝试 {{ d.attempts }} 次 · {{ dateTime(d.created_at) }}
      </p>
      <p v-if="d.last_error" class="sd-small">{{ d.last_error }}</p>
    </div>
    <button
      v-if="['blocked', 'failed'].includes(d.state) && ['group_admin', 'group_consultant'].includes(d.channel)"
      class="sd-button"
      :disabled="busy"
      @click="retry(d.id)"
    >
      重试
    </button>
  </div>
  <div v-if="!items.length && !busy" class="sd-empty">暂无通知记录。</div>
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
