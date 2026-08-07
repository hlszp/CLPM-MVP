<script setup lang="ts">
import type { SeverityLevel } from '#/constants/clpm-ui';

import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Tag, Tooltip } from 'ant-design-vue';

import { useIndustrialStatus } from '#/composables/use-industrial-status';
import { SEVERITY_ICON, SEVERITY_LABEL } from '#/constants/clpm-ui';

interface Props {
  severity?: null | SeverityLevel | string;
  /** 大小：small(紧凑) | middle(默认) */
  size?: 'middle' | 'small';
}

const props = withDefaults(defineProps<Props>(), {
  severity: null,
  size: 'middle',
});

const { getStatusMeta } = useIndustrialStatus();

const normalized = computed<SeverityLevel>(() => {
  const s = props.severity;
  if (s === 'CRITICAL' || s === 'ERROR' || s === 'WARN' || s === 'INFO') {
    return s;
  }
  return 'INFO';
});

const meta = computed(() => {
  const statusMap = {
    CRITICAL: 'error',
    ERROR: 'error',
    WARN: 'warning',
    INFO: 'info',
  } as const;
  return getStatusMeta(statusMap[normalized.value]);
});

const label = computed(() => SEVERITY_LABEL[normalized.value]);
const tagColor = computed(() => meta.value.color);
const iconName = computed(() => SEVERITY_ICON[normalized.value]);

const tagStyle = computed(() => ({
  background: meta.value.bgColor,
  borderColor: meta.value.borderColor,
  color: meta.value.color,
  margin: 0,
  padding: props.size === 'small' ? '0 4px' : '0 6px',
  fontSize: props.size === 'small' ? '12px' : '12px',
  lineHeight: props.size === 'small' ? '18px' : '20px',
  borderRadius: '3px',
}));

const tooltip = computed(() => {
  const desc: Record<SeverityLevel, string> = {
    CRITICAL: '紧急：影响安全或产品质量，需立即处理',
    ERROR: '严重：影响装置平稳运行，优先处理',
    WARN: '警告：存在异常趋势，建议尽快处理',
    INFO: '提示：一般问题，可择机处理',
  };
  return `${label.value} - ${desc[normalized.value]}`;
});
</script>

<template>
  <Tooltip :title="tooltip" placement="top">
    <Tag :color="tagColor" :style="tagStyle">
      <IconifyIcon
        :icon="iconName"
        :size="12"
        style="margin-right: 2px; vertical-align: -2px"
      />
      {{ label }}
    </Tag>
  </Tooltip>
</template>
