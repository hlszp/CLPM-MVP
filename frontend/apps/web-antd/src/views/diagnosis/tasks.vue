<script lang="ts" setup>
/**
 * 诊断任务页（P2-16-B2 Tab 化）
 *
 * - 3 Tab：进行中 / 已完成 / 已归档；Tab 标题 Badge 显示各类计数（/diagnosis/tasks/stats）
 * - 每 Tab 内独立的筛选/分页状态；进行中 Tab 自动轮询（5s）至全部终态
 * - 行级操作：诊断 / 取消 / 详情 / 归档 / 删除（状态机控制按钮可用性）
 * - 触发诊断 Modal：多选回路 + 时间范围
 * - RUNNING 状态行内显示进度条
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import {
  Badge,
  Button,
  DatePicker,
  Input,
  message,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  TabPane,
  Tabs,
  Tag,
  Tooltip,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  archiveDiagnosisTaskApi,
  cancelDiagnosisTaskApi,
  deleteDiagnosisTaskApi,
  getDiagnosisTasksApi,
  getDiagnosisTaskStatsApi,
  runDiagnosisTaskApi,
  triggerDiagnosisApi,
} from '#/api/diagnosis';
import { getLoopMonitorListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  ClpmDangerConfirmModal,
  ClpmDataCanvas,
  ClpmEmptyState,
  ClpmLoopLink,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useTableDensity } from '#/composables/use-table-density';
import { DIAGNOSIS_LABEL_OPTIONS } from '#/constants/diagnosis';
import {
  BADGE_REFRESH_INTERVAL,
  TASK_POLLING_INTERVAL,
} from '#/constants/polling';
import { runWithConcurrency } from '#/utils/concurrency';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'DiagnosisTasks' });

const { themeColors } = useClpmTheme();

// ===== A-07：表格密度三档（紧凑/标准/宽松，持久化）=====
const { tableSize, densityLabel, cycleDensity } =
  useTableDensity('diagnosis-tasks');

// ============ Tab 结构（P2-16-B2） ============
type TaskTabKey = 'active' | 'archived' | 'completed';

const activeTab = ref<TaskTabKey>('active');

/** Tab Badge 计数（/diagnosis/tasks/stats） */
const tabStats = reactive<DiagnosisApi.TaskStats>({
  active: 0,
  completed: 0,
  archived: 0,
});
const statsLoading = ref(false);

async function loadStats() {
  statsLoading.value = true;
  try {
    const data = await getDiagnosisTaskStatsApi();
    tabStats.active = data.active ?? 0;
    tabStats.completed = data.completed ?? 0;
    tabStats.archived = data.archived ?? 0;
  } catch {
    /* 拦截器已处理 */
  } finally {
    statsLoading.value = false;
  }
}

/** 每 Tab 独立状态（筛选/分页/数据/轮询定时器） */
interface TabState {
  loading: boolean;
  taskList: DiagnosisApi.TaskItem[];
  total: number;
  selectedRowKeys: string[];
  advancedFilterVisible: boolean;
  query: {
    page: number;
    pageSize: number;
    status: string | undefined;
    timeWindow: DiagnosisApi.TimeWindow | undefined;
    triggerType: string | undefined;
  };
  pollTimer: null | ReturnType<typeof setTimeout>;
  pollCount: number;
}

const MAX_POLL_COUNT = 120; // 最多轮询 120 次（10 分钟）

function createTabState(): TabState {
  return reactive<TabState>({
    loading: false,
    taskList: [],
    total: 0,
    selectedRowKeys: [],
    advancedFilterVisible: false,
    query: {
      status: undefined,
      triggerType: undefined,
      timeWindow: undefined,
      page: 1,
      pageSize: 20,
    },
    pollTimer: null,
    pollCount: 0,
  });
}

const tabStates: Record<TaskTabKey, TabState> = {
  active: createTabState(),
  completed: createTabState(),
  archived: createTabState(),
};

/** 当前 Tab 的视图状态 */
const currentState = computed(() => tabStates[activeTab.value]);

/** 轮询：仅 active Tab（进行中）启用 */
function stopAllPolling() {
  (Object.keys(tabStates) as TaskTabKey[]).forEach((k) => {
    if (tabStates[k].pollTimer) {
      clearTimeout(tabStates[k].pollTimer!);
      tabStates[k].pollTimer = null;
    }
  });
}

// ============ P2-12 徽章自动刷新：BADGE_REFRESH_INTERVAL 拉一次 stats，不打断用户操作 ============
let badgeRefreshTimer: null | ReturnType<typeof setInterval> = null;
function startBadgeRefresh() {
  stopBadgeRefresh();
  badgeRefreshTimer = setInterval(() => {
    // 仅当页面可见时刷新，避免后台消耗
    if (document.visibilityState === 'visible') {
      loadStats();
    }
  }, BADGE_REFRESH_INTERVAL);
}
function stopBadgeRefresh() {
  if (badgeRefreshTimer) {
    clearInterval(badgeRefreshTimer);
    badgeRefreshTimer = null;
  }
}

onBeforeUnmount(() => {
  stopAllPolling();
  stopBadgeRefresh();
});

/** 加载指定 Tab 的任务列表 */
async function loadTasks(key: TaskTabKey, silent = false) {
  const s = tabStates[key];
  if (!silent) s.loading = true;
  try {
    const params: DiagnosisApi.TaskListQueryParams = {
      status: s.query.status,
      triggerType: s.query.triggerType,
      timeWindow: s.query.timeWindow,
      page: s.query.page,
      pageSize: s.query.pageSize,
    };
    // active / completed Tab：仅未归档（后端默认 include_archived=false）
    // archived Tab：仅已归档
    if (key === 'archived') {
      params.archivedOnly = true;
    } else {
      params.includeArchived = false;
    }
    const data = await getDiagnosisTasksApi(params);
    s.taskList = data.items || [];
    s.total = data.total || 0;
  } catch {
    /* 拦截器已处理 */
  } finally {
    if (!silent) s.loading = false;
  }
}

/** 仅 active Tab 的递归 setTimeout 轮询 */
function startActivePolling() {
  const s = tabStates.active;
  if (s.pollTimer) {
    clearTimeout(s.pollTimer);
  }
  s.pollCount = 0;
  scheduleActivePoll();
}

function scheduleActivePoll() {
  const s = tabStates.active;
  s.pollTimer = setTimeout(async () => {
    s.pollCount++;
    if (s.pollCount > MAX_POLL_COUNT) {
      if (s.pollTimer) clearTimeout(s.pollTimer);
      s.pollTimer = null;
      return;
    }
    await loadTasks('active', true);
    const hasActive = s.taskList.some(
      (t) => t.status === 'PENDING' || t.status === 'RUNNING',
    );
    // 刷新 badge：有进行中任务时刷新计数（完成后 active→completed 迁移）
    if (!hasActive) {
      if (s.pollTimer) clearTimeout(s.pollTimer);
      s.pollTimer = null;
      loadStats();
      return;
    }
    scheduleActivePoll();
  }, TASK_POLLING_INTERVAL);
}

