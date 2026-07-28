<script lang="ts" setup>
/**
 * 手动任务页面（评估任务 → 手动任务 Tab）
 *
 * - 列表上部左侧：新建任务、批量删除；右侧：筛选查询
 * - 列表列：多选框、任务标题、任务类型、评估回路、小时窗口、时间窗口、评估状态、评估进度、创建时间、创建人、操作
 * - 发起重算 Drawer：时间窗 + 装置 + 回路 + dry-run 预览 + 确认提交
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { TaskApi } from '#/api/task';

import { computed, onMounted, onUnmounted, ref, watch } from 'vue';

import { Plus, RotateCw } from '@vben/icons';

import {
  Button,
  DatePicker,
  Drawer,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  cancelTaskApi,
  deleteTaskApi,
  getTaskListApi,
  startTaskApi,
  triggerBackfillApi,
} from '#/api/task';
import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { formatLocalTime, normalizeUtcTimestamp } from '#/utils/format';

defineOptions({ name: 'MetricRecompute' });

const { themeColors } = useClpmTheme();

// ============ 列表状态 ============
const loading = ref(false);
const loadError = ref(false);
const taskList = ref<TaskApi.TaskItem[]>([]);
const totalCount = ref(0);
const currentPage = ref(1);
const pageSize = ref(20);
const selectedRowKeys = ref<string[]>([]);

// ============ 危险操作确认（取消/删除任务：普通确认弹框，无需输入确认码） ============
const dangerVisible = ref(false);
const dangerAction = ref<'batch-delete' | 'cancel' | 'delete'>('delete');
const dangerTask = ref<null | TaskApi.TaskItem>(null);
const dangerLoading = ref(false);

const dangerTitle = computed(() => {
  if (dangerAction.value === 'cancel') return '取消评估任务';
  if (dangerAction.value === 'delete') return '删除任务记录';
  return '批量删除任务';
});

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
    return `将删除 ${selectedRowKeys.value.length} 条任务记录（仅终态任务可删除），不影响已写入的 KPI 快照`;
  }
  const t = dangerTask.value;
  if (!t) return '';
  const scope = `任务「${getTaskTitle(t)}」：${t.loopsTotal ?? '—'} 个回路 × ${t.windowCount ?? '—'} 个小时窗口（${formatTime(t.tsStart)} ~ ${formatTime(t.tsEnd)}）`;
  return dangerAction.value === 'cancel'
    ? `${scope}；取消后未计算的窗口不再执行，已写入的快照保留`
    : `${scope}；仅删除任务记录，不影响已写入的 KPI 快照`;
});

const dangerRollback = computed(() =>
  dangerAction.value === 'cancel'
    ? '取消不可撤销；如需评估可重新发起重算任务'
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
      const failed: string[] = [];
      for (const taskId of selectedRowKeys.value) {
        try {
          await deleteTaskApi(taskId);
        } catch {
          failed.push(taskId);
        }
      }
      if (failed.length > 0) {
        message.warning(
          `删除完成，${failed.length} 个任务删除失败（可能非终态）`,
        );
      } else {
        message.success(`已删除 ${selectedRowKeys.value.length} 个任务`);
      }
      selectedRowKeys.value = [];
    }
    dangerVisible.value = false;
    loadList();
  } catch (error: any) {
    message.error(error?.message || '操作失败');
  } finally {
    dangerLoading.value = false;
  }
}

// 筛选状态
const filterStatus = ref<TaskApi.TaskStatus | undefined>();
const filterDateRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>();

// ============ Drawer 状态 ============
const drawerVisible = ref(false);
const drawerLoading = ref(false);
const previewLoading = ref(false);
const previewResult = ref<null | TaskApi.BackfillPreviewResult>(null);

const form = ref({
  title: '',
  tsRange: [
    dayjs().subtract(7, 'day').startOf('hour'),
    dayjs().startOf('hour'),
  ] as [dayjs.Dayjs, dayjs.Dayjs],
  plantNodeIds: [] as string[],
  loopIds: [] as string[],
});

// 装置树数据
const plantNodeTreeData = ref<any[]>([]);
// 回路选项（按已选装置过滤）
const loopOptions = ref<{ label: string; value: string }[]>([]);

// 预览结果仅在当前表单参数下有效：任何影响范围的参数变更后预览失效，
// 强制用户重新 dry-run，避免按过期预览提交（Poka-Yoke 防呆）
watch(
  () => [form.value.tsRange, form.value.plantNodeIds, form.value.loopIds],
  () => {
    previewResult.value = null;
  },
  { deep: true },
);

// ============ 状态映射 ============
const statusColorMap: Record<string, string> = {
  PENDING: 'default',
  RUNNING: 'processing',
  SUCCESS: 'success',
  FAILED: 'error',
  CANCELLED: 'warning',
};

const statusTextMap: Record<string, string> = {
  PENDING: '待执行',
  RUNNING: '执行中',
  SUCCESS: '成功',
  FAILED: '失败',
  CANCELLED: '已取消',
};

const taskTypeTextMap: Record<string, string> = {
  BACKFILL: '手动评估',
  CUSTOM: '自定义评估',
  STANDARD: '标准评估',
};

// ============ 行选择 ============
const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys as string[];
  },
}));

// ============ 列定义 ============
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
    title: '评估回路',
    dataIndex: 'loopsTotal',
    key: 'loopsTotal',
    width: 90,
    className: 'clpm-num',
    align: 'center',
  },
  {
    title: '小时窗口',
    dataIndex: 'windowCount',
    key: 'windowCount',
    width: 90,
    className: 'clpm-num',
    align: 'center',
  },
  {
    title: '时间窗口',
    key: 'tsRange',
    width: 280,
    align: 'center',
  },
  {
    title: '评估状态',
    dataIndex: 'status',
    key: 'status',
    width: 100,
    align: 'center',
  },
  {
    title: '评估进度',
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
    title: '评估时长',
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
  },
]);

// ============ 加载列表 ============
/** 组装列表查询参数（日期型 RangePicker 结束值需扩展到当日 23:59:59） */
function buildQueryParams(): TaskApi.TaskListQueryParams {
  const params: TaskApi.TaskListQueryParams = {
    taskType: 'BACKFILL',
    page: currentPage.value,
    pageSize: pageSize.value,
  };
  if (filterStatus.value) params.status = filterStatus.value;
  if (filterDateRange.value) {
    params.startTime = filterDateRange.value[0].startOf('day').toISOString();
    params.endTime = filterDateRange.value[1].endOf('day').toISOString();
  }
  return params;
}

