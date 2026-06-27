<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * 评估任务列表页（UIUX §6.8.1-6.8.3）
 *
 * 对齐 IDS v3.2 §2.7.6 + PRD §4.3.7
 * - Tab 双轨：标准评估任务（只读）/ 自定义评估任务（可新建/取消）
 * - 顶部 KPI Strip：任务总数 / 成功 / 失败 / 运行中
 * - 状态机可视化：PENDING → RUNNING → SUCCESS/FAILED/CANCELLED
 * - 筛选栏：状态
 * - 表格：任务ID / 类型 / 状态 / 进度 / 当前阶段 / 回路进度 / 创建时间 / 操作
 * - 行点击：展开右侧详情抽屉（ClpmObjectSummaryBar）
 * - 新建任务：提交前弹出配置变更确认弹窗
 * - 自动轮询：有活跃任务时每 10s 刷新
 */
import type { TaskApi } from '#/api/task';
import type {
  KpiStripItem,
  SummaryAction,
  SummaryItem,
} from '#/components/clpm';

import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  DatePicker,
  Drawer,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  cancelTaskApi,
  getTaskListApi,
  triggerCustomEvaluateApi,
  triggerStandardEvaluateApi,
} from '#/api/task';
import { getLoopListApi } from '#/api/loop';
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmObjectSummaryBar,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';

defineOptions({ name: 'TaskList' });

const router = useRouter();
const { TabPane } = Tabs;
const { RangePicker } = DatePicker;

// ---- 状态 ----
const activeTab = ref<'custom' | 'standard'>('standard');
const loading = ref(false);
const taskList = ref<TaskApi.TaskItem[]>([]);
const pagination = reactive({
  page: 1,
  pageSize: 20,
  total: 0,
});
const lastRefresh = ref('');

const filter = reactive({
  status: undefined as TaskApi.TaskStatus | undefined,
});

// ---- 状态映射 ----
const statusColorMap: Record<TaskApi.TaskStatus, string> = {
  PENDING: 'default',
  RUNNING: 'processing',
  SUCCESS: 'success',
  FAILED: 'error',
  CANCELLED: 'warning',
};

const statusNameMap: Record<TaskApi.TaskStatus, string> = {
  PENDING: '待执行',
  RUNNING: '执行中',
  SUCCESS: '成功',
  FAILED: '失败',
  CANCELLED: '已取消',
};

const statusSummaryMap: Record<
  TaskApi.TaskStatus,
  'danger' | 'neutral' | 'primary' | 'success' | 'warning'
> = {
  PENDING: 'neutral',
  RUNNING: 'primary',
  SUCCESS: 'success',
  FAILED: 'danger',
  CANCELLED: 'warning',
};

const stageNameMap: Record<string, string> = {
  FETCH_DATA: '取数',
  PREPROCESS: '预处理',
  METRIC_CALC: '指标计算',
  CONFIDENCE: '可信度判定',
};

// 状态机流转节点
const stateFlow: {
  color: string;
  key: TaskApi.TaskStatus;
  label: string;
}[] = [
  { key: 'PENDING', label: '待执行', color: 'default' },
  { key: 'RUNNING', label: '执行中', color: 'processing' },
  { key: 'SUCCESS', label: '成功', color: 'success' },
  { key: 'FAILED', label: '失败', color: 'error' },
  { key: 'CANCELLED', label: '已取消', color: 'warning' },
];

// ---- 选中任务详情抽屉 ----
const drawerVisible = ref(false);
const selectedTask = ref<TaskApi.TaskItem | null>(null);

// ---- 新建自定义评估任务抽屉 ----
const createDrawerVisible = ref(false);
const loopOptions = ref<{ label: string; value: string }[]>([]);
const metricOptions = [
  { label: '好值率', value: 'good_value_rate' },
  { label: '自控率', value: 'auto_mode_rate' },
  { label: '有效自控率', value: 'effective_auto_rate' },
  { label: '平稳率', value: 'steady_rate' },
  { label: '准确率', value: 'accuracy_rate' },
  { label: '快速率', value: 'fast_response_rate' },
  { label: '振荡率', value: 'oscillation_rate' },
  { label: '饱和率', value: 'saturation_rate' },
];

