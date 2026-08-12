<script lang="ts" setup>
/**
 * 工作台 R5 评估证据区 · 六维形态雷达图（Phase 1 重构 · 2026-08-12）
 *
 * 6 轴：平稳率 / 准确率 / 快速率 / 自控率 / 好值率 / 饱和率
 * - 每轴 0-100，5 等分
 * - 数据多边形：填充 rgba(29,78,216,0.12)，边线 #1d4ed8 宽度 1.5
 * - 轴标签带数值（如 "平稳率 58"）
 * - 中心显示综合评分 + 等级（graphic 组件）
 * - 不需要 legend 和 tooltip
 *
 * 数据来源：父级传入 axes（六维达成度 0-100）。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

defineOptions({ name: 'WorkbenchRadar6' });

const props = defineProps<Props>();

interface Props {
  /** 六轴数据（0-100 达成度） */
  axes: {
    accuracyRate: number;
    autoModeRate: number;
    fastRate: number;
    goodValueRate: number;
    saturationRate: number;
    steadyRate: number;
  };
  /** 中心综合评分 */
  score: null | number;
  /** 中文等级标签 */
  grade?: string; // 优秀/良好/合格/警告/不合格
  /** 等级颜色 */
  gradeColor?: string;
}

const { chartTextColor, chartSplitLineColor } = useClpmTheme();
const { getEchartsBase } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

// ===== 配色常量（对齐设计规范 §R5 雷达图）=====
const POLY_COLOR = '#1d4ed8';
const POLY_FILL = 'rgba(29, 78, 216, 0.12)';

const AXIS_META = [
  { key: 'steadyRate', label: '平稳率' },
  { key: 'accuracyRate', label: '准确率' },
  { key: 'fastRate', label: '快速率' },
  { key: 'autoModeRate', label: '自控率' },
  { key: 'goodValueRate', label: '好值率' },
  { key: 'saturationRate', label: '饱和率' },
] as const;

/** 构造 ECharts option */
function buildOption() {
  const indicators = AXIS_META.map((m) => {
    const v = props.axes[m.key];
    const safe = typeof v === 'number' && !Number.isNaN(v) ? v : 0;
    return {
      name: `${m.label} ${Math.round(safe)}`,
      max: 100,
      min: 0,
    };
  });

  const values = AXIS_META.map((m) => {
    const v = props.axes[m.key];
    return typeof v === 'number' && !Number.isNaN(v) ? v : 0;
  });

  const score = props.score;
  const scoreText =
    score !== null && score !== undefined && !Number.isNaN(score)
      ? Number(score).toFixed(1)
      : '—';
  const gradeText = props.grade ?? '';
  const gradeColor = props.gradeColor ?? POLY_COLOR;

  return {
    ...getEchartsBase(),
    grid: { top: 16, right: 16, bottom: 16, left: 16, containLabel: true },
    radar: {
      indicator: indicators,
      center: ['50%', '52%'],
      radius: '64%',
      shape: 'polygon' as const,
      splitNumber: 5,
      axisName: {
        color: chartTextColor.value,
        fontSize: 11,
      },
      splitLine: {
        lineStyle: { color: chartSplitLineColor.value },
      },
      splitArea: {
        show: false,
      },
      axisLine: {
        lineStyle: { color: chartSplitLineColor.value },
      },
    },
    graphic: {
      elements: [
        {
          type: 'text',
          left: 'center',
          top: '46%',
          style: {
            text: scoreText,
            fontSize: 28,
            fontWeight: 700,
            fill: gradeColor,
            textAlign: 'center',
          },
          z: 100,
        },
        {
          type: 'text',
          left: 'center',
          top: '60%',
          style: {
            text: gradeText,
            fontSize: 12,
            fill: gradeColor,
            textAlign: 'center',
          },
          z: 100,
        },
      ],
    },
    series: [
      {
        type: 'radar' as const,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { color: POLY_COLOR, width: 1.5 },
        itemStyle: { color: POLY_COLOR },
        areaStyle: { color: POLY_FILL },
        data: [
          {
            value: values,
            name: '当前形态',
          },
        ],
      },
    ],
  };
}

function refresh() {
  renderEcharts(buildOption());
}

const hasData = computed(() => !!props.axes);

watch(
  () => [props.axes, props.score, props.grade, props.gradeColor],
  () => {
    if (hasData.value) refresh();
  },
  { deep: true, flush: 'post' },
);

onMounted(() => {
  if (hasData.value) refresh();
});</script>

<template>
  <div class="radar6">
    <EchartsUI ref="chartRef" height="100%" />
    <div v-if="!hasData" class="radar6__empty">
      <span>暂无形态数据</span>
    </div>
  </div>
</template>

<style scoped>
.radar6 {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.radar6__empty {
  position: absolute;
  top: 50%;
  left: 50%;
  font-size: 12px;
  color: hsl(var(--foreground) / 45%);
  pointer-events: none;
  transform: translate(-50%, -50%);
}
</style>
