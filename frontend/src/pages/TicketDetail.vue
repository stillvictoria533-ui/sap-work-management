<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from "vue";
import { useRoute, onBeforeRouteLeave, onBeforeRouteUpdate } from "vue-router";
import { readTicketListState, ticketListQuery } from "../ticketListState";
import { api, post, download, ApiError } from "../api";
import { user, refreshStats } from "../state";
import {
  priorities,
  systemLabel,
  eventStatus,
  dateTime,
  today,
  fileSize,
  type Ticket,
  type Attachment,
} from "../types";
import Status from "../components/Status.vue";
import ExpeditePanel from "../components/ExpeditePanel.vue";
import ScheduleDate from "../components/ScheduleDate.vue";
import { dateDifference } from "../schedule";
import FilePicker from "../components/FilePicker.vue";
import Icon from "../components/Icon.vue";
const route = useRoute(),
  ticket = ref<Ticket | null>(null),
  body = ref(""),
  editingSchedule = ref(false),
  expediteDirty = ref(false),
  estimateDate = ref(""),
  estimateBaseline = ref(""),
  estimateReason = ref(""),
  startEstimate = ref(""),
  startEstimateBaseline = ref(""),
  selectedAction = ref(""),
  reason = ref(""),
  files = ref<File[]>([]),
  busy = ref(false),
  loading = ref(false),
  error = ref(""),
  conflict = ref(false),
  newVersion = ref(0),
  pickerKey = ref(0),
  success = ref("");
