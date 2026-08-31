<script setup lang="ts">
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

/**
 * 共性问题回路组对比抽屉（16 号文 F4 · D5=a：勾选 2~3 回路并排 6 指标雷达）。
 *
 * 链路：工作台 Tab3 Pareto 柱 / 装置堆叠条段点击 → 本抽屉（分类 × 装置
 * 回路组列表：最新结论/置信度/严重度/KPI 摘要/最近诊断时间）→ 勾选 2~3
 * 回路 → 并排雷达 + 指标对照表，判断共因还是各自独立问题。
 *
 * 雷达 6 指标（取最新 run metric_summary，v2.0 方案 F-DG-06 原口径）：
 * 评分 / 自控率 / 振荡率 / 粘滞指数 / 回复时间 / 好值率。
 * 各轴按组内最大值归一（0~100），顶点悬浮显示原始值。
 *
 * 交互（S4）：勾选超 3 个 → 提示分批对比；不足 2 个 → 占位提示。
 * 空态：该分类×装置下无回路 → 文字占位；回路无 metricSummary → 对照表 "—"。
 */
import { computed, nextTick, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Button, Checkbox, Drawer, Empty, message, Spin } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisCategoryCohortApi } from '#/api/diagnosis';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import { MULTI_SERIES_PALETTE } from '#/composables/use-loop-palettes';

import {
  CATEGORY_META,
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
  SEVERITY_TEXT,
} from '../constants';

const props = defineProps<{
  category: DiagnosisApi.Category | null;
  plantNodeId?: null | string;
  /** 装置/单元显示名（仅展示用） */
  plantNodeName?: null | string;
}>();
const open = defineModel<boolean>('open', { default: false });

const router = useRouter();

/** 同时对比回路上限（D5=a：2~3 回路，雷达可读性边界） */
const MAX_COMPARE = 3;

const loading = ref(false);
const items = ref<DiagnosisApi.CategoryCohortItem[]>([]);
const selectedIds = ref<string[]>([]);

const categoryMeta = computed(() =>
  props.category ? (CATEGORY_META[props.category] ?? null) : null,
);

async function load(): Promise<void> {
  if (!props.category) return;
  loading.value = true;
  selectedIds.value = [];
  try {
    const res = await getDiagnosisCategoryCohortApi(
      props.category,
      props.plantNodeId ?? undefined,
    );
    items.value = res.items;
    // 默认勾选前 2 个（回路组 ≥2 时直接呈现对比，少一次点击）
    selectedIds.value = res.items.slice(0, 2).map((i) => i.loopId);
  } catch {
    items.value = []; // 错误提示由请求拦截器统一弹出
  } finally {
    loading.value = false;
  }
}

watch(open, (v) => {
  if (v) load();
});

function toggleSelect(loopId: string): void {
  const idx = selectedIds.value.indexOf(loopId);
  if (idx !== -1) {
    selectedIds.value.splice(idx, 1);
  } else if (selectedIds.value.length >= MAX_COMPARE) {
    // S4：勾第 4 个被提示
    message.warning(`最多同时对比 ${MAX_COMPARE} 个回路，超出请分批对比`);
  } else {
    selectedIds.value.push(loopId);
  }
}

const selectedLoops = computed(() =>
  selectedIds.value.flatMap((id) => {
    const item = items.value.find((i) => i.loopId === id);
    return item ? [item] : [];
  }),
);

/** 回路 → 雷达序列色（多序列分类色板，whitelisted 色板文件共享） */
function loopColor(loopId: string): string {
  const idx = selectedIds.value.indexOf(loopId);
  return (
    MULTI_SERIES_PALETTE[Math.max(idx, 0)] ?? MULTI_SERIES_PALETTE[0]!
  );
}

/** 下钻诊断记录页（13 号矩阵深链参数规范：records?category=） */
function drillRecords(): void {
  if (!props.category) return;
  router.push({
    path: '/diagnosis/records',
    query: { category: props.category },
  });
}

// ===== 时间工具（naive UTC → 本地） =====
function fmtUtc(naiveIso?: null | string): string {
  if (!naiveIso) return '—';
  const withZ = /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveIso)
    ? naiveIso
    : `${naiveIso}Z`;
  return dayjs(withZ).format('MM-DD HH:mm');
}

// ===== 雷达 6 指标 =====

interface RadarAxis {
  group: 'negative' | 'positive';
  key: string;
  label: string;
  unit: string;
}

const RADAR_AXES: RadarAxis[] = [
  { group: 'positive', key: 'score', label: '评分', unit: '' },
  { group: 'positive', key: 'autoModeRate', label: '自控率', unit: '%' },
  { group: 'negative', key: 'oscillationRate', label: '振荡率', unit: '%' },
  { group: 'negative', key: 'stictionIndex', label: '粘滞指数', unit: '%' },
  { group: 'negative', key: 'settlingTime', label: '回复时间', unit: 's' },
  { group: 'positive', key: 'goodValueRate', label: '好值率', unit: '%' },
];

