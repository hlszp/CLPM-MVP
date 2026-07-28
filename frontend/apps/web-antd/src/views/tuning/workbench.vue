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
import type { KpiStripItem } from '#/components/clpm';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import { Alert, Button, Card, Spin, Table, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getTuningHistoryApi } from '#/api/tuning';
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'TuningWorkbench' });

const { themeColors } = useClpmTheme();

const router = useRouter();

const loading = ref(false);
const historyStats = ref<null | TuningApi.HistoryStats>(null);

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

/** 任务状态显示名映射（Phase 2 对齐实现契约 v2.1 状态机） */
const statusNameMap: Record<TuningApi.TaskStatus, string> = {
  // Phase 2 新枚举
  DRAFT: '草稿',
  RUNNING: '执行中',
  IDENTIFIED: '已辨识',
  SIMULATED: '已仿真',
  COMPLETED: '已完成',
  INCONCLUSIVE: '不确定',
  ROLLED_BACK: '已回退',
  // 旧枚举（兼容期保留）
  PENDING: '待辨识',
  APPLIED: '已应用',
  VERIFIED: '已验证',
};

/** 任务状态颜色映射（Phase 2 对齐实现契约 v2.1 状态机） */
const statusColorMap: Record<TuningApi.TaskStatus, string> = {
  // Phase 2 新枚举
  DRAFT: 'default',
  RUNNING: 'processing',
  IDENTIFIED: 'cyan',
  SIMULATED: 'blue',
  COMPLETED: 'green',
  INCONCLUSIVE: 'orange',
  ROLLED_BACK: 'red',
  // 旧枚举（兼容期保留）
  PENDING: 'default',
  APPLIED: 'green',
  VERIFIED: 'success',
};

