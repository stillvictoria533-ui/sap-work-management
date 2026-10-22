<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import { stats } from "../state";
import { calendarDays, shiftMonth } from "../calendar";
import { priorities, today, type CalendarPage } from "../types";
import Icon from "../components/Icon.vue";
import Status from "../components/Status.vue";
import ScheduleDate from "../components/ScheduleDate.vue";

const route = useRoute(),
  router = useRouter();
const thisMonth = today().slice(0, 7);
function queryMonth() {
  const value = String(route.query.month || thisMonth);
  return /^[1-9]\d{3}-(0[1-9]|1[0-2])$/.test(value) &&
    Number(value.slice(0, 4)) < 9999
    ? value
    : thisMonth;
}
function queryPage() {
  const value = Number(route.query.page || 1);
  return Number.isSafeInteger(value) && value > 0 ? value : 1;
}
const mode = ref(route.query.mode === "estimated" ? "estimated" : "submitted");
const month = ref(queryMonth()),
  selected = ref(String(route.query.date || "")),
  page = ref(queryPage());
const data = ref<CalendarPage | null>(null),
  loading = ref(false),
  error = ref("");
let sequence = 0;
const dates = computed(() => calendarDays(month.value));
const readyData = computed(() =>
  data.value?.month === month.value && data.value?.mode === mode.value
    ? data.value
    : null,
);
const byDate = computed(
  () => new Map(readyData.value?.days.map((day) => [day.date, day]) || []),
);
const monthLabel = computed(
  () => `${month.value.slice(0, 4)} 年 ${Number(month.value.slice(5))} 月`,
);
const selectedLabel = computed(() =>
  selected.value
    ? `${Number(selected.value.slice(5, 7))} 月 ${Number(selected.value.slice(8))} 日`
    : "选择日期",
);
const urgentCount = computed(
  () =>
    readyData.value?.days.reduce((sum, day) => sum + day.urgent_count, 0) || 0,
);
const pages = computed(() =>
  Math.max(1, Math.ceil((data.value?.count || 0) / 20)),
);
const timeOfDay = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

async function load() {
  const id = ++sequence;
  loading.value = true;
  error.value = "";
  const params = new URLSearchParams({
    month: month.value,
    mode: mode.value,
    page: String(page.value),
  });
  if (selected.value) params.set("date", selected.value);
  try {
    const response = await api<CalendarPage>("/calendar?" + params);
    if (id !== sequence) return;
    data.value = response;
    selected.value = response.date;
    await router.replace({
      path: "/calendar",
      query: {
        month: response.month,
        ...(mode.value === "estimated" ? { mode: "estimated" } : {}),
        date: response.date,
        ...(page.value > 1 ? { page: String(page.value) } : {}),
      },
    });
  } catch (e) {
    if (id === sequence) error.value = (e as Error).message;
  } finally {
    if (id === sequence) loading.value = false;
  }
}
function selectMode(value: string) {
  if (mode.value === value) return;
  mode.value = value;
  page.value = 1;
  void load();
}
function selectMonth(value: string) {
  if (
    !/^[1-9]\d{3}-(0[1-9]|1[0-2])$/.test(value) ||
    Number(value.slice(0, 4)) >= 9999
  )
    return;
  month.value = value;
  selected.value = "";
  page.value = 1;
  void load();
}
function changeMonth(offset: number) {
  selectMonth(shiftMonth(month.value, offset));
}
function selectDate(value: string) {
  selected.value = value;
  page.value = 1;
  void load();
}
function turn(value: number) {
  page.value = value;
  void load();
}
watch(
  () => route.query,
  () => {
    if (route.path !== "/calendar") return;
    const nextMonth = queryMonth(),
      nextDate = String(route.query.date || ""),
      nextPage = queryPage(),
      nextMode = route.query.mode === "estimated" ? "estimated" : "submitted";
    if (
      month.value === nextMonth &&
      selected.value === nextDate &&
      page.value === nextPage &&
      mode.value === nextMode
    )
      return;
    mode.value = nextMode;
    month.value = nextMonth;
    selected.value = nextDate;
    page.value = nextPage;
    void load();
  },
);
onMounted(() => void load());
onUnmounted(() => {
  sequence++;
});
</script>

