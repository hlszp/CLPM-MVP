<script lang="ts" setup>
/**
 * 驾驶舱页2 · 右侧详情面板（方案 11 §6.3，两态，纯只读，无滚动条）
 *
 * 态一·聚合视图（默认，两段弹性）：
 *   顶部控制模式微型柱状图（固定高 64px，静态无动画，手动橙色，随左树节点联动口径）
 *   中部节点性能雷达（flex 弹性占满剩余高度，六维+中心综合评分，默认全厂）
 *
 * 态二·单回路详情（点击回路卡）：
 *   头部（等级徽章+回路号/名称/装置+综合评分+‹ ›连续浏览+×取消选择）
 *   实时数据八项网格（PV/SP/OP/模式/P/I/D/可信度，WS 刷新，绿色呼吸点）
 *   实时趋势（固定高 ~130px，近 24h PV/OP 曲线，SP 虚线）
 *   六维雷达（flex 弹性）· 最近闭环记录摘要（无记录则隐藏，不造数）
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { CockpitChartColors, LoopCardModel } from '../loops-shared';

import type { HandlingApi } from '#/api/handling';
import type { MetricApi } from '#/api/metric';

import { computed, onMounted, reactive, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { getDiagnosisRunsLatestApi } from '#/api/diagnosis';
import { getHandlingOrdersApi } from '#/api/handling';
import { getLoopMonitorDetailApi } from '#/api/loop';
import { getNodeSnapshotApi } from '#/api/metric';
import {
  type LoopRealtimeValues,
  useLoopRealtime,
} from '#/composables/use-loop-realtime';
import { useCockpitStore } from '#/store/cockpit';

import {
  type GradeInfo,
  MODE_KEY_ORDER,
  MODE_KEY_ZH,
  modeZhLabel,
  readCockpitColors,
  resolveGrade,
  sixDimsFromKpiSummary,
  sixDimsFromNodeSnapshot,
  type SixDimValues,
} from '../loops-shared';
import CockpitRadar from './cockpit-radar.vue';

defineOptions({ name: 'CockpitLoopDetailPanel' });

const props = withDefaults(
  defineProps<{
    /** 定级阈值（等级色染） */
    gradingThresholds?: MetricApi.GradingThresholdItem[];
    /** 态二：当前回路视图模型 */
    loop?: LoopCardModel | null;
    /** 态二：当前回路在筛选结果中的序号（1 起）/总数（‹ › 连续浏览） */
    loopIndex?: number;
    loopTotal?: number;
    /** 态一：模式分布（服务端聚合，键 AUTO/CAS/REMOTE/MANUAL/APC/UNKNOWN） */
    modeDistribution?: null | Record<string, number>;
    /** 态一：左树选中节点（null=全厂根） */
    nodeId?: null | string;
    nodeLoopCount?: number;
    nodeName?: string;
    view?: 'aggregate' | 'loop';
  }>(),
  {
    gradingThresholds: () => [],
    loop: null,
    loopIndex: 0,
    loopTotal: 0,
    modeDistribution: null,
    nodeId: null,
    nodeLoopCount: 0,
    nodeName: '全厂',
    view: 'aggregate',
  },
);

const emit = defineEmits<{
  close: [];
  next: [];
  prev: [];
}>();

const rootRef = ref<HTMLElement>();

// ---------------------------------------------------------------------------
// 态一：聚合视图（模式柱状图 + 节点雷达）
// ---------------------------------------------------------------------------
const nodeDims = ref<null | SixDimValues>(null);
const nodeScore = ref<null | number>(null);
const nodeGrade = computed<GradeInfo | null>(() =>
  resolveGrade(nodeScore.value, props.gradingThresholds),
);

const modeBarRef = ref<EchartsUIType>();
const { renderEcharts: renderModeBar } = useEcharts(modeBarRef);

const scopeTitle = computed(
  () => `控制模式分布 · ${props.nodeName} · ${props.nodeLoopCount} 回路`,
);

