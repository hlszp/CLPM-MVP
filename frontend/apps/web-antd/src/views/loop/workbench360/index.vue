<!--
  回路工作台（新版）页面壳（workbench360 P1，P1-2）

  单页全生命周期工作台：监视-评估-诊断-整定-处置零跳转（设计方案 v3 §3）。
  布局（对齐原型 #app/#body，vben 全局导航/页头之外的开发范围）：
  ┌ hdr（52px，LoopHeader）
  └ body（flex 行）
    ├ spine（LoopSpine，宽 --spine-w 可拖 180-420）
    ├ vsplit（5px 垂直拖拽）
    └ main（flex 列）
      ├ rail（JourneyRail ~60px）
      ├ canvas（趋势画布 flex:1：工具栏+事件层+趋势+双滚动条）
      ├ cv-mini（最大化态迷你趋势，--mini-h 可拖 100-主列60%）
      ├ split（SplitBar 24px 三按钮+拖拽）
      ├ ws（缩略态 96px / 展开态 --ws-h %；sections 占位 P2-P4）
      └ sbar（StatusBar 深色钉底）
  小屏降级（P1-9）：视口高 <760px 时展开态工作区改绝对定位底部抽屉（52%），分屏条隐藏。
  固定视口：整页零滚动（100% 高 + overflow:hidden）。
-->
<script setup lang="ts">
import type { Component } from 'vue';

import type {
  SeriesVisible,
  TrendEventMark,
} from './components/TrendChart/types';

import {
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { message } from 'ant-design-vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import {
  WB360_DEFAULT_WINDOW_KEY,
  WB360_TREND_PALETTE_DARK,
  WB360_TREND_PALETTE_LIGHT,
  WB360_WINDOW_PRESETS,
} from '#/constants/clpm-ui';

import JourneyRail from './components/JourneyRail.vue';
import LoopHeader from './components/LoopHeader.vue';
import LoopSpine from './components/LoopSpine.vue';
import AssessSection from './components/sections/AssessSection.vue';
import DiagSection from './components/sections/DiagSection.vue';
import HandlingSection from './components/sections/HandlingSection.vue';
import TuningSection from './components/sections/TuningSection.vue';
import SplitBar from './components/SplitBar.vue';
import StatusBar from './components/StatusBar.vue';
import ThumbStrip from './components/ThumbStrip.vue';
import TrendChart from './components/TrendChart/index.vue';
import { useTrendData } from './components/TrendChart/use-trend-data';
import {
  deriveLossSummary,
  useAssessHistory,
} from './composables/use-assess-history';
import { setDiagPrefill } from './composables/use-diag-prefill';
import { MINI_DEFAULT_H, useWb360Layout } from './composables/use-wb360-layout';
import { useWb360Loop } from './composables/use-wb360-loop';

defineOptions({ name: 'LoopWorkbench360' });

const route = useRoute();
const router = useRouter();

/* ── 上下文 ── */
const initialLoopId =
  typeof route.query.loopId === 'string' && route.query.loopId
    ? route.query.loopId
    : null;
const loop = useWb360Loop(initialLoopId);
const layout = useWb360Layout();
const trend = useTrendData();
/** 页面级评估历史（P2）：剖面/旅程条/页头徽章共用，剖面经 inject 消费 */
const assessHistory = useAssessHistory(loop.selectedLoopId);
const { isDark } = useClpmTheme();

const mainRef = ref<HTMLElement | null>(null);

/* ── 趋势窗口档位 ── */
const windowKey = ref(WB360_DEFAULT_WINDOW_KEY);
const windowPresets = WB360_WINDOW_PRESETS;
const activePreset = computed(
  () =>
    windowPresets.find((p) => p.key === windowKey.value) ?? windowPresets[0]!,
);

function reloadTrend() {
  const loopId = loop.selectedLoopId.value;
  if (!loopId || activePreset.value.custom) return;
  trend.loadWindow(loopId, activePreset.value);
}

function selectWindow(key: string) {
  if (key === windowKey.value) return;
  if (key === 'custom') {
    message.info('自定义时间窗将在正式版提供起止时间选择器');
    return;
  }
  windowKey.value = key;
  reloadTrend();
}

/* ── 历史 / 实时切换 ── */
function setLive(on: boolean) {
  trend.live.value = on;
  if (!on) {
    reloadTrend();
  } else if (!trend.domain.value) {
    reloadTrend();
  }
}