<template>
  <div class="sd-calendar">
    <div class="sd-heading">
      <div>
        <h1>开发日历</h1>
        <p class="cal-subtitle">
          {{
            mode === "estimated"
              ? "按开发预计完成日期查看交付安排；未评估工单不进入日历。"
              : "按工单提交日期回看需求，日期以北京时间为准。"
          }}
        </p>
      </div>
      <div class="sd-flex cal-heading-actions">
        <RouterLink class="sd-button sd-primary" to="/scheduling"
          ><Icon name="calendar-days" />调整排期<small
            v-if="stats.expedite_pending"
            >{{ stats.expedite_pending }} 条加急待评估</small
          ></RouterLink
        >
        <button class="sd-button" :disabled="loading" @click="load">
          <Icon name="refresh-cw" />刷新
        </button>
      </div>
    </div>
    <div class="sd-tabs" aria-label="日历视图">
      <button
        :aria-pressed="mode === 'submitted'"
        @click="selectMode('submitted')"
      >
        提交记录
      </button>
      <button
        :aria-pressed="mode === 'estimated'"
        @click="selectMode('estimated')"
      >
        预计完成
      </button>
    </div>
    <div class="cal-toolbar">
      <div class="cal-month-controls">
        <button
          class="sd-button cal-arrow"
          aria-label="上个月"
          :disabled="month === '1000-01'"
          @click="changeMonth(-1)"
        >
          <Icon name="chevron-left" />
        </button>
        <h2>{{ monthLabel }}</h2>
        <button
          class="sd-button cal-arrow"
          aria-label="下个月"
          :disabled="month === '9998-12'"
          @click="changeMonth(1)"
        >
          <Icon name="chevron-right" />
        </button>
        <button class="sd-button" @click="selectMonth(thisMonth)">本月</button>
      </div>
      <label class="cal-month-input"
        >查看月份<input
          type="month"
          aria-label="选择月份"
          min="1000-01"
          max="9998-12"
          :value="month"
          @change="selectMonth(($event.target as HTMLInputElement).value)"
      /></label>
    </div>
    <div v-if="error" class="sd-notice" role="alert">
      <span>{{ error }}</span
      ><button class="sd-quiet" @click="load">重试</button>
    </div>
    <div class="cal-overview" aria-live="polite">
      <span
        ><strong>{{ readyData?.month_count ?? "—" }}</strong>
        {{ mode === "estimated" ? "本月预计完成" : "本月提交" }}</span
      >
      <span
        ><strong>{{ readyData?.days.length ?? "—" }}</strong> 个{{
          mode === "estimated" ? "交付日" : "提交日"
        }}</span
      >
      <span v-if="urgentCount" class="cal-urgent-summary"
        ><span class="sd-dot"></span>{{ urgentCount }} 条紧急工单</span
      >
      <RouterLink
        v-if="mode === 'estimated' && readyData"
        :to="{ path: '/tickets', query: { schedule: 'unassessed' } }"
        class="sd-quiet"
        >{{ readyData.unassessed_count }} 条待评估（全部月份）</RouterLink
      >
      <span class="cal-overview-note">{{
        mode === "estimated"
          ? "按最新排期展示，包含待验收及已完成工单"
          : "包含已完成工单"
      }}</span>
    </div>
    <div class="cal-layout" :aria-busy="loading">
      <section
        class="cal-month"
        :aria-label="mode === 'estimated' ? '工单预计完成日历' : '工单提交日历'"
      >
        <div class="cal-weekdays" aria-hidden="true">
          <span
            v-for="day in ['一', '二', '三', '四', '五', '六', '日']"
            :key="day"
            >周{{ day }}</span
          >
        </div>
        <div class="cal-grid">
          <button
            v-for="day in dates"
            :key="day.date"
            class="cal-day"
            :class="{
              'is-outside': !day.currentMonth,
              'is-today': day.date === today(),
              'is-selected': day.date === selected,
              'has-tickets': byDate.has(day.date),
            }"
            :disabled="!day.currentMonth"
            :aria-pressed="day.date === selected"
            :aria-current="day.date === today() ? 'date' : undefined"
            :aria-label="`${day.date}，${byDate.get(day.date)?.count || 0} 条工单`"
            @click="selectDate(day.date)"
          >
            <span class="cal-day-top"
              ><span class="cal-day-number">{{ day.day }}</span
              ><small v-if="day.date === today()">今天</small></span
            >
            <span v-if="byDate.has(day.date)" class="cal-day-count"
              >{{ byDate.get(day.date)?.count }} <small>条工单</small></span
            >
            <span
              v-if="byDate.get(day.date)?.urgent_count"
              class="cal-day-urgent"
              ><span class="sd-dot"></span
              >{{ byDate.get(day.date)?.urgent_count }} 紧急</span
            >
          </button>
        </div>
        <p class="cal-caption">
          {{
            mode === "estimated"
              ? "点击日期查看预计当天完成的工单；历史调整保留在工单时间线中。"
              : "点击日期查看当天提交的工单。"
          }}
        </p>
      </section>
      <section class="cal-daily" aria-labelledby="cal-daily-title">
        <div class="cal-daily-heading">
          <div>
            <span class="cal-daily-year">{{ month.slice(0, 4) }}</span>
            <h2 id="cal-daily-title">{{ selectedLabel }}</h2>
          </div>
          <span class="cal-total">{{
            loading ? "加载中" : `${data?.count || 0} 条工单`
          }}</span>
        </div>
        <div v-if="loading" class="cal-empty" role="status">正在加载工单…</div>
        <div v-else-if="error" class="cal-empty">工单暂未加载，请重试。</div>
        <div v-else-if="!data?.results.length" class="cal-empty">
          <Icon name="calendar-days" />
          <p>
            {{
              mode === "estimated"
                ? "这一天没有预计完成的工单"
                : "这一天没有提交工单"
            }}
          </p>
          <small>可以选择其他日期，或切换月份查看历史。</small>
        </div>
        <ol v-else class="cal-tickets">
          <li v-for="ticket in data.results" :key="ticket.id">
            <RouterLink
              class="cal-ticket"
              :to="{
                path: `/tickets/${ticket.id}`,
                query: {
                  from: 'calendar',
                  calendar_mode: mode,
                  calendar_month: month,
                  calendar_date: selected,
                  calendar_page: String(page),
                },
              }"
            >
              <div class="cal-ticket-meta">
                <span>{{ ticket.number }}</span
                ><time
                  v-if="mode === 'submitted'"
                  :datetime="ticket.created_at"
                  >{{ timeOfDay(ticket.created_at) }}</time
                >
              </div>
              <h3>{{ ticket.title }}</h3>
              <div v-if="mode === 'estimated'" class="sd-small">
                <ScheduleDate :ticket="ticket" />
              </div>
              <div class="cal-ticket-person">
                <span class="cal-module">{{ ticket.module }}</span
                ><span>{{ ticket.owner_name }} 提交</span
                ><Icon name="arrow-up-right" />
              </div>
              <div class="cal-ticket-footer">
                <span class="cal-priority" :class="ticket.priority"
                  ><span class="sd-dot"></span>紧急程度：{{
                    priorities[ticket.priority] || ticket.priority
                  }}</span
                ><Status :value="ticket.status" />
              </div>
            </RouterLink>
          </li>
        </ol>
        <div v-if="!loading && !error && pages > 1" class="cal-pagination">
          <button
            class="sd-button cal-arrow"
            :disabled="page <= 1"
            aria-label="上一页工单"
            @click="turn(page - 1)"
          >
            <Icon name="chevron-left" /></button
          ><span>{{ page }} / {{ pages }}</span
          ><button
            class="sd-button cal-arrow"
            :disabled="page >= pages"
            aria-label="下一页工单"
            @click="turn(page + 1)"
          >
            <Icon name="chevron-right" />
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<style>
#sap-desk-design .sd-calendar .cal-subtitle {
  margin-top: 9px;
  font-size: 12px;
  color: var(--sd-muted);
}
#sap-desk-design .sd-calendar .cal-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 22px;
}
#sap-desk-design .sd-calendar .cal-month-controls {
  display: flex;
  align-items: center;
  gap: 12px;
}
#sap-desk-design .sd-calendar .cal-month-controls h2 {
  min-width: 136px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
