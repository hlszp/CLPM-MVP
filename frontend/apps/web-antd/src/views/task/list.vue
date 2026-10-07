<script lang="ts" setup>
/**
 * 统一任务列表（评估/诊断任务页共用宿主组件）
 *
 * IA 重构二期：手动（BACKFILL）/自动（STANDARD）任务合并为统一列表，
 * 任务类型为可选筛选（default-task-type 由宿主按角色传入，缺省查全部）。
 * 2026-10-05 诊断化改造：scope="diagnosis" 时收敛为诊断口径——隐藏评估
 * 触发按钮、隐藏小时窗口列、空态/确认弹窗文案按 scope 切换、
 * DIAGNOSIS 任务查看结果跳诊断记录页（REPORT 任务无结果页，不展示入口）。
 *
 * - 列表上部左侧：触发标准评估、新建手动评估（仅评估 scope）、批量删除、刷新；右侧：类型/状态/时间筛选
 * - 列表列：任务标题、任务类型、回路数、小时窗口（仅评估）、时间窗口、状态、结果摘要、进度、创建时间、时长、创建人、操作
 * - 自动轮询：有活跃任务时每 5s 刷新
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { TaskApi } from '#/api/task';

import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Plus, RotateCw } from '@vben/icons';

import {
  Button,
  DatePicker,
  Drawer,
  message,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  cancelTaskApi,
  deleteTaskApi,
  getTaskListApi,
  triggerStandardEvaluateApi,
} from '#/api/task';
import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { usePolling } from '#/composables/use-polling';
import {
  statusTokenToAntdColor,
  TASK_STATUS_LABEL,
  TASK_STATUS_TO_STATUS,
} from '#/constants/clpm-ui';
import { TASK_POLLING_INTERVAL } from '#/constants/polling';
import { runWithConcurrency } from '#/utils/concurrency';
import { formatLocalTime, normalizeUtcTimestamp } from '#/utils/format';

import BackfillTaskDrawer from './backfill-task-drawer.vue';

defineOptions({ name: 'TaskList' });

/** 任务筛选与口径由宿主传入：
 *  defaultTaskType：默认类型筛选（缺省 undefined = 全部）；
 *  fixedTaskType：锁定类型（隐藏类型筛选下拉）；
 *  excludeTaskTypes：排除类型（评估/诊断任务页互补切分，2026-10-01 起）；
 *  scope：口径（缺省 'assess' 评估口径；'diagnosis' 收敛诊断口径的
 *  按钮/列/文案/跳转，供诊断模块"诊断任务"页使用）
 */
const props = defineProps<{
  defaultTaskType?: TaskApi.TaskType;
  excludeTaskTypes?: TaskApi.TaskType[];
  fixedTaskType?: TaskApi.TaskType;
  scope?: 'assess' | 'diagnosis';
}>();

/** 轮询每轮回调（0929：父页监听以同步 RUNNING 徽章，此前徽章长期 stale）；
 *  viewDiagnosisRuns：诊断任务"查看结果"上抛宿主（2026-10-05 用户裁决：
 *  右侧抽屉展示该批次诊断记录，不再跳转诊断记录页） */
const emit = defineEmits<{
  polled: [];
  viewDiagnosisRuns: [task: TaskApi.TaskItem];
}>();

const isDiagnosisScope = computed(() => props.scope === 'diagnosis');

const router = useRouter();

const { themeColors } = useClpmTheme();

// ============ 列表状态 ============
const loading = ref(false);
const loadError = ref(false);
const taskList = ref<TaskApi.TaskItem[]>([]);
const totalCount = ref(0);
const currentPage = ref(1);
const pageSize = ref(20);

// 筛选状态
const filterTriggeredBy = ref<string | undefined>();

/** 类型筛选可选集 = 全部类型 − 宿主排除集（2026-10-03：评估任务页排除
 * 诊断/整定/报告后，下拉不再出现这些选项，避免选了查不出结果的误导） */
