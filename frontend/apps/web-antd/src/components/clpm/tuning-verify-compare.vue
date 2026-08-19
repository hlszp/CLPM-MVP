<script lang="ts" setup>
/**
 * 整定效果验证 · 前后窗曲线对比共享组件（09 设计方案 §4.5/§6.4）
 *
 * 三件套：
 * 1. KPI 摘要条（评分 + 六率，前后窗涨跌箭头；无快照侧"数据不足"）
 * 2. 前后窗 SP/PV/OP 趋势对比（上下分区，Y 轴量纲对齐）
 * 3. PV/OP X-Y 轨迹对比（前窗=灰 / 后窗=蓝）
 *
 * 使用处：整定模块「效果验证」独立页 + 处置模块 VERIFYING 环节嵌入。
 * 数据实时拉取（GET /tuning/verification/data），不落库。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningApi } from '#/api/tuning';

import { computed, nextTick, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Empty, Spin, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getVerificationDataApi } from '#/api/tuning';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

interface Props {
  loopId: string;
  /** 对比时点 ISO 串（naive UTC 或带 Z）；为空则不查询 */
  pointTime?: string;
  /** 窗口小时数 1/2/4/8/24/72/168 */
  windowHours?: number;
}

const props = withDefaults(defineProps<Props>(), {
  pointTime: '',
  windowHours: 24,
});

const { chartTextColor, chartSplitLineColor } = useClpmTheme();
const { getTooltipPreset } = useEchartsPreset();

const loading = ref(false);
const loadError = ref('');
const data = ref<null | TuningApi.VerificationData>(null);

const beforeChartRef = ref<EchartsUIType>();
const afterChartRef = ref<EchartsUIType>();
const xyChartRef = ref<EchartsUIType>();
const { renderEcharts: renderBefore } = useEcharts(beforeChartRef);
const { renderEcharts: renderAfter } = useEcharts(afterChartRef);
const { renderEcharts: renderXy } = useEcharts(xyChartRef);

// ===== 颜色 =====
const PV_COLOR = '#1d4ed8';
const SP_COLOR = '#6b7280';
const OP_COLOR = '#b45309';
const BEFORE_COLOR = '#6b7280';
const AFTER_COLOR = '#1d4ed8';

function fmtTs(ts: string): string {
  return dayjs(ts).format('MM-DD HH:mm');
}

/** 趋势图 option（前/后窗共用结构，Y 轴对齐由 yRange 保证） */
function buildTrendOption(
  wf: TuningApi.WaveformData,
  title: string,
  yRange: { max: number; min: number },
) {
  const xData = wf.timestamps.map((ts) => fmtTs(ts));
  return {
    title: {
      text: title,
      left: 8,
      top: 4,
      textStyle: { fontSize: 12, color: chartTextColor.value },
    },
    grid: { bottom: 32, left: 56, right: 56, top: 32 },
    legend: { textStyle: { color: chartTextColor.value }, top: 4, right: 8 },
    tooltip: getTooltipPreset(),
    xAxis: {
      type: 'category' as const,
      data: xData,
      axisLabel: { color: chartTextColor.value },
    },
    yAxis: [
      {
        type: 'value' as const,
        name: 'PV/SP',
        min: yRange.min,
        max: yRange.max,
        axisLabel: { color: chartTextColor.value },
        splitLine: { lineStyle: { color: chartSplitLineColor.value } },
      },
      {
        type: 'value' as const,
        name: 'OP',
        axisLabel: { color: chartTextColor.value },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: 'PV',
        type: 'line' as const,
        showSymbol: false,
        data: wf.pv,
        lineStyle: { color: PV_COLOR },
        itemStyle: { color: PV_COLOR },
      },
      {
        name: 'SP',
        type: 'line' as const,
        showSymbol: false,
        data: wf.sp,
        lineStyle: { color: SP_COLOR, type: 'dashed' as const },
        itemStyle: { color: SP_COLOR },
      },
      {
        name: 'OP',
        type: 'line' as const,
        showSymbol: false,
        yAxisIndex: 1,
        data: wf.op,
        lineStyle: { color: OP_COLOR, width: 1 },
        itemStyle: { color: OP_COLOR },
      },
    ],
  };
}

/** 前后窗 PV 统一 Y 量程（量纲对齐） */
function computeYRange(): { max: number; min: number } {
  const vals: number[] = [];
  for (const wf of [data.value?.before, data.value?.after]) {
    for (const v of wf?.pv ?? []) if (v != null) vals.push(v);
    for (const v of wf?.sp ?? []) if (v != null) vals.push(v);
  }
  if (vals.length === 0) return { min: 0, max: 1 };
  let min = Math.min(...vals);
  let max = Math.max(...vals);
  if (max - min < 1e-6) {
    max += 1;
    min -= 1;
  }
  const pad = (max - min) * 0.08;
  return {
    min: Math.floor((min - pad) * 100) / 100,
    max: Math.ceil((max + pad) * 100) / 100,
  };
}

