<script lang="ts" setup>
/**
 * 驾驶舱总览 · 回路适用性 L0~L4 分布环形图（2026-10-10 修订：
 * 替换原预警事件流后右列新区块）
 *
 * - 数据：/workbench/diagnosis fitness_gates（latest-per-loop 聚合
 *   kpi_snapshot_hourly.fitness_level，与诊断概览/整定总览门禁同口径）；
 *   全厂口径，随时间窗联动（窗口影响其余块，等级分布为最新快照口径）
 * - 标签口径对齐 clpm/fitness-badge（L0 不可评估 / L1 仅可监视 /
 *   L2 条件异常 / L3 待激励 / L4 可优化）；色阶随档位升高趋好
 * - 中心=参评回路数 evaluated（total 中未参评部分以灰段呈现，不静默丢弃）
 * - ECharts 环形 + 图例带计数；纯展示无点击行为
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { getWorkbenchDiagnosisApi } from '#/api/workbench';
import { useCockpitStore } from '#/store/cockpit';

import { useCockpitTheme } from '../composables/use-cockpit-theme';

const cockpitStore = useCockpitStore();
const { chartColors, gradeColors, isLight } = useCockpitTheme();

const loading = ref(true);
const gates = ref<null | WorkbenchApi.DiagnosisFitnessGates>(null);

async function load() {
  loading.value = true;
  try {
    const res = await getWorkbenchDiagnosisApi({
      scopeType: 'GLOBAL',
      window: cockpitStore.timeWindow,
    });
    gates.value = res?.fitness_gates ?? null;
  } catch {
    gates.value = null;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => cockpitStore.timeWindow, load);

/** C5 混合刷新：由父级触发重拉 */
defineExpose({ reload: load });

/** L0~L4 标签（口径对齐 clpm/fitness-badge LEVEL_META） */
const LEVEL_META = [
  { key: 'L0', label: 'L0 不可评估' },
  { key: 'L1', label: 'L1 仅可监视' },
  { key: 'L2', label: 'L2 条件异常' },
  { key: 'L3', label: 'L3 待激励' },
  { key: 'L4', label: 'L4 可优化' },
] as const;

const counts = computed(() =>
  LEVEL_META.map((m) => ({
    ...m,
    value: gates.value?.level_counts?.[m.key] ?? 0,
  })),
);

/** 未参评回路（total−evaluated，恒 ≥0）以灰段呈现，不静默丢弃 */
const unevaluated = computed(() => {
  const g = gates.value;
  if (!g) return 0;
  return Math.max(0, (g.total ?? 0) - (g.evaluated ?? 0));
});

const evaluated = computed(() => gates.value?.evaluated ?? 0);

const hasData = computed(
  () => counts.value.some((c) => c.value > 0) || unevaluated.value > 0,
);

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

function buildOption() {
  const cc = chartColors.value;
  const gc = gradeColors.value;
  // 色阶随档位升高趋好（L0 红 → L4 绿），主题变量经 gradeColors 解析
  const levelColors = [gc.POOR, gc.WARNING, gc.FAIR, gc.GOOD, gc.EXCELLENT];
  const data: { itemStyle: { color: string }; name: string; value: number }[] =
    counts.value.map((c, i) => ({
      name: c.label,
      value: c.value,
      itemStyle: { color: c.value > 0 ? (levelColors[i] ?? 'transparent') : 'transparent' },
    }));
  if (unevaluated.value > 0) {
    data.push({
      name: '未参评',
      value: unevaluated.value,
      itemStyle: { color: cc.splitLine },
    });
  }
  return {
    animation: false,
    legend: {
      bottom: 0,
      icon: 'circle',
      itemHeight: 8,
      itemWidth: 8,
      textStyle: { color: cc.text, fontSize: 10 },
      formatter: (name: string) => {
        const item = data.find((d) => d.name === name);
        return `${name}  ${item?.value ?? 0}`;
      },
    },
    series: [
      {
        type: 'pie' as const,
        radius: ['46%', '68%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        label: { show: false },
        emphasis: { scale: false },
        data,
      },
    ],
    graphic: {
      elements: [
        {
          type: 'text',
          left: 'center',
          top: '40%',
          style: {
            text: String(evaluated.value),
            fontSize: 22,
            fontWeight: 700,
            fill: cc.textStrong,
            textAlign: 'center',
          },
          z: 100,
        },
        {
          type: 'text',
          left: 'center',
          top: '52%',
          style: {
            text: `参评 / 共 ${gates.value?.total ?? 0}`,
            fontSize: 10,
            fill: cc.text,
            textAlign: 'center',
          },
          z: 100,
        },
      ],
    },
    textStyle: { color: cc.textStrong },
    tooltip: {
      backgroundColor: cc.panel,
      borderColor: cc.splitLine,
      borderWidth: 1,
      textStyle: { color: cc.textStrong, fontSize: 12 },
      trigger: 'item' as const,
      formatter: '{b}<br/>{c} 条（{d}%）',
    },
  };
}

function refresh() {
  renderEcharts(buildOption());
}

watch([counts, loading, isLight], () => {
  if (!loading.value && hasData.value) refresh();
});
</script>

<template>
  <div class="cockpit-panel donut">
    <div class="cockpit-panel__hd">
      回路适用性分布
      <span class="sub">全厂 · L0~L4 最新快照口径</span>
    </div>
    <div class="donut__bd">
      <div v-if="loading" class="donut__state">加载中…</div>
      <div v-else-if="!hasData" class="donut__state">暂无适用性数据</div>
      <div v-show="!loading && hasData" class="donut__chart">
        <EchartsUI ref="chartRef" height="100%" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.donut__bd {
  position: relative;
  flex: 1;
  min-height: 0;
  padding: 4px 8px 4px;
}

.donut__chart {
  width: 100%;
  height: 100%;
}

.donut__state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  color: var(--ck-text-3);
}
</style>