const TASK_TYPE_OPTIONS: { label: string; value: TaskApi.TaskType }[] = [
  { value: 'STANDARD', label: '标准评估' },
  { value: 'BACKFILL', label: '重算' },
  { value: 'CUSTOM', label: '自定义评估' },
  { value: 'DIAGNOSIS', label: '回路诊断' },
  { value: 'TUNING', label: '整定任务' },
  { value: 'REPORT', label: '报告导出' },
];
const typeOptions = computed(() =>
  TASK_TYPE_OPTIONS.filter(
    (o) => !props.excludeTaskTypes?.includes(o.value),
  ),
);
const filterTaskType = ref<TaskApi.TaskType | undefined>(
  props.fixedTaskType ?? props.defaultTaskType,
);
const filterStatus = ref<TaskApi.TaskStatus | undefined>();
const filterDateRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>();

// 新建手动评估抽屉（收编自原 metric/recompute.vue）
const backfillDrawerOpen = ref(false);

// ============ 状态映射（P2-01：收敛至 constants/clpm-ui.ts）============
const statusColorMap = (status: string) =>
  statusTokenToAntdColor(TASK_STATUS_TO_STATUS[status] ?? 'neutral');

const statusTextMap = TASK_STATUS_LABEL;

const taskTypeTextMap: Record<string, string> = {
  BACKFILL: '重算',
  CUSTOM: '自定义评估',
  STANDARD: '标准评估',
  // 2026-10-01：定时/手动诊断任务进入统一任务列表（每日全量诊断上线），
  // 原缺映射显示英文原串；TUNING/REPORT 同步补齐
  DIAGNOSIS: '回路诊断',
  TUNING: '整定任务',
  REPORT: '报告导出',
};

// ============ 空态文案（评估口径带动作按钮；诊断口径指引宿主页发起入口） ============
const emptyReason = computed(() =>
  isDiagnosisScope.value
    ? '暂无诊断任务记录。可点击右上「发起批量诊断」批量发起，或在回路工作台诊断剖面对单回路发起诊断'
    : '暂无评估任务记录。点击「触发标准评估」可对全部回路执行标准 KPI 评估，或点击「新建手动评估」按时间窗重算',
);
const emptyActionText = computed(() =>
  isDiagnosisScope.value ? '' : '触发标准评估',
);

// ============ 详情 Drawer ============
const drawerVisible = ref(false);
const selectedTask = ref<null | TaskApi.TaskItem>(null);
const selectedRowKeys = ref<string[]>([]);

// ============ 危险操作确认（取消/删除任务：普通确认弹框，无需输入确认码） ============
const dangerVisible = ref(false);
const dangerAction = ref<'batch-delete' | 'cancel' | 'delete'>('delete');
const dangerTask = ref<null | TaskApi.TaskItem>(null);
const dangerLoading = ref(false);

const dangerTitle = computed(() => {
  if (dangerAction.value === 'cancel') return '取消任务';
  if (dangerAction.value === 'delete') return '删除任务记录';
  return '批量删除任务';
});

/** 确认弹窗中的产出术语（评估=KPI 快照 / 诊断=诊断记录） */
const artifactTerm = computed(() =>
  isDiagnosisScope.value ? '诊断记录' : 'KPI 快照',
);

const dangerTarget = computed(() => {
  if (dangerAction.value === 'batch-delete') {
    return `已选 ${selectedRowKeys.value.length} 个任务`;
  }
  return dangerTask.value
    ? dangerTask.value.taskId.slice(-8).toUpperCase()
    : '';
});

const dangerImpact = computed(() => {
  if (dangerAction.value === 'batch-delete') {
    // P3-04：批量删除前预提示不可删除项（非终态任务不可删除）
    const selected = selectedRowKeys.value.length;
    const nonTerminal = taskList.value.filter(
      (t) =>
        selectedRowKeys.value.includes(t.taskId) &&
        !['CANCELLED', 'FAILED', 'SUCCESS'].includes(t.status),
    ).length;
    const deletable = selected - nonTerminal;
    if (nonTerminal > 0) {
      return `已选中 ${selected} 个任务，其中 ${nonTerminal} 个为非终态（执行中/待执行）不可删除，将删除 ${deletable} 个终态任务；不影响已写入的${artifactTerm.value}`;
    }
    return `将删除 ${selected} 条任务记录（仅终态任务可删除），不影响已写入的${artifactTerm.value}`;
  }
  const t = dangerTask.value;
  if (!t) return '';
  const scope = `任务「${getTaskTitle(t)}」（创建时间 ${formatTime(t.createdAt)}）`;
  return dangerAction.value === 'cancel'
    ? `${scope}；取消后计算中止，已写入的${artifactTerm.value}保留`
    : `${scope}；仅删除任务记录，不影响已写入的${artifactTerm.value}`;
});

