<script setup lang="ts">
import { statuses } from "../types";
defineProps<{ value: string }>();
</script>
<template>
  <span class="sd-status" :class="value"
    ><span class="sd-dot"></span>{{ statuses[value] || value }}</span
  >
</template>
