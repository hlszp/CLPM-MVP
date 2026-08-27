<script setup lang="ts">
import type { WorkbenchApi } from '#/api/workbench';

/**
 * 模块 4 态状态点（方案 §5.1 F-GL-05：Tab 三色 dot）
 *
 * 三色映射（工业状态色规范 §0.3）：
 *   绿 = 运行（CORE 内置 / ENABLED 在线）
 *   橙 = 维护中（MAINTENANCE，配合 ModuleVeil/Banner）
 *   灰 = 未安装（UNINSTALLED）
 */
import { computed } from 'vue';

const props = defineProps<{
  size?: number;
  status: WorkbenchApi.ModuleStatus;
}>();

const COLOR: Record<WorkbenchApi.ModuleStatus, string> = {
  CORE: '#52C41A',
  ENABLED: '#52C41A',
  MAINTENANCE: '#FA8C16',
  UNINSTALLED: '#BFBFBF',
};

const LABEL: Record<WorkbenchApi.ModuleStatus, string> = {
  CORE: '内置',
  ENABLED: '在线',
  MAINTENANCE: '维护中',
  UNINSTALLED: '未安装',
};

const color = computed(() => COLOR[props.status] ?? '#BFBFBF');
const label = computed(() => LABEL[props.status] ?? '未知');
</script>

<template>
  <span
    class="inline-block flex-none rounded-full"
    :style="{
      width: `${size ?? 8}px`,
      height: `${size ?? 8}px`,
      backgroundColor: color,
    }"
    :title="label"
  ></span>
</template>
