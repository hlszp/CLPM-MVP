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
import dayjs from 'dayjs';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { resolveModeLabel } from '#/composables/use-loop-realtime';
import {
  WB360_DEFAULT_WINDOW_KEY,
  WB360_TREND_PALETTE_DARK,
  WB360_TREND_PALETTE_LIGHT,
  WB360_WINDOW_PRESETS,
} from '#/constants/clpm-ui';

import AttentionDrawer from './components/AttentionDrawer.vue';
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
import { diagWithZone, useDiagData } from './composables/use-diag-data';
import { setDiagPrefill } from './composables/use-diag-prefill';
import { useJourneySummary } from './composables/use-journey-summary';
import { setTuningPrefill } from './composables/use-tuning-prefill';
import { MINI_DEFAULT_H, useWb360Layout } from './composables/use-wb360-layout';
import { useWb360Loop } from './composables/use-wb360-loop';

defineOptions({ name: 'LoopWorkbench360' });

// embed 模式：被其他页面的 Drawer 内嵌时通过 prop 指定初始回路，
// 且不回写宿主页面的路由 query（弹出"检查"工作台场景）
const props = defineProps<{ embedLoopId?: string }>();
const isEmbed = computed(() => props.embedLoopId !== undefined);

const route = useRoute();
const router = useRouter();

// 路由精简模式（回路监视页改版 P1-2）：?embed=1 进入时隐藏左脊柱，
// 直接定位 loopId，页头出现返回按钮（from query 优先，无 from 时 router.back）
const isCompact = computed(() => route.query.embed === '1');

/** 精简模式返回：from query 优先（携带监控上下文回监视页），无 from 时浏览器历史回退 */
function goCompactBack() {
  const from = typeof route.query.from === 'string' ? route.query.from : '';
  if (from) {
    router.push(from);
  } else {
    router.back();
  }
}

/* ── 上下文 ── */
const initialLoopId =
  props.embedLoopId ||
  (typeof route.query.loopId === 'string' && route.query.loopId
    ? route.query.loopId
    : null);
const loop = useWb360Loop(initialLoopId);
const layout = useWb360Layout();

/* ── 外部剖面直达（工作台规整 2026-10-04 D2/D3）：?section= 一次性消费 ──
 * 诊断概览/整定总览等模块页跳转协议：/loop/workbench360?loopId=&section=
 * 值域 assess|diagnosis|tuning|handling（diagnosis 为 diag 的对外别名）；
 * 消费后随选中回写从 URL 清除；模块禁用时静默忽略（openSection 自 guard） */
const SECTION_QUERY_ALIAS: Record<string, 'assess' | 'diag' | 'handling' | 'tuning'> = {
  assess: 'assess',
  diag: 'diag',
  diagnosis: 'diag',
  handling: 'handling',
  tuning: 'tuning',
};
if (!isEmbed.value) {
  const qs = typeof route.query.section === 'string' ? route.query.section : '';
  const sectionKey = qs ? SECTION_QUERY_ALIAS[qs] : undefined;
  if (sectionKey) layout.openSection(sectionKey);
}
const trend = useTrendData();
/** 趋势左轴域：PV 满量程优先（2026-10-02 终验；量程缺失退数据域） */
const trendYDomain = computed(() => {
  const r = loop.ranges.value.pvRange;
  return r && r.hi > r.lo ? r : trend.yDomain.value;
});
/** 页面级评估历史（P2）：剖面/旅程条/页头徽章共用，剖面经 inject 消费 */
const assessHistory = useAssessHistory(loop.selectedLoopId);
/** 页面级诊断数据（P3）：最新结论 + 历史，剖面经 inject 消费；旅程条/缩略卡共用 */
const diagData = useDiagData(loop.selectedLoopId);
/** 页面级整定/处置摘要（P4-4）：旅程条/缩略卡/状态栏/事件层共用；模块禁用零请求 */
const journey = useJourneySummary(
  () => loop.selectedLoopId.value,
  {
    enabled: () => ({
      handling: layout.isSectionAvailable('handling'),
      tuning: layout.isSectionAvailable('tuning'),
    }),
  },
);
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

