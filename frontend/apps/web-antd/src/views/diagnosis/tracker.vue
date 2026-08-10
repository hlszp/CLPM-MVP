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
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import {
  computed,
  defineAsyncComponent,
  onMounted,
  reactive,
  ref,
  watch,
} from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import {
  Button,
  Checkbox,
  DatePicker,
  Drawer,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  exportDiagnosisPdfApi,
  exportDiagnosisStatisticsApi,
  getTrackerListApi,
  updateTrackerStatusApi,
} from '#/api/diagnosis';
import {
  ClpmConfidenceBadge,
  ClpmDataCanvas,
  ClpmEmptyState,
  ClpmInfoTip,
  ClpmKpiStrip,
  ClpmLoopLink,
  ClpmPageToolbar,
  ClpmSeverityBadge,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useIndustrialStatus } from '#/composables/use-industrial-status';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import {
  DIAGNOSIS_TERM_EXPLANATIONS,
  SEVERITY_LABEL,
} from '#/constants/clpm-ui';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_OPTIONS,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'DiagnosisTrackerContent' });

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

const AbCompare = defineAsyncComponent(() => import('./ab-compare.vue'));

const router = useRouter();
const route = useRoute();
const userStore = useUserStore();

const { getStatusMeta } = useIndustrialStatus();
const { themeColors } = useClpmTheme();

/**
 * 角色判断（v-if 模式：Dropdown/状态流转等承载内部状态的交互组件不用 v-permission，
 * 避免历史 el.remove() 破坏组件内部状态的问题；v-permission 已修 Comment 占位版，
 * 但本页按钮均为响应式角色判断，v-if 语义更直观）
 *
 * 与后端 require_roles 对齐（deps.py 无 ADMIN 通配，严格按角色枚举）：
 * - 更新状态：PATCH /tracker/{loop_id}/status → require_roles("IC_ENGINEER")
 * - 导出 PDF：POST /tracker/{loop_id}/export → require_roles("IC_ENGINEER","ADMIN","PE_ENGINEER")
 */
const userRoles = computed(() => userStore.userInfo?.roles ?? []);
const canEditStatus = computed(() => userRoles.value.includes('IC_ENGINEER'));
const canViewAbCompare = computed(() =>
  userRoles.value.some((r) => ['ADMIN', 'EXPERT', 'IC_ENGINEER'].includes(r)),
);
const canExportPdf = computed(() =>
  userRoles.value.some((r) =>
    ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'].includes(r),
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
  severity: undefined as 'CRITICAL' | 'ERROR' | 'INFO' | 'WARN' | undefined,
  loopId: props.loopId || undefined,
  timeWindow: 'last_7_days' as DiagnosisApi.TimeWindow,
  page: 1,
  pageSize: 20,
});

/** 8 类诊断标签选项 */
const labelOptions = DIAGNOSIS_LABEL_OPTIONS;

/** 标签颜色映射 */
const labelColorMap = DIAGNOSIS_LABEL_COLOR_MAP;

/** 严重度筛选选项 */
const severityOptions: {
  label: string;
  value: 'CRITICAL' | 'ERROR' | 'INFO' | 'WARN';
}[] = [
  { label: SEVERITY_LABEL.CRITICAL, value: 'CRITICAL' },
  { label: SEVERITY_LABEL.ERROR, value: 'ERROR' },
  { label: SEVERITY_LABEL.WARN, value: 'WARN' },
  { label: SEVERITY_LABEL.INFO, value: 'INFO' },
];

/** 处理状态选项（C1-3：补齐 P1a 闭环状态机全态） */
const statusOptions: { label: string; value: DiagnosisApi.ActionStatus }[] = [
  { label: '待处理', value: 'PENDING' },
  { label: '处理中', value: 'IN_PROGRESS' },
  { label: '验证中', value: 'VERIFYING' },
  { label: '已闭环', value: 'CLOSED' },
  { label: '重开', value: 'REOPENED' },
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
  {
    title: '严重度',
    dataIndex: 'severity',
    key: 'severity',
    width: 80,
    align: 'center',
  },
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 180 },
  {
    title: '装置',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 160,
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
    width: 90,
    align: 'right',
  },
  {
    title: '可信度',
    dataIndex: 'confidence',
    key: 'confidence',
    width: 80,
    align: 'center',
  },
  {
    title: '处理状态',
    dataIndex: 'actionStatus',
    key: 'actionStatus',
    width: 100,
  },
  {
    title: '负责人',
    dataIndex: 'assignee',
    key: 'assignee',
    width: 90,
    ellipsis: true,
  },
  {
    title: '计划执行',
    dataIndex: 'plannedAt',
    key: 'plannedAt',
    width: 150,
  },
  {
    title: '创建时间',
    dataIndex: 'createdAt',
    key: 'createdAt',
    width: 150,
  },
  {
    title: '更新时间',
    dataIndex: 'updatedAt',
    key: 'updatedAt',
    width: 150,
  },
  { title: '操作', key: 'action', width: 220, fixed: 'right' },
];