function buildModeBarOption(colors: CockpitChartColors) {
  const dist = props.modeDistribution ?? {};
  const categories = MODE_KEY_ORDER.map((k) => MODE_KEY_ZH[k]);
  const data = MODE_KEY_ORDER.map((k) => ({
    value: dist[k] ?? 0,
    itemStyle: { color: k === 'MANUAL' ? colors.gradeFair : colors.accent },
  }));
  return {
    animation: false,
    grid: { top: 14, right: 4, bottom: 2, left: 4, containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: categories,
      axisLine: { lineStyle: { color: colors.border } },
      axisTick: { show: false },
      axisLabel: { color: colors.text3, fontSize: 10, interval: 0 },
    },
    yAxis: { show: false, type: 'value' as const },
    series: [
      {
        type: 'bar' as const,
        barWidth: 16,
        label: {
          show: true,
          position: 'top' as const,
          color: colors.text2,
          fontSize: 10,
        },
        data,
      },
    ],
  };
}

function refreshModeBar() {
  renderModeBar(buildModeBarOption(readCockpitColors(rootRef.value)));
}

async function loadNodeSnapshot(nodeId: null | string) {
  if (!nodeId) {
    nodeDims.value = null;
    nodeScore.value = null;
    return;
  }
  try {
    const snap = await getNodeSnapshotApi(nodeId);
    nodeDims.value = sixDimsFromNodeSnapshot(snap);
    nodeScore.value = snap?.score ?? null;
  } catch {
    nodeDims.value = null;
    nodeScore.value = null;
  }
}

watch(
  () => [props.view, props.nodeId],
  () => {
    if (props.view === 'aggregate') {
      void loadNodeSnapshot(props.nodeId ?? null);
      refreshModeBar();
    }
  },
  { immediate: true },
);

watch(
  () => props.modeDistribution,
  () => {
    if (props.view === 'aggregate') refreshModeBar();
  },
);

// ---------------------------------------------------------------------------
// 态二：单回路详情
// ---------------------------------------------------------------------------
const detailLoading = ref(false);

/** 实时八项（WS 局部更新；初值来自 GET /loops/{id}/monitor） */
const liveDetail = reactive<LoopRealtimeValues>({
  pv: null,
  sp: null,
  op: null,
  mode: null,
  modeLabel: null,
  pidP: null,
  pidI: null,
  pidD: null,
  pvQuality: null,
  readAt: null,
});

const confidenceLevel = ref<null | string>(null);
const loopDims = ref<null | SixDimValues>(null);

/** 最近闭环记录（最近诊断结论 + 处置状态；均为 null 时隐藏区块） */
const closure = ref<null | {
  diagnosisAt: null | string;
  diagnosisLabel: null | string;
  orderNo: null | string;
  orderStatus: null | string;
}>(null);

const trendRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendRef);
let lastTrend: LoopApiTrend | null = null;

type LoopApiTrend = {
  op: (null | number)[];
  pv: (null | number)[];
  sp: (null | number)[];
  timestamps: number[];
};

const modeText = computed(() =>
  modeZhLabel(liveDetail.mode, liveDetail.modeLabel),
);

function fmt(v: null | number | undefined): string {
  return v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : String(Math.round(v * 1000) / 1000);
}

