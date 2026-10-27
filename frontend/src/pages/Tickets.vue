<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  readTicketListState,
  ticketListQuery,
  type TicketListState,
} from "../ticketListState";
import { api, download } from "../api";
import { user, stats, refreshStats } from "../state";
import {
  statuses,
  priorities,
  modules,
  dateTime,
  type TicketListItem,
  type Page,
  type User,
} from "../types";
import Icon from "../components/Icon.vue";
import Status from "../components/Status.vue";
import ScheduleDate from "../components/ScheduleDate.vue";
const route = useRoute(),
  router = useRouter(),
  items = ref<TicketListItem[]>([]),
  count = ref(0),
  people = ref<User[]>([]),
  loading = ref(false),
  error = ref(""),
  exporting = ref(false),
  chipsElement = ref<HTMLElement | null>(null),
  moreFiltersButton = ref<HTMLButtonElement | null>(null);
let sequence = 0,
  debounce: ReturnType<typeof setTimeout>;
const listState = computed(() => readTicketListState(route.query));
const listQuery = computed(() => ticketListQuery(listState.value));
let pendingListState: TicketListState | null = null;
let listNavigation = 0;
function updateList(patch: Partial<TicketListState>) {
  const navigation = ++listNavigation;
  // Merge quick successive edits before the router finishes updating the URL.
  pendingListState = { ...(pendingListState || listState.value), ...patch };
  return router
    .replace({ path: "/tickets", query: ticketListQuery(pendingListState) })
    .finally(() => {
      if (navigation === listNavigation) pendingListState = null;
    });
}
function filter(
  key: "q" | "module" | "status" | "priority" | "owner" | "schedule" | "sort",
) {
  return computed({
    get: () => listState.value[key],
    set: (value: string) => updateList({ [key]: value, page: 1 }),
  });
}
const page = computed(() => listState.value.page),
  q = filter("q"),
  module = filter("module"),
  status = filter("status"),
  priority = filter("priority"),
  owner = filter("owner"),
  schedule = filter("schedule"),
  sort = filter("sort"),
  unreadOnly = computed({
    get: () => listState.value.tab === "unread",
    set: (value: boolean) =>
      updateList({ tab: value ? "unread" : "all", page: 1 }),
  }),
  extra = computed({
    get: () => listState.value.extra,
    set: (value: boolean) => updateList({ extra: value }),
  }),
  scope = computed(() => listState.value.scope),
  heading = computed(() =>
    scope.value === "archive"
      ? "已归档"
      : scope.value === "attention"
        ? "需要我处理"
        : user.value?.role === "admin"
          ? "需求工单"
          : "我的需求",
  );
const isArchive = computed(() => scope.value === "archive");
const availableStatuses = computed(() =>
  Object.fromEntries(
    Object.entries(statuses).filter(
      ([key]) => scope.value !== "attention" || key !== "done",
    ),
  ),
);
type FilterKey =
  | "q"
  | "module"
  | "status"
  | "priority"
  | "owner"
  | "schedule"
  | "sort"
  | "tab";