// ===== P2-04：表格列配置（显示/隐藏 + 排序，localStorage 持久化）=====
const { preferences: columnPrefs, updateColumns: persistColumns } =
  usePagePreference('diagnosis-tracker');

// ===== A-07：表格密度三档（紧凑/标准/宽松，持久化）=====
const { tableSize, densityLabel, cycleDensity } =
  useTableDensity('diagnosis-tracker');

/** 获取列 key（兼容 dataIndex 和 key） */
function getColumnKey(col: TableColumnsType[number]): string {
  const c = col as any;
  if (c.key) return String(c.key);
  if (c.dataIndex) {
    return Array.isArray(c.dataIndex)
      ? String(c.dataIndex[0])
      : String(c.dataIndex);
  }
  return '';
}

/** 默认列配置 */
function buildDefaultColumnConfigs(): ColumnConfig[] {
  return columns.map((c, i) => ({
    key: getColumnKey(c),
    label: String(c.title ?? ''),
    visible: true,
    order: i,
  }));
}

const columnConfigs = ref<ColumnConfig[]>(
  columnPrefs.value.columns && columnPrefs.value.columns.length > 0
    ? columnPrefs.value.columns
    : buildDefaultColumnConfigs(),
);

const visibleColumns = computed<TableColumnsType>(() => {
  const configMap = new Map(
    columnConfigs.value.map((c, i) => [
      c.key,
      { visible: c.visible, order: i },
    ]),
  );
  return columns
    .filter((c) => {
      const cfg = configMap.get(getColumnKey(c));
      return cfg ? cfg.visible : true;
    })
    .toSorted((a, b) => {
      const aOrder = configMap.get(getColumnKey(a))?.order ?? 99;
      const bOrder = configMap.get(getColumnKey(b))?.order ?? 99;
      return aOrder - bOrder;
    });
});

function handleUpdateColumns(cols: ColumnConfig[]) {
  columnConfigs.value = cols;
  persistColumns(cols);
}

function handleResetColumns() {
  columnConfigs.value = buildDefaultColumnConfigs();
  persistColumns(columnConfigs.value);
}

/** KpiStrip 摘要指标：各状态计数（后端聚合口径，不受分页影响）
 * 整改 A-03：零值中性——计数为 0 时不着色 */
