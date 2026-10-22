<script setup lang="ts">
import { computed } from "vue";
import { today } from "../types";
import { estimateHint } from "../schedule";
const props = defineProps<{
  ticket: {
    status: string;
    estimated_completion_date: string | null;
    estimate_needs_review: boolean;
  };
}>();
const hint = computed(() => estimateHint(props.ticket, today()));
</script>
<template>
  <span class="sd-schedule-date">
    <time
      v-if="ticket.estimated_completion_date"
      :datetime="ticket.estimated_completion_date"
      >{{ ticket.estimated_completion_date }}</time
    >
    <span v-else class="sd-muted">待评估</span>
    <small v-if="hint.label" class="sd-schedule-hint" :class="hint.tone">{{
      hint.label
    }}</small>
    <small
      v-if="ticket.estimate_needs_review && ticket.status === 'in-progress'"
      class="sd-schedule-hint"
      >需重新评估</small
    >
  </span>
</template>
<style>
#sap-desk-design .sd-schedule-date {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
  font-variant-numeric: tabular-nums;
}
#sap-desk-design .sd-schedule-hint {
  font-size: 11px;
  color: var(--sd-muted);
}
#sap-desk-design .sd-schedule-hint.overdue {
  color: var(--sd-red);
}
#sap-desk-design .sd-schedule-hint.today {
  color: var(--sd-accent);
}
</style>