/* ── 图例显隐 ── */
const seriesVisible = reactive<SeriesVisible>({
  mode: true,
  op: true,
  pv: true,
  sp: true,
});
const palette = computed(() =>
  isDark.value ? WB360_TREND_PALETTE_DARK : WB360_TREND_PALETTE_LIGHT,
);

/* ── 数据时钟（旅程条；实时=最近 WS 消息，历史=窗口末点） ── */
const clockText = computed(() => {
  const t = trend.live.value
    ? (loop.lastMessageAt.value?.getTime() ?? trend.domain.value?.t1)
    : trend.domain.value?.t1;
  if (!t) return '数据截至 --';
  const d = new Date(t);
  const p2 = (n: number) => (n < 10 ? `0${n}` : `${n}`);
  return `数据截至 ${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
});

/* ── 评估摘要（清单行真实数据 + 最新快照失分摘要，供旅程条/缩略卡） ── */
const assessSummary = computed(() => {
  const c = loop.current.value;
  if (!c) return null;
  return {
    kpiStatus: c.kpiStatus ?? null,
    score: c.score ?? null,
    scoreDelta: c.scoreDelta ?? null,
    lossSummary: deriveLossSummary(assessHistory.latest.value),
  };
});

/* ── 适用性（P2-5，G1 受阻显式提示 + 接入位） ──
 * fitness 数据出口缺失（后端 G1：snapshots 端点 fitnessLevel 恒 null、
 * /monitor summary 无该字段）。此处读取快照 fitnessLevel 作为接入位：
 * G1 落地（端点回填该字段）后自动点亮等级徽章，未落地时显式提示，禁编造。 */
const fitness = computed(() => ({
  level: assessHistory.latest.value?.fitnessLevel ?? null,
}));

/* ── 剖面渲染 ── */
const sectionComponents: Record<string, Component> = {
  assess: AssessSection,
  diag: DiagSection,
  handling: HandlingSection,
  tuning: TuningSection,
};

/** 各剖面 props（评估剖面：回路上下文；其余剖面 P3/P4 增量补充） */
const sectionProps = computed<Record<string, Record<string, unknown>>>(() => ({
  assess: {
    loopTagName: loop.current.value?.tagName ?? null,
    selectedLoopId: loop.selectedLoopId.value,
  },
}));

function onSectionOpen(key: string) {
  layout.openSection(key as never);
}

/* ── P2-4 失分→诊断联动：切诊断剖面 + 预填时间窗（不发新请求） ── */
function onDiagnoseWindow(win: { tsEnd: string; tsStart: string }) {
  if (!layout.isSectionAvailable('diag')) {
    message.warning('诊断模块未启用，无法跳转诊断剖面');
    return;
  }
  setDiagPrefill(win.tsStart, win.tsEnd);
  layout.openSection('diag');
  message.info('已切换到诊断剖面并预填该时间窗');
}

function onEventMarkClick(mark: TrendEventMark) {
  if (mark.section) layout.openSection(mark.section);
}

/* ── 分屏拖拽（页面持有主列高度上下文） ── */
let thumbsDragUp = 0;
function onSplitDragDelta(dy: number) {
  const bodyH = mainRef.value?.clientHeight ?? 0;
  const st = layout.readonlyState;
  if (st.panelState === 'max') {
    const cur = st.miniHeightPx ?? MINI_DEFAULT_H;
    layout.resizeMiniHeight(cur - dy, bodyH);
    return;
  }
  if (st.panelState === 'half') {
    const pct = st.wsHeightPct - (dy / Math.max(bodyH, 1)) * 100;
    layout.resizeWsPct(pct);
    return;
  }
  // thumbs 态：累计上拖 >24px 展开
  thumbsDragUp += -dy;
  if (thumbsDragUp > 24) {
    thumbsDragUp = 0;
    layout.setPanel('half');
  }
}

/* ── 左脊柱宽度拖拽（vsplit） ── */
function onVsplitDown(e: PointerEvent) {
  e.preventDefault();
  const sw = layout.readonlyState.spineWidth;
  const sx = e.clientX;
  const mv = (ev: PointerEvent) => {
    layout.resizeSpine(sw + (ev.clientX - sx));
  };
  const up = () => {
    window.removeEventListener('pointermove', mv);
    window.removeEventListener('pointerup', up);
  };
  window.addEventListener('pointermove', mv);
  window.addEventListener('pointerup', up);
}

/* ── 生命周期与联动 ── */
let offRealtime: (() => void) | null = null;

onMounted(() => {
  loop.startRealtime();
  offRealtime = loop.onRealtimePoint((p) => {
    trend.appendRealtimePoint(p.collectTime, p.role, p.value, p.quality);
  });
  loop.loadLoops().then(() => loop.loadTree());
});

onBeforeUnmount(() => {
  offRealtime?.();
});

// 选中回路变化：同步 query + 重载趋势窗口
watch(
  () => loop.selectedLoopId.value,
  (id) => {
    if (!id) return;
    if (route.query.loopId !== id) {
      router.replace({ query: { ...route.query, loopId: id } });
    }
    reloadTrend();
  },
);

const wsStyle = computed(() => {
  const st = layout.readonlyState;
  if (st.panelState === 'thumbs') return {};
  return { '--ws-h': `${st.wsHeightPct}%` };
});
const miniStyle = computed(() => {
  const st = layout.readonlyState;
  return { '--mini-h': `${st.miniHeightPx ?? MINI_DEFAULT_H}px` };
});

const wsName = computed(
  () => layout.activeSectionMeta.value?.label ?? '剖面工作区',
);
</script>

<template>
  <div class="wb360">
    <LoopHeader
      :connection-status="loop.connectionStatus.value"
      :fitness-level="fitness.level"
      :last-message-at="loop.lastMessageAt.value"
      :loop="loop.current.value"
    />

    <div class="wb360-body">
      <!-- 左脊柱（P1-3） -->
      <LoopSpine
        :grade-filter="loop.gradeFilter.value"
        :keyword="loop.keyword.value"
        :loops="loop.filteredLoops.value"
        :loops-error="loop.loopsError.value"
        :loops-loading="loop.loopsLoading.value"
        :selected-loop-id="loop.selectedLoopId.value"
        :selected-unit="loop.selectedUnit.value"
        :style="{ width: `${layout.readonlyState.spineWidth}px` }"
        :tree="loop.tree.value"
        :unit-total="loop.loops.value.length"
        @select-loop="loop.selectLoop($event)"
        @select-unit="loop.selectedUnit.value = $event"
        @update:grade-filter="loop.gradeFilter.value = $event"
        @update:keyword="loop.keyword.value = $event"
      />

      <!-- 脊柱-主区分隔（垂直拖拽） -->
      <div
        class="wb360-vsplit"
        title="拖动调整左脊柱宽度（180–420px）"
        @pointerdown="onVsplitDown"
      ></div>

      <!-- 主列 -->
      <main
        ref="mainRef"
        class="wb360-main"
        :class="{ max: layout.readonlyState.panelState === 'max' }"
      >
        <!-- 旅程状态条（P1-4） -->
        <JourneyRail
          :active-section="layout.activeSection.value"
          :assess="assessSummary"
          :sections="layout.availableSections.value"
          @open="onSectionOpen"
        >
          <template #clock>
            <span class="mono">{{ clockText }}</span>
          </template>
        </JourneyRail>

        <!-- 监视画布（常驻；最大化态隐藏） -->
        <section aria-label="监视画布" class="wb360-canvas">
          <div class="cv-bar">
            <div class="segctl" aria-label="历史/实时切换">
              <button
                :class="{ on: !trend.live.value }"
                type="button"
                @click="setLive(false)"
              >
                历史
              </button>
              <button
                :class="{ on: trend.live.value }"
                type="button"
                @click="setLive(true)"
              >
                实时
              </button>
            </div>
            <div class="segctl" aria-label="时间窗">
              <button
                v-for="p in windowPresets"
                :key="p.key"
                :class="{ on: windowKey === p.key }"
                type="button"
                @click="selectWindow(p.key)"
              >
                {{ p.label }}
              </button>
            </div>
            <div class="legend">
              <span
                class="lg"
                :class="{ off: !seriesVisible.pv }"
                @click="seriesVisible.pv = !seriesVisible.pv"
              >
                <span class="sw" :style="{ background: palette.pv }"></span>PV
              </span>
              <span
                class="lg"
                :class="{ off: !seriesVisible.sp }"
                @click="seriesVisible.sp = !seriesVisible.sp"
              >
                <span class="sw" :style="{ background: palette.sp }"></span>SP
              </span>
              <span
                class="lg"
                :class="{ off: !seriesVisible.op }"
                @click="seriesVisible.op = !seriesVisible.op"
              >
                <span class="sw" :style="{ background: palette.op }"></span
                >OP（右轴 %）
              </span>
              <span
                class="lg"
                :class="{ off: !seriesVisible.mode }"
                @click="seriesVisible.mode = !seriesVisible.mode"
              >
                <span class="mk">⏸</span>MODE 手动段
              </span>
            </div>
          </div>
          <div class="cv-body">
            <TrendChart
              :domain="trend.domain.value"
              :events="[]"
              :frames="trend.frames.value"
              :live="trend.live.value"
              :mode-mapping="loop.current.value?.modeMapping ?? null"
              :series-visible="seriesVisible"
              :y-domain="trend.yDomain.value"
              @event-click="onEventMarkClick"
            />
            <div v-if="trend.loading.value" class="cv-overlay">
              趋势数据加载中…
            </div>
            <div
              v-else-if="trend.error.value"
              class="cv-overlay cv-overlay-error"
            >
              {{ trend.error.value }}
            </div>
          </div>
        </section>

        <!-- 迷你趋势（最大化态，P1 恒全域视口） -->
        <div :style="miniStyle" class="wb360-cv-mini">
          <TrendChart
            :domain="trend.domain.value"
            :events="[]"
            :frames="trend.frames.value"
            :live="trend.live.value"
            :mode-mapping="loop.current.value?.modeMapping ?? null"
            :series-visible="seriesVisible"
            :y-domain="trend.yDomain.value"
            mini
            @event-click="onEventMarkClick"
          />
        </div>

        <!-- 分屏条（P1-6；小屏隐藏） -->
        <SplitBar
          class="page-split"
          :panel-state="layout.readonlyState.panelState"
          @drag-delta="onSplitDragDelta"
          @set-panel="layout.setPanel($event)"
        />

        <!-- 工作区（P1-5 缩略态 / 展开态） -->
        <section
          :style="wsStyle"
          aria-label="剖面工作区"
          class="wb360-ws"
          :class="
            layout.readonlyState.panelState === 'thumbs' ? 'thumbs' : 'open'
          "
        >
          <ThumbStrip
            v-if="layout.readonlyState.panelState === 'thumbs'"
            :assess="assessSummary"
            :sections="layout.availableSections.value"
            @open="onSectionOpen"
          />
          <template v-else>
            <div class="ws-head">
              <span class="wname">{{ wsName }}</span>
              <span class="ws-hint">剖面 · 页内完成，不跳转</span>
              <span class="spacer"></span>
              <button class="ws-close" type="button" @click="layout.closeWs()">
                收起 ▾
              </button>
            </div>
            <div class="ws-body">
              <component
                :is="
                  sectionComponents[layout.activeSection.value ?? 'assess'] ??
                  AssessSection
                "
                v-bind="sectionProps[layout.activeSection.value ?? 'assess']"
                @diagnose-window="onDiagnoseWindow"
              />
            </div>
          </template>
        </section>

        <!-- 状态栏（P1-8，深色钉底） -->
        <StatusBar
          :connection-status="loop.connectionStatus.value"
          :downsampled="trend.downsampled.value"
          :last-message-at="loop.lastMessageAt.value"
          :point-count="trend.pointCount.value"
          :source="trend.source.value"
          :unit-label="
            loop.selectedUnit.value ?? loop.current.value?.unitName ?? null
          "
          :window-label="activePreset.label"
        />
      </main>
    </div>
  </div>
</template>

<style scoped>
/* 整页固定视口（桌面应用式，禁止整页滚动）。
 * 高度对齐旧版回路工作台先例（calc(100vh - 110px) = vben header+tabs+间距），
 * vben 内容容器为 min-height 流式布局，height:100% 无法形成固定视口。 */
.wb360 {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 110px);
  overflow: hidden;
}

.wb360-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

/* 脊柱-主区分隔（5px 垂直拖拽） */
.wb360-vsplit {
  background: hsl(var(--accent) / 25%);
  border-left: 1px solid hsl(var(--border));
  border-right: 1px solid hsl(var(--border));
  cursor: col-resize;
  flex: none;
  width: 5px;
}

.wb360-vsplit:hover {
  background: hsl(var(--primary) / 12%);
}

.wb360-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  position: relative;
}

/* 最大化态：旧画布整体隐藏，迷你趋势贴旅程条正下方 */
.wb360-main.max .wb360-canvas {
  display: none;
}

.wb360-main.max .wb360-ws.open {
  flex: 1;
  height: auto;
}

.wb360-main.max .wb360-cv-mini {
  display: flex;
}

.wb360-cv-mini {
  border-bottom: 1px solid hsl(var(--border));
  display: none;
  flex: none;
  flex-direction: column;
  gap: 2px;
  height: var(--mini-h, 152px);
  padding: 2px 8px;
}

/* 监视画布 */
.wb360-canvas {
  background: hsl(var(--card));
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  position: relative;
}

.cv-bar {
  align-items: center;
  border-bottom: 1px solid hsl(var(--border));
  display: flex;
  flex: none;
  flex-wrap: wrap;
  gap: 10px;
  padding: 7px 12px;
  row-gap: 4px;
}

.segctl {
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
  display: inline-flex;
  overflow: hidden;
}

.segctl button {
  background: hsl(var(--card));
  border-right: 1px solid hsl(var(--border));
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  font-size: 12px;
  padding: 3px 12px;
}

.segctl button:last-child {
  border-right: none;
}

.segctl button.on {
  background: hsl(var(--primary));
  color: hsl(var(--primary-foreground));
}

.legend {
  align-items: center;
  color: hsl(var(--muted-foreground));
  display: flex;
  flex-wrap: wrap;
  font-size: 12px;
  gap: 10px;
}

.lg {
  align-items: center;
  cursor: pointer;
  display: inline-flex;
  gap: 5px;
  user-select: none;
}

.lg.off {
  opacity: 0.35;
}

.lg .sw {
  border-radius: 2px;
  height: 3px;
  width: 14px;
}

.lg .mk {
  color: hsl(var(--destructive));
  font-size: 11px;
}

.cv-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 4px 8px 0;
  position: relative;
}

.cv-overlay {
  align-items: center;
  background: hsl(var(--card) / 55%);
  bottom: 0;
  color: hsl(var(--muted-foreground));
  display: flex;
  font-size: 13px;
  justify-content: center;
  left: 0;
  position: absolute;
  right: 0;
  top: 0;
}

.cv-overlay-error {
  align-items: flex-start;
  background: hsl(var(--card) / 85%);
  color: hsl(var(--destructive));
  justify-content: center;
  padding: 0 24px;
  text-align: center;
}

/* 工作区：缩略态 / 展开态 */
.wb360-ws {
  background: hsl(var(--card));
  display: flex;
  flex: none;
  min-height: 0;
}

.wb360-ws.thumbs {
  align-items: stretch;
  gap: 10px;
  height: 96px;
  padding: 8px 12px;
}

.wb360-ws.open {
  flex-direction: column;
  height: var(--ws-h, 46%);
}

.ws-head {
  align-items: center;
  border-bottom: 1px solid hsl(var(--border));
  display: flex;
  flex: none;
  gap: 10px;
  padding: 6px 12px;
}

.ws-head .wname {
  font-size: 14px;
  font-weight: 700;
}

.ws-hint {
  border-radius: 4px;
  background: hsl(var(--accent) / 60%);
  color: hsl(var(--muted-foreground));
  font-size: 11px;
  padding: 1px 8px;
}

.ws-head .spacer {
  flex: 1;
}

.ws-close {
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  font-size: 12px;
  padding: 2px 10px;
}

.ws-close:hover {
  border-color: hsl(var(--primary));
  color: hsl(var(--primary));
}

.ws-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: auto;
  padding: 10px 12px;
}

.mono {
  font-family: var(--font-mono, monospace);
}

/* 小屏降级（P1-9）：展开态工作区 → 底部上拉抽屉，分屏条隐藏 */
@media (max-height: 760px) {
  .wb360-ws.open {
    border-top: 2px solid hsl(var(--primary));
    bottom: 26px;
    box-shadow: 0 8px 32px rgb(16 24 40 / 16%);
    height: 52%;
    left: 0;
    position: absolute;
    right: 0;
    z-index: 40;
  }

  .page-split {
    display: none;
  }
}
</style>
