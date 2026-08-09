<script setup lang="ts">
import type { ConfidenceLevel } from '#/api/metric';

import { computed } from 'vue';

import { Badge, Tooltip } from 'ant-design-vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useIndustrialStatus } from '#/composables/use-industrial-status';
import {
  CONFIDENCE_LEVEL_DESCRIPTION,
  CONFIDENCE_LEVEL_LABEL,
  resolveConfidenceLevel,
} from '#/constants/clpm-ui';

interface Props {
  /** 可信度数值 0~1（旧字段，兼容用） */
  confidence?: null | number;
  /** 有效数据率 0~1（优先用于等级判定） */
  validRate?: null | number;
  /** 后端直接返回的等级 A/B/C/D/E，若存在则直接使用 */
  level?: ConfidenceLevel | null;
  /** 是否显示数值（如 "A 92%"），默认 false 只显示等级 */
  showValue?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  confidence: null,
  validRate: null,
  level: null,
  showValue: false,
});

const { getStatusMeta } = useIndustrialStatus();
const { themeColors } = useClpmTheme();

const resolvedLevel = computed<ConfidenceLevel | null>(() =>
  resolveConfidenceLevel(props.confidence, props.validRate, props.level),
);

const levelText = computed(() => {
  if (!resolvedLevel.value) return '?';
  if (props.showValue && props.validRate != null) {
    return `${resolvedLevel.value} ${Math.round(props.validRate * 100)}%`;
  }
  return resolvedLevel.value;
});

const badgeStyle = computed(() => {
  if (!resolvedLevel.value) {
    return {
      backgroundColor: themeColors.value.NEUTRAL,
      color: 'hsl(0 0% 100%)',
      fontSize: '12px',
      fontWeight: 600,
      minWidth: '22px',
      height: '22px',
      lineHeight: '22px',
      padding: '0 4px',
      boxShadow: 'none',
    };
  }
  const statusMap = {
    A: 'ok',
    B: 'ok',
    C: 'info',
    D: 'warning',
    E: 'neutral',
  } as const;
  const meta = getStatusMeta(statusMap[resolvedLevel.value]);
  return {
    backgroundColor: meta.color,
    color: 'hsl(0 0% 100%)',
    fontSize: '12px',
    fontWeight: 600,
    minWidth: '22px',
    height: '22px',
    lineHeight: '22px',
    padding: '0 4px',
    boxShadow: 'none',
  };
});

const tooltipContent = computed(() => {
  if (!resolvedLevel.value) return '暂无可信度数据';
  const label = CONFIDENCE_LEVEL_LABEL[resolvedLevel.value];
  const desc = CONFIDENCE_LEVEL_DESCRIPTION[resolvedLevel.value];
  const rate =
    props.validRate == null
      ? ''
      : `（有效率 ${Math.round(props.validRate * 100)}%）`;
  return `${label}${rate}：${desc}`;
});
</script>

<template>
  <Tooltip :title="tooltipContent" placement="top">
    <Badge
      :count="levelText"
      :number-style="badgeStyle"
      :style="{ cursor: 'help' }"
    />
  </Tooltip>
</template>