const dangerRollback = computed(() =>
  dangerAction.value === 'cancel'
    ? (isDiagnosisScope.value
      ? '取消不可撤销；如需诊断可在本页重新发起批量诊断，或在回路工作台诊断剖面单回路发起'
      : '取消不可撤销；如需评估可重新触发标准评估')
    : '任务记录删除后不可恢复',
);

function openDanger(
  action: 'batch-delete' | 'cancel' | 'delete',
  task?: TaskApi.TaskItem,
) {
  dangerAction.value = action;
  dangerTask.value = task ?? null;
  dangerVisible.value = true;
}

async function handleDangerConfirm() {
  dangerLoading.value = true;
  try {
    if (dangerAction.value === 'cancel' && dangerTask.value) {
      await cancelTaskApi(dangerTask.value.taskId);
      message.success('任务已取消');
    } else if (dangerAction.value === 'delete' && dangerTask.value) {
      await deleteTaskApi(dangerTask.value.taskId);
      message.success('任务已删除');
    } else if (dangerAction.value === 'batch-delete') {
      // 并发批量删除（runWithConcurrency 内置 allSettled 语义，单项失败不中断）
      const { rejected } = await runWithConcurrency(
        selectedRowKeys.value,
        (taskId) => deleteTaskApi(taskId),
      );
      if (rejected > 0) {
        message.warning(`删除完成，${rejected} 个任务删除失败（可能非终态）`);
      } else {
        message.success(`已删除 ${selectedRowKeys.value.length} 个任务`);
      }
      selectedRowKeys.value = [];
    }
    dangerVisible.value = false;
    loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    dangerLoading.value = false;
  }
}

function handleCancel(record: TaskApi.TaskItem) {
  openDanger('cancel', record);
}

function handleDelete(record: TaskApi.TaskItem) {
  openDanger('delete', record);
}

function handleBatchDelete() {
  if (selectedRowKeys.value.length === 0) {
    message.warning('请先选择要删除的任务');
    return;
  }
  openDanger('batch-delete');
}

const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys as string[];
  },
}));

// ============ 列定义 ============
/** 小时窗口列仅评估口径展示（重算任务的小时窗计数；诊断任务无此概念恒空） */
const columns = computed<TableColumnsType>(() => [
  {
    title: '任务标题',
    key: 'taskTitle',
    width: 160,
    ellipsis: true,
    align: 'center',
  },
  {
    title: '任务类型',
    dataIndex: 'taskType',
    key: 'taskType',
    width: 100,
    align: 'center',
  },
  {
    title: '回路数',
    dataIndex: 'loopsTotal',
    key: 'loopsTotal',
    width: 90,
    className: 'clpm-num',
    align: 'center',
  },
  ...(isDiagnosisScope.value
    ? []
    : [
        {
          title: '小时窗口',
          dataIndex: 'windowCount',
          key: 'windowCount',
          width: 90,
          className: 'clpm-num',
          align: 'center',
        } as const,
      ]),
  {
    title: '时间窗口',
    key: 'tsRange',
    width: 280,
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
    title: '结果摘要',
    key: 'resultSummary',
    width: 140,
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
    title: '创建时间',
    dataIndex: 'createdAt',
    key: 'createdAt',
    width: 170,
    align: 'center',
  },
  {
    title: '时长',
    key: 'duration',
    width: 100,
    align: 'center',
  },
  {
    title: '创建人',
    dataIndex: 'createdBy',
    key: 'createdBy',
    width: 100,
    ellipsis: true,
    align: 'center',
  },
  {
    title: '操作',
    key: 'action',
    width: 120,
    fixed: 'right',
    align: 'center',
  },
]);