const startEstimateDirty = computed(
  () => startEstimate.value !== startEstimateBaseline.value,
);
const estimateDirty = computed(
  () => estimateDate.value !== estimateBaseline.value,
);
const startEstimateConflict = computed(
  () =>
    selectedAction.value === "start" &&
    startEstimateDirty.value &&
    (ticket.value?.estimated_completion_date || "") !==
      startEstimateBaseline.value &&
    (ticket.value?.estimated_completion_date || "") !== startEstimate.value,
);
const estimateConflict = computed(
  () =>
    editingSchedule.value &&
    estimateDirty.value &&
    (ticket.value?.estimated_completion_date || "") !==
      estimateBaseline.value &&
    (ticket.value?.estimated_completion_date || "") !== estimateDate.value,
);
let sequence = 0;
let mutationSequence = 0;
function resetEditors() {
  mutationSequence++;
  body.value = reason.value = estimateDate.value = estimateReason.value = "";
  selectedAction.value = startEstimate.value = success.value = error.value = "";
  startEstimateBaseline.value = estimateBaseline.value = "";
  files.value = [];
  editingSchedule.value =
    expediteDirty.value =
    busy.value =
    conflict.value =
      false;
  newVersion.value = 0;
  pickerKey.value++;
}
function currentMutation(id: number, operation: number) {
  return (
    String(route.params.id) === String(id) && operation === mutationSequence
  );
}
function beginMutation() {
  sequence++; // A GET started before this write must not overwrite its result.
  loading.value = false;
  busy.value = true;
  return ++mutationSequence;
}
async function load() {
  if (busy.value) return;
  const seq = ++sequence;
  loading.value = true;
  error.value = "";
  try {
    const t = await api<Ticket>("/tickets/" + route.params.id);
    if (seq !== sequence || (ticket.value && t.version < ticket.value.version))
      return;
    acceptUpdate(t);
    newVersion.value = 0;
    conflict.value = false;
    const last = t.events?.at(-1);
    if (last)
      await post("/tickets/" + t.id + "/read", { through_event: last.id });
    await refreshStats();
  } catch (e) {
    if (seq === sequence) error.value = (e as Error).message;
  } finally {
    if (seq === sequence) loading.value = false;
  }
}
watch(
  () => route.params.id,
  () => {
    resetEditors();
    ticket.value = null;
    void load();
  },
  { immediate: true },
);
function confirmLeave() {
  if (busy.value)
    return window.confirm("操作正在提交，确定离开？提交结果可稍后查看。");
  return (
    (!body.value &&
      !reason.value &&
      !files.value.length &&
      !editingSchedule.value &&
      !selectedAction.value &&
      !expediteDirty.value) ||
    window.confirm("内容尚未提交，确定离开？")
  );
}
onBeforeRouteLeave(confirmLeave);
onBeforeRouteUpdate(
  (to, from) => to.params.id === from.params.id || confirmLeave(),
);
let poll: ReturnType<typeof setInterval> | undefined;
let checking = false;
async function checkForUpdates() {
  if (
    !ticket.value ||
    busy.value ||
    loading.value ||
    checking ||
    (typeof document !== "undefined" && document.visibilityState === "hidden")
  )
    return;
  const id = ticket.value.id,
    seq = sequence;
  checking = true;
  try {
    const latest = await api<{ id: number; version: number }>(
      `/tickets/${id}/version`,
    );
    if (
      seq === sequence &&
      ticket.value?.id === id &&
      latest.version > ticket.value.version
    ) {
      newVersion.value = latest.version;
    }
  } catch {
    // A transient background failure must not overwrite an editor's status.
  } finally {
    checking = false;
  }
}
onMounted(() => {
  poll = setInterval(() => void checkForUpdates(), 30000);
});
onUnmounted(() => {
  sequence++;
  mutationSequence++;
  clearInterval(poll);
});
function acceptUpdate(updated: Ticket) {
  if (
    String(updated.id) === String(route.params.id) &&
    (!ticket.value || updated.version >= ticket.value.version)
  ) {
    const latest = updated.estimated_completion_date || "";
    // Rebase untouched dates; preserve actual edits and require a decision if
    // the server changed that same field. A new version alone grants no intent.
    if (!startEstimateDirty.value || startEstimate.value === latest) {
      startEstimateBaseline.value = startEstimate.value = latest;
    }
    if (!estimateDirty.value || estimateDate.value === latest) {
      estimateBaseline.value = estimateDate.value = latest;
    }
    ticket.value = updated;
  }
}
function selectAction(action: string) {
  selectedAction.value = action;
  startEstimateBaseline.value = startEstimate.value =
    ticket.value?.estimated_completion_date || "";
  editingSchedule.value = false;
  reason.value = "";
}
function resolveEstimateConflict(
  editor: "start" | "schedule",
  keepDraft: boolean,
) {
  const latest = ticket.value?.estimated_completion_date || "";
  if (editor === "start") {
    if (!keepDraft) startEstimate.value = latest;
    startEstimateBaseline.value = latest;
  } else {
    if (!keepDraft) estimateDate.value = latest;
    estimateBaseline.value = latest;
  }
  error.value = "";
}
async function submit() {
  if (!ticket.value || busy.value) return;
  const { id, version } = ticket.value;
  const submittedBody = body.value,
    submittedFiles = [...files.value];
  const operation = beginMutation();
  error.value = "";
  success.value = "";
  try {
    const data = new FormData();
    data.set("body", submittedBody);
    data.set("version", String(version));

    submittedFiles.forEach((f) => data.append("files", f));
    const updated = await post<Ticket>("/tickets/" + id + "/reply", data);
    if (!currentMutation(id, operation)) return;
    acceptUpdate(updated);

    if (body.value === submittedBody) body.value = "";
    files.value = files.value.filter((file) => !submittedFiles.includes(file));
    pickerKey.value++;
    success.value = "回复已发送，工单状态未改变。";
    await refreshStats();
  } catch (e) {
    if (!currentMutation(id, operation)) return;
    error.value = (e as Error).message;
    conflict.value = e instanceof ApiError && e.status === 409;
  } finally {
    if (currentMutation(id, operation)) busy.value = false;
  }
}
const actionLabels: Record<string, string> = {
  start: "开始处理",
  submit: "提交验收",
  complete: "直接完成",
  confirm: "确认完成",
  reject: "仍需处理",
  reopen: "重新打开",
};
const actions = computed(() => {
  const state = ticket.value?.status;
  if (state === "done") return ["reopen"];
  if (user.value?.role === "admin") {
    return state === "pending"
      ? ["start", "complete"]
      : state === "in-progress"
        ? ["submit", "complete"]
        : ["complete"];
  }
  return state === "waiting" ? ["confirm", "reject"] : [];
});
const nextStep = computed(
  () =>
    ({
      pending: "等待管理员受理",
      "in-progress": "等待管理员处理",
      waiting: "等待顾问验收",
      done: "已完成并归档；如有问题，可重新打开",
    })[ticket.value?.status || ""],
);
const needsReason = computed(() =>
  ["submit", "complete", "reject", "reopen"].includes(selectedAction.value),
);
const reasonHint = computed(
  () =>
    ({
      submit: "请说明处理结果及验收方式",
      complete: "请说明直接完成的原因，例如已在线下验收确认",
      reject: "请说明哪些问题仍需处理",
      reopen: "请说明重新打开的原因",
    })[selectedAction.value] || "可补充说明（选填）",
);
async function changeStatus() {
  if (!ticket.value || !selectedAction.value || busy.value) return;
  if (startEstimateConflict.value) {
    error.value = "排期已被更新，请先确认采用哪个日期。";
    return;
  }
  const { id, version, estimated_completion_date } = ticket.value;
  const action = selectedAction.value,
    submittedReason = reason.value,
    estimate = startEstimate.value;
  const operation = beginMutation();
  error.value = "";
  success.value = "";
  try {
    const updated = await post<Ticket>("/tickets/" + id + "/status", {
      action,
      version,
      reason: submittedReason,
      ...(action === "start" &&
      startEstimateDirty.value &&
      estimate !== (estimated_completion_date || "")
        ? { estimated_completion_date: estimate || null }
        : {}),
    });
    if (!currentMutation(id, operation)) return;
    acceptUpdate(updated);
    if (
      selectedAction.value === action &&
      reason.value === submittedReason &&
      startEstimate.value === estimate
    ) {
      selectedAction.value = "";
      reason.value = "";
    }
    success.value = "状态已更新并记录在时间线中。";
    await refreshStats();
  } catch (e) {
    if (!currentMutation(id, operation)) return;
    error.value = (e as Error).message;
    conflict.value = e instanceof ApiError && e.status === 409;
  } finally {
    if (currentMutation(id, operation)) busy.value = false;
  }
}
const aiFollowupMessage = computed(() => {
  const status = ticket.value?.ai_followup;
  return status?.enabled &&
    ["manual_required", "retry_wait"].includes(status.state)
    ? status.message
    : "";
});
const calendarReturn = computed(() =>
  route.query.from === "calendar"
    ? {
        path: "/calendar",
        query: {
          mode:
            route.query.calendar_mode === "estimated"
              ? "estimated"
              : "submitted",
          month: String(route.query.calendar_month || today().slice(0, 7)),
          date: String(route.query.calendar_date || today()),
          page: String(route.query.calendar_page || "1"),
        },
      }
    : null,
);
const estimateDelay = computed(() =>
  ticket.value?.estimated_completion_date && ticket.value.due_date
    ? dateDifference(
        ticket.value.estimated_completion_date,
        ticket.value.due_date,
      )
    : 0,
);
const scheduleReasonRequired = computed(
  () =>
    !!ticket.value?.estimated_completion_date ||
    !!ticket.value?.estimate_needs_review,
);
const startReasonRequired = computed(
  () =>
    selectedAction.value === "start" &&
    !!ticket.value?.estimated_completion_date &&
    startEstimate.value !== ticket.value.estimated_completion_date,
);
function editSchedule() {
  estimateBaseline.value = estimateDate.value =
    ticket.value?.estimated_completion_date || "";
  estimateReason.value = "";
  editingSchedule.value = true;
}
async function saveSchedule() {
  if (!ticket.value || busy.value) return;
  if (estimateConflict.value) {
    error.value = "排期已被更新，请先确认采用哪个日期。";
    return;
  }
  const { id, version } = ticket.value;
  const date = estimateDate.value,
    submittedReason = estimateReason.value;
  const operation = beginMutation();
  error.value = "";
  success.value = "";
  try {
    const updated = await post<Ticket>("/tickets/" + id + "/schedule", {
      estimated_completion_date: date || null,
      reason: submittedReason,
      version,
    });
    if (!currentMutation(id, operation)) return;
    acceptUpdate(updated);
    if (
      estimateDate.value === date &&
      estimateReason.value === submittedReason
    ) {
      editingSchedule.value = false;
      estimateReason.value = "";
    }
    success.value = "开发排期已保存。";
    await refreshStats();
  } catch (e) {
    if (!currentMutation(id, operation)) return;
    error.value = (e as Error).message;
    conflict.value = e instanceof ApiError && e.status === 409;
  } finally {
    if (currentMutation(id, operation)) busy.value = false;
  }
}
async function getFile(f: Attachment) {
  const id = route.params.id;
  try {
    await download(f.url, f.original_name);
  } catch (e) {
    if (route.params.id === id) error.value = (e as Error).message;
  }
}
</script>
<template>
  <div class="sd-detail-toolbar">
    <RouterLink
      class="sd-quiet sd-breadcrumb"
      :to="
        calendarReturn || {
          path: '/tickets',
          query: ticketListQuery(readTicketListState(route.query)),
        }
      "
      ><Icon name="arrow-left" />{{
        calendarReturn ? "返回日历" : "返回列表"
      }}</RouterLink
    >
    <div v-if="ticket" class="sd-detail-refresh-group">
      <span id="sd-refresh-hint" class="sd-small sd-muted">保留草稿</span>
      <button
        type="button"
        class="sd-quiet sd-detail-refresh"
        :disabled="busy || loading"
        :aria-busy="loading"
        aria-describedby="sd-refresh-hint"
        title="刷新工单，保留当前页面未提交的草稿"
        @click="load"
      >
        <Icon name="refresh-cw" :class="{ 'is-loading': loading }" />
        {{ loading ? "刷新中…" : "刷新工单" }}
      </button>
    </div>
  </div>
  <p v-if="loading && !ticket" role="status" class="sd-muted">正在加载…</p>
  <p
    v-if="ticket && newVersion > ticket.version"
    role="status"
    class="sd-detail-update"
  >
    <Icon name="message-circle" />工单有新回复或变更，刷新后查看。
  </p>
  <div v-if="error" class="sd-error" role="alert">
    {{ error }}
    <button v-if="!ticket" class="sd-button" :disabled="loading" @click="load">
      重新加载工单
    </button>
  </div>
  <template v-if="ticket"
    ><div class="sd-flex">
      <span class="sd-small sd-muted">{{ ticket.number }}</span
      ><Status :value="ticket.status" />
    </div>
    <h1 class="sd-detail-title">{{ ticket.title }}</h1>
    <div class="sd-flex sd-small sd-muted">
      <span class="sd-module">{{ ticket.module }}</span
      >{{ ticket.kind }} · {{ ticket.owner_name }} 提交
    </div>
    <p v-if="aiFollowupMessage" class="sd-next" role="status">
      <strong>AI 跟进</strong> · {{ aiFollowupMessage }}
    </p>
    <section class="sd-workflow" aria-label="工单流程">
      <p class="sd-small sd-muted">待受理 → 处理中 → 待验收 → 已完成</p>
      <p class="sd-workflow-next">{{ nextStep }}</p>
      <div class="sd-flex" v-if="actions.length">
        <button
          v-for="action in actions"
          :key="action"
          type="button"
          class="sd-button"
          :class="action === 'complete' ? '' : 'sd-primary'"
          :disabled="busy || conflict || editingSchedule"
          @click="selectAction(action)"
        >
          {{ actionLabels[action] }}
        </button>
      </div>
      <form
        v-if="selectedAction"
        class="sd-status-form"
        @submit.prevent="changeStatus"
      >
        <label v-if="selectedAction === 'start'" for="start-estimate"
          >开发预计完成日期（选填）
          <input
            id="start-estimate"
            type="date"
            v-model="startEstimate"
            :min="
              ticket.estimated_completion_date &&
              ticket.estimated_completion_date < today()
                ? ticket.estimated_completion_date
                : today()
            "
            max="9998-12-31"
          />
          <span class="sd-small sd-muted"
            >预计完成开发、自测并可提交验收的日期；暂不确定可留空。</span
          >
        </label>
        <div v-if="startEstimateConflict" class="sd-error" role="alert">
          <p>
            排期已更新为
            {{ ticket.estimated_completion_date || "待评估" }}，你的日期草稿为
            {{ startEstimate || "待评估" }}。请选择后再受理。
          </p>
          <div class="sd-flex">
            <button
              type="button"
              class="sd-button"
              @click="resolveEstimateConflict('start', false)"
            >
              使用最新排期
            </button>
            <button
              type="button"
              class="sd-button"
              @click="resolveEstimateConflict('start', true)"
            >
              确认采用我的改期
            </button>
          </div>
        </div>
        <label for="status-reason"
          >{{ actionLabels[selectedAction] }} ·
          {{
            needsReason || startReasonRequired ? "说明（必填）" : "说明（选填）"
          }}</label
        >
        <textarea
          id="status-reason"
          v-model="reason"
          :required="needsReason || startReasonRequired"
          maxlength="10000"
          :placeholder="reasonHint"
        ></textarea>
        <p class="sd-small sd-muted">
          此次操作会变更状态、记录时间线并通知对方。
        </p>
        <p
          v-if="selectedAction === 'start' && ticket.ai_followup?.enabled"
          class="sd-small sd-muted"
        >
          受理后进入 AI
          分析范围；顾问补充后继续跟进。可在说明中补充目标系统、对象或单据范围。
        </p>
        <div class="sd-flex">
          <button
            class="sd-button sd-primary"
            :disabled="busy || conflict || startEstimateConflict"
          >
            {{ busy ? "正在保存…" : "确认" + actionLabels[selectedAction] }}
          </button>
          <button
            type="button"
            class="sd-button"
            :disabled="busy"
            @click="
              selectedAction = '';
              reason = '';
            "
          >
            取消
          </button>
        </div>
      </form>
      <p v-if="success" class="sd-small" role="status">{{ success }}</p>
    </section>
    <ExpeditePanel
      :key="ticket.id"
      :ticket="ticket"
      :disabled="busy || conflict || editingSchedule || !!selectedAction"
      @updated="acceptUpdate"
      @dirty="expediteDirty = $event"
      @refresh="load"
    />
    <div class="sd-detail-grid">
      <div>
        <div class="sd-section-label">需求说明</div>
        <p class="sd-description">{{ ticket.description }}</p>
        <div class="sd-section-label" style="margin-top: 28px">沟通时间线</div>
        <ol class="sd-timeline">
          <li
            v-for="e in ticket.events"
            :key="e.id"
            :class="e.kind !== 'reply' ? 'sd-event' : ''"
          >
            <span class="sd-time-avatar">{{
              e.kind === "reply"
                ? e.is_ai
                  ? "AI"
                  : e.actor_name.slice(0, 1)
                : ""
            }}</span
            ><template v-if="e.kind === 'reply'"
              ><div class="sd-message-head">
                <span
                  >{{ e.actor_name }} ·
                  {{
                    e.is_ai
                      ? "AI 助手"
                      : e.actor_role === "admin"
                        ? "管理员"
                        : "顾问"
                  }}</span
                ><time>{{ dateTime(e.created_at) }}</time>
              </div>
              <div
                class="sd-message"
                :class="e.is_ai || e.actor_role === 'admin' ? 'sd-admin' : ''"
              >
                {{ e.body }}
              </div></template
            >
            <div v-else class="sd-event-text">
              {{ e.actor_name }}
              {{
                e.kind === "status"
                  ? `将状态从「${eventStatus(e.from_status)}」变更为「${eventStatus(e.to_status)}」`
                  : e.kind === "schedule"
                    ? "调整了开发排期"
                    : e.kind === "expedite"
                      ? "更新了加急申请"
                      : e.kind === "ai_retry"
                        ? "请求 AI 继续分析"
                        : "提交了需求工单"
              }}
              <p
                v-if="
                  ['status', 'schedule', 'expedite'].includes(e.kind) && e.body
                "
                class="sd-status-reason"
              >
                {{ e.body }}
              </p>
              <br /><time>{{ dateTime(e.created_at) }}</time>
            </div>
            <div class="event-files">
              <button
                v-for="f in e.attachments"
                :key="f.id"
                class="sd-attachment"
                @click="getFile(f)"
              >
                <Icon name="file-text" /><span
                  >{{ f.original_name
                  }}<small>{{ fileSize(f.size) }}</small></span
                >
              </button>
            </div>
          </li>
        </ol>
        <div v-if="ticket.status === 'done'" class="sd-next">
          此工单已完成并归档。需要继续沟通时，请先使用上方的“重新打开”。
        </div>
        <form v-else class="sd-composer" @submit.prevent="submit">
          <label for="reply">继续沟通</label>
          <textarea
            id="reply"
            v-model="body"
            required
            maxlength="10000"
            placeholder="写下反馈或补充说明…"
          ></textarea>
          <div class="sd-composer-actions">
            <span class="sd-small sd-muted">发送回复不会改变工单状态</span>
            <button class="sd-button sd-primary" :disabled="busy || conflict">
              {{ busy ? "正在发送…" : "发送回复"
              }}<Icon name="arrow-up-right" />
            </button>
          </div>
          <div class="reply-files">
            <FilePicker :key="pickerKey" v-model="files" />
          </div>
        </form>
      </div>
      <aside class="sd-detail-meta" aria-label="工单属性">
        <dl class="sd-meta-pair">
          <dt>当前状态</dt>
          <dd><Status :value="ticket.status" /></dd>
        </dl>
        <dl class="sd-meta-pair">
          <dt>优先级</dt>
          <dd>{{ priorities[ticket.priority] }}</dd>
        </dl>
        <dl class="sd-meta-pair">
          <dt>期望完成</dt>
          <dd>{{ ticket.due_date || "未指定" }}</dd>
        </dl>
        <dl class="sd-meta-pair sd-estimate-meta">
          <dt>开发预计完成</dt>
          <dd>
            <ScheduleDate :ticket="ticket" />
            <p v-if="estimateDelay > 0" class="sd-small sd-muted">
              比期望日期晚 {{ estimateDelay }} 天
            </p>
            <p
              v-if="
                ticket.estimate_needs_review && ticket.status === 'in-progress'
              "
              class="sd-small sd-muted"
            >
              工单已退回或重新打开，请重新评估排期。
            </p>
            <button
              v-if="
                user?.role === 'admin' &&
                ticket.status !== 'done' &&
                !editingSchedule
              "
              class="sd-quiet sd-edit-estimate"
              :disabled="busy || conflict || !!selectedAction"
              @click="editSchedule"
            >
              {{ ticket.estimated_completion_date ? "调整排期" : "设置排期" }}
            </button>
            <form
              v-if="editingSchedule"
              class="sd-estimate-form"
              @submit.prevent="saveSchedule"
            >
              <label for="estimate-date"
                >开发预计完成日期<input
                  id="estimate-date"
                  type="date"
                  v-model="estimateDate"
                  :min="
                    ticket.estimated_completion_date &&
                    ticket.estimated_completion_date < today()
                      ? ticket.estimated_completion_date
                      : today()
                  "
                  max="9998-12-31"
              /></label>
              <small class="sd-muted"
                >留空表示待评估。日期代表可提交验收的时间。</small
              >
              <div v-if="estimateConflict" class="sd-error" role="alert">
                <p>
                  排期已更新为
                  {{
                    ticket.estimated_completion_date || "待评估"
                  }}，你的日期草稿为
                  {{ estimateDate || "待评估" }}。请选择后再保存。
                </p>
                <div class="sd-flex">
                  <button
                    type="button"
                    class="sd-button"
                    @click="resolveEstimateConflict('schedule', false)"
                  >
                    使用最新排期
                  </button>
                  <button
                    type="button"
                    class="sd-button"
                    @click="resolveEstimateConflict('schedule', true)"
                  >
                    确认采用我的改期
                  </button>
                </div>
              </div>
              <label for="estimate-reason"
                >{{
                  scheduleReasonRequired ? "调整原因（必填）" : "说明（选填）"
                }}<textarea
                  id="estimate-reason"
                  v-model="estimateReason"
                  :required="scheduleReasonRequired"
                  maxlength="2000"
                  rows="3"
                  placeholder="说明排期依据或调整原因…"
                ></textarea>
              </label>
              <div class="sd-flex">
                <button
                  class="sd-button sd-primary"
                  :disabled="
                    busy ||
                    conflict ||
                    estimateConflict ||
                    (estimateDate ===
                      (ticket.estimated_completion_date || '') &&
                      !ticket.estimate_needs_review)
                  "
                >
                  {{ busy ? "正在保存…" : "保存排期" }}
                </button>
                <button
                  type="button"
                  class="sd-button"
                  :disabled="busy"
                  @click="
                    editingSchedule = false;
                    estimateReason = '';
                  "
                >
                  取消
                </button>
              </div>
            </form>
          </dd>
        </dl>
        <dl class="sd-meta-pair">
          <dt>提交人</dt>
          <dd>{{ ticket.owner_name }}</dd>
        </dl>
        <dl class="sd-meta-pair">
          <dt>SAP 系统</dt>
          <dd>{{ systemLabel(ticket.system) }}</dd>
        </dl>
        <dl class="sd-meta-pair">
          <dt>SAP 模块</dt>
          <dd>{{ ticket.module }}</dd>
        </dl>
        <dl class="sd-meta-pair">
          <dt>需求类型</dt>
          <dd>{{ ticket.kind }}</dd>
        </dl>
      </aside>
    </div></template
  >
