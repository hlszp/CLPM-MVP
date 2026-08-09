<script lang="ts" setup>
/**
 * 诊断任务页
 *
 * 对齐 PRD §4.4 + 实现契约 v2.0
 * - 表格展示未归档诊断任务（每回路一行）
 * - 行级操作：诊断 / 取消 / 详情 / 归档 / 删除（状态机控制按钮可用性）
 * - RUNNING 状态行内显示进度条
 * - 触发诊断 Modal：多选回路 + 时间范围（含快捷选项）
 * - 详情跳转诊断详情页（标签 + 证据链）
 * - PENDING/RUNNING 状态任务每 5 秒轮询，直到终态
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
  Button,
  Checkbox,
  DatePicker,
  Input,
  message,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  archiveDiagnosisTaskApi,
  cancelDiagnosisTaskApi,
  deleteDiagnosisTaskApi,
  getDiagnosisTasksApi,
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
import { runWithConcurrency } from '#/utils/concurrency';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'DiagnosisTasks' });

const { themeColors } = useClpmTheme();

// ===== A-07：表格密度三档（紧凑/标准/宽松，持久化）=====
const { tableSize, densityLabel, cycleDensity } =
  useTableDensity('diagnosis-tasks');

const loading = ref(false);
const taskList = ref<DiagnosisApi.TaskItem[]>([]);
const total = ref(0);
const selectedRowKeys = ref<string[]>([]);

/** 轮询定时器（PENDING/RUNNING 状态任务每 5 秒刷新）；递归 setTimeout 防止慢请求堆积 */
let pollTimer: null | ReturnType<typeof setTimeout> = null;

const query = reactive({
  status: undefined as string | undefined,
  triggerType: undefined as string | undefined,
  timeWindow: undefined as DiagnosisApi.TimeWindow | undefined,
  // 是否包含已归档任务（SUCCESS 完成即自动归档；开启后任务页含历史）
  includeArchived: false,
  page: 1,
  pageSize: 20,
});

/** 任务状态选项 */
const statusOptions: { label: string; value: DiagnosisApi.TaskStatus }[] = [
  { label: '待执行', value: 'PENDING' },
  { label: '执行中', value: 'RUNNING' },
  { label: '成功', value: 'SUCCESS' },
  { label: '失败', value: 'FAILED' },
  { label: '已取消', value: 'CANCELLED' },
];

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

/** 任务状态 → Tag 颜色与中文文案 */
const statusConfig: Record<
  DiagnosisApi.TaskStatus,
  { color: string; text: string }
> = {
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
/** 归档：仅诊断完成（SUCCESS）可归档 */
function canArchive(status: DiagnosisApi.TaskStatus): boolean {
  return status === 'SUCCESS';
}
/** 取消：仅待执行/执行中可取消 */
function canCancel(status: DiagnosisApi.TaskStatus): boolean {
  return status === 'PENDING' || status === 'RUNNING';
}
/** 删除：执行中（RUNNING）不可删除，须先取消 */
function canDelete(status: DiagnosisApi.TaskStatus): boolean {
  return status !== 'RUNNING';
}

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
    width: 160,
    align: 'center',
  },
  {
    title: '触发方式',
    dataIndex: 'triggerType',
    key: 'triggerType',
    width: 100,
    align: 'center',
  },
  { title: '操作', key: 'action', width: 280, fixed: 'right' },
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

/** RangePicker 预设快捷选项（P0-5: 改 computed，每次打开 Modal 时重新计算时间戳） */
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
    triggerModalVisible.value = false;
    await loadTasks();
    startPolling();
  } catch {
    // 错误已由拦截器处理
  } finally {
    triggerLoading.value = false;
  }
}

/** 取消：仅 PENDING/RUNNING 可取消（可逆轻操作走 Popconfirm 确认） */
async function handleCancel(record: DiagnosisApi.TaskItem) {
  await cancelDiagnosisTaskApi(record.taskId);
  message.success('任务已取消');
  await loadTasks();
}