const createForm = reactive<{
  loopIds: string[];
  metrics: string[];
  timeRange: [dayjs.Dayjs, dayjs.Dayjs] | undefined;
}>({
  loopIds: [],
  metrics: [],
  timeRange: undefined,
});

// ---- 配置变更确认弹窗 ----
const confirmModalVisible = ref(false);
const confirmLoading = ref(false);
const changeDescription = ref('');

// ---- 轮询 ----
let pollTimer: ReturnType<typeof setInterval> | null = null;

const hasActiveTasks = computed(() =>
  taskList.value.some((t) => t.status === 'PENDING' || t.status === 'RUNNING'),
);

// ---- KPI Strip 派生 ----
const kpiStripItems = computed<KpiStripItem[]>(() => {
  const list = taskList.value;
  const total = list.length;
  const successCount = list.filter((t) => t.status === 'SUCCESS').length;
  const failedCount = list.filter((t) => t.status === 'FAILED').length;
  const runningCount = list.filter(
    (t) => t.status === 'RUNNING' || t.status === 'PENDING',
  ).length;
  return [
    { key: 'total', label: '任务总数', value: total, status: 'neutral' },
    { key: 'success', label: '成功数', value: successCount, status: 'success' },
    { key: 'failed', label: '失败数', value: failedCount, status: 'danger' },
    { key: 'running', label: '运行中', value: runningCount, status: 'primary' },
  ];
});

// ---- 详情 Drawer：ObjectSummaryBar 派生 ----
const summaryPrimaryItem = computed<SummaryItem | null>(() => {
  if (!selectedTask.value) return null;
  const t = selectedTask.value;
  return {
    key: 'status',
    label: statusNameMap[t.status],
    value: t.taskId.slice(-8).toUpperCase(),
    status: statusSummaryMap[t.status],
  };
});

const summaryItems = computed<SummaryItem[]>(() => {
  if (!selectedTask.value) return [];
  const t = selectedTask.value;
  const items: SummaryItem[] = [];
  if (t.startedAt) {
    items.push({
      key: 'startedAt',
      label: '开始时间',
      value: formatTime(t.startedAt),
    });
  }
  if (t.finishedAt) {
    items.push({
      key: 'finishedAt',
      label: '结束时间',
      value: formatTime(t.finishedAt),
    });
  }
  if (t.startedAt) {
    items.push({
      key: 'duration',
      label: '耗时',
      value: formatDuration(t.startedAt, t.finishedAt),
    });
  }
  if (t.currentStage) {
    items.push({
      key: 'stage',
      label: '当前阶段',
      value: stageNameMap[t.currentStage] || t.currentStage,
    });
  }
  if (t.loopsTotal) {
    items.push({
      key: 'loops',
      label: '回路进度',
      value: `${t.loopsDone || 0} / ${t.loopsTotal}`,
    });
  }
  return items;
});

const summaryActions = computed<SummaryAction[]>(() => {
  if (!selectedTask.value) return [];
  const actions: SummaryAction[] = [
    {
      key: 'viewLog',
      label: '查看日志',
      icon: 'ant-design:file-text-outlined',
      type: 'default',
    },
    {
      key: 'rerun',
      label: '重新运行',
      icon: 'ant-design:reload-outlined',
      type: 'primary',
    },
  ];
  if (
    selectedTask.value.status === 'PENDING' ||
    selectedTask.value.status === 'RUNNING'
  ) {
    actions.push({
      key: 'cancel',
      label: '取消任务',
      icon: 'ant-design:close-outlined',
      danger: true,
    });
  }
  return actions;
});

// ---- 变更确认弹窗：摘要派生 ----
const changeSummary = computed(() => {
  const metrics =
    createForm.metrics.length > 0
      ? createForm.metrics
          .map((m) => metricOptions.find((o) => o.value === m)?.label || m)
          .join('、')
      : '全部指标';
  const timeRange =
    createForm.timeRange && createForm.timeRange.length === 2
      ? `${createForm.timeRange[0]!.format('YYYY-MM-DD HH:mm')} ~ ${createForm.timeRange[1]!.format('YYYY-MM-DD HH:mm')}`
      : '—';
  return {
    taskType: '自定义评估任务',
    target: `${createForm.loopIds.length} 个回路`,
    metrics,
    timeRange,
    impact: `将触发 ${createForm.loopIds.length} 个回路的 KPI 评估任务`,
  };
});

