<script lang="ts" setup>
/**
 * S7-TUNE-001 整定工作台
 *
 * 对齐 IDS v3.2 §2.5 + PRD §4.5
 * - 顶部：4 个统计卡片（总任务数/已完成/平均拟合度/近 7 天任务数）
 * - 中部：整定流程导航卡片（模型辨识/整定算法/闭环仿真/效果统计）
 * - 底部：最近整定任务表格（recentTasks 前 10 条）
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { TuningApi } from '#/api/tuning';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, Spin, Statistic, Table, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getTuningHistoryApi } from '#/api/tuning';

defineOptions({ name: 'TuningWorkbench' });

const router = useRouter();

const loading = ref(false);
const historyStats = ref<TuningApi.HistoryStats | null>(null);

/** 算法显示名映射 */
const algorithmNameMap: Record<TuningApi.Algorithm, string> = {
  IMC: 'IMC 内模控制',
  LAMBDA: 'Lambda 整定',
  ZN: 'Ziegler-Nichols',
  COHEN_COON: 'Cohen-Coon',
  SIMC: 'SIMC 简化 IMC',
};

/** 模型类型显示名映射 */
const modelTypeNameMap: Record<TuningApi.ModelType, string> = {
  FOPDT: 'FOPDT 一阶加纯滞后',
  SOPDT: 'SOPDT 二阶加纯滞后',
  IPDT: 'IPDT 积分加纯滞后',
};

/** 任务状态显示名映射 */
const statusNameMap: Record<TuningApi.TaskStatus, string> = {
  SIMULATED: '已仿真',
  APPLIED: '已应用',
  FAILED: '失败',
};

/** 任务状态颜色映射 */
const statusColorMap: Record<TuningApi.TaskStatus, string> = {
  SIMULATED: 'blue',
  APPLIED: 'green',
  FAILED: 'red',
};

/** 整定流程导航卡片配置 */
const navCards = [
  {
    key: 'model',
    title: '模型辨识',
    description: '基于历史数据辨识回路 FOPDT/SOPDT/IPDT 模型',
    icon: 'lucide:git-branch',
    path: '/tuning/model',
  },
  {
    key: 'algorithm',
    title: '整定算法',
    description: '基于模型参数计算推荐 PID（ZN/Cohen-Coon/IMC/Lambda/SIMC）',
    icon: 'lucide:cpu',
    path: '/tuning/algorithm',
  },
  {
    key: 'simulation',
    title: '闭环仿真',
    description: '对比当前 PID 与推荐 PID 的闭环响应性能',
    icon: 'lucide:play-circle',
    path: '/tuning/simulation',
  },
  {
    key: 'stats',
    title: '效果统计',
    description: '查看整定历史统计与效果分析',
    icon: 'lucide:file-bar-chart',
    path: '/tuning/stats',
  },
];

/** 最近任务表格列定义 */
const columns: TableColumnsType = [
  {
    title: '回路位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 140,
    ellipsis: true,
  },
  {
    title: '模型类型',
    dataIndex: 'modelType',
    key: 'modelType',
    width: 180,
  },
  {
    title: '算法',
    dataIndex: 'algorithm',
    key: 'algorithm',
    width: 150,
  },
  {
    title: '拟合度',
    dataIndex: 'fittingScore',
    key: 'fittingScore',
    width: 100,
    align: 'right',
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 100,
  },
  {
    title: '创建时间',
    dataIndex: 'createdAt',
    key: 'createdAt',
    width: 170,
  },
  { title: '操作', key: 'action', width: 110, fixed: 'right' },
];

/** 最近任务列表（前 10 条） */
const recentTasks = computed(() => {
  const list = historyStats.value?.recentTasks || [];
  return list.slice(0, 10);
});

/** 已完成任务数（APPLIED + SIMULATED） */
const completedCount = computed(() => {
  const byStatus = historyStats.value?.byStatus || {};
  return (byStatus.APPLIED || 0) + (byStatus.SIMULATED || 0);
});

/** 近 7 天任务数 */
const recent7DaysCount = computed(() => {
  const list = historyStats.value?.recentTasks || [];
  const sevenDaysAgo = dayjs().subtract(7, 'day');
  return list.filter((t) => dayjs(t.createdAt).isAfter(sevenDaysAgo)).length;
});

/** 平均拟合度 */
const avgFittingScore = computed(() => {
  return historyStats.value?.avgFittingScore ?? null;
});

/** 总任务数 */
const totalTasks = computed(() => {
  return historyStats.value?.totalTasks ?? 0;
});