// ============ 加载列表 ============
/** 组装列表查询参数（日期型 RangePicker 结束值需扩展到当日 23:59:59） */
function buildQueryParams(): TaskApi.TaskListQueryParams {
  const params: TaskApi.TaskListQueryParams = {
    page: currentPage.value,
    pageSize: pageSize.value,
  };
  if (filterTaskType.value) params.taskType = filterTaskType.value;
  if (filterTriggeredBy.value) params.triggeredBy = filterTriggeredBy.value;
  if (props.excludeTaskTypes?.length)
    params.excludeTaskTypes = props.excludeTaskTypes.join(',');
  if (filterStatus.value) params.status = filterStatus.value;
  if (filterDateRange.value) {
    params.startTime = filterDateRange.value[0].startOf('day').toISOString();
    params.endTime = filterDateRange.value[1].endOf('day').toISOString();
  }
  return params;
}

/**
 * 筛选条件变化：必须先回到第 1 页再查.
 *
 * 此前筛选控件直接 @change="loadList"，沿用当前页码——在第 3 页切换筛选后，
 * 若结果不足 3 页，后端返回 items=[] 且 total>0，页面会同时显示「共 N 条」
 * 与「暂无评估任务记录」的空态，等于告诉工程师"没有数据"。
 */
function handleFilterChange() {
  currentPage.value = 1;
  void loadList();
}

