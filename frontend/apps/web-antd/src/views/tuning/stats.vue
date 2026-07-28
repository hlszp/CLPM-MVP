<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * S7-TUNE-005 效果统计页
 *
 * 对齐 IDS v3.2 §2.5 + PRD §4.5
 * - 顶部统计卡片（总任务数/已应用数/平均拟合度/算法种类数）
 * - 中部图表区（算法分布饼图 + 状态分布柱状图）
 * - 底部任务列表（筛选 + 表格 + 分页）
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningApi } from '#/api/tuning';
import type { KpiStripItem } from '#/components/clpm';

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Alert, Button, Select, Table, Tag } from 'ant-design-vue';

import { getTuningHistoryApi, getTuningTasksApi } from '#/api/tuning';
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmPageToolbar,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'TuningStats' });

const { isDark, themeColors } = useClpmTheme();

const loading = ref(false);
const historyLoading = ref(false);
const historyStats = ref<null | TuningApi.HistoryStats>(null);
const taskList = ref<TuningApi.TuningTaskItem[]>([]);
const total = ref(0);

/** 算法选项 */
const algorithmOptions: { label: string; value: TuningApi.Algorithm }[] = [
  { label: 'IMC 内模控制', value: 'IMC' },
  { label: 'Lambda 整定', value: 'LAMBDA' },
  { label: 'Ziegler-Nichols', value: 'ZN' },
  { label: 'Cohen-Coon', value: 'COHEN_COON' },
  { label: 'SIMC 简化 IMC', value: 'SIMC' },
];

/** 状态选项（Phase 2 对齐实现契约 v2.1 状态机） */
const statusOptions: { label: string; value: TuningApi.TaskStatus }[] = [
  // Phase 2 新枚举
  { label: '草稿', value: 'DRAFT' },
  { label: '执行中', value: 'RUNNING' },
  { label: '已辨识', value: 'IDENTIFIED' },
  { label: '已仿真', value: 'SIMULATED' },
  { label: '已完成', value: 'COMPLETED' },
  { label: '不确定', value: 'INCONCLUSIVE' },
  { label: '已回退', value: 'ROLLED_BACK' },
  // 旧枚举（兼容期保留）
  { label: '待辨识（旧）', value: 'PENDING' },
  { label: '已应用（旧）', value: 'APPLIED' },
  { label: '已验证（旧）', value: 'VERIFIED' },
];

/** 查询参数 */
const query = reactive({
  algorithm: undefined as TuningApi.Algorithm | undefined,
  status: undefined as TuningApi.TaskStatus | undefined,
  page: 1,
  pageSize: 20,
});

/** 表格列定义 */
const columns: TableColumnsType = [
  {
    title: '回路位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 150,
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
    width: 120,
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
    width: 180,
  },
  { title: '操作', key: 'action', width: 110, fixed: 'right' },
];

function getFittingStatus(value: number): NonNullable<KpiStripItem['status']> {
  if (value >= 80) return 'success';
  if (value >= 60) return 'warning';
  return 'danger';
}

const kpiStripItems = computed<KpiStripItem[]>(() => {
  const totalTasksValue = historyStats.value?.totalTasks || 0;
  const avgFittingValue = avgFitting.value;
  return [
    {
      key: 'total',
      label: '总任务数',
      value: totalTasksValue,
      status: 'neutral',
    },
    {
      key: 'applied',
      label: '已应用数',
      value: appliedCount.value,
      status: 'success',
    },
    {
      key: 'fitting',
      label: '平均拟合度',
      value: avgFittingValue.toFixed(2),
      unit: '%',
      status: getFittingStatus(avgFittingValue),
    },
    {
      key: 'algorithms',
      label: '算法种类数',
      value: algorithmCount.value,
      status: 'neutral',
    },
  ];
});

// ECharts refs
const pieChartRef = ref<EchartsUIType>();
const barChartRef = ref<EchartsUIType>();
const { renderEcharts: renderPie } = useEcharts(pieChartRef);
const { renderEcharts: renderBar } = useEcharts(barChartRef);

/** 算法显示名映射 */
function algorithmName(code: TuningApi.Algorithm): string {
  return algorithmOptions.find((o) => o.value === code)?.label || code;
}

/** 状态显示名映射 */
function statusName(status: TuningApi.TaskStatus): string {
  return statusOptions.find((o) => o.value === status)?.label || status;
}

