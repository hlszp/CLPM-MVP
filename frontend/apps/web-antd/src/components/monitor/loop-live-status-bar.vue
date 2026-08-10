/** * 回路实时状态条（MW-P1-05） * * 显示选中回路的实时 PV/SP/OP/MODE、WS
连接状态、最后采样时间和 PV 质量码。 * - WS 消息到达后局部更新（≤2 秒延迟） * -
连接三态：online（在线）/ reconnecting（重连中）/ offline（离线） * -
离线时显示"数据延迟"提示（而非红色错误） * - dataFreshness 由 summary
返回，前端不复制停滞阈值 * * 对齐整改方案 §7.1 回路状态条 / §9.2 实时数据。 */
<script lang="ts" setup>
import type { LoopApi } from '#/api/loop';
import type { ConnectionStatus } from '#/utils/realtime-ws';

import { computed } from 'vue';

import { Tag } from 'ant-design-vue';

import { formatTime } from '#/utils/format';

defineOptions({ name: 'LoopLiveStatusBar' });

const props = defineProps<{
  /** WS 连接状态 */
  connectionStatus: ConnectionStatus;
  /** 服务端数据新鲜度状态（由 summary 返回，Phase 3 接入） */
  dataFreshness?: null | {
    reason?: null | string;
    status: 'DELAYED' | 'FRESH' | 'UNKNOWN';
    thresholdSeconds?: number;
  };
  /** 最近一次 WS 消息到达时间 */
  lastMessageAt: Date | null;
  /** 当前选中回路的监控列表项（含 currentValues） */
  loop?: LoopApi.MonitorListItem | null;
}>();

// ===== 连接状态显示 =====
const connectionTag = computed(() => {
  switch (props.connectionStatus) {
    case 'online': {
      return { color: 'green', text: '在线' };
    }
    case 'reconnecting': {
      return { color: 'orange', text: '重连中' };
    }
    default: {
      return { color: 'default', text: '离线' };
    }
  }
});

// ===== PV 质量码显示 =====
const qualityTag = computed(() => {
  const q = props.loop?.currentValues?.pvQuality;
  if (!q) return null;
  switch (q) {
    case 'BAD': {
      return { color: 'red', text: 'Bad' };
    }
    case 'GOOD': {
      return { color: 'green', text: 'Good' };
    }
    default: {
      return { color: 'orange', text: 'Uncertain' };
    }
  }
});

// ===== 实时值格式化 =====
function formatValue(
  value: null | number | undefined,
  unit?: null | string,
): string {
  if (value == null) return '—';
  return `${value}${unit ? ` ${unit}` : ''}`;
}

// ===== 采样时间显示 =====
const readAtText = computed(() => {
  const readAt = props.loop?.currentValues?.readAt;
  if (!readAt) return '—';
  return formatTime(readAt);
});

// ===== 数据新鲜度提示 =====
const freshnessText = computed(() => {
  if (!props.dataFreshness) return null;
  switch (props.dataFreshness.status) {
    case 'DELAYED': {
      return props.dataFreshness.reason || '数据延迟';
    }
    case 'FRESH': {
      return null; // 新鲜不显示提示
    }
    default: {
      return null;
    }
  }
});
</script>

<template>
  <div
    class="flex items-center gap-3 rounded border bg-white px-3 py-1.5 text-xs"
    role="status"
    :aria-label="`回路实时状态：${connectionTag.text}`"
  >
    <!-- 连接状态 -->
    <Tag :color="connectionTag.color" class="!m-0 !text-[11px]">
      {{ connectionTag.text }}
    </Tag>

    <!-- 回路位号 -->
    <span v-if="loop" class="font-medium text-gray-700">{{
      loop.tagName
    }}</span>

    <!-- PV/SP/OP/MODE 实时值 -->
    <template v-if="loop?.currentValues">
      <span class="text-gray-600">
        PV
        <span class="font-medium">{{
          formatValue(loop.currentValues.pv, loop.pvUnit)
        }}</span>
      </span>
      <span class="text-gray-600">
        SP
        <span class="font-medium">{{
          formatValue(loop.currentValues.sp, loop.pvUnit)
        }}</span>
      </span>
      <span class="text-gray-600">
        OP
        <span class="font-medium">{{
          formatValue(loop.currentValues.op, loop.opUnit)
        }}</span>
      </span>
      <span class="text-gray-600">
        模式
        <span class="font-medium">{{
          loop.currentValues.modeLabel || '—'
        }}</span>
      </span>
    </template>

    <!-- PV 质量码 -->
    <Tag v-if="qualityTag" :color="qualityTag.color" class="!m-0 !text-[11px]">
      {{ qualityTag.text }}
    </Tag>

    <!-- 采样时间 -->
    <span class="text-gray-400"> 采样 {{ readAtText }} </span>

    <!-- 数据延迟提示（非红色，对齐 UI/UX 规范不用红色表示普通陈旧） -->
    <span v-if="freshnessText" class="text-amber-600">
      {{ freshnessText }}
    </span>
  </div>
</template>