async function loadList() {
  loading.value = true;
  loadError.value = false;
  try {
    const result = await getTaskListApi(buildQueryParams());
    taskList.value = result.items ?? [];
    totalCount.value = result.total ?? 0;
    updatePolling();
  } catch (error) {
    console.error('加载任务列表失败:', error);
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

// ============ 自动刷新（polling 活跃任务） ============
function hasActiveTask(): boolean {
  return taskList.value.some(
    (t) => t.status === 'RUNNING' || t.status === 'PENDING',
  );
}

/** 判断任务是否处于活跃状态（PENDING/RUNNING） */
function isTaskActive(task: { status: string }): boolean {
  return task.status === 'PENDING' || task.status === 'RUNNING';
}

/** 判断任务是否处于终态（SUCCESS/FAILED/CANCELLED） */
function isTaskTerminal(task: { status: string }): boolean {
  return (
    task.status === 'SUCCESS' ||
    task.status === 'FAILED' ||
    task.status === 'CANCELLED'
  );
}

/** 轮询拉取列表；无活跃任务时自动停止（usePolling 失败熔断 3 次后停止） */
const { start: startPolling, stop: stopPolling } = usePolling(
  async () => {
    const result = await getTaskListApi(buildQueryParams());
    taskList.value = result.items ?? [];
    totalCount.value = result.total ?? 0;
    emit('polled');
    if (!hasActiveTask()) {
      stopPolling();
    }
  },
  { interval: TASK_POLLING_INTERVAL },
);

function updatePolling() {
  if (hasActiveTask()) {
    startPolling();
  } else {
    stopPolling();
  }
}

// ============ 触发标准评估 ============
const triggerLoading = ref(false);

async function handleTriggerStandard() {
  triggerLoading.value = true;
  try {
    await triggerStandardEvaluateApi();
    message.success('标准评估任务已触发');
    loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    triggerLoading.value = false;
  }
}

/** A2（整合方案）：任务完成 → 记录页查看产出
 *  CUSTOM 任务→按任务 ID 定位自定义快照；HOURLY 任务→按来源（手动·标准）过滤；
 *  DIAGNOSIS 任务→上抛宿主开批次记录抽屉（2026-10-05 用户裁决，不再跳页）；
 *  REPORT 任务无结果查看页（按钮不展示，见模板 taskType !== 'REPORT'） */
function viewResults(task: TaskApi.TaskItem) {
  if (task.taskType === 'DIAGNOSIS') {
    emit('viewDiagnosisRuns', task);
    return;
  }
  if (task.taskType === 'CUSTOM') {
    router.push({
      path: '/metric/loop-evaluation',
      query: { view: 'history', source: 'MANUAL_CUSTOM', taskId: task.taskId },
    });
  } else {
    router.push({ path: '/metric/loop-evaluation', query: { view: 'history', source: 'MANUAL_STANDARD' } });
  }
}

// ============ 行点击 → 详情抽屉 ============
function handleRowClick(record: TaskApi.TaskItem) {
  selectedTask.value = record;
  drawerVisible.value = true;
}

// ============ 工具函数 ============
function formatTime(ts: null | string | undefined): string {
  return formatLocalTime(ts, 'YYYY-MM-DD HH:mm');
}

function formatProgress(progress: null | number | undefined): number {
  if (progress === null || progress === undefined) return 0;
  return Math.round(progress * 100);
}

function parseTimestamp(ts: null | string | undefined): null | number {
  if (!ts) return null;
  const d = dayjs(normalizeUtcTimestamp(ts));
  return d.isValid() ? d.valueOf() : null;
}

function formatDuration(record: TaskApi.TaskItem): string {
  const start = parseTimestamp(record.startedAt);
  if (!start) return '—';
  const end = parseTimestamp(record.finishedAt) ?? Date.now();
  const diffSec = Math.floor((end - start) / 1000);
  if (diffSec < 0) return '—';
  const mm = Math.floor(diffSec / 60);
  const ss = diffSec % 60;
  return `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}

function getTaskTitle(record: TaskApi.TaskItem): string {
  if (record.title) return record.title;
  return `${taskTypeTextMap[record.taskType] || record.taskType}-${record.taskId.slice(-8).toUpperCase()}`;
}

// ============ 生命周期 ============
/** P3-01：暴露 refresh() 给 metric/tasks.vue 调用 */
function refresh() {
  return loadList();
}

// 暴露给父组件 + 单元测试的接口（<script setup> 默认私有，需 defineExpose 才能被 vm 访问）
defineExpose({
  refresh,
  artifactTerm,
  columns,
  dangerImpact,
  dangerRollback,
  dangerTitle,
  emptyActionText,
  emptyReason,
  formatProgress,
  formatTime,
  handleCancel,
  handleDangerConfirm,
  handleDelete,
  isDiagnosisScope,
  isTaskActive,
  isTaskTerminal,
  viewResults,
});

onMounted(() => {
  loadList();
});

onUnmounted(() => {
  stopPolling();
});
</script>

<template>
  <div>
    <!-- 工具栏：左侧操作按钮 + 右侧筛选 -->
    <div class="mb-3 flex items-center justify-between gap-3">
      <Space>
        <!-- 宿主扩展位（2026-10-05：诊断任务页「发起批量诊断」放批量删除旁） -->
        <slot name="toolbar-actions"></slot>
        <!-- 评估触发入口仅评估口径展示（诊断口径的发起入口在诊断任务页
             「发起批量诊断」/回路工作台诊断剖面，且评估任务会被
             excludeTaskTypes 过滤出列表，按钮会造成"发起后不可见"断层） -->
        <Button
          v-if="!isDiagnosisScope"
          type="primary"
          :loading="triggerLoading"
          @click="handleTriggerStandard"
        >
          <template #icon><RotateCw /></template>
          触发标准评估
        </Button>
        <!-- 新建手动评估（后端 /tasks/backfill require_roles(ADMIN, IC_ENGINEER)） -->
        <Button
          v-if="!isDiagnosisScope"
          v-permission="['ADMIN', 'IC_ENGINEER']"
          @click="backfillDrawerOpen = true"
        >
          <template #icon><Plus /></template>
          新建手动评估
        </Button>
        <Button
          danger
          :disabled="selectedRowKeys.length === 0"
          :loading="dangerLoading && dangerAction === 'batch-delete'"
          @click="handleBatchDelete"
        >
          批量删除
        </Button>
        <Button @click="loadList">
          <template #icon><RotateCw /></template>
          刷新
        </Button>
      </Space>
      <Space>
        <Select
          v-model:value="filterTriggeredBy"
          placeholder="发起方：全部"
          allow-clear
          style="width: 122px"
          @change="handleFilterChange"
        >
          <Select.Option value="system">自动（定时/系统）</Select.Option>
          <Select.Option value="user">手动（含工作台）</Select.Option>
        </Select>
        <Select
          v-if="!props.fixedTaskType"
          v-model:value="filterTaskType"
          :options="typeOptions"
          placeholder="任务类型：全部"
          allow-clear
          style="width: 150px"
          @change="handleFilterChange"
        />
        <Select
          v-model:value="filterStatus"
          placeholder="状态筛选"
          allow-clear
          style="width: 130px"
          @change="handleFilterChange"
        >
          <Select.Option value="PENDING">待执行</Select.Option>
          <Select.Option value="RUNNING">执行中</Select.Option>
          <Select.Option value="SUCCESS">成功</Select.Option>
          <Select.Option value="FAILED">失败</Select.Option>
          <Select.Option value="CANCELLED">已取消</Select.Option>
        </Select>
        <DatePicker.RangePicker
          v-model:value="filterDateRange"
          :allow-clear="true"
          @change="handleFilterChange"
        />
        <Button type="primary" @click="loadList">查询</Button>
      </Space>
    </div>

    <!-- 任务列表 -->
    <ClpmDataCanvas
      :loading="loading"
      :error="loadError"
      :empty="!loading && !loadError && taskList.length === 0"
      :empty-reason="emptyReason"
      :empty-action-text="emptyActionText"
      @retry="loadList"
      @empty-action="handleTriggerStandard"
    >
      <Table
        :columns="columns"
        :data-source="taskList"
        :row-selection="rowSelection"
        :pagination="{
          current: currentPage,
          pageSize,
          total: totalCount,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        row-key="taskId"
        :scroll="{ x: 1400 }"
        size="middle"
        :custom-row="
          (record: TaskApi.TaskItem) => ({
            onClick: () => handleRowClick(record),
            style: { cursor: 'pointer' },
          })
        "
        @change="
          (p: any) => {
            currentPage = p.current;
            pageSize = p.pageSize;
            loadList();
          }
        "
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'taskTitle'">
            <span class="font-medium">{{
              getTaskTitle(record as TaskApi.TaskItem)
            }}</span>
          </template>
          <template v-else-if="column.key === 'taskType'">
            <Tag>{{ taskTypeTextMap[record.taskType] || record.taskType }}</Tag>
          </template>
          <template v-else-if="column.key === 'loopsTotal'">
            <span v-if="record.loopsTotal" class="font-mono">{{
              record.loopsTotal
            }}</span>
            <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
          </template>
          <template v-else-if="column.key === 'windowCount'">
            <span v-if="record.windowCount" class="font-mono">{{
              record.windowCount
            }}</span>
            <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
          </template>
          <template v-else-if="column.key === 'tsRange'">
            <span class="font-mono text-xs">
              {{ formatTime(record.tsStart) }} ~ {{ formatTime(record.tsEnd) }}
            </span>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag :color="statusColorMap(record.status)">
              {{ statusTextMap[record.status] || record.status }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'resultSummary'">
            <template v-if="record.status === 'SUCCESS'">
              <span class="font-mono text-xs">
                {{ record.loopsDone ?? record.loopsTotal ?? 0 }}/{{
                  record.loopsTotal ?? 0
                }}
                回路
              </span>
            </template>
            <template v-else-if="record.status === 'FAILED'">
              <span
                class="text-xs"
                :style="{ color: themeColors.DANGER }"
                :title="record.errorMessage ?? ''"
              >
                {{ (record.errorMessage ?? '执行失败').slice(0, 20)
                }}{{ (record.errorMessage ?? '').length > 20 ? '…' : '' }}
              </span>
            </template>
            <template v-else-if="record.status === 'RUNNING'">
              <span class="text-xs text-neutral-400">
                {{ record.currentStage ?? '执行中' }}
              </span>
            </template>
            <template v-else>
              <span :style="{ color: themeColors.NEUTRAL }">—</span>
            </template>
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
          <template v-else-if="column.key === 'createdAt'">
            <span class="clpm-num">{{ formatTime(record.createdAt) }}</span>
          </template>
          <template v-else-if="column.key === 'duration'">
            <span class="font-mono">{{
              formatDuration(record as TaskApi.TaskItem)
            }}</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <Space :size="4">
              <Button
                v-if="
                  (record as TaskApi.TaskItem).status === 'SUCCESS' &&
                  (record as TaskApi.TaskItem).taskType !== 'REPORT'
                "
                type="link"
                size="small"
                @click.stop="viewResults(record as TaskApi.TaskItem)"
              >
                查看结果
              </Button>
              <Button
                v-if="
                  (record as TaskApi.TaskItem).status === 'RUNNING' ||
                  (record as TaskApi.TaskItem).status === 'PENDING'
                "
                type="link"
                size="small"
                danger
                @click.stop="handleCancel(record as TaskApi.TaskItem)"
              >
                取消
              </Button>
              <Button
                v-if="
                  ['SUCCESS', 'FAILED', 'CANCELLED'].includes(
                    (record as TaskApi.TaskItem).status,
                  )
                "
                type="link"
                size="small"
                danger
                @click.stop="handleDelete(record as TaskApi.TaskItem)"
              >
                删除
              </Button>
              <span
                v-if="
                  !['SUCCESS', 'FAILED', 'CANCELLED'].includes(
                    (record as TaskApi.TaskItem).status,
                  ) &&
                  (record as TaskApi.TaskItem).status !== 'RUNNING' &&
                  (record as TaskApi.TaskItem).status !== 'PENDING'
                "
                :style="{ color: themeColors.NEUTRAL }"
                >—</span
              >
            </Space>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 新建手动评估抽屉（收编自原 metric/recompute.vue） -->
    <BackfillTaskDrawer v-model:open="backfillDrawerOpen" @success="loadList" />

    <!-- 任务详情抽屉 -->
    <Drawer
      v-model:open="drawerVisible"
      title="任务详情"
      width="480"
      placement="right"
    >
      <template v-if="selectedTask">
        <div class="space-y-3">
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">任务标题</span>
            <span class="font-medium">{{ getTaskTitle(selectedTask) }}</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">任务ID</span>
            <span class="font-mono text-xs">{{ selectedTask.taskId }}</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">任务类型</span>
            <Tag>{{
              taskTypeTextMap[selectedTask.taskType] || selectedTask.taskType
            }}</Tag>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">状态</span>
            <Tag :color="statusColorMap(selectedTask.status)">
              {{ statusTextMap[selectedTask.status] || selectedTask.status }}
            </Tag>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">进度</span>
            <Progress
              :percent="formatProgress(selectedTask.progress)"
              :status="
                selectedTask.status === 'FAILED'
                  ? 'exception'
                  : selectedTask.status === 'SUCCESS'
                    ? 'success'
                    : 'active'
              "
              style="width: 180px"
            />
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">时间窗口</span>
            <span class="font-mono text-xs">
              {{ formatTime(selectedTask.tsStart) }} ~
              {{ formatTime(selectedTask.tsEnd) }}
            </span>
          </div>
          <div
            v-if="selectedTask.loopsTotal"
            class="flex justify-between border-b pb-2"
          >
            <span :style="{ color: themeColors.NEUTRAL }">回路进度</span>
            <span class="font-mono">
              {{ selectedTask.loopsDone || 0 }} / {{ selectedTask.loopsTotal }}
            </span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">创建人</span>
            <span>{{ selectedTask.createdBy || '—' }}</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">创建时间</span>
            <span class="clpm-num">{{
              formatTime(selectedTask.createdAt)
            }}</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">时长</span>
            <span class="font-mono">{{ formatDuration(selectedTask) }}</span>
          </div>
          <div v-if="selectedTask.errorMessage" class="border-b pb-2">
            <div class="mb-1" :style="{ color: themeColors.NEUTRAL }">
              错误信息
            </div>
            <div class="rounded bg-red-50 p-2 text-sm text-red-600">
              {{ selectedTask.errorMessage }}
            </div>
          </div>
        </div>
      </template>
    </Drawer>

    <!-- 危险操作确认（普通确认弹框，无需输入确认码） -->
    <Modal
      v-model:open="dangerVisible"
      :title="dangerTitle"
      :confirm-loading="dangerLoading"
      ok-text="确认"
      cancel-text="取消"
      :ok-button-props="{
        danger: dangerAction !== 'cancel',
        type: dangerAction === 'cancel' ? 'primary' : 'default',
      }"
      @ok="handleDangerConfirm"
    >
      <div class="space-y-2">
        <div>
          <span class="text-gray-500">操作目标：</span>
          <strong>{{ dangerTarget }}</strong>
        </div>
        <div class="text-gray-600">{{ dangerImpact }}</div>
        <div class="text-gray-400 text-sm">{{ dangerRollback }}</div>
      </div>
    </Modal>
  </div>
</template>
