<script setup lang="ts">
/**
 * 诊断证据抽屉 —— 当前诊断的证据链（算子特征值表 + 波形快照图）。
 *
 * 概览列表"证据"操作专用（2026-08-18）：轻量呈现，不含结论/建议区。
 * 证据已按保留策略清理（operatorResults 为空）时显示占位提示。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import { nextTick, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Drawer, Empty, Table } from 'ant-design-vue';

import type { DiagnosisApi } from '#/api/diagnosis';
import { getDiagnosisRunDetailApi } from '#/api/diagnosis';

const props = defineProps<{
  runId: null | string;
}>();

const open = defineModel<boolean>('open', { default: false });

const loading = ref(false);
const detail = ref<DiagnosisApi.RunDetail | null>(null);

const featureColumns = [
  { dataIndex: 'operator', title: '算子', width: 150 },
  { dataIndex: 'feature', title: '特征值', width: 160 },
  { dataIndex: 'value', title: '实测', width: 100 },
  { dataIndex: 'threshold', title: '阈值', width: 100 },
  { dataIndex: 'judgment', title: '判定', width: 90 },
];

/** 证据是否已按保留策略清理（>1 月仅留最新 1 条） */
const evidenceExpired = ref(false);

async function load(runId: string) {
  loading.value = true;
  detail.value = null;
  try {
    detail.value = await getDiagnosisRunDetailApi(runId);
    evidenceExpired.value =
      !detail.value?.operatorResults && !detail.value?.evidenceCharts;
  } finally {
    loading.value = false;
    nextTick(renderCharts);
  }
}

watch(open, (v) => {
  if (v && props.runId) {
    load(props.runId);
  }
});

/** 特征值行：全部算子的 evidence 摊平 */
const featureRows = ref<Array<Record<string, string>>>([]);
watch(detail, (d) => {
  const rows: Array<Record<string, string>> = [];
  for (const [name, op] of Object.entries(d?.operatorResults ?? {})) {
    for (const ev of op.evidence ?? []) {
      rows.push({
        key: `${name}-${ev.feature}`,
        operator: name,
        feature: ev.feature,
        value: String(ev.value ?? '—'),
        threshold: ev.threshold == null ? '—' : String(ev.threshold),
        judgment: ev.judgment || '—',
      });
    }
  }
  featureRows.value = rows;
});

const trendChartRef = ref<EchartsUIType>();
const scatterChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);
const { renderEcharts: renderScatter } = useEcharts(scatterChartRef);

function buildTrendOption() {
  const chart = detail.value?.evidenceCharts?.trend;
  const ts = chart?.ts ?? [];
  const toPoints = (arr?: (null | number)[]) =>
    (arr ?? []).map((v, i) => [ts[i]!, v ?? null]);
  return {
    animation: false,
    color: ['#1d4ed8', '#6b7280', '#b45309'],
    grid: { bottom: 48, left: 56, right: 16, top: 32 },
    legend: { data: ['PV', 'SP', 'OP'], top: 0 },
    series: [
      {
        connectNulls: false,
        data: toPoints(chart?.pv),
        name: 'PV',
        showSymbol: false,
        type: 'line',
      },
      {
        data: toPoints(chart?.sp),
        lineStyle: { type: 'dashed' },
        name: 'SP',
        showSymbol: false,
        type: 'line',
      },
      { data: toPoints(chart?.op), name: 'OP', showSymbol: false, type: 'line' },
    ],
    tooltip: { trigger: 'axis' },
    xAxis: {
      axisLabel: { formatter: (v: number) => `${Math.round(v / 60000)}m` },
      type: 'time',
    },
    yAxis: { scale: true, type: 'value' },
  };
}

function buildScatterOption() {
  const chart = detail.value?.evidenceCharts?.scatter;
  return {
    animation: false,
    color: ['#b45309'],
    grid: { bottom: 40, left: 56, right: 24, top: 24 },
    series: [
      {
        data: (chart?.pv ?? []).map((pv, i) => [chart?.op?.[i], pv]),
        itemStyle: { opacity: 0.35 },
        name: 'PV-OP',
        symbolSize: 3,
        type: 'scatter',
      },
    ],
    tooltip: { trigger: 'item' },
    xAxis: { name: 'OP', scale: true, type: 'value' },
    yAxis: { name: 'PV', scale: true, type: 'value' },
  };
}

function renderCharts() {
  if (!detail.value?.evidenceCharts) return;
  renderTrend(buildTrendOption() as any);
  renderScatter(buildScatterOption() as any);
}
</script>

<template>
  <Drawer
    v-model:open="open"
    :title="`诊断证据 · ${detail?.loopTagName ?? ''}`"
    width="680"
    :destroy-on-close="true"
  >
    <div v-if="loading" class="py-8 text-center text-neutral-400">证据加载中...</div>
    <template v-else-if="detail">
      <!-- 证据已按保留策略清理 -->
      <Empty
        v-if="evidenceExpired"
        description="该记录超过证据保留期（1 个月），证据已按保留策略清理；结论字段仍完整保留"
      />
      <template v-else>
        <div class="mb-2 text-xs font-medium text-neutral-500">
          证据链 · 特征值（{{ featureRows.length }} 项）
        </div>
        <Table
          v-if="featureRows.length > 0"
          :columns="featureColumns"
          :data-source="featureRows"
          :pagination="false"
          row-key="key"
          size="small"
          :scroll="{ y: 260 }"
        />
        <Empty v-else description="无特征值数据" />

        <template v-if="detail.evidenceCharts">
          <div class="mt-4 mb-2 text-xs font-medium text-neutral-500">
            证据链 · 波形快照（诊断时间窗 PV/SP/OP）
          </div>
          <EchartsUI ref="trendChartRef" height="240px" />
          <div class="mt-4 mb-2 text-xs font-medium text-neutral-500">
            PV-OP 相图（阀门特性）
          </div>
          <EchartsUI ref="scatterChartRef" height="220px" />
        </template>
      </template>
    </template>
    <Empty v-else description="无证据数据" />
  </Drawer>
</template>