#sap-desk-design .sd-calendar .cal-arrow {
  padding: 9px;
  min-width: 38px;
}
#sap-desk-design .sd-calendar .cal-month-input {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--sd-muted);
}
#sap-desk-design .sd-calendar .cal-month-input input {
  font-size: 12px;
  padding: 8px 10px;
}
#sap-desk-design .sd-calendar .cal-overview {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 22px;
  padding: 14px 0;
  border-top: 1px solid var(--sd-line);
  margin-bottom: 14px;
  color: var(--sd-muted);
  font-size: 12px;
}
#sap-desk-design .sd-calendar .cal-overview strong {
  font-size: 20px;
  font-weight: 500;
  color: var(--sd-ink);
  margin-right: 5px;
  font-variant-numeric: tabular-nums;
}
#sap-desk-design .sd-calendar .cal-overview-note {
  margin-left: auto;
  font-size: 11px;
}
#sap-desk-design .sd-calendar .cal-urgent-summary {
  color: var(--sd-red);
  display: flex;
  align-items: center;
  gap: 6px;
}
#sap-desk-design .sd-calendar .cal-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 24px;
  align-items: start;
}
#sap-desk-design .sd-calendar .cal-weekdays,
#sap-desk-design .sd-calendar .cal-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
}
#sap-desk-design .sd-calendar .cal-weekdays {
  color: var(--sd-muted);
  font-size: 11px;
  text-align: center;
  padding: 10px 0;
}
#sap-desk-design .sd-calendar .cal-weekdays span:nth-last-child(-n + 2) {
  color: var(--sd-red);
}
#sap-desk-design .sd-calendar .cal-grid {
  border-top: 1px solid var(--sd-line);
  border-left: 1px solid var(--sd-line);
  border-radius: 8px;
  overflow: hidden;
}
#sap-desk-design .sd-calendar .cal-day {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 10px;
  min-width: 0;
  min-height: 105px;
  padding: 12px 9px;
  border-right: 1px solid var(--sd-line);
  border-bottom: 1px solid var(--sd-line);
  text-align: left;
  background: var(--sd-paper);
  cursor: pointer;
  transition: background 0.15s;
}
#sap-desk-design .sd-calendar .cal-day:hover {
  background: var(--sd-tint);
}
#sap-desk-design .sd-calendar .cal-day:focus-visible {
  outline: 2px solid var(--sd-accent);
  outline-offset: -3px;
  z-index: 1;
}
#sap-desk-design .sd-calendar .cal-day.is-outside {
  background: var(--sd-bg);
  opacity: 1;
  color: var(--sd-muted);
}
#sap-desk-design .sd-calendar .cal-day.is-outside .cal-day-number {
  opacity: 0.45;
}
#sap-desk-design .sd-calendar .cal-day.is-selected {
  background: var(--sd-tint);
  box-shadow: inset 0 0 0 2px var(--sd-accent);
}
#sap-desk-design .sd-calendar .cal-day-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 3px;
}
#sap-desk-design .sd-calendar .cal-day-number {
  font-size: 13px;
  line-height: 24px;
  width: 24px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