function renderAll() {
  const d = data.value;
  if (!d) return;
  const yRange = computeYRange();
  const afterTitle = d.afterTruncated ? '后窗（数据截至当前时刻）' : '后窗';
  nextTick(() => {
    renderBefore(buildTrendOption(d.before, '前窗', yRange));
    renderAfter(buildTrendOption(d.after, afterTitle, yRange));
    // X-Y 轨迹：OP 为 x、PV 为 y
    const toXY = (wf: TuningApi.WaveformData) => {
      const pts: [number, number][] = [];
      wf.op.forEach((op, i) => {
        const pv = wf.pv[i];
        if (op != null && pv != null) pts.push([op, pv]);
      });
      return pts;
    };
    renderXy({
      grid: { bottom: 40, left: 56, right: 24, top: 32 },
      legend: { textStyle: { color: chartTextColor.value }, top: 4 },
      tooltip: { trigger: 'item' },
      xAxis: {
        type: 'value' as const,
        name: 'OP',
        axisLabel: { color: chartTextColor.value },
        splitLine: { lineStyle: { color: chartSplitLineColor.value } },
      },
      yAxis: {
        type: 'value' as const,
        name: 'PV',
        axisLabel: { color: chartTextColor.value },
        splitLine: { lineStyle: { color: chartSplitLineColor.value } },
      },
      series: [
        {
          name: '前窗',
          type: 'scatter' as const,
          symbolSize: 5,
          data: toXY(d.before),
          itemStyle: { color: BEFORE_COLOR, opacity: 0.55 },
        },
        {
          name: '后窗',
          type: 'scatter' as const,
          symbolSize: 5,
          data: toXY(d.after),
          itemStyle: { color: AFTER_COLOR, opacity: 0.75 },
        },
      ],
    });
  });
}

async function load() {
  if (!props.loopId || !props.pointTime) return;
  loading.value = true;
  loadError.value = '';
  try {
    data.value = await getVerificationDataApi({
      loopId: props.loopId,
      pointTime: props.pointTime,
      windowHours: props.windowHours,
    });
    renderAll();
  } catch (error: any) {
    loadError.value = error?.message || '验证数据加载失败';
    data.value = null;
  } finally {
    loading.value = false;
  }
}

watch(() => [props.loopId, props.pointTime, props.windowHours], load, {
  immediate: true,
});

// ===== KPI 摘要条 =====
const KPI_KEYS: {
  key: keyof TuningApi.KpiSummary;
  label: string;
  percent?: boolean;
}[] = [
  { key: 'score', label: '评分' },
  { key: 'goodValueRate', label: '完好率', percent: true },
  { key: 'effectiveAutoRate', label: '有效自控率', percent: true },
  { key: 'steadyRate', label: '平稳率', percent: true },
  { key: 'accuracyRate', label: '精确率', percent: true },
  { key: 'fastRate', label: '快速率', percent: true },
];

interface KpiDeltaRow {
  key: string;
  label: string;
  before: string;
  after: string;
  delta: null | number;
}

const kpiRows = computed<KpiDeltaRow[]>(() => {
  const b = data.value?.kpiBefore;
  const a = data.value?.kpiAfter;
  return KPI_KEYS.map(({ key, label, percent }) => {
    const bv = b?.[key];
    const av = a?.[key];
    // 后端快照已是 0~100 百分比口径（Numeric(5,2)），直接拼接 % 展示，
    // 不可再 ×100（与 handling-detail-drawer fmtKpi 同口径）。
    const fmt = (v: any) =>
      typeof v === 'number'
        ? (percent
          ? `${v.toFixed(1)}%`
          : v.toFixed(1))
        : '—';
    const delta =
      typeof bv === 'number' && typeof av === 'number' ? av - bv : null;
    return { key, label, before: fmt(bv), after: fmt(av), delta };
  });
});

const hasKpi = computed(
  () => !!(data.value?.kpiBefore || data.value?.kpiAfter),
);
</script>

<template>
  <Spin :spinning="loading">
    <Empty
      v-if="loadError"
      :description="loadError"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
    />
    <template v-else-if="data">
      <!-- KPI 摘要条 -->
      <div v-if="hasKpi" class="kpi-strip">
        <div v-for="row in kpiRows" :key="row.key" class="kpi-cell">
          <div class="kpi-label">{{ row.label }}</div>
          <div class="kpi-values">
            <span>{{ row.before }}</span>
            <span class="kpi-arrow">→</span>
            <span>{{ row.after }}</span>
            <span
              v-if="row.delta != null"
              class="kpi-delta"
              :class="
                row.delta > 0
                  ? 'kpi-delta--up'
                  : row.delta < 0
                    ? 'kpi-delta--down'
                    : ''
              "
            >
              {{ row.delta > 0 ? '▲' : row.delta < 0 ? '▼' : '' }}
              {{ Math.abs(row.delta).toFixed(1) }}
            </span>
          </div>
        </div>
      </div>
      <div v-else class="mb-2 text-xs text-neutral-400">
        窗口内无 KPI 快照（数据不足）
      </div>

      <!-- 前后窗趋势对比（上下分区） -->
      <EchartsUI ref="beforeChartRef" style="width: 100%; height: 240px" />
      <EchartsUI ref="afterChartRef" style="width: 100%; height: 240px" />

      <!-- X-Y 轨迹对比 -->
      <div class="mt-2 flex items-center gap-2">
        <span class="text-xs font-medium text-neutral-500">PV/OP X-Y 轨迹</span>
        <Tag :color="BEFORE_COLOR" class="mr-0">前窗</Tag>
        <Tag :color="AFTER_COLOR">后窗</Tag>
      </div>
      <EchartsUI ref="xyChartRef" style="width: 100%; height: 260px" />
    </template>
    <Empty
      v-else-if="!loading"
      description="请选择回路与对比时点"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
    />
  </Spin>
</template>

<style scoped>
.kpi-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 24px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: rgb(0 0 0 / 2%);
  border-radius: 4px;
}

.kpi-cell {
  min-width: 120px;
}

.kpi-label {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.kpi-values {
  display: flex;
  gap: 6px;
  align-items: baseline;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.kpi-arrow {
  color: hsl(var(--muted-foreground));
}

.kpi-delta {
  font-size: 11px;
}

.kpi-delta--up {
  color: #15803d;
}

.kpi-delta--down {
  color: #b91c1c;
}
</style>
