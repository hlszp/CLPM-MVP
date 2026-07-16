<script lang="ts" setup>
/**
 * 回路数据管理页 — 历史数据导入（Phase 3）
 *
 * 对齐 data-architecture-optimization-spec §5.2
 * 功能：
 * - 选择回路 + 时间范围，从远端 HTTP API 导入历史数据到本地 TDengine
 * - 冲突策略：overwrite（覆盖）/ skip（跳过）
 * - 导入完成后可选触发 KPI 回算
 * - 查看导入任务列表，支持取消和回算
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { LoopDataApi } from '#/api/loop-data';

import { computed, h, onMounted, onUnmounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Checkbox,
  CheckboxGroup,
  DatePicker,
  message,
  Modal,
  Radio,
  RadioGroup,
  Select,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi, type LoopApi } from '#/api/loop';
import {
  cancelImportApi,
  getImportTasksApi,
  startImportApi,
  triggerBackfillApi,
} from '#/api/loop-data';
import { ClpmPageToolbar } from '#/components/clpm';

defineOptions({ name: 'LoopData' });

const { RangePicker } = DatePicker;

// --- 回路选择 ---
const loops = ref<LoopApi.LoopListItem[]>([]);
const selectedLoopIds = ref<string[]>([]);
const loadingLoops = ref(false);
const searchKeyword = ref('');

const filteredLoops = computed(() => {
  if (!searchKeyword.value) return loops.value;
  const kw = searchKeyword.value.toLowerCase();
  return loops.value.filter(
    (l) =>
      l.tagName?.toLowerCase().includes(kw) ||
      l.description?.toLowerCase().includes(kw),
  );
});

const allSelected = computed(
  () =>
    filteredLoops.value.length > 0 &&
    selectedLoopIds.value.length === filteredLoops.value.length,
);

const indeterminate = computed(
  () =>
    selectedLoopIds.value.length > 0 &&
    selectedLoopIds.value.length < filteredLoops.value.length,
);

function handleSelectAll(e: any) {
  if (e.target.checked) {
    selectedLoopIds.value = filteredLoops.value.map((l) => l.loopId);
  } else {
    selectedLoopIds.value = [];
  }
}

// --- 导入参数 ---
const timeRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>([
  dayjs().subtract(7, 'day'),
  dayjs(),
]);
const interval = ref(1);
const conflictStrategy = ref<LoopDataApi.ConflictStrategy>('overwrite');
const triggerBackfill = ref(false);
const importing = ref(false);

// --- 任务列表 ---
const tasks = ref<LoopDataApi.ImportTask[]>([]);
const taskLoading = ref(false);
const taskPagination = ref({
  current: 1,
  pageSize: 10,
  total: 0,
});

let pollTimer: ReturnType<typeof setInterval> | null = null;

const taskColumns: TableColumnsType = [
  {
    title: '任务ID',
    dataIndex: 'taskId',
    key: 'taskId',
    width: 120,
    ellipsis: true,
    customRender: ({ text }) => text.slice(0, 8) + '...',
  },
  {
    title: '回路数',
    dataIndex: 'loopCount',
    key: 'loopCount',
    width: 80,
    align: 'center',
  },
  {
    title: '时间范围',
    key: 'timeRange',
    width: 280,
    customRender: ({ record }) =>
      `${dayjs(record.tsStart).format('MM-DD HH:mm')} ~ ${dayjs(record.tsEnd).format('MM-DD HH:mm')}`,
  },
  {
    title: '进度',
    key: 'progress',
    width: 160,
    customRender: ({ record }) => {
      const pct = Math.round((record.progress ?? 0) * 100);
      return `${record.importedCount}/${record.loopCount} (${pct}%)`;
    },
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 100,
    align: 'center',
    customRender: ({ record }) => {
      const colorMap: Record<string, string> = {
        PENDING: 'default',
        RUNNING: 'processing',
        SUCCESS: 'success',
        FAILED: 'error',
        CANCELLED: 'warning',
      };
      const labelMap: Record<string, string> = {
        PENDING: '待执行',
        RUNNING: '执行中',
        SUCCESS: '已完成',
        FAILED: '失败',
        CANCELLED: '已取消',
      };
      return h(
        Tag,
        { color: colorMap[record.status] ?? 'default' },
        () => labelMap[record.status] ?? record.status,
      );
    },
  },
  {
    title: '创建时间',
    dataIndex: 'createdAt',
    key: 'createdAt',
    width: 160,
    customRender: ({ text }) =>
      text ? dayjs(text).format('YYYY-MM-DD HH:mm:ss') : '-',
  },
  {
    title: '操作',
    key: 'action',
    width: 140,
    fixed: 'right',
    customRender: ({ record }) => {
      const isActive =
        record.status === 'PENDING' || record.status === 'RUNNING';
      return h('div', { class: 'flex gap-1' }, [
        isActive
          ? h(
              Button,
              {
                size: 'small',
                danger: true,
                onClick: () => handleCancel(record.taskId),
              },
              () => '取消',
            )
          : h(
              Button,
              {
                size: 'small',
                type: 'link',
                disabled: record.status !== 'SUCCESS',
                onClick: () => handleBackfill(record.taskId),
              },
              () => '回算',
            ),
      ]);
    },
  },
];

// --- 方法 ---

async function loadLoops() {
  loadingLoops.value = true;
  try {
    const resp = await getLoopListApi({
      page: 1,
      pageSize: 100,
      isActive: true,
      status: 'READY',
    } as any);
    loops.value = resp.items ?? [];
    selectedLoopIds.value = loops.value.map((l) => l.loopId);
  } catch {
    message.error('加载回路列表失败');
  } finally {
    loadingLoops.value = false;
  }
}

async function loadTasks() {
  taskLoading.value = true;
  try {
    const resp = await getImportTasksApi({
      page: taskPagination.value.current,
      pageSize: taskPagination.value.pageSize,
    });
    tasks.value = resp.items ?? [];
    taskPagination.value.total = resp.total ?? 0;
  } catch {
    // 静默处理
  } finally {
    taskLoading.value = false;
  }
}

function handleTaskPageChange(pag: TablePaginationConfig) {
  taskPagination.value.current = pag.current ?? 1;
  loadTasks();
}

async function handleStartImport() {
  if (selectedLoopIds.value.length === 0) {
    message.warning('请至少选择一个回路');
    return;
  }
  if (!timeRange.value || timeRange.value.length !== 2) {
    message.warning('请选择时间范围');
    return;
  }

  const tsStart = timeRange.value[0]!.toISOString();
  const tsEnd = timeRange.value[1]!.toISOString();

  Modal.confirm({
    title: '确认导入',
    content: `将导入 ${selectedLoopIds.value.length} 个回路的历史数据，时间范围 ${dayjs(tsStart).format('YYYY-MM-DD HH:mm')} ~ ${dayjs(tsEnd).format('YYYY-MM-DD HH:mm')}，冲突策略：${conflictStrategy.value === 'overwrite' ? '覆盖' : '跳过'}`,
    okText: '开始导入',
    cancelText: '取消',
    onOk: async () => {
      importing.value = true;
      try {
        await startImportApi({
          loopIds: selectedLoopIds.value,
          tsStart,
          tsEnd,
          interval: interval.value,
          conflictStrategy: conflictStrategy.value,
          triggerBackfill: triggerBackfill.value,
        });
        message.success('导入任务已启动');
        await loadTasks();
      } catch {
        message.error('启动导入失败');
      } finally {
        importing.value = false;
      }
    },
  });
}

async function handleCancel(taskId: string) {
  try {
    await cancelImportApi(taskId);
    message.success('已取消导入任务');
    await loadTasks();
  } catch {
    message.error('取消失败');
  }
}

async function handleBackfill(taskId: string) {
  try {
    const resp = await triggerBackfillApi(taskId);
    message.success(`KPI 回算已触发，共 ${resp.loopCount} 个回路`);
  } catch {
    message.error('触发回算失败');
  }
}

function hasActiveTasks() {
  return tasks.value.some(
    (t) => t.status === 'PENDING' || t.status === 'RUNNING',
  );
}

// --- 生命周期 ---

onMounted(async () => {
  await Promise.all([loadLoops(), loadTasks()]);
  // 有活跃任务时轮询
  pollTimer = setInterval(() => {
    if (hasActiveTasks()) {
      loadTasks();
    }
  }, 5000);
});

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
});

</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="数据管理"
      subtitle="从远端 API 导入历史数据到本地 TDengine，支持冲突处理与 KPI 回算"
    />

    <div class="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
      <!-- 左侧：回路选择 -->
      <div class="lg:col-span-1">
        <div class="rounded border p-3">
          <div class="mb-2 flex items-center justify-between">
            <span class="font-medium">回路选择</span>
            <Checkbox
              :checked="allSelected"
              :indeterminate="indeterminate"
              @change="handleSelectAll"
            >
              全选
            </Checkbox>
          </div>
          <input
            v-model="searchKeyword"
            placeholder="搜索回路..."
            class="mb-2 w-full border px-2 py-1 text-sm"
          />
          <Spin :spinning="loadingLoops">
            <CheckboxGroup
              v-model:value="selectedLoopIds"
              class="flex flex-col"
              style="max-height: 400px; overflow-y: auto"
            >
              <div
                v-for="loop in filteredLoops"
                :key="loop.loopId"
                class="flex items-center py-0.5"
              >
                <Checkbox :value="loop.loopId">
                  <span class="text-sm">{{ loop.tagName }}</span>
                  <span
                    v-if="loop.description"
                    class="ml-1 text-xs text-gray-400"
                  >
                    {{ loop.description }}
                  </span>
                </Checkbox>
              </div>
            </CheckboxGroup>
          </Spin>
          <div class="mt-2 text-xs text-gray-400">
            已选: {{ selectedLoopIds.length }}/{{ loops.length }}
          </div>
        </div>
      </div>

      <!-- 右侧：导入参数 + 任务列表 -->
      <div class="lg:col-span-2">
        <!-- 导入参数 -->
        <div class="mb-4 rounded border p-4">
          <div class="mb-3 font-medium">历史数据导入</div>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="mb-1 block text-sm text-gray-500">时间范围</label>
              <RangePicker
                v-model:value="timeRange"
                show-time
                format="YYYY-MM-DD HH:mm"
                class="w-full"
              />
            </div>
            <div>
              <label class="mb-1 block text-sm text-gray-500">采样间隔</label>
              <Select v-model:value="interval" class="w-full">
                <Select.Option :value="1">1 秒</Select.Option>
                <Select.Option :value="5">5 秒</Select.Option>
                <Select.Option :value="10">10 秒</Select.Option>
                <Select.Option :value="60">1 分钟</Select.Option>
              </Select>
            </div>
            <div>
              <label class="mb-1 block text-sm text-gray-500">冲突策略</label>
              <RadioGroup v-model:value="conflictStrategy">
                <Radio value="overwrite">覆盖（手工优先）</Radio>
                <Radio value="skip">跳过（保留已有）</Radio>
              </RadioGroup>
            </div>
            <div class="flex items-end">
              <Checkbox v-model:checked="triggerBackfill">
                导入后自动触发 KPI 回算
              </Checkbox>
            </div>
          </div>
          <div class="mt-4 flex justify-end">
            <Button
              type="primary"
              :loading="importing"
              :disabled="selectedLoopIds.length === 0"
              @click="handleStartImport"
            >
              开始导入
            </Button>
          </div>
        </div>

        <!-- 任务列表 -->
        <div class="rounded border p-4">
          <div class="mb-3 flex items-center justify-between">
            <span class="font-medium">导入任务列表</span>
            <Button size="small" @click="loadTasks">刷新</Button>
          </div>
          <Table
            :columns="taskColumns"
            :data-source="tasks"
            :loading="taskLoading"
            :pagination="taskPagination"
            row-key="taskId"
            size="small"
            :scroll="{ x: 900 }"
            @change="handleTaskPageChange"
          />
        </div>
      </div>
    </div>
  </Page>
</template>
