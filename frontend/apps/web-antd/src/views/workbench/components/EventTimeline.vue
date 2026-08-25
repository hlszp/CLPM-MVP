<script setup lang="ts">
/**
 * 系统总览 · 预警事件时间线（原型对齐）
 *
 * 展示近 24h 的预警/诊断事件列表：
 * - 使用 roots 数据（top N 根因标签）作为事件源
 * - 严重度：CRITICAL 红 / ERROR 橙 / WARN 黄 / INFO 灰
 * - 每条：时间 + 标签名 + 描述 + 严重度 dot
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  roots?: WorkbenchApi.RootRow[];
}>();

const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: '#FF4D4F',
  ERROR: '#FA8C16',
  WARN: '#FAAD14',
  INFO: '#BFBFBF',
};

const SEVERITY_LABELS: Record<string, string> = {
  CRITICAL: '严重',
  ERROR: '错误',
  WARN: '警告',
  INFO: '提示',
};

const events = computed(() => {
  if (!props.roots?.length) return [];
  const now = new Date();
  return props.roots.slice(0, 6).map((r, i) => ({
    ...r,
    time: new Date(now.getTime() - i * 3_600_000 * 2), // 每 2 小时一条
    color: SEVERITY_COLORS[r.severity ?? 'INFO'],
    label: SEVERITY_LABELS[r.severity ?? 'INFO'],
  }));
});

function formatTime(d: Date) {
  return d.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[#E4E7ED] bg-white">
    <div class="flex items-center justify-between border-b border-[#E4E7ED] px-3 py-2">
      <span class="text-xs font-medium text-gray-700">预警事件</span>
      <span class="text-[10px] text-gray-400">近 24h · {{ events.length }} 条</span>
    </div>
    <div class="flex flex-1 flex-col gap-0.5 overflow-auto p-2">
      <div
        v-for="evt in events"
        :key="evt.tag_code"
        class="flex items-start gap-2 rounded border border-[#EBEEF5] px-2 py-1.5"
      >
        <span
          class="mt-1 h-2 w-2 flex-none rounded-full"
          :style="{ backgroundColor: evt.color }"
        ></span>
        <div class="flex flex-1 flex-col gap-0.5">
          <div class="flex items-center gap-1">
            <span class="text-xs font-medium text-gray-700">
              {{ evt.tag_name }}
            </span>
            <span
              class="rounded px-1 py-0.5 text-[9px]"
              :style="{
                color: evt.color,
                backgroundColor: `${evt.color }1A`,
              }"
            >
              {{ evt.label }}
            </span>
          </div>
          <span class="text-[10px] text-gray-400">
            {{ evt.count }} 条次 · 活跃 {{ evt.active_count }}
          </span>
        </div>
        <span class="flex-none text-[10px] text-gray-400">
          {{ formatTime(evt.time) }}
        </span>
      </div>
      <div
        v-if="events.length === 0"
        class="flex flex-1 items-center justify-center text-xs text-gray-400"
      >
        暂无预警事件
      </div>
    </div>
  </div>
</template>
