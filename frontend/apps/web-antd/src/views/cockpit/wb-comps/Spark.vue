<script setup lang="ts">
/**
 * Sparkline 迷你趋势线（方案 §5.1 F-OV-02 · 装置排名 sparkline）
 *
 * - 纯 SVG polyline，**无动画**（工业 UI 规范：微型柱状/折线不加动画）
 * - 输入 ScoreTrendPoint[]（{t: ISO, v: 0~100}），自动归一映射到 viewBox
 * - 空数据 / 全 null → 渲染 "—" 占位，不报错
 * - 颜色由调用方注入（默认工业蓝 #1F4E79）
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = withDefaults(
  defineProps<{
    color?: string;
    height?: number;
    points?: WorkbenchApi.ScoreTrendPoint[];
    width?: number;
  }>(),
  {
    color: '#1F4E79',
    height: 28,
    points: () => [],
    width: 120,
  },
);

const PAD = 2; // 边距，避免线贴边

const pathD = computed<null | string>(() => {
  const vals = props.points.map((p) => p.v);
  if (vals.length === 0) return null;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1; // 全平线时避免除零
  const w = props.width - PAD * 2;
  const h = props.height - PAD * 2;
  const step = vals.length > 1 ? w / (vals.length - 1) : 0;
  return (
    vals
      .map((v, i) => {
        const x = PAD + i * step;
        const y = PAD + h - ((v - min) / range) * h;
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ') || null
  );
});

const lastVal = computed(() => {
  const pts = props.points;
  return pts.length > 0 ? pts[pts.length - 1]?.v : null;
});
</script>

<template>
  <span
    class="inline-flex items-center"
    :style="{ width: `${width}px`, height: `${height}px` }"
  >
    <svg
      v-if="pathD"
      :width="width"
      :height="height"
      :viewBox="`0 0 ${width} ${height}`"
      preserveAspectRatio="none"
    >
      <path
        :d="pathD"
        :stroke="color"
        fill="none"
        stroke-linecap="round"
        stroke-linejoin="round"
        stroke-width="1.5"
      />
    </svg>
    <span
      v-else
      class="flex h-full w-full items-center justify-center text-[10px] text-gray-300"
      >—</span
    >
  </span>
  <!-- 末值用于无障碍/调试，不显示 -->
  <span v-if="false">{{ lastVal }}</span>
</template>
