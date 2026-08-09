<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

/**
 * 工作台 · 评分趋势图（单页四区重构 v2 · 2026-08-07）
 *
 * 评估行右半区（50% 宽）：综合评分小时趋势折线图。
 * - ECharts 渲染，含 X/Y 坐标轴、tooltip、dataZoom
 * - 时段切换：最近 8h / 12h / 24h / 48h / 72h（Segmented）
 * - 取整点小时评分值（scoreHistory 已按 tsStart 升序）
 *
 * 数据来源：父级 provide 的 scoreHistory（KpiSnapshotItem[]）
 */
import type { KpiSnapshotItem } from '#/api/metric';

import { computed, inject, ref, type Ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Segmented } from 'ant-design-vue';
import dayjs from 'dayjs';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

defineOptions({ name: 'ScoreTrendChart' });

type RangeOption = {
  hours: number;
  label: string;
  value: string;
};

const RANGE_OPTIONS: RangeOption[] = [
  { hours: 8, label: '8小时', value: '8h' },
  { hours: 12, label: '12小时', value: '12h' },
  { hours: 24, label: '24小时', value: '24h' },
  { hours: 48, label: '48小时', value: '48h' },
  { hours: 72, label: '72小时', value: '72h' },
];

const scoreHistory = inject<Ref<KpiSnapshotItem[]>>(
  'scoreHistory',
  computed(() => []),
);

const selectedRange = ref('24h');

const { themeColors } = useClpmTheme();
const { getEchartsBase, getTooltipPreset } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

/** 按选中时段过滤后的评分序列 */
const filtered = computed(() => {
  const opt = RANGE_OPTIONS.find((o) => o.value === selectedRange.value);
  if (!opt) return [];
  const cutoff = dayjs().subtract(opt.hours, 'hour');
  return scoreHistory.value.filter((s) => {
    if (!s.tsStart) return false;
    return dayjs(s.tsStart).isAfter(cutoff);
  });
});

/** 构造 ECharts option */
function buildOption() {
  const items = filtered.value;
  const xData = items.map((s) => dayjs(s.tsStart).format('MM-DD HH:mm'));
  const yData = items.map((s) => (s.score == null ? null : Number(s.score)));

  return {
    ...getEchartsBase(),
    grid: { top: 28, right: 16, bottom: 40, left: 40, containLabel: true },
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis' as const,
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params;
        const v = p?.value;
        return `${p?.axisValue}<br/>评分：<b>${v == null ? '—' : Number(v).toFixed(2)}</b>`;
      },
    },
    xAxis: {
      type: 'category' as const,
      boundaryGap: false,
      data: xData,
      axisLabel: {
        color: themeColors.value.NEUTRAL,
        fontSize: 10,
        hideOverlap: true,
      },
      axisLine: { lineStyle: { color: themeColors.value.NEUTRAL } },
    },
    yAxis: {
      type: 'value' as const,
      min: 0,
      max: 100,
      axisLabel: {
        color: themeColors.value.NEUTRAL,
        fontSize: 10,
      },
      splitLine: {
        lineStyle: {
          color: themeColors.value.NEUTRAL,
          type: 'dashed' as const,
          opacity: 0.4,
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
        name: '综合评分',
        type: 'line' as const,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        showSymbol: false,
        connectNulls: true,
        lineStyle: { width: 1.8, color: themeColors.value.INFO },
        itemStyle: { color: themeColors.value.INFO },
        areaStyle: {
          color: {
            type: 'linear' as const,
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.INFO}33` },
              { offset: 1, color: `${themeColors.value.INFO}05` },
            ],
          },
        },
        data: yData,
      },
    ],
  };
}

function refresh() {
  if (filtered.value.length === 0) return;
  renderEcharts(buildOption());
}

watch(
  [filtered, selectedRange],
  () => {
    refresh();
  },
  { deep: true, flush: 'post' },
);

const hasData = computed(() => filtered.value.length > 0);
</script>

<template>
  <div class="score-trend">
    <div class="score-trend__header">
      <span class="score-trend__title">评分趋势</span>
      <Segmented
        v-model:value="selectedRange"
        size="small"
        :options="RANGE_OPTIONS"
      />
    </div>
    <div v-if="hasData" class="score-trend__chart">
      <EchartsUI ref="chartRef" height="100%" />
    </div>
    <div v-else class="score-trend__empty">
      <div>该时段暂无评分数据</div>
      <div class="mt-1 text-xs text-gray-400">
        可切换时间窗，或先发起一次性能评估
      </div>
    </div>
  </div>
</template>

<style scoped>
.score-trend {
  display: flex;
  flex-direction: column;
  gap: 4px;
  height: 100%;
  min-height: 0;
}

.score-trend__header {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
}

.score-trend__title {
  font-size: 12px;
  color: hsl(var(--foreground) / 60%);
}

.score-trend__chart {
  flex: 1;
  min-height: 0;
}

.score-trend__empty {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  margin: 0;
}
</style>
