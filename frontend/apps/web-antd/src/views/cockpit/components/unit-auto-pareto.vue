<script lang="ts" setup>
/**
 * 驾驶舱总览 · 单元自控率柏拉图（2026-10-10 修订：替换原预警事件流）
 *
 * - 柱：各单元自控率%（/workbench/overview units[].metrics.auto_mode_rate，
 *   0~1 小数 ×100，按值降序——最优在前）
 * - 折线：累计手动回路占比%（按柱序累加 loopCount×(1-自控率) / 全部手动
 *   回路；自控率缺数据的单元不参与柱与折线，仅计入总数说明）
 *   —— 语义：自控率最低的单元贡献了大部分手动回路（治理主战场）
 * - 回路数取 /cockpit/node-tree 各 UNIT 节点 loopCount（活跃回路口径）
 * - ECharts 双 Y 轴（左=%率 0~100，右=累计占比 0~100），主题色经
 *   readCockpitColors 解析（深浅双主题）；纯展示无点击行为
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { getCockpitNodeTreeApi } from '#/api/cockpit';
import { getWorkbenchOverviewApi } from '#/api/workbench';
import { useCockpitStore } from '#/store/cockpit';

import { useCockpitTheme } from '../composables/use-cockpit-theme';

const cockpitStore = useCockpitStore();
const { chartColors, gradeColors, isLight } = useCockpitTheme();

const loading = ref(true);
const units = ref<{ auto: null | number; loopCount: null | number; name: string }[]>([]);

async function load() {
  loading.value = true;
  try {
    const [treeRes, ovRes] = await Promise.all([
      getCockpitNodeTreeApi(),
      getWorkbenchOverviewApi({
        scopeType: 'GLOBAL',
        window: cockpitStore.timeWindow,
      }),
    ]);
    const loopByName = new Map<string, number>();
    for (const factory of treeRes ?? []) {
      const walk = (nodes: typeof factory.children) => {
        for (const n of nodes ?? []) {
          if (n.type === 'UNIT') loopByName.set(n.name, n.loopCount);
          else if (n.children?.length) walk(n.children);
        }
      };
      walk(factory.children);
    }
    units.value = (ovRes?.units ?? []).map((u) => ({
      name: u.name,
      auto: u.metrics?.auto_mode_rate ?? null,
      loopCount: loopByName.get(u.name) ?? null,
    }));
  } catch {
    units.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => cockpitStore.timeWindow, load);

/** C5 混合刷新：由父级触发重拉 */
defineExpose({ reload: load });

interface BarRow {
  auto: number;
  manualLoops: number;
  name: string;
  totalLoops: number;
}

/** 参与柱/折线的行（自控率与回路数齐备；按自控率降序） */
const barRows = computed<BarRow[]>(() =>
  units.value
    .filter(
      (u): u is { auto: number; loopCount: number; name: string } =>
        u.auto !== null && u.loopCount !== null,
    )
    .map((u) => ({
      name: u.name,
      auto: Math.round(u.auto * 10_000) / 100,
      manualLoops: Math.max(0, Math.round(u.loopCount * (1 - u.auto))),
      totalLoops: u.loopCount,
    }))
    .toSorted((a, b) => b.auto - a.auto),
);

/** 折线：累计手动回路占比（0~100，1 位小数） */
const cumulative = computed(() => {
  const totalManual = barRows.value.reduce((s, r) => s + r.manualLoops, 0);
  let acc = 0;
  return barRows.value.map((r) => {
    acc += r.manualLoops;
    return totalManual > 0 ? Math.round((acc / totalManual) * 1000) / 10 : 0;
  });
});

const totalManual = computed(() =>
  barRows.value.reduce((s, r) => s + r.manualLoops, 0),
);

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const hasData = computed(() => barRows.value.length > 0);

