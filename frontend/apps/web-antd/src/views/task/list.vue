<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * 任务管理列表页（UIUX §6.8.1-6.8.3）
 *
 * 对齐 IDS v3.2 §2.7.6 + PRD §4.3.7
 * - Tab 双轨：标准任务（只读）/ 自定义任务（可新建/取消）
 * - 顶部统计摘要：今日执行数 / 成功率 / 平均耗时
 * - 筛选栏：状态 / 时间窗
 * - 表格：任务ID / 类型 / 状态 / 进度 / 当前阶段 / 回路进度 / 创建时间 / 操作
 * - 行点击：展开右侧详情抽屉
 * - 自动轮询：有活跃任务时每 10s 刷新
 */
import type { TaskApi } from '#/api/task';

import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  DatePicker,
  Drawer,
  Form,
  FormItem,
  message,
  Popconfirm,
  Progress,
  Select,
  Space,
  Statistic,
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

const stageNameMap: Record<string, string> = {
  FETCH_DATA: '取数',
  PREPROCESS: '预处理',
  METRIC_CALC: '指标计算',
  CONFIDENCE: '可信度判定',
};

// ---- 选中任务详情抽屉 ----
const drawerVisible = ref(false);
const selectedTask = ref<TaskApi.TaskItem | null>(null);

// ---- 新建自定义任务抽屉 ----
const createDrawerVisible = ref(false);
const createLoading = ref(false);
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

// ---- 轮询 ----
let pollTimer: ReturnType<typeof setInterval> | null = null;

const hasActiveTasks = computed(() =>
  taskList.value.some((t) => t.status === 'PENDING' || t.status === 'RUNNING'),
);

