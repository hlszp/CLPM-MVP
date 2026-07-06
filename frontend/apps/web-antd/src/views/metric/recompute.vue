<script lang="ts" setup>
/**
 * 历史重算页面
 *
 * 对齐 spec: docs/过程文档/historical-recompute-design.md
 * - 顶部工具栏：发起重算 + 刷新
 * - 重算记录列表：按装置/时间/回路筛选
 * - 发起重算 Drawer：时间窗 + 装置 + 回路 + dry-run 预览 + 确认提交
 *
 * 路由：/metric/recompute
 * 权限：ADMIN + IC_ENGINEER
 */
import type { TableColumnsType } from 'ant-design-vue';

import { computed, onMounted, onUnmounted, ref } from 'vue';

import { Plus, RotateCw } from '@vben/icons';

import {
  Button,
  DatePicker,
  Drawer,
  Form,
  FormItem,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  TreeSelect,
  message,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  cancelTaskApi,
  deleteTaskApi,
  getTaskListApi,
  triggerBackfillApi,
} from '#/api/task';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { getLoopListApi } from '#/api/loop';
import type { TaskApi } from '#/api/task';

defineOptions({ name: 'MetricRecompute' });

// ============ 列表状态 ============
const loading = ref(false);
const taskList = ref<TaskApi.TaskItem[]>([]);
const totalCount = ref(0);
const currentPage = ref(1);
const pageSize = ref(20);

// 筛选状态
const filterStatus = ref<TaskApi.TaskStatus | undefined>();
const filterDateRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>();
const filterPlantNodeIds = ref<string | undefined>();

// ============ Drawer 状态 ============
const drawerVisible = ref(false);
const drawerLoading = ref(false);
const previewLoading = ref(false);
const previewResult = ref<TaskApi.BackfillPreviewResult | null>(null);

const form = ref({
  tsRange: [dayjs().subtract(7, 'day'), dayjs()] as [dayjs.Dayjs, dayjs.Dayjs],
  plantNodeIds: [] as string[],
  loopIds: [] as string[],
});

// 装置树数据
const plantNodeTreeData = ref<any[]>([]);
// 回路选项（按已选装置过滤）
const loopOptions = ref<{ label: string; value: string }[]>([]);

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

// ============ 列定义 ============
const columns = computed<TableColumnsType>(() => [
  {
    title: '任务ID',
    dataIndex: 'taskId',
    width: 180,
    ellipsis: true,
  },
  {
    title: '时间窗',
    key: 'tsRange',
    width: 280,
  },
  {
    title: '回路数',
    dataIndex: 'loopsTotal',
    width: 90,
  },
  {
    title: '小时窗口',
    dataIndex: 'windowCount',
    width: 100,
  },
  {
    title: '状态',
    dataIndex: 'status',
    width: 100,
  },
  {
    title: '进度',
    dataIndex: 'progress',
    width: 140,
  },
  {
    title: '创建时间',
    dataIndex: 'createdAt',
    width: 170,
  },
  {
    title: '操作',
    key: 'action',
    width: 140,
    fixed: 'right',
  },
]);

// ============ 加载列表 ============
async function loadList() {
  loading.value = true;
  try {
    const params: TaskApi.TaskListQueryParams = {
      taskType: 'BACKFILL',
      page: currentPage.value,
      pageSize: pageSize.value,
    };
    if (filterStatus.value) params.status = filterStatus.value;
    if (filterPlantNodeIds.value) params.plantNodeIds = filterPlantNodeIds.value;
    if (filterDateRange.value) {
      params.startTime = filterDateRange.value[0].toISOString();
      params.endTime = filterDateRange.value[1].toISOString();
    }
    const result = await getTaskListApi(params);
    taskList.value = result.items;
    totalCount.value = result.total;
    // 根据是否有活跃任务自动启停 polling
    updatePolling();
  } catch (error) {
    console.error('加载重算记录失败:', error);
    message.error('加载重算记录失败');
  } finally {
    loading.value = false;
  }
}

// ============ 自动刷新（polling 活跃任务） ============
const POLLING_INTERVAL = 5000; // 5 秒
let pollingTimer: ReturnType<typeof setInterval> | null = null;

function hasActiveTask(): boolean {
  return taskList.value.some(
    (t) => t.status === 'PENDING' || t.status === 'RUNNING',
  );
}

