<script setup lang="ts">
/**
 * 任务详情 · 紧凑卡（单源 selectedTask 联动）
 *
 * 决策 B 选项 2：工作台右侧内嵌紧凑卡，单源 selectedTask 联动；
 *   「📦 查看完整工单」→ emit open-drawer(orderId) 开既有 order-detail-drawer.vue 兜底
 *
 * 视觉：
 *   关键字段 grid + 流转时间线 4 节点（排程/开工/提交/验证）+ KPI 前后对比 + 完整工单入口
 *   无 task → 空态提示
 */
import type { HandlingApi } from '#/api/handling';

import { computed } from 'vue';

import { formatLocalTime } from '#/utils/format';

import HelpBubble from '../HelpBubble.vue';

const props = defineProps<{
  kpiCompare: HandlingApi.KpiComparison | null;
  task: HandlingApi.OrderItem | null;
}>();

const emit = defineEmits<{
  (e: 'openDrawer', orderId: string): void;
}>();

const helpItems = [
  { label: '紧凑卡', text: '工作台右侧内嵌任务详情紧凑卡，单源 selectedTask 联动。' },
  { label: '完整工单', text: '「📦 查看完整工单」打开既有工单详情抽屉（全量字段 + 流转操作）。' },
  { label: 'KPI 对比', text: '处置前后 KPI 评分对比（仅 VERIFYING/CLOSED 工单有数据；其余隐藏不阻断）。' },
];

interface TimelineNode {
  color: string;
  label: string;
  time: null | string | undefined;
}

const STATUS_COLOR: Record<HandlingApi.OrderStatus, string> = {
  CANCELLED: '#8C8C8C',
  CLOSED: '#52C41A',
  EXECUTING: '#1890FF',
  PENDING: '#FA8C16',
  REOPENED: '#FF4D4F',
  VERIFYING: '#722ED1',
};

const timeline = computed<TimelineNode[]>(() => {
  const t = props.task;
  if (!t) return [];
  return [
    { color: '#FA8C16', label: '排程', time: t.plannedAt },
    { color: '#1890FF', label: '开工', time: t.startedAt },
    { color: '#722ED1', label: '提交验证', time: t.submittedAt },
    { color: '#52C41A', label: '验证闭环', time: t.verifiedAt },
  ];
});

const statusColor = computed(() => {
  if (!props.task) return '#8C8C8C';
  return STATUS_COLOR[props.task.status] ?? '#8C8C8C';
});

const kpiBeforeScore = computed(
  () => props.kpiCompare?.kpiBefore?.score ?? null,
);
const kpiAfterScore = computed(
  () => props.kpiCompare?.kpiAfter?.score ?? null,
);
const kpiDelta = computed(() => {
  const b = kpiBeforeScore.value;
  const a = kpiAfterScore.value;
  if (b === null || a === null) return null;
  return Math.round((a - b) * 10) / 10;
});
const showKpi = computed(
  () => kpiBeforeScore.value !== null || kpiAfterScore.value !== null,
);

function fmt(ts: null | string | undefined): string {
  return formatLocalTime(ts, 'MM-DD HH:mm');
}

