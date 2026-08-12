<script lang="ts" setup>
/**
 * 工作台 R4 主画布 · 性能指标模式（Phase 1 重构 · 2026-08-12）
 *
 * KPI 历史混合图：
 * - 综合评分：折线（主线，蓝色 #1d4ed8，宽度 2.2，带数据点）
 * - 平稳率/准确率/快速率：棒图（绿/紫/琥珀，opacity 0.75，每时间点 3 根并列）
 * - 告警线：红色虚线 #c23434，dasharray 5,4，opacity 0.8
 * - 棒图粒度自适应：8h/24h 按小时、72h 按 2h 桶、168h 聚合为日粒度 7 组
 * - tooltip axis 模式 + dataZoom inside+slider
 *
 * 数据来源：父级传入 snapshots（GET /performance/loops/snapshots）。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { KpiSnapshotItem } from '#/api/metric';

import { computed, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import dayjs from 'dayjs';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

defineOptions({ name: 'WorkbenchKpiHistory' });

const props = withDefaults(defineProps<Props>(), {
  alarmLine: 60,
  timeWindow: '24h',
});

interface Props {
  /** KPI 快照序列（来自 GET /performance/loops/snapshots） */
  snapshots: KpiSnapshotItem[];
  /** 告警线阈值（默认 60，来自定级阈值配置） */
  alarmLine?: number;
  /** 时间窗口档位 */
  timeWindow?: '8h' | '24h' | '72h' | '168h' | 'custom';
}

const { chartTextColor, chartSplitLineColor } = useClpmTheme();
const { getTooltipPreset } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

// ===== 配色常量（对齐设计规范 §R4 性能指标模式）=====
const SCORE_COLOR = '#1d4ed8';
const STEADY_COLOR = '#1a7f4b';
const ACCURACY_COLOR = '#7c3aed';
const FAST_COLOR = '#b45309';
const ALARM_COLOR = '#c23434';

/** 按时间窗口分桶聚合 */
interface Bucket {
  ts: number;
  score: number[];
  steady: number[];
  accuracy: number[];
  fast: number[];
}

function toBucketTs(ts: null | string, window: string): null | number {
  if (!ts) return null;
  const d = dayjs(ts);
  if (!d.isValid()) return null;
  if (window === '168h') {
    // 日粒度：取当天 00:00
    return d.startOf('day').valueOf();
  }
  if (window === '72h') {
    // 2 小时桶
    const hour = d.hour();
    return d
      .hour(hour - (hour % 2))
      .minute(0)
      .second(0)
      .millisecond(0)
      .valueOf();
  }
  // 8h / 24h / custom：按小时
  return d.startOf('hour').valueOf();
}

/** 按 timeWindow 聚合快照为桶 */
function aggregateBuckets(): Bucket[] {
  const map = new Map<number, Bucket>();
  for (const s of props.snapshots) {
    const ts = toBucketTs(s.tsStart, props.timeWindow);
    if (ts === null) continue;
    let b = map.get(ts);
    if (!b) {
      b = { ts, accuracy: [], fast: [], score: [], steady: [] };
      map.set(ts, b);
    }
    if (s.score != null) b.score.push(Number(s.score));
    if (s.steadyRate != null) b.steady.push(Number(s.steadyRate));
    if (s.accuracyRate != null) b.accuracy.push(Number(s.accuracyRate));
    if (s.fastRate != null) b.fast.push(Number(s.fastRate));
  }
  return [...map.values()].sort((a, b) => a.ts - b.ts);
}

function avg(arr: number[]): null | number {
  if (arr.length === 0) return null;
  let sum = 0;
  for (const v of arr) sum += v;
  return sum / arr.length;
}

function fmtTime(ts: number): string {
  if (props.timeWindow === '168h') return dayjs(ts).format('MM-DD');
  return dayjs(ts).format('MM-DD HH:mm');
}

/** 构造 ECharts option */
function buildOption() {
  const buckets = aggregateBuckets();
  if (buckets.length === 0) return null;

  const xData = buckets.map((b) => fmtTime(b.ts));
  const scoreData = buckets.map((b) => avg(b.score));
  const steadyData = buckets.map((b) => avg(b.steady));
  const accuracyData = buckets.map((b) => avg(b.accuracy));
  const fastData = buckets.map((b) => avg(b.fast));

  return {
    backgroundColor: 'transparent',
    grid: { top: 28, right: 24, bottom: 40, left: 40, containLabel: true },
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis' as const,
      axisPointer: { type: 'shadow' as const },
    },
    legend: {
      data: ['综合评分', '平稳率', '准确率', '快速率'],
      top: 4,
      right: 8,
      textStyle: { fontSize: 11, color: chartTextColor.value },
      itemWidth: 12,
      itemHeight: 8,
    },
    xAxis: {
      type: 'category' as const,
      boundaryGap: true,
      data: xData,
      axisLabel: {
        color: chartTextColor.value,
        fontSize: 10,
        hideOverlap: true,
      },
      axisLine: { lineStyle: { color: chartSplitLineColor.value } },
    },
    yAxis: {
      type: 'value' as const,
      min: 0,
      max: 100,
      axisLabel: { color: chartTextColor.value, fontSize: 10 },
      splitLine: {
        lineStyle: {
          color: chartSplitLineColor.value,
          type: 'dashed' as const,
          opacity: 0.5,
        },
      },
    },
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
    series: [
      {
        name: '平稳率',
        type: 'bar',
        barMaxWidth: 14,
        barGap: '10%',
        barCategoryGap: '30%',
        itemStyle: { color: STEADY_COLOR, opacity: 0.75 },
        data: steadyData,
      },
      {
        name: '准确率',
        type: 'bar',
        barMaxWidth: 14,
        itemStyle: { color: ACCURACY_COLOR, opacity: 0.75 },
        data: accuracyData,
      },
      {
        name: '快速率',
        type: 'bar',
        barMaxWidth: 14,
        itemStyle: { color: FAST_COLOR, opacity: 0.75 },
        data: fastData,
      },
      {
        name: '综合评分',
        type: 'line',
        symbol: 'circle',
        symbolSize: 5,
        showSymbol: true,
        lineStyle: { width: 2.2, color: SCORE_COLOR },
        itemStyle: { color: SCORE_COLOR },
        z: 10,
        data: scoreData,
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: {
            color: ALARM_COLOR,
            type: 'dashed',
            width: 1.2,
            opacity: 0.8,
          },
          data: [
            {
              yAxis: props.alarmLine,
              label: {
                formatter: `告警 ${props.alarmLine}`,
                position: 'insideEndTop' as const,
                fontSize: 10,
                color: ALARM_COLOR,
              },
            },
          ],
        },
      },
    ] as any[],
  };
}

function refresh() {
  const opt = buildOption();
  if (opt) renderEcharts(opt);
}

const hasData = computed(() => props.snapshots.length > 0);

watch(
  () => [props.snapshots, props.alarmLine, props.timeWindow],
  () => {
    refresh();
  },
  { deep: true, flush: 'post' },
);
</script>

<template>
  <div class="kpi-history">
    <EchartsUI ref="chartRef" height="100%" />
    <div v-if="!hasData" class="kpi-history__empty">
      <span>该时段暂无 KPI 快照数据</span>
    </div>
  </div>
</template>

<style scoped>
.kpi-history {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.kpi-history__empty {
  position: absolute;
  top: 50%;
  left: 50%;
  font-size: 12px;
  color: hsl(var(--foreground) / 45%);
  pointer-events: none;
  transform: translate(-50%, -50%);
}
</style>
