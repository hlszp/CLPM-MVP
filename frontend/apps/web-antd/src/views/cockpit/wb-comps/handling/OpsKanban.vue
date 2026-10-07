<script setup lang="ts">
/**
 * 处置看板 · 4 泳道容器
 *
 * 待办(PENDING+REOPENED) / 处理中(EXECUTING) / 验证中(VERIFYING) / 已闭环(CLOSED)
 * 等宽并排，分区内滚动；laneFilter 命中道 active=true（总览漏斗联动）
 * laneCounts 可覆盖徽章计数（如闭环道用 statistics 真实总数，而非 fetched cap）
 */
import type { HandlingApi } from '#/api/handling';

import { computed } from 'vue';

import LaneCol from './LaneCol.vue';

type LaneKey = 'closed' | 'executing' | 'pending' | 'verifying';

interface KanbanLanes {
  pending: HandlingApi.OrderItem[];
  executing: HandlingApi.OrderItem[];
  verifying: HandlingApi.OrderItem[];
  closed: HandlingApi.OrderItem[];
}

const props = defineProps<{
  /** 人员过滤透传（StaffHBar 点人 → LaneCol → TaskCard 降透明） */
  handlerFilter?: null | string;
  /** 徽章计数覆盖（缺省取 lanes[key].length） */
  laneCounts?: Partial<Record<LaneKey, number>> | undefined;
  laneFilter: HandlingApi.OrderStatus | null;
  lanes: KanbanLanes;
  selectedTaskId: null | string;
}>();

const emit = defineEmits<{
  (e: 'select', task: HandlingApi.OrderItem): void;
}>();

interface LaneCfg {
  color: string;
  key: LaneKey;
  statuses: HandlingApi.OrderStatus[];
  title: string;
}

/** 4 道配置；statuses 用于漏斗 laneFilter 命中判断（待办道含 PENDING+REOPENED） */
const LANE_CFG: readonly LaneCfg[] = [
  { key: 'pending', title: '待办', color: '#FA8C16', statuses: ['PENDING', 'REOPENED'] },
  { key: 'executing', title: '处理中', color: '#1890FF', statuses: ['EXECUTING'] },
  { key: 'verifying', title: '验证中', color: '#722ED1', statuses: ['VERIFYING'] },
  { key: 'closed', title: '已闭环', color: '#52C41A', statuses: ['CLOSED'] },
] as const;

const cols = computed(() =>
  LANE_CFG.map((cfg) => {
    const tasks = props.lanes[cfg.key];
    return {
      active: props.laneFilter !== null && cfg.statuses.includes(props.laneFilter),
      color: cfg.color,
      count: props.laneCounts?.[cfg.key] ?? tasks.length,
      key: cfg.key,
      tasks,
      title: cfg.title,
    };
  }),
);
</script>

<template>
  <div class="flex h-full min-h-0 w-full flex-1 gap-1">
    <LaneCol
      v-for="col in cols"
      :key="col.key"
      :lane-key="col.key"
      :title="col.title"
      :color="col.color"
      :count="col.count"
      :tasks="col.tasks"
      :selected-task-id="selectedTaskId"
      :handler-filter="handlerFilter"
      :active="col.active"
      @select="emit('select', $event)"
    />
  </div>
</template>