/** 加载整定历史统计 */
async function loadHistory() {
  loading.value = true;
  try {
    const data = await getTuningHistoryApi();
    historyStats.value = data;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 跳转指定页面 */
function handleNavigate(path: string) {
  router.push(path);
}

/** 查看任务详情 */
function handleViewDetail(record: TuningApi.TuningTaskItem) {
  router.push({
    path: '/tuning/stats',
    query: { taskId: record.id },
  });
}

/** 时间格式化 */
function formatTime(t: string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

/** 拟合度格式化 */
function formatFittingScore(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return `${Number(val).toFixed(2)}%`;
}

/** 拟合度颜色 */
function fittingScoreColor(val: null | number | undefined): string {
  if (val === null || val === undefined) return '';
  if (val >= 80) return '#52c41a';
  if (val >= 60) return '#faad14';
  return '#ff4d4f';
}

onMounted(() => {
  loadHistory();
});
</script>

<template>
  <Page title="整定工作台">
    <Spin :spinning="loading">
      <!-- 顶部统计卡片 -->
      <div class="mb-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card size="small" :body-style="{ padding: '20px' }">
          <Statistic
            title="总任务数"
            :value="totalTasks"
            :value-style="{ color: '#1890ff' }"
          />
        </Card>
        <Card size="small" :body-style="{ padding: '20px' }">
          <Statistic
            title="已完成（应用+仿真）"
            :value="completedCount"
            :value-style="{ color: '#52c41a' }"
          />
        </Card>
        <Card size="small" :body-style="{ padding: '20px' }">
          <Statistic
            title="平均拟合度"
            :value="avgFittingScore ?? 0"
            :precision="2"
            suffix="%"
            :value-style="{
              color: fittingScoreColor(avgFittingScore),
            }"
          />
        </Card>
        <Card size="small" :body-style="{ padding: '20px' }">
          <Statistic
            title="近 7 天任务数"
            :value="recent7DaysCount"
            :value-style="{ color: '#722ed1' }"
          />
        </Card>
      </div>

      <!-- 整定流程导航卡片 -->
      <Card title="整定流程" class="mb-4">
        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card
            v-for="item in navCards"
            :key="item.key"
            hoverable
            size="small"
            :body-style="{ padding: '20px' }"
            class="cursor-pointer transition-all hover:shadow-md"
            @click="handleNavigate(item.path)"
          >
            <div class="flex flex-col items-start">
              <div
                class="mb-3 flex h-10 w-10 items-center justify-center rounded bg-blue-50 text-xl text-blue-600"
              >
                <span class="font-bold">{{ item.title.charAt(0) }}</span>
              </div>
              <div class="text-base font-semibold text-gray-800">
                {{ item.title }}
              </div>
              <div class="mt-1 text-xs text-gray-500">
                {{ item.description }}
              </div>
              <Button type="link" size="small" class="mt-2 !px-0">
                进入 →
              </Button>
            </div>
          </Card>
        </div>
      </Card>

      <!-- 最近整定任务表格 -->
      <Card title="最近整定任务">
        <Table
          :columns="columns"
          :data-source="recentTasks"
          :loading="loading"
          :pagination="false"
          :row-key="(record: TuningApi.TuningTaskItem) => record.id"
          :scroll="{ x: 950 }"
          size="middle"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'tagName'">
              <span class="font-mono text-xs font-medium">
                {{ record.tagName || record.loopId }}
              </span>
            </template>
            <template v-else-if="column.key === 'modelType'">
              {{
                modelTypeNameMap[record.modelType as TuningApi.ModelType] ||
                record.modelType
              }}
            </template>
            <template v-else-if="column.key === 'algorithm'">
              {{
                algorithmNameMap[record.algorithm as TuningApi.Algorithm] ||
                record.algorithm
              }}
            </template>
            <template v-else-if="column.key === 'fittingScore'">
              <span
                class="font-mono"
                :style="{
                  color: fittingScoreColor(record.fittingScore),
                }"
              >
                {{ formatFittingScore(record.fittingScore) }}
              </span>
            </template>
            <template v-else-if="column.key === 'status'">
              <Tag
                :color="statusColorMap[record.status as TuningApi.TaskStatus]"
              >
                {{
                  statusNameMap[record.status as TuningApi.TaskStatus] ||
                  record.status
                }}
              </Tag>
            </template>
            <template v-else-if="column.key === 'createdAt'">
              {{ formatTime(record.createdAt) }}
            </template>
            <template v-else-if="column.key === 'action'">
              <Button
                type="link"
                size="small"
                @click="handleViewDetail(record as TuningApi.TuningTaskItem)"
              >
                查看详情
              </Button>
            </template>
          </template>
        </Table>
      </Card>
    </Spin>
  </Page>
</template>
