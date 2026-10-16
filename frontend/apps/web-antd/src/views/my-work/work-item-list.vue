<script lang="ts" setup>
/**
 * 我的工作——工作项清单（展示组件）
 *
 * 单项信息（IA §4.3）：工作目标、回路/装置、来源、进入清单的理由、当前
 * 阶段、负责人、计划/期限、证据时间、等待条件、一个主要下一步。
 * 优先级理由逐项显示（逾期/重开/SLA 等），不生成新评分。
 */
import type { PersonalWorkApi } from '#/api/personal-work';

import { Button, Tag, Tooltip } from 'ant-design-vue';

import { formatLocalTime } from '#/utils/format';

import {
  priorityReasonLabel,
  SOURCE_LABEL,
  stageLabel,
} from './use-my-work';

type WorkItem = PersonalWorkApi.WorkItem;

const props = defineProps<{
  /** 动作进行中的 workId 集合 */
  actionBusy: Record<string, boolean>;
  /** 是否可打开回路工作台（四角色；false 时入口降级为纯文本） */
  canOpenWb360: boolean;
  /** 当前用户是否可执行业务操作（观察角色隐藏认领/移交/取消关注按钮） */
  canOperate: boolean;
  items: WorkItem[];
  /** 当前用户稳定 ID（移交按钮仅负责人本人可见） */
  myUserId: null | string;
}>();

const emit = defineEmits<{
  claim: [item: WorkItem];
  handoff: [item: WorkItem];
  open: [item: WorkItem];
  removeResume: [item: WorkItem];
  unfollow: [item: WorkItem];
}>();

function fmt(ts: null | string | undefined): string {
  return ts ? formatLocalTime(ts, 'MM-DD HH:mm') : '—';
}

/** 主要下一步是否为本页直连动作（认领）；其余经深链进原业务操作 */
function isClaimAction(item: WorkItem): boolean {
  return item.nextAction.kind === 'claim_investigation';
}

/** 移交按钮可见性：本人负责的核查（IN_PROGRESS）且可操作 */
function canHandoff(item: WorkItem): boolean {
  return (
    props.canOperate &&
    item.sourceKind === 'investigation' &&
    item.stage === 'IN_PROGRESS' &&
    item.ownerId !== null &&
    item.ownerId === props.myUserId
  );
}

function showUnfollow(item: WorkItem): boolean {
  return props.canOperate && item.workKind === 'FOLLOW';
}

function showRemoveResume(item: WorkItem): boolean {
  return item.workKind === 'RESUME';
}
</script>

<template>
  <div class="work-item-list">
    <div
      v-for="item in items"
      :key="`${item.workKind}:${item.workId}`"
      class="work-item"
      :data-work-id="item.workId"
      :data-work-kind="item.workKind"
      :data-partition="item.partition ?? ''"
    >
      <div class="work-item__main">
        <div class="work-item__title-row">
          <span class="work-item__title" :title="item.title">{{ item.title }}</span>
          <Tag
            v-for="reason in item.priorityReasons"
            :key="reason"
            color="red"
            class="work-item__priority"
          >
            {{ priorityReasonLabel(reason) }}
          </Tag>
          <Tag v-if="item.stage" color="default">{{ stageLabel(item.stage) }}</Tag>
          <Tag v-if="item.responsibilityPending" color="orange">待确认责任</Tag>
        </div>
        <div class="work-item__meta">
          <span class="work-item__loop">{{ item.loopTagName ?? item.loopId }}</span>
          <span v-if="item.unitName" class="work-item__unit">{{ item.unitName }}</span>
          <span class="work-item__source">{{ SOURCE_LABEL[item.sourceKind] ?? item.sourceKind }}</span>
          <span v-if="item.ownerDisplayName" class="work-item__owner">
            负责人：{{ item.ownerDisplayName }}
          </span>
        </div>
        <div class="work-item__reason" :title="item.reason">{{ item.reason }}</div>
        <div v-if="item.waitReason" class="work-item__wait">
          等待原因：{{ item.waitReason }}
          <template v-if="item.waitReviewCondition">
            ；复查条件：{{ item.waitReviewCondition }}
          </template>
        </div>
        <div class="work-item__times">
          <Tooltip title="工作日期只用于计划/期限；证据时间为业务记录更新时间">
            <span v-if="item.plannedAt">计划：{{ fmt(item.plannedAt) }}</span>
            <span v-if="item.dueAt">期限：{{ fmt(item.dueAt) }}</span>
            <span>证据/更新：{{ fmt(item.updatedAt) }}</span>
          </Tooltip>
        </div>
      </div>
      <div class="work-item__actions">
        <Button
          v-if="isClaimAction(item) && canOperate"
          type="primary"
          size="small"
          :loading="!!actionBusy[item.workId]"
          @click="emit('claim', item)"
        >
          {{ item.nextAction.label }}
        </Button>
        <Button
          v-if="canOpenWb360"
          size="small"
          :disabled="!!actionBusy[item.workId]"
          @click="emit('open', item)"
        >
          {{ isClaimAction(item) && canOperate ? '查看' : item.nextAction.label }}
        </Button>
        <span v-else class="work-item__noentry">无回路工作台入口权限</span>
        <Button
          v-if="canHandoff(item)"
          size="small"
          :disabled="!!actionBusy[item.workId]"
          @click="emit('handoff', item)"
        >
          移交
        </Button>
        <Button
          v-if="showUnfollow(item)"
          size="small"
          :disabled="!!actionBusy[item.workId]"
          @click="emit('unfollow', item)"
        >
          取消关注
        </Button>
        <Button
          v-if="showRemoveResume(item)"
          size="small"
          :disabled="!!actionBusy[item.workId]"
          @click="emit('removeResume', item)"
        >
          删除记录
        </Button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.work-item-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.work-item {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  padding: 10px 12px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.work-item__main {
  min-width: 0;
  flex: 1;
}

.work-item__title-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.work-item__title {
  overflow: hidden;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.work-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 4px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.work-item__loop {
  font-family: ui-monospace, monospace;
  color: hsl(var(--foreground));
}

.work-item__reason {
  margin-top: 4px;
  overflow: hidden;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.work-item__wait {
  margin-top: 4px;
  font-size: 12px;
  color: hsl(var(--warning, 214 84% 40%));
}

.work-item__times {
  display: flex;
  gap: 10px;
  margin-top: 4px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.work-item__actions {
  display: flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
}

.work-item__noentry {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}
</style>
