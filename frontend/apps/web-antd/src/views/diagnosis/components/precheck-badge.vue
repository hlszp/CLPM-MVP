<script setup lang="ts">
import type { DiagnosisApi } from '#/api/diagnosis';

/**
 * DiagnosisPrecheckBadge - 发起前数据充足性预检徽标（16 号文 F5）
 *
 * 复用 FitnessBadge 视觉模式（图标 + 1px 描边 + 透明背景，图标态紧凑）：
 * - sufficient 充足绿 / marginal 疑似不足琥珀 / insufficient 不足红
 * - unknown 无评估数据中性灰（数据源缺失，非数据质量结论，不误报"不足"）
 *
 * 悬浮显示依据（§4 F5.2："近 24h 快照 N 行，低于预期 M 行"）。
 * 事前提示定位：不阻止勾选/发起（fitness 门禁行为不变）。
 */
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Tooltip } from 'ant-design-vue';

import { PRECHECK_META } from '../constants';

defineOptions({ name: 'DiagnosisPrecheckBadge' });

const props = withDefaults(
  defineProps<{
    /** 预检项；undefined（尚未取到/查询失败）→ 不渲染 */
    item?: DiagnosisApi.PrecheckItem;
    /** 预检窗口（悬浮依据文案用） */
    window?: string;
  }>(),
  { item: undefined, window: '24h' },
);

const meta = computed(() =>
  props.item ? PRECHECK_META[props.item.level] : null,
);

/** 悬浮依据文案（§4 F5.2 口径） */
const tooltipText = computed(() => {
  const item = props.item;
  if (!item || !meta.value) return '';
  if (item.level === 'unknown') {
    return '无评估数据：评估尚未产出该回路快照（数据源缺失，不代表数据质量结论）';
  }
  const base = `近 ${props.window} 快照 ${item.rowCount} 行 / 预期 ${item.expectedRows} 行`;
  if (item.level === 'insufficient') {
    return `${base}，低于调度密度门禁（50%），发起诊断可能 DATA_INSUFFICIENT`;
  }
  if (item.level === 'marginal') {
    return `${base}，密度余量偏低（刚过 50% 门禁线）`;
  }
  return `${base}，密度充足`;
});

const tagStyle = computed(() => ({
  color: meta.value?.color,
  borderColor: meta.value?.color,
  background: 'transparent',
  margin: 0,
  padding: '0 3px',
  fontSize: '10px',
  lineHeight: '16px',
  borderRadius: '3px',
  display: 'inline-flex',
  alignItems: 'center',
  border: '1px solid',
  cursor: 'help',
  flexShrink: 0,
}));
</script>

<template>
  <Tooltip v-if="meta" :title="tooltipText" placement="top">
    <span :style="tagStyle" :aria-label="meta.label">
      <IconifyIcon :icon="meta.icon" :size="10" />
    </span>
  </Tooltip>
</template>