/** 整定流程导航卡片配置（统一使用 ant-design 图标集） */
const navCards = [
  {
    key: 'model',
    title: '模型辨识',
    description: '基于历史数据辨识回路 FOPDT/SOPDT/IPDT 模型',
    icon: 'ant-design:apartment-outlined',
    path: '/tuning/model',
  },
  {
    key: 'algorithm',
    title: '整定算法',
    description: '基于模型参数计算推荐 PID（ZN/Cohen-Coon/IMC/Lambda/SIMC）',
    icon: 'ant-design:calculator-outlined',
    path: '/tuning/algorithm',
  },
  {
    key: 'simulation',
    title: '闭环仿真',
    description: '对比当前 PID 与推荐 PID 的闭环响应性能',
    icon: 'ant-design:experiment-outlined',
    path: '/tuning/simulation',
  },
  {
    key: 'stats',
    title: '效果统计',
    description: '查看整定历史统计与效果分析',
    icon: 'ant-design:bar-chart-outlined',
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

/** 已完成任务数（Phase 2：COMPLETED + SIMULATED + 兼容旧 APPLIED） */
const completedCount = computed(() => {
  const byStatus = historyStats.value?.byStatus || {};
  return (
    (byStatus.COMPLETED || 0) +
    (byStatus.SIMULATED || 0) +
    (byStatus.APPLIED || 0)
  );
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

function getFittingStatus(value: number): NonNullable<KpiStripItem['status']> {
  if (value >= 80) return 'success';
  if (value >= 60) return 'warning';
  return 'danger';
}

const kpiStripItems = computed<KpiStripItem[]>(() => [
  {
    key: 'total',
    label: '总任务数',
    value: totalTasks.value,
    status: 'neutral',
  },
  {
    key: 'completed',
    label: '已完成',
    value: completedCount.value,
    status: 'success',
  },
  {
    key: 'fitting',
    label: '平均拟合度',
    value: (avgFittingScore.value ?? 0).toFixed(2),
    unit: '%',
    status: getFittingStatus(avgFittingScore.value ?? 0),
  },
  {
    key: 'recent',
    label: '近 7 天任务数',
    value: recent7DaysCount.value,
    status: 'neutral',
  },
]);

/** 待整定数（Phase 2：DRAFT/RUNNING/PENDING + IDENTIFIED） */
const pendingTuningCount = computed(() => {
  const byStatus = historyStats.value?.byStatus || {};
  return (
    (byStatus.DRAFT || 0) +
    (byStatus.RUNNING || 0) +
    (byStatus.PENDING || 0) +
    (byStatus.IDENTIFIED || 0)
  );
});

/**
 * 风险任务数（高整定风险回路数）
 * 后端暂未直接提供风险标记接口，使用 0 占位，待整定风险接口接入后替换
 */
const highRiskCount = computed(() => 0);

/**
 * 超阈值任务数（PID 参数超推荐范围）
 * 后端暂未直接提供超阈值标记接口，使用 0 占位，待整定风险接口接入后替换
 */
const overThresholdCount = computed(() => 0);

/** 风险相关 KPI 指标 */
const riskKpiItems = computed<KpiStripItem[]>(() => [
  {
    key: 'highRisk',
    label: '风险任务数',
    value: highRiskCount.value,
    status: 'danger',
  },
  {
    key: 'overThreshold',
    label: '超阈值任务数',
    value: overThresholdCount.value,
    status: 'warning',
  },
  {
    key: 'pending',
    label: '待整定数',
    value: pendingTuningCount.value,
    status: 'neutral',
  },
  {
    key: 'completed',
    label: '已完成数',
    value: completedCount.value,
    status: 'success',
  },
]);

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

/** 工具栏：刷新 */
function handleRefresh() {
  loadHistory();
}

// P2 #37 UX13: 导出功能开发中，按钮改为 disabled + tooltip

/** 工具栏：新建整定，跳转模型辨识 */
function handleCreate() {
  router.push('/tuning/model');
}

/** 拟合度格式化 */
function formatFittingScore(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return `${Number(val).toFixed(2)}%`;
}

/** 拟合度颜色 */
function fittingScoreColor(val: null | number | undefined): string {
  if (val === null || val === undefined) return '';
  if (val >= 80) return themeColors.value.SUCCESS;
  if (val >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

onMounted(() => {
  loadHistory();
});
</script>

<template>
  <Page>
    <Spin :spinning="loading">
      <ClpmPageToolbar
        title="整定工作台"
        subtitle="模型辨识、算法、仿真与效果统计的统一入口"
      >
        <template #actions>
          <ClpmToolbarButton
            icon="refresh"
            label="刷新"
            :loading="loading"
            @click="handleRefresh"
          />
          <ClpmToolbarButton
            icon="export"
            label="导出"
            disabled
            disabled-reason="导出功能开发中，待后端接口支持"
          />
          <ClpmToolbarButton
            icon="create"
            label="新建整定"
            variant="primary"
            @click="handleCreate"
          />
        </template>
      </ClpmPageToolbar>

      <!-- 常驻风险提示横幅：不可关闭 -->
      <Alert
        class="mt-3"
        type="warning"
        show-icon
        banner
        :closable="false"
        message="只读建议 · 人工实施 · 需留痕"
        description="本平台不直接修改 DCS 的 P/I/D 参数，参数由授权人员人工实施并留痕。"
      />

      <div class="mb-4 mt-4">
        <ClpmKpiStrip :items="kpiStripItems" />
      </div>

      <!-- 风险相关 KPI 指标 -->
      <div class="mb-4">
        <ClpmKpiStrip :items="riskKpiItems" />
      </div>

      <ClpmDataCanvas title="整定流程" class="mb-4">
        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card
            v-for="item in navCards"
            :key="item.key"
            hoverable
            size="small"
            :body-style="{ padding: '20px' }"
            class="cursor-pointer transition-shadow duration-200 hover:shadow-md"
            @click="handleNavigate(item.path)"
          >
            <div class="flex flex-col items-start">
              <div
                class="mb-3 flex h-10 w-10 items-center justify-center rounded bg-blue-50 text-xl text-blue-600"
              >
                <IconifyIcon :icon="item.icon" />
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
      </ClpmDataCanvas>

      <!-- 最近整定任务表格 -->
      <ClpmDataCanvas title="最近整定任务">
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
      </ClpmDataCanvas>
    </Spin>
  </Page>
</template>
