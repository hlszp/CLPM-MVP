<script lang="ts" setup>
/**
 * ClpmStandardActions — 标准工具栏动作区渲染器（UI/UX v6.1 §5.1）
 *
 * 消费 usePageToolbar() 返回的 toolbarItems，统一渲染标准工具按钮 + 分组分隔符。
 * 「列设置 setting」工具特殊处理：用 ClpmColumnSettings 包裹工具栏按钮作为
 * Popover 触发器，列配置由父级通过 columnConfigs 传入。
 *
 * 用法：
 * ```vue
 * <ClpmPageToolbar ...>
 *   <template #actions>
 *     <ClpmStandardActions
 *       :items="toolbarItems"
 *       :column-configs="columnConfigs"
 *       @update:columns="columnConfigs = $event"
 *       @reset-columns="resetColumns"
 *     />
 *   </template>
 * </ClpmPageToolbar>
 * ```
 * 不含 setting 工具的页面可省略 column-configs 与监听。
 */
import type { ColumnConfig } from '#/composables/use-clpm-preferences';
import type { ToolbarItem } from '#/composables/use-page-toolbar';

import {
  ClpmColumnSettings,
  ClpmToolbarButton,
  ClpmToolbarDivider,
} from '#/components/clpm';

defineOptions({ name: 'ClpmStandardActions' });

defineProps<{
  /** usePageToolbar() 返回的渲染描述数组 */
  items: ToolbarItem[];
  /** 列设置所需的列配置（仅当 items 含 setting 工具时需要） */
  columnConfigs?: ColumnConfig[];
}>();

const emit = defineEmits<{
  'reset-columns': [];
  'update:columns': [columns: ColumnConfig[]];
}>();
</script>

<template>
  <template v-for="(item, idx) in items" :key="idx">
    <ClpmToolbarDivider v-if="item.kind === 'divider'" />
    <!-- setting 工具：有列配置时用 ClpmColumnSettings 包裹，否则灰显占位 -->
    <ClpmColumnSettings
      v-else-if="
        item.kind === 'button' &&
        item.action === 'setting' &&
        !item.disabled &&
        columnConfigs &&
        columnConfigs.length > 0
      "
      :columns="columnConfigs"
      @update:columns="emit('update:columns', $event)"
      @reset="emit('reset-columns')"
    >
      <template #trigger>
        <ClpmToolbarButton
          icon="setting"
          :label="item.label"
          :tooltip="item.label"
          :active="item.active"
        />
      </template>
    </ClpmColumnSettings>
    <ClpmToolbarButton
      v-else-if="item.kind === 'button'"
      :icon="item.action"
      :label="item.label"
      :disabled="item.disabled"
      :disabled-reason="item.disabledReason"
      :loading="item.loading"
      :active="item.active"
      :tooltip="item.tooltip || item.label"
      @click="item.onClick"
    />
  </template>
</template>
