<script setup lang="ts">
/**
 * 诊断结果面板 —— 工作台与记录抽屉共用的唯一结果渲染组件。
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2
 * 分层（结论先行）：① 分类定性卡（主分类+置信度+严重度+处置方向）
 * ② 并存/待复核 chips ③ 症状标签行 ④ 证据链折叠区（特征表+波形快照）
 * ⑤ 处置建议区（后端已按 R1-R5 排序）。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import { nextTick, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Badge,
  Collapse,
  CollapsePanel,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import type { DiagnosisApi } from '#/api/diagnosis';

import { useClpmTheme } from '#/composables/use-clpm-theme';

import {
  CATEGORY_META,
  SEVERITY_COLOR,
  SEVERITY_TEXT,
} from '../constants';

const props = defineProps<{
  detail: DiagnosisApi.RunDetail;
}>();

const { isDark } = useClpmTheme();

const severityText = SEVERITY_TEXT;
const severityColor = SEVERITY_COLOR;

function metaOf(category?: null | DiagnosisApi.Category) {
  if (!category) return CATEGORY_META.DATA_INSUFFICIENT;
  return CATEGORY_META[category] ?? CATEGORY_META.DATA_INSUFFICIENT;
}

function confPercent(conf?: null | number) {
  if (conf == null) return '—';
  return `${Math.round(conf * 100)}%`;
}

const activeKeys = ref<string[]>([]);

/** 症状标签行：fusionResults 中 detected 的症状 */
const symptomRows = ref<Array<{ label: string; confidence: number }>>([]);
const SYMPTOM_LABELS: Record<string, string> = {
  OSCILLATION: '振荡',
  VALVE_STICTION: '阀门粘滞',
  OVERAGGRESSIVE: '响应过激',
  OVERCONSERVATIVE: '响应迟缓',
  EXTERNAL_DISTURBANCE: '外部扰动',
  QUALITY_ABNORMAL: '质量异常',
  OUTPUT_SATURATION: '输出饱和',
};
watch(
  () => props.detail,
  (d) => {
    symptomRows.value = Object.entries(d.fusionResults ?? {})
      .filter(([, f]) => f.detected)
      .map(([tag, f]) => ({
        label: SYMPTOM_LABELS[tag] ?? tag,
        confidence: f.confidence,
      }));
  },
  { immediate: true },
);

/** 证据链：全部算子的特征值行 */
const featureRows = ref<
  Array<{
    key: string;
    operator: string;
    feature: string;
    judgment: string;
    threshold: string;
    value: string;
  }>
>();
watch(
  () => props.detail,
  (d) => {
    const rows: Array<{
      key: string;
      operator: string;
      feature: string;
      judgment: string;
      threshold: string;
      value: string;
    }> = [];
    for (const [name, op] of Object.entries(d.operatorResults ?? {})) {
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
  },
  { immediate: true },
);

/** 图表：展开时才渲染；暗色切换时重渲 */
const trendChartRef = ref<EchartsUIType>();
const scatterChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);
const { renderEcharts: renderScatter } = useEcharts(scatterChartRef);

function buildTrendOption() {
  const chart = props.detail.evidenceCharts?.trend;
  const ts = chart?.ts ?? [];
  const toPoints = (arr?: (null | number)[]) =>
    (arr ?? []).map((v, i) => [ts[i], v ?? null]);
  return {
    animation: false,
    color: ['#1d4ed8', '#6b7280', '#b45309'],
    grid: { bottom: 48, left: 56, right: 16, top: 32 },
    legend: { data: ['PV', 'SP', 'OP'], top: 0 },
    series: [
      { connectNulls: false, data: toPoints(chart?.pv), name: 'PV', showSymbol: false, type: 'line' },
      {
        lineStyle: { type: 'dashed' },
        data: toPoints(chart?.sp),
        name: 'SP',
        showSymbol: false,
        type: 'line',
      },
      { data: toPoints(chart?.op), name: 'OP', showSymbol: false, type: 'line' },
    ],
    tooltip: { trigger: 'axis' },
    xAxis: { axisLabel: { formatter: (v: number) => `${Math.round(v / 60000)}m` }, type: 'time' },
    yAxis: { scale: true, type: 'value' },
  };
}

