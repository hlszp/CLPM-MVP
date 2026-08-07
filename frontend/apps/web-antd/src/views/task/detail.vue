<script lang="ts" setup>
import type { TaskApi } from '#/api/task';

/**
 * 任务详情页（UIUX §6.8.4）
 *
 * 对齐 IDS v3.2 §2.7.6 + PRD §4.3.7
 * - 顶部任务摘要：任务ID / 类型 / 状态 / 进度 / 时间窗 / 耗时
 * - 阶段时间线：取数 → 预处理 → 指标计算 → 可信度判定
 * - 错误信息卡片（失败时展示）
 * - 通知列表
 * - 自动轮询：活跃任务每 5s 刷新
 */
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Alert,
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Progress,
  Statistic,
  Table,
  Tag,
  Timeline,
  TimelineItem,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  cancelTaskApi,
  getTaskDetailApi,
  getTaskNotificationsApi,
  markNotificationReadApi,
} from '#/api/task';
import { ClpmPageToolbar, ClpmStandardActions } from '#/components/clpm';
import { usePolling } from '#/composables/use-polling';
import { usePageToolbar, showPageHelp } from '#/composables/use-page-toolbar';
import { normalizeUtcTimestamp } from '#/utils/format';

defineOptions({ name: 'TaskDetail' });

const route = useRoute();
const router = useRouter();

const taskId = computed(() => route.params.taskId as string);
const loading = ref(false);
const task = ref<null | TaskApi.TaskItem>(null);
const notifications = ref<TaskApi.TaskNotification[]>([]);
const cancelLoading = ref(false);

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

const stageOrder = ['FETCH_DATA', 'PREPROCESS', 'METRIC_CALC', 'CONFIDENCE'];

// ---- 计算属性 ----
const isActive = computed(
  () => task.value?.status === 'PENDING' || task.value?.status === 'RUNNING',
);

const isFailed = computed(() => task.value?.status === 'FAILED');

/** 当前阶段索引 */
const currentStageIndex = computed(() => {
  if (!task.value?.currentStage) return -1;
  return stageOrder.indexOf(task.value.currentStage);
});

/** 耗时（后端时间戳 naive 视为 UTC，补 Z 后再与本地当前时刻比较） */
const duration = computed(() => {
  if (!task.value?.startedAt) return '—';
  const start = dayjs(normalizeUtcTimestamp(task.value.startedAt));
  if (!start.isValid()) return '—';
  const end = task.value.finishedAt
    ? dayjs(normalizeUtcTimestamp(task.value.finishedAt))
    : dayjs();
  const diff = end.diff(start, 'second');
  if (diff < 0) return '—';
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`;
  return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`;
});