async function loadList() {
  loading.value = true;
  loadError.value = false;
  try {
    const result = await getTaskListApi(buildQueryParams());
    taskList.value = result.items;
    totalCount.value = result.total;
    updatePolling();
  } catch (error) {
    console.error('加载任务列表失败:', error);
    loadError.value = true;
    message.error('加载任务列表失败');
  } finally {
    loading.value = false;
  }
}

// ============ 自动刷新（polling 活跃任务） ============
const POLLING_INTERVAL = 5000;
let pollingTimer: null | ReturnType<typeof setInterval> = null;

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

function updatePolling() {
  if (hasActiveTask() && !pollingTimer) {
    pollingTimer = setInterval(async () => {
      try {
        const result = await getTaskListApi(buildQueryParams());
        taskList.value = result.items;
        totalCount.value = result.total;
        if (!hasActiveTask()) {
          stopPolling();
        }
      } catch (error) {
        console.error('自动刷新失败:', error);
      }
    }, POLLING_INTERVAL);
  } else if (!hasActiveTask() && pollingTimer) {
    stopPolling();
  }
}

function stopPolling() {
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
}

// ============ 装置树 & 回路选项 ============
async function loadPlantNodeTree() {
  try {
    const result = await getPlantNodeTreeApi();
    plantNodeTreeData.value = transformTreeData(result);
  } catch (error) {
    console.error('加载装置树失败:', error);
  }
}

function transformTreeData(nodes: any[]): any[] {
  return nodes.map((n) => ({
    title: n.name || n.nodeName,
    value: n.id || n.nodeId,
    key: n.id || n.nodeId,
    children: n.children ? transformTreeData(n.children) : undefined,
  }));
}

async function loadLoopOptions() {
  try {
    const allLoops: any[] = [];
    let page = 1;
    const loopPageSize = 100;
    let total = 0;
    do {
      const params: any = { page, pageSize: loopPageSize };
      if (form.value.plantNodeIds.length > 0) {
        params.plantNodeIds = form.value.plantNodeIds.join(',');
      }
      const result = await getLoopListApi(params);
      total = result.total;
      allLoops.push(...(result.items || []));
      page += 1;
    } while ((page - 1) * loopPageSize < total);
    loopOptions.value = allLoops.map((l: any) => ({
      label: l.tagName || l.loopName || l.id,
      value: l.id,
    }));
  } catch (error) {
    console.error('加载回路选项失败:', error);
    loopOptions.value = [];
  }
}

async function onPlantNodeChange() {
  form.value.loopIds = [];
  await loadLoopOptions();
}