function buildScatterOption() {
  const chart = props.detail.evidenceCharts?.scatter;
  return {
    animation: false,
    color: ['#b45309'],
    grid: { bottom: 40, left: 56, right: 24, top: 24 },
    series: [
      {
        itemStyle: { opacity: 0.35 },
        data: (chart?.pv ?? []).map((pv, i) => [chart?.op?.[i], pv]),
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
  if (!props.detail.evidenceCharts) return;
  renderTrend(buildTrendOption() as any);
  renderScatter(buildScatterOption() as any);
}

watch(activeKeys, (keys) => {
  if (keys.includes('charts')) {
    nextTick(renderCharts);
  }
});
watch(isDark, () => {
  if (activeKeys.value.includes('charts')) {
    nextTick(renderCharts);
  }
});
</script>

<template>
  <div class="diag-result-panel space-y-4">
    <!-- ① 分类定性卡 -->
    <div
      v-if="detail.primaryCategory"
      class="rounded-lg border p-4"
      :style="{
        borderColor: `${metaOf(detail.primaryCategory).color}55`,
        background: `${metaOf(detail.primaryCategory).color}0d`,
      }"
    >
      <div class="flex items-center gap-3">
        <span
          class="inline-block h-8 w-1.5 rounded-full"
          :style="{ background: metaOf(detail.primaryCategory).color }"
        />
        <div class="flex-1">
          <div class="text-lg font-semibold" :style="{ color: metaOf(detail.primaryCategory).color }">
            {{ detail.primaryCategoryLabel ?? metaOf(detail.primaryCategory).label }}
          </div>
          <div class="text-xs text-neutral-500">
            处置方向：{{ metaOf(detail.primaryCategory).direction }} · 发起人
            {{ detail.triggeredBy }}
          </div>
        </div>
        <div class="text-right">
          <div class="text-lg font-semibold tabular-nums">
            {{ confPercent(detail.primaryConfidence) }}
          </div>
          <div class="text-xs text-neutral-500">置信度</div>
        </div>
        <Badge
          v-if="detail.severity"
          :color="severityColor[detail.severity]"
          :text="`严重度 ${severityText[detail.severity] ?? detail.severity}`"
        />
      </div>
      <Alert
        v-if="detail.primaryCategory === 'DATA_INSUFFICIENT' && detail.dataGate?.reason"
        :message="detail.dataGate.reason"
        class="mt-3"
        show-icon
        type="warning"
      />
    </div>

    <!-- ② 并存 / 待复核 chips -->
    <div v-if="detail.secondaryCategories?.length || detail.pendingReview?.length" class="space-y-1">
      <div v-if="detail.secondaryCategories?.length" class="flex flex-wrap items-center gap-2">
        <span class="text-xs text-neutral-500">并存问题</span>
        <Tooltip v-for="j in detail.secondaryCategories" :key="j.category" :title="j.basis?.join('；')">
          <Tag :color="metaOf(j.category).color">
            {{ j.categoryLabel }} {{ confPercent(j.confidence) }}
          </Tag>
        </Tooltip>
      </div>
      <div v-if="detail.pendingReview?.length" class="flex flex-wrap items-center gap-2">
        <span class="text-xs text-neutral-500">待复核</span>
        <Tooltip
          v-for="j in detail.pendingReview"
          :key="j.category"
          :title="j.contaminationNote ?? j.basis?.join('；')"
        >
          <Tag :bordered="false" :color="metaOf(j.category).color" class="diag-pending-tag">
            ⚠ {{ j.categoryLabel }} {{ confPercent(j.confidence) }}（需复诊）
          </Tag>
        </Tooltip>
      </div>
    </div>

    <!-- ③ 症状标签行 -->
    <div v-if="symptomRows.length" class="flex flex-wrap items-center gap-2">
      <span class="text-xs text-neutral-500">症状证据</span>
      <Tag v-for="s in symptomRows" :key="s.label" color="default">
        {{ s.label }} · {{ confPercent(s.confidence) }}
      </Tag>
    </div>

    <!-- ④ 处置建议区（R1-R5 排序由后端给出） -->
    <div v-if="detail.recommendations?.length">
      <div class="mb-2 text-sm font-medium">处置建议</div>
      <ol class="space-y-2">
        <li
          v-for="(rec, i) in detail.recommendations"
          :key="i"
          class="rounded border border-solid border-neutral-200 bg-neutral-50 px-3 py-2 dark:border-neutral-700 dark:bg-neutral-800/60"
        >
          <div class="flex items-start gap-2">
            <Tag :color="i === 0 ? 'blue' : 'default'" class="mt-0.5 shrink-0">
              {{ i === 0 ? '优先' : `建议 ${i + 1}` }}
            </Tag>
            <div>
              <div class="text-sm">{{ rec.content }}</div>
              <div class="mt-0.5 text-xs text-neutral-500">依据：{{ rec.basis }}</div>
            </div>
          </div>
        </li>
      </ol>
    </div>

    <!-- ⑤ 证据链折叠区 -->
    <Collapse v-model:active-key="activeKeys" class="diag-evidence">
      <CollapsePanel v-if="featureRows?.length" header="证据链 · 特征值" key="features">
        <Table
          :columns="[
            { title: '算子', dataIndex: 'operator', width: 180 },
            { title: '特征', dataIndex: 'feature', width: 160 },
            { title: '实测值', dataIndex: 'value', width: 110 },
            { title: '阈值', dataIndex: 'threshold', width: 110 },
            { title: '判定', dataIndex: 'judgment' },
          ]"
          :data-source="featureRows"
          :pagination="false"
          row-key="key"
          size="small"
        />
      </CollapsePanel>
      <CollapsePanel v-if="detail.evidenceCharts" header="证据链 · 波形快照" key="charts">
        <div class="space-y-3">
          <div>
            <div class="mb-1 text-xs text-neutral-500">PV/SP/OP 趋势（诊断时间窗）</div>
            <EchartsUI ref="trendChartRef" height="260px" />
          </div>
          <div>
            <div class="mb-1 text-xs text-neutral-500">PV-OP 散点（回环/粘滞形态）</div>
            <EchartsUI ref="scatterChartRef" height="240px" />
          </div>
        </div>
      </CollapsePanel>
    </Collapse>
  </div>
</template>

<style scoped>
.diag-pending-tag {
  border-style: dashed;
  opacity: 0.85;
}
</style>
