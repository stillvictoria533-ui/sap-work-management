<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "../api";
import { dateTime, type OperationsHealth } from "../types";
import Icon from "../components/Icon.vue";

const health = ref<OperationsHealth | null>(null),
  loading = ref(false),
  error = ref(""),
  refreshedAt = ref("");
let timer: ReturnType<typeof setInterval> | undefined;
let sequence = 0;
async function load() {
  if (loading.value) return;
  const current = ++sequence;
  loading.value = true;
  try {
    const result = await api<OperationsHealth>("/ai-monitor");
    if (current !== sequence) return;
    health.value = result;
    refreshedAt.value = new Date().toISOString();
    error.value = "";
  } catch (e) {
    if (current === sequence) error.value = (e as Error).message;
  } finally {
    if (current === sequence) loading.value = false;
  }
}
onMounted(() => {
  void load();
  timer = setInterval(() => {
    if (!document.hidden) void load();
  }, 30000);
});
onUnmounted(() => {
  sequence++;
  clearInterval(timer);
});
const nodeLabel = computed(
  () =>
    ({
      online: "节点在线",
      offline: "节点离线",
      awaiting: "等待首次连接",
      disabled: "AI 跟进未启用",
    })[health.value?.node.status || "awaiting"],
);
const attention = computed(() => {
  const value = health.value;
  if (!value) return false;
  const { storage, backup } = value;
  return (
    Object.values(value.alerts).some(Boolean) ||
    ["failed", "stale"].includes(backup.status) ||
    backup.last_drill_ok === false ||
    (storage.limits.global_bytes > 0 &&
      storage.used_bytes >= storage.limits.global_bytes) ||
    (storage.free_bytes !== null &&
      storage.free_bytes <= storage.limits.min_free_bytes)
  );
});
const uncertain = computed(
  () =>
    !!health.value &&
    (health.value.node.status === "awaiting" ||
      health.value.backup.status === "unknown" ||
      health.value.backup.last_drill_ok === null ||
      health.value.storage.free_bytes === null),
);
const overall = computed(() =>
  attention.value
    ? "需要关注"
    : uncertain.value
      ? "部分状态待确认"
      : "运行正常",
);
const capabilities = [
  ["model_cli", "模型程序"],
  ["sap_tools", "SAP 工具文件"],
  ["production_identity", "固定身份配置"],
  ["proxy", "本机代理"],
] as const;
const reasons: Record<string, string> = {
  sap_unavailable: "SAP 暂不可用",
  authorization_missing: "身份或授权受阻",
  context_incomplete: "信息不足",
  evidence_incomplete: "证据不完整",
  runtime_error: "执行异常",
  time_limit: "超过处理时限",
  other: "其他原因",
};
const backupLabels = {
  healthy: "备份正常",
  running: "备份进行中",
  failed: "最近一次备份失败",
  stale: "备份超过 30 小时",
  unknown: "暂无有效状态",
};
const timeLabel = (value: string | null) =>
  value ? dateTime(value) : "暂无记录";
