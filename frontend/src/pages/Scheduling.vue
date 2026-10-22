<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from "vue";
import { useRoute, onBeforeRouteLeave } from "vue-router";
import { api, post, ApiError } from "../api";
import { refreshStats } from "../state";
import {
  today,
  type Page,
  type QueueTicket,
  type ExpediteRequest,
  type SchedulePreview,
  type ScheduleBatchResult,
} from "../types";
import Icon from "../components/Icon.vue";
import Status from "../components/Status.vue";
const route = useRoute();
const queue = ref<QueueTicket[]>([]),
  queueCount = ref(0),
  queuePage = ref(1),
  search = ref("");
const requests = ref<ExpediteRequest[]>([]),
  requestCount = ref(0),
  requestPage = ref(1);
const source = ref<ExpediteRequest | null>(null),
  rejecting = ref<ExpediteRequest | null>(null),
  rejectionNote = ref("");
const drafts = ref<Record<number, { ticket: QueueTicket; date: string }>>({}),
  reason = ref("");
const preview = ref<SchedulePreview | null>(null),
  result = ref<ScheduleBatchResult | null>(null);
const error = ref(""),
  loading = ref(false),
  requestError = ref(""),
  requestsLoading = ref(false),
  busy = ref(false),
  stale = ref(false);
let requestKey = crypto.randomUUID(),
  queueSequence = 0,
  requestSequence = 0,
  searchTimer: ReturnType<typeof setTimeout>;
