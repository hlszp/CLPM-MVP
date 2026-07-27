<script lang="ts" setup>
/**
 * S4-DIAG-010 Action Tracker 页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 表格展示跟踪记录列表（回路位号/诊断标签/状态/创建时间/更新时间/操作）
 * - 状态标签颜色：PENDING(default)/IN_PROGRESS(processing)/IMPLEMENTED(success)/IGNORED(warning)
 * - 顶部 KpiStrip：待处理 / 处理中 / 已实施 / 已忽略 各状态计数
 * - 状态机可视化：待处理 → 处理中 → 已实施 / 已忽略
 * - 状态更新下拉菜单（仅 IC_ENGINEER 可操作），Modal 含"变更说明"审计字段
 * - "A/B 对比"按钮打开抽屉展示处置前后 KPI 对比图表
 * - "导出 PDF"按钮后端同步生成诊断建议书，前端 Blob 直接下载（FDS §5.4.4）
 * - 筛选栏（状态/标签/时间）
 * - 抽屉模式与独立页模式统一使用 CLPM 组件
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';
import type { KpiStripItem } from '#/components/clpm';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import {
  Button,
  Checkbox,
  Drawer,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Select,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  exportDiagnosisPdfApi,
  exportDiagnosisStatisticsApi,
  getTrackerListApi,
  updateTrackerStatusApi,
} from '#/api/diagnosis';
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useIndustrialStatus } from '#/composables/use-industrial-status';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_OPTIONS,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';

import AbCompare from './ab-compare.vue';

defineOptions({ name: 'DiagnosisTracker' });

const props = withDefaults(
  defineProps<{
    /** 抽屉模式（从诊断列表页/详情页打开） */
    drawerMode?: boolean;
    /** 指定回路 ID（抽屉模式，可选预填筛选） */
    loopId?: string;
  }>(),
  {
    drawerMode: false,
    loopId: '',
  },
);

const emit = defineEmits<{
  (e: 'close'): void;
}>();

const router = useRouter();
const route = useRoute();
const userStore = useUserStore();

const { getStatusMeta } = useIndustrialStatus();
const { themeColors } = useClpmTheme();

/**
 * 角色判断（替代 v-permission 传角色名的误用——v-permission 校验 accessCodes 权限码，
 * 传角色名会导致 el.remove() 误删组件 DOM，Dropdown 内部状态被破坏后菜单无法展开）
 */
const userRoles = computed(() => userStore.userInfo?.roles ?? []);
const canEditStatus = computed(() =>
  userRoles.value.some((r) => r === 'IC_ENGINEER'),
);
const canViewAbCompare = computed(() =>
  userRoles.value.some((r) =>
    ['IC_ENGINEER', 'ADMIN', 'EXPERT'].includes(r),
  ),
);
const canExportPdf = computed(() =>
  userRoles.value.some((r) =>
    ['IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'].includes(r),
  ),
);

/**
 * 动态容器：抽屉模式用 Drawer，独立页模式用 Page
 * 通过 v-bind="containerProps" 和 v-on="containerListeners" 处理两模式 props/事件差异，
 * 消除抽屉/独立页约 500 行模板重复。
 */
const containerComponent = computed(() => (props.drawerMode ? Drawer : Page));
const containerProps = computed(() =>
  props.drawerMode
    ? { open: true, title: '异常跟踪', width: '80%', placement: 'right' }
    : {},
);
const containerListeners = computed(() =>
  props.drawerMode ? { close: () => emit('close') } : {},
);

const loading = ref(false);
const trackerList = ref<DiagnosisApi.TrackerItem[]>([]);
const total = ref(0);
/** 全量聚合统计（后端 SQL group-by，不受分页影响） */
const aggregates = ref<DiagnosisApi.DiagnosisAggregates | null>(null);

const query = reactive({
  diagnosisLabel: undefined as DiagnosisLabel | undefined,
  actionStatus: undefined as DiagnosisApi.ActionStatus | undefined,
  loopId: props.loopId || undefined,
  timeWindow: 'last_7_days' as DiagnosisApi.TimeWindow,
  page: 1,
  pageSize: 20,
});