function updatePolling() {
  if (hasActiveTask() && !pollingTimer) {
    pollingTimer = setInterval(async () => {
      // 静默刷新（不显示 loading）
      try {
        const params: TaskApi.TaskListQueryParams = {
          taskType: 'BACKFILL',
          page: currentPage.value,
          pageSize: pageSize.value,
        };
        if (filterStatus.value) params.status = filterStatus.value;
        if (filterPlantNodeIds.value) params.plantNodeIds = filterPlantNodeIds.value;
        if (filterDateRange.value) {
          params.startTime = filterDateRange.value[0].toISOString();
          params.endTime = filterDateRange.value[1].toISOString();
        }
        const result = await getTaskListApi(params);
        taskList.value = result.items;
        totalCount.value = result.total;
        // 无活跃任务时停止 polling
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
    const params: any = { page: 1, pageSize: 1000 };
    if (form.value.plantNodeIds.length > 0) {
      params.plantNodeIds = form.value.plantNodeIds.join(',');
    }
    const result = await getLoopListApi(params);
    loopOptions.value = (result.items || []).map((l: any) => ({
      label: l.tagName || l.loopName || l.id,
      value: l.id,
    }));
  } catch (error) {
    console.error('加载回路选项失败:', error);
    loopOptions.value = [];
  }
}

// 装置选择变化时重新加载回路选项
async function onPlantNodeChange() {
  form.value.loopIds = [];
  await loadLoopOptions();
}

// ============ Drawer 操作 ============
function openDrawer() {
  previewResult.value = null;
  form.value = {
    tsRange: [dayjs().subtract(7, 'day'), dayjs()],
    plantNodeIds: [],
    loopIds: [],
  };
  drawerVisible.value = true;
  loadPlantNodeTree();
  loadLoopOptions();
}

async function handlePreview() {
  if (!form.value.tsRange?.[0] || !form.value.tsRange?.[1]) {
    message.warning('请选择时间窗');
    return;
  }
  const tsStart = form.value.tsRange[0].toISOString();
  const tsEnd = form.value.tsRange[1].toISOString();

  // 时间窗最大 30 天校验
  const diffDays = form.value.tsRange[1].diff(form.value.tsRange[0], 'day');
  if (diffDays > 30) {
    message.error('时间窗不能超过 30 天');
    return;
  }

  previewLoading.value = true;
  try {
    const result = await triggerBackfillApi({
      tsStart,
      tsEnd,
      plantNodeIds:
        form.value.plantNodeIds.length > 0
          ? form.value.plantNodeIds
          : undefined,
      loopIds:
        form.value.loopIds.length > 0 ? form.value.loopIds : undefined,
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
      tsStart,
      tsEnd,
      plantNodeIds:
        form.value.plantNodeIds.length > 0
          ? form.value.plantNodeIds
          : undefined,
      loopIds:
        form.value.loopIds.length > 0 ? form.value.loopIds : undefined,
      dryRun: false,
    });
    const taskId = (result as { taskId: string }).taskId;
    message.success(`历史重算任务已触发: ${taskId}`);
    drawerVisible.value = false;
    loadList();
  } catch (error: any) {
    console.error('提交失败:', error);
    message.error(error?.message || '提交失败');
  } finally {
    drawerLoading.value = false;
  }
}

// ============ 取消任务 ============
async function handleCancel(taskId: string) {
  try {
    await cancelTaskApi(taskId);
    message.success('任务已取消');
    loadList();
  } catch (error: any) {
    message.error(error?.message || '取消失败');
  }
}

// ============ 删除任务 ============
async function handleDelete(taskId: string) {
  try {
    await deleteTaskApi(taskId);
    message.success('任务已删除');
    loadList();
  } catch (error: any) {
    message.error(error?.message || '删除失败');
  }
}

// ============ 工具函数 ============
/**
 * 格式化时间为本地时区（UTC+8）显示。
 *
 * 后端返回的时间可能是：
 * 1. 带 Z 或 +00:00 的 UTC ISO 字符串（如 "2026-07-05T10:00:00Z"）
 *    → dayjs 自动识别为 UTC 并转本地时区显示
 * 2. 不带时区的字符串（如 "2026-07-05 10:00:00"，PostgreSQL TIMESTAMP WITHOUT TIME ZONE）
 *    → 假定为 UTC，手动加 Z 标记后再转本地时区
 *
 * 显式标注 [UTC+8]，避免用户误认为显示的是 UTC 时间。
 */
function formatTime(ts: string | null | undefined): string {
  if (!ts) return '—';
  const hasTimezone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(ts);
  const normalized = hasTimezone ? ts : `${ts}Z`;
  return dayjs(normalized).format('YYYY-MM-DD HH:mm:ss [UTC+8]');
}

function formatProgress(progress: number | null | undefined): number {
  if (progress === null || progress === undefined) return 0;
  return Math.round(progress * 100);
}

function isTaskActive(task: TaskApi.TaskItem): boolean {
  return task.status === 'PENDING' || task.status === 'RUNNING';
}

function isTaskTerminal(task: TaskApi.TaskItem): boolean {
  return (
    task.status === 'SUCCESS' ||
    task.status === 'FAILED' ||
    task.status === 'CANCELLED'
  );
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
  <div class="p-4">
    <!-- 顶部工具栏 -->
    <div class="mb-4 flex items-center justify-between">
      <div class="text-lg font-medium">历史重算</div>
      <Space>
        <Button @click="loadList">
          <template #icon><RotateCw /></template>
          刷新
        </Button>
        <Button type="primary" @click="openDrawer">
          <template #icon><Plus /></template>
          发起重算
        </Button>
      </Space>
    </div>

    <!-- 筛选区 -->
    <div class="mb-4 flex items-center gap-3">
      <Select
        v-model:value="filterStatus"
        placeholder="状态筛选"
        allow-clear
        style="width: 140px"
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
    </div>

    <!-- 重算记录列表 -->
    <Table
      :columns="columns"
      :data-source="taskList"
      :loading="loading"
      :pagination="{
        current: currentPage,
        pageSize: pageSize,
        total: totalCount,
        showSizeChanger: true,
        showTotal: (t: number) => `共 ${t} 条`,
      }"
      row-key="taskId"
      @change="
        (p: any) => {
          currentPage = p.current;
          pageSize = p.pageSize;
          loadList();
        }
      "
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'tsRange'">
          <span class="font-mono text-xs">
            {{ formatTime(record.tsStart) }} ~ {{ formatTime(record.tsEnd) }}
          </span>
        </template>
        <template v-else-if="column.dataIndex === 'status'">
          <Tag :color="statusColorMap[record.status]">
            {{ statusTextMap[record.status] || record.status }}
          </Tag>
        </template>
        <template v-else-if="column.dataIndex === 'progress'">
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
        <template v-else-if="column.dataIndex === 'createdAt'">
          {{ formatTime(record.createdAt) }}
        </template>
        <template v-else-if="column.dataIndex === 'windowCount'">
          <span v-if="record.windowCount" class="font-mono">
            {{ record.windowCount }}
          </span>
          <span v-else class="text-gray-400">—</span>
        </template>
        <template v-else-if="column.key === 'action'">
          <Popconfirm
            v-if="isTaskActive(record as TaskApi.TaskItem)"
            title="确定取消此任务？"
            @confirm="handleCancel(record.taskId)"
          >
            <Button type="link" danger size="small">取消</Button>
          </Popconfirm>
          <Popconfirm
            v-else-if="isTaskTerminal(record as TaskApi.TaskItem)"
            title="确定删除此任务记录？删除后不可恢复。"
            @confirm="handleDelete(record.taskId)"
          >
            <Button type="link" size="small">删除</Button>
          </Popconfirm>
          <span v-else class="text-gray-400">—</span>
        </template>
      </template>
    </Table>

    <!-- 发起重算 Drawer -->
    <Drawer
      v-model:open="drawerVisible"
      title="发起历史重算"
      width="520"
      :mask-closable="false"
    >
      <Form layout="vertical">
        <FormItem label="时间窗" required>
          <DatePicker.RangePicker
            v-model:value="form.tsRange"
            :allow-clear="false"
            :disabled-date="(d: dayjs.Dayjs) => d.isAfter(dayjs())"
            style="width: 100%"
          />
          <div class="mt-1 text-xs text-gray-400">
            最大 30 天；将按小时窗口批量重算
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
          <div class="mt-1 text-xs text-gray-400">
            优先级高于装置；支持搜索回路名
          </div>
        </FormItem>

        <!-- 预览结果 -->
        <div
          v-if="previewResult"
          class="mt-4 rounded border border-blue-200 bg-blue-50 p-3"
        >
          <div class="mb-2 font-medium text-blue-700">影响范围预览</div>
          <div class="text-sm">
            <div>回路数：{{ previewResult.loopCount }}</div>
            <div>小时窗口数：{{ previewResult.windowCount }}</div>
            <div>
              预估耗时：{{ Math.ceil(previewResult.estimatedDurationSec / 60) }} 分钟
            </div>
            <div v-if="previewResult.sampleLoopNames.length > 0">
              样本回路：
              {{ previewResult.sampleLoopNames.join(', ') }}
              <span v-if="previewResult.loopCount > 5"> 等 {{ previewResult.loopCount }} 个</span>
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
  </div>
</template>