const changes = computed(() =>
  Object.values(drafts.value)
    .sort((a, b) => a.ticket.id - b.ticket.id)
    .map((row) => ({
      ticket_id: row.ticket.id,
      version: row.ticket.version,
      estimated_completion_date: row.date || null,
    })),
);
const content = computed(() =>
  JSON.stringify({
    changes: changes.value,
    reason: reason.value,
    request: source.value?.id,
    request_version: source.value?.version,
  }),
);
watch(
  content,
  () => {
    preview.value = null;
    result.value = null;
    requestKey = crypto.randomUUID();
  },
  { flush: "sync" },
);
const requestPages = computed(() =>
  Math.max(1, Math.ceil(requestCount.value / 20)),
);
const queuePages = computed(() =>
  Math.max(1, Math.ceil(queueCount.value / 20)),
);
const kindLabels = {
  advanced: "提前",
  delayed: "延期",
  scheduled: "首次排期",
  cleared: "待评估",
};
function payload() {
  return {
    request_key: requestKey,
    changes: changes.value,
    reason: reason.value,
    ...(source.value
      ? {
          expedite_request_id: source.value.id,
          expedite_request_version: source.value.version,
        }
      : {}),
  };
}
async function loadRequests(): Promise<void> {
  const id = ++requestSequence;
  requestError.value = "";
  requestsLoading.value = true;
  try {
    const response = await api<Page<ExpediteRequest>>(
      `/expedite-requests?status=pending&page=${requestPage.value}`,
    );
    if (id !== requestSequence) return;
    requests.value = response.results;
    requestCount.value = response.count;
  } catch (e) {
    if (id !== requestSequence) return;
    if (e instanceof ApiError && e.status === 404 && requestPage.value > 1) {
      requestPage.value = 1;
      await loadRequests();
    } else requestError.value = (e as Error).message;
  } finally {
    if (id === requestSequence) requestsLoading.value = false;
  }
}
async function loadQueue(): Promise<void> {
  const id = ++queueSequence;
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({
      page: String(queuePage.value),
      q: search.value,
    });
    if (source.value)
      params.set("focus_ticket", String(source.value.ticket_id));
    const response = await api<Page<QueueTicket>>(
      "/scheduling/queue?" + params,
    );
    if (id !== queueSequence) return;
    queue.value = response.results;
    queueCount.value = response.count;
  } catch (e) {
    if (id !== queueSequence) return;
    if (e instanceof ApiError && e.status === 404 && queuePage.value > 1) {
      queuePage.value = 1;
      await loadQueue();
    } else error.value = (e as Error).message;
  } finally {
    if (id === queueSequence) loading.value = false;
  }
}
watch(
  search,
  () => {
    clearTimeout(searchTimer);
    queueSequence++;
    queuePage.value = 1;
    searchTimer = setTimeout(() => void loadQueue(), 300);
  },
  { flush: "sync" },
);
function setDate(ticket: QueueTicket, date: string) {
  if (date === (ticket.estimated_completion_date || ""))
    delete drafts.value[ticket.id];
  else
    drafts.value[ticket.id] = {
      ticket: drafts.value[ticket.id]?.ticket || ticket,
      date,
    };
  error.value = "";
  stale.value = false;
}
function restore(ticket: QueueTicket) {
  delete drafts.value[ticket.id];
}
async function chooseRequest(request: ExpediteRequest) {
  if (busy.value) return;
  source.value = request;
  rejecting.value = null;
  rejectionNote.value = "";
  clearTimeout(searchTimer);
  search.value = "";
  clearTimeout(searchTimer);
  queuePage.value = 1;
  await loadQueue();
  const ticket = queue.value.find((t) => t.id === request.ticket_id);
  if (!ticket) {
    error.value = "该工单已不在开发队列，请刷新后重新评估。";
    return;
  }
  setDate(ticket, request.requested_date);
  if (!reason.value.trim()) reason.value = "根据业务优先级调整本批次开发排期。";
  document
    .querySelector(".sched-queue")
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
}
async function refresh() {
  if (
    changes.value.length &&
    !window.confirm("刷新会清除尚未保存的排期调整，确定刷新？")
  )
    return;
  drafts.value = {};
  source.value = null;
  preview.value = null;
  stale.value = false;
  await Promise.all([loadQueue(), loadRequests(), refreshStats()]);
}
async function makePreview() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  stale.value = false;
  const snapshot = content.value;
  try {
    const response = await post<SchedulePreview>(
      "/scheduling/preview",
      payload(),
    );
    if (snapshot === content.value) {
      preview.value = response;
      await nextTick();
      document.querySelector<HTMLElement>(".sched-preview")?.focus();
      document
        .querySelector(".sched-preview")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (e) {
    error.value = (e as Error).message;
    stale.value = e instanceof ApiError && e.status === 409;
  } finally {
    busy.value = false;
  }
}
async function save() {
  if (!preview.value || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const response = await post<ScheduleBatchResult>("/scheduling/commit", {
      ...payload(),
      preview_token: preview.value.preview_token,
    });
    drafts.value = {};
    reason.value = "";
    source.value = null;
    preview.value = null;
    result.value = response;
    stale.value = false;
    requestKey = crypto.randomUUID();
    await Promise.all([loadQueue(), loadRequests(), refreshStats()]);
  } catch (e) {
    error.value = (e as Error).message;
    stale.value = e instanceof ApiError && e.status === 409;
    if (e instanceof ApiError && [400, 409].includes(e.status))
      preview.value = null;
  } finally {
    busy.value = false;
  }
}
async function reject() {
  if (!rejecting.value || busy.value) return;
  busy.value = true;
  error.value = "";
  const item = rejecting.value;
  try {
    await post(`/expedite-requests/${item.id}/decision`, {
      version: item.version,
      ticket_version: item.ticket_version,
      reason: rejectionNote.value,
    });
    delete drafts.value[item.ticket_id];
    if (source.value?.id === item.id) source.value = null;
    rejecting.value = null;
    rejectionNote.value = "";
    await Promise.all([loadRequests(), loadQueue(), refreshStats()]);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function turnQueue(page: number) {
  queuePage.value = page;
  await loadQueue();
}
async function turnRequests(page: number) {
  requestPage.value = page;
  await loadRequests();
}
function warnUnsaved(event: BeforeUnloadEvent) {
  if (changes.value.length || rejectionNote.value) {
    event.preventDefault();
    event.returnValue = "";
  }
}
onMounted(async () => {
  window.addEventListener("beforeunload", warnUnsaved);
  await loadRequests();
  const id = String(route.query.request || "");
  if (/^[1-9]\d*$/.test(id)) {
    try {
      const request = await api<ExpediteRequest>("/expedite-requests/" + id);
      if (request.status === "pending") {
        await chooseRequest(request);
        return;
      }
      error.value = "该加急申请已处理，可以查看当前开发排期。";
    } catch (e) {
      error.value = (e as Error).message;
    }
  }
  await loadQueue();
});
onUnmounted(() => {
  queueSequence++;
  requestSequence++;
  clearTimeout(searchTimer);
  window.removeEventListener("beforeunload", warnUnsaved);
});
onBeforeRouteLeave(
  () =>
    (!changes.value.length && !rejectionNote.value) ||
    window.confirm("排期调整尚未保存，确定离开？"),
);
</script>
<template>
  <div class="sd-scheduling">
    <RouterLink to="/calendar?mode=estimated" class="sd-quiet sd-breadcrumb"
      ><Icon name="arrow-left" />返回开发日历</RouterLink
    >
    <div class="sd-heading">
      <div>
        <h1>开发排期</h1>
        <p class="sched-subtitle">一个开发队列，统一评估交付安排。</p>
      </div>
      <button class="sd-button" :disabled="busy || loading" @click="refresh">
        <Icon name="refresh-cw" />刷新队列
      </button>
    </div>
    <div v-if="error" class="sd-error" role="alert">
      {{ error }}<span v-if="stale">请刷新队列，重新评估后再预览。</span>
    </div>
    <div v-if="result" class="sd-notice sched-success" role="status">
      <Icon name="check" />
      <div>
        <strong>已保存 {{ result.changed_count }} 张工单的排期</strong>
        <p>
          已为
          {{ result.recipient_count }}
          位顾问生成站内提醒及钉钉汇总通知，钉钉将按队列投递。
        </p>
        <RouterLink to="/calendar?mode=estimated" class="sd-quiet"
          >查看更新后的日历<Icon name="arrow-up-right"
        /></RouterLink>
      </div>
    </div>
    <section class="sched-requests" aria-labelledby="expedite-inbox">
      <div class="sched-section-head">
        <h2 id="expedite-inbox">
          加急待评估 <small>{{ requestCount }}</small>
        </h2>
        <span class="sd-small sd-muted">先评估目标日期，再统一安排</span>
      </div>
      <p v-if="requestError" class="sd-error" role="alert">
        {{ requestError
        }}<button class="sd-quiet" @click="loadRequests">重试</button>
      </p>
      <p v-else-if="requestsLoading" class="sched-no-requests" role="status">
        正在加载加急申请…
      </p>
      <p v-else-if="!requests.length" class="sched-no-requests">
        暂无待评估的加急申请，可直接调整下方工单排期。
      </p>
      <div v-else class="sched-request-list">
        <article
          v-for="item in requests"
          :key="item.id"
          class="sched-request"
          :class="{ 'is-selected': source?.id === item.id }"
        >
          <div class="sched-request-top">
            <span class="sd-small sd-muted"
              >{{ item.ticket_number }} · {{ item.requester_name }}</span
            ><span class="sched-request-target"
              >希望 {{ item.requested_date }} 完成</span
            >
          </div>
          <h3>{{ item.ticket_title }}</h3>
          <p class="sd-small sd-muted">
            当前预计完成：{{
              item.current_estimated_completion_date || "待评估"
            }}
          </p>
          <p class="sched-request-reason">{{ item.reason }}</p>
          <div class="sd-flex">
            <button
              class="sd-button"
              :class="source?.id === item.id ? 'sd-primary' : ''"
              :disabled="busy"
              @click="chooseRequest(item)"
            >
              {{ source?.id === item.id ? "正在安排" : "安排排期" }}</button
            ><button
              class="sd-quiet"
              :disabled="busy"
              @click="
                rejecting = item;
                rejectionNote = '';
              "
            >
              无法安排
            </button>
          </div>
        </article>
      </div>
      <div v-if="requestPages > 1" class="sched-pager">
        <button
          class="sd-button"
          :disabled="requestPage <= 1 || busy"
          @click="turnRequests(requestPage - 1)"
        >
          上一页申请</button
        ><span>{{ requestPage }} / {{ requestPages }}</span
        ><button
          class="sd-button"
          :disabled="requestPage >= requestPages || busy"
          @click="turnRequests(requestPage + 1)"
        >
          下一页申请
        </button>
      </div>
      <form v-if="rejecting" class="sched-rejection" @submit.prevent="reject">
        <h3>暂时无法安排 · {{ rejecting.ticket_number }}</h3>
        <label for="rejection-note"
          >说明原因（将通知申请人）<textarea
            id="rejection-note"
            v-model="rejectionNote"
            required
            maxlength="2000"
            rows="3"
            :disabled="busy"
          ></textarea>
        </label>
        <div class="sd-flex">
          <button class="sd-button sd-primary" :disabled="busy">
            确认未通过并通知</button
          ><button
            type="button"
            class="sd-button"
            :disabled="busy"
            @click="
              rejecting = null;
              rejectionNote = '';
            "
          >
            取消
          </button>
        </div>
      </form>
    </section>
    <section class="sched-queue" aria-labelledby="schedule-queue">
      <div class="sched-section-head">
        <div>
          <h2 id="schedule-queue">
            开发队列 <small>{{ queueCount }}</small>
          </h2>
          <p class="sd-small sd-muted">
            待受理及处理中工单。逐项填写新日期，未修改的工单保留原排期。
          </p>
        </div>
        <span v-if="changes.length" class="sched-draft-count"
          >{{ changes.length }} 项待预览</span
        >
      </div>
      <p v-if="source" class="sched-source">
        正在处理 {{ source.ticket_number }} 的加急申请，目标日期
        {{ source.requested_date }}。<button
          class="sd-quiet"
          :disabled="busy"
          @click="source = null"
        >
          取消关联
        </button>
      </p>
      <label class="sd-search sched-search"
        ><Icon name="search" /><input
          v-model="search"
          type="search"
          placeholder="搜索队列中的工单编号或标题"
          aria-label="搜索开发队列"
          :disabled="busy"
      /></label>
      <p v-if="loading" class="sd-small sd-muted" role="status">
        正在加载队列…
      </p>
      <table v-if="queue.length" class="sched-table" :aria-busy="loading">
        <thead>
          <tr>
            <th>工单 / 提交人</th>
            <th>状态</th>
            <th>原预计完成</th>
            <th>调整为</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="ticket in queue"
            :key="ticket.id"
            :class="{ 'is-edited': drafts[ticket.id] }"
          >
            <td>
              <RouterLink :to="'/tickets/' + ticket.id">{{
                ticket.title
              }}</RouterLink
              ><small>{{ ticket.number }} · {{ ticket.owner_name }}</small
              ><small v-if="ticket.pending_expedite" class="sched-expedite-note"
                >加急申请 · 希望
                {{ ticket.pending_expedite.requested_date }} 完成</small
              >
            </td>
            <td data-label="状态"><Status :value="ticket.status" /></td>
            <td data-label="原预计完成">
              <time>{{ ticket.estimated_completion_date || "待评估" }}</time>
            </td>
            <td data-label="调整为">
              <div class="sched-date-control">
                <input
                  type="date"
                  :aria-label="ticket.number + ' 调整为'"
                  :value="
                    drafts[ticket.id]?.date ??
                    ticket.estimated_completion_date ??
                    ''
                  "
                  :min="
                    ticket.estimated_completion_date &&
                    ticket.estimated_completion_date < today()
                      ? ticket.estimated_completion_date
                      : today()
                  "
                  max="9998-12-31"
                  :disabled="busy || loading"
                  @input="
                    setDate(ticket, ($event.target as HTMLInputElement).value)
                  "
                /><button
                  v-if="drafts[ticket.id]"
                  class="sd-quiet"
                  :disabled="busy"
                  @click="restore(ticket)"
                >
                  恢复</button
                ><span v-else class="sd-small sd-muted">保持不变</span>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="!loading" class="sched-empty">
        {{
          search
            ? "没有匹配的工单，可清空搜索或切换页码。"
            : "当前没有待安排的开发工单。"
        }}
      </p>
      <div v-if="queuePages > 1" class="sched-pager">
        <button
          class="sd-button"
          :disabled="queuePage <= 1 || loading || busy"
          @click="turnQueue(queuePage - 1)"
        >
          上一页</button
        ><span>{{ queuePage }} / {{ queuePages }}</span
        ><button
          class="sd-button"
          :disabled="queuePage >= queuePages || loading || busy"
          @click="turnQueue(queuePage + 1)"
        >
          下一页
        </button>
      </div>
    </section>
    <form class="sched-plan-form" @submit.prevent="makePreview">
      <label for="batch-reason"
        >本批次调整说明<small
          >将展示给受影响的顾问，请填写可共享的排期原因。</small
        ><textarea
          id="batch-reason"
          v-model="reason"
          required
          maxlength="2000"
          rows="3"
          :disabled="busy"
          placeholder="例如：根据业务优先级调整开发、自测和交付安排。"
        ></textarea>
      </label>
      <div class="sd-flex">
        <button
          class="sd-button sd-primary"
          :disabled="busy || !changes.length || !reason.trim()"
        >
          {{
            busy ? "正在处理…" : "预览 " + changes.length + " 项调整"
          }}</button
        ><span class="sd-small sd-muted">留空的新日期表示恢复待评估。</span>
      </div>
    </form>
    <section
      v-if="preview"
      class="sched-preview"
      tabindex="-1"
      aria-labelledby="schedule-preview"
    >
      <div class="sched-section-head">
        <h2 id="schedule-preview">确认本批次调整</h2>
        <span class="sd-small sd-muted">尚未保存</span>
      </div>
      <div class="sched-preview-summary">
        <span
          ><strong>{{ preview.changed_count }}</strong> 张工单</span
        ><span v-if="preview.advanced_count"
          >提前 {{ preview.advanced_count }}</span
        ><span v-if="preview.delayed_count"
          >延期 {{ preview.delayed_count }}</span
        ><span v-if="preview.scheduled_count"
          >首次排期 {{ preview.scheduled_count }}</span
        ><span v-if="preview.cleared_count"
          >恢复待评估 {{ preview.cleared_count }}</span
        ><span>通知 {{ preview.recipient_count }} 位顾问</span>
      </div>
      <table class="sched-table sched-preview-table">
        <thead>
          <tr>
            <th>工单 / 提交人</th>
            <th>调整前</th>
            <th>调整后</th>
            <th>变化</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in preview.changes" :key="row.ticket_id">
            <td>
              {{ row.title
              }}<small>{{ row.number }} · {{ row.owner_name }}</small>
            </td>
            <td data-label="调整前">{{ row.previous_date || "待评估" }}</td>
            <td data-label="调整后">
              <strong>{{ row.new_date || "待评估" }}</strong>
            </td>
            <td data-label="变化">
              <span :class="row.kind === 'delayed' ? 'sched-delay' : ''">{{
                kindLabels[row.kind]
              }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="sched-public-reason">调整说明：{{ preview.reason }}</p>
      <p class="sd-small sd-muted">
        每张改期工单会记录日期及原因；同一顾问的多张工单合并发送一条钉钉提醒。
      </p>
      <button
        class="sd-button sd-primary sched-confirm"
        :disabled="busy"
        @click="save"
      >
        {{
          busy
            ? "正在保存…"
            : "确认保存并通知 " + preview.recipient_count + " 位顾问"
        }}
      </button>
    </section>
  </div>
</template>
<style>
#sap-desk-design .sd-scheduling h2 {
  font-size: 18px;
  font-weight: 500;
}
#sap-desk-design .sd-scheduling h2 small {
  font-size: 12px;
  color: var(--sd-muted);
  margin-left: 7px;
}
#sap-desk-design .sched-subtitle {
  margin-top: 9px;
  color: var(--sd-muted);
  font-size: 12px;
}
#sap-desk-design .sched-section-head {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
#sap-desk-design .sched-section-head p {
  margin-top: 8px;
}
#sap-desk-design .sched-requests {
  border: 1px solid var(--sd-line);
  border-radius: 9px;
  padding: 21px;
  margin: 25px 0 30px;
}
#sap-desk-design .sched-request-list {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 330px), 1fr));
}
#sap-desk-design .sched-request {
  border: 1px solid var(--sd-line);
  border-radius: 7px;
  padding: 16px;
}
#sap-desk-design .sched-request.is-selected {
  border-color: var(--sd-accent);
  background: var(--sd-tint);
}
#sap-desk-design .sched-request-top {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 9px;
}
#sap-desk-design .sched-request-target {
  font-size: 11px;
  color: var(--sd-accent);
}
#sap-desk-design .sched-request h3 {
  font-size: 14px;
  line-height: 1.7;
  margin-bottom: 6px;
}
#sap-desk-design .sched-request-reason {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 120px;
  overflow-y: auto;
  font-size: 12px;
  line-height: 1.8;
  margin: 10px 0 14px;
}
#sap-desk-design .sched-no-requests {
  font-size: 12px;
  color: var(--sd-muted);
}
#sap-desk-design .sched-rejection {
  display: grid;
  gap: 12px;
  margin-top: 17px;
  padding-top: 17px;
  border-top: 1px solid var(--sd-line);
}
#sap-desk-design .sched-rejection label {
  display: grid;
  gap: 8px;
  font-size: 12px;
}
#sap-desk-design .sched-search {
  display: block;
  margin: 17px 0;
  max-width: 450px;
}
#sap-desk-design .sched-draft-count {
  font-size: 12px;
  color: var(--sd-accent);
}
#sap-desk-design .sched-source {
  padding: 12px;
  border-radius: 6px;
  background: var(--sd-tint);
  font-size: 12px;
  line-height: 1.7;
}
#sap-desk-design .sched-source button {
  margin-left: 8px;
}
#sap-desk-design .sched-table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 12px;
}
#sap-desk-design .sched-table th {
  text-align: left;
  color: var(--sd-muted);
  font-weight: 400;
  padding: 12px 10px;
  border-bottom: 1px solid var(--sd-line);
}
#sap-desk-design .sched-table th:first-child {
  width: 38%;
}
#sap-desk-design .sched-table th:nth-child(2) {
  width: 16%;
}
#sap-desk-design .sched-table th:nth-child(3) {
  width: 19%;
}
#sap-desk-design .sched-table th:nth-child(4) {
  width: 27%;
}
#sap-desk-design .sched-table td {
  padding: 17px 10px;
  border-bottom: 1px solid var(--sd-line);
  vertical-align: middle;
  overflow-wrap: anywhere;
}
#sap-desk-design .sched-table td:first-child {
  line-height: 1.7;
}
#sap-desk-design .sched-table td:first-child a {
  color: var(--sd-ink);
  font-weight: 500;
  text-decoration: none;
}
#sap-desk-design .sched-table td:first-child small {
  display: block;
  color: var(--sd-muted);
  margin-top: 5px;
  font-size: 11px;
}
#sap-desk-design .sched-table tr.is-edited td {
  background: var(--sd-tint);
}
#sap-desk-design .sched-table td:first-child .sched-expedite-note {
  color: var(--sd-accent);
}
#sap-desk-design .sched-date-control {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
#sap-desk-design .sched-date-control input {
  min-width: 0;
  width: 160px;
  max-width: 100%;
  font-size: 12px;
  padding: 8px;
}
#sap-desk-design .sched-date-control button {
  font-size: 11px;
}
#sap-desk-design .sched-pager {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
  margin-top: 15px;
  font-size: 12px;
}
#sap-desk-design .sched-empty {
  text-align: center;
  padding: 35px;
  font-size: 12px;
  color: var(--sd-muted);
}
#sap-desk-design .sched-plan-form {
  display: grid;
  gap: 15px;
  margin: 28px 0;
}
#sap-desk-design .sched-plan-form label {
  display: grid;
  gap: 9px;
  font-size: 13px;
}
#sap-desk-design .sched-plan-form label small {
  font-size: 11px;
  color: var(--sd-muted);
}
#sap-desk-design .sched-plan-form textarea {
  width: 100%;
}
#sap-desk-design .sched-preview {
  padding: 23px;
  border: 1px solid var(--sd-accent);
  border-radius: 9px;
  background: var(--sd-paper);
  margin-bottom: 20px;
}
#sap-desk-design .sched-preview-summary {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  font-size: 12px;
  padding: 0 0 17px;
}
#sap-desk-design .sched-preview-summary strong {
  font-size: 20px;
  font-weight: 500;
}
#sap-desk-design .sched-delay {
  color: var(--sd-red);
}
#sap-desk-design .sched-public-reason {
  font-size: 12px;
  line-height: 1.8;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 17px 0 10px;
}
#sap-desk-design .sched-confirm {
  margin-top: 17px;
}
#sap-desk-design .sched-success {
  margin: 18px 0;
  align-items: flex-start;
}
#sap-desk-design .sched-success p {
  font-size: 12px;
  line-height: 1.8;
  margin: 7px 0;
}
@media (max-width: 700px) {
  #sap-desk-design .sched-requests,
  #sap-desk-design .sched-preview {
    padding: 15px;
  }
  #sap-desk-design .sched-table,
  #sap-desk-design .sched-table tbody {
    display: block;
  }
  #sap-desk-design .sched-table thead {
    display: none;
  }
  #sap-desk-design .sched-table tr {
    display: grid;
    grid-template-columns: 1fr 1fr;
    border-bottom: 1px solid var(--sd-line);
    padding: 12px;
    gap: 13px;
  }
  #sap-desk-design .sched-table td {
    padding: 0;
    border: 0;
  }
  #sap-desk-design .sched-table td:first-child {
    grid-column: 1/-1;
  }
  #sap-desk-design .sched-table:not(.sched-preview-table) td:last-child {
    grid-column: 1/-1;
  }
  #sap-desk-design .sched-table td[data-label]::before {
    content: attr(data-label);
    display: block;
    font-size: 10px;
    color: var(--sd-muted);
    margin-bottom: 6px;
  }
  #sap-desk-design .sched-table tr.is-edited {
    background: var(--sd-tint);
  }
  #sap-desk-design .sched-confirm {
    width: 100%;
    justify-content: center;
  }
  #sap-desk-design .sched-table td:first-child {
    font-size: 13px;
  }
}
</style>
