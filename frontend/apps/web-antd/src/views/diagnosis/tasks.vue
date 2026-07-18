<script lang="ts" setup>
/**
 * 诊断任务页
 *
 * 对齐 PRD §4.4 + 实现契约 v2.0
 * - 表格展示未归档诊断任务（每回路一行）
 * - 行级操作：诊断 / 取消 / 归档 / 结果（状态机控制按钮可用性）
 * - RUNNING 状态行内显示进度条
 * - 触发诊断 Modal：多选回路 + 时间范围（含快捷选项）
 * - 结果查看 Drawer：诊断标签 + 证据链
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
  DatePicker,
  Drawer,
  Empty,
  Input,
  message,
  Modal,
  Progress,
  Select,
  Spin,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  archiveDiagnosisTaskApi,
  deleteDiagnosisTaskApi,
  getDiagnosisTasksApi,
  runDiagnosisTaskApi,
  triggerDiagnosisApi,
} from '#/api/diagnosis';
import { getLoopMonitorListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { DIAGNOSIS_LABEL_COLOR_MAP } from '#/constants/diagnosis';

defineOptions({ name: 'DiagnosisTasks' });

const { themeColors } = useClpmTheme();

const loading = ref(false);
const taskList = ref<DiagnosisApi.TaskItem[]>([]);
const total = ref(0);
const selectedRowKeys = ref<string[]>([]);

/** 轮询定时器（PENDING/RUNNING 状态任务每 5 秒刷新） */
let pollTimer: null | ReturnType<typeof setInterval> = null;

