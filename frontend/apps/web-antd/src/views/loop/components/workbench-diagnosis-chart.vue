<script lang="ts" setup>
/**
 * 工作台 · 诊断行曲线图（单页四区重构 v2 · 2026-08-07）
 *
 * 诊断行右半区：PV/OP 趋势曲线 + FFT 频谱曲线，Segmented 切换。
 * - PV/OP：取近 24h 波形（getWaveformApi），双线（PV 主轴 / OP 副轴）
 * - FFT：取诊断可视化频谱（getDiagnosisVisualizationApi），柱状图
 * - ECharts 渲染，含坐标轴、tooltip、dataZoom
 *
 * 数据自加载：watch loopId 变化时拉取，避免父组件过度耦合。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Empty, Segmented, Spin } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisVisualizationApi, getWaveformApi } from '#/api/diagnosis';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

defineOptions({ name: 'WorkbenchDiagnosisChart' });

const props = defineProps<{
  loopId: null | string;
}>();

type ChartMode = 'fft' | 'waveform';

const mode = ref<ChartMode>('waveform');
const loading = ref(false);
const waveform = ref<DiagnosisApi.WaveformResult | null>(null);
const visualization = ref<DiagnosisApi.DiagnosisVisualizationData | null>(null);

const { themeColors } = useClpmTheme();
const { getTooltipPreset } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

/** 时间戳纳秒/微秒→毫秒 */
function toMs(ts: number): number {
  const abs = Math.abs(ts);
  if (abs >= 10_000_000_000_000_000) return Math.floor(ts / 1_000_000);
  if (abs >= 10_000_000_000_000) return Math.floor(ts / 1000);
  return ts;
}

function fmtTime(ts: number): string {
  return dayjs(toMs(ts) + 8 * 3600 * 1000).format('MM-DD HH:mm');
}

async function loadData(loopId: string) {
  loading.value = true;
  try {
    const endTime = dayjs();
    const startTime = endTime.subtract(24, 'hour');
    const [wf, vis] = await Promise.all([
      getWaveformApi(loopId, {
        startTime: startTime.toISOString(),
        endTime: endTime.toISOString(),
        downsample: true,
        maxPoints: 1500,
      }).catch(() => null),
      getDiagnosisVisualizationApi(loopId).catch(() => null),
    ]);
    waveform.value = wf;
    visualization.value = vis;
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.loopId,
  (id) => {
    if (id) {
      loadData(id);
    } else {
      waveform.value = null;
      visualization.value = null;
    }
  },
  { immediate: true },
);

function buildWaveformOption() {
  const wf = waveform.value;
  if (!wf || !wf.timestamps?.length) return null;
  const xData = wf.timestamps.map((t) => fmtTime(t));
  return {
    grid: { top: 28, right: 48, bottom: 36, left: 44, containLabel: true },
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis' as const,
    },
    legend: {
      data: ['PV', 'OP'],
      top: 4,
      textStyle: { fontSize: 11 },
      itemWidth: 10,
      itemHeight: 6,
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
    },
    yAxis: [
      {
        type: 'value' as const,
        name: 'PV',
        nameTextStyle: { fontSize: 10, color: themeColors.value.INFO },
        axisLabel: { color: themeColors.value.NEUTRAL, fontSize: 10 },
        splitLine: {
          lineStyle: {
            color: themeColors.value.NEUTRAL,
            type: 'dashed' as const,
            opacity: 0.4,
          },
        },
      },
      {
        type: 'value' as const,
        name: 'OP',
        nameTextStyle: { fontSize: 10, color: themeColors.value.WARNING },
        axisLabel: { color: themeColors.value.NEUTRAL, fontSize: 10 },
        splitLine: { show: false },
      },
    ],
    dataZoom: [{ type: 'inside', start: 0, end: 100 }],
    series: [
      {
        name: 'PV',
        type: 'line' as const,
        smooth: false,
        showSymbol: false,
        lineStyle: { width: 1.5, color: themeColors.value.INFO },
        itemStyle: { color: themeColors.value.INFO },
        data: wf.pv,
      },
      {
        name: 'OP',
        type: 'line' as const,
        yAxisIndex: 1,
        smooth: false,
        showSymbol: false,
        lineStyle: { width: 1.5, color: themeColors.value.WARNING },
        itemStyle: { color: themeColors.value.WARNING },
        data: wf.op,
      },
    ],
  };
}

