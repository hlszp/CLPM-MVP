<script lang="ts" setup>
/**
 * 回路详情弹窗（方案 11 §7 · 触发源：页1 §6 表格行 / 清单类弹窗行）
 *
 * 头部：回路号+名称+装置+等级徽章+评分；三列主体：
 * - 实时值区（PV/SP/OP/MODE/P/I/D，GET /loops/{id}/monitor）
 * - 趋势曲线（时间窗内 PV/SP/OP，同源 trend；7d/30d 走 tsStart/tsEnd 自定义窗口）
 * - 六维雷达（统一口径：自控率/平稳率/准确率/快速率/好值率/有效率，
 *   中心=综合评分，复用 workbench-radar6 的 graphic 中心评分模式）
 * 底部：最近闭环记录摘要（/handling/orders 取该回路最近 CLOSED 工单；
 * 无则隐藏该区，不造数）。无任何操作按钮。
 *
 * 权限降级：/loops/{id}/monitor 仅 ADMIN/IC_ENGINEER/PE_ENGINEER/EXPERT
 * 可访问，SPONSOR 角色将 403 —— 实时值/趋势/雷达区降级为提示文案，
 * 头部基础信息（/loops/{id}）不受影响。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { HandlingApi } from '#/api/handling';
import type { LoopApi } from '#/api/loop';

import { computed, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { getHandlingOrdersApi } from '#/api/handling';
import { getLoopDetailApi, getLoopMonitorDetailApi } from '#/api/loop';
import { useCockpitStore } from '#/store/cockpit';
import { formatLocalTime } from '#/utils/format';

import {
  GRADE_LABELS,
  gradeOfScore,
  useCockpitTheme,
} from '../../composables/use-cockpit-theme';
import { WINDOW_HOURS, WINDOW_LABELS } from '../../utils/format';
import CockpitModal from './cockpit-modal.vue';

const props = withDefaults(
  defineProps<{ loopId?: null | string; open?: boolean }>(),
  { loopId: null, open: false },
);

const emit = defineEmits<{ close: [] }>();

const cockpitStore = useCockpitStore();
const { chartColors, gradeColors, isLight } = useCockpitTheme();

const loading = ref(false);
/** 监控详情不可用（403/网络等）→ 实时值/趋势/雷达区降级提示 */
const monitorUnavailable = ref(false);
const detail = ref<LoopApi.LoopDetail | null>(null);
const monitor = ref<LoopApi.MonitorDetail | null>(null);
const closedOrder = ref<HandlingApi.OrderItem | null>(null);

async function load() {
  if (!props.loopId) return;
  loading.value = true;
  monitorUnavailable.value = false;
  detail.value = null;
  monitor.value = null;
  closedOrder.value = null;

  const win = cockpitStore.timeWindow;
  const now = new Date();
  const customRange =
    win === '24h'
      ? undefined
      : {
          tsEnd: now.toISOString(),
          tsStart: new Date(
            now.getTime() - WINDOW_HOURS[win] * 3600 * 1000,
          ).toISOString(),
        };

  const [d, m, orders] = await Promise.allSettled([
    getLoopDetailApi(props.loopId),
    getLoopMonitorDetailApi(props.loopId, 'last_24_hours', customRange),
    getHandlingOrdersApi({ loopId: props.loopId, page: 1, pageSize: 50 }),
  ]);
  if (d.status === 'fulfilled') detail.value = d.value;
  if (m.status === 'fulfilled') {
    monitor.value = m.value;
  } else {
    monitorUnavailable.value = true;
  }
  if (orders.status === 'fulfilled') {
    closedOrder.value =
      (orders.value?.items ?? []).find((o) => o.status === 'CLOSED') ?? null;
  }
  loading.value = false;
}

watch(
  () => [props.open, props.loopId, cockpitStore.timeWindow],
  ([open]) => {
    if (open) load();
  },
  { immediate: true },
);

// ---------------------------------------------------------------------------
// 头部
// ---------------------------------------------------------------------------
const score = computed(() => monitor.value?.kpiSummary?.composite_score ?? null);
const grade = computed(() => gradeOfScore(score.value));
const gradeText = computed(() =>
  grade.value ? GRADE_LABELS[grade.value] : '',
);
const gradeColor = computed(() =>
  grade.value ? gradeColors.value[grade.value] : chartColors.value.text,
);