function buildOption() {
  const cc = chartColors.value;
  const barColor = gradeColors.value.EXCELLENT;
  const lineColor = gradeColors.value.WARNING;
  return {
    animation: false,
    grid: { bottom: 28, left: 12, right: 12, top: 32, containLabel: true },
    legend: {
      icon: 'roundRect',
      itemHeight: 8,
      itemWidth: 12,
      textStyle: { color: cc.text, fontSize: 11 },
      top: 2,
    },
    series: [
      {
        name: '自控率',
        type: 'bar' as const,
        barMaxWidth: 22,
        yAxisIndex: 0,
        data: barRows.value.map((r) => r.auto),
        itemStyle: { color: barColor, borderRadius: [3, 3, 0, 0] },
      },
      {
        name: '累计手动回路占比',
        type: 'line' as const,
        yAxisIndex: 1,
        data: cumulative.value,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { color: lineColor, width: 1.5 },
        itemStyle: { color: lineColor },
        connectNulls: true,
      },
    ],
    textStyle: { color: cc.textStrong },
    tooltip: {
      backgroundColor: cc.panel,
      borderColor: cc.splitLine,
      borderWidth: 1,
      textStyle: { color: cc.textStrong, fontSize: 12 },
      trigger: 'axis' as const,
      formatter: (params: unknown) => {
        const list = params as { dataIndex: number }[];
        const i = list[0]?.dataIndex ?? 0;
        const r = barRows.value[i];
        if (!r) return '';
        const cum = cumulative.value[i];
        return [
          `<b>${r.name}</b>`,
          `回路 ${r.totalLoops} · 手动 ${r.manualLoops}`,
          `自控率 ${r.auto.toFixed(2)}%`,
          `累计手动占比 ${cum}%`,
        ].join('<br/>');
      },
    },
    xAxis: {
      type: 'category' as const,
      data: barRows.value.map((r) => r.name),
      axisLabel: {
        color: cc.text,
        fontSize: 10,
        interval: 0,
        hideOverlap: true,
        formatter: (v: string) => (v.length > 5 ? `${v.slice(0, 5)}…` : v),
      },
      axisLine: { lineStyle: { color: cc.splitLine } },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value' as const,
        name: '自控率%',
        nameTextStyle: { color: cc.text, fontSize: 10 },
        min: 0,
        max: 100,
        axisLabel: { color: cc.text, fontSize: 10 },
        splitLine: {
          lineStyle: { color: cc.splitLine, opacity: 0.6, type: 'dashed' as const },
        },
      },
      {
        type: 'value' as const,
        name: '累计%',
        nameTextStyle: { color: cc.text, fontSize: 10 },
        min: 0,
        max: 100,
        axisLabel: { color: cc.text, fontSize: 10, formatter: '{value}%' },
        splitLine: { show: false },
      },
    ],
  };
}

function refresh() {
  renderEcharts(buildOption());
}

watch([barRows, loading, isLight], () => {
  if (!loading.value && hasData.value) refresh();
});
</script>

<template>
  <div class="cockpit-panel pareto">
    <div class="cockpit-panel__hd">
      单元自控率
      <span class="sub">柱=自控率降序 · 折线=累计手动回路占比</span>
    </div>
    <div class="pareto__bd">
      <div v-if="loading" class="pareto__state">加载中…</div>
      <div v-else-if="!hasData" class="pareto__state">暂无单元自控率数据</div>
      <div v-show="!loading && hasData" class="pareto__chart">
        <EchartsUI ref="chartRef" height="100%" />
      </div>
    </div>
    <div v-if="hasData" class="pareto__ft">
      手动回路合计 <b>{{ totalManual }}</b> · 单元 <b>{{ barRows.length }}</b> 个
    </div>
  </div>
</template>

<style scoped>
.pareto__bd {
  position: relative;
  flex: 1;
  min-height: 0;
  padding: 4px 8px 4px;
}

.pareto__chart {
  width: 100%;
  height: 100%;
}

.pareto__state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  color: var(--ck-text-3);
}

.pareto__ft {
  display: flex;
  flex: none;
  gap: 4px;
  align-items: center;
  justify-content: flex-end;
  padding: 4px 12px 6px;
  font-size: 10px;
  color: var(--ck-text-3);
}

.pareto__ft b {
  color: var(--ck-text-2);
  font-variant-numeric: tabular-nums;
}
</style>
