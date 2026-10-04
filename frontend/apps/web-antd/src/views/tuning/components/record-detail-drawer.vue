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
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getTuningTaskDetailApi } from '#/api/tuning';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

import { fmtNum2, tuningAlgoLabel } from '../constants';

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
  return `P ${fmtNum2(pid.kp)} / I ${fmtNum2(pid.ti)} / D ${fmtNum2(pid.td)}`;
}

/** 实施前 → 推荐 Δ（A4：参数变化一目了然；null 实施前基线缺失时整体不显示） */
const pidDelta = computed(() => {
  const cur = detail.value?.currentPid;
  const rec = detail.value?.recommendedPid;
  if (!cur || !rec) return null;
  const fmtDelta = (a: number, b: number) => {
    const d = b - a;
    if (Math.abs(d) < 0.005) return { text: '持平', changed: false };
    return { text: `${d > 0 ? '+' : ''}${fmtNum2(d)}`, changed: true };
  };
  return {
    kp: fmtDelta(cur.kp, rec.kp),
    ti: fmtDelta(cur.ti, rec.ti),
    td: fmtDelta(cur.td, rec.td),
  };
});

/** 风险等级展示色 */
const RISK_COLOR: Record<string, string> = {
  LOW: 'green',
  MEDIUM: 'orange',
  HIGH: 'red',
};

const paramsText = computed(() => {
  const p = detail.value?.modelParams;
  if (!p) return '—';
  return Object.entries(p)
    .filter(([, v]) => v != null)
    .map(([k, v]) =>
      typeof v === 'number' ? `${k}=${fmtNum2(v)}` : `${k}=${v}`,
    )
    .join('，');
});

/** 辨识时间窗（naive UTC 补 Z 转本地） */
const timeWindowText = computed(() => {
  const s = detail.value?.timeWindowStart;
  const e = detail.value?.timeWindowEnd;
  if (!s && !e) return null;
  const fix = (v?: null | string) =>
    v ? dayjs(/[Zz]|[+-]\d{2}:?\d{2}$/.test(v) ? v : `${v}Z`).format('MM-DD HH:mm') : '—';
  return `${fix(s)} ~ ${fix(e)}`;
});

/** A4：参数变化表（P/I/D 三行 × 前/后/Δ） */
const deltaColumns = [
  { key: 'param', title: '参数', width: 70 },
  { key: 'before', title: '实施前', align: 'center' as const },
  { key: 'after', title: '推荐', align: 'center' as const },
  { key: 'delta', title: 'Δ', align: 'center' as const, width: 110 },
];

const deltaRows = computed(() => {
  const d = pidDelta.value;
  const cur = detail.value?.currentPid;
  const rec = detail.value?.recommendedPid;
  if (!d || !cur || !rec) return [];
  return [
    {
      key: 'kp',
      param: 'P (Kp)',
      before: fmtNum2(cur.kp),
      after: fmtNum2(rec.kp),
      delta: d.kp,
    },
    {
      key: 'ti',
      param: 'I (Ti)',
      before: fmtNum2(cur.ti),
      after: fmtNum2(rec.ti),
      delta: d.ti,
    },
    {
      key: 'td',
      param: 'D (Td)',
      before: fmtNum2(cur.td),
      after: fmtNum2(rec.td),
      delta: d.td,
    },
  ];
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
          <DescriptionsItem label="实施前 PID">{{
            fmtPid(detail.currentPid)
          }}</DescriptionsItem>
          <DescriptionsItem label="可信度">{{
            detail.confidenceLevel ?? '—'
          }}</DescriptionsItem>
          <DescriptionsItem label="创建">
            {{ detail.createdBy ?? '—' }} · {{ detail.createdAt }}
          </DescriptionsItem>
          <DescriptionsItem v-if="timeWindowText" label="辨识时间窗" :span="2">
            {{ timeWindowText }}
          </DescriptionsItem>
        </Descriptions>

        <!-- A4：实施前 → 推荐参数变化表（Δ 高亮） -->
        <div
          v-if="pidDelta"
          class="mt-4 text-xs font-medium text-neutral-500"
        >
          参数变化（实施前 → 推荐）
        </div>
        <Table
          v-if="pidDelta"
          class="mt-1"
          size="small"
          :pagination="false"
          :columns="deltaColumns"
          :data-source="deltaRows"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'delta'">
              <span
                v-if="record.delta.changed"
                class="clpm-num font-medium"
                style="color: #1d4ed8"
              >
                {{ record.delta.text }}
              </span>
              <span v-else class="text-neutral-400">{{ record.delta.text }}</span>
            </template>
          </template>
        </Table>

        <!-- A4：回退方案 + 风险评估（人工实施清单） -->
        <div class="mt-4 grid grid-cols-1 gap-2">
          <div
            v-if="detail.rollbackPid"
            class="rollback-box"
          >
            <span class="rollback-box__label">回退方案</span>
            <span class="clpm-num">{{ fmtPid(detail.rollbackPid) }}</span>
            <span class="text-xs text-neutral-400">
              （实施无效时按此恢复参数）
            </span>
          </div>
          <div
            v-if="detail.riskAssessment?.riskLevel"
            class="risk-box"
          >
            <span class="risk-box__label">风险评估</span>
            <Tag
              :color="RISK_COLOR[detail.riskAssessment.riskLevel] ?? 'default'"
              class="mr-1"
            >
              {{ detail.riskAssessment.riskLevel }}
            </Tag>
            <span
              v-if="detail.riskAssessment.description"
              class="text-xs text-neutral-500"
            >
              {{ detail.riskAssessment.description }}
            </span>
            <div
              v-if="detail.riskAssessment.factors?.length"
              class="mt-1 text-xs text-neutral-400"
            >
              因子：{{ detail.riskAssessment.factors.join('、') }}
            </div>
          </div>
        </div>

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

<style scoped>
.rollback-box,
.risk-box {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  font-size: 12px;
  background: hsl(var(--accent) / 40%);
  border-radius: 4px;
}

.rollback-box__label,
.risk-box__label {
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}
</style>
