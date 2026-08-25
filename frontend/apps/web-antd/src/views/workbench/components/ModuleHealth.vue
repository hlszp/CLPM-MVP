<script setup lang="ts">
/**
 * 系统总览 · 模块健康面板（原型对齐）
 *
 * 展示 4 个核心模块的运行状态卡片：
 * - 性能评估 / 回路诊断 / 参数整定 / 问题处置
 * - 每张卡片：模块名 + 版本 + 状态标签 + 简要描述
 * - 状态色：CORE 绿 / ENABLED 绿 / MAINTENANCE 橙 / UNINSTALLED 灰
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  plugins: WorkbenchApi.Plugin[];
}>();

const MODULE_KEYS = ['assess', 'diagnosis', 'tuning', 'handling'] as const;

const MODULE_NAMES: Record<string, string> = {
  assess: '性能评估',
  diagnosis: '回路诊断',
  tuning: '参数整定',
  handling: '问题处置',
};

const STATUS_COLORS: Record<WorkbenchApi.ModuleStatus, string> = {
  CORE: '#52C41A',
  ENABLED: '#52C41A',
  MAINTENANCE: '#FA8C16',
  UNINSTALLED: '#BFBFBF',
};

const STATUS_LABELS: Record<WorkbenchApi.ModuleStatus, string> = {
  CORE: '内置核心',
  ENABLED: '已启用',
  MAINTENANCE: '维护中',
  UNINSTALLED: '未安装',
};

const MODULES = computed(() =>
  MODULE_KEYS.map((key) => {
    const plugin = props.plugins.find((p) => p.module_key === key);
    return {
      key,
      name: MODULE_NAMES[key],
      version: plugin?.version ?? '—',
      status: plugin?.status ?? 'UNINSTALLED',
      is_core: plugin?.is_core ?? false,
    };
  }),
);
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[#E4E7ED] bg-white">
    <div class="flex items-center justify-between border-b border-[#E4E7ED] px-3 py-2">
      <span class="text-xs font-medium text-gray-700">
        模块健康 · 热插拔
      </span>
      <span class="text-[10px] text-gray-400">
        {{ plugins.filter(p => p.status === 'ENABLED' || p.status === 'CORE').length }} 在线
        ·
        {{ plugins.filter(p => p.status === 'MAINTENANCE').length }} 维护
      </span>
    </div>
    <div class="flex flex-1 flex-col gap-1.5 overflow-auto p-2">
      <div
        v-for="mod in MODULES"
        :key="mod.key"
        class="flex items-center justify-between rounded border border-[#EBEEF5] px-2 py-1.5"
        :class="{ 'border-orange-300 bg-orange-50': mod.status === 'MAINTENANCE' }"
      >
        <div class="flex flex-col gap-0.5">
          <div class="flex items-center gap-1.5">
            <span
              class="h-2 w-2 rounded-full"
              :style="{ backgroundColor: STATUS_COLORS[mod.status] }"
            ></span>
            <span class="text-xs font-medium text-gray-700">{{ mod.name }}</span>
          </div>
          <span class="text-[10px] text-gray-400">v{{ mod.version }}</span>
        </div>
        <span
          class="rounded px-1.5 py-0.5 text-[10px]"
          :style="{
            color: STATUS_COLORS[mod.status],
            backgroundColor: `${STATUS_COLORS[mod.status] }1A`,
          }"
        >
          {{ STATUS_LABELS[mod.status] }}
        </span>
      </div>
    </div>
  </div>
</template>
