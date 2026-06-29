<script lang="ts" setup>
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import { IconifyIcon } from '@vben/icons';

import { Button, Checkbox, Popover } from 'ant-design-vue';

defineOptions({ name: 'ClpmColumnSettings' });

interface Props {
  /** 当前列配置 */
  columns: ColumnConfig[];
}
const props = defineProps<Props>();
const emit = defineEmits<{
  'update:columns': [columns: ColumnConfig[]];
  reset: [];
}>();

function toggleVisible(key: string) {
  const cols = props.columns.map((c) =>
    c.key === key ? { ...c, visible: !c.visible } : c,
  );
  emit('update:columns', cols);
}

function moveUp(index: number) {
  if (index === 0) return;
  const cols = [...props.columns];
  [cols[index - 1]!, cols[index]!] = [cols[index]!, cols[index - 1]!];
  emit('update:columns', cols);
}

function moveDown(index: number) {
  if (index === props.columns.length - 1) return;
  const cols = [...props.columns];
  [cols[index]!, cols[index + 1]!] = [cols[index + 1]!, cols[index]!];
  emit('update:columns', cols);
}
</script>

<template>
  <Popover trigger="click" placement="bottomRight">
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
    <Button type="text" size="small">
      <IconifyIcon icon="ant-design:setting-outlined" />
      列设置
    </Button>
  </Popover>
</template>