function fmtTime(iso: null | string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function buildTrendOption(colors: CockpitChartColors) {
  const trend = lastTrend;
  const times = (trend?.timestamps ?? []).map((ts) => {
    const d = new Date(ts);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  });
  return {
    animation: false,
    grid: { top: 22, right: 8, bottom: 2, left: 8, containLabel: true },
    legend: {
      top: 0,
      right: 4,
      itemWidth: 12,
      itemHeight: 2,
      textStyle: { color: colors.text3, fontSize: 10 },
    },
    xAxis: {
      type: 'category' as const,
      data: times,
      axisLine: { lineStyle: { color: colors.border } },
      axisTick: { show: false },
      axisLabel: { color: colors.text3, fontSize: 10 },
    },
    yAxis: {
      type: 'value' as const,
      scale: true,
      splitLine: { lineStyle: { color: colors.border } },
      axisLabel: { color: colors.text3, fontSize: 10 },
    },
    series: [
      {
        name: 'PV',
        type: 'line' as const,
        showSymbol: false,
        lineStyle: { color: colors.pvLine, width: 1.5 },
        itemStyle: { color: colors.pvLine },
        data: trend?.pv ?? [],
      },
      {
        name: 'OP',
        type: 'line' as const,
        showSymbol: false,
        lineStyle: { color: colors.opLine, width: 1 },
        itemStyle: { color: colors.opLine },
        data: trend?.op ?? [],
      },
      {
        name: 'SP',
        type: 'line' as const,
        showSymbol: false,
        lineStyle: { color: colors.text3, width: 1, type: 'dashed' as const },
        itemStyle: { color: colors.text3 },
        data: trend?.sp ?? [],
      },
    ],
  };
}

function refreshTrend() {
  renderTrend(buildTrendOption(readCockpitColors(rootRef.value)));
}

async function loadLoopDetail(loopId: string) {
  detailLoading.value = true;
  lastTrend = null;
  closure.value = null;
  try {
    const [detail, diagRes, orderRes] = await Promise.all([
      getLoopMonitorDetailApi(loopId, 'last_24_hours'),
      getDiagnosisRunsLatestApi(undefined, loopId).catch(() => null),
      getHandlingOrdersApi({ loopId, page: 1, pageSize: 1 }).catch(() => null),
    ]);
    // 竞态守卫：加载期间已切换回路则丢弃
    if (props.loop?.loopId !== loopId) return;

    const cv = detail.currentValues;
    liveDetail.pv = cv?.pv ?? null;
    liveDetail.sp = cv?.sp ?? null;
    liveDetail.op = cv?.op ?? null;
    liveDetail.mode = cv?.mode ?? null;
    liveDetail.modeLabel = cv?.modeLabel ?? null;
    liveDetail.pvQuality = cv?.pvQuality ?? null;
    liveDetail.readAt = cv?.readAt ?? null;
    liveDetail.pidP = detail.runtimeParams?.pidP ?? null;
    liveDetail.pidI = detail.runtimeParams?.pidI ?? null;
    liveDetail.pidD = detail.runtimeParams?.pidD ?? null;

    confidenceLevel.value = detail.kpiSummary?.confidence_level ?? null;
    loopDims.value = sixDimsFromKpiSummary(detail.kpiSummary);
    lastTrend = detail.trend ?? null;
    refreshTrend();

    const diag = diagRes?.items?.[0];
    const order: HandlingApi.OrderItem | undefined = orderRes?.items?.[0];
    if (diag?.runId || order) {
      closure.value = {
        diagnosisLabel: diag?.runId
          ? (diag.primaryCategoryLabel ?? '已诊断')
          : null,
        diagnosisAt: diag?.lastDiagnosedAt ?? null,
        orderNo: order?.orderNo ?? null,
        orderStatus: order?.statusLabel ?? null,
      };
    }
  } catch {
    if (props.loop?.loopId === loopId) {
      loopDims.value = null;
      lastTrend = null;
    }
  } finally {
    if (props.loop?.loopId === loopId) detailLoading.value = false;
  }
}

watch(
  () => [props.view, props.loop?.loopId],
  () => {
    if (props.view === 'loop' && props.loop?.loopId) {
      void loadLoopDetail(props.loop.loopId);
    }
  },
  { immediate: true },
);

// ---------------------------------------------------------------------------
// WebSocket 实时刷新（复用全局单例；断连 30s 轮询降级）
// ---------------------------------------------------------------------------
const realtime = useLoopRealtime();

/** applyMessage 按 tagName 匹配，选中回路切换时同步最新 tagName */
const realtimeItem = computed(() =>
  props.loop
    ? [
        {
          loopId: props.loop.loopId,
          tagName: props.loop.tagName,
          currentValues: liveDetail,
        },
      ]
    : [],
);

onMounted(() => {
  realtime.start();
  realtime.onMessage((msg) => {
    realtime.applyMessage(msg, realtimeItem.value);
  });
});

watch(
  () => realtime.connectionStatus.value,
  (status, prev) => {
    if (status === 'offline') {
      realtime.startFallback(async () => {
        if (props.view === 'loop' && props.loop?.loopId) {
          await loadLoopDetail(props.loop.loopId);
        }
      });
    } else if (status === 'online' && prev !== 'online') {
      realtime.stopFallback();
      if (props.view === 'loop' && props.loop?.loopId) {
        void loadLoopDetail(props.loop.loopId);
      }
    }
  },
);

/** 主题切换后重渲染本面板内 ECharts（canvas 不随 CSS 变量自动更新） */
const cockpitStore = useCockpitStore();
watch(
  () => cockpitStore.theme,
  () => {
    if (props.view === 'aggregate') refreshModeBar();
    else refreshTrend();
  },
);
</script>

<template>
  <div ref="rootRef" class="ldp">
    <!-- ============ 态一：聚合视图（两段弹性） ============ -->
    <template v-if="view === 'aggregate'">
      <div class="ldp__hd">
        节点详情
        <span class="sub">{{ nodeName }}（聚合）</span>
      </div>
      <div class="ldp__body">
        <div class="ldp__section-title" :title="scopeTitle">{{ scopeTitle }}</div>
        <div class="ldp__modebar">
          <EchartsUI ref="modeBarRef" height="64px" />
        </div>
        <div class="ldp__radar">
          <CockpitRadar
            :dims="nodeDims"
            :score="nodeScore"
            :grade="nodeGrade"
          />
        </div>
      </div>
    </template>

    <!-- ============ 态二：单回路详情 ============ -->
    <template v-else-if="loop">
      <div class="ldp__hd ldp__hd--loop">
        <span
          class="ldp__badge"
          :style="{
            color: loop.grade ? `var(${loop.grade.colorVar})` : 'var(--ck-text-3)',
            borderColor: loop.grade
              ? `var(${loop.grade.colorVar})`
              : 'var(--ck-border-2)',
          }"
        >
          {{ loop.grade?.label ?? '无评分' }}
        </span>
        <div class="ldp__loophead">
          <div class="ldp__looptitle" :title="`${loop.tagName} ${loop.description}`">
            {{ loop.tagName }} · {{ loop.description || loop.unitName }}
          </div>
          <div class="ldp__loopsub">
            {{ loop.unitName }} · 综合评分
            <b
              :style="{
                color: loop.grade
                  ? `var(${loop.grade.colorVar})`
                  : 'var(--ck-text-3)',
              }"
            >
              {{ loop.score === null ? '—' : loop.score.toFixed(1) }}
            </b>
          </div>
        </div>
        <div class="ldp__nav">
          <button
            type="button"
            :disabled="loopIndex <= 1"
            title="上一回路"
            @click="emit('prev')"
          >
            ‹
          </button>
          <span class="ldp__navpos">{{ loopIndex }}/{{ loopTotal }}</span>
          <button
            type="button"
            :disabled="loopIndex >= loopTotal"
            title="下一回路"
            @click="emit('next')"
          >
            ›
          </button>
          <button type="button" title="取消选择" @click="emit('close')">×</button>
        </div>
      </div>

      <div class="ldp__body">
        <!-- 实时数据八项网格 -->
        <div class="ldp__grid">
          <div class="ldp__cell">
            <span class="ldp__k">
              <i
                class="ldp__dot"
                :class="{ online: realtime.connectionStatus.value === 'online' }"
              ></i>
              PV
            </span>
            <b>{{ fmt(liveDetail.pv) }}</b>
          </div>
          <div class="ldp__cell">
            <span class="ldp__k">SP</span><b>{{ fmt(liveDetail.sp) }}</b>
          </div>
          <div class="ldp__cell">
            <span class="ldp__k">OP</span><b>{{ fmt(liveDetail.op) }}</b>
          </div>
          <div class="ldp__cell">
            <span class="ldp__k">模式</span><b>{{ modeText }}</b>
          </div>
          <div class="ldp__cell">
            <span class="ldp__k">P</span><b>{{ fmt(liveDetail.pidP) }}</b>
          </div>
          <div class="ldp__cell">
            <span class="ldp__k">I</span><b>{{ fmt(liveDetail.pidI) }}</b>
          </div>
          <div class="ldp__cell">
            <span class="ldp__k">D</span><b>{{ fmt(liveDetail.pidD) }}</b>
          </div>
          <div class="ldp__cell">
            <span class="ldp__k">可信度</span><b>{{ confidenceLevel ?? '—' }}</b>
          </div>
        </div>

        <!-- 实时趋势（固定高 130px，近 24h PV/OP，SP 虚线） -->
        <div class="ldp__section-title">实时趋势 · 近 24h</div>
        <div class="ldp__trend">
          <EchartsUI v-if="!detailLoading" ref="trendRef" height="130px" />
          <div v-else class="ldp__hint">加载中…</div>
        </div>

        <!-- 六维雷达（flex 弹性） -->
        <div class="ldp__radar">
          <CockpitRadar
            :dims="loopDims"
            :score="loop.score"
            :grade="loop.grade"
          />
        </div>

        <!-- 最近闭环记录摘要（无记录隐藏，勿造数） -->
        <div v-if="closure" class="ldp__closure">
          <span v-if="closure.diagnosisLabel" class="ldp__closure-item">
            最近诊断：{{ closure.diagnosisLabel }} ·
            {{ fmtTime(closure.diagnosisAt) }}
          </span>
          <span v-if="closure.orderNo" class="ldp__closure-item">
            处置：{{ closure.orderNo }} · {{ closure.orderStatus }}
          </span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ldp {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.ldp__hd {
  display: flex;
  flex: none;
  gap: 8px;
  align-items: center;
  min-height: 44px;
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ck-text);
  border-bottom: 1px solid var(--ck-border);
}