const query = reactive({
  status: undefined as string | undefined,
  triggerType: undefined as string | undefined,
  timeWindow: undefined as DiagnosisApi.TimeWindow | undefined,
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
/** 结果：仅诊断完成（SUCCESS）可查看 */
function canViewResult(status: DiagnosisApi.TaskStatus): boolean {
  return status === 'SUCCESS';
}
/** 归档：仅诊断完成（SUCCESS）可归档 */
function canArchive(status: DiagnosisApi.TaskStatus): boolean {
  return status === 'SUCCESS';
}

const columns: TableColumnsType = [
  { title: '回路名称', dataIndex: 'tagName', key: 'tagName', width: 140 },
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
    title: '准确率',
    dataIndex: 'accuracyScore',
    key: 'accuracyScore',
    width: 80,
    align: 'center',
  },
  {
    title: '快速率',
    dataIndex: 'fastScore',
    key: 'fastScore',
    width: 80,
    align: 'center',
  },
  {
    title: '平稳率',
    dataIndex: 'steadyScore',
    key: 'steadyScore',
    width: 80,
    align: 'center',
  },
  {
    title: '自控率',
    dataIndex: 'effectiveAutoRate',
    key: 'effectiveAutoRate',
    width: 80,
    align: 'center',
  },
  {
    title: '任务状态',
    dataIndex: 'status',
    key: 'status',
    width: 140,
    align: 'center',
  },
  {
    title: '触发方式',
    dataIndex: 'triggerType',
    key: 'triggerType',
    width: 90,
    align: 'center',
  },
  {
    title: '结果',
    dataIndex: 'diagLabels',
    key: 'diagLabels',
    width: 180,
  },
  {
    title: '创建时间',
    dataIndex: 'triggeredAt',
    key: 'triggeredAt',
    width: 160,
  },
  { title: '操作', key: 'action', width: 260, fixed: 'right' },
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

/** RangePicker 预设快捷选项 */
const rangePresets = [
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
];

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
    await triggerDiagnosisApi({
      loopIds: selectedLoopIds.value,
      startTime: start.toISOString(),
      endTime: end.toISOString(),
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

// ============ 结果查看 Drawer ============
const resultDrawerVisible = ref(false);
const resultDetail = ref<DiagnosisApi.TaskDetail | null>(null);
const resultLoading = ref(false);

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

function diagLabelColor(label: string): string {
  return DIAG_LABEL_MAP[label]?.color ?? 'default';
}

// ============ 批量操作 ============ 诊断 / 归档 / 删除（确认弹窗） ============
const rowDiagnoseLoading = ref<string>('');
async function handleRowDiagnose(record: DiagnosisApi.TaskItem) {
  Modal.confirm({
    title: '确认诊断',
    content: `确认对回路 ${record.tagName} 执行诊断？`,
    onOk: async () => {
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
    },
  });
}

async function handleArchive(taskId: string) {
  Modal.confirm({
    title: '确认归档',
    content: '归档后任务将从诊断任务列表移除，可在诊断记录中查看。',
    onOk: async () => {
      await archiveDiagnosisTaskApi(taskId);
      message.success('任务已归档');
      await loadTasks();
    },
  });
}

async function handleDelete(record: DiagnosisApi.TaskItem) {
  Modal.confirm({
    title: '确认删除',
    content: `确认删除回路 ${record.tagName} 的诊断任务？`,
    okType: 'danger',
    onOk: async () => {
      await deleteDiagnosisTaskApi(record.taskId);
      message.success('任务已删除');
      selectedRowKeys.value = selectedRowKeys.value.filter(
        (k) => k !== record.taskId,
      );
      await loadTasks();
    },
  });
}

/** 批量删除：删除选中的所有任务（测试期间不限制状态） */
const batchDeleteLoading = ref(false);
async function handleBatchDelete() {
  const selected = taskList.value.filter((t) =>
    selectedRowKeys.value.includes(t.taskId),
  );
  if (selected.length === 0) {
    message.warning('请先选择要删除的任务');
    return;
  }
  Modal.confirm({
    title: '确认批量删除',
    content: `确认删除 ${selected.length} 个诊断任务？`,
    okType: 'danger',
    onOk: async () => {
      batchDeleteLoading.value = true;
      try {
        await Promise.all(
          selected.map((t) => deleteDiagnosisTaskApi(t.taskId)),
        );
        message.success(`已删除 ${selected.length} 个任务`);
        selectedRowKeys.value = [];
        await loadTasks();
      } catch {
        // 错误已由拦截器处理
      } finally {
        batchDeleteLoading.value = false;
      }
    },
  });
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
    let successCount = 0;
    let failCount = 0;
    await Promise.all(
      selected.map(async (t) => {
        try {
          await runDiagnosisTaskApi(t.taskId);
          successCount++;
        } catch {
          failCount++;
        }
      }),
    );
    if (successCount > 0) {
      message.success(
        `已执行 ${successCount} 个任务的诊断${failCount > 0 ? `，${failCount} 个失败` : ''}`,
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
  pollTimer = setInterval(async () => {
    pollCount++;
    if (pollCount > MAX_POLL_COUNT) {
      stopPolling();
      return;
    }
    await loadTasks(true);
    const hasActive = taskList.value.some(
      (t) => t.status === 'PENDING' || t.status === 'RUNNING',
    );
    if (!hasActive) stopPolling();
  }, 5000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
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

function formatTime(t: null | string | undefined): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return t;
  }
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

function labelColor(label: string): string {
  return (
    DIAGNOSIS_LABEL_COLOR_MAP[
      label as keyof typeof DIAGNOSIS_LABEL_COLOR_MAP
    ] || 'default'
  );
}

function labelName(label: string): string {
  const map: Record<string, string> = {
    EXTERNAL_DISTURBANCE: '外扰频繁',
    MANUAL_REVIEW: '人工复核',
    OSCILLATION: '振荡',
    OUTPUT_SATURATION: '输出饱和',
    OVERAGGRESSIVE: '参数过激',
    OVERCONSERVATIVE: '参数过保守',
    QUALITY_ABNORMAL: 'PV 质量异常',
    VALVE_STICTION: '阀门粘滞',
  };
  return map[label] || label;
}

function formatEvidence(
  evidence: Record<string, unknown>,
): { key: string; value: string }[] {
  if (!evidence || typeof evidence !== 'object') return [];
  return Object.entries(evidence).map(([k, v]) => ({
    key: k,
    value: typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v),
  }));
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
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
      </div>

      <!-- 操作按钮区 -->
      <div class="mb-3 flex items-center gap-2">
        <Button type="primary" @click="openTriggerModal">
          <template #icon
            ><IconifyIcon icon="ant-design:plus-outlined"
          /></template>
          新增任务
        </Button>
        <Button
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
        :scroll="{ x: 1320 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'compositeScore'">
            <span
              class="clpm-num font-medium"
              :style="{ color: scoreColor(record.compositeScore) }"
            >
              {{ formatScore(record.compositeScore) }}
            </span>
          </template>
          <template v-else-if="column.key === 'accuracyScore'">
            <span class="clpm-num">{{
              formatScore(record.accuracyScore)
            }}</span>
          </template>
          <template v-else-if="column.key === 'fastScore'">
            <span class="clpm-num">{{ formatScore(record.fastScore) }}</span>
          </template>
          <template v-else-if="column.key === 'steadyScore'">
            <span class="clpm-num">{{ formatScore(record.steadyScore) }}</span>
          </template>
          <template v-else-if="column.key === 'effectiveAutoRate'">
            <span class="clpm-num">{{
              formatRate(record.effectiveAutoRate)
            }}</span>
          </template>
          <template v-else-if="column.key === 'status'">
            <div class="flex flex-col items-center gap-1">
              <Tag
                :color="
                  statusConfig[record.status as DiagnosisApi.TaskStatus].color
                "
              >
                {{
                  statusConfig[record.status as DiagnosisApi.TaskStatus].text
                }}
              </Tag>
              <!-- RUNNING 状态显示进度条 -->
              <Progress
                v-if="record.status === 'RUNNING'"
                :percent="100"
                :show-info="false"
                size="small"
                status="active"
                stroke-color="#1677ff"
                style="width: 100px"
              />
            </div>
          </template>
          <template v-else-if="column.key === 'triggerType'">
            {{ triggerTypeName(record.triggerType) }}
          </template>
          <template v-else-if="column.key === 'diagLabels'">
            <template v-if="record.diagLabels && record.diagLabels.length > 0">
              <Tag
                v-for="label in record.diagLabels"
                :key="label"
                :color="diagLabelColor(label)"
                size="small"
                style="margin-bottom: 2px"
              >
                {{ diagLabelText(label) }}
              </Tag>
            </template>
            <Tooltip
              v-else-if="record.status === 'FAILED' && record.errorMessage"
              :title="record.errorMessage"
            >
              <Tag color="error" size="small">诊断失败</Tag>
            </Tooltip>
            <span v-else style="color: var(--text-color-secondary)">—</span>
          </template>
          <template v-else-if="column.key === 'triggeredAt'">
            <span class="clpm-num">{{ formatTime(record.triggeredAt) }}</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <!-- 诊断 → 详情 → 归档 → 删除，基于状态机控制可用性 -->
            <Button
              type="link"
              size="small"
              :disabled="!canDiagnose(record.status as DiagnosisApi.TaskStatus)"
              :loading="rowDiagnoseLoading === record.taskId"
              @click="handleRowDiagnose(record as DiagnosisApi.TaskItem)"
            >
              诊断
            </Button>
            <Button
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
              @click="handleArchive(record.taskId)"
            >
              归档
            </Button>
            <Button
              type="link"
              size="small"
              danger
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

    <!-- 结果查看 Drawer -->
    <Drawer
      v-model:open="resultDrawerVisible"
      title="诊断结果"
      placement="right"
      width="480"
      :destroy-on-close="true"
    >
      <Spin :spinning="resultLoading">
        <div v-if="resultDetail" class="space-y-4">
          <div
            class="rounded border p-3"
            style="border-color: hsl(var(--border))"
          >
            <div class="mb-2 text-base font-semibold">
              {{ resultDetail.tagName }}
            </div>
            <div class="space-y-1 text-sm">
              <div>
                <span :style="{ color: themeColors.NEUTRAL }">任务状态：</span>
                <Tag :color="statusConfig[resultDetail.status].color">
                  {{ statusConfig[resultDetail.status].text }}
                </Tag>
              </div>
              <div>
                <span :style="{ color: themeColors.NEUTRAL }">触发方式：</span>
                {{ triggerTypeName(resultDetail.triggerType) }}
              </div>
              <div>
                <span :style="{ color: themeColors.NEUTRAL }">诊断时间：</span>
                <span class="clpm-num">{{
                  formatTime(resultDetail.triggeredAt)
                }}</span>
              </div>
              <div>
                <span :style="{ color: themeColors.NEUTRAL }">数据范围：</span>
                <span class="clpm-num">{{
                  `${formatTime(resultDetail.timeRangeStart)} ~ ${formatTime(
                    resultDetail.timeRangeEnd,
                  )}`
                }}</span>
              </div>
              <div v-if="resultDetail.errorMessage">
                <span :style="{ color: themeColors.DANGER }">错误信息：</span>
                {{ resultDetail.errorMessage }}
              </div>
            </div>
          </div>

          <div v-if="resultDetail.results.length > 0">
            <div class="mb-2 font-medium">诊断标签</div>
            <div class="space-y-2">
              <div
                v-for="(item, idx) in resultDetail.results"
                :key="idx"
                class="flex items-center justify-between rounded border p-2"
                style="border-color: hsl(var(--border))"
              >
                <Tag :color="labelColor(item.diagLabel)">
                  {{ labelName(item.diagLabel) }}
                </Tag>
                <span
                  class="clpm-num text-sm"
                  :style="{
                    color:
                      item.confidence >= 0.8
                        ? themeColors.DANGER
                        : item.confidence >= 0.5
                          ? themeColors.WARNING
                          : themeColors.NEUTRAL,
                  }"
                >
                  置信度 {{ (item.confidence * 100).toFixed(0) }}%
                </span>
              </div>
            </div>
          </div>

          <div v-if="resultDetail.results.length > 0">
            <div class="mb-2 font-medium">证据链</div>
            <div class="space-y-3">
              <div
                v-for="(item, idx) in resultDetail.results"
                :key="`evidence-${idx}`"
                class="rounded border p-2"
                style="border-color: hsl(var(--border))"
              >
                <div class="mb-1 text-sm font-medium">
                  {{ labelName(item.diagLabel) }}
                </div>
                <ul class="ml-4 list-disc space-y-1 text-xs">
                  <li
                    v-for="ev in formatEvidence(item.evidenceChain)"
                    :key="ev.key"
                  >
                    <span :style="{ color: themeColors.NEUTRAL }">
                      {{ ev.key }}：
                    </span>
                    <span class="clpm-num">{{ ev.value }}</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>

          <Empty
            v-if="resultDetail.results.length === 0"
            description="暂无诊断结果"
          />
        </div>
        <Empty v-else description="暂无数据" />
      </Spin>
    </Drawer>
  </Page>
</template>
