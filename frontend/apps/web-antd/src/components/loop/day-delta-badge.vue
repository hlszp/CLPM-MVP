<script lang="ts" setup>
/**
 * DayDeltaBadge —— "较昨日"增量徽标（整改 C1-1）
 *
 * 巡检第一问"较昨日新增/恶化/好转了什么"的最小呈现单元：
 * - WORSENED：红 ▼|delta|（恶化，最需要关注）
 * - IMPROVED：绿 ▲delta（好转）
 * - FLAT：不显示（持平不制造视觉噪音）
 * - NEW：蓝「新增」（昨日无基线快照）
 * - null（无当前评分）：不渲染（由调用方 v-if 控制亦可）
 *
 * 色彩语义对齐 color-convention.md：红=异常/恶化，绿=正常/好转，蓝=信息/新增。
 */
import { computed } from 'vue';

import { Tooltip } from 'ant-design-vue';

defineOptions({ name: 'DayDeltaBadge' });

const props = withDefaults(defineProps<Props>(), {
  delta: null,
  trend: null,
});

interface Props {
  /** 评分增量（已按 1 位小数取舍），FLAT/NEW 时可为 null */
  delta?: null | number;
  /** 趋势枚举（后端 monitor 列表 dayTrend） */
  trend?: 'FLAT' | 'IMPROVED' | 'NEW' | 'WORSENED' | null;
}

const text = computed(() => {
  switch (props.trend) {
    case 'FLAT': {
      return '';
    }
    case 'IMPROVED': {
      return `▲${props.delta?.toFixed(1) ?? ''}`;
    }
    case 'NEW': {
      return '新增';
    }
    case 'WORSENED': {
      return `▼${Math.abs(props.delta ?? 0).toFixed(1)}`;
    }
    default: {
      return '';
    }
  }
});

const colorClass = computed(() => {
  switch (props.trend) {
    case 'IMPROVED': {
      return 'text-emerald-600';
    }
    case 'NEW': {
      return 'text-blue-500';
    }
    case 'WORSENED': {
      return 'text-rose-600';
    }
    default: {
      return 'text-gray-400';
    }
  }
});

const tooltip = computed(() => {
  switch (props.trend) {
    case 'FLAT': {
      return '较昨日评分基本持平（变化 < 2 分）';
    }
    case 'IMPROVED': {
      return `较昨日评分好转 ${props.delta?.toFixed(1) ?? ''} 分`;
    }
    case 'NEW': {
      return '昨日无基线快照，今日首次出分';
    }
    case 'WORSENED': {
      return `较昨日评分恶化 ${Math.abs(props.delta ?? 0).toFixed(1)} 分，建议优先关注`;
    }
    default: {
      return '';
    }
  }
});
</script>

<template>
  <Tooltip v-if="trend && text" :title="tooltip">
    <span class="text-xs font-medium" :class="colorClass">{{ text }}</span>
  </Tooltip>
</template>
