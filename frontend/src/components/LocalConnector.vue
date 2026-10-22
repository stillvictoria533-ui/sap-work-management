<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api, post } from "../api";
import { dateTime } from "../types";
import Icon from "./Icon.vue";

const props = defineProps<{ system: string }>();
const emit = defineEmits<{
  attach: [query: { id: string; label: string }];
  available: [value: boolean];
}>();
interface Device {
  id: string;
  name: string;
  online: boolean;
  adapter: string;
  operations: string[];
  last_success_at: string | null;
  expires_at: string;
}
interface Query {
  id: string;
  device_id: string;
  operation: string;
  object_name: string;
  status: string;
  error_message: string;
  result: Record<string, unknown>;
  finished_at: string | null;
}
interface State {
  enabled: boolean;
  devices: Device[];
  queries: Query[];
}
const state = ref<State>({ enabled: false, devices: [], queries: [] });
const dialog = ref<HTMLDialogElement>();
const code = ref(""),
  expires = ref(""),
  error = ref(""),
  busy = ref(false);
const operation = ref("tcode"),
  objectName = ref("SE38"),
  deviceId = ref("");
const online = computed(() => state.value.devices.some((d) => d.online));
const selectedDevice = computed(() =>
  state.value.devices.find((d) => d.id === deviceId.value),
);
const operationLabels: Record<string, string> = {
  tcode: "事务码映射",
  ddic: "DDIC 结构",
  gui_session: "当前 SAP GUI 会话",
};
const supportedOperations = computed(
  () => selectedDevice.value?.operations || [],
);
watch(
  () => supportedOperations.value.join(","),
  () => {
    if (!supportedOperations.value.includes(operation.value))
      operation.value = supportedOperations.value[0] || "tcode";
    objectName.value = operation.value === "gui_session" ? "CURRENT" : "SE38";
  },
);
const active = computed(() =>
  state.value.queries.some(
    (q) =>
      q.device_id === deviceId.value &&
      ["queued", "running"].includes(q.status),
  ),
);
const labels: Record<string, string> = {
  queued: "等待本机接收",
  running: "请在连接器终端确认 / 正在读取",
  completed: "读取成功",
  failed: "未完成",
};
let timer: ReturnType<typeof setInterval>,
  closed = false,
  refreshing = false;