/** Tab 切换：加载对应 Tab 数据；active Tab 启动轮询 */
function handleTabChange(key: number | string) {
  const k = String(key) as TaskTabKey;
  activeTab.value = k;
  loadTasks(k);
  // 轮询启停
  stopAllPolling();
  if (k === 'active') {
    const hasPending = tabStates.active.taskList.some(
      (t) => t.status === 'PENDING' || t.status === 'RUNNING',
    );
    if (hasPending) startActivePolling();
  }
}

function handleSearchFor(key: TaskTabKey) {
  tabStates[key].query.page = 1;
  loadTasks(key);
  // 搜索后同步刷新徽章（可能有状态变更）
  loadStats();
}

function handleResetFor(key: TaskTabKey) {
  const s = tabStates[key];
  s.query.status = undefined;
  s.query.triggerType = undefined;
  s.query.timeWindow = undefined;
  s.advancedFilterVisible = false;
  handleSearchFor(key);
}

function handleTableChangeFor(
  key: TaskTabKey,
  pagination: TablePaginationConfig,
) {
  const s = tabStates[key];
  s.query.page = pagination.current || 1;
  s.query.pageSize = pagination.pageSize || 20;
  loadTasks(key);
}

// ============ 任务触发：成功后 → active Tab 数据 + badge 刷新 ============
function refreshAfterMutation() {
  loadStats();
  loadTasks(activeTab.value);
  // 如果当前切到 active，尝试启动轮询（可能新增了 PENDING/RUNNING 任务）
  if (activeTab.value === 'active') {
    const hasPending = tabStates.active.taskList.some(
      (t) => t.status === 'PENDING' || t.status === 'RUNNING',
    );
    if (hasPending) startActivePolling();
  }
}

/** 任务状态选项（按 Tab 维度过滤可选值） */
const statusOptionsAll: { label: string; value: DiagnosisApi.TaskStatus }[] = [
  { label: '待执行', value: 'PENDING' },
  { label: '执行中', value: 'RUNNING' },
  { label: '成功', value: 'SUCCESS' },
  { label: '失败', value: 'FAILED' },
  { label: '已取消', value: 'CANCELLED' },
];
const statusOptionsByTab: Record<
  TaskTabKey,
  { label: string; value: DiagnosisApi.TaskStatus }[]
> = {
  active: statusOptionsAll.filter((o) =>
    ['PENDING', 'RUNNING'].includes(o.value),
  ),
  completed: statusOptionsAll.filter((o) =>
    ['CANCELLED', 'FAILED', 'SUCCESS'].includes(o.value),
  ),
  archived: statusOptionsAll,
};
/** 触发方式选项 */
const triggerTypeOptions: { label: string; value: string }[] = [
  { label: '手动', value: 'manual' },
  { label: '自动', value: 'auto' },
];

/** 时间窗选项 */
const timeWindowOptions: {
  label: string;
  value: DiagnosisApi.TimeWindow;
}[] = [
  { label: '近 24 小时', value: 'last_24_hours' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

/** 任务状态 → Tag 颜色与中文文案（放宽 key 为 string 以兼容模板 bodyCell slot 的隐式 any） */
const statusConfig: Record<string, { color: string; text: string }> = {
  PENDING: { color: 'default', text: '待执行' },
  RUNNING: { color: 'processing', text: '执行中' },
  SUCCESS: { color: 'success', text: '成功' },
  FAILED: { color: 'error', text: '失败' },
  CANCELLED: { color: 'warning', text: '已取消' },
};

// ============ 路由 ============
const router = useRouter();

// ============ 按钮状态机 ============
/** 诊断：仅待执行状态可触发 */
function canDiagnose(status: DiagnosisApi.TaskStatus): boolean {
  return status === 'PENDING';
}
/** 结果：SUCCESS 可查看结果；PENDING/RUNNING 可跳详情看历史诊断 */
function canViewResult(status: DiagnosisApi.TaskStatus): boolean {
  return status === 'SUCCESS' || status === 'PENDING' || status === 'RUNNING';
}
/** 归档：仅诊断完成（SUCCESS）可归档，且非归档 Tab */
function canArchive(
  status: DiagnosisApi.TaskStatus,
  tabKey: TaskTabKey,
): boolean {
  return tabKey !== 'archived' && status === 'SUCCESS';
}
/** 取消：仅待执行/执行中可取消，且非归档 Tab */
function canCancel(
  status: DiagnosisApi.TaskStatus,
  tabKey: TaskTabKey,
): boolean {
  return (
    tabKey !== 'archived' && (status === 'PENDING' || status === 'RUNNING')
  );
}
/** 删除：执行中（RUNNING）不可删除，须先取消 */
function canDelete(status: DiagnosisApi.TaskStatus): boolean {
  return status !== 'RUNNING';
}

/** 当前 Tab 是否允许"新增诊断"（归档 Tab 不允许新增） */
const canTriggerNew = computed(() => activeTab.value !== 'archived');

/** 当前 Tab 选中数（用于批量操作按钮禁用态） */
const selectedCount = computed(() => currentState.value.selectedRowKeys.length);

// ============ 表格列 ============
const columns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 180 },
  {
    title: '装置/单元',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 120,
    ellipsis: true,
  },
  {
    title: '当前评分',
    dataIndex: 'compositeScore',
    key: 'compositeScore',
    width: 90,
    align: 'center',
  },
  {
    title: '任务状态',
    dataIndex: 'status',
    key: 'status',
    width: 180,
    align: 'center',
  },
  {
    title: '触发方式',
    dataIndex: 'triggerType',
    key: 'triggerType',
    width: 100,
    align: 'center',
  },
  {
    title: '触发时间',
    dataIndex: 'triggeredAt',
    key: 'triggeredAt',
    width: 160,
  },
  { title: '操作', key: 'action', width: 300, fixed: 'right' },
];

// ============ 触发诊断 Modal ============
const triggerModalVisible = ref(false);
const triggerLoading = ref(false);
const loopList = ref<LoopApi.MonitorListItem[]>([]);
const loopKeyword = ref('');
const loopAreaFilter = ref<string | undefined>(undefined);
const loopUnitFilter = ref<string | undefined>(undefined);
const selectedLoopIds = ref<string[]>([]);
const loopLoading = ref(false);
/** 装置（AREA）列表 */
const areaNodes = ref<PlantNodeApi.PlantNode[]>([]);
/** 装置 → 单元名称列表 映射 */
const areaToUnitNames = ref<Map<string, string[]>>(new Map());
/** 触发诊断时间范围（默认最近 24 小时） */
const triggerTimeRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>([
  dayjs().subtract(24, 'hour'),
  dayjs(),
]);

