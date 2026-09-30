<script setup lang="ts">
/**
 * 系统总览 · 预警事件时间线（原型对齐）
 *
 * 0930 数据源修正：接预警规则引擎真实事件（alert_event，经 A-01 的
 * alert_events 块下发）——此前接的是 diagnosis_run 检出标签（诊断链），
 * 用户在"有预警事件"的前提下看到空白，属设计位错接。
 * 每条：规则编号 + 回路位号 + 严重度 + 触发时间（真实时间）。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

import { formatLocalTime } from '#/utils/format';

const props = defineProps<{
  alertEvents?: WorkbenchApi.AlertEventItem[];
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

const STATUS_LABELS: Record<string, string> = {
  ACTIVE: '未决',
  ACKNOWLEDGED: '已确认',
  RESOLVED: '已解除',
};

const events = computed(() => {
  if (!props.alertEvents?.length) return [];
  return props.alertEvents.map((e) => ({
    ...e,
    color: SEVERITY_COLORS[e.severity ?? 'INFO'] ?? SEVERITY_COLORS.INFO!,
    label: SEVERITY_LABELS[e.severity ?? 'INFO'] ?? e.severity,
    statusLabel: e.status ? (STATUS_LABELS[e.status] ?? e.status) : '',
  }));
});
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
        :key="evt.id"
        class="flex items-start gap-2 rounded border border-[#EBEEF5] px-2 py-1.5"
      >
        <span
          class="mt-1 h-2 w-2 flex-none rounded-full"
          :style="{ backgroundColor: evt.color }"
        ></span>
        <div class="flex flex-1 flex-col gap-0.5">
          <div class="flex items-center gap-1">
            <span class="text-xs font-medium text-gray-700">
              {{ evt.rule_code }}
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
            <span
              v-if="evt.statusLabel"
              class="rounded bg-gray-100 px-1 py-0.5 text-[9px] text-gray-500"
            >
              {{ evt.statusLabel }}
            </span>
          </div>
          <span class="text-[10px] text-gray-400">
            触发值 {{ evt.triggered_value ?? '—' }}
          </span>
        </div>
        <span class="flex-none text-[10px] text-gray-400">
          {{ formatLocalTime(evt.triggered_at, 'MM-DD HH:mm') }}
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