/** 8 类诊断标签选项 */
const labelOptions = DIAGNOSIS_LABEL_OPTIONS;

/** 标签颜色映射 */
const labelColorMap = DIAGNOSIS_LABEL_COLOR_MAP;

/** 处理状态选项 */
const statusOptions: { label: string; value: DiagnosisApi.ActionStatus }[] = [
  { label: '待处理', value: 'PENDING' },
  { label: '处理中', value: 'IN_PROGRESS' },
  { label: '已实施', value: 'IMPLEMENTED' },
  { label: '已忽略', value: 'IGNORED' },
];

/** 处理状态颜色映射（已迁移至 useIndustrialStatus，保留 statusOptions 用于下拉） */
// const statusColorMap 已废弃，改用 useIndustrialStatus().getStatusMeta(status)

/** 时间窗选项（对齐后端 _build_time_window_condition 支持的值） */
const timeWindowOptions: { label: string; value: DiagnosisApi.TimeWindow }[] = [
  { label: '近 24 小时', value: 'last_24_hours' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

const columns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 150 },
  {
    title: '装置',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 180,
    ellipsis: true,
  },
  {
    title: '诊断标签',
    dataIndex: 'diagnosisLabel',
    key: 'diagnosisLabel',
    width: 130,
  },
  {
    title: '综合评分',
    dataIndex: 'compositeScore',
    key: 'compositeScore',
    width: 100,
    align: 'right',
  },
  {
    title: '置信度',
    dataIndex: 'confidence',
    key: 'confidence',
    width: 100,
    align: 'right',
  },
  {
    title: '处理状态',
    dataIndex: 'actionStatus',
    key: 'actionStatus',
    width: 110,
  },
  {
    title: '创建时间',
    dataIndex: 'createdAt',
    key: 'createdAt',
    width: 170,
  },
  {
    title: '更新时间',
    dataIndex: 'updatedAt',
    key: 'updatedAt',
    width: 170,
  },
  { title: '操作', key: 'action', width: 260, fixed: 'right' },
];

/** KpiStrip 摘要指标：各状态计数（后端聚合口径，不受分页影响） */
const kpiStripItems = computed<KpiStripItem[]>(() => {
  const counts = aggregates.value?.statusCounts ?? {};
  return [
    {
      key: 'pending',
      label: '待处理',
      value: counts.PENDING ?? 0,
      unit: '条',
      status: 'warning',
    },
    {
      key: 'in_progress',
      label: '处理中',
      value: counts.IN_PROGRESS ?? 0,
      unit: '条',
      status: 'primary',
    },
    {
      key: 'implemented',
      label: '已实施',
      value: counts.IMPLEMENTED ?? 0,
      unit: '条',
      status: 'success',
    },
    {
      key: 'ignored',
      label: '已忽略',
      value: counts.IGNORED ?? 0,
      unit: '条',
      status: 'neutral',
    },
  ];
});

// 状态更新 Modal
const statusModalVisible = ref(false);
const statusModalLoading = ref(false);
const editingItem = ref<DiagnosisApi.TrackerItem | null>(null);
const statusForm = reactive({
  status: 'PENDING' as DiagnosisApi.ActionStatus,
  comment: '',
  changeRemark: '',
  // D3: MOC 变更管理关联（仅 IMPLEMENTED 时必填）
  mocRef: '',
  mocNotApplicable: false,
  mocReason: '',
});

/** D3: 当前是否需要展示 MOC 字段（仅 IMPLEMENTED 状态） */
const showMocFields = computed(() => statusForm.status === 'IMPLEMENTED');

// A/B 对比抽屉
const abCompareVisible = ref(false);
const abCompareLoopId = ref('');
const abCompareImplementedAt = ref('');

/** 正在导出 PDF 的回路 ID（空串表示无导出中任务，防重复点击） */
const exportingLoopId = ref('');

/** 工具栏 CSV 统计导出 loading（防重复点击） */
const exportingCsv = ref(false);

