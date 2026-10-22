<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { post, ApiError } from "../api";
import { user, refreshStats } from "../state";
import { today, type Ticket } from "../types";
import Icon from "./Icon.vue";
const props = defineProps<{ ticket: Ticket; disabled?: boolean }>();
const emit = defineEmits<{
  updated: [ticket: Ticket];
  dirty: [value: boolean];
  refresh: [];
}>();
const editing = ref(false),
  requestedDate = ref(""),
  reason = ref(""),
  busy = ref(false),
  error = ref(""),
  stale = ref(false);
let requestKey = crypto.randomUUID();
const latest = computed(() => props.ticket.expedite_requests?.[0]);
const pending = computed(() =>
  props.ticket.expedite_requests?.find((r) => r.status === "pending"),
);
const editable = computed(() =>
  ["pending", "in-progress"].includes(props.ticket.status),
);
const canRequest = computed(
  () =>
    editable.value &&
    (!props.ticket.estimated_completion_date ||
      props.ticket.estimated_completion_date > today()),
);
const maxDate = computed(() =>
  props.ticket.estimated_completion_date
    ? new Date(
        Date.parse(props.ticket.estimated_completion_date + "T00:00:00Z") -
          86400000,
      )
        .toISOString()
        .slice(0, 10)
    : "9998-12-31",
);
const labels = {
  pending: "待管理员评估",
  approved: "已安排",
  rejected: "未通过",
  cancelled: "已结束",
};
watch([editing, requestedDate, reason], () =>
  emit("dirty", editing.value && !!(requestedDate.value || reason.value)),
);
watch(
  () => props.ticket.id,
  () => {
    editing.value = false;
    requestedDate.value = "";
    reason.value = "";
    error.value = "";
    requestKey = crypto.randomUUID();
  },
);
watch(pending, (value) => {
  if (value) {
    editing.value = false;
    requestedDate.value = "";
    reason.value = "";
  }
});
async function submit() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const ticket = await post<Ticket>(`/tickets/${props.ticket.id}/expedite`, {
      request_key: requestKey,
      version: props.ticket.version,
      requested_date: requestedDate.value,
      reason: reason.value,
    });
    editing.value = false;
    requestedDate.value = "";
    reason.value = "";
    stale.value = false;
    requestKey = crypto.randomUUID();
    emit("updated", ticket);
    emit("dirty", false);
    await refreshStats();
  } catch (e) {
    error.value = (e as Error).message;
    stale.value = e instanceof ApiError && e.status === 409;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section
    v-if="pending || latest || (user?.role === 'consultant' && editable)"
    class="sd-expedite"
    aria-label="加急申请"
  >
    <div class="sd-expedite-head">
      <div>
        <span class="sd-section-label">加急申请</span
        ><span v-if="pending || latest" class="sd-expedite-state">{{
          labels[(pending || latest)!.status]
        }}</span>
      </div>
      <RouterLink
        v-if="user?.role === 'admin' && pending"
        class="sd-button sd-primary"
        :to="{ path: '/scheduling', query: { request: String(pending.id) } }"
        >处理申请<Icon name="arrow-up-right"
      /></RouterLink>
      <button
        v-if="user?.role === 'consultant' && canRequest && !pending && !editing"
        class="sd-button"
        :disabled="disabled"
        @click="
          editing = true;
          error = '';
        "
      >
        申请加急
      </button>
    </div>
    <template v-if="pending"
      ><p>
        希望完成日期：<strong>{{ pending.requested_date }}</strong>
      </p>
      <p class="sd-expedite-reason">{{ pending.reason }}</p>
      <small class="sd-muted"
        >申请提交后，原排期继续保留，待管理员评估后更新。</small
      ></template
    >
    <template v-else-if="latest"
      ><p>希望完成日期：{{ latest.requested_date }}</p>
      <p v-if="latest.decision_note" class="sd-expedite-reason">
        {{ latest.decision_note }}
      </p></template
    >
    <p
      v-if="user?.role === 'consultant' && !pending && !latest && !editing"
      class="sd-small sd-muted"
    >
      {{
        canRequest
          ? "需要提前交付时，可说明目标日期和业务影响。"
          : "当前排期已到期，可在继续沟通中补充紧急情况。"
      }}
    </p>
    <form v-if="editing" class="sd-expedite-form" @submit.prevent="submit">
      <label for="expedite-date"
        >希望完成日期<input
          id="expedite-date"
          v-model="requestedDate"
          type="date"
          :min="today()"
          :max="maxDate"
          required
          :disabled="busy"
      /></label>
      <label for="expedite-reason"
        >加急原因及业务影响<textarea
          id="expedite-reason"
          v-model="reason"
          required
          maxlength="2000"
          rows="3"
          :disabled="busy"
          placeholder="为什么需要提前？未按时完成会影响哪些业务？"
        ></textarea>
      </label>
      <p v-if="error" class="sd-error" role="alert">
        {{ error
        }}<button
          v-if="stale"
          class="sd-quiet"
          type="button"
          @click="emit('refresh')"
        >
          刷新工单后重新评估
        </button>
      </p>
      <div class="sd-flex">
        <button class="sd-button sd-primary" :disabled="busy || disabled">
          {{ busy ? "正在提交…" : "提交加急申请" }}</button
        ><button
          class="sd-button"
          type="button"
          :disabled="busy"
          @click="
            editing = false;
            requestedDate = '';
            reason = '';
          "
        >
          取消
        </button>
      </div>
    </form>
  </section>
</template>
<style>
#sap-desk-design .sd-expedite {
  border: 1px solid var(--sd-line);
  border-radius: 9px;
  padding: 19px 22px;
  margin: 22px 0 28px;
  background: var(--sd-paper);
}
#sap-desk-design .sd-expedite-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
#sap-desk-design .sd-expedite-head .sd-section-label {
  display: inline;
}
#sap-desk-design .sd-expedite-state {
  font-size: 11px;
  margin-left: 12px;
  padding: 3px 7px;
  border-radius: 4px;
  background: var(--sd-tint);
}
#sap-desk-design .sd-expedite p {
  font-size: 12px;
  line-height: 1.8;
}
#sap-desk-design .sd-expedite-reason {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 9px 0;
}
#sap-desk-design .sd-expedite-form {
  display: grid;
  gap: 14px;
  margin-top: 15px;
}
#sap-desk-design .sd-expedite-form label {
  display: grid;
  gap: 7px;
  font-size: 12px;
}
#sap-desk-design .sd-expedite-form input {
  max-width: 240px;
}
#sap-desk-design .sd-expedite-form textarea {
  width: 100%;
}
</style>