// ---- 数据加载 ----
async function loadDetail() {
  loading.value = true;
  try {
    task.value = await getTaskDetailApi(taskId.value);
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

async function loadNotifications() {
  try {
    const data = await getTaskNotificationsApi(50);
    notifications.value = (data.items || []).filter(
      (n) => n.taskId === taskId.value,
    );
  } catch {
    // 错误已由拦截器处理
  }
}

// ---- 操作 ----
async function handleCancel() {
  cancelLoading.value = true;
  try {
    await cancelTaskApi(taskId.value);
    await loadDetail();
  } catch {
    // 错误已由拦截器处理
  } finally {
    cancelLoading.value = false;
  }
}

async function handleMarkRead(notification: TaskApi.TaskNotification) {
  try {
    await markNotificationReadApi(notification.taskId);
    await loadNotifications();
  } catch {
    // 错误已由拦截器处理
  }
}

function handleBack() {
  router.push('/tasks');
}

/** 工具栏刷新：重载任务详情与通知 */
function handleRefresh() {
  loadDetail();
  loadNotifications();
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '评估任务详情 帮助',
    content:
      '查看评估任务的执行阶段（取数 → 预处理 → 指标计算 → 可信度判定）、进度、耗时、错误信息与通知。活跃任务（待执行/执行中）每 5 秒自动轮询刷新，进入终态后自动停止。可取消未完成的任务。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));

// ---- 轮询（活跃任务每 5s 刷新；进入终态后自动停止，usePolling 负责防堆积/隐藏暂停/失败熔断） ----
const { start: startPolling, stop: stopPolling } = usePolling(
  async () => {
    if (!isActive.value) {
      stopPolling();
      return;
    }
    await loadDetail();
    await loadNotifications();
  },
  { interval: 5000 },
);

// ---- 工具函数 ----
/**
 * 时间展示统一走 utils/format 约定（naive 视为 UTC 补 Z 转本地）；
 * 保留无效值回退原文的既有行为。
 */
function formatTime(time?: null | string): string {
  if (!time) return '—';
  const d = dayjs(normalizeUtcTimestamp(time));
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : time;
}

function formatProgress(progress?: null | number): number {
  if (progress === null || progress === undefined) return 0;
  return Math.round(progress * 100);
}

function getStageColor(stage: string): string {
  if (!task.value) return 'gray';
  if (task.value.status === 'FAILED' && stage === task.value.currentStage) {
    return 'red';
  }
  const idx = stageOrder.indexOf(stage);
  if (idx < currentStageIndex.value) return 'green';
  if (idx === currentStageIndex.value) return 'blue';
  return 'gray';
}

// ---- 生命周期 ----
onMounted(() => {
  loadDetail();
  loadNotifications();
  startPolling();
});

onUnmounted(() => {
  stopPolling();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="评估任务详情"
      subtitle="执行阶段、错误信息、通知和结果摘要。"
      :loading="loading"
    >
      <template #actions>
        <Button type="link" @click="handleBack">← 返回评估任务列表</Button>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <!-- 任务摘要 -->
    <Card class="mb-4" :loading="loading">
      <template v-if="task">
        <div class="mb-4 flex items-center justify-between">
          <div>
            <h2 class="text-xl font-semibold">
              {{
                task.taskType === 'STANDARD' ? '标准评估任务' : '自定义评估任务'
              }}
              <Tag :color="statusColorMap[task.status]" class="ml-2">
                {{ statusNameMap[task.status] }}
              </Tag>
            </h2>
            <p class="font-mono text-sm text-gray-500">{{ task.taskId }}</p>
          </div>
          <Button
            v-if="isActive"
            danger
            :loading="cancelLoading"
            @click="handleCancel"
          >
            取消任务
          </Button>
        </div>

        <!-- 进度条 -->
        <div class="mb-4">
          <Progress
            :percent="formatProgress(task.progress)"
            :status="
              task.status === 'FAILED'
                ? 'exception'
                : task.status === 'SUCCESS'
                  ? 'success'
                  : 'active'
            "
            :stroke-width="12"
          />
          <div v-if="task.currentStage" class="mt-1 text-sm text-gray-500">
            当前阶段：{{ stageNameMap[task.currentStage] || task.currentStage }}
            <span v-if="task.loopsTotal" class="ml-4">
              回路进度：{{ task.loopsDone || 0 }} / {{ task.loopsTotal }}
            </span>
          </div>
        </div>

        <!-- 统计卡片 -->
        <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Card size="small">
            <Statistic title="创建人" :value="task.createdBy" />
          </Card>
          <Card size="small">
            <Statistic title="创建时间" :value="formatTime(task.createdAt)" />
          </Card>
          <Card size="small">
            <Statistic title="开始时间" :value="formatTime(task.startedAt)" />
          </Card>
          <Card size="small">
            <Statistic title="耗时" :value="duration" />
          </Card>
        </div>
      </template>
    </Card>

    <!-- 阶段时间线 -->
    <Card v-if="task" class="mb-4" title="执行阶段">
      <Timeline>
        <TimelineItem
          v-for="stage in stageOrder"
          :key="stage"
          :color="getStageColor(stage)"
        >
          <div class="flex items-center gap-2">
            <span class="font-medium">{{ stageNameMap[stage] }}</span>
            <Tag
              v-if="stage === task.currentStage && task.status === 'RUNNING'"
              color="processing"
              size="small"
            >
              进行中
            </Tag>
            <Tag
              v-else-if="
                stage === task.currentStage && task.status === 'FAILED'
              "
              color="error"
              size="small"
            >
              失败
            </Tag>
            <span
              v-else-if="stageOrder.indexOf(stage) < currentStageIndex"
              class="text-xs text-green-600"
            >
              ✓ 已完成
            </span>
          </div>
        </TimelineItem>
      </Timeline>
    </Card>

    <!-- 错误信息 -->
    <Card v-if="isFailed && task?.errorMessage" class="mb-4" title="错误信息">
      <Alert
        type="error"
        :message="task.errorMessage"
        show-icon
        :description="task.errorMessage"
      />
    </Card>

    <!-- 任务详情 -->
    <Card v-if="task" class="mb-4" title="任务详情">
      <Descriptions :column="2" bordered size="small">
        <DescriptionsItem label="任务ID">
          <span class="font-mono">{{ task.taskId }}</span>
        </DescriptionsItem>
        <DescriptionsItem label="任务类型">
          {{ task.taskType === 'STANDARD' ? '标准评估' : '自定义评估' }}
        </DescriptionsItem>
        <DescriptionsItem label="状态">
          <Tag :color="statusColorMap[task.status]">
            {{ statusNameMap[task.status] }}
          </Tag>
        </DescriptionsItem>
        <DescriptionsItem label="进度">
          {{ formatProgress(task.progress) }}%
        </DescriptionsItem>
        <DescriptionsItem label="当前阶段">
          {{
            task.currentStage
              ? stageNameMap[task.currentStage] || task.currentStage
              : '—'
          }}
        </DescriptionsItem>
        <DescriptionsItem label="回路进度">
          <span v-if="task.loopsTotal">
            {{ task.loopsDone || 0 }} / {{ task.loopsTotal }}
          </span>
          <span v-else>—</span>
        </DescriptionsItem>
        <DescriptionsItem label="创建人">
          {{ task.createdBy }}
        </DescriptionsItem>
        <DescriptionsItem label="创建时间">
          {{ formatTime(task.createdAt) }}
        </DescriptionsItem>
        <DescriptionsItem label="开始时间">
          {{ formatTime(task.startedAt) }}
        </DescriptionsItem>
        <DescriptionsItem label="完成时间">
          {{ formatTime(task.finishedAt) }}
        </DescriptionsItem>
      </Descriptions>
    </Card>

    <!-- 通知列表 -->
    <Card v-if="notifications.length > 0" title="任务通知">
      <Table
        :columns="[
          { title: '状态', dataIndex: 'status', key: 'status', width: 100 },
          {
            title: '消息',
            dataIndex: 'message',
            key: 'message',
            ellipsis: true,
          },
          {
            title: '时间',
            dataIndex: 'createdAt',
            key: 'createdAt',
            width: 180,
          },
          { title: '操作', key: 'action', width: 100 },
        ]"
        :data-source="notifications"
        row-key="taskId"
        size="small"
        :pagination="false"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'status'">
            <Tag :color="statusColorMap[record.status as TaskApi.TaskStatus]">
              {{ statusNameMap[record.status as TaskApi.TaskStatus] }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'createdAt'">
            {{ formatTime(record.createdAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              type="link"
              size="small"
              @click="handleMarkRead(record as TaskApi.TaskNotification)"
            >
              标记已读
            </Button>
          </template>
        </template>
      </Table>
    </Card>
  </Page>
</template>
