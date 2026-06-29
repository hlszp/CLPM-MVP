<script lang="ts" setup>
/**
 * 可信度徽章组件
 *
 * 对齐 UI/UX §7.15 可信度标识组件规范：
 * - A 级（valid_rate ≥ 95%）：青绿色，可信度高
 * - B 级（80% ≤ valid_rate < 95%）：浅绿色，可信度较好
 * - C 级（60% ≤ valid_rate < 80%）：琥珀色，可信度一般
 * - D 级（20% ≤ valid_rate < 60%）：橙红色，可信度较低
 * - E 级（valid_rate < 20%）：灰色，标记 INCONCLUSIVE，评分留空
 */
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Tag, Tooltip } from 'ant-design-vue';

defineOptions({ name: 'MetricConfidenceBadge' });

type ConfidenceLevel = 'A' | 'B' | 'C' | 'D' | 'E';

interface Props {
  /** 可信度等级，传 null/undefined 时不渲染任何内容 */
  level?: ConfidenceLevel | null;
  /** 有效数据率，0~1，用于 Tooltip 显示 */
  validRate?: number | null;
  /** 尺寸 */
  size?: 'default' | 'small';
  /** 是否显示等级文字，默认 true；为 false 时仅显示色块圆点 */
  showLabel?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  level: null,
  validRate: null,
  size: 'default',
  showLabel: true,
});

/** 等级配置：Tag 颜色预设、色块圆点颜色、文字、是否为 INCONCLUSIVE */
const levelMap: Record<
  ConfidenceLevel,
  { color: string; dot: string; label: string; inconclusive?: boolean }
> = {
  A: { color: 'green', dot: '#52c41a', label: 'A' },
  B: { color: 'cyan', dot: '#13c2c2', label: 'B' },
  C: { color: 'gold', dot: '#faad14', label: 'C' },
  D: { color: 'orange', dot: '#fa8c16', label: 'D' },
  E: {
    color: 'default',
    dot: '#8c8c8c',
    label: 'INCONCLUSIVE',
    inconclusive: true,
  },
};

/** 当前等级配置 */
const current = computed(() => {
  if (!props.level) return null;
  return levelMap[props.level] ?? null;
});

/** Tooltip 显示的有效数据率文本 */
const tooltipText = computed(() => {
  if (props.validRate === null || props.validRate === undefined) {
    return null;
  }
  const pct = (props.validRate * 100).toFixed(1);
  return `有效数据率: ${pct}%`;
});
</script>

<template>
  <Tooltip v-if="current" :title="tooltipText">
    <Tag
      v-if="showLabel"
      :color="current.color"
      :size="size"
      class="m-0 inline-flex items-center"
    >
      <span class="inline-flex items-center gap-1">
        <span>{{ current.label }}</span>
        <IconifyIcon
          v-if="current.inconclusive"
          icon="ant-design:exclamation-circle-outlined"
          class="text-xs"
        />
      </span>
    </Tag>
    <span v-else class="inline-flex items-center">
      <span
        class="inline-block rounded-full"
        :class="size === 'small' ? 'h-2 w-2' : 'h-2.5 w-2.5'"
        :style="{ backgroundColor: current.dot }"
      ></span>
      <IconifyIcon
        v-if="current.inconclusive"
        icon="ant-design:exclamation-circle-outlined"
        class="ml-1 text-xs text-gray-500"
      />
    </span>
  </Tooltip>
</template>
