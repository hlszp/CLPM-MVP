<script lang="ts" setup>
/**
 * 整定工作台 · 锚点③ 仿真对比（09 设计方案 §4.3/§6.2）
 *
 * 当前 PID + 勾选推荐组（1~2 组）在同一辨识模型上做 SP 阶跃响应仿真：
 * 曲线叠加图（当前=灰/推荐1=蓝/推荐2=橙）+ 量化指标表
 * （上升时间/超调量/调节时间/ITAE）。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningWorkbenchContext } from '../composables/use-tuning-workbench';

import { computed, nextTick, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { Alert, Button, Card, Table } from 'ant-design-vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{ ctx: TuningWorkbenchContext }>();
const { ctx } = props;

const { chartTextColor, chartSplitLineColor } = useClpmTheme();
const { getTooltipPreset } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

/** 候选配色：当前=灰 / 推荐1=蓝 / 推荐2=橙（09 §6.2） */
const SERIES_COLORS = ['#6b7280', '#1d4ed8', '#b45309'];

const simResult = computed(() => ctx.simResult.value);
const candidates = computed(() => ctx.simResult.value?.candidateResponses ?? []);

function render() {
  const res = simResult.value;
  if (!res) return;
  const series = (res.candidateResponses ?? []).map((c, i) => ({
    name: c.label,
    type: 'line' as const,
    showSymbol: false,
    data: c.response.pv,
    lineStyle: { width: c.label === '当前 PID' ? 1.5 : 2, color: SERIES_COLORS[i] },
    itemStyle: { color: SERIES_COLORS[i] },
  }));
  nextTick(() => {
    renderEcharts({
      grid: { bottom: 48, left: 56, right: 16, top: 32 },
      legend: { textStyle: { color: chartTextColor.value }, top: 4 },
      series,
      tooltip: getTooltipPreset(),
      xAxis: {
        type: 'category',
        data: res.timestamps.map((t) => String(t)),
        name: '秒',
        axisLabel: { color: chartTextColor.value },
      },
      yAxis: {
        type: 'value',
        name: 'PV（增量）',
        axisLabel: { color: chartTextColor.value },
        splitLine: { lineStyle: { color: chartSplitLineColor.value } },
      },
    });
  });
}

watch(simResult, render);

const metricColumns = [
  { dataIndex: 'label', key: 'label', title: '参数组' },
  { dataIndex: 'riseTime', key: 'riseTime', title: '上升时间 (s)' },
  { dataIndex: 'overshoot', key: 'overshoot', title: '超调量 (%)' },
  { dataIndex: 'settlingTime', key: 'settlingTime', title: '调节时间 (s)' },
  { dataIndex: 'itae', key: 'itae', title: 'ITAE' },
];

const metricRows = computed(() =>
  candidates.value.map((c) => ({
    key: c.label,
    label: c.label,
    riseTime: c.metrics.riseTime ?? '—',
    overshoot: c.metrics.overshoot ?? '—',
    settlingTime: c.metrics.settlingTime ?? '—',
    itae: c.metrics.itae ?? '—',
  })),
);
</script>

<template>
  <Card id="tuning-anchor-simulate" size="small" class="tuning-section">
    <template #title>
      <span class="section-title">③ 仿真对比</span>
      <span class="ml-2 text-xs font-normal text-neutral-400">
        SP 阶跃响应（RK4 闭环仿真）
      </span>
    </template>
    <template #extra>
      <Button
        type="primary"
        size="small"
        :loading="ctx.simulating.value"
        :disabled="!ctx.canSimulate.value"
        @click="ctx.runSimulate()"
      >
        开始仿真
      </Button>
    </template>

    <Alert
      v-if="ctx.simError.value"
      class="mb-2"
      type="error"
      :message="ctx.simError.value"
      show-icon
    />
    <Alert
      v-else-if="!ctx.canSimulate.value && !simResult"
      type="info"
      message="请先在②整定矩阵勾选 1~2 组推荐参数"
      show-icon
    />

    <template v-if="simResult">
      <EchartsUI ref="chartRef" style="height: 320px; width: 100%" />
      <Table
        class="mt-3"
        :columns="metricColumns"
        :data-source="metricRows"
        :pagination="false"
        size="small"
      />
    </template>
  </Card>
</template>

<style scoped>
.section-title {
  font-size: 13px;
  font-weight: 600;
}
</style>
