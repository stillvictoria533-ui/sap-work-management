<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router";
import { api, post } from "../api";
import { dateTime, type Page, type Ticket } from "../types";
import {
  isActiveTurn,
  replaceTurn,
  type ChatConversation,
  type ChatStatus,
  type ChatTurn,
} from "../chat";
import Icon from "../components/Icon.vue";
import ChatMessage from "../components/ChatMessage.vue";

const route = useRoute(),
  router = useRouter();
const history = ref<ChatConversation[]>([]),
  historyCount = ref(0),
  historyPage = ref(1),
  historyBusy = ref(false);
const current = ref<ChatConversation | null>(null),
  status = ref<ChatStatus | null>(null),
  draft = ref("");
const system = ref("DS4"),
  linkedTicket = ref<ChatConversation["ticket"]>(null);
const loading = ref(false),
  sending = ref(false),
  error = ref(""),
  streamHint = ref(""),
  historyOpen = ref(false);
const ticketDialog = ref<HTMLDialogElement>(),
  ticketSearch = ref(""),
  ticketResults = ref<Ticket[]>([]),
  ticketBusy = ref(false),
  ticketError = ref("");
const input = ref<HTMLTextAreaElement>(),
  feed = ref<HTMLElement>(),
  copied = ref("");
const turns = computed(() => current.value?.turns || []);
const lastTurn = computed(() => turns.value.at(-1));
const active = computed(() => isActiveTurn(lastTurn.value));
const suggestions = [
  {
    icon: "message-circle",
    title: "接口问题排查",
    text: "从错误信息开始，整理排查路径",
    prompt:
      "帮我梳理 SAP 接口报错的排查步骤。我会补充错误信息、发生时间和相关单号。",
  },
  {
    icon: "file-text",
    title: "ABAP 代码分析",
    text: "解释逻辑、判断条件与异常",
    prompt:
      "帮我分析下面这段 ABAP 代码，说明数据来源、主要逻辑和潜在问题：\n\n",
  },
  {
    icon: "search",
    title: "表结构与字段",
    text: "理解字段含义与数据关系",
    prompt:
      "帮我解释这个 SAP 表和字段的用途，以及应该如何确认它的数据来源：\n\n",
  },
  {
    icon: "shield-check",
    title: "需求影响分析",
    text: "整理影响范围与测试场景",
    prompt: "帮我评估以下 SAP 需求可能涉及的对象和测试场景：\n\n",
  },
];
let stream: EventSource | null = null,
  sequence = 0,
  ticketSequence = 0,
  closed = false;
let statusTimer: ReturnType<typeof setInterval>,
  ticketTimer: ReturnType<typeof setTimeout>;
let pending: {
  key: string;
  text: string;
  conversation: string | null;
  createKey: string;
  system: string;
  ticket: number | null;
} | null = null;
const drafts = new Map<string, string>();

