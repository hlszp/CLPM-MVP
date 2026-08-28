<script lang="ts" setup>
/**
 * 待办详情弹窗（方案 11 §7 · 触发源：§4 待办行）
 *
 * 数据：GET /handling/orders/{id}（OrderDetail）。
 * 内容：问题摘要（title）+ 回路信息 + 当前状态 + 处置时间线
 * （创建/计划 → 开工 → 提交验证 → 验证结论，附执行反馈记录，只读）。
 * 无任何操作按钮。
 */
import type { HandlingApi } from '#/api/handling';

import { computed, ref, watch } from 'vue';

import { getHandlingOrderApi } from '#/api/handling';
import { formatLocalTime } from '#/utils/format';

import CockpitModal from './cockpit-modal.vue';

const props = withDefaults(
  defineProps<{ open?: boolean; orderId?: null | string }>(),
  { open: false, orderId: null },
);

const emit = defineEmits<{ close: [] }>();

const loading = ref(false);
const failed = ref(false);
const order = ref<HandlingApi.OrderDetail | null>(null);

async function load() {
  if (!props.orderId) return;
  loading.value = true;
  failed.value = false;
  order.value = null;
  try {
    order.value = await getHandlingOrderApi(props.orderId);
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.open, props.orderId],
  ([open]) => {
    if (open) load();
  },
  { immediate: true },
);

/** 处置时间线节点（只读，按发生顺序；未发生的节点不显示） */
const timeline = computed(() => {
  const o = order.value;
  if (!o) return [];
  const nodes: { at: string; label: string; note?: string }[] = [];
  if (o.plannedAt) {
    nodes.push({
      at: o.plannedAt,
      label: '创建/计划',
      note: o.plannedBy ? `计划人 ${o.plannedBy}` : undefined,
    });
  }
  if (o.startedAt) {
    nodes.push({
      at: o.startedAt,
      label: '开工处置',
      note: o.handler ? `处置人 ${o.handler}` : undefined,
    });
  }
  for (const fb of o.feedbackLog ?? []) {
    nodes.push({ at: fb.at, label: '执行反馈', note: `${fb.by}：${fb.content}` });
  }
  if (o.submittedAt) nodes.push({ at: o.submittedAt, label: '提交验证' });
  if (o.verifiedAt) {
    nodes.push({
      at: o.verifiedAt,
      label: `验证结论：${o.verifyResult === 'EFFECTIVE' ? '有效' : (o.verifyResult === 'INEFFECTIVE' ? '无效' : '—')}`,
      note: o.verifyNote ?? undefined,
    });
  }
  return nodes;
});

const STATUS_CLASS: Record<string, string> = {
  CLOSED: 'st-closed',
  EXECUTING: 'st-executing',
  PENDING: 'st-pending',
  REOPENED: 'st-reopened',
  VERIFYING: 'st-verifying',
};

const statusClass = computed(() =>
  order.value ? (STATUS_CLASS[order.value.status] ?? '') : '',
);
</script>

