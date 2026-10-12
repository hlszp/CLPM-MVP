<script lang="ts" setup>
import { computed } from 'vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'ScoreSparkline' });

const props = withDefaults(defineProps<Props>(), {
  width: 80,
  height: 20,
});

interface Props {
  /**
   * 折线数据。null = 未计算（P3-D6b R03-2）：该点断开不画
   * （绝不画作 0 分）；连续非 null 段各画一段路径。
   */
  data: (null | number)[];
  width?: number;
  height?: number;
}

const { themeColors } = useClpmTheme();

/** 非空值集合（比例尺只按实际有值点计算，null 不拉低基线） */
const validValues = computed(() =>
  props.data.filter((v): v is number => v !== null),
);

function scaleOf(vals: number[], height: number) {
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  return (v: number) => height - ((v - min) / range) * (height - 2) - 1;
}

function buildPaths(
  data: (null | number)[],
  width: number,
  height: number,
): string[] {
  if (!data || data.length < 2) return [];
  const vals = data.filter((v): v is number => v !== null);
  if (vals.length < 2) return [];
  const yOf = scaleOf(vals, height);
  const step = width / (data.length - 1);
  const paths: string[] = [];
  let current: string[] = [];
  data.forEach((v, i) => {
    if (v === null) {
      // null 断点：结束当前段，不画作 0
      if (current.length >= 2) paths.push(`M ${current.join(' L ')}`);
      current = [];
      return;
    }
    const x = i * step;
    current.push(`${x.toFixed(1)},${yOf(v).toFixed(1)}`);
  });
  if (current.length >= 2) paths.push(`M ${current.join(' L ')}`);
  return paths;
}

const pathDs = computed(() =>
  buildPaths(props.data, props.width, props.height),
);

/** 末点：最后一个非 null 值（null 尾部不画点；全空无点） */
const lastValid = computed<null | { index: number; value: number }>(() => {
  for (let i = props.data.length - 1; i >= 0; i--) {
    const v = props.data[i];
    if (v !== null && v !== undefined) {
      return { index: i, value: v };
    }
  }
  return null;
});

const lastPointY = computed(() => {
  const last = lastValid.value;
  if (!last || validValues.value.length < 2) return props.height / 2;
  const yOf = scaleOf(validValues.value, props.height);
  return yOf(last.value);
});

const lastPointX = computed(() => {
  const last = lastValid.value;
  if (!last || props.data.length < 2) return props.width;
  return (last.index * props.width) / (props.data.length - 1);
});

const lineColor = computed(() => {
  const last = lastValid.value;
  if (!last) return themeColors.value.NEUTRAL;
  if (last.value >= 80) return themeColors.value.SUCCESS;
  if (last.value >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
});
</script>

<template>
  <svg
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    preserveAspectRatio="none"
    class="score-sparkline"
  >
    <path
      v-for="(d, i) in pathDs"
      :key="i"
      :d="d"
      fill="none"
      :stroke="lineColor"
      stroke-width="1.5"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <circle
      v-if="lastValid"
      :cx="lastPointX"
      :cy="lastPointY"
      r="2"
      :fill="lineColor"
    />
  </svg>
</template>

<style scoped>
.score-sparkline {
  display: block;
}
</style>
