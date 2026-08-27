<script lang="ts" setup>
/**
 * 整定记录页（09 设计方案 §6.3 + 追溯矩阵 GAP-2b 批次视图）
 *
 * 视图切换：整定记录（统计卡 + 记录表格 + 详情抽屉 + 去验证入口）/
 * 整定批次（批次列表 + 批次详情抽屉），两视图独立筛选、独立加载。
 */
import type { TuningApi } from '#/api/tuning';

import { onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, RadioGroup, Select, Table, Tag } from 'ant-design-vue';

import {
  getTuningBatchesApi,
  getTuningHistoryApi,
  getTuningTasksApi,
} from '#/api/tuning';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';

import BatchDetailDrawer from './components/batch-detail-drawer.vue';
import RecordDetailDrawer from './components/record-detail-drawer.vue';
import { fmtNum2, tuningAlgoLabel } from './constants';

defineOptions({ name: 'TuningRecords' });

const router = useRouter();

// ===== 视图切换（整定记录 / 整定批次） =====
const viewMode = ref<'batches' | 'records'>('records');

// ===== 统计卡 =====
const stats = ref<null | TuningApi.TuningHistoryStats>(null);

async function loadStats() {
  stats.value = await getTuningHistoryApi();
}

function statusCount(keys: string[]): number {
  if (!stats.value) return 0;
  return keys.reduce((sum, k) => sum + (stats.value!.byStatus[k] ?? 0), 0);
}

// ===== 表格 =====
const loading = ref(false);
const rows = ref<TuningApi.TuningTaskItem[]>([]);
const pagination = reactive({ current: 1, pageSize: 20, total: 0 });
// 状态筛选为多值（工作台下钻可携 DRAFT,PENDING 逗号多值口径）
const statusFilter = ref<string[]>([]);
// 回路 / 创建时间窗筛选（仅深链带入，页面无对应控件）
const loopIdFilter = ref<string | undefined>();
const startTimeFilter = ref<string | undefined>();
const endTimeFilter = ref<string | undefined>();

const STATUS_META: Record<string, { color: string; label: string }> = {
  DRAFT: { color: 'default', label: '草稿' },
  PENDING: { color: 'gold', label: '待实施' },
  SIMULATED: { color: 'processing', label: '已仿真' },
  APPLIED: { color: 'cyan', label: '已实施' },
  VERIFIED: { color: 'success', label: '已验证' },
  COMPLETED: { color: 'success', label: '已完成' },
  ROLLED_BACK: { color: 'warning', label: '已回退' },
  INCONCLUSIVE: { color: 'default', label: '无法判定' },
};

function fittingClass(score: null | number | undefined): string {
  if (score == null) return '';
  if (score >= 80) return 'text-green-600';
  if (score >= 60) return 'text-amber-600';
  return 'text-red-600';
}

async function loadList() {
  loading.value = true;
  try {
    const res = await getTuningTasksApi({
      page: pagination.current,
      pageSize: pagination.pageSize,
      // 多值状态逗号拼接直传（后端 /tuning/tasks 已支持逗号多值与时间窗）
      status:
        statusFilter.value.length > 0 ? statusFilter.value.join(',') : undefined,
      loopId: loopIdFilter.value,
      startTime: startTimeFilter.value,
      endTime: endTimeFilter.value,
    });
    rows.value = res.items;
    pagination.total = res.total;
  } finally {
    loading.value = false;
  }
}

const columns = [
  { dataIndex: 'tagName', key: 'tagName', title: '回路位号', width: 160 },
  { dataIndex: 'modelType', key: 'modelType', title: '模型', width: 90 },
  { dataIndex: 'algorithm', key: 'algorithm', title: '算法', width: 180 },
  { key: 'pid', title: '推荐 P/I/D', width: 200 },
  {
    dataIndex: 'fittingScore',
    key: 'fittingScore',
    title: '拟合度',
    width: 90,
  },
  { dataIndex: 'status', key: 'status', title: '状态', width: 100 },
  { key: 'created', title: '创建人/时间', width: 190 },
  { key: 'actions', title: '操作', width: 130 },
];

// ===== 详情抽屉 =====
const drawerVisible = ref(false);
const activeRecordId = ref<null | string>(null);

function openDetail(row: TuningApi.TuningTaskItem) {
  activeRecordId.value = row.id;
  drawerVisible.value = true;
}

function goVerification(row: TuningApi.TuningTaskItem) {
  router.push({
    path: '/tuning/verification',
    query: { loopId: row.loopId, recordId: row.id },
  });
}

function fmtPid(pid?: null | TuningApi.PidParams): string {
  if (!pid) return '—';
  return `${fmtNum2(pid.kp)} / ${fmtNum2(pid.ti)} / ${fmtNum2(pid.td)}`;
}