/** 加载列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getTrackerListApi({
      diagnosisLabel: query.diagnosisLabel,
      actionStatus: query.actionStatus,
      loopId: query.loopId,
      timeWindow: query.timeWindow,
      page: query.page,
      pageSize: query.pageSize,
    });
    trackerList.value = data.items || [];
    total.value = data.total || 0;
    aggregates.value = data.aggregates ?? null;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function handleSearch() {
  query.page = 1;
  loadList();
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 20;
  loadList();
}

/** 提交状态更新（含变更说明审计字段 + D3 MOC 校验） */
async function handleSubmitStatus() {
  if (!editingItem.value) return;
  if (!statusForm.changeRemark.trim()) {
    message.warning('请填写变更说明');
    return;
  }
  // D3: IMPLEMENTED 状态校验 MOC 变更管理关联
  if (statusForm.status === 'IMPLEMENTED') {
    if (statusForm.mocNotApplicable) {
      if (!statusForm.mocReason.trim()) {
        message.warning('勾选 MOC 不适用时，必须填写依据说明');
        return;
      }
    } else if (!statusForm.mocRef.trim()) {
      message.warning(
        '标记已实施时必须填写 MOC 变更管理关联编号，或勾选"不适用"并填写依据说明',
      );
      return;
    }
  }
  statusModalLoading.value = true;
  try {
    await updateTrackerStatusApi(editingItem.value.loopId, {
      status: statusForm.status,
      comment: statusForm.comment,
      changeRemark: statusForm.changeRemark,
      // D3: 仅 IMPLEMENTED 时传递 MOC 字段，其他状态不传避免覆盖已有值
      ...(statusForm.status === 'IMPLEMENTED'
        ? {
            mocRef: statusForm.mocRef.trim() || undefined,
            mocNotApplicable: statusForm.mocNotApplicable || undefined,
            mocReason: statusForm.mocReason.trim() || undefined,
          }
        : {}),
    });
    message.success('状态更新成功');
    statusModalVisible.value = false;
    await loadList();
    // F7：状态置为"已实施"后自动弹出 A/B 对比 Drawer（FDS §5.4.4）
    if (statusForm.status === 'IMPLEMENTED' && editingItem.value) {
      handleOpenAbCompare({
        ...editingItem.value,
        actionStatus: 'IMPLEMENTED',
        updatedAt: new Date().toISOString(),
      });
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    statusModalLoading.value = false;
  }
}

/** 打开状态更新 Modal（直接打开，Modal 内含状态 Select，无需 Dropdown 快捷选择） */
function handleOpenStatusModal(record: DiagnosisApi.TrackerItem) {
  editingItem.value = record;
  statusForm.status = record.actionStatus as DiagnosisApi.ActionStatus;
  statusForm.comment = record.comment || '';
  statusForm.changeRemark = '';
  // D3: 预填已有 MOC 信息（已实施记录可查看历史值），新操作默认清空
  statusForm.mocRef = record.mocRef || '';
  statusForm.mocNotApplicable = record.mocNotApplicable || false;
  statusForm.mocReason = record.mocReason || '';
  statusModalVisible.value = true;
}

/** 生成导出 PDF 文件名：CLPM-诊断建议书-[位号]-[日期].pdf */
function buildExportFileName(tagName: string): string {
  const d = new Date();
  const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  return `CLPM-诊断建议书-${tagName}-${date}.pdf`;
}

/** 导出 PDF（FDS §5.4.4：后端同步生成，前端 Blob 直接下载） */
async function handleExportPdf(record: DiagnosisApi.TrackerItem) {
  // 同一行正在导出时，避免重复提交
  if (exportingLoopId.value === record.loopId) {
    return;
  }
  exportingLoopId.value = record.loopId;
  try {
    const blob = await exportDiagnosisPdfApi(record.loopId);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = buildExportFileName(record.tagName);
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    message.success('诊断建议书已导出');
  } catch {
    // 错误已由拦截器处理
  } finally {
    exportingLoopId.value = '';
  }
}

/**
 * 将 timeWindow 枚举转为 [startDate, endDate] ISO 字符串（对齐后端 _build_time_window_condition 口径）。
 * last_24_hours → 24h / last_7_days → 7d / last_30_days → 30d
 */
function timeWindowToRange(timeWindow: DiagnosisApi.TimeWindow): {
  endDate: string;
  startDate: string;
} {
  const now = dayjs();
  const map: Record<DiagnosisApi.TimeWindow, number> = {
    last_24_hours: 24,
    last_7_days: 7 * 24,
    last_30_days: 30 * 24,
  };
  const hours = map[timeWindow] ?? 24 * 7;
  return {
    startDate: now.subtract(hours, 'hour').toISOString(),
    endDate: now.toISOString(),
  };
}

/** 生成 CSV 导出文件名：CLPM-异常跟踪统计_[start]_[end].csv */
function buildCsvFileName(startDate: string, endDate: string): string {
  const s = startDate.slice(0, 10);
  const e = endDate.slice(0, 10);
  return `CLPM-异常跟踪统计_${s}_${e}.csv`;
}

/**
 * 工具栏 CSV 统计导出（SVC-13：GET /diagnosis/statistics/export）。
 *
 * 按当前 timeWindow 推导时间范围，导出诊断标签统计 CSV（UTF-8 with BOM）。
 * 与行级"导出 PDF"按钮互补：CSV 适合整体统计汇报，PDF 适合单回路建议书。
 */
async function handleExportCsv() {
  if (exportingCsv.value) return;
  exportingCsv.value = true;
  const { startDate, endDate } = timeWindowToRange(query.timeWindow);
  const params = { startDate, endDate };
  console.warn('[tracker.handleExportCsv] 请求导出', {
    timeWindow: query.timeWindow,
    ...params,
  });
  try {
    const blob = await exportDiagnosisStatisticsApi(params);
    console.warn('[tracker.handleExportCsv] 响应成功', {
      size: blob.size,
      type: blob.type,
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = buildCsvFileName(startDate, endDate);
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    message.success('诊断统计 CSV 已导出');
  } catch (error) {
    console.error('[tracker.handleExportCsv] 导出失败', { params, error });
    // 错误已由拦截器处理
  } finally {
    exportingCsv.value = false;
  }
}

/** 打开 A/B 对比 */
function handleOpenAbCompare(record: DiagnosisApi.TrackerItem) {
  abCompareLoopId.value = record.loopId;
  // FDS §5.4.4：已实施状态时传递实施时间点，自动截取前后窗口
  abCompareImplementedAt.value =
    record.actionStatus === 'IMPLEMENTED' && record.updatedAt
      ? record.updatedAt
      : '';
  abCompareVisible.value = true;
}

/** 跳转诊断详情 */
function handleViewDetail(loopId: string) {
  router.push(`/diagnosis/detail/${loopId}`);
}

/** 工具栏：刷新 */
function handleRefresh() {
  loadList();
}

// P2 #37 UX13: 批量处理功能开发中，按钮保持 disabled + tooltip；
// 导出按钮已接通 SVC-13 CSV 统计导出（D5）

function formatTime(t: string): string {
  if (!t) return '—';
  try {
    // 强制北京时间（UTC+8）
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return t;
  }
}

function labelName(label: DiagnosisLabel): string {
  return getDiagnosisLabelName(label);
}

function statusName(status: DiagnosisApi.ActionStatus): string {
  return statusOptions.find((o) => o.value === status)?.label || status;
}

onMounted(() => {
  // F13：独立页模式下从路由 query 读取 loopId 预选回路
  if (!props.drawerMode && route.query.loopId) {
    query.loopId = String(route.query.loopId);
  }
  loadList();
});
</script>

<template>
  <!--
    抽屉模式 / 独立页模式统一容器：
    - 抽屉模式（drawerMode=true）：从诊断列表页/详情页右侧滑出，用 Drawer
    - 独立页模式（drawerMode=false）：直接路由访问 /diagnosis/tracker，用 Page
    通过 containerComponent/containerProps/containerListeners 消除两模式约 500 行模板重复
  -->
  <component
    :is="containerComponent"
    v-bind="containerProps"
    v-on="containerListeners"
  >
    <ClpmPageToolbar
      :compact="drawerMode"
      :title="drawerMode ? undefined : '异常跟踪'"
      :subtitle="
        drawerMode
          ? '状态、标签、时间窗统一筛选'
          : '状态、标签、时间窗统一筛选，跟踪异常处置闭环'
      "
    >
      <Select
        v-model:value="query.diagnosisLabel"
        placeholder="诊断标签"
        style="width: 160px"
        allow-clear
        :options="labelOptions"
        @change="handleSearch"
      />
      <Select
        v-model:value="query.actionStatus"
        placeholder="处理状态"
        style="width: 140px"
        allow-clear
        :options="statusOptions"
        @change="handleSearch"
      />
      <Select
        v-model:value="query.timeWindow"
        style="width: 140px"
        :options="timeWindowOptions"
        @change="handleSearch"
      />
      <template #actions>
        <ClpmToolbarButton
          icon="refresh"
          label="刷新"
          :loading="loading"
          @click="handleRefresh"
        />
        <ClpmToolbarButton
          icon="export"
          label="导出"
          :loading="exportingCsv"
          @click="handleExportCsv"
        />
        <ClpmToolbarButton
          icon="ant-design:thunderbolt-outlined"
          label="批量处理"
          variant="primary"
          disabled
          disabled-reason="批量处理功能开发中，待后端接口支持"
        />
      </template>
    </ClpmPageToolbar>

    <!-- KpiStrip：各状态计数 -->
    <ClpmKpiStrip
      :class="drawerMode ? 'mt-3' : 'mt-4'"
      :items="kpiStripItems"
      :loading="loading"
    />

    <!-- 状态机可视化：待处理 → 处理中 → 已实施 / 已忽略 -->
    <div class="status-flow-bar mt-3">
      <span class="status-flow-bar__label">状态流转</span>
      <Tag color="default">待处理</Tag>
      <span class="status-flow-bar__arrow">→</span>
      <Tag color="processing">处理中</Tag>
      <span class="status-flow-bar__arrow">→</span>
      <Tag color="success">已实施</Tag>
      <span class="status-flow-bar__alt">/</span>
      <Tag color="warning">已忽略</Tag>
    </div>

    <ClpmDataCanvas class="mt-3" title="异常跟踪列表" :loading="loading">
      <Table
        :columns="columns"
        :data-source="trackerList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: DiagnosisApi.TrackerItem) => record.loopId"
        :scroll="{ x: 1400 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'diagnosisLabel'">
            <Tag
              :color="labelColorMap[record.diagnosisLabel as DiagnosisLabel]"
            >
              {{ record.labelName || labelName(record.diagnosisLabel) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'compositeScore'">
            <span
              class="clpm-num font-medium"
              :style="{ color: themeColors.INFO }"
            >
              {{ Number(record.compositeScore).toFixed(2) }}
            </span>
          </template>
          <template v-else-if="column.key === 'confidence'">
            <span class="clpm-num">{{
              Number(record.confidence).toFixed(2)
            }}</span>
          </template>
          <template v-else-if="column.key === 'actionStatus'">
            <Tag
              :color="getStatusMeta(record.actionStatus as string).color"
              :style="{
                background: getStatusMeta(record.actionStatus as string)
                  .bgColor,
                borderColor: getStatusMeta(record.actionStatus as string)
                  .borderColor,
              }"
            >
              {{ statusName(record.actionStatus as DiagnosisApi.ActionStatus) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'createdAt'">
            {{ formatTime(record.createdAt) }}
          </template>
          <template v-else-if="column.key === 'updatedAt'">
            {{ formatTime(record.updatedAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <div class="flex flex-col gap-1">
              <div class="flex gap-1">
                <Button
                  type="link"
                  size="small"
                  @click="handleViewDetail(record.loopId)"
                >
                  详情
                </Button>
                <Button
                  v-if="canEditStatus"
                  type="link"
                  size="small"
                  @click="
                    handleOpenStatusModal(record as DiagnosisApi.TrackerItem)
                  "
                >
                  更新状态
                </Button>
                <Button
                  v-if="canViewAbCompare"
                  type="link"
                  size="small"
                  @click="
                    handleOpenAbCompare(record as DiagnosisApi.TrackerItem)
                  "
                >
                  A/B 对比
                </Button>
                <Button
                  v-if="canExportPdf"
                  type="link"
                  size="small"
                  :loading="exportingLoopId === record.loopId"
                  :disabled="
                    exportingLoopId !== '' && exportingLoopId !== record.loopId
                  "
                  @click="handleExportPdf(record as DiagnosisApi.TrackerItem)"
                >
                  导出 PDF
                </Button>
              </div>
            </div>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 状态更新 Modal（含变更说明审计字段 + D3 MOC 变更管理关联） -->
    <Modal
      v-model:open="statusModalVisible"
      title="更新处理状态"
      :confirm-loading="statusModalLoading"
      width="560px"
      @ok="handleSubmitStatus"
    >
      <Form :model="statusForm" layout="vertical" class="pt-4">
        <FormItem label="回路位号">
          <span class="font-medium">{{ editingItem?.tagName }}</span>
        </FormItem>
        <FormItem label="处理状态" required>
          <Select v-model:value="statusForm.status" :options="statusOptions" />
        </FormItem>
        <FormItem label="变更说明" required>
          <Input.TextArea
            v-model:value="statusForm.changeRemark"
            placeholder="请说明本次状态变更的原因或依据，例如：经现场确认阀门存在粘滞，已安排检修"
            :rows="3"
            :maxlength="500"
            show-count
          />
        </FormItem>
        <FormItem label="处理备注">
          <Input.TextArea
            v-model:value="statusForm.comment"
            placeholder="例如：已联系设备部拆阀检查"
            :rows="3"
          />
        </FormItem>

        <!-- D3: MOC 变更管理关联（仅 IMPLEMENTED 状态展示，危化企业变更管理合规要求） -->
        <template v-if="showMocFields">
          <div class="moc-section-title">
            MOC 变更管理关联
            <span class="moc-section-hint">
              （危化企业变更管理合规要求，标记"已实施"时必填）
            </span>
          </div>
          <FormItem
            v-if="!statusForm.mocNotApplicable"
            label="MOC 关联编号"
            required
          >
            <Input
              v-model:value="statusForm.mocRef"
              placeholder="请填写变更管理工单编号，例如：MOC-2026-001"
              :maxlength="255"
              allow-clear
            />
          </FormItem>
          <FormItem>
            <Checkbox v-model:checked="statusForm.mocNotApplicable">
              此变更不涉及 MOC（如仅参数微调、无需变更管理审批）
            </Checkbox>
          </FormItem>
          <FormItem
            v-if="statusForm.mocNotApplicable"
            label="不适用依据说明"
            required
          >
            <Input.TextArea
              v-model:value="statusForm.mocReason"
              placeholder="请说明为何此变更不涉及 MOC 变更管理，例如：仅 PID 参数微调，未改变控制方案与联锁逻辑"
              :rows="3"
              :maxlength="500"
              show-count
            />
          </FormItem>
        </template>
      </Form>
    </Modal>

    <!-- A/B 对比抽屉 -->
    <AbCompare
      v-if="abCompareVisible"
      :loop-id="abCompareLoopId"
      :implemented-at="abCompareImplementedAt"
      :drawer-mode="true"
      @close="abCompareVisible = false"
    />
  </component>
</template>

<style scoped>
/* 状态机可视化条 */
.status-flow-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 10px 12px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.status-flow-bar__label {
  margin-right: 4px;
  font-size: 13px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.status-flow-bar__arrow {
  font-size: 14px;
  font-weight: 700;
  color: hsl(var(--muted-foreground));
}

.status-flow-bar__alt {
  margin: 0 2px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

/* D3: MOC 变更管理关联区块标题 */
.moc-section-title {
  padding-top: 12px;
  margin: 12px 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: hsl(var(--foreground));
  border-top: 1px solid hsl(var(--border));
}

.moc-section-hint {
  font-size: 12px;
  font-weight: 400;
  color: hsl(var(--muted-foreground));
}
</style>