/** 状态颜色映射（Phase 2 对齐实现契约 v2.1 状态机） */
function statusColor(status: TuningApi.TaskStatus): string {
  switch (status) {
    // Phase 2 新枚举
    case 'COMPLETED': {
      return 'green';
    }
    case 'RUNNING': {
      return 'processing';
    }
    case 'INCONCLUSIVE': {
      return 'orange';
    }
    case 'ROLLED_BACK': {
      return 'red';
    }
    case 'DRAFT': {
      return 'default';
    }
    // 兼容旧枚举
    case 'APPLIED': {
      return 'green';
    }
    case 'IDENTIFIED': {
      return 'cyan';
    }
    case 'PENDING': {
      return 'default';
    }
    case 'SIMULATED': {
      return 'blue';
    }
    case 'VERIFIED': {
      return 'success';
    }
    default: {
      return 'default';
    }
  }
}

/** 模型类型显示名映射 */
function modelTypeName(type: TuningApi.ModelType): string {
  switch (type) {
    case 'FOPDT': {
      return 'FOPDT 一阶加纯滞后';
    }
    case 'IPDT': {
      return 'IPDT 积分加纯滞后';
    }
    case 'SOPDT': {
      return 'SOPDT 二阶加纯滞后';
    }
    default: {
      return type;
    }
  }
}

