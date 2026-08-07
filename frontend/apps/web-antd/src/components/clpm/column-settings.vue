<script lang="ts" setup>
/**
 * 列设置（UI/UX v6.1 §7.16 列设置）
 *
 * 两种用法：
 * 1. 默认触发器（向后兼容）：不传 trigger 插槽，渲染自带「列设置」按钮
 *    <ClpmColumnSettings :columns="cols" @update:columns="..." @reset="..." />
 * 2. 外部触发器（统一工具栏 setting 工具）：传 #trigger 插槽，用工具栏按钮触发
 *    <ClpmColumnSettings v-model:open="open" :columns="cols" ...>
 *      <template #trigger><ClpmToolbarButton icon="setting" ... /></template>
 *    </ClpmColumnSettings>
 *
 * 受控/非受控展开：传 open prop 即受控（v-model:open），否则内部状态自管。
 */
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import { computed, ref } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Button, Checkbox, Popover } from 'ant-design-vue';

defineOptions({ name: 'ClpmColumnSettings' });

const props = defineProps<Props>();
const emit = defineEmits<{
  reset: [];
  'update:columns': [columns: ColumnConfig[]];
  'update:open': [open: boolean];
}>();
interface Props {
  /** 当前列配置 */
  columns: ColumnConfig[];
  /** 受控展开（配合外部触发器，如工具栏 setting 按钮） */
  open?: boolean;
}

/** 非受控内部展开状态 */
const innerOpen = ref(false);
const isControlled = computed(() => props.open !== undefined);
const openModel = computed<boolean>({
  get: () => (isControlled.value ? Boolean(props.open) : innerOpen.value),
  set: (v) => {
    if (isControlled.value) emit('update:open', v);
    else innerOpen.value = v;
  },
});

function toggleVisible(key: string) {
  const cols = props.columns.map((c) =>
    c.key === key ? { ...c, visible: !c.visible } : c,
  );
  emit('update:columns', cols);
}

function moveUp(index: number) {
  if (index === 0) return;
  const cols = [...props.columns];
  const [column] = cols.splice(index, 1);
  if (!column) return;
  cols.splice(index - 1, 0, column);
  emit('update:columns', cols);
}

function moveDown(index: number) {
  if (index === props.columns.length - 1) return;
  const cols = [...props.columns];
  const [column] = cols.splice(index, 1);
  if (!column) return;
  cols.splice(index + 1, 0, column);
  emit('update:columns', cols);
}
</script>

<template>
  <Popover v-model:open="openModel" trigger="click" placement="bottomRight">
    <template #content>
      <div class="w-56">
        <div class="mb-2 text-sm font-medium">列设置</div>
        <div
          v-for="(col, idx) in columns"
          :key="col.key"
          class="flex items-center gap-2 py-1"
        >
          <Checkbox :checked="col.visible" @change="toggleVisible(col.key)" />
          <span class="flex-1 text-sm">{{ col.label }}</span>
          <Button
            type="text"
            size="small"
            :disabled="idx === 0"
            @click="moveUp(idx)"
          >
            <IconifyIcon icon="ant-design:arrow-up-outlined" />
          </Button>
          <Button
            type="text"
            size="small"
            :disabled="idx === columns.length - 1"
            @click="moveDown(idx)"
          >
            <IconifyIcon icon="ant-design:arrow-down-outlined" />
          </Button>
        </div>
        <div class="mt-2 border-t pt-2">
          <Button type="link" size="small" @click="emit('reset')">
            恢复默认
          </Button>
        </div>
      </div>
    </template>
    <slot name="trigger">
      <Button type="text" size="small">
        <IconifyIcon icon="ant-design:setting-outlined" />
        列设置
      </Button>
    </slot>
  </Popover>
</template>
