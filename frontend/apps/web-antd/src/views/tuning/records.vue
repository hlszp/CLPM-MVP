<script lang="ts" setup>
/**
 * 整定记录页（09 设计方案 §6.3）
 *
 * 顶部统计卡 + 记录表格（拟合度三档色阶/状态 tag）+ 详情抽屉 + 去验证入口。
 */
import type { TuningApi } from '#/api/tuning';

import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, Select, Table, Tag } from 'ant-design-vue';

import { getTuningHistoryApi, getTuningTasksApi } from '#/api/tuning';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';

import RecordDetailDrawer from './components/record-detail-drawer.vue';
import { fmtNum2, tuningAlgoLabel } from './constants';

defineOptions({ name: 'TuningRecords' });

const router = useRouter();

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
const statusFilter = ref<string | undefined>();

const STATUS_META: Record<string, { color: string; label: string }> = {
  SIMULATED: { color: 'processing', label: '已仿真' },
  APPLIED: { color: 'cyan', label: '已实施' },
  VERIFIED: { color: 'success', label: '已验证' },
  COMPLETED: { color: 'success', label: '已完成' },
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
      status: statusFilter.value,
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

onMounted(() => {
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
        <Button
          size="small"
          @click="
            loadList();
            loadStats();
          "
          >刷新</Button
        >
      </template>
    </ClpmPageToolbar>

    <!-- 统计卡 -->
    <div class="stats-row">
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

    <!-- 筛选 + 表格 -->
    <Card size="small">
      <div class="mb-2 flex items-center gap-2">
        <span class="text-xs text-neutral-500">状态</span>
        <Select
          v-model:value="statusFilter"
          allow-clear
          placeholder="全部"
          size="small"
          style="width: 140px"
          :options="[
            { label: '已仿真', value: 'SIMULATED' },
            { label: '已实施', value: 'APPLIED' },
            { label: '已验证', value: 'VERIFIED' },
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

    <RecordDetailDrawer
      v-model:visible="drawerVisible"
      :record-id="activeRecordId"
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
