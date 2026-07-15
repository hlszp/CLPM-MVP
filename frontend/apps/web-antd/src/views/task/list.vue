<script lang="ts" setup>
/**
 * 自动任务页面（评估任务 → 自动任务 Tab）
 *
 * 参照手动任务布局：
 * - 列表上部左侧：刷新、触发标准评估；右侧：筛选查询
 * - 列表列：任务标题、任务类型、评估回路、小时窗口、时间窗口、评估状态、评估进度、创建时间、评估时长、创建人、操作
 * - 自动轮询：有活跃任务时每 5s 刷新
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { TaskApi } from '#/api/task';

import { computed, onMounted, onUnmounted, ref } from 'vue';

import { RotateCw } from '@vben/icons';

import {
  Button,
  DatePicker,
  Drawer,
  message,
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

defineOptions({ name: 'TaskList' });

const { themeColors } = useClpmTheme();

// ============ 列表状态 ============
const loading = ref(false);
const loadError = ref(false);
const taskList = ref<TaskApi.TaskItem[]>([]);
const totalCount = ref(0);
const currentPage = ref(1);
const pageSize = ref(20);

// 筛选状态
const filterStatus = ref<TaskApi.TaskStatus | undefined>();
const filterDateRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>();

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
  STANDARD: '自动评估',
};

// ============ 详情 Drawer ============
const drawerVisible = ref(false);
const selectedTask = ref<null | TaskApi.TaskItem>(null);

async function handleCancel(record: TaskApi.TaskItem) {
  if (!window.confirm(`确认取消任务 ${record.taskId.slice(-8).toUpperCase()}？`)) return;
  try {
    await cancelTaskApi(record.taskId);
    message.success('任务已取消');
    loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

async function handleDelete(record: TaskApi.TaskItem) {
  if (!window.confirm(`确认删除任务 ${record.taskId.slice(-8).toUpperCase()}？`)) return;
  try {
    await deleteTaskApi(record.taskId);
    message.success('任务已删除');
    loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

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
    align: 'center',
  },
]);

// ============ 加载列表 ============
async function loadList() {
  loading.value = true;
  loadError.value = false;
  try {
    const params: TaskApi.TaskListQueryParams = {
      taskType: 'STANDARD',
      page: currentPage.value,
      pageSize: pageSize.value,
    };
    if (filterStatus.value) params.status = filterStatus.value;
    if (filterDateRange.value) {
      params.startTime = filterDateRange.value[0].toISOString();
      params.endTime = filterDateRange.value[1].toISOString();
    }
    const result = await getTaskListApi(params);
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
const POLLING_INTERVAL = 5000;
let pollingTimer: null | ReturnType<typeof setInterval> = null;

function hasActiveTask(): boolean {
  return taskList.value.some((t) => t.status === 'RUNNING' || t.status === 'PENDING');
}

function updatePolling() {
  if (hasActiveTask() && !pollingTimer) {
    pollingTimer = setInterval(async () => {
      try {
        const params: TaskApi.TaskListQueryParams = {
          taskType: 'STANDARD',
          page: currentPage.value,
          pageSize: pageSize.value,
        };
        if (filterStatus.value) params.status = filterStatus.value;
        if (filterDateRange.value) {
          params.startTime = filterDateRange.value[0].toISOString();
          params.endTime = filterDateRange.value[1].toISOString();
        }
        const result = await getTaskListApi(params);
        taskList.value = result.items ?? [];
        totalCount.value = result.total ?? 0;
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

// ============ 行点击 → 详情抽屉 ============
function handleRowClick(record: TaskApi.TaskItem) {
  selectedTask.value = record;
  drawerVisible.value = true;
}

// ============ 工具函数 ============
function formatTime(ts: null | string | undefined): string {
  if (!ts) return '—';
  const hasTimezone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(ts);
  const normalized = hasTimezone ? ts : `${ts}Z`;
  return dayjs(normalized).format('YYYY-MM-DD HH:mm');
}

function formatProgress(progress: null | number | undefined): number {
  if (progress === null || progress === undefined) return 0;
  return Math.round(progress * 100);
}

function parseTimestamp(ts: null | string | undefined): null | number {
  if (!ts) return null;
  const hasTimezone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(ts);
  const normalized = hasTimezone ? ts : `${ts}Z`;
  const d = dayjs(normalized);
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
        <Button
          type="primary"
          :loading="triggerLoading"
          @click="handleTriggerStandard"
        >
          触发标准评估
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
            <span class="font-medium">{{ getTaskTitle(record as TaskApi.TaskItem) }}</span>
          </template>
          <template v-else-if="column.key === 'taskType'">
            <Tag>{{ taskTypeTextMap[record.taskType] || record.taskType }}</Tag>
          </template>
          <template v-else-if="column.key === 'loopsTotal'">
            <span v-if="record.loopsTotal" class="font-mono">{{ record.loopsTotal }}</span>
            <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
          </template>
          <template v-else-if="column.key === 'windowCount'">
            <span v-if="record.windowCount" class="font-mono">{{ record.windowCount }}</span>
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
            <span class="font-mono">{{ formatDuration(record as TaskApi.TaskItem) }}</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <Space :size="4">
              <Button
                v-if="(record as TaskApi.TaskItem).status === 'RUNNING' || (record as TaskApi.TaskItem).status === 'PENDING'"
                type="link"
                size="small"
                danger
                @click.stop="handleCancel(record as TaskApi.TaskItem)"
              >
                取消
              </Button>
              <Button
                v-if="['SUCCESS', 'FAILED', 'CANCELLED'].includes((record as TaskApi.TaskItem).status)"
                type="link"
                size="small"
                danger
                @click.stop="handleDelete(record as TaskApi.TaskItem)"
              >
                删除
              </Button>
              <span v-if="!['SUCCESS', 'FAILED', 'CANCELLED'].includes((record as TaskApi.TaskItem).status) && (record as TaskApi.TaskItem).status !== 'RUNNING' && (record as TaskApi.TaskItem).status !== 'PENDING'" :style="{ color: themeColors.NEUTRAL }">—</span>
            </Space>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

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
            <Tag>{{ taskTypeTextMap[selectedTask.taskType] || selectedTask.taskType }}</Tag>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">评估状态</span>
            <Tag :color="statusColorMap[selectedTask.status]">
              {{ statusTextMap[selectedTask.status] || selectedTask.status }}
            </Tag>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">评估进度</span>
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
              {{ formatTime(selectedTask.tsStart) }} ~ {{ formatTime(selectedTask.tsEnd) }}
            </span>
          </div>
          <div v-if="selectedTask.loopsTotal" class="flex justify-between border-b pb-2">
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
            <span class="clpm-num">{{ formatTime(selectedTask.createdAt) }}</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">评估时长</span>
            <span class="font-mono">{{ formatDuration(selectedTask) }}</span>
          </div>
          <div v-if="selectedTask.errorMessage" class="border-b pb-2">
            <div class="mb-1" :style="{ color: themeColors.NEUTRAL }">错误信息</div>
            <div class="rounded bg-red-50 p-2 text-sm text-red-600">
              {{ selectedTask.errorMessage }}
            </div>
          </div>
        </div>
      </template>
    </Drawer>
  </div>
</template>