// ===== 批次视图（GAP-2b：独立筛选、独立加载，不复用记录视图筛选） =====
const batchLoading = ref(false);
const batchRows = ref<TuningApi.TuningBatchSummary[]>([]);
const batchPagination = reactive({ current: 1, pageSize: 20, total: 0 });
const batchStatusFilter = ref<string | undefined>();

const BATCH_STATUS_META: Record<string, { color: string; label: string }> = {
  BLOCKED: { color: 'error', label: '阻塞' },
  PENDING: { color: 'default', label: '待启动' },
  READY: { color: 'processing', label: '就绪' },
  RUNNING: { color: 'cyan', label: '执行中' },
  COMPLETED: { color: 'success', label: '已完成' },
  CANCELLED: { color: 'default', label: '已取消' },
};

const batchColumns = [
  { dataIndex: 'batchNo', key: 'batchNo', title: '批次号', width: 140 },
  { dataIndex: 'title', key: 'title', title: '标题' },
  { key: 'scope', title: '范围', width: 130 },
  { dataIndex: 'status', key: 'status', title: '状态', width: 100 },
  { dataIndex: 'recordCount', key: 'recordCount', title: '记录数', width: 80 },
  { key: 'blocked', title: '阻塞状态', width: 110 },
  { dataIndex: 'createdAt', key: 'createdAt', title: '创建时间', width: 190 },
];

async function loadBatchList() {
  batchLoading.value = true;
  try {
    const res = await getTuningBatchesApi({
      page: batchPagination.current,
      pageSize: batchPagination.pageSize,
      status: batchStatusFilter.value,
    });
    batchRows.value = res.items;
    batchPagination.total = res.total;
  } finally {
    batchLoading.value = false;
  }
}

// 批次详情抽屉
const batchDrawerVisible = ref(false);
const activeBatchId = ref<null | number>(null);

function openBatchDetail(row: TuningApi.TuningBatchSummary) {
  activeBatchId.value = row.id;
  batchDrawerVisible.value = true;
}

// 切到批次视图时加载一次（之后翻页/筛选自行触发）
watch(viewMode, (v) => {
  if (v === 'batches') loadBatchList();
});

// 刷新按当前视图分发
function refresh() {
  if (viewMode.value === 'batches') {
    loadBatchList();
  } else {
    loadList();
    loadStats();
  }
}

// ===== 路由 query 初值（追溯矩阵 G6：工作台统计下钻接参） =====
const route = useRoute();

/**
 * 挂载时从 route.query 读取一次筛选初值（不做 watch 同步，之后用户可自由修改）。
 * 支持：status（逗号多值，如 DRAFT,PENDING）、loopId、startTime/endTime（ISO8601）。
 */
function applyRouteQuery() {
  const q = route.query;
  if (typeof q.status === 'string' && q.status) {
    // 逗号多值，仅保留已知状态
    statusFilter.value = q.status
      .split(',')
      .filter((s) => s && STATUS_META[s]);
  }
  if (typeof q.loopId === 'string' && q.loopId) {
    loopIdFilter.value = q.loopId;
  }
  if (typeof q.startTime === 'string' && q.startTime) {
    startTimeFilter.value = q.startTime;
  }
  if (typeof q.endTime === 'string' && q.endTime) {
    endTimeFilter.value = q.endTime;
  }
}