/** 按需诊断标签选项（B6：剔除兜底标签 MANUAL_REVIEW） */
const triggerLabelOptions = DIAGNOSIS_LABEL_OPTIONS.filter(
  (o) => o.value !== 'MANUAL_REVIEW',
);
/** 已选诊断标签（默认全选；全选时提交 labels=undefined 即全量） */
const selectedLabels = ref<string[]>(triggerLabelOptions.map((o) => o.value));

/** RangePicker 预设快捷选项 */
const rangePresets = computed(() => [
  {
    label: '最近1小时',
    value: [dayjs().subtract(1, 'hour'), dayjs()] as [dayjs.Dayjs, dayjs.Dayjs],
  },
  {
    label: '最近24小时',
    value: [dayjs().subtract(24, 'hour'), dayjs()] as [
      dayjs.Dayjs,
      dayjs.Dayjs,
    ],
  },
  {
    label: '最近7天',
    value: [dayjs().subtract(7, 'day'), dayjs()] as [dayjs.Dayjs, dayjs.Dayjs],
  },
]);

/** 装置选项（从 plant node 树提取 AREA 节点） */
const areaOptions = computed(() =>
  areaNodes.value.map((n: PlantNodeApi.PlantNode) => ({
    value: n.id,
    label: n.name,
  })),
);

/** 单元选项（根据选中装置过滤） */
const unitOptions = computed(() => {
  if (loopAreaFilter.value) {
    const names = areaToUnitNames.value.get(loopAreaFilter.value) || [];
    return names.map((n) => ({ value: n, label: n }));
  }
  const units = new Map<string, string>();
  loopList.value.forEach((l) => {
    if (l.unitName) units.set(l.unitName, l.unitName);
  });
  return [...units.entries()].map(([value, label]) => ({ value, label }));
});

/** 经过筛选的回路列表 */
const filteredLoops = computed(() => {
  let result = loopList.value;
  if (loopAreaFilter.value) {
    const unitNames = areaToUnitNames.value.get(loopAreaFilter.value) || [];
    const unitNameSet = new Set(unitNames);
    result = result.filter((l) => l.unitName && unitNameSet.has(l.unitName));
  }
  if (loopUnitFilter.value) {
    result = result.filter((l) => l.unitName === loopUnitFilter.value);
  }
  if (loopKeyword.value) {
    const kw = loopKeyword.value.toLowerCase();
    result = result.filter(
      (l) =>
        l.tagName.toLowerCase().includes(kw) ||
        l.unitName?.toLowerCase().includes(kw) ||
        l.description?.toLowerCase().includes(kw),
    );
  }
  return result;
});

/** 回路选择表格列定义 */
const loopColumns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 120 },
  {
    title: '名称',
    dataIndex: 'description',
    key: 'description',
    width: 160,
    ellipsis: true,
  },
  {
    title: '装置/单元',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 120,
    ellipsis: true,
  },
  {
    title: '综合评分',
    dataIndex: 'score',
    key: 'score',
    width: 90,
    align: 'center',
  },
  {
    title: '自控率',
    dataIndex: 'effectiveAutoRate',
    key: 'effectiveAutoRate',
    width: 80,
    align: 'center',
  },
];

/** 回路选择表格行选择配置 */
const loopRowSelection = computed(() => ({
  selectedRowKeys: selectedLoopIds.value,
  onChange: (keys: (number | string)[]) => {
    selectedLoopIds.value = keys.map(String);
  },
  getCheckboxProps: (record: LoopApi.MonitorListItem) => ({
    disabled: !record.isActive,
  }),
}));

/** 全选当前筛选后的所有回路 */
function handleSelectAllLoops() {
  selectedLoopIds.value = filteredLoops.value
    .filter((l) => l.isActive)
    .map((l) => l.loopId);
}

/** 加载回路监控列表（含最新评分） */
async function loadLoopList() {
  loopLoading.value = true;
  try {
    const data = await getLoopMonitorListApi({
      page: 1,
      pageSize: 100,
    });
    loopList.value = data.items || [];
  } catch {
    // 错误已由拦截器处理
  } finally {
    loopLoading.value = false;
  }
}

/** 加载工厂节点树 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    const areas: PlantNodeApi.PlantNode[] = [];
    const mapping = new Map<string, string[]>();
    for (const factory of tree) {
      if (factory.type === 'AREA') {
        areas.push(factory);
        const unitNames = (factory.children || [])
          .filter((c) => c.type === 'UNIT')
          .map((c) => c.name);
        mapping.set(factory.id, unitNames);
      }
      for (const area of factory.children || []) {
        if (area.type === 'AREA' && !areas.some((a) => a.id === area.id)) {
          areas.push(area);
          const unitNames = (area.children || [])
            .filter((c) => c.type === 'UNIT')
            .map((c) => c.name);
          mapping.set(area.id, unitNames);
        }
      }
    }
    areaNodes.value = areas;
    areaToUnitNames.value = mapping;
  } catch {
    // 错误已由拦截器处理
  }
}

/** 装置变更时重置单元筛选 */
function handleAreaChange() {
  loopUnitFilter.value = undefined;
}

/** 打开新增任务 Modal */
function openTriggerModal() {
  selectedLoopIds.value = [];
  loopKeyword.value = '';
  loopAreaFilter.value = undefined;
  loopUnitFilter.value = undefined;
  triggerTimeRange.value = [dayjs().subtract(24, 'hour'), dayjs()];
  selectedLabels.value = triggerLabelOptions.map((o) => o.value);
  triggerModalVisible.value = true;
  if (loopList.value.length === 0) {
    loadLoopList();
  }
  if (areaNodes.value.length === 0) {
    loadPlantNodes();
  }
}

/** 确认触发诊断 */
async function handleTriggerConfirm() {
  if (selectedLoopIds.value.length === 0) {
    message.warning('请至少选择一个回路');
    return;
  }
  triggerLoading.value = true;
  try {
    const [start, end] = triggerTimeRange.value;
    // 全选时提交 labels=undefined（后端按全量执行），否则提交标签子集
    const labels =
      selectedLabels.value.length === triggerLabelOptions.length
        ? undefined
        : selectedLabels.value;
    await triggerDiagnosisApi({
      loopIds: selectedLoopIds.value,
      startTime: start.toISOString(),
      endTime: end.toISOString(),
      labels,
    });
    message.success(`已触发 ${selectedLoopIds.value.length} 个回路的诊断任务`);
    handleTriggerModalSuccess();
  } catch {
    // 错误已由拦截器处理
  } finally {
    triggerLoading.value = false;
  }
}