// ---- 统计摘要 ----
const stats = computed(() => {
  const list = taskList.value;
  if (list.length === 0) {
    return { total: 0, successRate: 0, avgDuration: 0 };
  }
  const total = list.length;
  const successCount = list.filter((t) => t.status === 'SUCCESS').length;
  const finished = list.filter(
    (t) => t.startedAt && t.finishedAt,
  );
  let avgDuration = 0;
  if (finished.length > 0) {
    const sum = finished.reduce((acc, t) => {
      const diff = dayjs(t.finishedAt!).diff(dayjs(t.startedAt!), 'second');
      return acc + diff;
    }, 0);
    avgDuration = sum / finished.length;
  }
  return {
    total,
    successRate: Number(((successCount / total) * 100).toFixed(1)),
    avgDuration: Math.round(avgDuration),
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

function openCreateDrawer() {
  createForm.loopIds = [];
  createForm.metrics = [];
  createForm.timeRange = undefined;
  createDrawerVisible.value = true;
  if (loopOptions.value.length === 0) {
    loadLoopOptions();
  }
}

async function handleCreateTask() {
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

  createLoading.value = true;
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
    createDrawerVisible.value = false;
    activeTab.value = 'custom';
    pagination.page = 1;
    loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    createLoading.value = false;
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
  <Page title="任务管理">
    <!-- 统计摘要 -->
    <div class="mb-4 grid grid-cols-3 gap-3">
      <Card size="small" :loading="loading">
        <Statistic title="任务总数" :value="stats.total" />
      </Card>
      <Card size="small" :loading="loading">
        <Statistic
          title="成功率"
          :value="stats.successRate"
          suffix="%"
          :value-style="{ color: '#52c41a' }"
        />
      </Card>
      <Card size="small" :loading="loading">
        <Statistic
          title="平均耗时"
          :value="stats.avgDuration"
          suffix="s"
        />
      </Card>
    </div>

    <Card>
      <Tabs v-model:active-key="activeTab">
        <TabPane key="standard" tab="标准任务" />
        <TabPane key="custom" tab="自定义任务" />
      </Tabs>

      <!-- 筛选栏 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="filter.status"
          placeholder="状态筛选"
          style="width: 140px"
          allow-clear
          :options="Object.entries(statusNameMap).map(([value, label]) => ({ label, value }))"
          @change="handleSearch"
        />
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
        <div class="flex-1"></div>
        <Button
          v-if="activeTab === 'standard'"
          type="primary"
          ghost
          @click="handleTriggerStandard"
        >
          手动触发标准评估
        </Button>
        <Button
          v-if="activeTab === 'custom'"
          type="primary"
          @click="openCreateDrawer"
        >
          + 新建自定义任务
        </Button>
      </div>

      <!-- 任务列表 -->
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
                <Button
                  type="link"
                  size="small"
                  danger
                  @click.stop
                >
                  取消
                </Button>
              </Popconfirm>
            </Space>
          </template>
        </template>
      </Table>
    </Card>

    <!-- 任务详情抽屉 -->
    <Drawer
      v-model:open="drawerVisible"
      title="任务摘要"
      width="520"
      placement="right"
    >
      <template v-if="selectedTask">
        <div class="mb-4">
          <h3 class="text-lg font-semibold">
            {{ selectedTask.taskType === 'STANDARD' ? '标准评估任务' : '自定义评估任务' }}
          </h3>
          <p class="font-mono text-xs text-gray-500">{{ selectedTask.taskId }}</p>
        </div>

        <div class="mb-4">
          <div class="mb-1 text-xs text-gray-500">进度</div>
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

        <div class="space-y-2">
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">状态</span>
            <Tag :color="statusColorMap[selectedTask.status]">
              {{ statusNameMap[selectedTask.status] }}
            </Tag>
          </div>
          <div v-if="selectedTask.currentStage" class="flex justify-between border-b pb-2">
            <span class="text-gray-500">当前阶段</span>
            <span>{{ stageNameMap[selectedTask.currentStage] || selectedTask.currentStage }}</span>
          </div>
          <div v-if="selectedTask.loopsTotal" class="flex justify-between border-b pb-2">
            <span class="text-gray-500">回路进度</span>
            <span>{{ selectedTask.loopsDone || 0 }} / {{ selectedTask.loopsTotal }}</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">创建人</span>
            <span>{{ selectedTask.createdBy }}</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">创建时间</span>
            <span>{{ formatTime(selectedTask.createdAt) }}</span>
          </div>
          <div v-if="selectedTask.startedAt" class="flex justify-between border-b pb-2">
            <span class="text-gray-500">开始时间</span>
            <span>{{ formatTime(selectedTask.startedAt) }}</span>
          </div>
          <div v-if="selectedTask.finishedAt" class="flex justify-between border-b pb-2">
            <span class="text-gray-500">完成时间</span>
            <span>{{ formatTime(selectedTask.finishedAt) }}</span>
          </div>
          <div v-if="selectedTask.startedAt" class="flex justify-between border-b pb-2">
            <span class="text-gray-500">耗时</span>
            <span>{{ formatDuration(selectedTask.startedAt, selectedTask.finishedAt) }}</span>
          </div>
          <div v-if="selectedTask.errorMessage" class="border-b pb-2">
            <div class="mb-1 text-gray-500">错误信息</div>
            <div class="rounded bg-red-50 p-2 text-sm text-red-600">
              {{ selectedTask.errorMessage }}
            </div>
          </div>
        </div>

        <div class="mt-6">
          <Button
            type="primary"
            block
            @click="handleViewDetail(selectedTask.taskId)"
          >
            查看任务详情
          </Button>
        </div>
      </template>
    </Drawer>

    <!-- 新建自定义任务抽屉 -->
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
        ⚠ 自定义任务结果不参与装置级聚合，仅独立展示
      </div>

      <div class="flex justify-end gap-2">
        <Button @click="createDrawerVisible = false">取消</Button>
        <Button
          type="primary"
          :loading="createLoading"
          :disabled="createForm.loopIds.length === 0 || !createForm.timeRange"
          @click="handleCreateTask"
        >
          提交任务
        </Button>
      </div>
    </Drawer>
  </Page>
</template>