async function loadStatus() {
  try {
    status.value = await api<ChatStatus>("/assistant/status");
  } catch {
    /* Keep existing connection state while the request recovers. */
  }
}
async function loadHistory(more = false) {
  historyBusy.value = true;
  try {
    const page = more ? historyPage.value + 1 : 1;
    const data = await api<Page<ChatConversation>>(
      "/assistant/conversations?page=" + page,
    );
    history.value = more
      ? [
          ...history.value,
          ...data.results.filter(
            (c) => !history.value.some((v) => v.id === c.id),
          ),
        ]
      : data.results;
    historyCount.value = data.count;
    historyPage.value = page;
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    historyBusy.value = false;
  }
}
function closeStream() {
  stream?.close();
  stream = null;
  streamHint.value = "";
}
async function scrollBottom(force = false) {
  const el = feed.value;
  const nearBottom =
    !el || el.scrollHeight - el.scrollTop - el.clientHeight < 130;
  await nextTick();
  if (feed.value && (force || nearBottom))
    feed.value.scrollTop = feed.value.scrollHeight;
}
function updateTurn(value: ChatTurn) {
  if (!current.value) return;
  current.value.turns = replaceTurn(current.value.turns || [], value);
  void scrollBottom();
}
function followTurn() {
  closeStream();
  if (!current.value || !active.value || closed) return;
  const id = current.value.id,
    turn = lastTurn.value!;
  const connection = new EventSource(
    `/api/assistant/conversations/${id}/turns/${turn.id}/stream`,
  );
  stream = connection;
  let failures = 0;
  connection.addEventListener("snapshot", (event) => {
    if (current.value?.id !== id || closed) return;
    try {
      updateTurn(JSON.parse((event as MessageEvent).data));
      failures = 0;
      streamHint.value = "";
    } catch {
      streamHint.value = "正在恢复回答状态…";
    }
  });
  connection.addEventListener("end", () => {
    connection.close();
    if (stream === connection) stream = null;
    streamHint.value = "";
    void loadHistory();
    void loadStatus();
  });
  connection.addEventListener("revoked", () => {
    connection.close();
    error.value = "登录状态或访问权限已变化，请重新登录。";
    current.value = null;
  });
  connection.onerror = () => {
    if (stream !== connection || closed) return;
    streamHint.value = "连接暂时中断，正在恢复…";
    if (++failures >= 4) {
      connection.close();
      streamHint.value = "连接已中断，问题仍在后台处理。请点击“刷新对话”恢复。";
    }
  };
}
async function loadConversation(id: string) {
  const token = ++sequence;
  closeStream();
  loading.value = true;
  error.value = "";
  try {
    const data = await api<ChatConversation>("/assistant/conversations/" + id);
    if (token !== sequence || closed) return;
    current.value = data;
    system.value = data.system;
    linkedTicket.value = data.ticket;
    followTurn();
    await scrollBottom(true);
  } catch (e) {
    if (token === sequence) {
      current.value = null;
      error.value = (e as Error).message;
    }
  } finally {
    if (token === sequence) loading.value = false;
  }
}
watch(
  () => route.params.id,
  (value, previous) => {
    if (previous !== undefined)
      drafts.set(String(previous || "new"), draft.value);
    draft.value = drafts.get(String(value || "new")) || "";
    historyOpen.value = false;
    if (value) {
      if (current.value?.id !== value) {
        current.value = null;
        void loadConversation(String(value));
      }
    } else {
      sequence++;
      closeStream();
      current.value = null;
      linkedTicket.value = null;
      system.value = "DS4";
      error.value = "";
      loading.value = false;
    }
  },
  { immediate: true },
);
onMounted(() => {
  void loadStatus();
  void loadHistory();
  statusTimer = setInterval(() => {
    if (!document.hidden) void loadStatus();
  }, 15000);
});
onBeforeUnmount(() => {
  closed = true;
  sequence++;
  closeStream();
  clearInterval(statusTimer);
  clearTimeout(ticketTimer);
});
onBeforeRouteLeave(
  () =>
    !draft.value.trim() || window.confirm("内容尚未发送，确定离开 AI 助手？"),
);

