<script lang="ts" setup>
/**
 * UI-04 衍生 ClpmStatusPanel 状态面板（v6.1 §7.16.4 / §14 A-03 G-01）
 *
 * 用于 AAS 同步状态、装置级 KPI 概览、多回路聚合状态展示。
 * 替代 AAS / dashboard 页面散落的 Card 堆叠。
 */
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { useIndustrialStatus } from '#/composables/use-industrial-status';

import ClpmNumeric from './numeric.vue';

defineOptions({ name: 'ClpmStatusPanel' });

const props = withDefaults(defineProps<Props>(), {
  columns: 3,
  bordered: true,
  dense: false,
});

interface StatusItem {
  /** 项目标签 */
  label: string;
  /** 数值或文本 */
  value?: null | number | string;
  /** 业务状态枚举（用于色相映射） */
  status?: string;
  /** 单位 */
  unit?: string;
  /** 精度 */
  precision?: number;
  /** 是否空值 */
  empty?: boolean;
  /** 趋势 */
  trend?: 'down' | 'flat' | 'up';
  /** 副标题/说明 */
  hint?: string;
}

interface Props {
  /** 面板标题 */
  title: string;
  /** 状态项列表 */
  items: StatusItem[];
  /** 列数（每行显示几个项目） */
  columns?: number;
  /** 是否显示边框（默认 true，对齐 §3.5.1 边框优先） */
  bordered?: boolean;
  /** 紧凑模式 */
  dense?: boolean;
}

const { getStatusMeta } = useIndustrialStatus();

/** 处理后的状态项 */
const processedItems = computed(() =>
  props.items.map((item) => {
    const meta = item.status ? getStatusMeta(item.status) : null;
    return {
      ...item,
      meta,
    };
  }),
);

/** 网格样式 */
const gridStyle = computed(() => ({
  gridTemplateColumns: `repeat(${props.columns}, minmax(0, 1fr))`,
}));
</script>

<template>
  <div
    class="clpm-status-panel"
    :class="[
      bordered ? 'clpm-status-panel--bordered' : '',
      dense ? 'clpm-status-panel--dense' : '',
    ]"
  >
    <div v-if="title" class="clpm-status-panel__header">
      <span class="clpm-status-panel__title">{{ title }}</span>
    </div>
    <div class="clpm-status-panel__grid" :style="gridStyle">
      <div
        v-for="(item, idx) in processedItems"
        :key="idx"
        class="clpm-status-panel__item"
      >
        <div class="clpm-status-panel__label">{{ item.label }}</div>
        <div class="clpm-status-panel__value-row">
          <ClpmNumeric
            :value="item.value"
            :precision="item.precision"
            :unit="item.unit"
            :trend="item.trend"
            :empty="item.empty"
            size="lg"
            :weight="700"
            class="clpm-status-panel__value"
            :class="[
              item.meta ? `clpm-status-panel__value--${item.meta.status}` : '',
            ]"
          />
          <IconifyIcon
            v-if="item.meta"
            :icon="item.meta.icon"
            class="clpm-status-panel__status-icon"
            :style="{ color: item.meta.color }"
          />
        </div>
        <div v-if="item.hint" class="clpm-status-panel__hint">
          {{ item.hint }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.clpm-status-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.clpm-status-panel--bordered {
  padding: 12px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border-default));
  border-radius: var(--radius-industrial-lg);
  box-shadow: none;
}

.clpm-status-panel--dense {
  gap: 4px;
  padding: 8px;
}

.clpm-status-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 6px;
  border-bottom: 1px solid hsl(var(--border-default));
}

.clpm-status-panel__title {
  font-size: 13px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.clpm-status-panel__grid {
  display: grid;
  gap: 12px;
}

.clpm-status-panel__item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.clpm-status-panel__label {
  font-size: 11px;
  font-weight: 500;
  color: hsl(var(--muted-foreground));
}

.clpm-status-panel__value-row {
  display: flex;
  gap: 4px;
  align-items: center;
}

.clpm-status-panel__value {
  flex: 1 1 auto;
}

.clpm-status-panel__value--ok {
  color: hsl(var(--status-ok));
}

.clpm-status-panel__value--warning {
  color: hsl(var(--status-warning));
}

.clpm-status-panel__value--error {
  color: hsl(var(--status-error));
}

.clpm-status-panel__value--info {
  color: hsl(var(--status-info));
}

.clpm-status-panel__value--neutral {
  color: hsl(var(--status-neutral));
}

.clpm-status-panel__status-icon {
  flex-shrink: 0;
  font-size: 14px;
}

.clpm-status-panel__hint {
  margin-top: 2px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}
</style>