#sap-desk-design .sd-calendar .cal-day.is-today .cal-day-number {
  border-radius: 50%;
  background: var(--sd-accent);
  color: var(--sd-on);
}
#sap-desk-design .sd-calendar .cal-day-top small {
  font-size: 9px;
  color: var(--sd-muted);
}
#sap-desk-design .sd-calendar .cal-day-count {
  font-size: 17px;
  font-weight: 500;
  padding-left: 4px;
  line-height: 1.3;
  color: var(--sd-ink);
}
#sap-desk-design .sd-calendar .cal-day-count small {
  font-size: 10px;
  font-weight: 400;
  color: var(--sd-muted);
}
#sap-desk-design .sd-calendar .cal-day-urgent {
  padding-left: 4px;
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  color: var(--sd-red);
}
#sap-desk-design .sd-calendar .cal-caption {
  padding-top: 12px;
  font-size: 11px;
  color: var(--sd-muted);
}
#sap-desk-design .sd-calendar .cal-daily {
  border: 1px solid var(--sd-line);
  border-radius: 8px;
  overflow: hidden;
  background: var(--sd-paper);
}
#sap-desk-design .sd-calendar .cal-daily-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px;
  border-bottom: 1px solid var(--sd-line);
}
#sap-desk-design .sd-calendar .cal-daily-year {
  color: var(--sd-muted);
  font-size: 10px;
  letter-spacing: 2px;
}
#sap-desk-design .sd-calendar .cal-total {
  color: var(--sd-muted);
  font-size: 11px;
}
#sap-desk-design .sd-calendar .cal-empty {
  min-height: 235px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 9px;
  padding: 30px 20px;
  color: var(--sd-muted);
  text-align: center;
  font-size: 12px;
}
#sap-desk-design .sd-calendar .cal-empty > svg {
  width: 26px;
  height: 26px;
  opacity: 0.55;
  margin-bottom: 5px;
}
#sap-desk-design .sd-calendar .cal-empty small {
  font-size: 11px;
}
#sap-desk-design .sd-calendar .cal-tickets {
  list-style: none;
  padding: 0;
  margin: 0;
  max-height: 470px;
  overflow-y: auto;
  scrollbar-width: thin;
}
#sap-desk-design .sd-calendar .cal-tickets li + li {
  border-top: 1px solid var(--sd-line);
}
#sap-desk-design .sd-calendar .cal-ticket {
  display: block;
  padding: 17px 18px;
  color: inherit;
  text-decoration: none;
  transition: background 0.15s;
}
#sap-desk-design .sd-calendar .cal-ticket:hover {
  background: var(--sd-bg);
}
#sap-desk-design .sd-calendar .cal-ticket:focus-visible {
  outline: 2px solid var(--sd-accent);
  outline-offset: -3px;
}
#sap-desk-design .sd-calendar .cal-ticket-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--sd-muted);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}
#sap-desk-design .sd-calendar .cal-ticket h3 {
  font-size: 13px;
  line-height: 1.7;
  margin: 8px 0 9px;
  overflow-wrap: anywhere;
}
#sap-desk-design .sd-calendar .cal-ticket-person {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--sd-muted);
  font-size: 11px;
}
#sap-desk-design .sd-calendar .cal-ticket-person > svg {
  margin-left: auto;
  width: 14px;
  height: 14px;
}
#sap-desk-design .sd-calendar .cal-module {
  padding: 1px 6px;
  border: 1px solid var(--sd-line);
  border-radius: 4px;
  color: var(--sd-ink);
  font-size: 10px;
}
#sap-desk-design .sd-calendar .cal-ticket-footer {
  margin-top: 13px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