// ---- 列定义 ----
const columns: TableColumnsType = [
  {
    title: '任务ID',
    dataIndex: 'taskId',
    key: 'taskId',
    width: 200,
    ellipsis: true,
  },
  {
    title: '类型',
    dataIndex: 'taskType',
    key: 'taskType',
    width: 90,
    align: 'center',
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 100,
    align: 'center',
  },
  {
    title: '进度',
    dataIndex: 'progress',
    key: 'progress',
    width: 140,
    align: 'center',
  },
  {
    title: '当前阶段',
    dataIndex: 'currentStage',
    key: 'currentStage',
    width: 110,
    align: 'center',
  },
  {
    title: '回路进度',
    key: 'loops',
    width: 120,
    align: 'center',
  },
  {
    title: '创建时间',
    dataIndex: 'createdAt',
    key: 'createdAt',
    width: 170,
  },
  { title: '操作', key: 'action', width: 160, fixed: 'right' },
];

// ---- 数据加载 ----
async function loadList() {
  loading.value = true;
  try {
    const data = await getTaskListApi({
      taskType: activeTab.value === 'standard' ? 'STANDARD' : 'CUSTOM',
      status: filter.status,
      page: pagination.page,
      pageSize: pagination.pageSize,
    });
    taskList.value = data.items || [];
    pagination.total = data.total ?? 0;
    lastRefresh.value = dayjs().format('HH:mm:ss');
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

async function loadLoopOptions() {
  try {
    const data = await getLoopListApi({ page: 1, pageSize: 9999 });
    loopOptions.value = (data.items || []).map((loop) => ({
      label: `${loop.tagName} (${loop.loopId})`,
      value: loop.loopId,
    }));
  } catch {
    // 错误已由拦截器处理
  }
}

// ---- 操作 ----
function handleSearch() {
  pagination.page = 1;
  loadList();
}

function handleTableChange(p: TablePaginationConfig) {
  pagination.page = p.current || 1;
  pagination.pageSize = p.pageSize || 20;
  loadList();
}

function handleRowClick(record: TaskApi.TaskItem) {
  selectedTask.value = record;
  drawerVisible.value = true;
}

function handleViewDetail(taskId: string) {
  drawerVisible.value = false;
  router.push(`/tasks/${taskId}`);
}

async function handleCancel(taskId: string) {
  try {
    await cancelTaskApi(taskId);
    message.success('任务已取消');
    loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

async function handleCancelFromDrawer(taskId: string) {
  try {
    await cancelTaskApi(taskId);
    message.success('任务已取消');
    drawerVisible.value = false;
    loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

function handleSummaryAction(key: string) {
  if (!selectedTask.value) return;
  const task = selectedTask.value;
  if (key === 'viewLog') {
    handleViewDetail(task.taskId);
  } else if (key === 'rerun') {
    if (task.taskType === 'STANDARD') {
      handleTriggerStandard();
      drawerVisible.value = false;
    } else {
      message.info('自定义任务请通过"新建任务"重新配置');
      drawerVisible.value = false;
      activeTab.value = 'custom';
      openCreateDrawer();
    }
  } else if (key === 'cancel') {
    handleCancelFromDrawer(task.taskId);
  }
}

function openCreateDrawer() {
  createForm.loopIds = [];
  createForm.metrics = [];
  createForm.timeRange = undefined;
  createDrawerVisible.value = true;
  if (loopOptions.value.length === 0) {
    loadLoopOptions();
  }
}

// 提交按钮：先校验，再弹出变更确认弹窗
function openConfirmModal() {
  if (createForm.loopIds.length === 0) {
    message.warning('请至少选择 1 个回路');
    return;
  }
  if (!createForm.timeRange || createForm.timeRange.length !== 2) {
    message.warning('请选择评估时间窗');
    return;
  }
  const tsStart = createForm.timeRange[0]!.format('YYYY-MM-DDTHH:mm:ss');
  const tsEnd = createForm.timeRange[1]!.format('YYYY-MM-DDTHH:mm:ss');
  if (dayjs(tsEnd).isBefore(dayjs(tsStart))) {
    message.warning('结束时间需晚于开始时间');
    return;
  }
  const diffDays = dayjs(tsEnd).diff(dayjs(tsStart), 'day');
  if (diffDays > 30) {
    message.warning('时间跨度不能超过 30 天');
    return;
  }
  changeDescription.value = '';
  confirmModalVisible.value = true;
}

async function handleConfirmSubmit() {
  if (!createForm.timeRange || createForm.timeRange.length !== 2) return;
  const tsStart = createForm.timeRange[0]!.format('YYYY-MM-DDTHH:mm:ss');
  const tsEnd = createForm.timeRange[1]!.format('YYYY-MM-DDTHH:mm:ss');

  confirmLoading.value = true;
  try {
    await triggerCustomEvaluateApi({
      loopIds: createForm.loopIds,
      metrics:
        createForm.metrics.length > 0
          ? createForm.metrics
          : metricOptions.map((m) => m.value),
      tsStart,
      tsEnd,
    });
    message.success('自定义评估任务已提交');
    confirmModalVisible.value = false;
    createDrawerVisible.value = false;
    activeTab.value = 'custom';
    pagination.page = 1;
    loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    confirmLoading.value = false;
  }
}

async function handleTriggerStandard() {
  try {
    await triggerStandardEvaluateApi();
    message.success('标准评估任务已触发');
    activeTab.value = 'standard';
    loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

// ---- 导出 CSV ----
function handleExport() {
  if (taskList.value.length === 0) {
    message.warning('暂无数据可导出');
    return;
  }
  const headers = [
    '任务ID',
    '类型',
    '状态',
    '进度',
    '当前阶段',
    '回路进度',
    '创建时间',
  ];
  const rows = taskList.value.map((t) => [
    t.taskId,
    t.taskType === 'STANDARD' ? '标准' : '自定义',
    statusNameMap[t.status],
    `${formatProgress(t.progress)}%`,
    t.currentStage ? (stageNameMap[t.currentStage] || t.currentStage) : '',
    t.loopsTotal ? `${t.loopsDone || 0}/${t.loopsTotal}` : '',
    formatTime(t.createdAt),
  ]);
  const csv = [headers, ...rows]
    .map((r) =>
      r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(','),
    )
    .join('\n');
  const blob = new Blob([`\uFEFF${csv}`], {
    type: 'text/csv;charset=utf-8;',
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `评估任务列表_${dayjs().format('YYYYMMDD_HHmmss')}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
  message.success('导出成功');
}

// ---- 轮询管理 ----
function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    if (hasActiveTasks.value) {
      loadList();
    }
  }, 10_000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// ---- 工具函数 ----
function formatTime(time?: null | string): string {
  if (!time) return '—';
  const d = dayjs(time);
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : time;
}

function formatDuration(start?: null | string, end?: null | string): string {
  if (!start) return '—';
  const endTime = end ? dayjs(end) : dayjs();
  const diff = endTime.diff(dayjs(start), 'second');
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`;
  return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`;
}

function formatProgress(progress?: null | number): number {
  if (progress === null || progress === undefined) return 0;
  return Math.round(progress * 100);
}

// ---- 生命周期 ----
watch(activeTab, () => {
  pagination.page = 1;
  filter.status = undefined;
  loadList();
});

onMounted(() => {
  loadList();
  startPolling();
});

onUnmounted(() => {
  stopPolling();
});
</script>

<template>
  <Page>
    <!-- 顶部工具栏 -->
    <ClpmPageToolbar
      title="评估任务"
      subtitle="监控标准/自定义评估任务的执行状态与进度"
      :loading="loading"
      :last-refresh="lastRefresh"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="ant-design:reload-outlined"
          label="刷新"
          :loading="loading"
          @click="loadList"
        />
        <ClpmToolbarButton
          v-if="activeTab === 'standard'"
          icon="ant-design:play-circle-outlined"
          label="触发标准评估"
          variant="primary"
          @click="handleTriggerStandard"
        />
        <ClpmToolbarButton
          v-if="activeTab === 'custom'"
          icon="ant-design:plus-outlined"
          label="新建任务"
          variant="primary"
          @click="openCreateDrawer"
        />
        <ClpmToolbarButton
          icon="ant-design:download-outlined"
          label="导出"
          @click="handleExport"
        />
      </template>
    </ClpmPageToolbar>

    <!-- KPI Strip：任务统计 -->
    <ClpmKpiStrip class="mt-3" :items="kpiStripItems" :loading="loading" />

    <!-- 状态机可视化：PENDING → RUNNING → SUCCESS/FAILED/CANCELLED -->
    <div
      class="mt-3 flex flex-wrap items-center gap-2 rounded border bg-card p-3 text-sm"
    >
      <span class="text-muted-foreground">状态流转：</span>
      <template v-for="(s, idx) in stateFlow" :key="s.key">
        <Tag :color="s.color">{{ s.label }}</Tag>
        <span
          v-if="idx < stateFlow.length - 1"
          class="text-muted-foreground"
        >
          →
        </span>
      </template>
    </div>

    <!-- 任务列表数据画布 -->
    <ClpmDataCanvas class="mt-3" title="任务列表">
      <template #extra>
        <Tabs v-model:active-key="activeTab" size="small">
          <TabPane key="standard" tab="标准评估任务" />
          <TabPane key="custom" tab="自定义评估任务" />
        </Tabs>
      </template>

      <!-- 筛选栏 -->
      <div class="mb-3 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="filter.status"
          placeholder="状态筛选"
          style="width: 140px"
          allow-clear
          :options="
            Object.entries(statusNameMap).map(([value, label]) => ({
              label,
              value,
            }))
          "
          @change="handleSearch"
        />
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
      </div>

      <!-- 任务列表表格 -->
      <Table
        :columns="columns"
        :data-source="taskList"
        :loading="loading"
        :pagination="{
          current: pagination.page,
          pageSize: pagination.pageSize,
          total: pagination.total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        row-key="taskId"
        :scroll="{ x: 1180 }"
        size="middle"
        :custom-row="
          (record: TaskApi.TaskItem) => ({
            onClick: () => handleRowClick(record),
            style: { cursor: 'pointer' },
          })
        "
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'taskId'">
            <span class="font-mono text-xs">{{ record.taskId }}</span>
          </template>
          <template v-else-if="column.key === 'taskType'">
            <Tag :color="record.taskType === 'STANDARD' ? 'blue' : 'purple'">
              {{ record.taskType === 'STANDARD' ? '标准' : '自定义' }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag :color="statusColorMap[record.status as TaskApi.TaskStatus]">
              {{ statusNameMap[record.status as TaskApi.TaskStatus] }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'progress'">
            <Progress
              :percent="formatProgress(record.progress)"
              size="small"
              :status="
                record.status === 'FAILED'
                  ? 'exception'
                  : record.status === 'SUCCESS'
                    ? 'success'
                    : 'active'
              "
            />
          </template>
          <template v-else-if="column.key === 'currentStage'">
            <span v-if="record.currentStage">
              {{ stageNameMap[record.currentStage] || record.currentStage }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'loops'">
            <span v-if="record.loopsTotal" class="text-sm">
              {{ record.loopsDone || 0 }} / {{ record.loopsTotal }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'createdAt'">
            {{ formatTime(record.createdAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <Space :size="4">
              <Button
                type="link"
                size="small"
                @click.stop="handleViewDetail(record.taskId)"
              >
                详情
              </Button>
              <Popconfirm
                v-if="
                  record.status === 'PENDING' || record.status === 'RUNNING'
                "
                title="确认取消该任务？"
                ok-text="确认"
                cancel-text="取消"
                @confirm="handleCancel(record.taskId)"
              >
                <Button type="link" size="small" danger @click.stop>
                  取消
                </Button>
              </Popconfirm>
            </Space>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 任务详情抽屉 -->
    <Drawer
      v-model:open="drawerVisible"
      title="任务摘要"
      width="560"
      placement="right"
    >
      <template v-if="selectedTask">
        <!-- ObjectSummaryBar：任务摘要 + 操作 -->
        <ClpmObjectSummaryBar
          :title="
            selectedTask.taskType === 'STANDARD'
              ? '标准评估任务'
              : '自定义评估任务'
          "
          :subtitle="selectedTask.taskId"
          :primary-item="summaryPrimaryItem"
          :items="summaryItems"
          :actions="summaryActions"
          @action="handleSummaryAction"
        />

        <!-- 进度条 -->
        <div class="mt-4">
          <div class="mb-1 text-xs text-gray-500">执行进度</div>
          <Progress
            :percent="formatProgress(selectedTask.progress)"
            :status="
              selectedTask.status === 'FAILED'
                ? 'exception'
                : selectedTask.status === 'SUCCESS'
                  ? 'success'
                  : 'active'
            "
          />
        </div>

        <!-- 补充信息 -->
        <div class="mt-4 space-y-2">
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">创建人</span>
            <span>{{ selectedTask.createdBy }}</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">创建时间</span>
            <span>{{ formatTime(selectedTask.createdAt) }}</span>
          </div>
          <div v-if="selectedTask.errorMessage" class="border-b pb-2">
            <div class="mb-1 text-gray-500">错误信息</div>
            <div class="rounded bg-red-50 p-2 text-sm text-red-600">
              {{ selectedTask.errorMessage }}
            </div>
          </div>
        </div>
      </template>
    </Drawer>

    <!-- 新建自定义评估任务抽屉 -->
    <Drawer
      v-model:open="createDrawerVisible"
      title="新建自定义评估任务"
      width="560"
      placement="right"
    >
      <Form layout="vertical">
        <FormItem label="回路范围" required>
          <Select
            v-model:value="createForm.loopIds"
            mode="multiple"
            placeholder="选择目标回路（至少 1 个）"
            :options="loopOptions"
            show-search
            style="width: 100%"
            :max-tag-count="5"
          />
        </FormItem>
        <FormItem label="评估指标" help="不选则计算全部指标">
          <Select
            v-model:value="createForm.metrics"
            mode="multiple"
            placeholder="选择目标指标子集（可选）"
            :options="metricOptions"
            style="width: 100%"
            :max-tag-count="5"
          />
        </FormItem>
        <FormItem label="评估时间窗" required>
          <RangePicker
            v-model:value="createForm.timeRange"
            style="width: 100%"
            :show-time="{ format: 'HH:mm:ss' }"
            format="YYYY-MM-DD HH:mm:ss"
          />
        </FormItem>
      </Form>

      <div class="mb-4 rounded bg-amber-50 p-3 text-sm text-amber-700">
        ⚠ 自定义评估任务结果不参与装置级聚合，仅独立展示
      </div>

      <div class="flex justify-end gap-2">
        <Button @click="createDrawerVisible = false">取消</Button>
        <Button
          type="primary"
          :disabled="createForm.loopIds.length === 0 || !createForm.timeRange"
          @click="openConfirmModal"
        >
          提交任务
        </Button>
      </div>
    </Drawer>

    <!-- 配置变更确认弹窗 -->
    <Modal
      v-model:open="confirmModalVisible"
      title="配置变更确认"
      width="520"
      :confirm-loading="confirmLoading"
      ok-text="确认提交"
      cancel-text="取消"
      @ok="handleConfirmSubmit"
    >
      <div class="space-y-3">
        <!-- 变更摘要 -->
        <div>
          <div class="mb-1 text-xs text-gray-500">变更摘要</div>
          <div class="rounded bg-gray-50 p-3 text-sm">
            <div class="mb-1">
              <span class="text-gray-500">任务类型：</span>
              <span class="font-medium">{{ changeSummary.taskType }}</span>
            </div>
            <div class="mb-1">
              <span class="text-gray-500">目标对象：</span>
              <span class="font-medium">{{ changeSummary.target }}</span>
            </div>
            <div class="mb-1">
              <span class="text-gray-500">评估指标：</span>
              <span class="font-medium">{{ changeSummary.metrics }}</span>
            </div>
            <div>
              <span class="text-gray-500">时间窗：</span>
              <span class="font-mono text-xs">
                {{ changeSummary.timeRange }}
              </span>
            </div>
          </div>
        </div>

        <!-- 影响范围 -->
        <div>
          <div class="mb-1 text-xs text-gray-500">影响范围</div>
          <div class="rounded bg-blue-50 p-3 text-sm text-blue-700">
            <span class="mr-1">⚠</span>
            {{ changeSummary.impact }}
          </div>
        </div>

        <!-- 变更说明 -->
        <div>
          <div class="mb-1 text-xs text-gray-500">
            变更说明（可选）
          </div>
          <Input.TextArea
            v-model:value="changeDescription"
            placeholder="请填写本次变更的说明、原因或备注"
            :rows="3"
          />
        </div>
      </div>
    </Modal>
  </Page>
</template>
