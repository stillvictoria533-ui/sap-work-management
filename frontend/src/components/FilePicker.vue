<script setup lang="ts">
import { ref } from "vue";
import { fileSize } from "../types";
const props = defineProps<{ modelValue: File[] }>();
const emit = defineEmits<{ "update:modelValue": [File[]] }>();
const error = ref(""),
  input = ref<HTMLInputElement | null>(null);
function change(e: Event) {
  const files = Array.from((e.target as HTMLInputElement).files || []);
  error.value = "";
  if (
    files.length > 5 ||
    files.some(
      (f) =>
        f.size > 20 * 1024 * 1024 ||
        f.size === 0 ||
        !/\.(png|jpe?g|pdf|docx|xlsx|csv|txt)$/i.test(f.name),
    )
  ) {
    error.value =
      "最多 5 个附件，每个 20MB；支持图片、PDF、DOCX、XLSX、CSV、TXT。";
    if (input.value) input.value.value = "";
    return;
  }
  emit("update:modelValue", files);
}
function remove(i: number) {
  emit(
    "update:modelValue",
    props.modelValue.filter((_, n) => n !== i),
  );
  if (input.value) input.value.value = "";
}
</script>
<template>
  <div class="sd-upload">
    <label class="sd-field"
      >相关附件 <small>最多 5 个，每个 20MB；文本请使用 UTF-8 编码</small
      ><input
        ref="input"
        type="file"
        multiple
        accept=".png,.jpg,.jpeg,.pdf,.docx,.xlsx,.csv,.txt"
        @change="change"
    /></label>
  </div>
  <div class="selected-files">
    <div v-for="(f, i) in modelValue" :key="f.name + i">
      <span>{{ f.name }} · {{ fileSize(f.size) }}</span
      ><button
        type="button"
        class="sd-quiet"
        @click="remove(i)"
        :aria-label="'移除 ' + f.name"
      >
        移除
      </button>
    </div>
  </div>
  <p v-if="error" class="sd-error" role="alert">{{ error }}</p>
</template>
