<script lang="ts" setup>
/**
 * 整定批次 · 详情抽屉（追溯矩阵 GAP-2b）
 *
 * 批次信息 + 前置工单摘要 + 关联整定记录列表（N:M）+
 * scatters 前后对比简单展示（按 loop_id 配对，Δ=after-before）。
 */
import type { TuningApi } from '#/api/tuning';

import { computed, ref, watch } from 'vue';

import {
  Descriptions,
  DescriptionsItem,
  Drawer,
  Empty,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';

import { getTuningBatchDetailApi } from '#/api/tuning';

import { fmtNum2, tuningAlgoLabel } from '../constants';

const props = defineProps<{ batchId: null | number; visible: boolean }>();
const emit = defineEmits<{ 'update:visible': [boolean] }>();

const loading = ref(false);
const detail = ref<null | TuningApi.TuningBatchDetail>(null);

// 批次状态色点（与列表视图同口径）
const BATCH_STATUS_META: Record<string, { color: string; label: string }> = {
  BLOCKED: { color: 'error', label: '阻塞' },
  PENDING: { color: 'default', label: '待启动' },
  READY: { color: 'processing', label: '就绪' },
  RUNNING: { color: 'cyan', label: '执行中' },
  COMPLETED: { color: 'success', label: '已完成' },
  CANCELLED: { color: 'default', label: '已取消' },
};

// 前置工单状态中文标签
const ORDER_STATUS_LABEL: Record<string, string> = {
  PENDING: '待处理',
  EXECUTING: '执行中',
  VERIFYING: '验证中',
  CLOSED: '已闭环',
  CANCELLED: '已取消',
};

const recordColumns = [
  { dataIndex: 'tagName', key: 'tagName', title: '回路位号', width: 140 },
  { dataIndex: 'algorithm', key: 'algorithm', title: '算法', width: 140 },
  { dataIndex: 'fittingScore', key: 'fittingScore', title: '拟合度', width: 90 },
  { dataIndex: 'status', key: 'status', title: '状态', width: 100 },
  { dataIndex: 'createdBy', key: 'createdBy', title: '创建人', width: 100 },
];

const scatterColumns = [
  { dataIndex: 'loopId', key: 'loopId', title: '回路', width: 140 },
  { dataIndex: 'before', key: 'before', title: '整定前', width: 90 },
  { dataIndex: 'after', key: 'after', title: '整定后', width: 90 },
  { dataIndex: 'delta', key: 'delta', title: 'Δ', width: 80 },
];

// scatters 前后配对（JSONB 原样 [{loop_id, score, ...}]，兼容 camelCase 键）
const scatterRows = computed(() => {
  const before = detail.value?.scattersBefore ?? [];
  const after = detail.value?.scattersAfter ?? [];
  const afterByLoop = new Map<string, Record<string, any>>();
  for (const p of after) {
    const lid = p.loop_id ?? p.loopId;
    if (lid != null) afterByLoop.set(String(lid), p);
  }
  const rows: { after: number; before: number; delta: number; loopId: string }[] =
    [];
  for (const p of before) {
    const lid = p.loop_id ?? p.loopId;
    if (lid == null) continue;
    const b = Number(p.score);
    const a = afterByLoop.get(String(lid));
    if (a == null || Number.isNaN(b)) continue;
    const afterScore = Number(a.score);
    if (Number.isNaN(afterScore)) continue;
    rows.push({
      loopId: String(lid),
      before: b,
      after: afterScore,
      delta: Math.round((afterScore - b) * 10) / 10,
    });
  }
  // Δ 降序（改善最大居上）
  return rows.toSorted((x, y) => y.delta - x.delta);
});

watch(
  () => [props.visible, props.batchId],
  async () => {
    if (!props.visible || props.batchId == null) return;
    loading.value = true;
    detail.value = null;
    try {
      detail.value = await getTuningBatchDetailApi(props.batchId);
    } finally {
      loading.value = false;
    }
  },
  { immediate: true },
);
</script>

<template>
  <Drawer
    :open="visible"
    title="整定批次详情"
    width="720"
    @close="emit('update:visible', false)"
  >
    <Spin :spinning="loading">
      <template v-if="detail">
        <Descriptions size="small" :column="2" bordered>
          <DescriptionsItem label="批次号">{{
            detail.batchNo
          }}</DescriptionsItem>
          <DescriptionsItem label="状态">
            <Tag
              :color="BATCH_STATUS_META[detail.status]?.color ?? 'default'"
            >
              {{ BATCH_STATUS_META[detail.status]?.label ?? detail.status }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="标题" :span="2">{{
            detail.title
          }}</DescriptionsItem>
          <DescriptionsItem label="范围">
            {{ detail.scopeType }} · {{ detail.scopeId }}
          </DescriptionsItem>
          <DescriptionsItem label="记录数">{{
            detail.recordCount
          }}</DescriptionsItem>
          <DescriptionsItem v-if="detail.blockReason" label="阻塞原因" :span="2">
            <span class="text-red-600">{{ detail.blockReason }}</span>
          </DescriptionsItem>
          <DescriptionsItem label="期望启动">{{
            detail.expectedStartAt ?? '—'
          }}</DescriptionsItem>
          <DescriptionsItem label="实际启动">{{
            detail.actualStartAt ?? '—'
          }}</DescriptionsItem>
          <DescriptionsItem label="完成时间">{{
            detail.completedAt ?? '—'
          }}</DescriptionsItem>
          <DescriptionsItem label="创建时间">{{
            detail.createdAt ?? '—'
          }}</DescriptionsItem>
        </Descriptions>

        <!-- 前置工单摘要 -->
        <div class="mt-4 text-xs font-medium text-neutral-500">
          前置工单（全部闭环/取消后方可启动）
        </div>
        <div v-if="detail.prereqOrders.length > 0" class="mt-1 flex flex-wrap gap-1">
          <Tag
            v-for="o in detail.prereqOrders"
            :key="o.orderId"
            :color="o.closed ? 'success' : 'warning'"
          >
            {{ o.orderNo ?? o.orderId }} ·
            {{ ORDER_STATUS_LABEL[o.status ?? ''] ?? o.status ?? '已删除' }}
          </Tag>
        </div>
        <div v-else class="mt-1 text-xs text-neutral-400">无前置工单</div>

        <!-- 关联整定记录 -->
        <div class="mt-4 text-xs font-medium text-neutral-500">关联整定记录</div>
        <Table
          class="mt-1"
          :columns="recordColumns"
          :data-source="detail.records"
          :pagination="false"
          row-key="recordId"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'tagName'">
              {{ record.tagName ?? record.loopId }}
            </template>
            <template v-else-if="column.key === 'algorithm'">
              {{ tuningAlgoLabel(record.algorithm) }}
            </template>
            <template v-else-if="column.key === 'fittingScore'">
              <span class="clpm-num">{{
                record.fittingScore == null
                  ? '—'
                  : `${fmtNum2(record.fittingScore)}%`
              }}</span>
            </template>
          </template>
        </Table>

        <!-- scatters 前后对比 -->
        <div class="mt-4 text-xs font-medium text-neutral-500">
          整定前后评分对比（批次固化快照）
        </div>
        <Table
          v-if="scatterRows.length > 0"
          class="mt-1"
          :columns="scatterColumns"
          :data-source="scatterRows"
          :pagination="false"
          row-key="loopId"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'before'">
              <span class="clpm-num">{{ fmtNum2(record.before) }}</span>
            </template>
            <template v-else-if="column.key === 'after'">
              <span class="clpm-num">{{ fmtNum2(record.after) }}</span>
            </template>
            <template v-else-if="column.key === 'delta'">
              <span
                class="clpm-num"
                :class="record.delta >= 0 ? 'text-green-600' : 'text-red-600'"
              >
                {{ record.delta >= 0 ? '+' : '' }}{{ fmtNum2(record.delta) }}
              </span>
            </template>
          </template>
        </Table>
        <Empty
          v-else
          class="mt-2"
          description="无固化散点快照"
          :image="Empty.PRESENTED_IMAGE_SIMPLE"
        />
      </template>
    </Spin>
  </Drawer>
</template>
