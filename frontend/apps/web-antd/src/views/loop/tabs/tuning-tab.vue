<script lang="ts" setup>
/**
 * 回路工作台 · 整定 Tab（IA 重构 Phase B·§4.1.1）
 *
 * 定位：单回路整定摘要 —— 一眼看清"整过几次、最新推荐 PID 是什么"。
 * 遵循"摘要 + 1 主图 + 跳转入口"硬性规则，禁止内嵌完整整定向导。
 *
 * 三区：
 * ① 跳转入口：开始整定（带 loopId 上下文）/ 整定工作台
 * ② 摘要区：整定次数 + 最近一次整定算法/状态/推荐 PID + 平均拟合度
 * ③ 主图：整定历史列表 Table（算法/模型/PID/拟合度/可信度/状态/时间/操作）
 *
 * 数据来源：本 Tab 自行加载 getTuningTasksApi（切到 Tab 才请求，概览不需要）
 * 后端零改动：全部组合现有 API。
 */
import type { TuningApi } from '#/api/tuning';

import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Button, Empty, Spin, Table, Tag } from 'ant-design-vue';

import { getTuningTasksApi } from '#/api/tuning';
import { ClpmDataCanvas } from '#/components/clpm';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'LoopWorkbenchTuningTab' });

const props = defineProps<{ loopId: string }>();

const router = useRouter();

// ===== 常量 =====
const TASK_STATUS_META: Record<string, { color: string; label: string }> = {
  APPLIED: { color: 'orange', label: '已实施' },
  COMPLETED: { color: 'blue', label: '已完成' },
  DRAFT: { color: 'default', label: '草稿' },
  IDENTIFIED: { color: 'blue', label: '已辨识' },
  INCONCLUSIVE: { color: 'default', label: '不确定' },
  PENDING: { color: 'default', label: '待处理' },
  ROLLED_BACK: { color: 'red', label: '已回退' },
  RUNNING: { color: 'processing', label: '进行中' },
  SIMULATED: { color: 'blue', label: '已仿真' },
  VERIFIED: { color: 'green', label: '已验证' },
};

const ALGORITHM_LABEL: Record<string, string> = {
  COHEN_COON: 'Cohen-Coon',
  IMC: 'IMC',
  LAMBDA: 'Lambda',
  SIMC: 'SIMC',
  ZN: 'Ziegler-Nichols',
};

const CONFIDENCE_COLOR: Record<string, string> = {
  A: 'green',
  B: 'blue',
  C: 'gold',
  D: 'orange',
  E: 'red',
  INCONCLUSIVE: 'default',
};

// ===== 数据状态 =====
const loading = ref(false);
const tasks = ref<TuningApi.TuningTaskItem[]>([]);

/** 最近一次整定任务 */
const latestTask = computed(() => tasks.value[0] ?? null);

/** 平均拟合度 */
const avgFitting = computed(() => {
  const valid = tasks.value
    .map((t) => t.fittingScore)
    .filter((v): v is number => v !== null && v !== undefined);
  if (valid.length === 0) return null;
  return valid.reduce((a, b) => a + b, 0) / valid.length;
});

/** PID 参数文本 */
function pidText(pid?: null | TuningApi.PidParams): string {
  if (!pid) return '—';
  return `P=${pid.kp}, Ti=${pid.ti}s, Td=${pid.td}s`;
}

// ===== 数据加载 =====
async function loadData() {
  loading.value = true;
  try {
    const res = await getTuningTasksApi({
      loopId: props.loopId,
      page: 1,
      pageSize: 20,
    });
    // 按创建时间降序（最新在前）
    tasks.value = (res.items || []).toSorted((a, b) =>
      b.createdAt.localeCompare(a.createdAt),
    );
  } catch {
    tasks.value = [];
  } finally {
    loading.value = false;
  }
}

// ===== 跳转入口 =====
function startTuning() {
  router.push({
    path: '/tuning/flow',
    query: { loopId: props.loopId },
  });
}

function goTuningWorkbench() {
  router.push({
    path: '/tuning/workbench',
    query: { loopId: props.loopId },
  });
}

function viewDetail(record: TuningApi.TuningTaskItem) {
  router.push({
    path: '/tuning/flow',
    query: { taskId: record.id },
  });
}

// ===== 表格列 =====
const columns = [
  { title: '算法', dataIndex: 'algorithm', key: 'algorithm', width: 120 },
  { title: '模型', dataIndex: 'modelType', key: 'modelType', width: 90 },
  { title: '推荐 PID', key: 'pid', width: 220 },
  { title: '拟合度', dataIndex: 'fittingScore', key: 'fittingScore', width: 90, align: 'right' as const },
  { title: '可信度', dataIndex: 'confidenceLevel', key: 'confidenceLevel', width: 90, align: 'center' as const },
  { title: '状态', dataIndex: 'status', key: 'status', width: 100, align: 'center' as const },
  { title: '创建时间', dataIndex: 'createdAt', key: 'createdAt', width: 150 },
  { title: '操作', key: 'action', width: 90, align: 'center' as const, fixed: 'right' as const },
];

