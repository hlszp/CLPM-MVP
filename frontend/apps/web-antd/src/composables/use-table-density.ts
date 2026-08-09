/**
 * useTableDensity —— 表格密度三档（整改 A-07 表格基线）
 *
 * 三档密度：compact(32px)/default(36px)/relaxed(44px)，映射 antd Table
 * size small/middle/large；按页面持久化（复用 usePagePreference）。
 * 默认 compact（工业高密度扫读场景，对齐方案场景句"27 寸显示器高密度"）。
 *
 * 用法：
 * ```ts
 * const { tableSize, densityLabel, cycleDensity } = useTableDensity('loop-manage');
 * <ClpmToolbarButton icon="ant-design:column-height-outlined" :label="`密度：${densityLabel}`"
 *   :tooltip="`密度：${densityLabel}（点击切换）`" @click="cycleDensity" />
 * <Table :size="tableSize" ... />
 * ```
 */
import type { TableProps } from 'ant-design-vue';

import { computed } from 'vue';

import { usePagePreference } from '#/composables/use-clpm-preferences';

export type TableDensity = 'compact' | 'default' | 'relaxed';

/** 密度 → antd Table size（32/36/44 行高由 antd 主题与高密度样式共同保证） */
const DENSITY_SIZE: Record<TableDensity, TableProps['size']> = {
  compact: 'small',
  default: 'middle',
  relaxed: 'large',
};

const DENSITY_LABEL: Record<TableDensity, string> = {
  compact: '紧凑',
  default: '标准',
  relaxed: '宽松',
};

/** 循环顺序：紧凑 → 标准 → 宽松 → 紧凑 */
const NEXT_DENSITY: Record<TableDensity, TableDensity> = {
  compact: 'default',
  default: 'relaxed',
  relaxed: 'compact',
};

export function useTableDensity(pageKey: string) {
  const { preferences } = usePagePreference(pageKey);

  const density = computed<TableDensity>({
    get: () =>
      (preferences.value.tableDensity as TableDensity | undefined) ?? 'compact',
    set: (v) => {
      preferences.value = { ...preferences.value, tableDensity: v };
    },
  });

  /** antd Table size 绑定值 */
  const tableSize = computed(() => DENSITY_SIZE[density.value]);
  /** 当前密度中文名 */
  const densityLabel = computed(() => DENSITY_LABEL[density.value]);

  /** 点击循环切换 */
  function cycleDensity() {
    density.value = NEXT_DENSITY[density.value];
  }

  return { cycleDensity, density, densityLabel, tableSize };
}