// ============ Drawer 操作 ============
function openDrawer() {
  previewResult.value = null;
  form.value = {
    title: '',
    tsRange: [
      dayjs().subtract(7, 'day').startOf('hour'),
      dayjs().startOf('hour'),
    ] as [dayjs.Dayjs, dayjs.Dayjs],
    plantNodeIds: [],
    loopIds: [],
  };
  drawerVisible.value = true;
  loadPlantNodeTree();
  loadLoopOptions();
}

async function handlePreview() {
  if (!form.value.title?.trim()) {
    message.warning('请输入任务标题');
    return;
  }
  if (!form.value.tsRange?.[0] || !form.value.tsRange?.[1]) {
    message.warning('请选择时间窗');
    return;
  }
  const tsStart = form.value.tsRange[0].toISOString();
  const tsEnd = form.value.tsRange[1].toISOString();

  const diffDays = form.value.tsRange[1].diff(form.value.tsRange[0], 'day');
  if (diffDays > 30) {
    message.error('时间窗不能超过 30 天');
    return;
  }

  previewLoading.value = true;
  try {
    const result = await triggerBackfillApi({
      title: form.value.title.trim(),
      tsStart,
      tsEnd,
      plantNodeIds:
        form.value.plantNodeIds.length > 0
          ? form.value.plantNodeIds
          : undefined,
      loopIds: form.value.loopIds.length > 0 ? form.value.loopIds : undefined,
      dryRun: true,
    });
    previewResult.value = result as TaskApi.BackfillPreviewResult;
    message.success('预览完成');
  } catch (error: any) {
    console.error('预览失败:', error);
    message.error(error?.message || '预览失败');
  } finally {
    previewLoading.value = false;
  }
}

async function handleSubmit() {
  if (!previewResult.value) {
    message.warning('请先点击「预览影响范围」');
    return;
  }
  const tsStart = form.value.tsRange[0].toISOString();
  const tsEnd = form.value.tsRange[1].toISOString();

  drawerLoading.value = true;
  try {
    const result = await triggerBackfillApi({
      title: form.value.title.trim(),
      tsStart,
      tsEnd,
      plantNodeIds:
        form.value.plantNodeIds.length > 0
          ? form.value.plantNodeIds
          : undefined,
      loopIds: form.value.loopIds.length > 0 ? form.value.loopIds : undefined,
      dryRun: false,
    });
    const taskId = (result as { taskId: string }).taskId;
    message.success(`任务已创建: ${taskId}`);
    drawerVisible.value = false;
    loadList();
  } catch (error: any) {
    console.error('提交失败:', error);
    message.error(error?.message || '提交失败');
  } finally {
    drawerLoading.value = false;
  }
}

// ============ 删除/取消任务（普通确认弹框） ============
function handleBatchDelete() {
  if (selectedRowKeys.value.length === 0) {
    message.warning('请先选择要删除的任务');
    return;
  }
  openDanger('batch-delete');
}

function handleDelete(record: TaskApi.TaskItem) {
  openDanger('delete', record);
}

