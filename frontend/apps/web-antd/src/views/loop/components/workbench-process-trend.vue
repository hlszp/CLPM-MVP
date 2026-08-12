<script lang="ts" setup>
/**
 * 工作台 R4 主画布 · 过程变量模式（Phase 1 重构 · 2026-08-12）
 *
 * PV/SP/OP 同轴趋势图：
 * - 双 Y 轴：左轴 PV/SP（工程单位），右轴 OP（%）
 * - PV 蓝色实线 / SP 灰色虚线 / OP 琥珀色细线
 * - MODE 背景带（markArea）：AUTO 绿浅底 / MANUAL 红浅底
 * - 事件标记（markPoint）：▼ 诊断 / ◆ 整定 / ▐ 验证 / ▓ 缺口
 * - tooltip axis 模式 + dataZoom inside+slider
 *
 * 数据来源：父级传入 trend（GET /loops/{id}/monitor 的 trend 字段）。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { LoopApi } from '#/api/loop';

import { computed, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import dayjs from 'dayjs';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

defineOptions({ name: 'WorkbenchProcessTrend' });

/** 事件标记类型 */
interface ProcessEventMark {
  type: 'diagnosis' | 'tuning' | 'verify' | 'gap';
  timestamp: number;
  label?: string;
}

/** MODE 背景带 */
interface ModeBand {
  start: number;
  end: number;
  mode: string; // AUTO/MANUAL/CAS 等
  color?: string;
}

interface Props {
  /** 过程趋势数据（来自 GET /loops/{id}/monitor 的 trend 字段） */
  trend: LoopApi.MonitorTrend | null;
  /** PV 工程单位（如 "%"） */
  pvUnit?: string;
  /** OP 工程单位（如 "%"） */
  opUnit?: string;
  /** 事件标记列表（诊断/整定/验证/缺口） */
  eventMarks?: ProcessEventMark[];
  /** MODE 背景带数据（时间段+模式标签） */
  modeBands?: ModeBand[];
}

const props = withDefaults(defineProps<Props>(), {
  pvUnit: '',
  opUnit: '%',
  eventMarks: () => [],
  modeBands: () => [],
});

const { chartTextColor, chartSplitLineColor } = useClpmTheme();
const { getTooltipPreset } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

// ===== 颜色常量（对齐设计规范 §R4 主画布配色）=====
const PV_COLOR = '#1d4ed8';
const SP_COLOR = '#6b7280';
const OP_COLOR = '#b45309';
const DIAG_COLOR = '#c23434';
const TUNE_COLOR = '#1a7f4b';
const VERIFY_COLOR = '#1d4ed8';
const GAP_COLOR = '#9ca3af';

/** MODE → 背景色映射（浅底） */
function modeBandColor(mode: string, custom?: string): string {
  if (custom) return custom;
  const upper = mode.toUpperCase();
  if (upper === 'AUTO') return 'rgba(25, 135, 84, 0.08)';
  if (upper === 'MANUAL' || upper === 'MAN') return 'rgba(220, 53, 69, 0.08)';
  if (upper === 'CAS' || upper === 'CASCADE') return 'rgba(13, 110, 253, 0.06)';
  if (upper === 'REMOTE' || upper === 'RAC') return 'rgba(13, 110, 253, 0.06)';
  if (upper === 'APC') return 'rgba(13, 110, 253, 0.06)';
  return 'rgba(108, 117, 125, 0.06)';
}

/** 时间戳格式化 */
function fmtTime(ts: number): string {
  return dayjs(ts).format('MM-DD HH:mm');
}

/** 事件标记 → markPoint data */
function buildEventMarkPoints() {
  if (!props.eventMarks || props.eventMarks.length === 0) return [];
  return props.eventMarks.map((m) => {
    const color =
      m.type === 'diagnosis'
        ? DIAG_COLOR
        : m.type === 'tuning'
          ? TUNE_COLOR
          : m.type === 'verify'
            ? VERIFY_COLOR
            : GAP_COLOR;
    const symbol =
      m.type === 'diagnosis'
        ? 'triangle'
        : m.type === 'tuning'
          ? 'diamond'
          : m.type === 'verify'
            ? 'rect'
            : 'roundRect';
    return {
      coord: [m.timestamp, 0],
      symbol,
      symbolSize: 10,
      itemStyle: { color, borderColor: 'transparent' },
      label: {
        show: !!m.label,
        formatter: m.label ?? '',
        position: 'top',
        fontSize: 10,
        color,
      },
    };
  });
}

/** MODE 背景带 → markArea data */
function buildModeBandAreas() {
  if (!props.modeBands || props.modeBands.length === 0) return [];
  return props.modeBands.map((b) => [
    {
      xAxis: b.start,
      itemStyle: { color: modeBandColor(b.mode, b.color) },
    },
    { xAxis: b.end },
  ]);
}