async function send() {
  const text = draft.value.trim();
  if (
    !text ||
    text.length > 12000 ||
    sending.value ||
    active.value ||
    loading.value
  )
    return;
  sending.value = true;
  error.value = "";
  if (
    !pending ||
    pending.text !== text ||
    pending.conversation !== (current.value?.id || null)
  ) {
    pending = {
      key: crypto.randomUUID(),
      createKey: crypto.randomUUID(),
      text,
      conversation: current.value?.id || null,
      system: system.value,
      ticket: linkedTicket.value?.id || null,
    };
  }
  try {
    if (!current.value) {
      current.value = await post<ChatConversation>("/assistant/conversations", {
        request_key: pending.createKey,
        system: pending.system,
        ticket_id: pending.ticket,
      });
      pending.conversation = current.value.id;
      await router.replace("/assistant/" + current.value.id);
    }
    const id = current.value.id;
    const value = await post<ChatTurn>(`/assistant/conversations/${id}/turns`, {
      request_key: pending.key,
      message: text,
    });
    if (closed || current.value?.id !== id) return;
    updateTurn(value);
    if (current.value.title === "新对话")
      current.value.title = text.replace(/\s+/g, " ").slice(0, 60);
    draft.value = "";
    drafts.delete("new");
    drafts.delete(id);
    pending = null;
    followTurn();
    void loadHistory();
    await scrollBottom(true);
  } catch (e) {
    error.value = (e as Error).message;
    draft.value = text;
  } finally {
    sending.value = false;
  }
}
async function newChat() {
  if (sending.value) return;
  if (!route.params.id) {
    draft.value = "";
    linkedTicket.value = null;
    error.value = "";
  } else await router.push("/assistant");
  await nextTick();
  input.value?.focus();
}
async function retry(turn: ChatTurn) {
  if (!current.value || sending.value) return;
  sending.value = true;
  error.value = "";
  try {
    updateTurn(
      await post<ChatTurn>(
        `/assistant/conversations/${current.value.id}/turns/${turn.id}/retry`,
        { attempt: turn.attempt },
      ),
    );
    followTurn();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    sending.value = false;
  }
}
async function cancel() {
  if (!current.value || !lastTurn.value) return;
  sending.value = true;
  try {
    updateTurn(
      await post<ChatTurn>(
        `/assistant/conversations/${current.value.id}/turns/${lastTurn.value.id}/cancel`,
      ),
    );
    closeStream();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    sending.value = false;
  }
}
function choosePrompt(text: string) {
  draft.value = text;
  input.value?.focus();
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    void send();
  }
}
async function copy(turn: ChatTurn) {
  try {
    await navigator.clipboard.writeText(turn.output_text);
    copied.value = turn.id;
    setTimeout(() => (copied.value = ""), 1800);
  } catch {
    error.value = "复制失败，请选中回答文字后手动复制。";
  }
}
async function searchTickets() {
  const seq = ++ticketSequence;
  ticketBusy.value = true;
  ticketError.value = "";
  try {
    const data = await api<Page<Ticket>>(
      "/tickets?q=" + encodeURIComponent(ticketSearch.value),
    );
    if (seq === ticketSequence) ticketResults.value = data.results;
  } catch (e) {
    if (seq === ticketSequence) ticketError.value = (e as Error).message;
  } finally {
    if (seq === ticketSequence) ticketBusy.value = false;
  }
}
function openTickets() {
  ticketDialog.value?.showModal();
  void searchTickets();
}
watch(ticketSearch, () => {
  clearTimeout(ticketTimer);
  ticketTimer = setTimeout(searchTickets, 250);
});
function selectTicket(ticket: Ticket) {
  linkedTicket.value = {
    id: ticket.id,
    title: ticket.title,
    number: ticket.number,
  };
  ticketDialog.value?.close();
}
</script>
<template>
  <div class="ai-layout">
    <aside
      class="ai-history"
      :class="{ 'is-open': historyOpen }"
      aria-label="我的 AI 对话"
    >
      <button class="ai-new" @click="newChat" :disabled="sending">
        <Icon name="plus" />新对话
      </button>
      <div class="ai-history-heading">
        <span>我的对话</span><small>{{ historyCount }}</small>
      </div>
      <p v-if="!history.length && !historyBusy" class="ai-history-empty">
        从右侧开始提问。<br />对话会自动保存在这里。
      </p>
      <button
        v-for="item in history"
        :key="item.id"
        class="ai-history-item"
        :aria-current="current?.id === item.id ? 'true' : undefined"
        :disabled="sending"
        @click="router.push('/assistant/' + item.id)"
      >
        <span>{{ item.title }}</span
        ><small
          >{{ item.system }} ·
          {{ dateTime(item.updated_at).slice(0, 10) }}</small
        >
      </button>
      <button
        v-if="history.length < historyCount"
        class="sd-quiet ai-more"
        :disabled="historyBusy"
        @click="loadHistory(true)"
      >
        {{ historyBusy ? "加载中…" : "加载更多" }}
      </button>
      <div class="ai-history-footer">
        <Icon name="shield-check" /><span>对话仅自己可见</span>
      </div>
    </aside>
    <section class="ai-workspace" aria-label="SAP AI 助手">
      <header class="ai-header">
        <div class="ai-title-row">
          <button
            class="ai-history-toggle sd-quiet"
            aria-label="展开对话历史"
            :aria-expanded="historyOpen"
            @click="historyOpen = !historyOpen"
          >
            <Icon name="panel-left" />
          </button>
          <div>
            <h1>SAP AI 助手</h1>
            <p>分析问题，梳理思路，让每一步都有依据。</p>
          </div>
        </div>
        <span class="ai-connection"><Icon name="unplug" />SAP 未接通</span>
      </header>
      <div class="ai-toolbar">
        <label class="ai-system-label"
          ><span class="sd-small sd-muted">问题所属系统</span
          ><select
            v-model="system"
            aria-label="问题所属 SAP 系统"
            :disabled="!!current || sending"
          >
            <option value="DS4">DS4 / 110 · 开发</option>
            <option value="QS4">QS4 / 120 · 测试</option>
          </select></label
        ><span class="ai-readonly"
          ><Icon name="shield-check" />仅分析，不执行</span
        ><button
          v-if="!current && !linkedTicket"
          class="sd-quiet ai-link"
          @click="openTickets"
          :disabled="sending"
        >
          <Icon name="link" />关联工单</button
        ><button
          v-if="current"
          class="sd-quiet ai-link"
          :disabled="sending || loading"
          @click="loadConversation(current.id)"
        >
          <Icon name="refresh-cw" />刷新对话
        </button>
      </div>
      <div v-if="linkedTicket" class="ai-ticket-context">
        <Icon name="link" /><RouterLink :to="'/tickets/' + linkedTicket.id"
          >{{ linkedTicket.number }} · {{ linkedTicket.title }}</RouterLink
        ><button
          v-if="!current"
          class="sd-quiet"
          aria-label="取消关联工单"
          @click="linkedTicket = null"
        >
          <Icon name="x" />
        </button>
      </div>
      <div
        v-if="status && !status.provider.ready"
        class="ai-service-note"
        role="status"
      >
        <Icon name="circle-alert" /><span>{{ status.provider.message }}</span
        ><button class="sd-quiet" @click="loadStatus">检查状态</button>
      </div>
      <div v-if="error" class="sd-error ai-error" role="alert">{{ error }}</div>
      <div v-if="loading" class="ai-loading" role="status">
        <Icon name="loader-circle" />正在加载对话…
      </div>
      <div
        v-else
        ref="feed"
        class="ai-feed"
        :class="{ 'is-empty': !turns.length }"
      >
        <div v-if="!turns.length" class="ai-welcome">
          <div class="ai-welcome-mark"><Icon name="sparkles" /></div>
          <h2>今天想解决什么 SAP 问题？</h2>
          <p>
            描述业务现象，或粘贴代码与错误日志。<br />SAP
            尚未接通，我会基于你提供的材料分析。
          </p>
          <div class="ai-suggestions">
            <button
              v-for="item in suggestions"
              :key="item.title"
              @click="choosePrompt(item.prompt)"
            >
              <Icon :name="item.icon" /><span
                ><strong>{{ item.title }}</strong
                ><small>{{ item.text }}</small></span
              ><Icon name="arrow-up-right" />
            </button>
          </div>
        </div>
        <article v-for="turn in turns" :key="turn.id" class="ai-turn">
          <div class="ai-user-message">
            <span class="ai-message-time"
              >你 · {{ dateTime(turn.created_at) }}</span
            >
            <div>{{ turn.input_text }}</div>
          </div>
          <div class="ai-answer">
            <div class="ai-answer-label">
              <span class="ai-answer-mark"><Icon name="sparkles" /></span
              ><strong>{{
                turn.status === "blocked" ? "问题已保存" : "SAP AI 助手"
              }}</strong
              ><span
                v-if="turn.status === 'completed'"
                class="ai-answer-model"
                >{{ status?.provider.name || "AI" }}</span
              >
            </div>
            <ChatMessage v-if="turn.output_text" :text="turn.output_text" />
            <div v-if="isActiveTurn(turn)" class="ai-progress" role="status">
              <span class="ai-pulse"></span
              >{{ turn.status === "queued" ? "等待处理…" : "正在分析你的问题…"
              }}<span class="sd-small sd-muted"
                >可离开页面，回答会自动保存</span
              >
            </div>
            <div
              v-if="turn.error_message"
              class="ai-turn-notice"
              :class="{ 'is-failure': turn.status === 'failed' }"
            >
              <Icon
                :name="turn.status === 'cancelled' ? 'square' : 'circle-alert'"
              /><span>{{ turn.error_message }}</span>
            </div>
            <div class="ai-answer-actions">
              <button
                v-if="turn.output_text"
                class="sd-quiet"
                @click="copy(turn)"
              >
                <Icon :name="copied === turn.id ? 'check' : 'copy'" />{{
                  copied === turn.id ? "已复制" : "复制回答"
                }}</button
              ><button
                v-if="
                  turn.id === lastTurn?.id &&
                  ['blocked', 'failed', 'cancelled'].includes(turn.status)
                "
                class="sd-quiet"
                :disabled="sending || !status?.provider.ready"
                @click="retry(turn)"
              >
                <Icon name="refresh-cw" />重试</button
              ><span v-if="turn.status === 'completed'" class="ai-source"
                >依据聊天材料{{
                  turn.context_meta.ticket ? "与关联工单" : ""
                }}
                · 未查询 SAP</span
              >
            </div>
            <p
              v-if="turn.context_meta.history_truncated"
              class="ai-context-note"
            >
              本次使用最近可容纳的已完成对话，较早内容未加入；必要时请重新补充。
            </p>
          </div>
        </article>
      </div>
      <p v-if="streamHint" class="ai-stream-hint" role="status">
        {{ streamHint }}
      </p>
      <form class="ai-composer" @submit.prevent="send">
        <label class="ai-input-label" for="ai-message">向 SAP AI 助手提问</label
        ><textarea
          id="ai-message"
          ref="input"
          v-model="draft"
          rows="3"
          maxlength="12000"
          placeholder="描述问题，例如：这段 ABAP 为什么没有查到采购订单？"
          :disabled="loading || sending"
          @keydown="onKeydown"
        ></textarea>
        <div class="ai-composer-footer">
          <span>{{
            draft.length
              ? draft.length.toLocaleString() + " / 12,000"
              : "Enter 发送 · Shift + Enter 换行"
          }}</span
          ><button
            v-if="active"
            type="button"
            class="ai-stop"
            :disabled="sending"
            @click="cancel"
          >
            <Icon name="square" />停止生成</button
          ><button
            v-else
            class="ai-send"
            type="submit"
            :disabled="!draft.trim() || sending || loading"
          >
            <span>{{ sending ? "保存中" : "发送" }}</span
            ><Icon name="arrow-up" />
          </button>
        </div>
      </form>
      <footer class="ai-footer">
        <span
          class="ai-provider-dot"
          :class="{ ready: status?.provider.ready }"
        ></span
        >{{
          status?.provider.ready
            ? status.provider.name + " 已就绪"
            : "AI 服务待就绪"
        }}<span>·</span>SAP 未接通，回答不代表系统实时结果。
      </footer>
    </section>
    <dialog ref="ticketDialog" class="ai-ticket-dialog">
      <div class="ai-dialog-heading">
        <h2>关联工单</h2>
        <button
          class="sd-quiet"
          aria-label="关闭工单选择"
          @click="ticketDialog?.close()"
        >
          <Icon name="x" />
        </button>
      </div>
      <p>
        将有权访问的工单正文及最近沟通加入当前对话。附件不会自动读取，AI
        回答不会写回工单。
      </p>
      <input
        v-model="ticketSearch"
        aria-label="搜索要关联的工单"
        placeholder="搜索编号或标题"
      />
      <div v-if="ticketError" class="sd-error" role="alert">
        {{ ticketError }}
      </div>
      <p v-if="ticketBusy" role="status">正在查找…</p>
      <p v-else-if="!ticketResults.length">没有找到可关联的工单。</p>
      <div class="ai-ticket-options">
        <button
          v-for="ticket in ticketResults"
          :key="ticket.id"
          @click="selectTicket(ticket)"
        >
          <small>{{ ticket.number }} · {{ ticket.module }}</small
          ><span>{{ ticket.title }}</span>
        </button>
      </div>
    </dialog>
  </div>
</template>
<style src="../assistant.css"></style>
