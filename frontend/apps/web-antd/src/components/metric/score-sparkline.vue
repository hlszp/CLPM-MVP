<script lang="ts" setup>
import { computed } from 'vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'ScoreSparkline' });

interface Props {
  data: number[];
  width?: number;
  height?: number;
}

const props = withDefaults(defineProps<Props>(), {
  width: 80,
  height: 20,
});

const { themeColors } = useClpmTheme();

function buildPath(data: number[], width: number, height: number): string {
  if (!data || data.length < 2) return '';
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = width / (data.length - 1);
  const points = data.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / range) * (height - 2) - 1;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return `M ${points.join(' L ')}`;
}

const pathD = computed(() => buildPath(props.data, props.width, props.height));

const lastPointY = computed(() => {
  const data = props.data;
  if (!data || data.length < 2) return props.height / 2;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const last = data[data.length - 1]!;
  return props.height - ((last - min) / range) * (props.height - 2) - 1;
});

const lineColor = computed(() => {
  const data = props.data;
  if (!data || data.length < 2) return themeColors.value.NEUTRAL;
  const last = data[data.length - 1]!;
  if (last >= 80) return themeColors.value.SUCCESS;
  if (last >= 60) return themeColors.value.WARNING;
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
      :d="pathD"
      fill="none"
      :stroke="lineColor"
      stroke-width="1.5"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <circle
      :cx="width"
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