/** 构造 ECharts option */
function buildOption() {
  const t = props.trend;
  if (!t || !t.timestamps || t.timestamps.length === 0) return null;

  const pvData: [number, null | number][] = t.timestamps.map((ts, i) => [
    ts,
    t.pv[i] ?? null,
  ]);
  const spData: [number, null | number][] = t.timestamps.map((ts, i) => [
    ts,
    t.sp[i] ?? null,
  ]);
  const opData: [number, null | number][] = t.timestamps.map((ts, i) => [
    ts,
    t.op[i] ?? null,
  ]);

  const markPoints = buildEventMarkPoints();
  const markAreas = buildModeBandAreas();

  const pvUnitSuffix = props.pvUnit ? ` (${props.pvUnit})` : '';
  const opUnitSuffix = props.opUnit ? ` (${props.opUnit})` : '';

  const series: any[] = [
    {
      name: 'PV',
      type: 'line',
      yAxisIndex: 0,
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: 1.8, color: PV_COLOR },
      itemStyle: { color: PV_COLOR },
      data: pvData,
      markArea:
        markAreas.length > 0
          ? { silent: true, data: markAreas }
          : undefined,
      markPoint:
        markPoints.length > 0
          ? { symbol: 'triangle', symbolSize: 10, data: markPoints }
          : undefined,
    },
    {
      name: 'SP',
      type: 'line',
      yAxisIndex: 0,
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: 1.2, color: SP_COLOR, type: 'dashed' },
      itemStyle: { color: SP_COLOR },
      data: spData,
    },
    {
      name: 'OP',
      type: 'line',
      yAxisIndex: 1,
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: 1.2, color: OP_COLOR, opacity: 0.85 },
      itemStyle: { color: OP_COLOR, opacity: 0.85 },
      data: opData,
    },
  ];
  return {
    backgroundColor: 'transparent',
    grid: { top: 28, right: 50, bottom: 40, left: 45, containLabel: true },
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis' as const,
      axisPointer: { type: 'line' as const },
      formatter: (params: any) => {
        const arr = Array.isArray(params) ? params : [params];
        if (arr.length === 0) return '';
        const first = arr[0];
        const ts = first?.axisValue;
        const time = ts ? fmtTime(Number(ts)) : '—';
        const lines: string[] = [time];
        for (const p of arr) {
          const name = p?.seriesName ?? '';
          const v = p?.value?.[1];
          lines.push(
            `<span style="color:${p?.color}">●</span> ${name}: <b>${v == null ? '—' : Number(v).toFixed(2)}</b>`,
          );
        }
        return lines.join('<br/>');
      },
    },
    legend: {
      data: ['PV', 'SP', 'OP'],
      top: 4,
      right: 8,
      textStyle: { fontSize: 11, color: chartTextColor.value },
      itemWidth: 12,
      itemHeight: 8,
    },
    xAxis: {
      type: 'time' as const,
      axisLabel: {
        color: chartTextColor.value,
        fontSize: 10,
        hideOverlap: true,
        formatter: (val: number) => fmtTime(val),
      },
      axisLine: { lineStyle: { color: chartSplitLineColor.value } },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: 'value' as const,
        name: `PV/SP${pvUnitSuffix}`,
        nameTextStyle: { fontSize: 10, color: chartTextColor.value },
        scale: true,
        axisLabel: { color: chartTextColor.value, fontSize: 10 },
        splitLine: {
          lineStyle: {
            color: chartSplitLineColor.value,
            type: 'dashed' as const,
            opacity: 0.5,
          },
        },
      },
      {
        type: 'value' as const,
        name: `OP${opUnitSuffix}`,
        nameTextStyle: { fontSize: 10, color: chartTextColor.value },
        min: 0,
        max: 100,
        position: 'right' as const,
        axisLabel: { color: chartTextColor.value, fontSize: 10 },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      {
        type: 'slider',
        height: 14,
        bottom: 4,
        start: 0,
        end: 100,
        showDetail: false,
      },
    ],
    series,
  };
}

function refresh() {
  const opt = buildOption();
  if (opt) renderEcharts(opt);
}

const hasData = computed(() => {
  const t = props.trend;
  return !!(t && t.timestamps && t.timestamps.length > 0);
});

watch(
  () => [props.trend, props.eventMarks, props.modeBands, props.pvUnit, props.opUnit],
  () => {
    refresh();
  },
  { deep: true, flush: 'post' },
);
</script>

<template>
  <div class="process-trend">
    <EchartsUI ref="chartRef" height="100%" />
    <div v-if="!hasData" class="process-trend__empty">
      <span>该时段暂无过程数据</span>
    </div>
  </div>
</template>

<style scoped>
.process-trend {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.process-trend__empty {
  position: absolute;
  top: 50%;
  left: 50%;
  font-size: 12px;
  color: hsl(var(--foreground) / 45%);
  pointer-events: none;
  transform: translate(-50%, -50%);
}
</style>
