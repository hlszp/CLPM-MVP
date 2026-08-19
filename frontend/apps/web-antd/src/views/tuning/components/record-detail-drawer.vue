<script lang="ts" setup>
/**
 * 整定记录 · 详情抽屉（09 设计方案 §6.3）
 *
 * 完整模型参数 + 推荐/当前 PID + 仿真快照图（simulationResult 落库 JSON
 * 直接画 ECharts）+ 关联处置项提示。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningApi } from '#/api/tuning';

import { computed, nextTick, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Descriptions,
  DescriptionsItem,
  Drawer,
  Empty,
  Spin,
  Tag,
} from 'ant-design-vue';

import { getTuningTaskDetailApi } from '#/api/tuning';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

import { tuningAlgoLabel } from '../constants';

const props = defineProps<{ recordId: null | string; visible: boolean }>();
const emit = defineEmits<{ 'update:visible': [boolean] }>();

const { chartTextColor, chartSplitLineColor } = useClpmTheme();
const { getTooltipPreset } = useEchartsPreset();

const loading = ref(false);
const detail = ref<null | TuningApi.TuningTaskDetail>(null);

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const SERIES_COLORS = ['#6b7280', '#1d4ed8', '#b45309'];

const simCandidates = computed(
  () =>
    (detail.value?.simulationResult as any)?.candidateResponses as
      | undefined
      | { label: string; response: { pv: number[] } }[],
);
const simTimestamps = computed(
  () =>
    (detail.value?.simulationResult as any)?.timestamps as number[] | undefined,
);

function renderChart() {
  const ts = simTimestamps.value;
  const candidates = simCandidates.value;
  if (!ts || !candidates?.length) return;
  nextTick(() => {
    renderEcharts({
      grid: { bottom: 40, left: 48, right: 16, top: 32 },
      legend: { textStyle: { color: chartTextColor.value }, top: 4 },
      series: candidates.map((c, i) => ({
        name: c.label,
        type: 'line' as const,
        showSymbol: false,
        data: c.response.pv,
        lineStyle: { width: 2, color: SERIES_COLORS[i % SERIES_COLORS.length] },
        itemStyle: { color: SERIES_COLORS[i % SERIES_COLORS.length] },
      })),
      tooltip: getTooltipPreset(),
      xAxis: {
        type: 'category',
        data: ts.map(String),
        axisLabel: { color: chartTextColor.value },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: chartTextColor.value },
        splitLine: { lineStyle: { color: chartSplitLineColor.value } },
      },
    });
  });
}

watch(
  () => [props.visible, props.recordId],
  async () => {
    if (!props.visible || !props.recordId) return;
    loading.value = true;
    detail.value = null;
    try {
      detail.value = await getTuningTaskDetailApi(props.recordId);
      renderChart();
    } finally {
      loading.value = false;
    }
  },
  { immediate: true },
);

function fmtPid(pid?: null | TuningApi.PidParams): string {
  if (!pid) return '—';
  return `P ${pid.kp} / I ${pid.ti} / D ${pid.td}`;
}

const paramsText = computed(() => {
  const p = detail.value?.modelParams;
  if (!p) return '—';
  return Object.entries(p)
    .filter(([, v]) => v != null)
    .map(([k, v]) => `${k}=${v}`)
    .join('，');
});
</script>

<template>
  <Drawer
    :open="visible"
    title="整定记录详情"
    width="640"
    @close="emit('update:visible', false)"
  >
    <Spin :spinning="loading">
      <template v-if="detail">
        <Descriptions size="small" :column="2" bordered>
          <DescriptionsItem label="回路">{{
            detail.tagName ?? detail.loopId
          }}</DescriptionsItem>
          <DescriptionsItem label="状态">
            <Tag>{{ detail.status }}</Tag>
          </DescriptionsItem>
          <DescriptionsItem label="模型类型">{{
            detail.modelType
          }}</DescriptionsItem>
          <DescriptionsItem label="模型参数">{{ paramsText }}</DescriptionsItem>
          <DescriptionsItem label="整定算法">{{
            tuningAlgoLabel(detail.algorithm)
          }}</DescriptionsItem>
          <DescriptionsItem label="拟合度">
            {{
              detail.fittingScore == null
                ? '—'
                : `${detail.fittingScore.toFixed(1)}%`
            }}
          </DescriptionsItem>
          <DescriptionsItem label="推荐 PID">{{
            fmtPid(detail.recommendedPid)
          }}</DescriptionsItem>
          <DescriptionsItem label="当前 PID">{{
            fmtPid(detail.currentPid)
          }}</DescriptionsItem>
          <DescriptionsItem label="可信度">{{
            detail.confidenceLevel ?? '—'
          }}</DescriptionsItem>
          <DescriptionsItem label="创建">
            {{ detail.createdBy ?? '—' }} · {{ detail.createdAt }}
          </DescriptionsItem>
        </Descriptions>

        <div class="mt-4 text-xs font-medium text-neutral-500">仿真快照</div>
        <EchartsUI
          v-if="simCandidates?.length"
          ref="chartRef"
          style="width: 100%; height: 260px"
        />
        <Empty
          v-else
          description="无仿真快照数据"
          :image="Empty.PRESENTED_IMAGE_SIMPLE"
        />
      </template>
    </Spin>
  </Drawer>
</template>