function buildFftOption() {
  const vis = visualization.value;
  const spec = vis?.spectrum;
  if (!spec || !spec.frequencies?.length) return null;
  return {
    grid: { top: 28, right: 16, bottom: 36, left: 48, containLabel: true },
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis' as const,
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params;
        return `频率：${p?.axisValue} Hz<br/>幅值：<b>${Number(p?.value ?? 0).toFixed(4)}</b>`;
      },
    },
    xAxis: {
      type: 'category' as const,
      data: spec.frequencies.map((f) => Number(f).toFixed(3)),
      name: 'Hz',
      nameTextStyle: { fontSize: 10, color: themeColors.value.NEUTRAL },
      axisLabel: {
        color: themeColors.value.NEUTRAL,
        fontSize: 10,
        hideOverlap: true,
      },
    },
    yAxis: {
      type: 'value' as const,
      name: '幅值',
      nameTextStyle: { fontSize: 10, color: themeColors.value.NEUTRAL },
      axisLabel: { color: themeColors.value.NEUTRAL, fontSize: 10 },
      splitLine: {
        lineStyle: {
          color: themeColors.value.NEUTRAL,
          type: 'dashed' as const,
          opacity: 0.4,
        },
      },
    },
    dataZoom: [{ type: 'inside', start: 0, end: 100 }],
    series: [
      {
        name: '幅值',
        type: 'bar' as const,
        barWidth: '60%',
        itemStyle: {
          color: themeColors.value.INFO,
          borderRadius: [2, 2, 0, 0],
        },
        data: spec.amplitudes,
        markLine:
          spec.peakFrequency == null
            ? undefined
            : {
                silent: true,
                symbol: 'none',
                lineStyle: {
                  color: themeColors.value.DANGER,
                  type: 'dashed' as const,
                },
                data: [
                  {
                    xAxis: Number(spec.peakFrequency).toFixed(3),
                    label: {
                      formatter: `峰频 ${Number(spec.peakFrequency).toFixed(3)}Hz`,
                      fontSize: 10,
                      color: themeColors.value.DANGER,
                    },
                  },
                ],
              },
      },
    ],
  };
}

function refresh() {
  const opt =
    mode.value === 'waveform' ? buildWaveformOption() : buildFftOption();
  if (opt) renderEcharts(opt);
}

watch(
  [mode, waveform, visualization],
  () => {
    refresh();
  },
  { deep: true, flush: 'post' },
);

const hasData = computed(() => {
  if (mode.value === 'waveform') {
    return !!waveform.value?.timestamps?.length;
  }
  return !!visualization.value?.spectrum?.frequencies?.length;
});
</script>

<template>
  <div class="diag-chart">
    <div class="diag-chart__header">
      <span class="diag-chart__title">
        {{ mode === 'waveform' ? 'PV/OP 曲线' : 'FFT 频谱' }}
      </span>
      <Segmented
        v-model:value="mode"
        size="small"
        :options="[
          { label: 'PV/OP', value: 'waveform' },
          { label: 'FFT', value: 'fft' },
        ]"
      />
    </div>
    <Spin :spinning="loading" size="small" class="diag-chart__body">
      <div v-if="hasData" class="diag-chart__canvas">
        <EchartsUI ref="chartRef" height="100%" />
      </div>
      <Empty
        v-else
        description="暂无曲线数据"
        :image="Empty.PRESENTED_IMAGE_SIMPLE"
        class="diag-chart__empty"
      />
    </Spin>
  </div>
</template>

<style scoped>
.diag-chart {
  display: flex;
  flex-direction: column;
  gap: 4px;
  height: 100%;
  min-height: 0;
}

.diag-chart__header {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
}

.diag-chart__title {
  font-size: 12px;
  color: hsl(var(--foreground) / 60%);
}

.diag-chart__body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.diag-chart__canvas {
  width: 100%;
  height: 100%;
}

.diag-chart__empty {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  margin: 0;
}
</style>