const scheduleLabels: Record<string, string> = {
  unassessed: "待评估",
  today: "今日到期",
  overdue: "已逾期",
};
const sortLabels: Record<string, string> = {
  estimate_asc: "预计完成由近到远",
  estimate_desc: "预计完成由远到近",
};
const activeFilters = computed(() => {
  const chips: { key: FilterKey; label: string }[] = [];
  if (q.value) chips.push({ key: "q", label: `搜索：${q.value}` });
  if (module.value)
    chips.push({ key: "module", label: `模块：${module.value}` });
  if (status.value)
    chips.push({
      key: "status",
      label: `状态：${statuses[status.value] || status.value}`,
    });
  if (priority.value)
    chips.push({
      key: "priority",
      label: `优先级：${priorities[priority.value] || priority.value}`,
    });
  if (owner.value && user.value?.role === "admin")
    chips.push({
      key: "owner",
      label: `提交人：${people.value.find((p) => String(p.id) === owner.value)?.name || "账号 " + owner.value}`,
    });
  if (schedule.value)
    chips.push({
      key: "schedule",
      label: `排期：${scheduleLabels[schedule.value]}`,
    });
  if (sort.value)
    chips.push({ key: "sort", label: `排序：${sortLabels[sort.value]}` });
  if (unreadOnly.value) chips.push({ key: "tab", label: "只看未读" });
  return chips;
});
const extraFilterCount = computed(
  () =>
    activeFilters.value.filter((chip) =>
      ["priority", "owner", "schedule", "sort", "tab"].includes(chip.key),
    ).length,
);
async function removeFilter(key: FilterKey) {
  const index = activeFilters.value.findIndex((chip) => chip.key === key);
  await updateList({ [key]: key === "tab" ? "all" : "", page: 1 });
  await nextTick();
  const buttons =
    chipsElement.value?.querySelectorAll<HTMLButtonElement>(".sd-filter-chip");
  (
    buttons?.[Math.min(index, buttons.length - 1)] || moreFiltersButton.value
  )?.focus();
}
function params() {
  return new URLSearchParams({
    page: String(page.value),
    q: q.value,
    module: module.value,
    status: status.value,
    priority: priority.value,
    owner: owner.value,
    schedule: schedule.value,
    sort: sort.value,
    tab: unreadOnly.value ? "unread" : "all",
    scope: scope.value,
  });
}
const requestParams = computed(() => params().toString());
async function load() {
  const id = ++sequence;
  loading.value = true;
  error.value = "";
  try {
    const data = await api<Page<TicketListItem>>(
      "/tickets?" + requestParams.value,
    );
    if (id === sequence) {
      items.value = data.results;
      count.value = data.count;
    }
  } catch (e) {
    if (id === sequence) error.value = (e as Error).message;
  } finally {
    if (id === sequence) loading.value = false;
  }
}
watch(
  [requestParams, () => route.path],
  ([next, path], [previous]) => {
    clearTimeout(debounce);
    sequence++;
    if (path !== "/tickets") return;
    const searchChanged =
      previous !== undefined &&
      new URLSearchParams(next).get("q") !==
        new URLSearchParams(previous).get("q");
    if (searchChanged) debounce = setTimeout(() => void load(), 300);
    else void load();
  },
  { immediate: true },
);
onUnmounted(() => {
  sequence++;
  clearTimeout(debounce);
});
onMounted(async () => {
  void refreshStats();
  if (user.value?.role === "admin")
    try {
      people.value = (await api<{ results: User[] }>("/accounts")).results;
    } catch (e) {
      error.value = (e as Error).message;
    }
});
function turn(n: number) {
  updateList({ page: n });
}
async function clearFilters() {
  await updateList({
    q: "",
    module: "",
    status: "",
    priority: "",
    owner: "",
    schedule: "",
    sort: "",
    tab: "all",
    page: 1,
  });
  await nextTick();
  moreFiltersButton.value?.focus();
}
async function exportData(mode: string) {
  exporting.value = true;
  try {
    await download(
      "/api/tickets/export?" + params() + "&mode=" + mode,
      `sap-desk-${mode}.csv`,
    );
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    exporting.value = false;
  }
}
</script>
<template>
  <div class="sd-heading">
    <h1>{{ heading }}</h1>
    <RouterLink
      v-if="user?.role === 'consultant'"
      to="/tickets/new"
      class="sd-button sd-primary"
      ><Icon name="plus" />提交需求</RouterLink
    >
    <details v-else class="export-menu">
      <summary class="sd-button"><Icon name="download" />导出</summary>
      <div>
        <button
          class="sd-button"
          :disabled="exporting"
          @click="exportData('tickets')"
        >
          工单列表 CSV</button
        ><button
          class="sd-button"
          :disabled="exporting"
          @click="exportData('events')"
        >
          沟通记录 CSV</button
        ><small>导出当前筛选范围的全部结果</small>
      </div>
    </details>
  </div>
  <div class="sd-filter-row">
    <label class="sd-search"
      ><Icon name="search" /><input
        v-model="q"
        type="search"
        aria-label="搜索工单"
        placeholder="搜索编号、标题或沟通内容" /></label
    ><select v-model="module" aria-label="模块筛选">
      <option value="">全部模块</option>
      <option v-for="m in modules" :key="m">{{ m }}</option></select
    ><select v-if="!isArchive" v-model="status" aria-label="状态筛选">
      <option value="">全部状态</option>
      <option
        v-for="(name, value) in availableStatuses"
        :key="value"
        :value="value"
      >
        {{ name }}
      </option></select
    ><button
      ref="moreFiltersButton"
      class="sd-filter-more"
      aria-controls="ticket-extra-filters"
      @click="extra = !extra"
      :aria-expanded="extra"
    >
      <Icon name="sliders-horizontal" />更多筛选<small
        v-if="extraFilterCount"
        class="sd-filter-count"
        >{{ extraFilterCount }}</small
      >
    </button>
  </div>
  <div
    v-show="extra"
    id="ticket-extra-filters"
    class="sd-extra-filters sd-ticket-extra"
    role="region"
    aria-label="更多筛选条件"
  >
    <label
      >优先级<select v-model="priority">
        <option value="">全部优先级</option>
        <option v-for="(name, value) in priorities" :key="value" :value="value">
          {{ name }}
        </option>
      </select></label
    ><label v-if="user?.role === 'admin'"
      >提交人<select v-model="owner">
        <option value="">全部提交人</option>
        <option v-for="p in people" :key="p.id" :value="p.id">
          {{ p.name }}
        </option>
      </select></label
    >
    <label v-if="!isArchive"
      >开发排期<select v-model="schedule" aria-label="开发排期筛选">
        <option value="">全部排期</option>
        <option value="unassessed">待评估</option>
        <option value="today">今日到期</option>
        <option value="overdue">已逾期</option>
      </select></label
    >
    <label
      >排序<select v-model="sort" aria-label="工单排序">
        <option value="">最近更新</option>
        <option value="estimate_asc">预计完成：由近到远</option>
        <option value="estimate_desc">预计完成：由远到近</option>
      </select></label
    >
    <label class="checkbox-line sd-unread-filter"
      ><input v-model="unreadOnly" type="checkbox" />只看未读</label
    >
  </div>
  <div
    v-if="activeFilters.length"
    ref="chipsElement"
    class="sd-active-filters"
    role="group"
    aria-label="已选条件"
  >
    <span class="sd-active-filter-label">已选条件</span>
    <button
      v-for="chip in activeFilters"
      :key="chip.key"
      class="sd-filter-chip"
      type="button"
      :aria-label="'移除筛选：' + chip.label"
      :title="chip.label"
      @click="removeFilter(chip.key)"
    >
      <span>{{ chip.label }}</span
      ><Icon name="x" />
    </button>
    <button
      type="button"
      class="sd-quiet sd-clear-filters"
      @click="clearFilters"
    >
      清除筛选
    </button>
  </div>
  <p v-if="error" class="sd-error" role="alert">
    {{ error }} <button @click="load" class="sd-quiet">重试</button>
  </p>
  <p v-if="loading" class="sd-small sd-muted" role="status">正在加载…</p>
  <table
    v-if="items.length"
    class="sd-table sd-scheduled-table"
    :class="{ 'sd-archive-table': isArchive }"
    :aria-busy="loading"
  >
    <thead>
      <tr>
        <th>需求 / 最近更新</th>
        <th>模块</th>
        <th>优先级</th>
        <th v-if="!isArchive">状态</th>
        <th>开发预计完成</th>
        <th>提交人</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="t in items" :key="t.id">
        <td>
          <RouterLink
            class="sd-ticket-title"
            :to="{ path: '/tickets/' + t.id, query: listQuery }"
            >{{ t.title }}</RouterLink
          >
          <div class="sd-ticket-sub">
            <span>{{ t.number }}</span
            >· <span>{{ dateTime(t.updated_at) }}</span
            ><span
              v-if="t.unread"
              class="sd-dot"
              aria-label="有未读动态"
            ></span>
          </div>
        </td>
        <td>
          <span class="sd-module">{{ t.module }}</span>
        </td>
        <td>
          <span
            class="sd-priority"
            :class="['urgent', 'high'].includes(t.priority) ? 'high' : 'medium'"
            ><i aria-hidden="true"></i>{{ priorities[t.priority] }}</span
          >
        </td>
        <td v-if="!isArchive"><Status :value="t.status" /></td>
        <td class="sd-schedule-cell">
          <span class="sd-schedule-mobile-label">开发预计完成</span
          ><ScheduleDate :ticket="t" />
        </td>
        <td>{{ t.owner_name }}</td>
      </tr>
    </tbody>
  </table>
  <div v-else-if="!loading && !error" class="sd-empty">
    {{
      stats.total === 0
        ? "还没有工单，从提交第一项需求开始。"
        : "没有符合筛选条件的工单。"
    }}
  </div>
  <div class="sd-list-footer">
    <span>共 {{ count }} 条</span>
    <div v-if="count > 20" class="sd-page-step">
      <button
        :disabled="page <= 1 || loading"
        @click="turn(page - 1)"
        aria-label="上一页"
      >
        <Icon name="chevron-left" /></button
      ><span>{{ page }} / {{ Math.max(1, Math.ceil(count / 20)) }}</span
      ><button
        :disabled="page * 20 >= count || loading"
        @click="turn(page + 1)"
        aria-label="下一页"
      >
        <Icon name="chevron-right" />
      </button>
    </div>
  </div>