.ldp__hd .sub {
  font-size: 11px;
  font-weight: 400;
  color: var(--ck-text-3);
}

.ldp__hd--loop {
  gap: 10px;
}

.ldp__badge {
  flex: none;
  padding: 1px 8px;
  font-size: 11px;
  border: 1px solid;
  border-radius: 8px;
}

.ldp__loophead {
  flex: 1;
  min-width: 0;
}

.ldp__looptitle {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  white-space: nowrap;
}

.ldp__loopsub {
  font-size: 11px;
  font-weight: 400;
  color: var(--ck-text-3);
}

.ldp__loopsub b {
  font-variant-numeric: tabular-nums;
}

.ldp__nav {
  display: flex;
  flex: none;
  gap: 4px;
  align-items: center;
}

.ldp__nav button {
  width: 22px;
  height: 22px;
  padding: 0;
  font-size: 13px;
  color: var(--ck-text-2);
  cursor: pointer;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 5px;
}

.ldp__nav button:hover:not(:disabled) {
  color: var(--ck-text);
  border-color: var(--ck-accent);
}

.ldp__nav button:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.ldp__navpos {
  font-size: 11px;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text-3);
}

/* 主体：两段/多段弹性，任何屏幕高度不出现面板滚动条 */
.ldp__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 8px 12px 10px;
  overflow: hidden;
}