const kpiStripItems = computed<KpiStripItem[]>(() => {
  const counts = aggregates.value?.statusCounts ?? {};
  const pending = counts.PENDING ?? 0;
  const inProgress = counts.IN_PROGRESS ?? 0;
  // C1-3：验证中卡 = VERIFYING + 存量 IMPLEMENTED（后端兼容映射口径）
  const verifying = (counts.VERIFYING ?? 0) + (counts.IMPLEMENTED ?? 0);
  return [
    {
      key: 'pending',
      label: '待处理',
      value: pending,
      unit: '条',
      status: pending > 0 ? 'warning' : 'neutral',
    },
    {
      key: 'in_progress',
      label: '处理中',
      value: inProgress,
      unit: '条',
      status: inProgress > 0 ? 'primary' : 'neutral',
    },
    {
      key: 'verifying',
      label: '验证中',
      value: verifying,
      unit: '条',
      status: verifying > 0 ? 'primary' : 'neutral',
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
  // V62-P3-008：负责人与计划执行时间（tracker 闭环字段）
  assignee: '',
  plannedAt: '',
});

/** D3: 当前是否需要展示 MOC 字段（仅 IMPLEMENTED 状态） */
const showMocFields = computed(() => statusForm.status === 'IMPLEMENTED');

/** V62-P3-008：计划执行时间 DatePicker 双向绑定（dayjs ↔ ISO 字符串） */
const plannedAtDate = computed<dayjs.Dayjs | undefined>({
  get: () => (statusForm.plannedAt ? dayjs(statusForm.plannedAt) : undefined),
  set: (v) => {
    statusForm.plannedAt = v ? v.toISOString() : '';
  },
});

// A/B 对比抽屉
const abCompareVisible = ref(false);
const abCompareLoopId = ref('');
const abCompareImplementedAt = ref('');

/** 正在导出 PDF 的回路 ID（空串表示无导出中任务，防重复点击） */
const exportingLoopId = ref('');

/**
 * P3-29：PDF 导出本地伪进度百分比（0-100）
 *
 * 现状：后端 API 仍是同步生成 `POST /tracker/{loopId}/export`（无异步 taskId 无进度接口）
 * 方案：前端在同步请求期间按固定节拍推进进度至 92% cap（避免显示为挂死），
 *       Blob 到达时瞬间跳到 100%。后端 P3-33 提供异步任务+进度 API 后，
 *       只需把 handleExportPdf 的进度源从 setInterval 换成 usePolling(taskId) 即可。
 */
const pdfExportPercent = ref(0);
let pdfExportTicker: null | ReturnType<typeof setInterval> = null;
const PDF_EXPORT_TICK_MS = 500;
const PDF_EXPORT_TICK_STEP = 8;
const PDF_EXPORT_CAP_PERCENT = 92;

/** 启动本地伪进度 ticker */
function startPdfProgressTicker() {
  if (pdfExportTicker) return;
  pdfExportPercent.value = 0;
  pdfExportTicker = setInterval(() => {
    if (pdfExportPercent.value < PDF_EXPORT_CAP_PERCENT) {
      pdfExportPercent.value = Math.min(
        PDF_EXPORT_CAP_PERCENT,
        pdfExportPercent.value + PDF_EXPORT_TICK_STEP,
      );
    }
  }, PDF_EXPORT_TICK_MS);
}

/** 停止 ticker 并强制跳到指定百分比 */
function stopPdfProgressTicker(finalPercent = 100) {
  if (pdfExportTicker) {
    clearInterval(pdfExportTicker);
    pdfExportTicker = null;
  }
  pdfExportPercent.value = Math.max(0, Math.min(100, finalPercent));
  // 100ms 后归 0 以便下次导出显示为 0 起步
  setTimeout(() => {
    if (pdfExportPercent.value >= 100) {
      pdfExportPercent.value = 0;
    }
  }, 1000);
}

/** 工具栏 CSV 统计导出 loading（防重复点击） */
const exportingCsv = ref(false);

/** 加载列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getTrackerListApi({
      diagnosisLabel: query.diagnosisLabel,
      actionStatus: query.actionStatus,
      // severity: query.severity, // TODO: 后端支持后启用
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
      // V62-P3-008：负责人与计划执行时间（可选，空值传 undefined 不覆盖）
      assignee: statusForm.assignee.trim() || undefined,
      plannedAt: statusForm.plannedAt || undefined,
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
  // V62-P3-008：回填负责人与计划执行时间
  statusForm.assignee = record.assignee || '';
  statusForm.plannedAt = record.plannedAt || '';
  statusModalVisible.value = true;
}

/** 生成导出 PDF 文件名：CLPM-诊断建议书-[位号]-[日期].pdf */
function buildExportFileName(tagName: string): string {
  const d = new Date();
  const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  return `CLPM-诊断建议书-${tagName}-${date}.pdf`;
}

/** 导出 PDF（P3-29：同步模式下增加本地伪进度反馈；后端 P3-33 异步 API 上线后将切到真实进度轮询） */
async function handleExportPdf(record: DiagnosisApi.TrackerItem) {
  if (exportingLoopId.value === record.loopId) {
    return;
  }
  exportingLoopId.value = record.loopId;
  const startedAt = Date.now();
  startPdfProgressTicker();
  try {
    const blob = await exportDiagnosisPdfApi(record.loopId);
    // 后端返回 Blob → 本地跳 100%，然后 1s 后自动归零
    stopPdfProgressTicker(100);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = buildExportFileName(record.tagName);
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1);
    message.success(
      `诊断建议书已导出（耗时 ${elapsed}s）。后续版本将升级为异步任务模式，进度可追踪可取消。`,
    );
  } catch {
    // 错误已由拦截器处理，UI 上归零
    stopPdfProgressTicker(0);
  } finally {
    // 兜底：1.5s 后确保进度条隐藏
    setTimeout(() => {
      if (pdfExportTicker) {
        stopPdfProgressTicker(0);
      }
    }, 1500);
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
  try {
    const blob = await exportDiagnosisStatisticsApi(params);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = buildCsvFileName(startDate, endDate);
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    message.success('诊断统计 CSV 已导出');
  } catch {
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

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '异常跟踪 帮助',
    content:
      '跟踪诊断异常的处置闭环：待处理 → 处理中 → 已实施 / 已忽略。支持按严重度、诊断标签、处理状态、时间窗筛选；行级操作含详情、更新状态（含 MOC 变更管理关联）、A/B 对比、导出 PDF。CSV 统计导出按当前时间窗生成整体统计。',
  });
}

// ===== 统一工具栏（标准 4 工具：刷新 / 导出 / 列设置 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  export: {
    onClick: handleExportCsv,
    loading: exportingCsv.value,
  },
  setting: {},
  help: { onClick: handleHelp },
}));