<template>
  <CockpitModal
    :open="open"
    :title="`处置待办详情 · ${order?.orderNo ?? ''}`"
    @close="emit('close')"
  >
    <div v-if="loading" class="td__state">加载中…</div>
    <div v-else-if="failed || !order" class="td__state">
      工单详情加载失败
    </div>

    <div v-else class="td">
      <!-- 问题摘要 + 状态 -->
      <div class="td__head">
        <div class="td__title">{{ order.title }}</div>
        <span class="td__status" :class="statusClass">{{ order.statusLabel }}</span>
      </div>

      <!-- 回路信息 -->
      <div class="td__meta">
        <div class="td__meta-item">
          <span class="k">回路</span>
          <span class="v mono">{{ order.loopTagName }}</span>
        </div>
        <div v-if="order.loopDescription" class="td__meta-item">
          <span class="k">名称</span>
          <span class="v">{{ order.loopDescription }}</span>
        </div>
        <div v-if="order.unitPath" class="td__meta-item">
          <span class="k">装置</span>
          <span class="v">{{ order.unitPath }}</span>
        </div>
        <div class="td__meta-item">
          <span class="k">处置类型</span>
          <span class="v">{{ order.actionTypeLabel ?? order.actionType }}</span>
        </div>
        <div class="td__meta-item">
          <span class="k">来源</span>
          <span class="v">{{ order.source === 'DIAGNOSIS' ? '诊断转化' : '手动新建' }}</span>
        </div>
        <div v-if="order.handler" class="td__meta-item">
          <span class="k">处置人</span>
          <span class="v">{{ order.handler }}</span>
        </div>
      </div>

      <!-- KPI 前后对比（VERIFYING/CLOSED 固化快照，只读） -->
      <div
        v-if="order.kpiBefore?.score !== null && order.kpiBefore?.score !== undefined && order.kpiAfter?.score !== null && order.kpiAfter?.score !== undefined"
        class="td__kpi"
      >
        KPI 评分：{{ order.kpiBefore!.score!.toFixed(1) }} →
        {{ order.kpiAfter!.score!.toFixed(1) }}
        <span
          :class="(order.kpiAfter!.score! - order.kpiBefore!.score!) >= 0 ? 'up' : 'down'"
        >
          （{{ (order.kpiAfter!.score! - order.kpiBefore!.score!) >= 0 ? '+' : ''
          }}{{ (order.kpiAfter!.score! - order.kpiBefore!.score!).toFixed(1) }}）
        </span>
      </div>

      <!-- 处置时间线 -->
      <div class="td__sec-hd">处置时间线</div>
      <div v-if="timeline.length === 0" class="td__state">暂无处置记录</div>
      <div v-else class="td__timeline">
        <div v-for="(n, i) in timeline" :key="i" class="td__node">
          <span class="td__node-dot"></span>
          <div class="td__node-body">
            <div class="td__node-label">{{ n.label }}</div>
            <div v-if="n.note" class="td__node-note">{{ n.note }}</div>
          </div>
          <span class="td__node-at">{{ formatLocalTime(n.at, 'MM-DD HH:mm') }}</span>
        </div>
      </div>
    </div>
  </CockpitModal>
</template>

<style scoped>
.td__state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 160px;
  font-size: 12px;
  color: var(--ck-text-3);
}

.td__head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--ck-border);
}

.td__title {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--ck-text);
}

.td__status {
  flex: none;
  padding: 2px 10px;
  font-size: 12px;
  color: var(--ck-text-2);
  background: var(--ck-panel-3);
  border-radius: 999px;
}

.td__status.st-pending {
  color: var(--ck-grade-fair);
}

.td__status.st-executing {
  color: var(--ck-accent);
}

.td__status.st-verifying {
  color: var(--ck-grade-good);
}

.td__status.st-closed {
  color: var(--ck-grade-excellent);
}

.td__status.st-reopened {
  color: var(--ck-grade-warning);
}

.td__meta {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px 16px;
  margin-top: 12px;
}

.td__meta-item {
  display: flex;
  gap: 8px;
  font-size: 12px;
}

.td__meta-item .k {
  flex: none;
  color: var(--ck-text-3);
}

.td__meta-item .v {
  overflow: hidden;
  color: var(--ck-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mono {
  font-variant-numeric: tabular-nums;
}

.td__kpi {
  margin-top: 12px;
  padding: 8px 12px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text);
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
}

.td__kpi .up {
  color: var(--ck-grade-excellent);
}

.td__kpi .down {
  color: var(--ck-grade-warning);
}

.td__sec-hd {
  margin: 14px 0 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ck-text);
}

.td__timeline {
  display: flex;
  flex-direction: column;
}

.td__node {
  position: relative;
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr) auto;
  gap: 10px;
  padding: 6px 0;
}

.td__node:not(:last-child)::before {
  position: absolute;
  top: 22px;
  bottom: -6px;
  left: 6px;
  width: 1px;
  content: '';
  background: var(--ck-border-2);
}

.td__node-dot {
  z-index: 1;
  width: 8px;
  height: 8px;
  margin-top: 5px;
  background: var(--ck-accent);
  border-radius: 50%;
}

.td__node-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--ck-text);
}

.td__node-note {
  margin-top: 2px;
  font-size: 11px;
  line-height: 1.6;
  color: var(--ck-text-2);
  white-space: pre-wrap;
}

.td__node-at {
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text-3);
}
</style>