const tagName = computed(
  () => detail.value?.basicInfo.tagName ?? monitor.value?.tagName ?? '',
);
const description = computed(() => detail.value?.basicInfo.description ?? '');
const unitName = computed(() => detail.value?.basicInfo.unitName ?? '');

// ---------------------------------------------------------------------------
// 实时值区
// ---------------------------------------------------------------------------
const MODE_LABELS: Record<string, string> = {
  Auto: '自动',
  Cascade: '串级',
  Manual: '手动',
};

const realtimeItems = computed(() => {
  const cv = monitor.value?.currentValues;
  const rp = monitor.value?.runtimeParams;
  const modeText =
    cv?.modeLabel ??
    (rp?.controlMode ? (MODE_LABELS[rp.controlMode] ?? rp.controlMode) : '—');
  const num = (v: null | number | undefined) =>
    typeof v === 'number' && !Number.isNaN(v) ? v.toFixed(2) : '—';
  return [
    { label: 'PV', value: num(cv?.pv) },
    { label: 'SP', value: num(cv?.sp) },
    { label: 'OP', value: num(cv?.op) },
    { label: 'MODE', value: modeText },
    { label: 'P', value: num(rp?.pidP) },
    { label: 'I', value: num(rp?.pidI) },
    { label: 'D', value: num(rp?.pidD) },
  ];
});

// ---------------------------------------------------------------------------
// 趋势曲线（PV/SP/OP，time 轴，timestamps 为毫秒 epoch）
// ---------------------------------------------------------------------------
const trendRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendRef);

const hasTrend = computed(
  () => (monitor.value?.trend?.timestamps?.length ?? 0) > 0,
);

function toPairs(ts: number[], vals: (null | number)[]): [number, number][] {
  const out: [number, number][] = [];
  for (const [i, t] of ts.entries()) {
    const v = vals[i];
    if (typeof v === 'number') out.push([t!, v]);
  }
  return out;
}

function buildTrendOption() {
  const t = monitor.value?.trend;
  const cc = chartColors.value;
  if (!t || t.timestamps.length === 0) return {};
  const pvColor = gradeColors.value.GOOD;
  const spColor = gradeColors.value.FAIR;
  const opColor = gradeColors.value.EXCELLENT;
  const line = (
    name: string,
    vals: (null | number)[],
    color: string,
    dashed = false,
  ) => ({
    data: toPairs(t.timestamps, vals),
    itemStyle: { color },
    lineStyle: { color, type: dashed ? ('dashed' as const) : ('solid' as const), width: 1.5 },
    name,
    showSymbol: false,
    type: 'line' as const,
  });
  return {
    animation: false,
    grid: { bottom: 24, left: 10, right: 10, top: 26, containLabel: true },
    legend: {
      icon: 'roundRect',
      itemHeight: 8,
      itemWidth: 12,
      textStyle: { color: cc.text, fontSize: 10 },
      top: 0,
    },
    series: [
      line('PV', t.pv, pvColor),
      line('SP', t.sp, spColor, true),
      line('OP', t.op, opColor),
    ],
    textStyle: { color: cc.textStrong },
    tooltip: {
      backgroundColor: cc.panel,
      borderColor: cc.splitLine,
      borderWidth: 1,
      textStyle: { color: cc.textStrong, fontSize: 12 },
      trigger: 'axis' as const,
    },
    xAxis: {
      axisLabel: { color: cc.text, fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: cc.splitLine } },
      type: 'time' as const,
    },
    yAxis: {
      axisLabel: { color: cc.text, fontSize: 10 },
      scale: true,
      splitLine: {
        lineStyle: { color: cc.splitLine, opacity: 0.6, type: 'dashed' as const },
      },
      type: 'value' as const,
    },
  };
}

// ---------------------------------------------------------------------------
// 六维雷达（统一口径：自控率/平稳率/准确率/快速率/好值率/有效率，中心综合评分）
// ---------------------------------------------------------------------------
const radarRef = ref<EchartsUIType>();
const { renderEcharts: renderRadar } = useEcharts(radarRef);

const AXIS_META = [
  { key: 'auto_mode_rate', label: '自控率' },
  { key: 'steady_rate', label: '平稳率' },
  { key: 'accuracy_rate', label: '准确率' },
  { key: 'fast_rate', label: '快速率' },
  { key: 'good_value_rate', label: '好值率' },
  { key: 'effective_auto_rate', label: '有效率' },
] as const;