.ldp__section-title {
  flex: none;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 11px;
  color: var(--ck-text-3);
  white-space: nowrap;
}

.ldp__modebar {
  flex: none;
  height: 64px;
}

.ldp__radar {
  flex: 1;
  min-height: 0;
  margin-top: 6px;
}

.ldp__grid {
  display: grid;
  flex: none;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
  margin-bottom: 8px;
}

.ldp__cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 5px 8px;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 6px;
}

.ldp__k {
  display: flex;
  gap: 4px;
  align-items: center;
  font-size: 10px;
  color: var(--ck-text-3);
}

.ldp__cell b {
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text);
}

/* 绿色呼吸点（WS 在线时呼吸，离线灰点静止） */
.ldp__dot {
  width: 6px;
  height: 6px;
  background: var(--ck-text-3);
  border-radius: 50%;
}

.ldp__dot.online {
  background: var(--ck-grade-excellent);
  animation: ldp-breath 2s ease-in-out infinite;
}

@keyframes ldp-breath {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.35;
  }
}

.ldp__trend {
  flex: none;
  height: 130px;
  margin-bottom: 6px;
}

.ldp__hint {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  color: var(--ck-text-3);
}

.ldp__closure {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 2px;
  padding-top: 6px;
  margin-top: 6px;
  font-size: 11px;
  color: var(--ck-text-2);
  border-top: 1px solid var(--ck-border);
}
</style>