// ===== 生命周期 =====
onMounted(() => {
  loadData();
});

watch(
  () => props.loopId,
  () => {
    loadData();
  },
);
</script>

<template>
  <div class="space-y-3 py-2">
    <!-- ① 跳转入口 -->
    <div class="flex items-center gap-2">
      <span class="text-xs text-gray-400">整定处置：</span>
      <Button type="primary" size="small" @click="startTuning">
        开始整定
      </Button>
      <Button size="small" @click="goTuningWorkbench">整定工作台</Button>
    </div>

    <!-- ② 摘要区：整定统计 + 最近一次推荐 -->
    <ClpmDataCanvas
      title="整定摘要"
      :loading="loading"
      :empty="!loading && tasks.length === 0"
      empty-text="暂无整定记录"
      empty-reason="可能原因：该回路尚未进行过整定。"
      empty-action-text="开始整定"
      @empty-action="startTuning"
    >
      <Spin :spinning="loading">
        <div
          v-if="tasks.length > 0"
          class="grid grid-cols-2 gap-3 md:grid-cols-4"
        >
          <div class="rounded border p-3">
            <div class="text-xs text-gray-400">整定次数</div>
            <div class="mt-1 text-lg font-semibold text-blue-600">
              {{ tasks.length }}
            </div>
          </div>
          <div class="rounded border p-3">
            <div class="text-xs text-gray-400">平均拟合度</div>
            <div class="mt-1 text-lg font-semibold">
              {{
                avgFitting === null
                  ? '—'
                  : `${(avgFitting * 100).toFixed(1)}%`
              }}
            </div>
          </div>
          <div class="rounded border p-3">
            <div class="text-xs text-gray-400">最近算法</div>
            <div class="mt-1 text-sm font-medium">
              {{ latestTask ? (ALGORITHM_LABEL[latestTask.algorithm] || latestTask.algorithm) : '—' }}
            </div>
          </div>
          <div class="rounded border p-3">
            <div class="text-xs text-gray-400">最近状态</div>
            <div class="mt-1">
              <Tag
                v-if="latestTask"
                :color="TASK_STATUS_META[latestTask.status]?.color || 'default'"
              >
                {{ TASK_STATUS_META[latestTask.status]?.label || latestTask.status }}
              </Tag>
              <span v-else class="text-sm text-gray-400">—</span>
            </div>
          </div>
        </div>
        <div v-if="latestTask" class="mt-3 rounded border bg-gray-50 p-3">
          <div class="mb-1 text-xs text-gray-400">最近推荐 PID</div>
          <div class="text-sm font-medium">
            {{ pidText(latestTask.recommendedPid) }}
          </div>
        </div>
      </Spin>
    </ClpmDataCanvas>

    <!-- ③ 主图：整定历史列表 -->
    <ClpmDataCanvas
      title="整定历史"
      description="该回路最近 20 条整定任务记录。"
      :loading="loading"
      :empty="!loading && tasks.length === 0"
      empty-text="暂无整定记录"
    >
      <Table
        v-if="tasks.length > 0"
        :data-source="tasks"
        :columns="columns"
        :pagination="false"
        size="small"
        :row-key="(record: TuningApi.TuningTaskItem) => record.id"
        :scroll="{ x: 950 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'algorithm'">
            {{ ALGORITHM_LABEL[(record as TuningApi.TuningTaskItem).algorithm] || (record as TuningApi.TuningTaskItem).algorithm }}
          </template>
          <template v-else-if="column.key === 'pid'">
            <span class="text-xs">{{ pidText((record as TuningApi.TuningTaskItem).recommendedPid) }}</span>
          </template>
          <template v-else-if="column.key === 'fittingScore'">
            {{
              (record as TuningApi.TuningTaskItem).fittingScore == null
                ? '—'
                : `${(((record as TuningApi.TuningTaskItem).fittingScore as number) * 100).toFixed(1)}%`
            }}
          </template>
          <template v-else-if="column.key === 'confidenceLevel'">
            <Tag
              v-if="(record as TuningApi.TuningTaskItem).confidenceLevel"
              :color="CONFIDENCE_COLOR[(record as TuningApi.TuningTaskItem).confidenceLevel as string] || 'default'"
            >
              {{ (record as TuningApi.TuningTaskItem).confidenceLevel }}
            </Tag>
            <span v-else class="text-xs text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag :color="TASK_STATUS_META[(record as TuningApi.TuningTaskItem).status]?.color || 'default'">
              {{ TASK_STATUS_META[(record as TuningApi.TuningTaskItem).status]?.label || (record as TuningApi.TuningTaskItem).status }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'createdAt'">
            <span class="text-xs">{{ formatTime((record as TuningApi.TuningTaskItem).createdAt) }}</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <Button type="link" size="small" @click="viewDetail(record as TuningApi.TuningTaskItem)">
              查看
            </Button>
          </template>
        </template>
      </Table>
      <Empty
        v-else-if="!loading"
        description="暂无整定记录"
        class="py-8"
      />
    </ClpmDataCanvas>
  </div>
</template>
