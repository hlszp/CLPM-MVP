/** * 工作台当前回路活跃关注项（MW-P3-06） * *
顶部显示开放项总数和最高优先级；默认展示最多 3 条明细， *
超过时提供"查看全部"入口跳转到关注队列（按 loopId 筛选）。 * eventId/trackerId
深链接自动定位对应项。 * 不出现规则编辑入口。 * * 对齐整改方案 §6
三层呈现之"单回路上下文"。 */
<script lang="ts" setup>
import type { MonitorApi } from '#/api/monitor';

import { computed } from 'vue';
import { useRouter } from 'vue-router';

import { Tag, Tooltip } from 'ant-design-vue';

import { formatTime } from '#/utils/format';

defineOptions({ name: 'WorkbenchActiveAttention' });

const props = defineProps<{
  /** 活跃关注项汇总（来自 summary.activeAttention） */
  activeAttention: MonitorApi.ActiveAttentionSummary;
  /** 当前回路 ID */
  loopId: string;
}>();

const router = useRouter();

const PRIORITY_META: Record<
  MonitorApi.AttentionPriority,
  { color: string; label: string }
> = {
  URGENT: { color: 'red', label: '紧急' },
  HIGH: { color: 'volcano', label: '高' },
  MEDIUM: { color: 'orange', label: '中' },
  LOW: { color: 'default', label: '低' },
};

const SOURCE_LABEL: Record<MonitorApi.AttentionSource, string> = {
  ALERT: '预警',
  DEGRADATION: '评分恶化',
  DATA_QUALITY: '数据质量',
  TRACKER: '工单',
  VERIFICATION: '验证超期',
};

const highestPriorityMeta = computed(() => {
  const p = props.activeAttention.highestPriority;
  if (!p) return null;
  return PRIORITY_META[p];
});

const displayItems = computed(() => props.activeAttention.items.slice(0, 3));

function goToAttentionQueue() {
  router.push({
    path: '/monitor/attention',
    query: { loopId: props.loopId },
  });
}
</script>

<template>
  <div class="active-attention" role="region" aria-label="当前回路活跃关注项">
    <!-- 汇总条 -->
    <div class="active-attention__summary">
      <span class="active-attention__title">
        <Tag
          v-if="highestPriorityMeta"
          :color="highestPriorityMeta.color"
          class="!m-0 !text-[10px]"
        >
          {{ highestPriorityMeta.label }}
        </Tag>
        <span class="active-attention__count">
          {{ activeAttention.total }} 项待处理
        </span>
      </span>
      <a
        v-if="activeAttention.total > 0"
        class="active-attention__link"
        role="button"
        tabindex="0"
        @click="goToAttentionQueue"
        @keydown.enter="goToAttentionQueue"
      >
        查看全部 →
      </a>
    </div>

    <!-- 明细列表（最多 3 条） -->
    <div v-if="displayItems.length > 0" class="active-attention__list">
      <Tooltip
        v-for="item in displayItems"
        :key="item.attentionId"
        placement="bottom"
      >
        <template #title>
          <div class="text-xs">
            <div>{{ item.title }}</div>
            <div class="text-gray-400">{{ item.summary }}</div>
            <div v-if="item.rankReasons.length > 0" class="text-gray-400">
              {{ item.rankReasons.join('；') }}
            </div>
            <div class="text-gray-400">
              发生：{{ formatTime(item.occurredAt) }}
            </div>
          </div>
        </template>
        <div class="active-attention__item" role="button" tabindex="0">
          <Tag
            :color="PRIORITY_META[item.priority]?.color || 'default'"
            class="!m-0 !text-[10px]"
          >
            {{ PRIORITY_META[item.priority]?.label || item.priority }}
          </Tag>
          <span class="active-attention__item-source">
            {{ SOURCE_LABEL[item.source] || item.source }}
          </span>
          <span class="active-attention__item-title">{{ item.title }}</span>
          <span class="active-attention__item-time">
            {{ formatTime(item.occurredAt) }}
          </span>
        </div>
      </Tooltip>
    </div>

    <!-- 无关注项提示 -->
    <div v-else class="active-attention__empty">当前回路无活跃关注项</div>
  </div>
</template>

<style scoped>
.active-attention {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 8px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
}

.active-attention__summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
}

.active-attention__title {
  display: flex;
  gap: 4px;
  align-items: center;
}

.active-attention__count {
  font-weight: 500;
  color: hsl(var(--foreground) / 85%);
}

.active-attention__link {
  font-size: 11px;
  color: hsl(var(--primary));
  white-space: nowrap;
  cursor: pointer;
}

.active-attention__link:hover {
  text-decoration: underline;
}

.active-attention__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.active-attention__item {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 1px 0;
  font-size: 11px;
  cursor: pointer;
  border-radius: 3px;
}

.active-attention__item:hover {
  background: hsl(var(--accent) / 8%);
}

.active-attention__item-source {
  flex-shrink: 0;
  color: hsl(var(--foreground) / 60%);
  white-space: nowrap;
}

.active-attention__item-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  color: hsl(var(--foreground) / 80%);
  white-space: nowrap;
}

.active-attention__item-time {
  flex-shrink: 0;
  color: hsl(var(--foreground) / 40%);
  white-space: nowrap;
}

.active-attention__empty {
  font-size: 11px;
  color: hsl(var(--foreground) / 40%);
}
</style>