/** 诊断标签中文映射 */
const DIAG_LABEL_MAP: Record<string, { color: string; text: string }> = {
  OSCILLATION: { text: '振荡', color: 'red' },
  VALVE_STICTION: { text: '阀门粘滞', color: 'volcano' },
  OVERAGGRESSIVE: { text: '参数过激', color: 'orange' },
  OVERCONSERVATIVE: { text: '参数过保守', color: 'gold' },
  EXTERNAL_DISTURBANCE: { text: '外扰频繁', color: 'lime' },
  QUALITY_ABNORMAL: { text: 'PV质量异常', color: 'cyan' },
  OUTPUT_SATURATION: { text: '输出饱和', color: 'blue' },
  MANUAL_REVIEW: { text: '人工复核', color: 'purple' },
  NORMAL: { text: '正常', color: 'green' },
};

function diagLabelText(label: string): string {
  return DIAG_LABEL_MAP[label]?.text ?? label;
}

// ============ 行级操作 ============
const rowDiagnoseLoading = ref<string>('');
/** 行级诊断（可逆轻操作走 Popconfirm 确认） */
async function handleRowDiagnose(record: Record<string, any>) {
  const r = record as DiagnosisApi.TaskItem;
  rowDiagnoseLoading.value = r.taskId;
  try {
    await runDiagnosisTaskApi(r.taskId);
    message.success(`已执行回路 ${r.tagName} 的诊断`);
    refreshAfterMutation();
  } catch {
    // 错误已由拦截器处理
  } finally {
    rowDiagnoseLoading.value = '';
  }
}

/** 取消：仅 PENDING/RUNNING 可取消 */
async function handleCancel(record: Record<string, any>) {
  const r = record as DiagnosisApi.TaskItem;
  await cancelDiagnosisTaskApi(r.taskId);
  message.success('任务已取消');
  refreshAfterMutation();
}

/** 详情：跳转到诊断详情页 */
function handleViewDetail(record: Record<string, any>) {
  const r = record as DiagnosisApi.TaskItem;
  router.push({
    path: `/diagnosis/detail/${r.loopId}`,
    query: { taskId: r.taskId },
  });
}

/** 归档：软操作可恢复（可在诊断记录中查看），危险确认弹窗免确认码 */
const archiveOpen = ref(false);
const archiveTarget = ref<DiagnosisApi.TaskItem | null>(null);
const archiveLoading = ref(false);

function handleArchive(record: Record<string, any>) {
  archiveTarget.value = record as DiagnosisApi.TaskItem;
  archiveOpen.value = true;
}

async function handleArchiveConfirm() {
  if (!archiveTarget.value) return;
  archiveLoading.value = true;
  try {
    await archiveDiagnosisTaskApi(archiveTarget.value.taskId);
    message.success('任务已归档');
    archiveOpen.value = false;
    refreshAfterMutation();
  } finally {
    archiveLoading.value = false;
  }
}

/** 行级删除：危险确认弹窗 */
const deleteOpen = ref(false);
const deleteTarget = ref<DiagnosisApi.TaskItem | null>(null);
const deleteLoading = ref(false);

function handleDelete(record: Record<string, any>) {
  deleteTarget.value = record as DiagnosisApi.TaskItem;
  deleteOpen.value = true;
}

async function handleDeleteConfirm() {
  if (!deleteTarget.value) return;
  const record = deleteTarget.value;
  deleteLoading.value = true;
  try {
    await deleteDiagnosisTaskApi(record.taskId);
    message.success('任务已删除');
    currentState.value.selectedRowKeys =
      currentState.value.selectedRowKeys.filter((k) => k !== record.taskId);
    deleteOpen.value = false;
    refreshAfterMutation();
  } finally {
    deleteLoading.value = false;
  }
}

/** 批量删除：跳过执行中（RUNNING）任务，须先取消；危险确认弹窗免确认码 */
const batchDeleteLoading = ref(false);
const batchDeleteOpen = ref(false);
const batchDeleteTargets = ref<DiagnosisApi.TaskItem[]>([]);
const batchDeleteSkipped = ref(0);

function handleBatchDelete() {
  const selectedAll = currentState.value.taskList.filter((t) =>
    currentState.value.selectedRowKeys.includes(t.taskId),
  );
  const selected = selectedAll.filter((t) =>
    canDelete(t.status as DiagnosisApi.TaskStatus),
  );
  const skipped = selectedAll.length - selected.length;
  if (selected.length === 0) {
    message.warning(
      skipped > 0
        ? '所选任务均在执行中，请先取消后再删除'
        : '请先选择要删除的任务',
    );
    return;
  }
  batchDeleteTargets.value = selected;
  batchDeleteSkipped.value = skipped;
  batchDeleteOpen.value = true;
}

async function handleBatchDeleteConfirm() {
  batchDeleteLoading.value = true;
  try {
    const { fulfilled, rejected } = await runWithConcurrency(
      batchDeleteTargets.value,
      (t) => deleteDiagnosisTaskApi(t.taskId),
    );
    if (rejected === 0) {
      message.success(`已删除 ${fulfilled} 个任务`);
    } else {
      message.warning(
        `已删除 ${fulfilled} 个任务，${rejected} 个失败（错误已记录）`,
      );
    }
    currentState.value.selectedRowKeys = [];
    batchDeleteOpen.value = false;
    refreshAfterMutation();
  } catch {
    // 错误已由拦截器处理
  } finally {
    batchDeleteLoading.value = false;
  }
}

/** 批量诊断：对选中的任务行执行诊断（不创建新任务） */
const batchDiagnoseLoading = ref(false);
async function handleBatchTrigger() {
  const selected = currentState.value.taskList.filter((t) =>
    currentState.value.selectedRowKeys.includes(t.taskId),
  );
  if (selected.length === 0) {
    message.warning('请先选中需要诊断的任务');
    return;
  }
  batchDiagnoseLoading.value = true;
  try {
    const { fulfilled, rejected } = await runWithConcurrency(selected, (t) =>
      runDiagnosisTaskApi(t.taskId),
    );
    if (fulfilled > 0) {
      message.success(
        `已执行 ${fulfilled} 个任务的诊断${rejected > 0 ? `，${rejected} 个失败` : ''}`,
      );
    } else {
      message.error('全部诊断任务执行失败');
    }
    currentState.value.selectedRowKeys = [];
    refreshAfterMutation();
  } catch {
    // 错误已由拦截器处理
  } finally {
    batchDiagnoseLoading.value = false;
  }
}

// ============ 格式化工具 ============
function formatScore(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return val.toFixed(1);
}

function formatRate(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return `${(val * 100).toFixed(1)}%`;
}