onMounted(() => {
  applyRouteQuery();
  loadStats();
  loadList();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      subtitle="整定方案历史追溯：保存的方案 → 实施 → 验证闭环"
      title="整定记录"
    >
      <template #actions>
        <!-- 视图切换：整定记录 / 整定批次（两视图独立筛选与加载） -->
        <RadioGroup
          v-model:value="viewMode"
          size="small"
          option-type="button"
          :options="[
            { label: '整定记录', value: 'records' },
            { label: '整定批次', value: 'batches' },
          ]"
        />
        <Button size="small" @click="refresh">刷新</Button>
      </template>
    </ClpmPageToolbar>

    <!-- 统计卡（记录视图专属） -->
    <div v-if="viewMode === 'records'" class="stats-row">
      <Card size="small" class="stats-card">
        <div class="stats-num">{{ stats?.totalTasks ?? '—' }}</div>
        <div class="stats-label">总任务数</div>
      </Card>
      <Card size="small" class="stats-card">
        <div class="stats-num">{{ statusCount(['APPLIED', 'VERIFIED']) }}</div>
        <div class="stats-label">已实施</div>
      </Card>
      <Card size="small" class="stats-card">
        <div class="stats-num">
          {{
            stats?.avgFittingScore == null
              ? '—'
              : `${stats.avgFittingScore.toFixed(1)}%`
          }}
        </div>
        <div class="stats-label">平均拟合度</div>
      </Card>
      <Card size="small" class="stats-card">
        <div class="stats-num">{{ statusCount(['VERIFIED']) }}</div>
        <div class="stats-label">已验证闭环</div>
      </Card>
    </div>

    <!-- 记录视图：筛选 + 表格 -->
    <Card v-if="viewMode === 'records'" size="small">
      <div class="mb-2 flex items-center gap-2">
        <span class="text-xs text-neutral-500">状态</span>
        <Select
          v-model:value="statusFilter"
          mode="multiple"
          allow-clear
          placeholder="全部"
          size="small"
          :max-tag-count="2"
          style="width: 220px"
          :options="[
            { label: '草稿', value: 'DRAFT' },
            { label: '待实施', value: 'PENDING' },
            { label: '已仿真', value: 'SIMULATED' },
            { label: '已实施', value: 'APPLIED' },
            { label: '已验证', value: 'VERIFIED' },
            { label: '已回退', value: 'ROLLED_BACK' },
          ]"
          @change="loadList"
        />
      </div>
      <Table
        :columns="columns"
        :data-source="rows"
        :loading="loading"
        :pagination="{
          current: pagination.current,
          pageSize: pagination.pageSize,
          total: pagination.total,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        row-key="id"
        size="small"
        @change="
          (p: any) => {
            pagination.current = p.current;
            loadList();
          }
        "
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'algorithm'">
            {{ tuningAlgoLabel(record.algorithm) }}
          </template>
          <template v-else-if="column.key === 'pid'">
            <span class="clpm-num">{{ fmtPid(record.recommendedPid) }}</span>
          </template>
          <template v-else-if="column.key === 'fittingScore'">
            <span class="clpm-num" :class="fittingClass(record.fittingScore)">
              {{
                record.fittingScore == null
                  ? '—'
                  : `${record.fittingScore.toFixed(1)}%`
              }}
            </span>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag :color="STATUS_META[record.status]?.color ?? 'default'">
              {{ STATUS_META[record.status]?.label ?? record.status }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'created'">
            <span class="text-xs"
              >{{ record.createdBy ?? '—' }} · {{ record.createdAt }}</span
            >
          </template>
          <template v-else-if="column.key === 'actions'">
            <Button
              size="small"
              type="link"
              @click="openDetail(record as TuningApi.TuningTaskItem)"
              >详情</Button
            >
            <Button
              size="small"
              type="link"
              @click="goVerification(record as TuningApi.TuningTaskItem)"
              >去验证</Button
            >
          </template>
        </template>
      </Table>
    </Card>

    <!-- 批次视图：状态筛选 + 批次表格（行点击开详情抽屉） -->
    <Card v-else size="small">
      <div class="mb-2 flex items-center gap-2">
        <span class="text-xs text-neutral-500">状态</span>
        <Select
          v-model:value="batchStatusFilter"
          allow-clear
          placeholder="全部"
          size="small"
          style="width: 160px"
          :options="[
            { label: '阻塞', value: 'BLOCKED' },
            { label: '待启动', value: 'PENDING' },
            { label: '就绪', value: 'READY' },
            { label: '执行中', value: 'RUNNING' },
            { label: '已完成', value: 'COMPLETED' },
            { label: '已取消', value: 'CANCELLED' },
          ]"
          @change="
            batchPagination.current = 1;
            loadBatchList();
          "
        />
      </div>
      <Table
        :columns="batchColumns"
        :custom-row="
          (record: TuningApi.TuningBatchSummary) => ({
            onClick: () => openBatchDetail(record),
            style: 'cursor: pointer',
          })
        "
        :data-source="batchRows"
        :loading="batchLoading"
        :pagination="{
          current: batchPagination.current,
          pageSize: batchPagination.pageSize,
          total: batchPagination.total,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        row-key="id"
        size="small"
        @change="
          (p: any) => {
            batchPagination.current = p.current;
            loadBatchList();
          }
        "
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'scope'">
            {{ record.scopeType }} · {{ record.scopeId }}
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag :color="BATCH_STATUS_META[record.status]?.color ?? 'default'">
              {{ BATCH_STATUS_META[record.status]?.label ?? record.status }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'blocked'">
            <Tag v-if="record.blocked" color="error">阻塞</Tag>
            <span v-else class="text-neutral-400">—</span>
          </template>
          <template v-else-if="column.key === 'createdAt'">
            <span class="text-xs">{{ record.createdAt ?? '—' }}</span>
          </template>
        </template>
      </Table>
    </Card>

    <RecordDetailDrawer
      v-model:visible="drawerVisible"
      :record-id="activeRecordId"
    />
    <BatchDetailDrawer
      v-model:visible="batchDrawerVisible"
      :batch-id="activeBatchId"
    />
  </Page>
</template>

<style scoped>
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}

.stats-card {
  text-align: center;
}

.stats-num {
  font-size: 20px;
  font-weight: 600;
}

.stats-label {
  margin-top: 2px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}
</style>
