<script setup lang="ts">
import { onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import { user } from "../state";
import { setupMotion } from "../motion";
import Icon from "../components/Icon.vue";
const router = useRouter();
let cleanup = () => {};
onMounted(() => {
  cleanup = setupMotion();
});
onUnmounted(() => cleanup());
function handleClick(event: MouseEvent) {
  const target = (event.target as HTMLElement).closest(
    "button,a",
  ) as HTMLElement | null;
  if (!target) return;
  if (target.dataset.nav === "login") {
    void router.push(user.value ? "/tickets" : "/login");
  } else if (target.tagName === "A") {
    event.preventDefault();
    const id = target.getAttribute("href");
    if (id?.startsWith("#"))
      document
        .querySelector(id)
        ?.scrollIntoView({
          behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "instant"
            : "smooth",
        });
  }
}
</script>
<template>
  <section
    id="sd-home-screen"
    class="sd-home"
    @click="handleClick"
    aria-label="SAP Desk 首页"
  >
    <header class="sd-home-header">
      <a class="sd-home-brand" href="#top" aria-label="SAP Desk 首页"
        ><span class="sd-mark" aria-hidden="true"
          ><b></b><b></b><b></b><b></b></span
        >SAP Desk<span class="sd-home-brand-note">需求协作</span></a
      >
      <nav aria-label="首页导航">
        <a class="sd-how-link" href="#journey">协作流程</a
        ><button class="sd-home-entry" type="button" data-nav="login">
          <span class="sd-entry-icon"><Icon name="arrow-up-right" /></span
          >进入工作台
        </button>
      </nav>
    </header>
    <div class="sd-home-hero" id="top">
      <h1><span>从一个需求，</span><span>到一次交付。</span></h1>
      <div class="sd-home-subline">
        <span>SAP 需求协作空间</span
        ><a href="#journey" aria-label="向下浏览协作流程"
          >向下探索 <Icon name="arrow-down"
        /></a>
      </div>
    </div>
    <section
      class="sd-journey"
      id="journey"
      aria-label="滚动浏览：提交需求、沟通反馈、完成交付"
    >
      <div class="sd-journey-stage">
        <div class="sd-stage-grid" aria-hidden="true"></div>
        <div class="sd-stage-arch" aria-hidden="true"><div></div></div>
        <div class="sd-stage-shadow" aria-hidden="true"></div>
        <div class="sd-stage-copy">
          <div class="sd-stage-copy-panel" data-stage-copy="0">
            <span>01 / 提交</span>
            <h2>先把需求，<br />说清楚。</h2>
            <p>一个入口，留下背景与期望。</p>
          </div>
          <div
            class="sd-stage-copy-panel"
            data-stage-copy="1"
            aria-hidden="true"
            inert
          >
            <span>02 / 沟通</span>
            <h2>每次推进，<br />都有回应。</h2>
            <p>反馈与补充，在同一条时间线上。</p>
          </div>
          <div
            class="sd-stage-copy-panel"
            data-stage-copy="2"
            aria-hidden="true"
            inert
          >
            <span>03 / 完成</span>
            <h2>让过程可追溯。<br />让结果有着落。</h2>
            <p>从受理到归档，每一步清晰可见。</p>
          </div>
        </div>
        <div class="sd-paper-scene" aria-label="工单从提交到完成的动态示意">
          <div class="sd-paper-back sd-paper-back-far" aria-hidden="true"></div>
          <div
            class="sd-paper-back sd-paper-back-near"
            aria-hidden="true"
          ></div>
          <div class="sd-coral-tab" aria-hidden="true"></div>
          <article class="sd-paper">
            <header class="sd-paper-top">
              <span>REQ / 1048</span><span class="sd-paper-module">MM</span>
            </header>
            <div class="sd-paper-panel" data-paper-panel="0">
              <div class="sd-paper-eyebrow">一项新的需求</div>
              <h3>采购审批<br />策略调整</h3>
              <div class="sd-paper-rule"></div>
              <p>为不同采购金额，<br />建立清晰的审批路径。</p>
              <footer>
                <span>林晓 · SAP 顾问</span
                ><span class="sd-paper-status">待受理</span>
              </footer>
            </div>
            <div
              class="sd-paper-panel"
              data-paper-panel="1"
              aria-hidden="true"
              inert
            >
              <div class="sd-paper-eyebrow">沟通时间线</div>
              <h3>一起，<br />把细节确认好。</h3>
              <div class="sd-paper-conversation">
                <div>
                  <b>管理员</b>
                  <p>审批策略已调整，请验证边界场景。</p>
                </div>
                <div>
                  <b>林晓</b>
                  <p>测试通过，结果符合预期。</p>
                </div>
              </div>
            </div>
            <div
              class="sd-paper-panel sd-paper-complete"
              data-paper-panel="2"
              aria-hidden="true"
              inert
            >
              <div class="sd-complete-symbol"><Icon name="check" /></div>
              <div class="sd-paper-eyebrow">REQUEST COMPLETED</div>
              <h3>有始。<br />有终。</h3>
              <p>已完成并归档，沟通记录完整保留。</p>
              <footer>
                <span>REQ / 1048</span
                ><span class="sd-paper-status">已关闭</span>
              </footer>
            </div>
          </article>
        </div>
        <div class="sd-journey-bottom">
          <span class="sd-scroll-label">随滚动，向前一步</span>
          <div class="sd-journey-steps" aria-label="跳转协作阶段">
            <button type="button" data-journey-step="0" aria-pressed="true">
              01 <span>提交</span></button
            ><button type="button" data-journey-step="1" aria-pressed="false">
              02 <span>沟通</span></button
            ><button type="button" data-journey-step="2" aria-pressed="false">
              03 <span>完成</span>
            </button>
          </div>
        </div>
      </div>
    </section>
    <section class="sd-home-end">
      <span class="sd-end-eyebrow">YOUR NEXT REQUEST STARTS HERE.</span>
      <h2>下一项协作，<br />从这里开始。</h2>
      <button class="sd-home-entry" type="button" data-nav="login">
        <span class="sd-entry-icon"><Icon name="arrow-up-right" /></span
        >进入工作台
      </button>
    </section>
    <footer class="sd-home-footer">
      <span>SAP Desk · 需求协作空间</span><span>SAP CONSULTING</span>
    </footer>
  </section>
</template>