// P2 #37 UX13: 批量处理功能开发中，按钮保持 disabled + tooltip；
// 导出按钮已接通 SVC-13 CSV 统计导出（D5）

function labelName(label: DiagnosisLabel): string {
  return getDiagnosisLabelName(label);
}

function statusName(status: DiagnosisApi.ActionStatus): string {
  return statusOptions.find((o) => o.value === status)?.label || status;
}

/**
 * 状态标签 Tag 属性：一次调用 getStatusMeta 并返回仅含 color/style 的对象，
 * 避免模板内对同一 status 重复调用 3 次 getStatusMeta（P1-1d）。
 */
function statusTagAttrs(status: DiagnosisApi.ActionStatus): {
  color: string;
  style: { background: string; borderColor: string };
} {
  const meta = getStatusMeta(status);
  return {
    color: meta.color,
    style: {
      background: meta.bgColor,
      borderColor: meta.borderColor,
    },
  };
}

onMounted(() => {
  // F13：独立页模式下从路由 query 读取 loopId 预选回路
  if (!props.drawerMode && route.query.loopId) {
    query.loopId = String(route.query.loopId);
  }
  // C1-3：从路由 query 读取 status 预选状态（工作台"验证超期"卡 ?status=VERIFYING 直达）
  if (!props.drawerMode && route.query.status) {
    const s = String(route.query.status) as DiagnosisApi.ActionStatus;
    if (statusOptions.some((o) => o.value === s)) query.actionStatus = s;
  }
  loadList();
});

/**
 * P1-5a：监听 route.query.loopId 变化（独立页模式）。
 * onMounted 中读取 route.query.loopId 是一次性的，路由参数变化时需重新加载列表。
 */