function rawMetric(
  item: DiagnosisApi.CategoryCohortItem,
  axis: RadarAxis,
): null | number {
  const group = item.metricSummary?.[axis.group] as
    | Record<string, null | number | undefined>
    | undefined;
  const v = group?.[axis.key];
  return typeof v === 'number' && !Number.isNaN(v) ? v : null;
}

/** 每轴组内最大值（归一基准；全空/全 0 → 1 避免除零） */
const axisMax = computed<number[]>(() =>
  RADAR_AXES.map((axis) => {
    const vals = selectedLoops.value
      .map((l) => rawMetric(l, axis))
      .filter((v): v is number => v !== null && v > 0);
    return vals.length > 0 ? Math.max(...vals) : 1;
  }),
);

function fmtMetric(v: null | number, unit: string): string {
  if (v === null) return '—';
  const digits = Math.abs(v) < 10 ? 1 : 0;
  return `${v.toFixed(digits)}${unit}`;
}

// ===== ECharts 雷达 =====

const { chartTextColor, chartSplitLineColor } = useClpmTheme();
const { getEchartsBase } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const canCompare = computed(() => selectedLoops.value.length >= 2);

function buildOption() {
  const loops = selectedLoops.value;
  return {
    ...getEchartsBase(),
    legend: {
      bottom: 0,
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: chartTextColor.value, fontSize: 11 },
    },
    tooltip: {
      trigger: 'item' as const,
      // radar 单 series 多 data：dataIndex 区分回路，悬浮显示原始值
      // （formatter 签名与 ECharts TooltipOption 对齐，沿用域内 any 惯例）
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params;
        const loop = p?.dataIndex == null ? undefined : loops[p.dataIndex];
        if (!loop) return '';
        const lines = RADAR_AXES.map(
          (axis) =>
            `${axis.label}：${fmtMetric(rawMetric(loop, axis), axis.unit)}`,
        );
        return `${loop.loopTagName}<br/>${lines.join('<br/>')}`;
      },
    },
    radar: {
      indicator: RADAR_AXES.map((a) => ({ max: 100, min: 0, name: a.label })),
      center: ['50%', '46%'],
      radius: '62%',
      shape: 'polygon' as const,
      splitNumber: 4,
      axisName: { color: chartTextColor.value, fontSize: 11 },
      splitLine: { lineStyle: { color: chartSplitLineColor.value } },
      splitArea: { show: false },
      axisLine: { lineStyle: { color: chartSplitLineColor.value } },
    },
    series: [
      {
        type: 'radar' as const,
        symbol: 'circle',
        symbolSize: 3,
        data: loops.map((loop) => {
          const color = loopColor(loop.loopId);
          return {
            name: loop.loopTagName,
            value: RADAR_AXES.map((axis, i) => {
              const raw = rawMetric(loop, axis);
              return raw === null ? 0 : Math.round((raw / axisMax.value[i]!) * 100);
            }),
            lineStyle: { color, width: 1.5 },
            itemStyle: { color },
            areaStyle: { color, opacity: 0.08 },
          };
        }),
      },
    ],
  };
}

function refreshChart(): void {
  if (!canCompare.value) return;
  renderEcharts(buildOption());
}

watch(
  [selectedLoops, open],
  async () => {
    if (!open.value) return;
    await nextTick();
    refreshChart();
  },
  { deep: true, flush: 'post' },
);

/** 对照表行（6 指标 × 勾选回路，原始值） */
const tableRows = computed(() =>
  RADAR_AXES.map((axis) => ({
    axis,
    values: selectedLoops.value.map((l) => rawMetric(l, axis)),
  })),
);
</script>

