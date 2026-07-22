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

import type { LoopApi } from '#/api/loop';
import type { LoopDataApi } from '#/api/loop-data';

import { computed, h, onMounted, onUnmounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Checkbox,
  DatePicker,
  Input,
  message,
  Modal,
  Progress,
  Radio,
  RadioGroup,
  Select,
  Table,
  Tag,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi } from '#/api/loop';
import {
  cancelImportApi,
  deleteImportApi,
  getImportTasksApi,
  startImportApi,
  triggerBackfillApi,
} from '#/api/loop-data';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { ClpmPageToolbar } from '#/components/clpm';

defineOptions({ name: 'LoopData' });

const { RangePicker } = DatePicker;

// --- 工厂模型节点树 ---
interface TreeNode {
  id: string;
  name: string;
  type: string;
  parentId: null | string;
  children?: TreeNode[];
  pId?: string;
  value?: string;
  title?: string;
  isLeaf?: boolean;
}
const plantTree = ref<TreeNode[]>([]);

/**
 * Phase 10 UX 包：回路列表改服务端分页
 *
 * 原实现 pageSize=100 一次性拉全量 READY 回路，>100 个回路静默丢失。
 * 改为：plantNode 单选 + keyword + 服务端分页（page/pageSize 由后端返回 total）。
 *
 * 单选理由：后端 ``getLoopListApi`` 仅支持单个 ``plantNodeId``，
 * 多选会强制前端 client-side filter，与"服务端分页"目标冲突。
 * TreeSelect 由 ``multiple`` 改为单选，递归子孙节点的过滤仍由后端完成。
 */
const selectedPlantNodeId = ref<string | undefined>();

/** 递归格式化树节点给 TreeSelect */
function formatTreeForSelect(nodes: TreeNode[]): TreeNode[] {
  return nodes.map((n) => {
    const formatted: TreeNode = {
      ...n,
      value: n.id,
      title: n.name,
      pId: n.parentId ?? undefined,
      isLeaf: n.type === 'UNIT',
    };
    if (n.children && n.children.length > 0) {
      formatted.children = formatTreeForSelect(n.children);
    }
    return formatted;
  });
}

async function loadPlantTree() {
  try {
    const resp = await getPlantNodeTreeApi();
    plantTree.value = resp ?? [];
  } catch {
    // 错误已由拦截器处理
  }
}

// --- 回路选择（服务端分页） ---
const loops = ref<LoopApi.LoopListItem[]>([]);
const selectedLoopIds = ref<string[]>([]);
const loadingLoops = ref(false);
const searchKeyword = ref('');

const loopPage = ref(1);
const loopPageSize = ref(20);
const totalLoops = ref(0);

const loopColumns: TableColumnsType = [
  {
    title: '位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 130,
    ellipsis: true,
  },
  {
    title: '名称',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
];

/** 当前页全选状态：仅针对当前页 loops，不再覆盖全部 totalLoops */
const currentPageIds = computed(() => loops.value.map((l) => l.loopId));

const allSelected = computed(
  () =>
    currentPageIds.value.length > 0 &&
    currentPageIds.value.every((id) => selectedLoopIds.value.includes(id)),
);

const indeterminate = computed(() => {
  const selectedInPage = currentPageIds.value.filter((id) =>
    selectedLoopIds.value.includes(id),
  );
  return (
    selectedInPage.length > 0 &&
    selectedInPage.length < currentPageIds.value.length
  );
});

function handleSelectAll(e: any) {
  const pageIds = currentPageIds.value;
  if (e.target.checked) {
    // 选中当前页全部（合并到已选集合，去重）
    const merged = new Set([...selectedLoopIds.value, ...pageIds]);
    selectedLoopIds.value = [...merged];
  } else {
    // 取消当前页全部
    const pageSet = new Set(pageIds);
    selectedLoopIds.value = selectedLoopIds.value.filter(
      (id) => !pageSet.has(id),
    );
  }
}

/** 反选：仅对当前页进行反选 */
function handleInvertSelection() {
  const pageSet = new Set(currentPageIds.value);
  const currentlySelectedInPage = selectedLoopIds.value.filter((id) =>
    pageSet.has(id),
  );
  const currentlyUnselectedInPage = currentPageIds.value.filter(
    (id) => !selectedLoopIds.value.includes(id),
  );
  const selectedOutsidePage = selectedLoopIds.value.filter(
    (id) => !pageSet.has(id),
  );
  selectedLoopIds.value = [
    ...selectedOutsidePage,
    ...currentlyUnselectedInPage,
  ];
  // currentlySelectedInPage 被反选为未选
  void currentlySelectedInPage;
}

/** 清空所有选中（跨页） */
function handleClearSelection() {
  selectedLoopIds.value = [];
}

/** 是否有激活的筛选条件 */
const hasActiveFilters = computed(
  () =>
    selectedPlantNodeId.value !== undefined ||
    searchKeyword.value.trim().length > 0,
);

/** 筛选结果摘要文本（基于服务端 total） */
const filterSummary = computed(() => {
  if (!hasActiveFilters.value) return null;
  if (totalLoops.value === 0) return '无匹配回路';
  return `共 ${totalLoops.value} 个回路`;
});

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

let pollTimer: null | ReturnType<typeof setInterval> = null;

const taskColumns: TableColumnsType = [
  {
    title: '任务ID',
    dataIndex: 'taskId',
    key: 'taskId',
    width: 96,
    ellipsis: true,
    customRender: ({ text }) => `${text.slice(0, 8)}...`,
  },
  {
    title: '回路数',
    dataIndex: 'loopCount',
    key: 'loopCount',
    width: 56,
    align: 'center',
  },
  {
    title: '时间范围',
    key: 'timeRange',
    width: 178,
    customRender: ({ record }) =>
      `${dayjs(record.tsStart).format('MM-DD HH:mm')} ~ ${dayjs(record.tsEnd).format('MM-DD HH:mm')}`,
  },
  {
    title: '进度',
    key: 'progress',
    width: 150,
    customRender: ({ record }) => {
      const pct = Math.round((record.progress ?? 0) * 100);
      return h('div', {}, [
        h(
          'div',
          { class: 'text-xs mb-1' },
          `${record.status === 'RUNNING' ? '执行中...' : ''} ${pct}%`,
        ),
        h(Progress, {
          percent: pct,
          size: 'small',
          strokeColor: record.status === 'FAILED' ? '#ff4d4f' : '#1890ff',
          showInfo: false,
        }),
      ]);
    },
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 72,
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
    width: 126,
    customRender: ({ text }) =>
      text ? dayjs(text).format('MM-DD HH:mm:ss') : '-',
  },
  {
    title: '操作',
    key: 'action',
    width: 104,
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
        // 删除按钮：仅终态任务可删除（活跃任务需先取消）
        h(
          Button,
          {
            size: 'small',
            type: 'link',
            danger: true,
            disabled: isActive,
            onClick: () => handleDelete(record.taskId),
          },
          () => '删除',
        ),
      ]);
    },
  },
];

