<script setup lang="ts">
import type { SlaLevel } from './sla-util';

/**
 * 处置看板 · 任务卡（单条工单）
 *
 * 字段（对齐原型 taskCard）：no(orderNo) / title / by(handler) /
 *   due(plannedAt 倒计时) / od(sla 警示色) / loop(loopTagName) / reopen(status===REOPENED)
 * SLA 警示色左边框：超期红 / 临期橙 / 正常绿 / 无排程灰
 * 选中态：蓝边框 + 浅蓝底 + ring（单源 selectedTask 联动）
 */
import type { HandlingApi } from '#/api/handling';

import { computed } from 'vue';

import { formatDueCountdown, SLA_COLOR } from './sla-util';

const props = defineProps<{
  /** 人员过滤（StaffHBar 点人联动）：非 null 时，handler 不匹配的卡片降透明 */
  handlerFilter?: null | string;
  selected: boolean;
  sla: SlaLevel;
  task: HandlingApi.OrderItem;
}>();

const emit = defineEmits<{
  (e: 'select', task: HandlingApi.OrderItem): void;
}>();

const dueText = computed(() => formatDueCountdown(props.task.plannedAt));
const isReopened = computed(() => props.task.status === 'REOPENED');
const slaColor = computed(() => SLA_COLOR[props.sla]);
/** StaffHBar 点人过滤：非匹配 handler 的卡片降透明（不阻断选中态） */
const dimmed = computed(
  () => !!props.handlerFilter && (props.task.handler ?? '') !== props.handlerFilter,
);

function onClick() {
  emit('select', props.task);
}
</script>

<template>
  <div
    class="group cursor-pointer rounded-[2px] border bg-white px-1.5 py-1 text-[10px] leading-tight transition-colors"
    :class="[
      selected
        ? 'border-[#1F4E79] bg-[#E6F7FF] ring-1 ring-[#1F4E79]'
        : 'border-[#E4E7ED] hover:border-[#1F4E79] hover:bg-gray-50',
      dimmed && 'opacity-40',
    ]"
    :style="{ borderLeft: `3px solid ${slaColor}` }"
    @click="onClick"
  >
    <!-- 行1：编号 + 类型 + 重开标 -->
    <div class="flex items-center gap-1">
      <span class="font-semibold text-[#1F4E79]">{{ task.orderNo }}</span>
      <span
        v-if="task.actionTypeLabel"
        class="rounded-[1px] bg-[#F0F2F5] px-1 text-[8.5px] text-[#595959]"
      >{{ task.actionTypeLabel }}</span>
      <span
        v-if="isReopened"
        class="rounded-[1px] bg-[#FFF1F0] px-1 text-[8.5px] text-[#FF4D4F]"
      >重开</span>
    </div>
    <!-- 行2：标题（截断） -->
    <div class="mt-0.5 truncate text-[#262626]">{{ task.title || '—' }}</div>
    <!-- 行3：处理人 · 倒计时 · 回路 -->
    <div class="mt-0.5 flex items-center gap-1 text-[8.5px] text-[#8C8C8C]">
      <span class="truncate">{{ task.handler || '未指派' }}</span>
      <span class="flex-none">·</span>
      <span :style="{ color: slaColor }" class="flex-none font-medium">{{ dueText }}</span>
      <span class="flex-none">·</span>
      <span class="truncate">{{ task.loopTagName }}</span>
    </div>
  </div>
</template>