/** 详情：跳转到诊断详情页 */
function handleViewDetail(record: DiagnosisApi.TaskItem) {
  router.push({
    path: `/diagnosis/detail/${record.loopId}`,
    query: { taskId: record.taskId },
  });
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

// ============ 批量操作 ============ 诊断 / 归档 / 删除（确认弹窗） ============
const rowDiagnoseLoading = ref<string>('');
/** 行级诊断（可逆轻操作走 Popconfirm 确认） */
async function handleRowDiagnose(record: DiagnosisApi.TaskItem) {
  rowDiagnoseLoading.value = record.taskId;
  try {
    await runDiagnosisTaskApi(record.taskId);
    message.success(`已执行回路 ${record.tagName} 的诊断`);
    await loadTasks();
    startPolling();
  } catch {
    // 错误已由拦截器处理
  } finally {
    rowDiagnoseLoading.value = '';
  }
}

/** 归档：软操作可恢复（可在诊断记录中查看），危险确认弹窗免确认码 */
const archiveOpen = ref(false);
const archiveTarget = ref<DiagnosisApi.TaskItem | null>(null);
const archiveLoading = ref(false);

function handleArchive(record: DiagnosisApi.TaskItem) {
  archiveTarget.value = record;
  archiveOpen.value = true;
}

async function handleArchiveConfirm() {
  if (!archiveTarget.value) return;
  archiveLoading.value = true;
  try {
    await archiveDiagnosisTaskApi(archiveTarget.value.taskId);
    message.success('任务已归档');
    archiveOpen.value = false;
    await loadTasks();
  } finally {
    archiveLoading.value = false;
  }
}

/** 行级删除：危险确认弹窗（UIUX v6.1 §9.8 / §14 P-01） */
const deleteOpen = ref(false);
const deleteTarget = ref<DiagnosisApi.TaskItem | null>(null);
const deleteLoading = ref(false);

function handleDelete(record: DiagnosisApi.TaskItem) {
  deleteTarget.value = record;
  deleteOpen.value = true;
}

async function handleDeleteConfirm() {
  if (!deleteTarget.value) return;
  const record = deleteTarget.value;
  deleteLoading.value = true;
  try {
    await deleteDiagnosisTaskApi(record.taskId);
    message.success('任务已删除');
    selectedRowKeys.value = selectedRowKeys.value.filter(
      (k) => k !== record.taskId,
    );
    deleteOpen.value = false;
    await loadTasks();
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
  const selectedAll = taskList.value.filter((t) =>
    selectedRowKeys.value.includes(t.taskId),
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
    // allSettled 语义 + 并发限制：单项失败不中断其余删除
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
    selectedRowKeys.value = [];
    batchDeleteOpen.value = false;
    await loadTasks();
  } catch {
    // 错误已由拦截器处理
  } finally {
    batchDeleteLoading.value = false;
  }
}

/** 批量诊断：对选中的任务行执行诊断（不创建新任务） */
const batchDiagnoseLoading = ref(false);
async function handleBatchTrigger() {
  const selected = taskList.value.filter((t) =>
    selectedRowKeys.value.includes(t.taskId),
  );
  if (selected.length === 0) {
    message.warning('请先选中需要诊断的任务');
    return;
  }
  batchDiagnoseLoading.value = true;
  try {
    // allSettled 语义 + 并发限制：批量诊断并发数受控，单项失败不中断其余
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
    selectedRowKeys.value = [];
    await loadTasks();
    startPolling();
  } catch {
    // 错误已由拦截器处理
  } finally {
    batchDiagnoseLoading.value = false;
  }
}

// ============ 数据加载与轮询 ============
let pollCount = 0;
const MAX_POLL_COUNT = 120; // 最多轮询 120 次（10 分钟）

async function loadTasks(silent = false) {
  if (!silent) loading.value = true;
  try {
    const data = await getDiagnosisTasksApi({
      status: query.status,
      triggerType: query.triggerType,
      timeWindow: query.timeWindow,
      includeArchived: query.includeArchived,
      page: query.page,
      pageSize: query.pageSize,
    });
    taskList.value = data.items || [];
    total.value = data.total || 0;
  } catch {
    // 错误已由拦截器处理
  } finally {
    if (!silent) loading.value = false;
  }
}

function startPolling() {
  stopPolling();
  pollCount = 0;
  schedulePoll();
}

/** 递归 setTimeout：等上一次 loadTasks 完成后再排定下一次，避免慢请求时回调堆积 */
function schedulePoll() {
  pollTimer = setTimeout(async () => {
    pollCount++;
    if (pollCount > MAX_POLL_COUNT) {
      stopPolling();
      return;
    }
    await loadTasks(true);
    const hasActive = taskList.value.some(
      (t) => t.status === 'PENDING' || t.status === 'RUNNING',
    );
    if (!hasActive) {
      stopPolling();
      return;
    }
    schedulePoll();
  }, 5000);
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function handleSearch() {
  query.page = 1;
  loadTasks();
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 20;
  loadTasks();
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

/** 行选择配置 */
const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys.map(String);
  },
}));