// --- 方法 ---

/**
 * 加载回路列表（服务端分页）
 *
 * Phase 10 UX 包：取消"一次性 pageSize=100 + 默认全选"的旧实现，
 * 改为标准服务端分页：
 * - page / pageSize 由后端返回 total
 * - keyword 下推到后端模糊搜索
 * - plantNodeId 单选下推到后端递归子孙过滤
 * - selectedLoopIds 不再默认全选，由用户显式勾选
 */
async function loadLoops() {
  loadingLoops.value = true;
  try {
    const resp = await getLoopListApi({
      page: loopPage.value,
      pageSize: loopPageSize.value,
      isActive: true,
      status: 'READY',
      keyword: searchKeyword.value.trim() || undefined,
      plantNodeId: selectedPlantNodeId.value,
    } as any);
    loops.value = resp.items ?? [];
    totalLoops.value = resp.total ?? 0;
    // Phase 10 UX 包：取消默认全选——让用户显式选择要导入的回路，
    // 避免误操作触发大批量远端拉取
  } catch {
    // 错误已由全局拦截器 toast 透传后端 message，这里不再覆盖通用文案
  } finally {
    loadingLoops.value = false;
  }
}

function handleLoopPageChange(pag: TablePaginationConfig) {
  loopPage.value = pag.current ?? 1;
  loopPageSize.value = pag.pageSize ?? loopPageSize.value;
  loadLoops();
}

/** 触发筛选时重置到第 1 页 */
function handleLoopSearch() {
  loopPage.value = 1;
  loadLoops();
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
    // 错误已由拦截器处理
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

  const [rangeStart, rangeEnd] = timeRange.value;
  if (!rangeStart || !rangeEnd) return;
  const tsStart = rangeStart.toISOString();
  const tsEnd = rangeEnd.toISOString();

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
        // Phase 10 UX 包：透传后端错误信息——全局拦截器已显示后端 message，
        // 这里不再覆盖通用文案，避免双重 toast
      } finally {
        importing.value = false;
      }
    },
  });
}

/** Phase 10 UX 包：取消活跃导入任务加二次确认
 * 取消活跃任务可能导致已拉取部分数据被丢弃，需用户显式确认 */
function handleCancel(taskId: string) {
  Modal.confirm({
    title: '确认取消任务',
    content: '取消后该导入任务将停止，已拉取的数据可能不完整。确定取消吗？',
    okText: '取消任务',
    okType: 'danger',
    cancelText: '保留',
    onOk: async () => {
      try {
        await cancelImportApi(taskId);
        message.success('已取消导入任务');
        await loadTasks();
      } catch {
        // 错误已由拦截器透传
      }
    },
  });
}

async function handleBackfill(taskId: string) {
  try {
    const resp = await triggerBackfillApi(taskId);
    message.success(`KPI 回算已触发，共 ${resp.loopCount} 个回路`);
  } catch {
    // 错误已由拦截器透传
  }
}

function handleDelete(taskId: string) {
  Modal.confirm({
    title: '确认删除',
    content: '删除后该导入任务记录将不可恢复，确定删除吗？',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await deleteImportApi(taskId);
        message.success('已删除导入任务');
        await loadTasks();
      } catch {
        // 错误已由拦截器透传
      }
    },
  });
}