function scoreColor(val: null | number | undefined): string {
  if (val === null || val === undefined) return themeColors.value.NEUTRAL;
  if (val < 60) return themeColors.value.DANGER;
  if (val < 80) return themeColors.value.WARNING;
  return themeColors.value.SUCCESS;
}

function triggerTypeName(t: string): string {
  return t === 'auto' ? '自动' : '手动';
}

/** 行选择：绑定当前 Tab */
const rowSelection = computed(() => ({
  selectedRowKeys: currentState.value.selectedRowKeys,
  onChange: (keys: (number | string)[]) => {
    currentState.value.selectedRowKeys = keys.map(String);
  },
}));

// ============ trigger modal close 刷新当前 Tab + badge ============
function handleTriggerModalSuccess() {
  triggerModalVisible.value = false;
  // 新增任务后默认跳到 active Tab 查看
  activeTab.value = 'active';
  refreshAfterMutation();
}

onMounted(async () => {
  // 先加载 badge 计数，再加载 active Tab
  await loadStats();
  await loadTasks('active');
  // active Tab 有未终态任务即启动轮询
  const hasPending = tabStates.active.taskList.some(
    (t) => t.status === 'PENDING' || t.status === 'RUNNING',
  );
  if (hasPending) startActivePolling();
  // P2-12 启动徽章 30s 自动刷新
  startBadgeRefresh();
});

/** P3-01：暴露 refresh() 给 task-center.vue 调用，替代 tabKey 强制重建 */
async function refresh() {
  await loadStats();
  await loadTasks(activeTab.value);
}

defineExpose({ refresh });
</script>