</template>

<style>
#sap-desk-design .sd-detail-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-bottom: 22px;
}
#sap-desk-design .sd-detail-toolbar .sd-breadcrumb {
  margin-bottom: 0;
  white-space: nowrap;
}
#sap-desk-design .sd-detail-refresh-group {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}
#sap-desk-design .sd-detail-refresh {
  justify-content: center;
  min-width: 96px;
  min-height: 36px;
  padding: 6px 10px;
  border: 1px solid var(--sd-line);
  border-radius: 6px;
  background: var(--sd-paper);
  white-space: nowrap;
  transition:
    background 0.16s,
    color 0.16s;
}
#sap-desk-design .sd-detail-refresh:hover:not(:disabled) {
  color: var(--sd-ink);
  background: var(--sd-tint);
}
#sap-desk-design .sd-detail-refresh svg {
  width: 14px;
  height: 14px;
}
#sap-desk-design .sd-detail-refresh .is-loading {
  animation: sd-refresh-spin 0.9s linear infinite;
}
#sap-desk-design .sd-detail-update {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 18px;
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--sd-tint);
  color: var(--sd-ink);
  font-size: 12px;
}
@keyframes sd-refresh-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (max-width: 650px) {
  #sap-desk-design .sd-detail-refresh {
    min-height: 40px;
  }
}
@media (prefers-reduced-motion: reduce) {
  #sap-desk-design .sd-detail-refresh .is-loading {
    animation: none;
  }
}
#sap-desk-design .sd-edit-estimate {
  display: block;
  margin-top: 10px;
  font-size: 12px;
}
#sap-desk-design .sd-estimate-form {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}
#sap-desk-design .sd-estimate-form label {
  display: grid;
  gap: 6px;
  font-size: 12px;
}
#sap-desk-design .sd-estimate-form input,
#sap-desk-design .sd-estimate-form textarea {
  width: 100%;
  min-width: 0;
}
#sap-desk-design .sd-estimate-form .sd-flex {
  flex-wrap: wrap;
}
#sap-desk-design .sd-estimate-form .sd-button {
  padding: 8px 10px;
  font-size: 12px;
}
@media (max-width: 700px) {
  #sap-desk-design .sd-estimate-meta {
    grid-column: 1 / -1;
  }
}
</style>
