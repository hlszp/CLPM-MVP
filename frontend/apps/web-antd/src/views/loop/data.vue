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
import type { TableRowSelection } from 'ant-design-vue/lib/table/interface';

import type { LoopApi } from '#/api/loop';
import type { LoopDataApi } from '#/api/loop-data';

import { computed, h, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import {
  Alert,
  Button,
  Checkbox,
  DatePicker,
  Input,
  message,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  RadioGroup,
  Select,
  Table,
  Tag,
  Tooltip,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi } from '#/api/loop';
import {
  cancelImportApi,
  checkIntegrityApi,
  deleteImportApi,
  getImportTasksApi,
  startImportApi,
  triggerBackfillApi,
} from '#/api/loop-data';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { usePolling } from '#/composables/use-polling';
import { useTableDensity } from '#/composables/use-table-density';
import { TASK_POLLING_INTERVAL } from '#/constants/polling';
import { runWithConcurrency } from '#/utils/concurrency';

import IntegrityReportDrawer from './components/integrity-report-drawer.vue';

defineOptions({ name: 'LoopData' });

const { RangePicker } = DatePicker;

/**
 * 导入功能角色（与后端 loop_data.py `_IMPORT_ROLES` 对齐：
 * 导入/完整性检查/任务管理端点均 require_roles(ADMIN, IC_ENGINEER, PE_ENGINEER)）
 */
const IMPORT_ROLES = new Set(['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER']);

const route = useRoute();
const userStore = useUserStore();
const { themeColors } = useClpmTheme();

/**
 * 当前用户是否具备导入管理权限。
 * 模板按钮走 v-permission 指令；表格 customRender 内 h() 渲染的按钮
 * 无法用指令，改用本 computed 控制显隐（对齐 v-permission 文档的选型建议）。
 */
const canManageImports = computed(() => {
  const roles = userStore.userInfo?.roles ?? [];
  return roles.some((r) => IMPORT_ROLES.has(r));
});

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
/** 回路列表加载错误（三态分离：接口异常 vs 接口正常但无数据） */
const loopsError = ref<null | string>(null);
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

/** 当前页 ID 集合（表格行渲染用） */
const currentPageIds = computed(() => loops.value.map((l) => l.loopId));

/** 已选 ID 集合（O(1) 查询，供全选/半选状态判定） */
const selectedIdSet = computed(() => new Set(selectedLoopIds.value));

// --- 全选（覆盖当前筛选条件下全部回路，含未显示的分页） ---
const selectAllLoading = ref(false);
/**
 * 筛选全集缓存：signature 为筛选条件签名（装置/单元 + 关键字），
 * ids 为该条件下全部 READY 回路 ID（跨分页累取）。
 * 用途：① 全选/取消全选的作用域；② 全选/半选状态的精确判定。
 * 筛选条件变更时失效（handleLoopSearch 内清空）。
 */
const filteredIdsCache = ref<null | { ids: string[]; signature: string }>(null);

function currentFilterSignature(): string {
  return JSON.stringify({
    plantNodeId: selectedPlantNodeId.value ?? null,
    keyword: searchKeyword.value.trim(),
  });
}

/**
 * 拉取当前筛选条件下全部回路 ID。
 * 后端 GET /loops pageSize 上限 100，按页累取；上限 50 页（5000 个）防失控。
 */
async function fetchAllFilteredLoopIds(): Promise<string[]> {
  const PAGE_SIZE = 100; // 后端 pageSize 校验上限 le=100
  const MAX_PAGES = 50;
  const ids: string[] = [];
  let total = 0;
  for (let page = 1; page <= MAX_PAGES; page++) {
    const resp = await getLoopListApi({
      page,
      pageSize: PAGE_SIZE,
      isActive: true,
      status: 'READY',
      keyword: searchKeyword.value.trim() || undefined,
      plantNodeId: selectedPlantNodeId.value,
    } as any);
    const items = resp.items ?? [];
    total = resp.total ?? 0;
    for (const item of items) ids.push(item.loopId);
    if (ids.length >= total || items.length === 0) break;
  }
  if (ids.length < total) {
    // 超过安全上限（5000）时显式提示，避免静默截断导致"以为全选实则遗漏"
    message.warning(`回路共 ${total} 个，全选仅覆盖前 ${ids.length} 个`);
  }
  return ids;
}

/** 全选状态：缓存有效时按筛选全集精确判定，否则退化为当前页口径 */
const allSelected = computed(() => {
  const cache = filteredIdsCache.value;
  if (cache && cache.signature === currentFilterSignature()) {
    return (
      cache.ids.length > 0 &&
      cache.ids.every((id) => selectedIdSet.value.has(id))
    );
  }
  return (
    currentPageIds.value.length > 0 &&
    currentPageIds.value.every((id) => selectedIdSet.value.has(id))
  );
});

const indeterminate = computed(() => {
  const cache = filteredIdsCache.value;
  if (cache && cache.signature === currentFilterSignature()) {
    const selectedCount = cache.ids.filter((id) =>
      selectedIdSet.value.has(id),
    ).length;
    return selectedCount > 0 && selectedCount < cache.ids.length;
  }
  const selectedInPage = currentPageIds.value.filter((id) =>
    selectedIdSet.value.has(id),
  );
  return (
    selectedInPage.length > 0 &&
    selectedInPage.length < currentPageIds.value.length
  );
});

/**
 * 全选/取消全选：作用于当前筛选条件下全部回路（含未显示分页），
 * 不仅仅是当前页。勾选=并入筛选全集；取消=从已选中剔除筛选全集。
 */
async function handleSelectAll(e: any) {
  const checked = !!e.target.checked;
  selectAllLoading.value = true;
  try {
    const signature = currentFilterSignature();
    if (
      !filteredIdsCache.value ||
      filteredIdsCache.value.signature !== signature
    ) {
      const ids = await fetchAllFilteredLoopIds();
      filteredIdsCache.value = { signature, ids };
    }
    const allIds = filteredIdsCache.value.ids;
    if (checked) {
      selectedLoopIds.value = [
        ...new Set([...selectedLoopIds.value, ...allIds]),
      ];
    } else {
      const removeSet = new Set(allIds);
      selectedLoopIds.value = selectedLoopIds.value.filter(
        (id) => !removeSet.has(id),
      );
    }
  } catch {
    message.error('获取筛选回路全集失败，请重试');
  } finally {
    selectAllLoading.value = false;
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

// --- 数据完整性检查 ---
const integrityChecking = ref(false);
const integrityDrawerVisible = ref(false);
const integrityResult = ref<LoopDataApi.IntegrityCheckResult | null>(null);

// --- 任务列表 ---
const tasks = ref<LoopDataApi.ImportTask[]>([]);
const taskLoading = ref(false);
/** 任务列表加载错误（三态分离：接口异常 vs 接口正常但无数据） */
const tasksError = ref<null | string>(null);
const taskPagination = ref({
  current: 1,
  pageSize: 10,
  total: 0,
  showSizeChanger: false,
});
const selectedTaskIds = ref<string[]>([]);

const taskRowSelection = computed<TableRowSelection<LoopDataApi.ImportTask>>(
  () => ({
    selectedRowKeys: selectedTaskIds.value,
    onChange: (keys: (number | string)[]) => {
      selectedTaskIds.value = keys as string[];
    },
    getCheckboxProps: (record: LoopDataApi.ImportTask) => {
      const isActive =
        record.status === 'PENDING' || record.status === 'RUNNING';
      return {
        disabled: isActive,
      };
    },
  }),
);

/**
 * 任务轮询：统一走 usePolling（递归 setTimeout 防堆积、页面隐藏自动暂停、
 * 卸载自动清理）。按需启停——仅在存在活跃导入任务（PENDING/RUNNING）时轮询，
 * 无活跃任务即停止，避免空转请求。
 */
const { start: startTaskPolling, stop: stopTaskPolling } = usePolling(
  async () => {
    await loadTasks();
    // 本轮刷新后若无活跃任务，停止轮询
    if (!hasActiveTasks()) stopTaskPolling();
  },
  { interval: TASK_POLLING_INTERVAL },
);

/** 按任务活跃度同步轮询开关（任务列表加载/任务操作完成后调用） */
function syncTaskPolling() {
  if (hasActiveTasks()) {
    startTaskPolling();
  } else {
    stopTaskPolling();
  }
}

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
          strokeColor:
            record.status === 'FAILED'
              ? themeColors.value.DANGER
              : themeColors.value.INFO,
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
      // 无导入管理角色时不渲染操作按钮（后端端点同样 require_roles，避免 403）
      if (!canManageImports.value) {
        return h('span', { class: 'text-xs text-gray-400' }, '—');
      }
      const isActive =
        record.status === 'PENDING' || record.status === 'RUNNING';
      return h('div', { class: 'flex gap-1' }, [
        isActive
          ? h(
              Popconfirm,
              {
                title:
                  '取消后该导入任务将停止，已拉取的数据可能不完整。确定取消吗？',
                okText: '取消任务',
                okType: 'danger',
                cancelText: '保留',
                onConfirm: () => handleCancel(record.taskId),
              },
              {
                default: () =>
                  h(Button, { size: 'small', danger: true }, () => '取消'),
              },
            )
          : // P3-07：disabled 回算按钮增加 Tooltip 说明原因
            h(
              Tooltip,
              {
                title:
                  record.status === 'SUCCESS' ? '' : '仅导入成功的任务可回算',
              },
              {
                default: () =>
                  h(
                    Button,
                    {
                      size: 'small',
                      type: 'link',
                      disabled: record.status !== 'SUCCESS',
                      onClick: () => handleBackfill(record.taskId),
                    },
                    () => '回算',
                  ),
              },
            ),
        // 删除按钮：仅终态任务可删除（活跃任务需先取消）
        // P3-07：disabled 删除按钮增加 Tooltip 说明原因
        h(
          Tooltip,
          { title: isActive ? '执行中/待执行的任务不可删除，请先取消' : '' },
          {
            default: () =>
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
          },
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
  loopsError.value = null;
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
  } catch (error: any) {
    // 错误已由全局拦截器 toast 透传后端 message；此处仅记录用于内联错误占位
    loopsError.value = error?.message ?? '加载失败';
    loops.value = [];
    totalLoops.value = 0;
  } finally {
    loadingLoops.value = false;
  }
}

function handleLoopPageChange(pag: TablePaginationConfig) {
  loopPage.value = pag.current ?? 1;
  loopPageSize.value = pag.pageSize ?? loopPageSize.value;
  loadLoops();
}

/** 触发筛选时重置到第 1 页（筛选条件变更，全选筛选全集缓存同步失效） */
function handleLoopSearch() {
  loopPage.value = 1;
  filteredIdsCache.value = null;
  loadLoops();
}

async function loadTasks() {
  taskLoading.value = true;
  tasksError.value = null;
  try {
    const resp = await getImportTasksApi({
      page: taskPagination.value.current,
      pageSize: taskPagination.value.pageSize,
    });
    tasks.value = resp.items ?? [];
    taskPagination.value.total = resp.total ?? 0;
  } catch (error: any) {
    // 错误已由拦截器 toast；此处仅记录用于内联错误占位。
    // 注意保留旧 tasks 不清空：轮询任务依赖 hasActiveTasks() 判活，
    // 清空会导致瞬时网络抖动后轮询永久停止，无法自动恢复。
    tasksError.value = error?.message ?? '加载失败';
  } finally {
    taskLoading.value = false;
  }
}

function handleTaskPageChange(pag: TablePaginationConfig) {
  taskPagination.value.current = pag.current ?? 1;
  loadTasks();
}

// --- 数据完整性检查 ---

/** 检查完整性：loopIds 优先用已选回路，未选则传 undefined 查全部 READY */
async function handleCheckIntegrity() {
  if (!timeRange.value || timeRange.value.length !== 2) return;
  const [rangeStart, rangeEnd] = timeRange.value;
  if (!rangeStart || !rangeEnd) return;

  const loopIds =
    selectedLoopIds.value.length > 0 ? selectedLoopIds.value : undefined;

  integrityChecking.value = true;
  // 先打开 Drawer 显示 loading 占位
  integrityDrawerVisible.value = true;
  try {
    const result = await checkIntegrityApi({
      loopIds,
      tsStart: rangeStart.toISOString(),
      tsEnd: rangeEnd.toISOString(),
      expectedInterval: interval.value,
    });
    integrityResult.value = result;
  } catch {
    // 错误已由拦截器透传
    integrityDrawerVisible.value = false;
  } finally {
    integrityChecking.value = false;
  }
}

/** 基于完整性检查结果一键补齐（强制 skip 策略，AGENTS.md 红线）
 * skip 策略仅补缺口、不覆盖已有数据，属可逆轻操作：
 * 确认动作由 IntegrityReportDrawer 内 Popconfirm 承载，此处直接执行 */
async function handleBackfillFromIntegrity(
  loopIds: string[],
  tsStart: string,
  tsEnd: string,
) {
  importing.value = true;
  try {
    await startImportApi({
      loopIds,
      tsStart,
      tsEnd,
      interval: interval.value,
      conflictStrategy: 'skip',
      triggerBackfill: triggerBackfill.value,
    });
    message.success('补齐任务已启动');
    integrityDrawerVisible.value = false;
    await loadTasks();
    syncTaskPolling();
  } catch {
    // 错误已由拦截器透传
  } finally {
    importing.value = false;
  }
}

/** 开始导入按钮的 Popconfirm 文案（导入为可取消的异步任务，属可逆轻操作） */
const importConfirmTitle = computed(() => {
  const [rangeStart, rangeEnd] = timeRange.value ?? [];
  const rangeText =
    rangeStart && rangeEnd
      ? `，时间范围 ${dayjs(rangeStart).format('YYYY-MM-DD HH:mm')} ~ ${dayjs(rangeEnd).format('YYYY-MM-DD HH:mm')}`
      : '';
  return `将导入 ${selectedLoopIds.value.length} 个回路的历史数据${rangeText}，冲突策略：${conflictStrategy.value === 'overwrite' ? '覆盖（将覆盖本地已有数据，可从远端重新导入）' : '跳过'}。确认导入？`;
});

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
    syncTaskPolling();
  } catch {
    // Phase 10 UX 包：透传后端错误信息——全局拦截器已显示后端 message，
    // 这里不再覆盖通用文案，避免双重 toast
  } finally {
    importing.value = false;
  }
}

/** Phase 10 UX 包：取消活跃导入任务加二次确认（Popconfirm 内联确认）
 * 取消活跃任务可能导致已拉取部分数据被丢弃，需用户显式确认 */
async function handleCancel(taskId: string) {
  try {
    await cancelImportApi(taskId);
    message.success('已取消导入任务');
    await loadTasks();
    syncTaskPolling();
  } catch {
    // 错误已由拦截器透传
  }
}

async function handleBackfill(taskId: string) {
  try {
    const resp = await triggerBackfillApi(taskId);
    message.success(`KPI 回算已触发，共 ${resp.loopCount} 个回路`);
  } catch {
    // 错误已由拦截器透传
  }
}

/** 删除导入任务：简单确认（删除后不可恢复） */
function handleDelete(taskId: string) {
  Modal.confirm({
    title: '确定删除？',
    content: '删除后该导入任务记录将不可恢复',
    okType: 'danger',
    okText: '删除',
    cancelText: '取消',
    onOk: async () => {
      try {
        await deleteImportApi(taskId);
        message.success('已删除导入任务');
        await loadTasks();
        syncTaskPolling();
      } catch {
        // 错误已由拦截器透传
      }
    },
  });
}

/** 批量删除导入任务：简单确认 */
function handleBatchDelete() {
  if (selectedTaskIds.value.length === 0) {
    message.warning('请选择要删除的任务');
    return;
  }
  const count = selectedTaskIds.value.length;
  Modal.confirm({
    title: '确定删除？',
    content: `将删除选中的 ${count} 个导入任务，删除后不可恢复`,
    okType: 'danger',
    okText: '删除',
    cancelText: '取消',
    onOk: async () => {
      // 批量删除走并发控制（allSettled 语义：单项失败不中断其余项）
      const { fulfilled } = await runWithConcurrency(
        selectedTaskIds.value,
        (taskId) => deleteImportApi(taskId),
      );
      if (fulfilled > 0) {
        message.success(`已删除 ${fulfilled} 个导入任务`);
      }
      selectedTaskIds.value = [];
      await loadTasks();
      syncTaskPolling();
    },
  });
}

function hasActiveTasks() {
  return tasks.value.some(
    (t) => t.status === 'PENDING' || t.status === 'RUNNING',
  );
}

/** 工具栏刷新：重新加载回路列表与导入任务列表 */
async function handleRefresh() {
  taskLoading.value = true;
  try {
    await Promise.all([loadLoops(), loadTasks()]);
  } finally {
    taskLoading.value = false;
  }
  syncTaskPolling();
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '数据检查 帮助',
    content:
      '数据检查页：左侧选择回路（支持按装置/单元树筛选 + 关键字搜索 + 服务端分页；"全选"覆盖当前筛选条件下全部回路，含未显示分页），右侧选择时间范围/采样间隔/冲突策略后从远端 API 导入到本地 TDengine；导入完成后可选触发 KPI 回算。支持数据完整性检查（按小时分桶列级缺失统计）与一键补齐缺口（skip 策略）。任务列表展示进度/状态，活跃任务自动轮询。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: taskLoading.value },
  help: { onClick: handleHelp },
}));