function openDrawer() {
  if (props.task) emit('openDrawer', props.task.id);
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div
      class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]"
    >
      <span
        class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#1F4E79]"
      ></span>
      任务详情
      <HelpBubble
        :size="12"
        theme="blue"
        title="任务详情说明"
        :items="helpItems"
        class="ml-1"
      />
      <template v-if="task">
        <span class="ml-auto text-[9.5px] font-normal text-[#595959]">
          {{ task.orderNo }}
        </span>
        <span
          class="ml-[4px] rounded-[1px] px-[4px] text-[8.5px] font-medium text-white"
          :style="{ background: statusColor }"
        >{{ task.statusLabel }}</span>
      </template>
    </div>
    <div class="min-h-0 flex-1 overflow-y-auto p-[6px_8px]">
      <div
        v-if="!task"
        class="flex h-full items-center justify-center text-[10px] text-[#BFBFBF]"
      >
        选择左侧工单查看详情
      </div>
      <template v-else>
        <!-- 关键字段 grid -->
        <div class="grid grid-cols-[auto_1fr] gap-x-[6px] gap-y-[3px] text-[10px]">
          <span class="text-[#8C8C8C]">标题</span>
          <span class="truncate text-[#262626]" :title="task.title">{{ task.title || '—' }}</span>
          <span class="text-[#8C8C8C]">回路</span>
          <span class="truncate text-[#595959]">{{ task.loopTagName }}</span>
          <span class="text-[#8C8C8C]">类型</span>
          <span class="text-[#595959]">{{ task.actionTypeLabel || task.actionType }}</span>
          <span class="text-[#8C8C8C]">处理人</span>
          <span class="text-[#595959]">{{ task.handler || '未指派' }}</span>
          <span class="text-[#8C8C8C]">排程</span>
          <span class="tabular-nums text-[#595959]">{{ formatLocalTime(task.plannedAt) }}</span>
          <span class="text-[#8C8C8C]">更新</span>
          <span class="tabular-nums text-[#595959]">{{ formatLocalTime(task.updatedAt) }}</span>
        </div>

        <!-- 流转时间线 -->
        <div class="mt-[6px] border-t border-[#F0F2F5] pt-[5px]">
          <div class="mb-[3px] text-[9.5px] font-medium text-[#1F4E79]">流转时间线</div>
          <div class="relative flex flex-col gap-[5px] pl-[12px]">
            <div class="absolute left-[3px] top-[4px] bottom-[4px] w-px bg-[#E4E7ED]"></div>
            <div
              v-for="node in timeline"
              :key="node.label"
              class="relative flex items-center"
            >
              <span
                class="absolute -left-[12px] inline-block h-[6px] w-[6px] rounded-full ring-2 ring-white"
                :style="{ background: node.time ? node.color : '#BFBFBF' }"
              ></span>
              <span class="flex-none text-[9.5px] text-[#8C8C8C]">{{ node.label }}</span>
              <span
                class="ml-auto tabular-nums text-[9.5px]"
                :style="{ color: node.time ? '#595959' : '#BFBFBF' }"
              >{{ node.time ? fmt(node.time) : '—' }}</span>
            </div>
          </div>
        </div>

        <!-- KPI 前后对比 -->
        <div
          v-if="showKpi"
          class="mt-[6px] border-t border-[#F0F2F5] pt-[5px]"
        >
          <div class="mb-[3px] text-[9.5px] font-medium text-[#1F4E79]">KPI 前后对比</div>
          <div class="flex items-center gap-[6px] text-[10px]">
            <div class="flex flex-1 flex-col items-center rounded-[1px] bg-[#FAFBFC] py-[2px]">
              <span class="text-[8.5px] text-[#8C8C8C]">处置前</span>
              <span class="font-bold tabular-nums text-[#595959]">{{ kpiBeforeScore ?? '—' }}</span>
            </div>
            <span class="text-[#8C8C8C]">→</span>
            <div class="flex flex-1 flex-col items-center rounded-[1px] bg-[#FAFBFC] py-[2px]">
              <span class="text-[8.5px] text-[#8C8C8C]">处置后</span>
              <span class="font-bold tabular-nums text-[#595959]">{{ kpiAfterScore ?? '—' }}</span>
            </div>
            <div class="flex flex-1 flex-col items-center rounded-[1px] py-[2px]">
              <span class="text-[8.5px] text-[#8C8C8C]">变化</span>
              <span
                class="font-bold tabular-nums"
                :style="{ color: kpiDelta === null ? '#BFBFBF' : (kpiDelta >= 0 ? '#52C41A' : '#FF4D4F') }"
              >{{ kpiDelta === null ? '—' : `${kpiDelta >= 0 ? '+' : ''}${kpiDelta}` }}</span>
            </div>
          </div>
        </div>

        <!-- 完整工单入口 -->
        <button
          class="mt-[6px] w-full rounded-[2px] bg-[#1F4E79] py-[3px] text-[10px] font-medium text-white transition-opacity hover:opacity-90"
          @click="openDrawer"
        >
          📦 查看完整工单
        </button>
      </template>
    </div>
  </div>
</template>