const hasRadar = computed(() => !!monitor.value?.kpiSummary);

function axisValue(key: (typeof AXIS_META)[number]['key']): number {
  const v = monitor.value?.kpiSummary?.[key];
  return typeof v === 'number' && !Number.isNaN(v) ? v : 0;
}

function buildRadarOption() {
  const cc = chartColors.value;
  const poly = cc.radar;
  const scoreText =
    score.value === null ? '—' : Number(score.value).toFixed(1);
  return {
    animation: false,
    graphic: {
      elements: [
        {
          left: 'center',
          style: {
            fill: gradeColor.value,
            fontSize: 24,
            fontWeight: 700,
            text: scoreText,
            textAlign: 'center',
          },
          top: '44%',
          type: 'text',
          z: 100,
        },
        {
          left: 'center',
          style: {
            fill: gradeColor.value,
            fontSize: 11,
            text: gradeText.value,
            textAlign: 'center',
          },
          top: '58%',
          type: 'text',
          z: 100,
        },
      ],
    },
    radar: {
      axisLine: { lineStyle: { color: cc.splitLine } },
      axisName: { color: cc.text, fontSize: 10 },
      center: ['50%', '54%'],
      indicator: AXIS_META.map((m) => ({
        max: 100,
        min: 0,
        name: `${m.label} ${Math.round(axisValue(m.key))}`,
      })),
      radius: '62%',
      shape: 'polygon' as const,
      splitArea: { show: false },
      splitLine: { lineStyle: { color: cc.splitLine } },
      splitNumber: 5,
    },
    series: [
      {
        areaStyle: { color: isLight.value ? 'rgba(29, 78, 216, 0.12)' : 'rgba(96, 165, 250, 0.16)' },
        data: [
          {
            name: '六维形态',
            value: AXIS_META.map((m) => axisValue(m.key)),
          },
        ],
        itemStyle: { color: poly },
        lineStyle: { color: poly, width: 1.5 },
        symbol: 'circle',
        symbolSize: 3,
        type: 'radar' as const,
      },
    ],
  };
}

function refreshCharts() {
  if (hasTrend.value) renderTrend(buildTrendOption());
  if (hasRadar.value) renderRadar(buildRadarOption());
}

watch(
  [monitor, loading, isLight],
  () => {
    if (!loading.value && monitor.value) {
      // 等 DOM 挂载后渲染
      requestAnimationFrame(refreshCharts);
    }
  },
  { flush: 'post' },
);
</script>