onMounted(() => {
  loadTasks();
});

onBeforeUnmount(() => {
  stopPolling();
});
</script>

<template>
  <Page>
    <ClpmDataCanvas title="诊断任务列表" :loading="loading">
      <!-- 筛选栏 -->
      <div class="mb-3 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="query.status"
          placeholder="任务状态"
          style="width: 140px"
          allow-clear
          :options="statusOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.triggerType"
          placeholder="触发方式"
          style="width: 120px"
          allow-clear
          :options="triggerTypeOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.timeWindow"
          placeholder="时间窗"
          style="width: 140px"
          allow-clear
          :options="timeWindowOptions"
          @change="handleSearch"
        />
        <Checkbox
          v-model:checked="query.includeArchived"
          @change="handleSearch"
        >
          显示已归档
        </Checkbox>
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
      </div>

      <!-- 操作按钮区（触发类按钮对齐后端 require_roles("ADMIN","IC_ENGINEER","PE_ENGINEER")） -->
      <div class="mb-3 flex items-center gap-2">
        <Button
          v-permission="['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER']"
          type="primary"
          @click="openTriggerModal"
        >
          <template #icon
            ><IconifyIcon icon="ant-design:plus-outlined"
          /></template>
          新增任务
        </Button>
        <Button
          v-permission="['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER']"
          type="primary"
          :disabled="selectedRowKeys.length === 0"
          :loading="batchDiagnoseLoading"
          @click="handleBatchTrigger"
        >
          <template #icon
            ><IconifyIcon icon="ant-design:thunderbolt-outlined"
          /></template>
          批量诊断{{
            selectedRowKeys.length > 0 ? `（${selectedRowKeys.length}）` : ''
          }}
        </Button>
        <Button
          danger
          :disabled="selectedRowKeys.length === 0"
          :loading="batchDeleteLoading"
          @click="handleBatchDelete"
        >
          <template #icon
            ><IconifyIcon icon="ant-design:delete-outlined"
          /></template>
          批量删除
        </Button>
        <!-- A-07：密度三档切换（紧凑/标准/宽松，点击循环） -->
        <ClpmToolbarButton
          class="ml-auto"
          icon="ant-design:column-height-outlined"
          :label="`密度：${densityLabel}`"
          :tooltip="`密度：${densityLabel}（点击切换）`"
          @click="cycleDensity"
        />
      </div>

      <Table
        :columns="columns"
        :data-source="taskList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: DiagnosisApi.TaskItem) => record.taskId"
        :row-selection="rowSelection"
        :scroll="{ x: 1030 }"
        :size="tableSize"
        @change="handleTableChange"
      >
        <template #emptyText>
          <ClpmEmptyState scene="task" icon="lucide:clipboard-list" />
        </template>
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'tagName'">
            <ClpmLoopLink
              :loop-id="record.loopId"
              :tag-name="record.tagName"
              :unit-name="record.unitName"
              default-target="diagnosis"
            />
          </template>
          <template v-else-if="column.key === 'compositeScore'">
            <Tooltip>
              <template #title>
                <div class="text-xs leading-relaxed">
                  <div>准确率：{{ formatScore(record.accuracyScore) }}</div>
                  <div>快速率：{{ formatScore(record.fastScore) }}</div>
                  <div>平稳率：{{ formatScore(record.steadyScore) }}</div>
                  <div>自控率：{{ formatRate(record.effectiveAutoRate) }}</div>
                </div>
              </template>
              <span
                class="clpm-num font-medium cursor-help"
                :style="{ color: scoreColor(record.compositeScore) }"
              >
                {{ formatScore(record.compositeScore) }}
              </span>
            </Tooltip>
          </template>
          <template v-else-if="column.key === 'status'">
            <div
              class="flex items-center justify-center gap-1 whitespace-nowrap"
            >
              <Tag
                :color="
                  statusConfig[record.status as DiagnosisApi.TaskStatus]
                    ?.color ?? 'default'
                "
              >
                {{
                  statusConfig[record.status as DiagnosisApi.TaskStatus]
                    ?.text ?? record.status
                }}
              </Tag>
              <!-- 已归档标识（开启"显示已归档"后区分历史任务） -->
              <Tag
                v-if="record.isArchived"
                color="default"
                style="font-size: 11px"
              >
                已归档
              </Tag>
              <!-- RUNNING 状态显示进度条 -->
              <Progress
                v-if="record.status === 'RUNNING'"
                :percent="100"
                :show-info="false"
                size="small"
                status="active"
                stroke-color="var(--status-info)"
                style="width: 60px"
              />
            </div>
          </template>
          <template v-else-if="column.key === 'triggerType'">
            <Tooltip>
              <template #title>
                <div class="text-xs leading-relaxed">
                  <div>创建时间：{{ formatTime(record.triggeredAt) }}</div>
                  <template
                    v-if="record.diagLabels && record.diagLabels.length > 0"
                  >
                    <div>
                      诊断标签：{{
                        record.diagLabels.map(diagLabelText).join('、')
                      }}
                    </div>
                  </template>
                  <template
                    v-else-if="
                      record.status === 'FAILED' && record.errorMessage
                    "
                  >
                    <div style="color: var(--status-error)">
                      错误：{{ record.errorMessage }}
                    </div>
                  </template>
                </div>
              </template>
              <span class="cursor-help">
                {{ triggerTypeName(record.triggerType) }}
              </span>
            </Tooltip>
          </template>
          <template v-else-if="column.key === 'action'">
            <!-- 诊断 → 取消 → 详情 → 归档 → 删除，基于状态机控制可用性 -->
            <!-- 行级"诊断"对齐后端 POST /tasks/{id}/run require_roles("ADMIN","IC_ENGINEER","PE_ENGINEER") -->
            <Popconfirm
              :title="`确认对回路 ${record.tagName} 执行诊断？`"
              @confirm="handleRowDiagnose(record as DiagnosisApi.TaskItem)"
            >
              <Button
                v-permission="['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER']"
                type="link"
                size="small"
                :disabled="
                  !canDiagnose(record.status as DiagnosisApi.TaskStatus)
                "
                :loading="rowDiagnoseLoading === record.taskId"
              >
                诊断
              </Button>
            </Popconfirm>
            <Popconfirm
              :title="`确认取消回路 ${record.tagName} 的诊断任务？`"
              ok-type="danger"
              @confirm="handleCancel(record as DiagnosisApi.TaskItem)"
            >
              <Button
                type="link"
                size="small"
                :disabled="!canCancel(record.status as DiagnosisApi.TaskStatus)"
              >
                取消
              </Button>
            </Popconfirm>
            <Button
              v-permission="['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT']"
              type="link"
              size="small"
              :disabled="
                !canViewResult(record.status as DiagnosisApi.TaskStatus)
              "
              @click="handleViewDetail(record as DiagnosisApi.TaskItem)"
            >
              详情
            </Button>
            <Button
              type="link"
              size="small"
              :disabled="!canArchive(record.status as DiagnosisApi.TaskStatus)"
              @click="handleArchive(record as DiagnosisApi.TaskItem)"
            >
              归档
            </Button>
            <Button
              type="link"
              size="small"
              danger
              :disabled="!canDelete(record.status as DiagnosisApi.TaskStatus)"
              @click="handleDelete(record as DiagnosisApi.TaskItem)"
            >
              删除
            </Button>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 新增诊断任务 Modal -->
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
      impact-scope="归档后任务将从诊断任务列表移除，可在诊断记录中查看"
      rollback-tip="此操作为软归档，记录仍可在诊断记录页查看"
      :require-confirm-code="false"
      :loading="archiveLoading"
      @confirm="handleArchiveConfirm"
    />

    <!-- 删除诊断任务：危险确认弹窗（UIUX v6.1 §9.8 / §14 P-01） -->
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
