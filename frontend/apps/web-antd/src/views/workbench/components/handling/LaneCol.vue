<script setup lang="ts">
import type { SlaLevel } from './sla-util';

/**
 * 处置看板 · 单泳道列
 *
 * LaneHeader（色点 + 标题 + 计数徽章）+ 卡片列 overflow-y-auto
 * active=true 时整列高亮（漏斗 laneFilter 命中 / StaffHBar 点人过滤）
 * sla 预计算到 items computed，避免每次选中变更逐卡重算（AGENTS naive datetime 规避）
 */
import type { HandlingApi } from '#/api/handling';

import { computed } from 'vue';

import { computeSla } from './sla-util';
import TaskCard from './TaskCard.vue';

const props = defineProps<{
  active: boolean;
  color: string;
  count: number;
  /** 人员过滤透传（StaffHBar 点人 → TaskCard 降透明） */
  handlerFilter?: null | string;
  laneKey: string;
  selectedTaskId: null | string;
  tasks: HandlingApi.OrderItem[];
  title: string;
}>();

const emit = defineEmits<{
  (e: 'select', task: HandlingApi.OrderItem): void;
}>();

interface SlatedTask {
  sla: SlaLevel;
  task: HandlingApi.OrderItem;
}

/** tasks 变化时一次性预算 SLA，选中变更不触发重算 */
const items = computed<SlatedTask[]>(() =>
  props.tasks.map((t) => ({ sla: computeSla(t.plannedAt), task: t })),
);
</script>

<template>
  <div
    class="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[2px] border bg-white"
    :class="active ? 'border-[#1F4E79] ring-1 ring-[#1F4E79]' : 'border-[#E4E7ED]'"
  >
    <!-- LaneHeader -->
    <div
      class="flex h-[24px] flex-none items-center border-b border-[#E4E7ED] px-2 text-[10.5px] font-semibold"
      :style="{ color }"
    >
      <span
        class="mr-1.5 inline-block h-[8px] w-[8px] rounded-[1px]"
        :style="{ background: color }"
      ></span>
      <span>{{ title }}</span>
      <span
        class="ml-1.5 inline-flex h-[14px] min-w-[16px] items-center justify-center rounded-[8px] px-1 text-[9px] text-white"
        :style="{ background: color }"
      >{{ count }}</span>
    </div>
    <!-- 卡片列（分区内滚动） -->
    <div class="min-h-0 flex-1 overflow-y-auto p-1">
      <div v-if="items.length > 0" class="flex flex-col" style="gap: 3px">
        <TaskCard
          v-for="it in items"
          :key="it.task.id"
          :task="it.task"
          :selected="it.task.id === selectedTaskId"
          :sla="it.sla"
          :handler-filter="handlerFilter"
          @select="emit('select', $event)"
        />
      </div>
      <div
        v-else
        class="flex h-full items-center justify-center text-[9.5px] text-[#BFBFBF]"
      >
        暂无{{ title }}工单
      </div>
    </div>
  </div>
</template>