/** 拟合度颜色 */
function fittingColor(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val))
    return themeColors.value.DANGER;
  if (val >= 80) return themeColors.value.SUCCESS;
  if (val >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

/** 拟合度格式化 */
function formatFitting(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return `${val?.toFixed(2) ?? '0.00'}%`;
}

/** 已应用任务数（Phase 2：COMPLETED + 兼容旧 APPLIED） */
const appliedCount = computed(() => {
  const byStatus = historyStats.value?.byStatus || {};
  return (byStatus.COMPLETED || 0) + (byStatus.APPLIED || 0);
});

/** 算法种类数 */
const algorithmCount = computed(() => {
  return Object.keys(historyStats.value?.byAlgorithm || {}).length;
});

/** 平均拟合度 */
const avgFitting = computed(() => {
  const v = historyStats.value?.avgFittingScore;
  if (v === null || v === undefined || Number.isNaN(v)) return 0;
  return v;
});

/**
 * 算法分类色板（ZL 工业语义色板，随主题响应）
 * 注：色板无紫色，COHEN_COON 以 NEUTRAL(slate) 兜底保持分类可区分
 */
const algoColors = computed<Record<string, string>>(() => ({
  COHEN_COON: themeColors.value.NEUTRAL,
  IMC: themeColors.value.INFO,
  LAMBDA: themeColors.value.SUCCESS,
  SIMC: themeColors.value.ACCENT,
  ZN: themeColors.value.WARNING,
}));

/** 状态分布柱状图色板（ZL 工业语义色板，随主题响应） */
const statusChartColors = computed<Record<string, string>>(() => ({
  // Phase 2 新枚举
  COMPLETED: themeColors.value.SUCCESS,
  RUNNING: themeColors.value.INFO,
  IDENTIFIED: themeColors.value.ACCENT,
  SIMULATED: themeColors.value.INFO,
  INCONCLUSIVE: themeColors.value.WARNING,
  ROLLED_BACK: themeColors.value.DANGER,
  DRAFT: themeColors.value.NEUTRAL,
  // 兼容旧枚举
  APPLIED: themeColors.value.SUCCESS,
  PENDING: themeColors.value.NEUTRAL,
  VERIFIED: themeColors.value.SUCCESS,
}));

/** 加载历史统计 */
async function loadHistory() {
  historyLoading.value = true;
  try {
    const data = await getTuningHistoryApi();
    historyStats.value = data;
    renderPieChart();
    renderBarChart();
  } catch {
    // 错误已由拦截器处理
  } finally {
    historyLoading.value = false;
  }
}

/** 加载任务列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getTuningTasksApi({
      algorithm: query.algorithm,
      status: query.status,
      page: query.page,
      pageSize: query.pageSize,
    });
    taskList.value = data.items || [];
    total.value = data.total || 0;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 渲染算法分布饼图 */
function renderPieChart() {
  const byAlgorithm = historyStats.value?.byAlgorithm || {};
  const entries = Object.entries(byAlgorithm);
  if (entries.length === 0) {
    renderPie({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  const colorMap = algoColors.value;

  renderPie({
    legend: { bottom: 0, orient: 'horizontal' },
    series: [
      {
        avoidLabelOverlap: false,
        data: entries.map(([code, count]) => ({
          itemStyle: { color: colorMap[code] || themeColors.value.NEUTRAL },
          name: algorithmName(code as TuningApi.Algorithm),
          value: count,
        })),
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowColor: 'rgba(0, 0, 0, 0.5)',
            shadowOffsetX: 0,
          },
        },
        label: { formatter: '{b}: {c} ({d}%)', show: true },
        radius: ['40%', '70%'],
        type: 'pie',
      },
    ],
    tooltip: { trigger: 'item' },
  });
}

/** 渲染状态分布柱状图（Phase 2 对齐新状态机） */
function renderBarChart() {
  const byStatus = historyStats.value?.byStatus || {};
  // Phase 2 状态机优先 + 兼容旧枚举（仅展示有数据的）
  const allStatuses: TuningApi.TaskStatus[] = [
    'DRAFT',
    'RUNNING',
    'IDENTIFIED',
    'SIMULATED',
    'COMPLETED',
    'INCONCLUSIVE',
    'ROLLED_BACK',
    'PENDING',
    'APPLIED',
    'VERIFIED',
  ];
  const statusOrder = allStatuses.filter((s) => (byStatus[s] || 0) > 0);
  const colorMap = statusChartColors.value;

  if (statusOrder.length === 0) {
    renderBar({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  renderBar({
    backgroundColor: 'transparent',
    grid: {
      bottom: 40,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 30,
    },
    series: [
      {
        barWidth: '50%',
        data: statusOrder.map((s) => ({
          itemStyle: { color: colorMap[s] },
          value: byStatus[s] || 0,
        })),
        name: '任务数',
        type: 'bar',
      },
    ],
    tooltip: {
      axisPointer: { type: 'shadow' },
      trigger: 'axis',
    },
    xAxis: {
      data: statusOrder.map((s) => statusName(s)),
      type: 'category',
    },
    yAxis: { type: 'value' },
  });
}

function handleSearch() {
  query.page = 1;
  loadList();
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 20;
  loadList();
}

onMounted(() => {
  loadHistory();
  loadList();
});

/** 深色模式切换时重绘 ECharts 图表 */
watch(isDark, () => {
  nextTick(() => {
    renderPieChart();
    renderBarChart();
  });
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="效果统计"
      subtitle="查看整定任务分布、拟合质量与历史效果。"
    />
    <Alert
      type="warning"
      show-icon
      banner
      :closable="false"
      message="只读建议 · 人工实施 · 需留痕"
      description="本平台不直接修改 DCS 的 P/I/D 参数，参数由授权人员人工实施并留痕。"
      style="margin-bottom: 12px"
    />
    <div class="mb-4 mt-4">
      <ClpmKpiStrip :items="kpiStripItems" :loading="historyLoading" />
    </div>

    <!-- 中部图表区 -->
    <div class="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
      <ClpmDataCanvas title="算法分布" :loading="historyLoading">
        <EchartsUI ref="pieChartRef" height="320px" />
      </ClpmDataCanvas>
      <ClpmDataCanvas title="状态分布" :loading="historyLoading">
        <EchartsUI ref="barChartRef" height="320px" />
      </ClpmDataCanvas>
    </div>

    <!-- 底部任务列表 -->
    <ClpmDataCanvas title="整定任务列表">
      <!-- 筛选栏 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="query.algorithm"
          placeholder="算法筛选"
          style="width: 200px"
          allow-clear
          :options="algorithmOptions"
        />
        <Select
          v-model:value="query.status"
          placeholder="状态筛选"
          style="width: 160px"
          allow-clear
          :options="statusOptions"
        />
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
      </div>

      <Table
        :columns="columns"
        :data-source="taskList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: TuningApi.TuningTaskItem) => record.id"
        :scroll="{ x: 1100 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'tagName'">
            {{ record.tagName || record.loopId || '—' }}
          </template>
          <template v-else-if="column.key === 'modelType'">
            {{ modelTypeName(record.modelType as TuningApi.ModelType) }}
          </template>
          <template v-else-if="column.key === 'algorithm'">
            {{ algorithmName(record.algorithm as TuningApi.Algorithm) }}
          </template>
          <template v-else-if="column.key === 'fittingScore'">
            <span :style="{ color: fittingColor(record.fittingScore) }">
              {{ formatFitting(record.fittingScore) }}
            </span>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag :color="statusColor(record.status as TuningApi.TaskStatus)">
              {{ statusName(record.status as TuningApi.TaskStatus) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'createdAt'">
            {{ formatTime(record.createdAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <Button type="link" size="small" disabled> 查看详情 </Button>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>
  </Page>
</template>
