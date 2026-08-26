<script lang="ts" setup>
/**
 * 整定效果验证 · 前后评分散点 Δ 图（ECharts 标准宽幅散点图）
 *
 * 2026-08-26 重构（原图 SVG + 右侧 4 项统计）：
 * 1. 渲染：改用 @vben/plugins/echarts 标准宽幅散点图
 *    - 坐标域 [55,95]×[55,95]（同原 SVG 域），刻度 60/70/80/90
 *    - 系列：改善点（绿 Δ≥0）/ 回退点（红 Δ<0）/ 无改善线（灰色对角虚线）
 *    - 图例统一放在标题栏下或右上角，原右侧"改善最大/中位/失败回退/验证口径"删除
 * 2. 数据：props.points 或主动调 A-13 getWorkbenchTuningScattersApi(params)
 *    - 无数据 → Empty 空态；严格禁止前端 mock 假点兜底（对齐 §100021095：
 *      真实数据时用空结构 + 可展示信息替换模拟数据兜底，让问题暴露）
 *
 * 对接链路：父 tuning.vue → A-04 /workbench/tuning → scatters props 传入；
 *            props.batchId 存在时也走 A-13 刷新（支持将来批次过滤）
 */
import type { EchartsUIType, ECOption } from '@vben/plugins/echarts';

import type { WorkbenchApi } from '#/api/workbench';

import { computed, nextTick, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Empty, Spin } from 'ant-design-vue';

import { getWorkbenchTuningScattersApi } from '#/api/workbench';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

interface Props {
  /** 散点数据（父级 A-04 聚合一次下传，优先使用） */
  points?: WorkbenchApi.TuningScatterPoint[];
  /** A-13 过滤：batch_id（支持点击批次卡片后重拉），不传则不主动二次请求 */
  batchId?: null | number;
  /** A-13 过滤：scope 参数（装置/单元/全厂），不传不主动请求 */
  scopeParams?: WorkbenchApi.ScopeParams;
}

const props = withDefaults(defineProps<Props>(), {
  batchId: null,
  points: () => [],
  scopeParams: () => ({}),
});

const { chartTextColor, chartSplitLineColor } = useClpmTheme();
const { getTooltipPreset } = useEchartsPreset();

const OK = '#52C41A';
const CRIT = '#FF4D4F';
const NO_GAIN = '#94A3B8';
const MN = 55;
const MX = 95;

const loading = ref(false);
const loadError = ref<null | string>(null);
/** 最终生效数据（不兜底 mock） */
const resolved = ref<WorkbenchApi.TuningScatterPoint[]>([]);

const chartRef = ref<EchartsUIType>();
const { renderEcharts, resize } = useEcharts(chartRef);

/** 点坐标域约束（只显示 [MN,MX] 方形），超出不显示而非截断改写原值 */
const visible = computed(() =>
  (resolved.value ?? []).filter(
    (p) =>
      Number.isFinite(p.before) &&
      Number.isFinite(p.after) &&
      p.before >= MN &&
      p.before <= MX &&
      p.after >= MN &&
      p.after <= MX,
  ),
);

const totalCount = computed(() => visible.value.length);

/** A-13 主动请求（仅当 batchId / scopeParams 显式传入；否则只消费 props.points） */
async function fetchScatters() {
  if (!props.batchId && Object.keys(props.scopeParams ?? {}).length === 0) {
    resolved.value = props.points ?? [];
    buildAndRender();
    return;
  }
  loading.value = true;
  loadError.value = null;
  try {
    const res = await getWorkbenchTuningScattersApi({
      ...props.scopeParams,
      batchId: props.batchId ?? undefined,
    });
    resolved.value = res?.points ?? [];
  } catch (error: unknown) {
    loadError.value =
      error instanceof Error ? error.message : '整定散点数据加载失败';
    resolved.value = [];
  } finally {
    loading.value = false;
    buildAndRender();
  }
}