/* ── 诊断→趋势联动 + 导出 + SP 容差带（2026-10-03 终验优化）── */
const mainTrendRef = ref<null | {
  exportPng: (title: string) => null | string;
  locate: (t0: number, t1: number) => void;
}>(null);
const spToleranceInput = ref<'' | null | number>(null);
const spTolerance = computed(() => {
  const v = spToleranceInput.value;
  return typeof v === 'number' && v > 0 ? v : null;
});
/** 左轴量程%（仅量程域有效；数据域兜底时 % 无意义不显示） */
const trendSpanPct = computed(() => !!loop.ranges.value.pvRange);

const p2 = (n: number) => (n < 10 ? `0${n}` : `${n}`);
function fmtLocal(ts: number) {
  const dt = new Date(ts);
  return `${p2(dt.getMonth() + 1)}-${p2(dt.getDate())} ${p2(dt.getHours())}:${p2(dt.getMinutes())}`;
}

/** 诊断结论/历史行 → 主趋势视口定位该时间窗（切历史模式；超出当前窗口提示换档） */
function onLocateTrend(p: { tsEnd: string; tsStart: string }) {
  const a = Date.parse(p.tsStart);
  const b = Date.parse(p.tsEnd);
  if (!Number.isFinite(a) || !Number.isFinite(b) || !(b > a)) {
    message.warning('该记录时间窗无效，无法定位趋势');
    return;
  }
  const d = trend.domain.value;
  if (d && (a > d.t1 || b < d.t0)) {
    message.warning(
      '该时间窗超出当前趋势窗口范围，请先切换更大的时间窗（如 3D / 7D）',
    );
    return;
  }
  setLive(false);
  if (layout.readonlyState.panelState !== 'half') {
    layout.setPanel('half');
  }
  mainTrendRef.value?.locate(a, b);
  message.success(
    `趋势已定位到 ${fmtLocal(a)} ~ ${fmtLocal(b)}（历史模式，切回"实时"恢复追跟）`,
  );
}

function trendFileBase() {
  const tag =
    loop.current.value?.tagName ?? loop.current.value?.loopId ?? 'loop';
  return `wb360-${tag}-${activePreset.value.label}`;
}

function exportTrendPng() {
  const url = mainTrendRef.value?.exportPng(
    `${loop.current.value?.tagName ?? ''} 趋势 · ${activePreset.value.label} · ${fmtLocal(Date.now())} 导出`,
  );
  if (!url) {
    message.warning('当前无趋势画布可导出');
    return;
  }
  const a = document.createElement('a');
  a.download = `${trendFileBase()}.png`;
  a.href = url;
  a.click();
}