#sap-desk-design .sd-calendar .cal-priority {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--sd-muted);
  font-size: 10px;
}
#sap-desk-design .sd-calendar .cal-priority.high,
#sap-desk-design .sd-calendar .cal-priority.urgent {
  color: var(--sd-red);
}
#sap-desk-design .sd-calendar .cal-pagination {
  padding: 12px 18px;
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 14px;
  border-top: 1px solid var(--sd-line);
  font-size: 11px;
  color: var(--sd-muted);
}
@media (max-width: 1180px) {
  #sap-desk-design .sd-calendar .cal-layout {
    grid-template-columns: minmax(0, 1fr);
  }
  #sap-desk-design .sd-calendar .cal-empty {
    min-height: 150px;
  }
}
@media (max-width: 600px) {
  #sap-desk-design .sd-calendar .cal-tickets {
    max-height: none;
  }
  #sap-desk-design .sd-calendar .cal-toolbar {
    gap: 12px;
  }
  #sap-desk-design .sd-calendar .cal-month-controls {
    gap: 7px;
  }
  #sap-desk-design .sd-calendar .cal-month-controls h2 {
    font-size: 17px;
    min-width: 115px;
  }
  #sap-desk-design .sd-calendar .cal-overview {
    gap: 12px;
  }
  #sap-desk-design .sd-calendar .cal-overview-note {
    width: 100%;
    margin-left: 0;
  }
  #sap-desk-design .sd-calendar .cal-day {
    min-height: 86px;
    padding: 8px 3px;
    gap: 8px;
  }
  #sap-desk-design .sd-calendar .cal-day-count {
    font-size: 15px;
  }
  #sap-desk-design .sd-calendar .cal-day-count small {
    display: none;
  }
  #sap-desk-design .sd-calendar .cal-day-urgent {
    font-size: 9px;
    gap: 3px;
    padding-left: 2px;
  }
  #sap-desk-design .sd-calendar .cal-day-top {
    justify-content: center;
  }
  #sap-desk-design .sd-calendar .cal-day-top small {
    display: none;
  }
}
</style>