<template>
  <Page>
    <!-- IA 整改 P2-16-B2：诊断任务 Tab 化。3 Tab：进行中（active）/已完成（completed）/已归档（archived） -->
    <Tabs v-model:active-key="activeTab" type="card" @change="handleTabChange">
      <!-- ================== Tab 1：进行中 ================== -->
      <TabPane key="active">
        <template #tab>
          <Badge :count="tabStats.active" :offset="[6, 0]" size="small">
            <span>进行中</span>
          </Badge>
        </template>
        <ClpmDataCanvas
          title="进行中任务（未归档且 PENDING/RUNNING）"
          :loading="tabStates.active.loading"
        >
          <template #toolbar>
            <ClpmToolbarButton
              type="primary"
              :disabled="!canTriggerNew"
              @click="openTriggerModal"
            >
              <IconifyIcon icon="lucide:play-circle" class="mr-1" />
              新增诊断
            </ClpmToolbarButton>
            <ClpmToolbarButton
              :disabled="selectedCount === 0 || activeTab === 'archived'"
              :loading="batchDiagnoseLoading"
              @click="handleBatchTrigger"
            >
              <IconifyIcon icon="lucide:zap" class="mr-1" />
              批量诊断
            </ClpmToolbarButton>
            <ClpmToolbarButton
              :disabled="selectedCount === 0"
              :loading="batchDeleteLoading"
              @click="handleBatchDelete"
            >
              <IconifyIcon icon="lucide:trash-2" class="mr-1" />
              批量删除
            </ClpmToolbarButton>
            <ClpmToolbarButton @click="cycleDensity">
              <IconifyIcon icon="lucide:table-2" class="mr-1" />
              {{ densityLabel }}
            </ClpmToolbarButton>
          </template>

          <!-- 筛选栏 -->
          <div class="mb-3 flex flex-wrap items-center gap-2">
            <Select
              v-model:value="tabStates.active.query.status"
              placeholder="任务状态"
              allow-clear
              style="width: 140px"
              :options="statusOptionsByTab.active"
            />
            <Select
              v-model:value="tabStates.active.query.triggerType"
              placeholder="触发方式"
              allow-clear
              style="width: 120px"
              :options="triggerTypeOptions"
            />
            <a
              v-if="!tabStates.active.advancedFilterVisible"
              class="text-xs cursor-pointer"
              :style="{ color: themeColors.INFO }"
              @click="tabStates.active.advancedFilterVisible = true"
            >
              展开高级筛选 ▾
            </a>
            <template v-if="tabStates.active.advancedFilterVisible">
              <Select
                v-model:value="tabStates.active.query.timeWindow"
                placeholder="时间窗口"
                allow-clear
                style="width: 140px"
                :options="timeWindowOptions"
              />
              <a
                class="text-xs cursor-pointer"
                :style="{ color: themeColors.INFO }"
                @click="tabStates.active.advancedFilterVisible = false"
              >
                收起 ▴
              </a>
            </template>
            <div class="ml-auto flex items-center gap-2">
              <Button size="small" @click="handleResetFor('active')">
                重置
              </Button>
              <Button
                size="small"
                type="primary"
                @click="handleSearchFor('active')"
              >
                查询
              </Button>
            </div>
          </div>

          <!-- 表格 -->
          <Table
            :columns="columns"
            :data-source="tabStates.active.taskList"
            :loading="tabStates.active.loading"
            :pagination="{
              current: tabStates.active.query.page,
              pageSize: tabStates.active.query.pageSize,
              total: tabStates.active.total,
              showSizeChanger: true,
              showQuickJumper: true,
              showTotal: (total) => `共 ${total} 条`,
            }"
            :row-selection="rowSelection"
            :row-key="(record: DiagnosisApi.TaskItem) => record.taskId"
            :size="tableSize"
            :scroll="{ x: 1100 }"
            @change="(p) => handleTableChangeFor('active', p)"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'tagName'">
                <ClpmLoopLink
                  :loop-id="record.loopId"
                  :tag-name="record.tagName"
                />
              </template>
              <template v-else-if="column.key === 'compositeScore'">
                <span
                  class="clpm-num font-medium"
                  :style="{ color: scoreColor(record.compositeScore) }"
                >
                  {{ formatScore(record.compositeScore) }}
                </span>
              </template>
              <template v-else-if="column.key === 'status'">
                <div class="flex flex-col items-center gap-1">
                  <Tag :color="statusConfig[record.status]?.color ?? 'default'">
                    {{ statusConfig[record.status]?.text ?? '未知' }}
                  </Tag>
                  <!-- RUNNING 行内进度条 -->
                  <Progress
                    v-if="record.status === 'RUNNING'"
                    :percent="0"
                    :show-info="false"
                    size="small"
                    status="active"
                    style="width: 100px"
                  />
                  <!-- 诊断标签（任务完成后显示） -->
                  <div
                    v-if="record.labels?.length"
                    class="flex flex-wrap gap-1 justify-center max-w-[180px]"
                  >
                    <Tag
                      v-for="(lb, idx) in record.labels.slice(0, 3)"
                      :key="idx"
                      :color="DIAG_LABEL_MAP[lb.label]?.color ?? 'default'"
                      style="padding: 0 4px; margin: 0; font-size: 11px"
                    >
                      {{ diagLabelText(lb.label) }}
                      {{ (lb.confidence * 100).toFixed(0) }}%
                    </Tag>
                    <Tag
                      v-if="record.labels.length > 3"
                      style="padding: 0 4px; margin: 0; font-size: 11px"
                    >
                      +{{ record.labels.length - 3 }}
                    </Tag>
                  </div>
                </div>
              </template>
              <template v-else-if="column.key === 'triggerType'">
                {{ triggerTypeName(record.triggerType) }}
              </template>
              <template v-else-if="column.key === 'triggeredAt'">
                {{ formatTime(record.triggeredAt) }}
              </template>
              <template v-else-if="column.key === 'action'">
                <Space size="small" wrap>
                  <Popconfirm
                    v-if="canDiagnose(record.status)"
                    title="确认执行诊断？"
                    @confirm="handleRowDiagnose(record)"
                  >
                    <Button
                      size="small"
                      type="link"
                      :loading="rowDiagnoseLoading === record.taskId"
                    >
                      诊断
                    </Button>
                  </Popconfirm>
                  <Button
                    v-if="canViewResult(record.status)"
                    size="small"
                    type="link"
                    @click="handleViewDetail(record)"
                  >
                    详情
                  </Button>
                  <Popconfirm
                    v-if="canCancel(record.status, 'active')"
                    title="确认取消该任务？"
                    @confirm="handleCancel(record)"
                  >
                    <Button size="small" type="link" danger> 取消 </Button>
                  </Popconfirm>
                  <Tooltip
                    v-if="canArchive(record.status, 'active')"
                    title="归档：将已完成任务移至「已归档」Tab，不删除数据，可随时查看"
                  >
                    <Button
                      size="small"
                      type="link"
                      @click="handleArchive(record)"
                    >
                      归档
                    </Button>
                  </Tooltip>
                  <Popconfirm
                    v-if="canDelete(record.status)"
                    title="确认删除该任务？"
                    ok-text="删除"
                    cancel-text="取消"
                    :ok-button-props="{ danger: true }"
                    @confirm="handleDelete(record)"
                  >
                    <Button size="small" type="link" danger> 删除 </Button>
                  </Popconfirm>
                </Space>
              </template>
            </template>
            <template #emptyText>
              <ClpmEmptyState
                title="暂无进行中任务"
                description="点击右上角「新增诊断」创建新的诊断任务"
              />
            </template>
          </Table>
        </ClpmDataCanvas>
      </TabPane>

      <!-- ================== Tab 2：已完成 ================== -->
      <TabPane key="completed">
        <template #tab>
          <Badge :count="tabStats.completed" :offset="[6, 0]" size="small">
            <span>已完成</span>
          </Badge>
        </template>
        <ClpmDataCanvas
          title="已完成任务（未归档且终态 SUCCESS/FAILED/CANCELLED）"
          :loading="tabStates.completed.loading"
        >
          <template #toolbar>
            <ClpmToolbarButton
              type="primary"
              :disabled="!canTriggerNew"
              @click="openTriggerModal"
            >
              <IconifyIcon icon="lucide:play-circle" class="mr-1" />
              新增诊断
            </ClpmToolbarButton>
            <ClpmToolbarButton
              :disabled="selectedCount === 0"
              :loading="batchDeleteLoading"
              @click="handleBatchDelete"
            >
              <IconifyIcon icon="lucide:trash-2" class="mr-1" />
              批量删除
            </ClpmToolbarButton>
            <ClpmToolbarButton @click="cycleDensity">
              <IconifyIcon icon="lucide:table-2" class="mr-1" />
              {{ densityLabel }}
            </ClpmToolbarButton>
          </template>

          <div class="mb-3 flex flex-wrap items-center gap-2">
            <Select
              v-model:value="tabStates.completed.query.status"
              placeholder="任务状态"
              allow-clear
              style="width: 140px"
              :options="statusOptionsByTab.completed"
            />
            <Select
              v-model:value="tabStates.completed.query.triggerType"
              placeholder="触发方式"
              allow-clear
              style="width: 120px"
              :options="triggerTypeOptions"
            />
            <a
              v-if="!tabStates.completed.advancedFilterVisible"
              class="text-xs cursor-pointer"
              :style="{ color: themeColors.INFO }"
              @click="tabStates.completed.advancedFilterVisible = true"
            >
              展开高级筛选 ▾
            </a>
            <template v-if="tabStates.completed.advancedFilterVisible">
              <Select
                v-model:value="tabStates.completed.query.timeWindow"
                placeholder="时间窗口"
                allow-clear
                style="width: 140px"
                :options="timeWindowOptions"
              />
              <a
                class="text-xs cursor-pointer"
                :style="{ color: themeColors.INFO }"
                @click="tabStates.completed.advancedFilterVisible = false"
              >
                收起 ▴
              </a>
            </template>
            <div class="ml-auto flex items-center gap-2">
              <Button size="small" @click="handleResetFor('completed')">
                重置
              </Button>
              <Button
                size="small"
                type="primary"
                @click="handleSearchFor('completed')"
              >
                查询
              </Button>
            </div>
          </div>

          <Table
            :columns="columns"
            :data-source="tabStates.completed.taskList"
            :loading="tabStates.completed.loading"
            :pagination="{
              current: tabStates.completed.query.page,
              pageSize: tabStates.completed.query.pageSize,
              total: tabStates.completed.total,
              showSizeChanger: true,
              showQuickJumper: true,
              showTotal: (total) => `共 ${total} 条`,
            }"
            :row-selection="rowSelection"
            :row-key="(record: DiagnosisApi.TaskItem) => record.taskId"
            :size="tableSize"
            :scroll="{ x: 1100 }"
            @change="(p) => handleTableChangeFor('completed', p)"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'tagName'">
                <ClpmLoopLink
                  :loop-id="record.loopId"
                  :tag-name="record.tagName"
                />
              </template>
              <template v-else-if="column.key === 'compositeScore'">
                <span
                  class="clpm-num font-medium"
                  :style="{ color: scoreColor(record.compositeScore) }"
                >
                  {{ formatScore(record.compositeScore) }}
                </span>
              </template>
              <template v-else-if="column.key === 'status'">
                <div class="flex flex-col items-center gap-1">
                  <Tag :color="statusConfig[record.status]?.color ?? 'default'">
                    {{ statusConfig[record.status]?.text ?? '未知' }}
                  </Tag>
                  <div
                    v-if="record.labels?.length"
                    class="flex flex-wrap gap-1 justify-center max-w-[180px]"
                  >
                    <Tag
                      v-for="(lb, idx) in record.labels.slice(0, 3)"
                      :key="idx"
                      :color="DIAG_LABEL_MAP[lb.label]?.color ?? 'default'"
                      style="padding: 0 4px; margin: 0; font-size: 11px"
                    >
                      {{ diagLabelText(lb.label) }}
                      {{ (lb.confidence * 100).toFixed(0) }}%
                    </Tag>
                    <Tag
                      v-if="record.labels.length > 3"
                      style="padding: 0 4px; margin: 0; font-size: 11px"
                    >
                      +{{ record.labels.length - 3 }}
                    </Tag>
                  </div>
                </div>
              </template>
              <template v-else-if="column.key === 'triggerType'">
                {{ triggerTypeName(record.triggerType) }}
              </template>
              <template v-else-if="column.key === 'triggeredAt'">
                {{ formatTime(record.triggeredAt) }}
              </template>
              <template v-else-if="column.key === 'action'">
                <Space size="small" wrap>
                  <Popconfirm
                    v-if="canDiagnose(record.status)"
                    title="确认重新执行诊断？"
                    @confirm="handleRowDiagnose(record)"
                  >
                    <Button
                      size="small"
                      type="link"
                      :loading="rowDiagnoseLoading === record.taskId"
                    >
                      重跑
                    </Button>
                  </Popconfirm>
                  <Button
                    v-if="canViewResult(record.status)"
                    size="small"
                    type="link"
                    @click="handleViewDetail(record)"
                  >
                    详情
                  </Button>
                  <Tooltip
                    v-if="canArchive(record.status, 'completed')"
                    title="归档：将已完成任务移至「已归档」Tab，不删除数据，可随时查看"
                  >
                    <Button
                      size="small"
                      type="link"
                      @click="handleArchive(record)"
                    >
                      归档
                    </Button>
                  </Tooltip>
                  <Popconfirm
                    v-if="canDelete(record.status)"
                    title="确认删除该任务？"
                    ok-text="删除"
                    cancel-text="取消"
                    :ok-button-props="{ danger: true }"
                    @confirm="handleDelete(record)"
                  >
                    <Button size="small" type="link" danger> 删除 </Button>
                  </Popconfirm>
                </Space>
              </template>
            </template>
            <template #emptyText>
              <ClpmEmptyState
                title="暂无已完成任务"
                description="创建诊断任务并执行完成后，结果会显示在此处"
              />
            </template>
          </Table>
        </ClpmDataCanvas>
      </TabPane>

      <!-- ================== Tab 3：已归档 ================== -->
      <TabPane key="archived">
        <template #tab>
          <Badge :count="tabStats.archived" :offset="[6, 0]" size="small">
            <span>已归档</span>
          </Badge>
        </template>
        <ClpmDataCanvas
          title="已归档任务（历史归档，仅查看/删除，归档后不支持重新执行诊断）"
          :loading="tabStates.archived.loading"
        >
          <template #toolbar>
            <ClpmToolbarButton
              :disabled="!canTriggerNew"
              @click="openTriggerModal"
            >
              <IconifyIcon icon="lucide:play-circle" class="mr-1" />
              新增诊断
            </ClpmToolbarButton>
            <ClpmToolbarButton
              :disabled="selectedCount === 0"
              :loading="batchDeleteLoading"
              @click="handleBatchDelete"
            >
              <IconifyIcon icon="lucide:trash-2" class="mr-1" />
              批量删除
            </ClpmToolbarButton>
            <ClpmToolbarButton @click="cycleDensity">
              <IconifyIcon icon="lucide:table-2" class="mr-1" />
              {{ densityLabel }}
            </ClpmToolbarButton>
          </template>

          <div class="mb-3 flex flex-wrap items-center gap-2">
            <Select
              v-model:value="tabStates.archived.query.status"
              placeholder="任务状态"
              allow-clear
              style="width: 140px"
              :options="statusOptionsByTab.archived"
            />
            <Select
              v-model:value="tabStates.archived.query.triggerType"
              placeholder="触发方式"
              allow-clear
              style="width: 120px"
              :options="triggerTypeOptions"
            />
            <a
              v-if="!tabStates.archived.advancedFilterVisible"
              class="text-xs cursor-pointer"
              :style="{ color: themeColors.INFO }"
              @click="tabStates.archived.advancedFilterVisible = true"
            >
              展开高级筛选 ▾
            </a>
            <template v-if="tabStates.archived.advancedFilterVisible">
              <Select
                v-model:value="tabStates.archived.query.timeWindow"
                placeholder="时间窗口"
                allow-clear
                style="width: 140px"
                :options="timeWindowOptions"
              />
              <a
                class="text-xs cursor-pointer"
                :style="{ color: themeColors.INFO }"
                @click="tabStates.archived.advancedFilterVisible = false"
              >
                收起 ▴
              </a>
            </template>
            <div class="ml-auto flex items-center gap-2">
              <Button size="small" @click="handleResetFor('archived')">
                重置
              </Button>
              <Button
                size="small"
                type="primary"
                @click="handleSearchFor('archived')"
              >
                查询
              </Button>
            </div>
          </div>

          <Table
            :columns="columns"
            :data-source="tabStates.archived.taskList"
            :loading="tabStates.archived.loading"
            :pagination="{
              current: tabStates.archived.query.page,
              pageSize: tabStates.archived.query.pageSize,
              total: tabStates.archived.total,
              showSizeChanger: true,
              showQuickJumper: true,
              showTotal: (total) => `共 ${total} 条`,
            }"
            :row-selection="rowSelection"
            :row-key="(record: DiagnosisApi.TaskItem) => record.taskId"
            :size="tableSize"
            :scroll="{ x: 1100 }"
            @change="(p) => handleTableChangeFor('archived', p)"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'tagName'">
                <ClpmLoopLink
                  :loop-id="record.loopId"
                  :tag-name="record.tagName"
                />
              </template>
              <template v-else-if="column.key === 'compositeScore'">
                <span
                  class="clpm-num font-medium"
                  :style="{ color: scoreColor(record.compositeScore) }"
                >
                  {{ formatScore(record.compositeScore) }}
                </span>
              </template>
              <template v-else-if="column.key === 'status'">
                <div class="flex flex-col items-center gap-1">
                  <Tag :color="statusConfig[record.status]?.color ?? 'default'">
                    {{ statusConfig[record.status]?.text ?? '未知' }}
                  </Tag>
                  <Tag
                    v-if="record.isArchived"
                    color="default"
                    style="padding: 0 4px; margin: 0; font-size: 11px"
                  >
                    归档于 {{ formatTime(record.archivedAt) }}
                  </Tag>
                  <div
                    v-if="record.labels?.length"
                    class="flex flex-wrap gap-1 justify-center max-w-[180px]"
                  >
                    <Tag
                      v-for="(lb, idx) in record.labels.slice(0, 3)"
                      :key="idx"
                      :color="DIAG_LABEL_MAP[lb.label]?.color ?? 'default'"
                      style="padding: 0 4px; margin: 0; font-size: 11px"
                    >
                      {{ diagLabelText(lb.label) }}
                      {{ (lb.confidence * 100).toFixed(0) }}%
                    </Tag>
                    <Tag
                      v-if="record.labels.length > 3"
                      style="padding: 0 4px; margin: 0; font-size: 11px"
                    >
                      +{{ record.labels.length - 3 }}
                    </Tag>
                  </div>
                </div>
              </template>
              <template v-else-if="column.key === 'triggerType'">
                {{ triggerTypeName(record.triggerType) }}
              </template>
              <template v-else-if="column.key === 'triggeredAt'">
                {{ formatTime(record.triggeredAt) }}
              </template>
              <template v-else-if="column.key === 'action'">
                <Space size="small" wrap>
                  <Button
                    v-if="canViewResult(record.status)"
                    size="small"
                    type="link"
                    @click="handleViewDetail(record)"
                  >
                    详情
                  </Button>
                  <Popconfirm
                    v-if="canDelete(record.status)"
                    title="确认删除该归档任务？删除后无法恢复"
                    ok-text="删除"
                    cancel-text="取消"
                    :ok-button-props="{ danger: true }"
                    @confirm="handleDelete(record)"
                  >
                    <Button size="small" type="link" danger> 删除 </Button>
                  </Popconfirm>
                </Space>
              </template>
            </template>
            <template #emptyText>
              <ClpmEmptyState
                title="暂无已归档任务"
                description="完成的诊断任务归档后会显示在此处"
              />
            </template>
          </Table>
        </ClpmDataCanvas>
      </TabPane>
    </Tabs>

    <!-- 新增诊断任务 Modal（与 Tab 无关，公共弹窗） -->
    <Modal
      v-model:open="triggerModalVisible"
      title="新增诊断任务"
      width="820px"
      :confirm-loading="triggerLoading"
      :mask-closable="false"
      @ok="handleTriggerConfirm"
    >
      <div class="space-y-4 py-2">
        <!-- 回路选择 -->
        <div>
          <div class="mb-2 flex items-center justify-between">
            <span class="font-medium">选择回路</span>
            <div class="flex items-center gap-3">
              <Button size="small" type="link" @click="handleSelectAllLoops"
                >全选</Button
              >
              <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
                已选 {{ selectedLoopIds.length }} 个回路
              </span>
            </div>
          </div>
          <div class="mb-2 flex flex-wrap gap-2">
            <Select
              v-model:value="loopAreaFilter"
              placeholder="装置"
              allow-clear
              style="width: 160px"
              :options="areaOptions"
              @change="handleAreaChange"
            />
            <Select
              v-model:value="loopUnitFilter"
              placeholder="单元"
              allow-clear
              style="width: 160px"
              :options="unitOptions"
            />
            <Input
              v-model:value="loopKeyword"
              placeholder="搜索位号 / 名称 / 装置"
              allow-clear
              style="width: 220px"
            />
          </div>
          <Table
            :columns="loopColumns"
            :data-source="filteredLoops"
            :loading="loopLoading"
            :row-key="(record: LoopApi.MonitorListItem) => record.loopId"
            :row-selection="loopRowSelection"
            :pagination="{ pageSize: 8, size: 'small' }"
            :scroll="{ y: 320 }"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'tagName'">
                <span class="font-mono text-sm">{{ record.tagName }}</span>
              </template>
              <template v-else-if="column.key === 'score'">
                <span
                  class="clpm-num font-medium"
                  :style="{ color: scoreColor(record.score) }"
                >
                  {{ formatScore(record.score) }}
                </span>
              </template>
              <template v-else-if="column.key === 'effectiveAutoRate'">
                <span class="clpm-num">{{
                  formatRate(record.effectiveAutoRate)
                }}</span>
              </template>
            </template>
          </Table>
        </div>

        <!-- 诊断标签（B6 按需诊断） -->
        <div>
          <div class="mb-2 flex items-center justify-between">
            <span class="font-medium">诊断标签</span>
            <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
              默认全选（全量诊断）；可按需勾选标签子集
            </span>
          </div>
          <Select
            v-model:value="selectedLabels"
            mode="multiple"
            :options="triggerLabelOptions"
            placeholder="选择诊断标签"
            style="width: 100%"
            :max-tag-count="4"
          />
        </div>

        <!-- 诊断时间范围 -->
        <div>
          <div class="mb-2 font-medium">诊断时间范围</div>
          <DatePicker.RangePicker
            v-model:value="triggerTimeRange"
            :allow-clear="false"
            :show-time="{ format: 'HH:mm' }"
            format="YYYY-MM-DD HH:mm"
            :presets="rangePresets"
            style="width: 100%"
          />
          <div class="mt-1 text-xs" :style="{ color: themeColors.NEUTRAL }">
            快捷选项：最近 1 小时 / 24 小时 / 7 天；最大 30 天时间窗口
          </div>
        </div>
      </div>
    </Modal>

    <!-- 归档诊断任务：危险确认弹窗（软操作可恢复，免确认码） -->
    <ClpmDangerConfirmModal
      v-model:open="archiveOpen"
      title="归档诊断任务"
      action="归档"
      :target="archiveTarget?.tagName ?? ''"
      impact-scope="归档后任务将从诊断任务列表移除，可在「已归档」Tab 中查看"
      rollback-tip="此操作为软归档，记录仍保留在数据库中"
      :require-confirm-code="false"
      :loading="archiveLoading"
      @confirm="handleArchiveConfirm"
    />

    <!-- 删除诊断任务：危险确认弹窗 -->
    <ClpmDangerConfirmModal
      v-model:open="deleteOpen"
      title="删除诊断任务"
      action="删除"
      :target="deleteTarget?.tagName ?? ''"
      impact-scope="删除后该回路的诊断任务将不可恢复"
      rollback-tip="此操作不可逆，删除后无法恢复"
      require-confirm-code
      confirm-code-placeholder="请输入回路 tag 以确认"
      :loading="deleteLoading"
      @confirm="handleDeleteConfirm"
    />

    <!-- 批量删除诊断任务：危险确认弹窗（批量软确认，免确认码） -->
    <ClpmDangerConfirmModal
      v-model:open="batchDeleteOpen"
      title="批量删除诊断任务"
      action="删除"
      :target="`选中的 ${batchDeleteTargets.length} 个任务`"
      :impact-scope="`删除后这些诊断任务将不可恢复${batchDeleteSkipped > 0 ? `（已跳过 ${batchDeleteSkipped} 个执行中任务）` : ''}`"
      rollback-tip="此操作不可逆，删除后无法恢复"
      :require-confirm-code="false"
      :loading="batchDeleteLoading"
      @confirm="handleBatchDeleteConfirm"
    />
  </Page>
</template>