function hasActiveTasks() {
  return tasks.value.some(
    (t) => t.status === 'PENDING' || t.status === 'RUNNING',
  );
}

// --- 生命周期 ---

onMounted(async () => {
  await Promise.all([loadPlantTree(), loadLoops(), loadTasks()]);
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

    <div class="mt-4 flex gap-4" style="height: calc(100vh - 200px)">
      <!-- 左侧：回路选择 -->
      <div class="flex w-[30%] flex-col">
        <div
          class="flex flex-1 flex-col overflow-hidden rounded border border-gray-200 bg-white"
        >
          <!-- 面板头部 -->
          <div
            class="shrink-0 border-b px-3 py-2.5"
            style="background: #fafafa"
          >
            <div class="flex items-center justify-between">
              <span class="text-sm font-semibold text-gray-800">回路选择</span>
              <span class="text-xs text-gray-400">
                {{ selectedLoopIds.length }}/{{ totalLoops }}
              </span>
            </div>
          </div>

          <!-- 筛选区 -->
          <div class="shrink-0 space-y-2 px-3 py-2.5">
            <TreeSelect
              v-model:value="selectedPlantNodeId"
              :tree-data="formatTreeForSelect(plantTree)"
              :field-names="{
                children: 'children',
                label: 'title',
                value: 'value',
              }"
              placeholder="按装置/单元筛选..."
              class="w-full"
              :allow-clear="true"
              size="small"
              tree-node-filter-prop="title"
              @change="handleLoopSearch"
            />
            <Input.Search
              v-model:value="searchKeyword"
              placeholder="搜索回路..."
              size="small"
              allow-clear
              @search="handleLoopSearch"
            />
            <!-- 筛选结果提示 -->
            <div
              v-if="filterSummary"
              class="rounded bg-blue-50 px-2 py-1 text-xs text-blue-600"
            >
              {{ filterSummary }}
            </div>
          </div>

          <!-- 操作条 -->
          <div
            class="shrink-0 flex items-center justify-between border-t px-3 py-1.5"
            style="background: #fafafa"
          >
            <Checkbox
              :checked="allSelected"
              :indeterminate="indeterminate"
              @change="handleSelectAll"
            >
              <span class="text-xs">全选</span>
            </Checkbox>
            <div class="flex gap-2">
              <Button
                size="small"
                type="link"
                class="!text-xs !px-1"
                @click="handleInvertSelection"
              >
                反选
              </Button>
              <Button
                size="small"
                type="link"
                class="!text-xs !px-1"
                :disabled="selectedLoopIds.length === 0"
                @click="handleClearSelection"
              >
                清空
              </Button>
            </div>
          </div>

          <!-- 回路表格 -->
          <div class="min-h-0 flex-1 overflow-y-auto">
            <Table
              :columns="loopColumns"
              :data-source="loops"
              :loading="loadingLoops"
              :pagination="{
                current: loopPage,
                pageSize: loopPageSize,
                total: totalLoops,
                showSizeChanger: true,
                pageSizeOptions: ['10', '20', '50', '100'],
                showTotal: (t: number) => `共 ${t} 个`,
                size: 'small',
                showLessItems: true,
              }"
              :row-selection="{
                selectedRowKeys: selectedLoopIds,
                onChange: (keys: any) => (selectedLoopIds = keys as string[]),
              }"
              row-key="loopId"
              size="small"
              :bordered="false"
              class="loop-selection-table"
              @change="handleLoopPageChange"
            >
              <template #emptyText>
                <div class="py-6 text-center text-gray-400">
                  <div class="mb-1 text-lg">&#128269;</div>
                  <div v-if="hasActiveFilters">
                    筛选无结果，尝试调整筛选条件
                  </div>
                  <div v-else>暂无可用回路</div>
                </div>
              </template>
            </Table>
          </div>
        </div>
      </div>

      <!-- 右侧：导入参数 + 任务列表 -->
      <div class="flex min-w-0 w-[70%] flex-col">
        <!-- 导入参数 -->
        <div class="mb-4 shrink-0 rounded border p-4">
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
        <div class="flex min-h-0 flex-1 flex-col rounded border p-4">
          <div class="mb-3 flex shrink-0 items-center justify-between">
            <span class="font-medium">导入任务列表</span>
            <Button size="small" @click="loadTasks">刷新</Button>
          </div>
          <div class="min-h-0 flex-1 overflow-y-auto">
            <Table
              :columns="taskColumns"
              :data-source="tasks"
              :loading="taskLoading"
              :pagination="taskPagination"
              row-key="taskId"
              size="small"
              :scroll="{ x: 768 }"
              @change="handleTaskPageChange"
            />
          </div>
        </div>
      </div>
    </div>
  </Page>
</template>

<style scoped>
.loop-selection-table :deep(.ant-table-cell) {
  border-inline-end: none !important;
}

.loop-selection-table :deep(.ant-table-thead > tr > th) {
  border-inline-end: none !important;
}

.loop-selection-table :deep(.ant-table-tbody > tr.ant-table-row-selected > td) {
  box-shadow: none;
}
</style>