function buildOption(): ECOption {
  const good: Array<[number, number, WorkbenchApi.TuningScatterPoint]> = [];
  const bad: Array<[number, number, WorkbenchApi.TuningScatterPoint]> = [];
  for (const p of visible.value) {
    const row: [number, number, WorkbenchApi.TuningScatterPoint] = [
      p.before,
      p.after,
      p,
    ];
    if (p.delta >= 0) good.push(row);
    else bad.push(row);
  }
  // 无改善线：对角 markLine（y=x），图例 label 独立
  const minBound = MN;
  const maxBound = MX;

  return {
    animation: false,
    grid: { bottom: 44, left: 48, right: 20, top: 44 },
    legend: {
      icon: 'roundRect',
      itemHeight: 8,
      right: 8,
      top: 8,
      textStyle: { color: chartTextColor.value, fontSize: 11 },
      data: ['改善点', '回退点', '无改善线'],
    },
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'item',
      formatter: (params: any) => {
        const raw = params?.data?.[2] as
          | undefined
          | WorkbenchApi.TuningScatterPoint;
        if (!raw) return '';
        const label = raw.loop_name ?? raw.loop_id;
        const sign = raw.delta >= 0 ? '+' : '';
        return `<div style="font-size:12px;line-height:1.6">
          <div style="font-weight:600;margin-bottom:4px">${label}</div>
          <div>整定前评分：<b>${raw.before}</b></div>
          <div>验证后评分：<b>${raw.after}</b></div>
          <div>得分变化：<b style="color:${raw.delta >= 0 ? OK : CRIT}">${sign}${raw.delta} 分</b></div>
          ${raw.significance ? '<div style="color:#52C41A">提升显著（≥5 分）</div>' : ''}
        </div>`;
      },
    },
    xAxis: {
      type: 'value',
      name: '整定前评分',
      nameLocation: 'middle',
      nameGap: 26,
      min: MN,
      max: MX,
      interval: 10,
      nameTextStyle: { color: chartTextColor.value, fontSize: 11 },
      axisLabel: { color: chartTextColor.value, fontSize: 10 },
      splitLine: { lineStyle: { color: chartSplitLineColor.value } },
    },
    yAxis: {
      type: 'value',
      name: '验证后评分',
      nameLocation: 'middle',
      nameGap: 36,
      min: MN,
      max: MX,
      interval: 10,
      nameTextStyle: { color: chartTextColor.value, fontSize: 11 },
      axisLabel: { color: chartTextColor.value, fontSize: 10 },
      splitLine: { lineStyle: { color: chartSplitLineColor.value } },
    },
    series: [
      {
        name: '改善点',
        type: 'scatter' as const,
        symbolSize: 10,
        data: good as any,
        itemStyle: {
          color: OK,
          opacity: 0.85,
          borderColor: '#fff',
          borderWidth: 1,
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: {
            color: NO_GAIN,
            type: 'dashed',
            width: 1.2,
          },
          label: {
            formatter: '无改善线',
            position: 'insideEndTop',
            color: NO_GAIN,
            fontSize: 10,
          },
          data: [
            [
              { coord: [minBound, minBound] },
              { coord: [maxBound, maxBound] },
            ],
          ],
        },
      },
      {
        name: '回退点',
        type: 'scatter' as const,
        symbolSize: 10,
        data: bad as any,
        itemStyle: {
          color: CRIT,
          opacity: 0.85,
          borderColor: '#fff',
          borderWidth: 1,
        },
      },
      // 无改善线：用 markLine 在第一个散点系列上绘制，但图例仍要显示此项
      // echarts markLine 不出现在图例，故补一条 line 空数据 + showSymbol=false 作为图例占位
      {
        name: '无改善线',
        type: 'line',
        showSymbol: false,
        lineStyle: {
          color: NO_GAIN,
          type: 'dashed',
          width: 1.2,
        },
        data: [],
      },
    ],
  } as any as ECOption;
}

function buildAndRender() {
  nextTick(() => {
    if (!chartRef.value) return;
    if (totalCount.value === 0) {
      // 空态：保持 ECharts 容器，但不绘制；外层 v-if="!count" 显示 Empty
      const chart = (chartRef.value as any)?.getInstance?.();
      if (chart) chart.clear();
      return;
    }
    renderEcharts(buildOption());
    resize();
  });
}

onMounted(() => {
  fetchScatters();
});

// props 变化（父级重新请求后 points 更新 / batchId / scope 变更）→ 重绘
watch(
  () => [props.points, props.batchId, props.scopeParams],
  () => fetchScatters(),
  { deep: true },
);

// 主题变化 → 重绘（确保颜色变量重取）
watch(
  () => [chartTextColor.value, chartSplitLineColor.value],
  () => buildAndRender(),
);
</script>

<template>
  <div class="delta-scatter flex h-full w-full flex-col overflow-hidden bg-white">
    <!-- 标题栏（同原型：蓝色小竖线 + 中文标题 + 回路计数） -->
    <div
      class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5"
    >
      <span class="flex items-center gap-1.5 text-xs font-medium text-[#1F4E79]">
        <span
          class="inline-block h-1 w-3 rounded-sm bg-[#1F4E79]"
        ></span>
        整定效果验证
        <span class="text-[10px] font-normal text-gray-400">
          整定前 × 验证后 · {{ totalCount }} 回路
        </span>
      </span>
    </div>

    <div class="relative min-h-0 flex-1">
      <Spin :spinning="loading" tip="加载中">
        <Empty
          v-if="!loading && totalCount === 0"
          :description="loadError || '暂无整定前后对比数据'"
          class="!absolute !inset-0 !m-0 !flex !items-center !justify-center"
          :image="Empty.PRESENTED_IMAGE_SIMPLE"
        />
        <EchartsUI
          v-show="totalCount > 0"
          ref="chartRef"
          class="h-full w-full"
        />
      </Spin>
    </div>
  </div>
</template>

<style scoped>
.delta-scatter :deep(.ant-spin-container),
.delta-scatter :deep(.ant-spin) {
  width: 100%;
  height: 100%;
}

.delta-scatter :deep(.ant-spin-blur) {
  opacity: 0.25;
}
</style>