function bytes(value: number | null) {
  if (value === null) return "暂不可用";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GiB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${value} B`;
}
function limit(value: number) {
  return value ? bytes(value) : "未设置上限";
}
function waitTime(value: number | null) {
  if (value === null) return "暂无等待";
  if (value < 60) return "不足 1 分钟";
  if (value < 3600) return `${Math.floor(value / 60)} 分钟`;
  return `${Math.floor(value / 3600)} 小时 ${Math.floor((value % 3600) / 60)} 分钟`;
}
const storagePercent = computed(() => {
  const storage = health.value?.storage;
  return storage && storage.limits.global_bytes
    ? Math.min(100, (100 * storage.used_bytes) / storage.limits.global_bytes)
    : 0;
});
</script>

<template>
  <div class="sd-heading ops-heading">
    <div>
      <p class="ops-kicker">OPERATIONS / 运行状态</p>
      <h1>运行状态</h1>
      <p class="sd-muted">工单 AI、附件空间与本机备份</p>
    </div>
    <button class="sd-button" :disabled="loading" @click="load">
      <Icon name="refresh-cw" />刷新状态
    </button>
  </div>
  <p v-if="error" class="sd-error" role="alert">
    状态刷新失败：{{ error
    }}<span v-if="health">。以下为上次成功获取的状态。</span>
  </p>
  <p v-if="loading && !health" class="sd-muted" role="status">
    正在读取运行状态…
  </p>
  <template v-if="health">
    <div
      class="ops-overview"
      :class="{
        'ops-attention': attention,
        'ops-pending': uncertain && !attention,
      }"
    >
      <div>
        <span class="ops-dot"></span><strong>{{ overall }}</strong
        ><span class="sd-muted"> · {{ nodeLabel }}</span>
      </div>
      <small class="sd-muted">{{
        refreshedAt ? "更新于 " + dateTime(refreshedAt) : ""
      }}</small>
    </div>
    <section class="ops-section" aria-labelledby="ops-ai-heading">
      <div class="ops-section-heading">
        <div>
          <span>01</span>
          <h2 id="ops-ai-heading">工单 AI</h2>
        </div>
        <span
          class="ops-chip"
          :class="{
            'ops-chip-alert': health.node.status === 'offline',
            'ops-chip-neutral': ['awaiting', 'disabled'].includes(
              health.node.status,
            ),
          }"
          >{{ nodeLabel }}</span
        >
      </div>
      <div class="ops-grid ops-ai-grid">
        <div>
          <span class="ops-label">最近节点活动</span
          ><strong>{{ timeLabel(health.node.last_seen_at) }}</strong
          ><small>最近扫描：{{ timeLabel(health.node.last_scan_at) }}</small>
        </div>
        <div>
          <span class="ops-label">等待处理</span
          ><strong>{{ health.queue.queued }} <small>项</small></strong
          ><small
            >最久等待 {{ waitTime(health.queue.oldest_wait_seconds) }}</small
          >
        </div>
        <div>
          <span class="ops-label">正在分析</span
          ><strong>{{ health.queue.running }} <small>项</small></strong
          ><small>稍后重试 {{ health.queue.retry_wait }} 项</small>
        </div>
        <div>
          <span class="ops-label">需要跟进</span
          ><strong>{{ health.queue.manual_required }} <small>项</small></strong
          ><small>等待补充 {{ health.queue.waiting_for_information }} 项</small>
        </div>
      </div>
      <div class="ops-detail-row">
        <span
          >最近成功回复
          <strong>{{ timeLabel(health.last_reply_at) }}</strong></span
        ><span
          >最近连续受阻
          <strong>{{ health.failures.consecutive }} 次</strong></span
        >
      </div>
      <p
        v-if="health.queue.needs_acceptance || health.queue.inactive_submitters"
        class="sd-small ops-reasons"
      >
        受理账号不可用或受理待核实 {{ health.queue.needs_acceptance }} 项 ·
        提交人账号已停用 {{ health.queue.inactive_submitters }} 项
      </p>
      <p v-if="health.failures.consecutive" class="sd-small ops-reasons">
        {{
          Object.entries(health.failures.by_reason)
            .map(([key, count]) => `${reasons[key] || "其他原因"} ${count} 次`)
            .join(" · ")
        }}
      </p>
      <div class="ops-capabilities">
        <span
          v-for="[key, label] in capabilities"
          :key="key"
          class="ops-capability"
          ><span
            :class="
              health.node.capabilities[key] === true
                ? 'ops-ok'
                : health.node.capabilities[key] === false
                  ? 'ops-bad'
                  : 'ops-unknown'
            "
          ></span
          >{{ label }} ·
          {{
            health.node.capabilities[key] === true
              ? "就绪"
              : health.node.capabilities[key] === false
                ? "异常"
                : "未知"
          }}</span
        >
      </div>
      <p class="sd-small sd-muted ops-note">
        节点活动和依赖检查反映执行环境状态，具体工单仍以实际查询结果为准。
      </p>
    </section>
    <div class="ops-columns">
      <section class="ops-section" aria-labelledby="ops-storage-heading">
        <div class="ops-section-heading">
          <div>
            <span>02</span>
            <h2 id="ops-storage-heading">附件空间</h2>
          </div>
          <span class="sd-small sd-muted"
            >{{ health.storage.count }} 个附件</span
          >
        </div>
        <p class="ops-storage-total">
          <strong>{{ bytes(health.storage.used_bytes) }}</strong
          ><span class="sd-muted">
            / {{ limit(health.storage.limits.global_bytes) }}</span
          >
        </p>
        <div
          v-if="health.storage.limits.global_bytes"
          class="ops-progress"
          role="progressbar"
          aria-label="平台附件容量"
          :aria-valuenow="Math.round(storagePercent)"
          aria-valuemin="0"
          aria-valuemax="100"
        >
          <span :style="{ width: storagePercent + '%' }"></span>
        </div>
        <dl class="ops-list">
          <div>
            <dt>单工单最大用量 / 上限</dt>
            <dd>
              {{ bytes(health.storage.largest_ticket_bytes) }} /
              {{ limit(health.storage.limits.ticket_bytes) }}
            </dd>
          </div>
          <div>
            <dt>单提交人最大用量 / 上限</dt>
            <dd>
              {{ bytes(health.storage.largest_owner_bytes) }} /
              {{ limit(health.storage.limits.owner_bytes) }}
            </dd>
          </div>
          <div>
            <dt>磁盘剩余 / 最低预留</dt>
            <dd>
              {{ bytes(health.storage.free_bytes) }} /
              {{ bytes(health.storage.limits.min_free_bytes) }}
            </dd>
          </div>
        </dl>
        <p class="sd-small sd-muted ops-note">
          提交人容量包含其工单中的全部附件；达到上限时文字沟通仍可继续。
        </p>
      </section>
      <section class="ops-section" aria-labelledby="ops-backup-heading">
        <div class="ops-section-heading">
          <div>
            <span>03</span>
            <h2 id="ops-backup-heading">本机备份</h2>
          </div>
          <span
            class="ops-chip"
            :class="{
              'ops-chip-alert': ['failed', 'stale'].includes(
                health.backup.status,
              ),
              'ops-chip-neutral': ['unknown', 'running'].includes(
                health.backup.status,
              ),
            }"
            >{{ backupLabels[health.backup.status] }}</span
          >
        </div>
        <dl class="ops-list">
          <div>
            <dt>最近成功备份</dt>
            <dd>{{ timeLabel(health.backup.last_success_at) }}</dd>
          </div>
          <div>
            <dt>最近恢复演练</dt>
            <dd>{{ timeLabel(health.backup.last_drill_at) }}</dd>
          </div>
          <div>
            <dt>演练结果</dt>
            <dd
              :class="{ 'ops-reasons': health.backup.last_drill_ok === false }"
            >
              {{
                health.backup.last_drill_ok === null
                  ? "尚无结果"
                  : health.backup.last_drill_ok
                    ? "通过"
                    : "未通过"
              }}
            </dd>
          </div>
          <div>
            <dt>运维状态更新时间</dt>
            <dd>{{ timeLabel(health.backup.reported_at) }}</dd>
          </div>
        </dl>
        <p class="sd-small sd-muted ops-note">
          备份与恢复验证由管理员在隔离环境执行。
        </p>
      </section>
    </div>
  </template>
</template>

<style scoped>
.ops-heading {
  align-items: end;
  gap: 18px;
  flex-wrap: wrap;
}
.ops-heading h1 {
  margin: 4px 0 6px;
}
.ops-heading p {
  margin: 0;
}
.ops-kicker {
  font-size: 10px;
  letter-spacing: 0.14em;
  color: var(--sd-muted);
}
.ops-overview {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 15px 18px;
  margin: 25px 0 18px;
  border: 1px solid var(--sd-line);
  border-radius: 9px;
  background: var(--sd-tint);
  font-size: 13px;
}
.ops-dot,
.ops-capability span {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 8px;
  border-radius: 50%;
  background: #398062;
}
.ops-attention .ops-dot,
.ops-bad {
  background: #b24a35 !important;
}
.ops-pending .ops-dot {
  background: #a39b8e;
}
.ops-unknown {
  background: #a39b8e !important;
}
.ops-section {
  border: 1px solid var(--sd-line);
  border-radius: 10px;
  background: var(--sd-paper);
  padding: 22px;
  min-width: 0;
}
.ops-section-heading,
.ops-section-heading > div {
  display: flex;
  align-items: center;
  gap: 10px;
}
.ops-section-heading {
  justify-content: space-between;
  margin-bottom: 22px;
}
.ops-section-heading h2 {
  margin: 0;
  font-size: 17px;
}
.ops-section-heading > div > span {
  font-size: 11px;
  color: var(--sd-accent);
}
.ops-chip {
  border-radius: 99px;
  padding: 5px 10px;
  background: #e4f1e9;
  color: #315f49;
  font-size: 11px;
  white-space: nowrap;
}
.ops-chip-alert {
  background: #f7e4dd;
  color: #a3442d;
}
.ops-chip-neutral {
  background: var(--sd-tint);
  color: var(--sd-muted);
}
.ops-grid {
  display: grid;
  gap: 20px;
}
.ops-ai-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.ops-ai-grid > div {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.ops-label,
.ops-list dt {
  color: var(--sd-muted);
  font-size: 12px;
}
.ops-ai-grid strong {
  font-size: 19px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.ops-ai-grid strong small {
  font-size: 11px;
  font-weight: 400;
}
.ops-ai-grid > div > small {
  font-size: 11px;
  color: var(--sd-muted);
}
.ops-detail-row,
.ops-capabilities {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  border-top: 1px solid var(--sd-line);
  padding-top: 15px;
  margin-top: 19px;
  font-size: 12px;
  color: var(--sd-muted);
}
.ops-detail-row strong {
  color: var(--sd-ink);
  font-weight: 500;
}
.ops-reasons {
  color: #a3442d;
  margin: 10px 0 0;
}
.ops-capability {
  white-space: nowrap;
}
.ops-note {
  margin: 16px 0 0;
  line-height: 1.7;
}
.ops-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  margin-top: 18px;
}
.ops-storage-total {
  margin: 0 0 12px;
  font-size: 12px;
}
.ops-storage-total strong {
  font-size: 24px;
  color: var(--sd-ink);
}
.ops-progress {
  height: 6px;
  border-radius: 8px;
  background: var(--sd-tint);
  overflow: hidden;
}
.ops-progress span {
  display: block;
  height: 100%;
  background: var(--sd-accent);
}
.ops-list {
  margin: 16px 0 0;
}
.ops-list > div {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 11px 0;
  border-bottom: 1px solid var(--sd-line);
}
.ops-list > div:last-child {
  border-bottom: 0;
}
.ops-list dd {
  margin: 0;
  font-size: 12px;
  text-align: right;
  overflow-wrap: anywhere;
}
@media (max-width: 900px) {
  .ops-ai-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .ops-columns {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 480px) {
  .ops-section {
    padding: 17px;
  }
  .ops-ai-grid > div:first-child strong {
    font-size: 15px;
  }
  .ops-list > div {
    flex-direction: column;
    gap: 5px;
  }
  .ops-list dd {
    text-align: left;
  }
}
</style>