</template>

<style>
#sap-desk-design .sd-ticket-extra {
  padding: 16px;
  margin: -5px 0 20px;
  border: 1px solid var(--sd-line);
  border-radius: 8px;
  background: var(--sd-bg);
  gap: 16px;
}
#sap-desk-design .sd-ticket-extra .sd-unread-filter {
  flex-direction: row;
  align-items: center;
  align-self: end;
  margin: 0;
  min-height: 36px;
}
#sap-desk-design .sd-filter-count {
  min-width: 18px;
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--sd-tint);
  font-variant-numeric: tabular-nums;
}
#sap-desk-design .sd-active-filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: -3px 0 22px;
}
#sap-desk-design .sd-active-filter-label,
#sap-desk-design .sd-clear-filters {
  font-size: 12px;
  color: var(--sd-muted);
}
#sap-desk-design .sd-filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  max-width: 100%;
  min-height: 32px;
  padding: 5px 9px;
  border: 1px solid var(--sd-line);
  border-radius: 6px;
  background: var(--sd-bg);
  color: var(--sd-ink);
  font-size: 12px;
  cursor: pointer;
}
#sap-desk-design .sd-filter-chip > span {
  max-width: 260px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
#sap-desk-design .sd-filter-chip > svg {
  width: 13px;
  height: 13px;
  flex-shrink: 0;
}
#sap-desk-design .sd-filter-chip:hover {
  background: var(--sd-tint);
}
#sap-desk-design .sd-filter-chip:focus-visible,
#sap-desk-design .sd-clear-filters:focus-visible,
#sap-desk-design .sd-filter-more:focus-visible {
  outline: 2px solid var(--sd-accent);
  outline-offset: 3px;
}
#sap-desk-design .sd-scheduled-table th:first-child {
  width: 34%;
}
#sap-desk-design .sd-scheduled-table th:nth-child(2) {
  width: 8%;
}
#sap-desk-design .sd-scheduled-table th:nth-child(3) {
  width: 10%;
}
#sap-desk-design .sd-scheduled-table th:nth-child(4) {
  width: 14%;
}
#sap-desk-design .sd-scheduled-table th:nth-child(5) {
  width: 21%;
}
#sap-desk-design .sd-scheduled-table th:nth-child(6) {
  width: 13%;
}
#sap-desk-design .sd-scheduled-table.sd-archive-table th:first-child {
  width: 41%;
}
#sap-desk-design .sd-scheduled-table.sd-archive-table th:nth-child(2) {
  width: 9%;
}
#sap-desk-design .sd-scheduled-table.sd-archive-table th:nth-child(3) {
  width: 11%;
}
#sap-desk-design .sd-scheduled-table.sd-archive-table th:nth-child(4) {
  width: 25%;
}
#sap-desk-design .sd-scheduled-table.sd-archive-table th:nth-child(5) {
  width: 14%;
}
#sap-desk-design .sd-schedule-mobile-label {
  display: none;
}
@media (max-width: 700px) {
  #sap-desk-design .sd-scheduled-table .sd-schedule-cell {
    flex: 1 1 100%;
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-top: 5px;
  }
  #sap-desk-design .sd-schedule-mobile-label {
    display: inline;
    font-size: 11px;
    color: var(--sd-muted);
  }
}
</style>