function handleCancel(record: TaskApi.TaskItem) {
  openDanger('cancel', record);
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

// ============ 启动任务 ============
const startLoading = ref(false);

async function handleStartTask(record: TaskApi.TaskItem) {
  startLoading.value = true;
  try {
    await startTaskApi(record.taskId);
    message.success('任务已开始执行');
    loadList();
  } catch (error: any) {
    message.error(error?.message || '启动任务失败');
  } finally {
    startLoading.value = false;
  }
}

// ============ 生命周期 ============
onMounted(() => {
  loadList();
});

onUnmounted(() => {
  stopPolling();
});

// 暴露给单元测试的接口（<script setup> 默认私有，需 defineExpose 才能被 vm 访问）
defineExpose({
  columns,
  formatProgress,
  formatTime,
  handleCancel,
  handleDangerConfirm,
  handleDelete,
  isTaskActive,
  isTaskTerminal,
  statusColorMap,
  statusTextMap,
});
</script>

<template>
  <div>
    <!-- 工具栏：左侧操作按钮 + 右侧筛选 -->
    <div class="mb-3 flex items-center justify-between gap-3">
      <Space>
        <Button type="primary" @click="openDrawer">
          <template #icon><Plus /></template>
          新建任务
        </Button>
        <Button
          danger
          :disabled="selectedRowKeys.length === 0"
          :loading="dangerLoading && dangerAction === 'batch-delete'"
          @click="handleBatchDelete"
        >
          删除
        </Button>
        <Button @click="loadList">
          <template #icon><RotateCw /></template>
          刷新
        </Button>
      </Space>
      <Space>
        <Select
          v-model:value="filterStatus"
          placeholder="状态筛选"
          allow-clear
          style="width: 130px"
          @change="loadList"
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
          @change="loadList"
        />
        <Button type="primary" @click="loadList">查询</Button>
      </Space>
    </div>

    <!-- 任务列表 -->
    <ClpmDataCanvas
      :loading="loading"
      :error="loadError"
      :empty="!loading && !loadError && taskList.length === 0"
      @retry="loadList"
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
            <Tag :color="statusColorMap[record.status]">
              {{ statusTextMap[record.status] || record.status }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'progress'">
            <Progress
              :percent="formatProgress(record.progress)"
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
                v-if="(record as TaskApi.TaskItem).status === 'PENDING'"
                type="link"
                size="small"
                :loading="startLoading"
                @click="handleStartTask(record as TaskApi.TaskItem)"
              >
                评估
              </Button>
              <Button
                v-if="(record as TaskApi.TaskItem).status === 'RUNNING'"
                type="link"
                size="small"
                @click="handleCancel(record as TaskApi.TaskItem)"
              >
                取消
              </Button>
              <Button
                type="link"
                size="small"
                @click="handleDelete(record as TaskApi.TaskItem)"
              >
                删除
              </Button>
            </Space>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 发起重算 Drawer -->
    <Drawer
      v-model:open="drawerVisible"
      title="新建任务"
      width="520"
      :mask-closable="false"
    >
      <Form layout="vertical">
        <FormItem label="任务标题" required>
          <Input
            v-model:value="form.title"
            placeholder="请输入任务标题"
            :maxlength="100"
            allow-clear
          />
        </FormItem>

        <FormItem label="时间窗" required>
          <DatePicker.RangePicker
            v-model:value="form.tsRange"
            :allow-clear="false"
            :disabled-date="(d: dayjs.Dayjs) => d.isAfter(dayjs())"
            :show-time="{
              format: 'HH:mm',
              defaultValue: [dayjs().startOf('hour'), dayjs().startOf('hour')],
            }"
            format="YYYY-MM-DD HH:mm"
            style="width: 100%"
          />
          <div class="mt-1 text-xs" :style="{ color: themeColors.NEUTRAL }">
            默认整点时刻（如 01:00~03:00），可精确到分钟；最大 30
            天；按小时窗口批量重算
          </div>
        </FormItem>

        <FormItem label="装置（可选，不选=全部）">
          <TreeSelect
            v-model:value="form.plantNodeIds"
            :tree-data="plantNodeTreeData"
            tree-checkable
            allow-clear
            placeholder="不选=全部装置"
            style="width: 100%"
            @change="onPlantNodeChange"
          />
        </FormItem>

        <FormItem label="回路（可选，不选=对应装置全部）">
          <Select
            v-model:value="form.loopIds"
            mode="multiple"
            allow-clear
            placeholder="不选=对应装置全部回路"
            :options="loopOptions"
            :filter-option="
              (input: string, option: any) =>
                option.label.toLowerCase().includes(input.toLowerCase())
            "
            style="width: 100%"
          />
          <div class="mt-1 text-xs" :style="{ color: themeColors.NEUTRAL }">
            优先级高于装置；支持搜索回路名
          </div>
        </FormItem>

        <!-- 预览结果 -->
        <div
          v-if="previewResult"
          class="mt-4 rounded border border-blue-200 bg-blue-50 p-3"
        >
          <div class="mb-2 font-medium" :style="{ color: themeColors.INFO }">
            影响范围预览
          </div>
          <div class="text-sm">
            <div>回路数：{{ previewResult.loopCount }}</div>
            <div>小时窗口数：{{ previewResult.windowCount }}</div>
            <div>
              预估耗时：{{ Math.ceil(previewResult.estimatedDurationSec / 60) }}
              分钟
            </div>
            <div v-if="previewResult.sampleLoopNames.length > 0">
              样本回路：
              {{ previewResult.sampleLoopNames.join(', ') }}
              <span v-if="previewResult.loopCount > 5">
                等 {{ previewResult.loopCount }} 个</span
              >
            </div>
          </div>
        </div>
      </Form>

      <template #footer>
        <Space>
          <Button @click="drawerVisible = false">取消</Button>
          <Button :loading="previewLoading" @click="handlePreview">
            预览影响范围
          </Button>
          <Button
            type="primary"
            :loading="drawerLoading"
            :disabled="!previewResult"
            @click="handleSubmit"
          >
            确认重算
          </Button>
        </Space>
      </template>
    </Drawer>

    <!-- 危险操作确认（§9.8：取消/删除任务 typed confirmation 屏障） -->
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
