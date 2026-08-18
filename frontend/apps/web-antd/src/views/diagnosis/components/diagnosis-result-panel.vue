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

import { computed, nextTick, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import dayjs from 'dayjs';

import {
  Alert,
  Badge,
  Collapse,
  CollapsePanel,
  Popover,
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
  TRIGGER_TYPE_COLOR,
  TRIGGER_TYPE_TEXT,
} from '../constants';

const props = withDefaults(
  defineProps<{
    detail: DiagnosisApi.RunDetail;
    /** 区段筛选：all=全部（默认，工作台/记录抽屉共用）；
     * conclusion=诊断结论；evidence=证据链；advice=处置建议（诊断详情弹窗三 Tab） */
    section?: 'advice' | 'all' | 'conclusion' | 'evidence';
  }>(),
  { section: 'all' },
);

const showConclusion = computed(
  () => props.section === 'all' || props.section === 'conclusion',
);
const showEvidence = computed(
  () => props.section === 'all' || props.section === 'evidence',
);
const showAdvice = computed(
  () => props.section === 'all' || props.section === 'advice',
);

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

/** 主分类置信度口径说明（详情 API confidenceDefinitions 动态返回） */
const primaryConfBasis = computed(() => {
  const defs = props.detail.confidenceDefinitions;
  const cat = props.detail.primaryCategory;
  if (!defs || !cat) return null;
  const lines = [
    `【${CATEGORY_META[cat]?.label ?? cat}】${defs.categories[cat] ?? ''}`,
    `【族内融合】${defs.fusion}`,
    `【次分类门槛】置信度 ≥${defs.secondaryGate} 才纳入次分类`,
  ];
  return lines.filter((l) => !l.endsWith('】'));
});

const activeKeys = ref<string[]>(['features', 'charts']);

/** 症状标签行：fusionResults 中 detected 的症状 */
const symptomRows = ref<Array<{ label: string; confidence: number }>>([]);
const SYMPTOM_LABELS: Record<string, string> = {
  OSCILLATION: '振荡',
  VALVE_STICTION: '阀门粘滞',
  OVERAGGRESSIVE: '响应过激',
  OVERCONSERVATIVE: '响应迟缓',
  EXTERNAL_DISTURBANCE: '外部扰动',
  QUALITY_ABNORMAL: '质量异常',
  LINK_ABNORMAL: '通信链路异常',
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

/** 断流段定位：质量码算子 bad_segments（窗口偏移秒）→ 本地钟点时段 */
const badSegments = computed(() => {
  const segs = (
    props.detail.operatorResults?.quality_code_rules
      ?.features as Record<string, any> | undefined
  )?.bad_segments;
  if (!Array.isArray(segs) || segs.length === 0) return [];
  // timeWindowStart 为 naive UTC ISO（无 Z 后缀）：直接 new Date 会被浏览器
  // 当本地时间解析，断流时段显示成 UTC 钟点数字（差 8 小时）；补 Z 后按
  // UTC 解析，dayjs 格式化时自动转本地时区
  const ws = props.detail.timeWindowStart;
  const base = ws
    ? new Date(
        /[Zz]|[+-]\d{2}:?\d{2}$/.test(ws) ? ws : `${ws}Z`,
      ).getTime()
    : null;
  return segs.map((s: any) => {
    const startS = Number(s.start_offset_s ?? 0);
    const endS = Number(s.end_offset_s ?? startS);
    const durS = Math.max(1, endS - startS);
    const fmt = (off: number) =>
      base == null ? '—' : dayjs(base + off * 1000).format('MM-DD HH:mm');
    return {
      points: s.points ?? 0,
      startText: fmt(startS),
      endText: fmt(endS),
      offsetText: `${Math.round(startS / 60)}~${Math.round(endS / 60)} min`,
      duration:
        durS < 60
          ? `${durS.toFixed(0)} 秒`
          : durS < 3600
            ? `${(durS / 60).toFixed(1)} 分钟`
            : `${(durS / 3600).toFixed(1)} 小时`,
    };
  });
});

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
      v-if="showConclusion && detail.primaryCategory"
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
            <template v-if="detail.triggerType">
              ·
              <span :style="{ color: TRIGGER_TYPE_COLOR[detail.triggerType] }">
                {{ detail.triggerTypeLabel ?? TRIGGER_TYPE_TEXT[detail.triggerType] }}
              </span>
            </template>
          </div>
        </div>
        <div class="text-right">
          <div class="flex items-center justify-end gap-1">
            <span class="text-lg font-semibold tabular-nums">
              {{ confPercent(detail.primaryConfidence) }}
            </span>
            <Popover v-if="primaryConfBasis" trigger="click" placement="leftTop">
              <template #content>
                <div class="max-w-320px space-y-1 text-xs leading-5">
                  <div
                    v-for="(line, i) in primaryConfBasis"
                    :key="i"
                    class="whitespace-pre-wrap"
                  >
                    {{ line }}
                  </div>
                </div>
              </template>
              <span
                class="flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-solid border-neutral-300 text-10px text-neutral-500 hover:border-blue-400 hover:text-blue-500"
              >
                ?
              </span>
            </Popover>
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
    <div v-if="showConclusion && (detail.secondaryCategories?.length || detail.pendingReview?.length)" class="space-y-1">
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
    <div v-if="showConclusion && symptomRows.length" class="flex flex-wrap items-center gap-2">
      <span class="text-xs text-neutral-500">症状证据</span>
      <Tag v-for="s in symptomRows" :key="s.label" color="default">
        {{ s.label }} · {{ confPercent(s.confidence) }}
      </Tag>
    </div>

    <!-- ③+ 数据质量行：门禁指标（诊断可信度前提）+ 断流时段定位 -->
    <div
      v-if="showEvidence && detail.dataGate"
      class="flex flex-wrap items-center gap-x-4 gap-y-1 rounded border border-solid border-neutral-200 bg-neutral-50 px-3 py-1.5 text-xs text-neutral-600 dark:border-neutral-700 dark:bg-neutral-800/60"
    >
      <span class="font-medium">数据质量 {{ detail.dataGate.confidenceLevel }} 级</span>
      <span>点数 {{ detail.dataGate.pointCount.toLocaleString() }}/{{ detail.dataGate.expectedPoints.toLocaleString() }}</span>
      <span>有效 {{ (detail.dataGate.validRate * 100).toFixed(1) }}%</span>
      <span>缺口 {{ (detail.dataGate.gapRatio * 100).toFixed(1) }}%</span>
      <template v-if="badSegments.length">
        <span class="font-medium text-red-500">断流时段</span>
        <Tooltip
          v-for="(seg, i) in badSegments"
          :key="i"
          :title="`窗口内偏移 ${seg.offsetText}；${seg.points} 点`"
        >
          <Tag color="red" :bordered="false">
            {{ seg.startText }}~{{ seg.endText }}（{{ seg.duration }}）
          </Tag>
        </Tooltip>
      </template>
    </div>

    <!-- ④ 处置建议区（R1-R5 排序由后端给出） -->
    <div v-if="showAdvice && detail.recommendations?.length">
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
    <Collapse v-if="showEvidence" v-model:active-key="activeKeys" class="diag-evidence">
      <CollapsePanel v-if="featureRows?.length" header="特征值" key="features">
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
      <CollapsePanel v-if="detail.evidenceCharts" header="波形快照" key="charts">
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