// ===== A-07：表格密度三档（紧凑/标准/宽松，持久化）=====
const { tableSize, densityLabel, cycleDensity } = useTableDensity('loop-data');

// --- 生命周期 ---

onMounted(async () => {
  // 空态引导跳转（/loop/data?loopId=xxx）：预选该回路，便于直接发起导入
  const presetLoopId = route.query.loopId;
  if (typeof presetLoopId === 'string' && presetLoopId) {
    selectedLoopIds.value = [presetLoopId];
  }
  await Promise.all([loadPlantTree(), loadLoops(), loadTasks()]);
  // 按需启动轮询：仅在有活跃导入任务时开启（usePolling 组件卸载时自动清理）
  syncTaskPolling();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="数据检查"
      subtitle="从远端 API 导入历史数据到本地 TDengine，支持冲突处理与 KPI 回算"
      compact
      :loading="taskLoading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
        <!-- A-07：密度三档切换（紧凑/标准/宽松，点击循环） -->
        <ClpmToolbarButton
          icon="ant-design:column-height-outlined"
          :label="`密度：${densityLabel}`"
          :tooltip="`密度：${densityLabel}（点击切换）`"
          @click="cycleDensity"
        />
      </template>
    </ClpmPageToolbar>

    <div class="mt-4 flex gap-4" style="height: calc(100vh - 200px)">
      <!-- 左侧：回路选择 -->
      <div class="flex w-[30%] flex-col">
        <div
          class="flex flex-1 flex-col overflow-hidden rounded border border-gray-200 bg-white"
        >
          <!-- 面板头部 -->
          <div
            class="shrink-0 border-b px-3 py-2.5"
            style="background: hsl(var(--muted) / 42%)"
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
            style="background: hsl(var(--muted) / 42%)"
          >
            <Tooltip
              title="选中当前筛选条件下全部回路（含未显示分页）；取消则剔除全部"
            >
              <Checkbox
                :checked="allSelected"
                :indeterminate="indeterminate"
                :disabled="selectAllLoading"
                @change="handleSelectAll"
              >
                <span class="text-xs">{{
                  selectAllLoading ? '获取中...' : '全选'
                }}</span>
              </Checkbox>
            </Tooltip>
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

          <!-- 回路表格（错误态与空态分离：接口异常显示内联错误占位） -->
          <div class="min-h-0 flex-1 overflow-y-auto">
            <Alert
              v-if="loopsError"
              type="error"
              show-icon
              :message="`回路列表加载失败：${loopsError}`"
              description="请检查后端服务或稍后重试。"
              class="m-2"
            >
              <template #action>
                <Button size="small" type="link" @click="loadLoops">
                  重试
                </Button>
              </template>
            </Alert>
            <Table
              v-if="!loopsError"
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
                // 跨分页保留选中态：全选覆盖筛选全集后，切换分页不丢已选行
                preserveSelectedRowKeys: true,
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
        <div class="mb-4 shrink-0 rounded border p-3">
          <div class="flex items-center gap-3 flex-wrap">
            <Tooltip title="选择历史数据的时间范围">
              <RangePicker
                v-model:value="timeRange"
                show-time
                format="YYYY-MM-DD HH:mm"
                size="small"
                :placeholder="['开始时间', '结束时间']"
              />
            </Tooltip>
            <Tooltip title="数据采样间隔，支持1秒/5秒/10秒/1分钟">
              <Select
                v-model:value="interval"
                size="small"
                style="width: 100px"
              >
                <Select.Option :value="1">1s</Select.Option>
                <Select.Option :value="5">5s</Select.Option>
                <Select.Option :value="10">10s</Select.Option>
                <Select.Option :value="60">1m</Select.Option>
              </Select>
            </Tooltip>
            <Tooltip title="冲突策略：覆盖（手工优先）/ 跳过（保留已有）">
              <RadioGroup v-model:value="conflictStrategy">
                <Radio value="overwrite">覆盖</Radio>
                <Radio value="skip">跳过</Radio>
              </RadioGroup>
            </Tooltip>
            <Tooltip title="导入完成后自动触发KPI回算">
              <Checkbox v-model:checked="triggerBackfill"> 触发KPI </Checkbox>
            </Tooltip>
            <Button
              v-permission="IMPORT_ROLES"
              size="small"
              :loading="integrityChecking"
              :disabled="!timeRange || timeRange.length !== 2"
              @click="handleCheckIntegrity"
            >
              检查完整性
            </Button>
            <Popconfirm
              :title="importConfirmTitle"
              ok-text="开始导入"
              cancel-text="取消"
              @confirm="handleStartImport"
            >
              <Button
                v-permission="IMPORT_ROLES"
                type="primary"
                size="small"
                :loading="importing"
                :disabled="selectedLoopIds.length === 0"
              >
                开始导入
              </Button>
            </Popconfirm>
          </div>
        </div>

        <!-- 任务列表 -->
        <div class="flex min-h-0 flex-1 flex-col rounded border p-4">
          <div class="mb-3 flex shrink-0 items-center justify-between">
            <span class="font-medium">导入任务列表</span>
            <div class="flex gap-2">
              <Button
                v-permission="IMPORT_ROLES"
                size="small"
                danger
                :disabled="selectedTaskIds.length === 0"
                @click="handleBatchDelete"
              >
                批量删除 ({{ selectedTaskIds.length }})
              </Button>
            </div>
          </div>
          <div class="min-h-0 flex-1 overflow-y-auto">
            <Alert
              v-if="tasksError"
              type="error"
              show-icon
              :message="`导入任务列表加载失败：${tasksError}`"
              description="请检查后端服务或稍后重试。"
              class="mb-2"
            >
              <template #action>
                <Button size="small" type="link" @click="loadTasks">
                  重试
                </Button>
              </template>
            </Alert>
            <Table
              v-if="!tasksError"
              :columns="taskColumns"
              :data-source="tasks"
              :loading="taskLoading"
              :pagination="taskPagination"
              row-key="taskId"
              :size="tableSize"
              :scroll="{ x: 768 }"
              :row-selection="taskRowSelection"
              @change="handleTaskPageChange"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- 数据完整性检查报告抽屉 -->
    <IntegrityReportDrawer
      v-model:visible="integrityDrawerVisible"
      :result="integrityResult"
      :loading="integrityChecking"
      :ts-start="timeRange?.[0]?.toISOString() ?? ''"
      :ts-end="timeRange?.[1]?.toISOString() ?? ''"
      :expected-interval="interval"
      @backfill="handleBackfillFromIntegrity"
    />
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