async function refresh() {
  if (refreshing || closed) return;
  refreshing = true;
  try {
    const value = await api<State>("/assistant/connector/status");
    if (closed) return;
    if (
      value.devices.some(
        (d) => !state.value.devices.some((old) => old.id === d.id),
      )
    )
      code.value = "";
    state.value = value;
    emit("available", value.enabled);
    if (!value.enabled) clearInterval(timer);
    if (!value.devices.some((d) => d.id === deviceId.value))
      deviceId.value = value.devices[0]?.id || "";
  } catch {
    // Never continue showing online after loss of contact with the broker.
    state.value.devices = state.value.devices.map((d) => ({
      ...d,
      online: false,
    }));
  } finally {
    refreshing = false;
  }
}
onMounted(() => {
  void refresh();
  timer = setInterval(() => {
    if (!document.hidden) void refresh();
  }, 3000);
});
onBeforeUnmount(() => {
  closed = true;
  clearInterval(timer);
});
async function pair() {
  busy.value = true;
  error.value = "";
  try {
    const data = await post<{ code: string; expires_at: string }>(
      "/assistant/connector/pair-code",
    );
    code.value = data.code;
    expires.value = data.expires_at;
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function revoke(id: string) {
  busy.value = true;
  error.value = "";
  try {
    await post(`/assistant/connector/devices/${id}/revoke`);
    await refresh();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function query() {
  busy.value = true;
  error.value = "";
  try {
    await post("/assistant/connector/queries", {
      request_key: crypto.randomUUID(),
      device_id: deviceId.value,
      operation: operation.value,
      object_name:
        operation.value === "gui_session"
          ? "CURRENT"
          : objectName.value.trim().toUpperCase(),
    });
    await refresh();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
function canAttach(item: Query) {
  return (
    props.system === "DS4" &&
    state.value.devices.some((d) => d.id === item.device_id) &&
    !!item.finished_at &&
    Date.parse(item.finished_at) >= Date.now() - 30 * 60_000
  );
}
function attach(item: Query) {
  if (!canAttach(item)) return;
  emit("attach", {
    id: item.id,
    label: `DS4 / 110 · ${item.object_name} · ${dateTime(item.finished_at || "")}`,
  });
  dialog.value?.close();
}
</script>
<template>
  <div v-if="state.enabled" class="ai-local-bar">
    <span
      ><span class="ai-provider-dot" :class="{ ready: online }"></span>本机 DS4
      <small>{{
        online ? "连接器在线 · 按需只读查询" : "连接器未连接"
      }}</small></span
    >
    <button class="sd-quiet" @click="dialog?.showModal()">
      <Icon name="link" />管理连接与查询
    </button>
  </div>
  <dialog ref="dialog" class="ai-local-dialog">
    <header class="ai-dialog-heading">
      <div>
        <small>MAC / WINDOWS · 免安装试用版</small>
        <h2>连接我的 DS4</h2>
      </div>
      <button
        class="sd-quiet"
        aria-label="关闭本机连接面板"
        @click="dialog?.close()"
      >
        <Icon name="x" />
      </button>
    </header>
    <p class="ai-local-intro">
      SAP
      凭据留在本机。每次查询需在连接器终端确认，结果只进入你的账号；点击“附到下一条提问”后才交给
      AI 分析。
    </p>
    <div v-if="error" class="sd-error" role="alert">{{ error }}</div>
    <section class="ai-local-section">
      <h3>01 · 配对这台电脑</h3>
      <p>
        解压对应系统的试用包：Mac 双击「启动连接器.command」；Windows
        双击「Start-Connector.cmd」。输入服务器地址和下方配对码。Windows
        版只需已登录的 SAP GUI，实机验证待完成。
      </p>
      <button class="sd-quiet ai-local-action" :disabled="busy" @click="pair">
        生成一次性配对码
      </button>
      <div v-if="code" class="ai-local-code">
        <input
          aria-label="一次性配对码"
          readonly
          :value="code"
          @focus="($event.target as HTMLInputElement).select()"
        /><small
          >有效至
          {{
            dateTime(expires)
          }}，仅供自己的连接器使用。生成新码会使旧码失效。</small
        >
      </div>
      <div
        v-for="device in state.devices"
        :key="device.id"
        class="ai-local-device"
      >
        <div>
          <strong>{{ device.name }}</strong
          ><small
            >{{ device.online ? "在线" : "离线" }} ·
            {{
              device.last_success_at
                ? "DS4 最近成功：" + dateTime(device.last_success_at)
                : "DS4 尚未验证"
            }}</small
          >
        </div>
        <button class="sd-quiet" :disabled="busy" @click="revoke(device.id)">
          断开
        </button>
      </div>
    </section>
    <section class="ai-local-section">
      <h3>02 · 发起只读查询</h3>
      <p>
        Mac 支持事务码映射和 DDIC 结构；Windows 当前仅支持 DS4/110 的 GUI
        会话信息，需要允许 SAP GUI Scripting。不操作画面、不读取业务记录。
      </p>
      <form class="ai-local-query" @submit.prevent="query">
        <label
          >本机设备<select v-model="deviceId" required>
            <option value="" disabled>先配对连接器</option>
            <option
              v-for="d in state.devices"
              :key="d.id"
              :value="d.id"
              :disabled="!d.online"
            >
              {{ d.name }}{{ d.online ? "" : "（离线）" }}
            </option>
          </select></label
        >
        <label
          >查询类型<select v-model="operation">
            <option v-for="op in supportedOperations" :key="op" :value="op">
              {{ operationLabels[op] }}
            </option>
          </select></label
        >
        <label
          >对象名称<input
            v-model="objectName"
            :readonly="operation === 'gui_session'"
            maxlength="30"
            pattern="[A-Za-z][A-Za-z0-9_]{0,29}"
            required
            placeholder="例如 SE38 / T000"
        /></label>
        <button
          class="ai-send"
          :disabled="
            busy ||
            active ||
            !state.devices.some((d) => d.id === deviceId && d.online)
          "
        >
          提交查询
        </button>
      </form>
      <p v-if="active" role="status" class="ai-local-wait">
        已提交。请切到连接器终端输入 y 确认；查询最多等待 4 分钟。
      </p>
    </section>
    <section class="ai-local-section">
      <h3>03 · 查看证据并附到对话</h3>
      <p v-if="!state.queries.length">查询完成后，结果会显示在这里。</p>
      <article
        v-for="item in state.queries"
        :key="item.id"
        class="ai-local-result"
      >
        <div class="ai-local-result-heading">
          <strong
            >DS4 / 110 · {{ operationLabels[item.operation] || item.operation }}
            {{ item.object_name }}</strong
          ><span :class="{ success: item.status === 'completed' }">{{
            labels[item.status] || item.status
          }}</span>
        </div>
        <p v-if="item.error_message" role="status">{{ item.error_message }}</p>
        <template v-if="item.status === 'completed'">
          <small
            >采集时间：{{ dateTime(item.finished_at || "") }} ·
            本机查询快照</small
          >
          <details>
            <summary>查看原始查询结果</summary>
            <pre>{{ JSON.stringify(item.result, null, 2) }}</pre>
          </details>
          <button
            class="sd-quiet ai-local-action"
            :disabled="!canAttach(item)"
            @click="attach(item)"
          >
            附到下一条提问
          </button>
          <small v-if="!canAttach(item)"
            >需使用 DS4 对话、30
            分钟内的结果及未撤销的设备；历史结果仍可查看。</small
          >
        </template>
      </article>
    </section>
  </dialog>
</template>
<style>
#sap-desk-design .ai-local-bar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 28px;
  border-bottom: 1px solid var(--sd-line);
  background: var(--sd-side);
  font-size: 12px;
}
#sap-desk-design .ai-local-bar > span {
  display: flex;
  align-items: center;
  gap: 8px;
}
#sap-desk-design .ai-local-bar small {
  color: var(--sd-muted);
}
#sap-desk-design .ai-local-bar button {
  display: flex;
  align-items: center;
  gap: 6px;
}
#sap-desk-design .ai-local-dialog {
  width: min(740px, calc(100vw - 32px));
  max-height: 85dvh;
  overflow-y: auto;
  padding: 28px;
  border: 1px solid var(--sd-line);
  border-radius: 14px;
  color: var(--sd-ink);
  background: var(--sd-paper);
}
#sap-desk-design .ai-local-dialog::backdrop {
  background: #26242070;
}
#sap-desk-design .ai-local-dialog h2 {
  margin: 5px 0 0;
  font-size: 24px;
}
#sap-desk-design .ai-local-dialog small,
#sap-desk-design .ai-local-dialog p {
  color: var(--sd-muted);
  line-height: 1.7;
}
#sap-desk-design .ai-local-intro {
  font-size: 13px;
  margin: 16px 0 20px;
}
#sap-desk-design .ai-local-section {
  border-top: 1px solid var(--sd-line);
  padding: 20px 0 4px;
}
#sap-desk-design .ai-local-section h3 {
  font-size: 15px;
  margin: 0 0 10px;
}
#sap-desk-design .ai-local-section p {
  font-size: 12px;
  margin: 8px 0 12px;
}
#sap-desk-design .ai-local-action {
  border: 1px solid var(--sd-line);
  border-radius: 6px;
  padding: 7px 10px;
  margin: 6px 0;
}
#sap-desk-design .ai-local-code {
  margin: 10px 0;
  display: grid;
  gap: 5px;
}
#sap-desk-design .ai-local-code input {
  font-family: monospace;
  width: 100%;
}
#sap-desk-design .ai-local-device {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  gap: 12px;
}
#sap-desk-design .ai-local-device strong,
#sap-desk-design .ai-local-device small {
  display: block;
  font-size: 12px;
}
#sap-desk-design .ai-local-query {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
#sap-desk-design .ai-local-query label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
}
#sap-desk-design .ai-local-query input,
#sap-desk-design .ai-local-query select {
  min-width: 0;
  width: 100%;
}
#sap-desk-design .ai-local-query button {
  align-self: end;
  justify-self: start;
  height: 38px;
}
#sap-desk-design .ai-local-wait {
  color: var(--sd-accent);
}
#sap-desk-design .ai-local-result {
  padding: 14px;
  margin: 12px 0;
  border: 1px solid var(--sd-line);
  border-radius: 8px;
}
#sap-desk-design .ai-local-result-heading {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: space-between;
  font-size: 12px;
}
#sap-desk-design .ai-local-result-heading span {
  color: var(--sd-muted);
}
#sap-desk-design .ai-local-result-heading .success {
  color: #347856;
}
#sap-desk-design .ai-local-result details {
  margin: 12px 0;
  font-size: 12px;
}
#sap-desk-design .ai-local-result pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 210px;
  overflow: auto;
  background: var(--sd-side);
  padding: 12px;
}
#sap-desk-design .ai-local-result summary {
  cursor: pointer;
}
@media (max-width: 640px) {
  #sap-desk-design .ai-local-bar {
    padding: 8px 12px;
  }
  #sap-desk-design .ai-local-bar small {
    display: none;
  }
  #sap-desk-design .ai-local-dialog {
    padding: 18px;
  }
}
</style>
