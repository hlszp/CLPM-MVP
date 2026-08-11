<script lang="ts" setup>
/**
 * F3b 推理过程时间线 — reasoning-timeline.vue
 *
 * ECharts 单图：PV 波形折线 + 各诊断标签的算法事件点 scatter 叠加。
 *  - 主线：waveform.pv / waveform.timestamps
 *  - 事件点：从每个 label 的 evidence 中提取时间戳数组，按标签色着色
 *  - 无时间戳特征的算法：markLine 标注"基于全窗统计判定"
 *  - dataZoom 支持时间窗缩放
 *
 * 降级：waveform 为空显示占位。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { nextTick, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Empty } from 'ant-design-vue';

import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import {
  DIAGNOSIS_LABEL_COLOR_HEX_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';

defineOptions({ name: 'ReasoningTimeline' });

const props = defineProps<{
  /** 诊断标签结果列表 */
  labels: DiagnosisApi.DiagnosisLabelItem[];
  /** 算法元数据（用于映射标签→事件点） */
  meta: DiagnosisApi.AlgorithmMetaList | null;
  /** PV 波形数据 */
  waveform: DiagnosisApi.WaveformResult | null;
}>();

const { isDark, themeColors, chartColors } = useClpmTheme();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

/** 时间戳精度转换：纳秒/微秒级→毫秒级（与 ab-compare.vue 一致） */
function toMs(ts: number): number {
  const absTs = Math.abs(ts);
  if (absTs >= 10_000_000_000_000_000) return Math.floor(ts / 1_000_000);
  if (absTs >= 10_000_000_000_000) return Math.floor(ts / 1000);
  return ts;
}

/** evidence 中疑似时间戳数组的字段名 */
const TIMESTAMP_KEYS = [
  'peak_times',
  'shift_points',
  'step_indices',
  'event_times',
  'timestamps',
  'peakTimes',
  'shiftPoints',
  'stepIndices',
  'eventTimes',
];

interface EventPoint {
  xAxis: number;
  itemStyle: { color: string };
  labelName: string;
  value: number;
}

/** 从单个标签的 evidence 中提取事件点 */
function extractEvents(
  item: DiagnosisApi.DiagnosisLabelItem,
  color: string,
): EventPoint[] {
  const evidence = (item.evidence ?? {}) as Record<string, unknown>;
  const labelName =
    item.labelName || DIAGNOSIS_LABEL_NAME_MAP[item.label] || item.label;
  for (const key of TIMESTAMP_KEYS) {
    const raw = evidence[key];
    if (Array.isArray(raw) && raw.length > 0) {
      return raw
        .map(Number)
        .filter((t) => Number.isFinite(t))
        .map((t) => ({
          xAxis: toMs(t),
          itemStyle: { color },
          labelName,
          value: 1,
        }));
    }
  }
  return [];
}

/** 有无标签产生了事件点（决定是否画 markLine） */
function buildChart() {
  const wf = props.waveform;
  if (
    !wf ||
    !wf.pv ||
    wf.pv.length === 0 ||
    !wf.timestamps ||
    wf.timestamps.length === 0
  ) {
    renderEcharts({
      title: {
        left: 'center',
        text: '暂无波形数据',
        textStyle: { color: themeColors.value.NEUTRAL },
      },
    });
    return;
  }

  const ts = wf.timestamps.map((t) => toMs(t));
  const pvSeries = wf.pv.map((v) => (v === null ? null : v));

  // 收集各标签的事件点
  const allEvents: EventPoint[] = [];
  const noEventLabels: string[] = [];
  for (const item of props.labels ?? []) {
    const color =
      DIAGNOSIS_LABEL_COLOR_HEX_MAP[item.label] || themeColors.value.DANGER;
    const events = extractEvents(item, color);
    if (events.length > 0) {
      allEvents.push(...events);
    } else {
      noEventLabels.push(
        item.labelName || DIAGNOSIS_LABEL_NAME_MAP[item.label] || item.label,
      );
    }
  }

  const series: Record<string, unknown>[] = [
    {
      data: pvSeries,
      itemStyle: { color: themeColors.value.INFO },
      lineStyle: { width: 2 },
      name: 'PV',
      showSymbol: false,
      type: 'line',
    },
  ];

  if (allEvents.length > 0) {
    series.push({
      data: allEvents.map((e) => [e.xAxis, e.value]),
      itemStyle: {
        borderColor: chartColors.value.border,
        borderWidth: 1,
      },
      name: '算法事件点',
      symbolSize: 10,
      type: 'scatter',
    });
  }

  // 无时间戳的算法：markLine 标注全窗统计
  const markLines: Record<string, unknown>[] = [];
  if (noEventLabels.length > 0) {
    markLines.push({
      label: {
        color: themeColors.value.NEUTRAL,
        formatter: `基于全窗统计判定：${noEventLabels.join('、')}`,
      },
      lineStyle: { color: themeColors.value.NEUTRAL, type: 'dashed' },
      yAxis: 'average',
    });
  }

  renderEcharts({
    backgroundColor: 'transparent',
    dataZoom: [
      { end: 100, start: 0, type: 'inside' },
      { end: 100, start: 0, type: 'slider' },
    ],
    grid: { bottom: 60, containLabel: true, left: '2%', right: '2%', top: 40 },
    legend: { data: ['PV', '算法事件点'], top: 5 },
    series,
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      valueFormatter: (val) =>
        val === null || val === undefined ? '—' : Number(val).toFixed(3),
    },
    xAxis: {
      axisLabel: {
        formatter: (val: string) => {
          const d = new Date(Number(val) + 8 * 3600 * 1000);
          const hh = String(d.getUTCHours()).padStart(2, '0');
          const mm = String(d.getUTCMinutes()).padStart(2, '0');
          const dd = String(d.getUTCDate()).padStart(2, '0');
          const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
          return `${mo}-${dd} ${hh}:${mm}`;
        },
      },
      data: ts,
      type: 'category',
    },
    yAxis: { axisLabel: { formatter: '{value}' }, type: 'value' },
  });
}

watch(
  () => [props.waveform, props.labels, props.meta],
  () => {
    nextTick(buildChart);
  },
  { deep: true, immediate: true },
);

watch(isDark, () => {
  nextTick(buildChart);
});
</script>

<template>
  <ClpmDataCanvas title="推理过程时间线">
    <EchartsUI v-if="waveform" ref="chartRef" height="360px" />
    <Empty
      v-else
      description="暂无波形数据。请确认已选择回路与时间窗，且时间窗内有有效过程数据"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
    />
  </ClpmDataCanvas>
</template>