function exportTrendCsv() {
  const fr = trend.frames.value;
  if (fr.length === 0) {
    message.warning('当前无趋势数据可导出');
    return;
  }
  const mm = loop.current.value?.modeMapping ?? null;
  const rows = ['timestamp,pv,sp,op,mode,quality'];
  for (const f of fr) {
    rows.push(
      [
        new Date(f.ts).toISOString(),
        f.pv ?? '',
        f.sp ?? '',
        f.op ?? '',
        resolveModeLabel(f.mode, mm) ?? f.mode ?? '',
        f.quality ?? '',
      ].join(','),
    );
  }
  const blob = new Blob([`\uFEFF${rows.join('\n')}`], {
    type: 'text/csv;charset=utf-8',
  });
  const a = document.createElement('a');
  a.download = `${trendFileBase()}.csv`;
  a.href = URL.createObjectURL(blob);
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ── 历史 / 实时切换 ── */
function setLive(on: boolean) {
  trend.live.value = on;  if (!on) {
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

/* ── 诊断摘要（P3：最新诊断真实数据，供旅程条/缩略卡） ── */
const diagSummary = computed(() => {
  const l = diagData.latest.value;
  if (!l) return null;
  const ts = diagWithZone(l.lastDiagnosedAt ?? null);
  return {
    categoryLabel: l.primaryCategoryLabel ?? null,
    lastDiagnosedText: ts ? dayjs(ts).format('MM-DD HH:mm') : null,
    runCount: l.runCount ?? null,
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

/** 各剖面 props（P4：整定/处置剖面回路上下文） */
const sectionProps = computed<Record<string, Record<string, unknown>>>(() => ({
  assess: {
    loopTagName: loop.current.value?.tagName ?? null,
    selectedLoopId: loop.selectedLoopId.value,
  },
  diag: {
    fitnessLevel: loop.current.value?.fitnessLevel ?? null,
    fitnessTags: loop.current.value?.fitnessTags ?? [],
    loopTagName: loop.current.value?.tagName ?? null,
    selectedLoopId: loop.selectedLoopId.value,
  },
  handling: {
    loopTagName: loop.current.value?.tagName ?? null,
    selectedLoopId: loop.selectedLoopId.value,
  },
  tuning: {
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

/* ── P4 诊断→整定页内动线：切整定剖面 + 预填辨识窗（use-tuning-prefill 单一事实源） ── */
function onGoTuning(payload: {
  categoryLabel: null | string;
  loopId: string;
  primaryConfidence: null | number;
  tsEnd: string;
  tsStart: string;
}) {
  if (!layout.isSectionAvailable('tuning')) {
    message.warning('整定模块未启用，无法切换到整定剖面');
    return;
  }
  if (payload.tsStart && payload.tsEnd) {
    setTuningPrefill({
      categoryLabel: payload.categoryLabel,
      primaryConfidence: payload.primaryConfidence,
      tsEnd: payload.tsEnd,
      tsStart: payload.tsStart,
    });
    layout.openSection('tuning');
    message.info('已切换到参数整定剖面，并预填该结论的辨识时间窗');
  } else {
    layout.openSection('tuning');
    message.info('已切换到参数整定剖面');
  }
}

function onEventMarkClick(mark: TrendEventMark) {
  if (mark.section) layout.openSection(mark.section);
}

/* ── P4-4 事件标注层：诊断 ▼（页面级诊断历史首页）+ 整定 ◆ / 工单实施 ▮（摘要页）── */
const trendEvents = computed<TrendEventMark[]>(() => {
  const marks: TrendEventMark[] = [];
  for (const row of diagData.rows.value) {
    const local = diagWithZone(row.createdAt);
    if (!local) continue;
    marks.push({
      glyph: '▼',
      key: `diag-${row.id}`,
      label: '诊断',
      section: 'diag',
      ts: new Date(local).getTime(),
    });
  }
  for (const seed of journey.eventSeeds.value) {
    const local = diagWithZone(seed.tsIso);
    if (!local) continue;
    marks.push({
      glyph: seed.glyph,
      key: seed.key,
      label: seed.label,
      section: seed.section,
      ts: new Date(local).getTime(),
    });
  }
  return marks;
});

/* ── P4-6 关注抽屉（页头 🔔 唤起，全局通知性质例外） ── */
const attentionOpen = ref(false);

/* ── P4 剖面内数据变化 → 刷新页头摘要（保存方案/创建/转单/流转） ── */
function onJourneyDirty() {
  journey.refresh();
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
  // 精简模式无左脊柱：装置树只喂脊柱，跳过 loadTree；loadLoops 必须保留（current 数据源）
  loop.loadLoops().then(() => {
    if (!isCompact.value) loop.loadTree();
  });
});

onBeforeUnmount(() => {
  offRealtime?.();
});

// 选中回路变化：同步 query + 重载趋势窗口（embed 模式不改宿主路由）。
// immediate：?loopId= 直接定位（含精简模式）时 selectedLoopId 初值即命中，
// 值不再变化，不补立即执行趋势会永远空白（同剖面 composable 的 immediate 口径）
watch(
  () => loop.selectedLoopId.value,
  (id) => {
    if (!id) return;
    // ?section= 已消费：随选中回写一并清除（含 loopId 恰好命中的直达场景）
    if (
      !isEmbed.value &&
      (route.query.loopId !== id || route.query.section !== undefined)
    ) {
      const { section: _drop, ...rest } = route.query;
      router.replace({ query: { ...rest, loopId: id } });
    }
    reloadTrend();
  },
  { immediate: true },
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
  <div class="wb360" :class="{ 'wb360--embed': isEmbed }">
    <LoopHeader
      :connection-status="loop.connectionStatus.value"
      :fitness-level="fitness.level"
      :last-message-at="loop.lastMessageAt.value"
      :loop="loop.current.value"
      :show-back="isCompact"
      @back="goCompactBack"
      @open-attention="attentionOpen = true"
    />

    <div class="wb360-body">
      <!-- 左脊柱（P1-3）；路由精简模式隐藏（P1-2：回路定位走 URL，脊柱无用） -->
      <LoopSpine
        v-if="!isCompact"
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

      <!-- 脊柱-主区分隔（垂直拖拽）；精简模式随之隐藏 -->
      <div
        v-if="!isCompact"
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
        <!-- 旅程状态条（P1-4；P4-4 整定/处置段真实数据） -->
        <JourneyRail
          :active-section="layout.activeSection.value"
          :assess="assessSummary"
          :diag="diagSummary"
          :handling="journey.handlingSummary.value"
          :sections="layout.availableSections.value"
          :tuning="journey.tuningSummary.value"
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
            <div aria-label="SP 容差带" class="segctl tol" title="设定 SP 容差带宽（工程值），0/空=不显示">
              <span class="tol-l">SP±</span>
              <input
                v-model.number="spToleranceInput"
                :placeholder="loop.ranges.value.pvUnit ?? '工程值'"
                min="0"
                step="any"
                type="number"
              />
            </div>
            <div aria-label="导出趋势" class="segctl">
              <button type="button" @click="exportTrendPng">PNG</button>
              <button type="button" @click="exportTrendCsv">CSV</button>
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
            ref="mainTrendRef"
            :domain="trend.domain.value"
            :events="trendEvents"
            :frames="trend.frames.value"
            :live="trend.live.value"
            :mode-mapping="loop.current.value?.modeMapping ?? null"
            :op-domain="loop.ranges.value.opRange"
            :pv-unit="loop.ranges.value.pvUnit"
            :series-visible="seriesVisible"
            :span-pct="trendSpanPct"
            :sp-tolerance="spTolerance"
            :y-domain="trendYDomain"
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
            :events="trendEvents"
            :frames="trend.frames.value"
            :live="trend.live.value"
            :mode-mapping="loop.current.value?.modeMapping ?? null"
            :series-visible="seriesVisible"
            :y-domain="trendYDomain"
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
            :diag="diagSummary"
            :handling="journey.handlingSummary.value"
            :sections="layout.availableSections.value"
            :tuning="journey.tuningSummary.value"
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
                @go-tuning="onGoTuning"
                @journey-dirty="onJourneyDirty"
                @locate-trend="onLocateTrend"
              />
            </div>
          </template>
        </section>

        <!-- 状态栏（P1-8，深色钉底；P4-4 计数接入） -->
        <StatusBar
          :connection-status="loop.connectionStatus.value"
          :diag-count="layout.isSectionAvailable('diag') ? diagData.total.value : null"
          :downsampled="trend.downsampled.value"
          :handling-open-count="
            layout.isSectionAvailable('handling')
              ? (journey.handlingSummary.value?.inFlightCount ?? 0)
              : null
          "
          :last-message-at="loop.lastMessageAt.value"
          :point-count="trend.pointCount.value"
          :snapshot-count="assessHistory.total.value"
          :source="trend.source.value"
          :unit-label="
            loop.selectedUnit.value ?? loop.current.value?.unitName ?? null
          "
          :window-label="activePreset.label"
        />
      </main>
    </div>

    <!-- 本回路关注抽屉（P4-6；页头 🔔 唤起） -->
    <AttentionDrawer
      v-model:open="attentionOpen"
      :loop-id="loop.selectedLoopId.value"
      :loop-tag-name="loop.current.value?.tagName ?? null"
    />
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

/* embed 模式（Drawer 内嵌）：容器高度由宿主给定，去掉整页视口假设 */
.wb360.wb360--embed {
  flex: 1;
  height: 100%;
  min-height: 0;
}

.wb360-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

/* 脊柱-主区分隔（5px 垂直拖拽） */
.wb360-vsplit {
  flex: none;
  width: 5px;
  cursor: col-resize;
  background: hsl(var(--accent) / 25%);
  border-right: 1px solid hsl(var(--border));
  border-left: 1px solid hsl(var(--border));
}

.wb360-vsplit:hover {
  background: hsl(var(--primary) / 12%);
}

.wb360-main {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
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
  display: none;
  flex: none;
  flex-direction: column;
  gap: 2px;
  height: var(--mini-h, 152px);
  padding: 2px 8px;
  border-bottom: 1px solid hsl(var(--border));
}

/* 监视画布 */
.wb360-canvas {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  background: hsl(var(--card));
}

.cv-bar {
  display: flex;
  flex: none;
  flex-wrap: wrap;
  gap: 10px;
  row-gap: 4px;
  align-items: center;
  padding: 7px 12px;
  border-bottom: 1px solid hsl(var(--border));
}

.segctl {
  display: inline-flex;
  overflow: hidden;
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

/* SP 容差带输入（终验优化） */
.segctl.tol {
  gap: 2px;
  align-items: center;
  padding: 0 6px;
}

.segctl.tol .tol-l {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

.segctl.tol input {
  width: 64px;
  padding: 3px 2px;
  font-size: 12px;
  color: inherit;
  outline: none;
  background: transparent;
  border: none;
}

.segctl.tol input:focus {
  border-bottom: 1px solid hsl(var(--primary) / 60%);
}

.segctl button {
  padding: 3px 12px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: hsl(var(--card));
  border-right: 1px solid hsl(var(--border));
}

.segctl button:last-child {
  border-right: none;
}

.segctl button.on {
  color: hsl(var(--primary-foreground));
  background: hsl(var(--primary));
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.lg {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  cursor: pointer;
  user-select: none;
}

.lg.off {
  opacity: 0.35;
}

.lg .sw {
  width: 14px;
  height: 3px;
  border-radius: 2px;
}

.lg .mk {
  font-size: 11px;
  color: hsl(var(--destructive));
}

.cv-body {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 4px 8px 0;
}

.cv-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: hsl(var(--muted-foreground));
  background: hsl(var(--card) / 55%);
}

.cv-overlay-error {
  align-items: flex-start;
  justify-content: center;
  padding: 0 24px;
  color: hsl(var(--destructive));
  text-align: center;
  background: hsl(var(--card) / 85%);
}

/* 工作区：缩略态 / 展开态 */
.wb360-ws {
  display: flex;
  flex: none;
  min-height: 0;
  background: hsl(var(--card));
}

.wb360-ws.thumbs {
  gap: 10px;
  align-items: stretch;
  height: 96px;
  padding: 8px 12px;
}

.wb360-ws.open {
  flex-direction: column;
  height: var(--ws-h, 46%);
}

.ws-head {
  display: flex;
  flex: none;
  gap: 10px;
  align-items: center;
  padding: 6px 12px;
  border-bottom: 1px solid hsl(var(--border));
}

.ws-head .wname {
  font-size: 14px;
  font-weight: 700;
}

.ws-hint {
  padding: 1px 8px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
  background: hsl(var(--accent) / 60%);
  border-radius: 4px;
}

.ws-head .spacer {
  flex: 1;
}

.ws-close {
  padding: 2px 10px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.ws-close:hover {
  color: hsl(var(--primary));
  border-color: hsl(var(--primary));
}

.ws-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 10px 12px;
  overflow: auto;
}

.mono {
  font-family: var(--font-mono, monospace);
}

/* 小屏降级（P1-9）：展开态工作区 → 底部上拉抽屉，分屏条隐藏 */
@media (max-height: 760px) {
  .wb360-ws.open {
    position: absolute;
    right: 0;
    bottom: 26px;
    left: 0;
    z-index: 40;
    height: 52%;
    border-top: 2px solid hsl(var(--primary));
    box-shadow: 0 8px 32px rgb(16 24 40 / 16%);
  }

  .page-split {
    display: none;
  }
}
</style>