<template>
  <Drawer
    v-model:open="open"
    width="72%"
    :destroy-on-close="true"
  >
    <template #title>
      <span :style="{ color: categoryMeta?.color }">
        共性对比 · {{ categoryMeta?.label ?? category ?? '' }}
      </span>
      <span v-if="plantNodeName" class="cohort-title__scope">
        {{ plantNodeName }}
      </span>
      <span class="cohort-title__count">{{ items.length }} 个回路</span>
    </template>
    <template #extra>
      <Button size="small" type="link" @click="drillRecords">
        查看诊断记录 →
      </Button>
    </template>

    <Spin :spinning="loading">
      <Empty
        v-if="!loading && items.length === 0"
        :image="Empty.PRESENTED_IMAGE_SIMPLE"
        description="该分类 × 装置范围内暂无匹配回路（最新结论非本分类或未诊断）"
      />

      <template v-else>
        <!-- ① 回路组列表（勾选 2~3 回路对比） -->
        <div class="cohort-sec-title">
          回路组（勾选 2~{{ MAX_COMPARE }} 个回路并排对比）
        </div>
        <div class="cohort-list">
          <div
            v-for="item in items"
            :key="item.loopId"
            class="cohort-row"
            :class="{
              'cohort-row--active': selectedIds.includes(item.loopId),
            }"
            @click="toggleSelect(item.loopId)"
          >
            <Checkbox
              :checked="selectedIds.includes(item.loopId)"
              @click.prevent="toggleSelect(item.loopId)"
            />
            <span
              class="cohort-row__tag"
              :style="{
                color: selectedIds.includes(item.loopId)
                  ? loopColor(item.loopId)
                  : undefined,
              }"
              :title="item.loopDescription ?? ''"
            >
              {{ item.loopTagName }}
            </span>
            <span
              v-if="item.importanceLevel"
              class="cohort-row__level"
              :style="{ color: IMPORTANCE_LEVEL_COLOR[item.importanceLevel] }"
            >
              {{ IMPORTANCE_LEVEL_TEXT[item.importanceLevel] }}
            </span>
            <span class="cohort-row__conf tabular-nums">
              置信度
              {{
                item.primaryConfidence == null
                  ? '—'
                  : `${Math.round(item.primaryConfidence * 100)}%`
              }}
            </span>
            <span class="cohort-row__sev">
              {{ item.severity ? (SEVERITY_TEXT[item.severity] ?? item.severity) : '—' }}
            </span>
            <span class="cohort-row__kpi tabular-nums">
              评分
              {{
                fmtMetric(
                  rawMetric(
                    item,
                    RADAR_AXES[0]!,
                  ),
                  '',
                )
              }}
            </span>
            <span class="cohort-row__time tabular-nums">
              {{ fmtUtc(item.lastDiagnosedAt) }}
            </span>
          </div>
        </div>

        <!-- ② 并排雷达对比（≥2 勾选时渲染） -->
        <template v-if="canCompare">
          <div class="cohort-sec-title">
            指标雷达对比（各轴按组内最大值归一，悬浮查看原始值）
          </div>
          <div class="cohort-radar">
            <EchartsUI ref="chartRef" height="300px" />
          </div>

          <!-- ③ 关键指标对照表（原始值） -->
          <div class="cohort-sec-title">关键指标对照（原始值）</div>
          <table class="cohort-table">
            <thead>
              <tr>
                <th>指标</th>
                <th
                  v-for="l in selectedLoops"
                  :key="l.loopId"
                  :style="{ color: loopColor(l.loopId) }"
                >
                  {{ l.loopTagName }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in tableRows" :key="row.axis.key">
                <td class="cohort-table__axis">
                  {{ row.axis.label }}{{ row.axis.unit ? `（${row.axis.unit}）` : '' }}
                </td>
                <td
                  v-for="(v, i) in row.values"
                  :key="i"
                  class="tabular-nums"
                >
                  {{ fmtMetric(v, '') }}
                </td>
              </tr>
            </tbody>
          </table>
        </template>
        <div v-else class="cohort-compare-hint">
          再勾选 {{ 2 - selectedLoops.length }} 个回路开始并排对比
        </div>
      </template>
    </Spin>
  </Drawer>
</template>

<style scoped>
.cohort-title__scope {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: hsl(var(--muted-foreground));
}

.cohort-title__count {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: hsl(var(--muted-foreground));
}

.cohort-sec-title {
  margin: 12px 0 6px;
  font-size: 12px;
  font-weight: 600;
}

.cohort-sec-title:first-child {
  margin-top: 0;
}

.cohort-list {
  display: flex;
  flex-direction: column;
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.cohort-row {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
  border-bottom: 1px solid hsl(var(--border));
}

.cohort-row:last-child {
  border-bottom: none;
}

.cohort-row:hover {
  background: hsl(var(--accent) / 40%);
}

.cohort-row--active {
  background: hsl(var(--accent) / 55%);
}

.cohort-row__tag {
  min-width: 110px;
  font-weight: 500;
}

.cohort-row__level {
  flex-shrink: 0;
  font-size: 11px;
}

.cohort-row__conf {
  color: hsl(var(--muted-foreground));
}

.cohort-row__sev {
  color: hsl(var(--muted-foreground));
}

.cohort-row__kpi {
  color: hsl(var(--muted-foreground));
}

.cohort-row__time {
  margin-left: auto;
  color: hsl(var(--muted-foreground));
}

.cohort-radar {
  position: relative;
  height: 300px;
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.cohort-table {
  width: 100%;
  font-size: 12px;
  border-collapse: collapse;
}

.cohort-table th,
.cohort-table td {
  padding: 4px 10px;
  text-align: left;
  border-bottom: 1px solid hsl(var(--border));
}

.cohort-table__axis {
  color: hsl(var(--muted-foreground));
}

.cohort-compare-hint {
  padding: 18px 0;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  text-align: center;
}
</style>