<template>
  <CockpitModal
    :open="open"
    :title="`回路详情 · ${tagName || loopId || ''}`"
    @close="emit('close')"
  >
    <div v-if="loading" class="ld__state">加载中…</div>

    <div v-else class="ld">
      <!-- 头部：回路号 + 名称 + 装置 + 等级徽章 + 评分 -->
      <div class="ld__head">
        <div class="ld__head-main">
          <span class="ld__tag">{{ tagName }}</span>
          <span v-if="description" class="ld__desc">{{ description }}</span>
          <span v-if="unitName" class="ld__unit">{{ unitName }}</span>
        </div>
        <div class="ld__head-score">
          <span
            v-if="gradeText"
            class="ld__grade"
            :style="{ color: gradeColor, borderColor: gradeColor }"
          >{{ gradeText }}</span>
          <span class="ld__score" :style="{ color: gradeColor }">
            {{ score === null ? '—' : score.toFixed(1) }}
          </span>
        </div>
      </div>

      <div v-if="monitorUnavailable" class="ld__notice">
        实时运行数据不可用（当前角色无回路监控权限或服务异常），仅展示台账基础信息。
      </div>

      <!-- 三列主体 -->
      <div v-if="monitor" class="ld__grid">
        <!-- 实时值区 -->
        <div class="ld__col">
          <div class="ld__col-hd">实时值</div>
          <div class="ld__values">
            <div v-for="it in realtimeItems" :key="it.label" class="ld__value">
              <span class="ld__value-label">{{ it.label }}</span>
              <span class="ld__value-num">{{ it.value }}</span>
            </div>
          </div>
          <div class="ld__readat">
            读取于 {{ formatLocalTime(monitor.currentValues?.readAt, 'MM-DD HH:mm:ss') }}
          </div>
        </div>

        <!-- 趋势曲线 -->
        <div class="ld__col">
          <div class="ld__col-hd">
            趋势曲线<span class="ld__col-sub">{{ WINDOW_LABELS[cockpitStore.timeWindow] }} · PV/SP/OP</span>
          </div>
          <div class="ld__chart">
            <div v-if="!hasTrend" class="ld__chart-empty">暂无趋势数据</div>
            <EchartsUI v-show="hasTrend" ref="trendRef" height="100%" />
          </div>
        </div>

        <!-- 六维雷达 -->
        <div class="ld__col">
          <div class="ld__col-hd">六维形态</div>
          <div class="ld__chart">
            <div v-if="!hasRadar" class="ld__chart-empty">暂无形态数据</div>
            <EchartsUI v-show="hasRadar" ref="radarRef" height="100%" />
          </div>
        </div>
      </div>

      <!-- 底部：最近闭环记录摘要（无则隐藏） -->
      <div v-if="closedOrder" class="ld__closed">
        <span class="ld__closed-label">最近闭环</span>
        <span class="ld__closed-no mono">{{ closedOrder.orderNo }}</span>
        <span class="ld__closed-title" :title="closedOrder.title">
          {{ closedOrder.title }}
        </span>
        <span class="ld__closed-verify">
          验证结论：{{ closedOrder.verifyResult === 'EFFECTIVE' ? '有效' : closedOrder.verifyResult === 'INEFFECTIVE' ? '无效' : '—' }}
        </span>
        <span class="ld__closed-at">
          {{ formatLocalTime(closedOrder.verifiedAt, 'YYYY-MM-DD HH:mm') }}
        </span>
      </div>
    </div>
  </CockpitModal>
</template>

<style scoped>
.ld__state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
  font-size: 12px;
  color: var(--ck-text-3);
}

.ld__head {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--ck-border);
}

.ld__head-main {
  display: flex;
  gap: 10px;
  align-items: baseline;
  min-width: 0;
}

.ld__tag {
  font-size: 17px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text);
}

.ld__desc {
  overflow: hidden;
  font-size: 12px;
  color: var(--ck-text-2);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ld__unit {
  flex: none;
  padding: 1px 8px;
  font-size: 11px;
  color: var(--ck-text-2);
  background: var(--ck-panel-3);
  border-radius: 999px;
}

.ld__head-score {
  display: flex;
  flex: none;
  gap: 10px;
  align-items: center;
}

.ld__grade {
  padding: 2px 10px;
  font-size: 12px;
  border: 1px solid;
  border-radius: 999px;
}

.ld__score {
  font-size: 26px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.ld__notice {
  margin-top: 12px;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--ck-grade-fair);
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
}

.ld__grid {
  display: grid;
  grid-template-columns: 200px 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
}

.ld__col {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
}

.ld__col-hd {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ck-text);
  border-bottom: 1px solid var(--ck-border);
}

.ld__col-sub {
  font-size: 10px;
  font-weight: 400;
  color: var(--ck-text-3);
}

.ld__values {
  display: grid;
  flex: 1;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  align-content: start;
  padding: 10px 12px;
}

.ld__value {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
  background: var(--ck-panel-3);
  border-radius: 6px;
}

.ld__value-label {
  font-size: 10px;
  color: var(--ck-text-3);
}

.ld__value-num {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text);
}

.ld__readat {
  flex: none;
  padding: 6px 12px;
  font-size: 10px;
  color: var(--ck-text-3);
  border-top: 1px solid var(--ck-border);
}

.ld__chart {
  position: relative;
  flex: 1;
  min-height: 220px;
}

.ld__chart-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  color: var(--ck-text-3);
}

.ld__closed {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-top: 12px;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--ck-text-2);
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
}

.ld__closed-label {
  flex: none;
  font-weight: 600;
  color: var(--ck-grade-excellent);
}

.ld__closed-no {
  flex: none;
  font-variant-numeric: tabular-nums;
}

.ld__closed-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ld__closed-verify,
.ld__closed-at {
  flex: none;
  color: var(--ck-text-3);
}

.mono {
  font-variant-numeric: tabular-nums;
}
</style>
