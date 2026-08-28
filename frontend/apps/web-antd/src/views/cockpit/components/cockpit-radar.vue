<script lang="ts" setup>
/**
 * 驾驶舱页2 · 六维绩效雷达（方案 11 §5.4 统一口径）
 *
 * 六轴：自控率/平稳率/准确率/快速率/好值率/有效率（0~100）；
 * 中心显示综合评分（五档色染）+ 等级标签。
 * 节点级与回路级共用；颜色从 .cockpit-root CSS 变量解析（随驾驶舱主题）。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { GradeInfo, SixDimValues } from '../loops-shared';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useCockpitStore } from '#/store/cockpit';

import { readCockpitColors, resolveCssVar, SIX_DIMS } from '../loops-shared';

defineOptions({ name: 'CockpitRadar' });

const props = defineProps<{
  /** 六维得分（null=无数据，显示空态） */
  dims: null | SixDimValues;
  /** 五档等级（中心色染；无评分传 null） */
  grade?: GradeInfo | null;
  /** 中心综合评分 */
  score?: null | number;
}>();

const cockpitStore = useCockpitStore();

const rootRef = ref<HTMLElement>();
const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const hasData = computed(() => props.dims !== null);

function buildOption() {
  const colors = readCockpitColors(rootRef.value);
  const dims = props.dims;

  const indicators = SIX_DIMS.map((m) => {
    const v = dims?.[m.key];
    const safe = typeof v === 'number' && !Number.isNaN(v) ? v : null;
    return {
      name: safe === null ? m.label : `${m.label} ${Math.round(safe)}`,
      max: 100,
      min: 0,
    };
  });

  const values = SIX_DIMS.map((m) => {
    const v = dims?.[m.key];
    return typeof v === 'number' && !Number.isNaN(v) ? v : 0;
  });

  const score = props.score;
  const scoreText =
    score === null || score === undefined || Number.isNaN(score)
      ? '—'
      : Number(score).toFixed(1);
  const gradeColor = props.grade
    ? resolveCssVar(rootRef.value, props.grade.colorVar)
    : colors.text2;

  return {
    animation: false,
    radar: {
      indicator: indicators,
      center: ['50%', '52%'],
      radius: '62%',
      shape: 'polygon' as const,
      splitNumber: 5,
      axisName: { color: colors.text2, fontSize: 11 },
      splitLine: { lineStyle: { color: colors.border } },
      splitArea: { show: false },
      axisLine: { lineStyle: { color: colors.border } },
    },
    graphic: {
      elements: [
        {
          type: 'text',
          left: 'center',
          top: '44%',
          style: {
            text: scoreText,
            fontSize: 26,
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
            text: props.grade?.label ?? '',
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
        lineStyle: { color: colors.accent, width: 1.5 },
        itemStyle: { color: colors.accent },
        areaStyle: { color: colors.accent, opacity: 0.15 },
        data: [{ value: values, name: '六维绩效' }],
      },
    ],
  };
}

function refresh() {
  renderEcharts(buildOption());
}

watch(
  () => [props.dims, props.score, props.grade, cockpitStore.theme],
  () => {
    if (hasData.value) refresh();
  },
  { deep: true, flush: 'post' },
);

onMounted(() => {
  if (hasData.value) refresh();
});
</script>

<template>
  <div ref="rootRef" class="ckradar">
    <EchartsUI v-if="hasData" ref="chartRef" height="100%" />
    <div v-else class="ckradar__empty">暂无绩效数据</div>
  </div>
</template>

<style scoped>
.ckradar {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.ckradar__empty {
  position: absolute;
  top: 50%;
  left: 50%;
  font-size: 12px;
  color: var(--ck-text-3);
  transform: translate(-50%, -50%);
}
</style>