watch(
  () => route.query.loopId,
  (newId) => {
    if (!props.drawerMode && newId) {
      query.loopId = String(newId);
      loadList();
    }
  },
);
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
        v-model:value="query.severity"
        placeholder="严重度"
        style="width: 120px"
        allow-clear
        :options="severityOptions"
        @change="handleSearch"
      />
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
          icon="ant-design:thunderbolt-outlined"
          label="批量处理"
          variant="primary"
          disabled
          disabled-reason="批量处理功能开发中，待后端接口支持"
        />
        <ClpmStandardActions
          :items="toolbarItems"
          :column-configs="columnConfigs"
          @update:columns="handleUpdateColumns"
          @reset-columns="handleResetColumns"
        />
        <!-- A-07：密度三档切换（紧凑/标准/宽松，点击循环）；仅独立页模式显示 -->
        <ClpmToolbarButton
          v-if="!drawerMode"
          icon="ant-design:column-height-outlined"
          :label="`密度：${densityLabel}`"
          :tooltip="`密度：${densityLabel}（点击切换）`"
          @click="cycleDensity"
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
        :columns="visibleColumns"
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
        :scroll="{ x: 1600 }"
        :size="tableSize"
        @change="handleTableChange"
      >
        <template #emptyText>
          <ClpmEmptyState
            scene="tracker"
            title="暂无待处理异常"
            description="当前筛选条件下没有需要跟踪处理的诊断异常"
          />
        </template>
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'severity'">
            <ClpmSeverityBadge :severity="record.severity" size="small" />
          </template>
          <template v-else-if="column.key === 'tagName'">
            <ClpmLoopLink
              :loop-id="record.loopId"
              :tag-name="record.tagName"
              :unit-name="record.unitName"
              :show-tracker="true"
              default-target="diagnosis"
            />
          </template>
          <template v-else-if="column.key === 'diagnosisLabel'">
            <Space :size="4">
              <Tag
                :color="labelColorMap[record.diagnosisLabel as DiagnosisLabel]"
              >
                {{ record.labelName || labelName(record.diagnosisLabel) }}
              </Tag>
              <ClpmInfoTip
                v-if="
                  (
                    DIAGNOSIS_TERM_EXPLANATIONS as Record<
                      string,
                      { term: string; short: string; detail?: string }
                    >
                  )[record.diagnosisLabel]
                "
                :term="
                  (
                    DIAGNOSIS_TERM_EXPLANATIONS as Record<
                      string,
                      { term: string; short: string; detail?: string }
                    >
                  )[record.diagnosisLabel]!.term
                "
                :tip="
                  (
                    DIAGNOSIS_TERM_EXPLANATIONS as Record<
                      string,
                      { term: string; short: string; detail?: string }
                    >
                  )[record.diagnosisLabel]!.short
                "
                :detail="
                  (
                    DIAGNOSIS_TERM_EXPLANATIONS as Record<
                      string,
                      { term: string; short: string; detail?: string }
                    >
                  )[record.diagnosisLabel]!.detail
                "
              />
            </Space>
          </template>
          <template v-else-if="column.key === 'compositeScore'">
            <span
              class="clpm-num font-medium"
              :style="{ color: themeColors.INFO }"
            >
              {{
                record.compositeScore != null
                  ? Number(record.compositeScore).toFixed(0)
                  : '—'
              }}
            </span>
          </template>
          <template v-else-if="column.key === 'confidence'">
            <ClpmConfidenceBadge
              :confidence="record.confidence"
              :level="null"
            />
          </template>
          <template v-else-if="column.key === 'actionStatus'">
            <Tag
              v-bind="
                statusTagAttrs(record.actionStatus as DiagnosisApi.ActionStatus)
              "
            >
              {{ statusName(record.actionStatus as DiagnosisApi.ActionStatus) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'plannedAt'">
            {{ formatTime(record.plannedAt) }}
          </template>
          <template v-else-if="column.key === 'createdAt'">
            {{ formatTime(record.createdAt) }}
          </template>
          <template v-else-if="column.key === 'updatedAt'">
            {{ formatTime(record.updatedAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <div class="clpm-row-actions">
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
                @click="handleOpenAbCompare(record as DiagnosisApi.TrackerItem)"
              >
                A/B对比
              </Button>
              <Tooltip
                title="后端同步生成诊断建议书（含诊断证据 / 波形 / 处置建议），内容复杂时约需 5~15s。后续版本将升级为异步任务模式（可取消、实时追踪进度）。"
              >
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
                  导出PDF
                </Button>
              </Tooltip>
              <!-- P3-29：该行导出时在按钮旁显示内联进度条（同步导出的伪进度；切异步后替换为真实进度数据） -->
              <div
                v-if="exportingLoopId === record.loopId"
                class="mt-2"
                style="max-width: 220px"
              >
                <Progress
                  :percent="pdfExportPercent"
                  :show-info="true"
                  size="small"
                  :stroke-color="
                    pdfExportPercent >= 100 ? '#198754' : '#0d6efd'
                  "
                />
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

        <!-- V62-P3-008：负责人与计划执行时间（tracker 闭环字段） -->
        <FormItem label="负责人">
          <Input
            v-model:value="statusForm.assignee"
            placeholder="实施责任人（可选，便于跟踪到人）"
            :maxlength="50"
            allow-clear
          />
        </FormItem>
        <FormItem label="计划执行时间">
          <DatePicker
            v-model:value="plannedAtDate"
            show-time
            format="YYYY-MM-DD HH:mm"
            placeholder="选择计划执行时间（可选）"
            style="width: 100%"
            allow-clear
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
