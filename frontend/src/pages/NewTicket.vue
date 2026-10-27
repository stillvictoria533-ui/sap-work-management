<script setup lang="ts">
import { reactive, ref, onUnmounted } from "vue";
import { useRouter, onBeforeRouteLeave } from "vue-router";
import { post, ApiError } from "../api";
import { refreshStats } from "../state";
import {
  systems,
  modules,
  kinds,
  priorities,
  today,
  type Ticket,
} from "../types";
import FilePicker from "../components/FilePicker.vue";
import Icon from "../components/Icon.vue";
const router = useRouter(),
  form = reactive({
    title: "",
    system: "",
    module: "MM",
    kind: "配置",
    priority: "medium",
    due_date: "",
    description: "",
  }),
  files = ref<File[]>([]),
  busy = ref(false),
  error = ref(""),
  existingTicket = ref(0),
  saved = ref(false),
  requestKey = crypto.randomUUID();
let active = true;
onUnmounted(() => {
  active = false;
});
onBeforeRouteLeave(
  () =>
    saved.value ||
    (!form.title && !form.description) ||
    window.confirm("离开后，未提交的内容将丢失。确定离开？"),
);
async function submit() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  existingTicket.value = 0;
  try {
    const data = new FormData();
    data.set("request_key", requestKey);
    for (const [k, v] of Object.entries(form)) if (v) data.set(k, v);
    files.value.forEach((f) => data.append("files", f));
    const ticket = await post<Ticket>("/tickets", data);
    void refreshStats().catch(() => {});
    if (!active) return;
    saved.value = true;
    await router.push("/tickets/" + ticket.id);
  } catch (e) {
    if (!active) return;
    error.value = (e as Error).message;
    if (e instanceof ApiError && e.status === 409) {
      existingTicket.value = Number(e.data.ticket_id) || 0;
    }
  } finally {
    if (active) busy.value = false;
  }
}
</script>
<template>
  <div class="sd-form-wrap">
    <RouterLink to="/tickets" class="sd-quiet sd-breadcrumb"
      ><Icon name="arrow-left" />返回我的需求</RouterLink
    >
    <div class="sd-heading"><h1>说说你的需求。</h1></div>
    <form @submit.prevent="submit">
      <fieldset
        :disabled="busy"
        style="border: 0; padding: 0; margin: 0; min-width: 0"
      >
        <div class="sd-form-section">
          <h3><span>01</span>基本信息</h3>
          <div class="sd-form-grid">
            <label class="sd-field sd-wide"
              >标题 *<input
                v-model="form.title"
                required
                maxlength="120"
                placeholder="例如：采购订单审批策略调整" /></label
            ><label class="sd-field"
              >SAP 系统 *<select v-model="form.system" required>
                <option value="" disabled>请选择系统</option>
                <option v-for="s in systems" :key="s.id" :value="s.id">
                  {{ s.label }}
                </option>
              </select></label
            ><label class="sd-field"
              >SAP 模块 *<select v-model="form.module">
                <option v-for="m in modules" :key="m">{{ m }}</option>
              </select></label
            ><label class="sd-field"
              >需求类型 *<select v-model="form.kind">
                <option v-for="k in kinds" :key="k">{{ k }}</option>
              </select></label
            ><label class="sd-field"
              >优先级 *<select v-model="form.priority">
                <option
                  v-for="(label, key) in priorities"
                  :key="key"
                  :value="key"
                >
                  {{ label }}
                </option>
              </select></label
            ><label class="sd-field"
              >期望完成日期<input
                v-model="form.due_date"
                type="date"
                :min="today()"
              /><small>可选，作为排期参考</small></label
            >
          </div>
        </div>
        <div class="sd-form-section">
          <h3><span>02</span>需求说明</h3>
          <label class="sd-field"
            >详细描述 *<textarea
              v-model="form.description"
              required
              maxlength="10000"
              rows="8"
              placeholder="业务背景：目前遇到了什么问题？&#10;&#10;期望结果：希望如何处理？&#10;&#10;验收标准：怎样算完成？"
            ></textarea>
          </label>
        </div>
        <div class="sd-form-section">
          <h3><span>03</span>相关附件</h3>
          <FilePicker v-model="files" />
        </div>
        <p v-if="error" class="sd-error" role="alert">{{ error }}</p>
        <p v-if="existingTicket">
          <RouterLink :to="'/tickets/' + existingTicket"
            >查看已创建工单并补充说明</RouterLink
          >
          <span class="sd-small sd-muted"
            >本页草稿保留，请先复制需要补充的内容。</span
          >
        </p>
        <div class="sd-form-bottom">
          <p>提交后进入「待受理」，可在时间线继续补充。</p>
          <button class="sd-button sd-primary" :disabled="busy">
            {{ busy ? "正在提交…" : "提交需求" }}<Icon name="arrow-up-right" />
          </button>
        </div>
      </fieldset>
    </form>
  </div>
</template>
