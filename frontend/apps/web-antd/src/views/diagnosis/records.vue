<script setup lang="ts">
/**
 * 诊断记录 —— 历史列表 + 筛选 + 导出 + 抽屉详情。
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2
 * 16 号文 F1 入口 2：详情抽屉头部"诊断档案"按钮 → 回路诊断档案抽屉。
 * 2026-10-05 用户裁决：列顺序（回路/诊断时间/主分类/次分类/触发方式在前）、
 * 主分类空值显式化（NULL=未见异常）、批量选择 + 批量删除（四角色，
 * 非终态/在办处置建议由后端跳过）；档案空态发起诊断改抽屉内嵌工作台。
 */
import type { Dayjs } from 'dayjs';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, defineAsyncComponent, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  DatePicker,
  Drawer,
  message,
  Modal,
  Select,
  Table,
  Tabs,
} from 'ant-design-vue';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';

dayjs.extend(utc);

import {
  deleteDiagnosisRunsApi,
  exportDiagnosisRunsApi,
  getDiagnosisOperatorsApi,
  getDiagnosisRunDetailApi,
  getDiagnosisRunsApi,
} from '#/api/diagnosis';
import ClpmDataCanvas from '#/components/clpm/data-canvas.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { formatLocalTime } from '#/utils/format';

import DiagnosisResultPanel from './components/diagnosis-result-panel.vue';
// 16 号文 F1 入口 2：详情抽屉头部"诊断档案"按钮 → 回路诊断档案抽屉
import DiagnosisLoopArchiveDrawer from './components/loop-archive-drawer.vue';
import DiagnosisMetricsPanel from './components/metrics-panel.vue';
import {
  CATEGORY_META,
  CATEGORY_OPTIONS,
  REVIEW_STATUS_COLOR,
  REVIEW_STATUS_TEXT,
  RUN_STATUS_TEXT,
  SEVERITY_TEXT,
  TRIGGER_TYPE_COLOR,
  TRIGGER_TYPE_TEXT,
} from './constants';

const { RangePicker } = DatePicker;

const loading = ref(false);
const items = ref<DiagnosisApi.RunListItem[]>([]);
const total = ref(0);
const exporting = ref(false);

const query = reactive({
  page: 1,
  pageSize: 20,
  category: undefined as DiagnosisApi.Category | undefined,
  severity: undefined as DiagnosisApi.Severity | undefined,
  status: undefined as DiagnosisApi.RunStatus | undefined,
  reviewStatus: undefined as DiagnosisApi.ReviewStatus | undefined,
  range: undefined as [Dayjs, Dayjs] | undefined,
  // 回路筛选（仅深链带入，页面无对应控件）
  loopId: undefined as string | undefined,
});

async function load() {
  loading.value = true;
  try {
    const params: DiagnosisApi.RunQuery = {
      page: query.page,
      pageSize: query.pageSize,
      category: query.category,
      severity: query.severity,
      status: query.status,
      reviewStatus: query.reviewStatus,
      loopId: query.loopId,
    };
    if (query.range) {
      // D1 修复（2026-10-01）：后端按 naive UTC 比较 created_at，此前发本地
      // naive 串致本地 00:00–08:00 创建的记录被算入前一天。本地日界转 UTC。
      params.startTime = query.range[0]
        .startOf('day')
        .utc()
        .format('YYYY-MM-DDTHH:mm:ss');
      params.endTime = query.range[1]
        .endOf('day')
        .utc()
        .format('YYYY-MM-DDTHH:mm:ss');
    }
    const res = await getDiagnosisRunsApi(params);
    items.value = res.items;
    total.value = res.total;
  } finally {
    loading.value = false;
  }
}

/** 筛选变更统一入口：回第 1 页再查（此前第 5 页改筛选会请求空页） */
function handleFilterChange() {
  query.page = 1;
  load();
}

function handleTableChange(pag: { current?: number; pageSize?: number }) {
  query.page = pag.current ?? 1;
  query.pageSize = pag.pageSize ?? 20;
  load();
}

// ---- 抽屉详情（2026-10-05 合并双 Tab：诊断结论 + 诊断指标；宽度可拖拽调整） ----
const drawerOpen = ref(false);
const detail = ref<DiagnosisApi.RunDetail | null>(null);
const detailLoading = ref(false);
/** 当前行（详情抽屉 + 档案入口取 loopId/loopTagName） */
const currentRow = ref<DiagnosisApi.RunListItem | null>(null);
/** 抽屉激活 Tab：conclusion=诊断结论 / metrics=诊断指标（操作列"指标"直达） */
const detailTab = ref<'conclusion' | 'metrics'>('conclusion');
/** 指标 Tab 数据源（runId；面板自加载，Tab 首次激活才挂载发请求） */
const metricsRunId = ref('');
/** 抽屉宽度（左缘拖拽手柄调整，范围 520px ~ 94vw，初始 900） */
const detailDrawerWidth = ref(900);

function startDrawerResize(e: MouseEvent) {
  e.preventDefault();
  const startX = e.clientX;
  const startW = detailDrawerWidth.value;
  const maxW = Math.round(window.innerWidth * 0.94);
  const onMove = (ev: MouseEvent) => {
    detailDrawerWidth.value = Math.min(
      maxW,
      Math.max(520, startW + (startX - ev.clientX)),
    );
  };
  const onUp = () => {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    document.body.style.userSelect = '';
  };
  document.body.style.userSelect = 'none';
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

async function openDetail(record: DiagnosisApi.RunListItem) {
  currentRow.value = record;
  // 同步指标 Tab 数据源（行点击进入后手动切 Tab 也能看到当前行指标）
  metricsRunId.value = record.id;
  detailTab.value = 'conclusion';
  drawerOpen.value = true;
  detailLoading.value = true;
  detail.value = null;
  try {
    detail.value = await getDiagnosisRunDetailApi(record.id);
  } finally {
    detailLoading.value = false;
  }
}

// ---- 诊断档案抽屉（16 号文 F1 入口 2：详情抽屉头部按钮） ----
const archiveOpen = ref(false);
const archiveLoopId = ref<null | string>(null);
const archiveLoopTagName = ref<null | string | undefined>(undefined);
const router = useRouter();

function openArchive() {
  const row = currentRow.value;
  if (!row?.loopId) return;
  archiveLoopId.value = row.loopId;
  archiveLoopTagName.value = row.loopTagName ?? undefined;
  archiveOpen.value = true;
}

/**
 * 档案内 run 点击 → 刷新 focus 深链参数并直接打开该次详情
 * （等价跳转 /diagnosis/records?loopId=&focus={runId} 的页内降级）
 */
function onArchiveOpenRun(item: DiagnosisApi.LatestRunItem) {
  archiveOpen.value = false;
  if (item.runId) {
    router.replace({
      query: { ...route.query, loopId: item.loopId, focus: item.runId },
    });
    openDetail({ id: item.runId } as DiagnosisApi.RunListItem);
  }
}

/** 档案空态引导发起诊断 → 抽屉内嵌工作台诊断剖面（2026-10-05 与诊断概览同款动线） */
const Workbench360 = defineAsyncComponent(
  () => import('#/views/loop/workbench360/index.vue'),
);
const diagWorkbenchOpen = ref(false);
const diagWorkbenchLoopId = ref('');

function onArchiveTriggerDiagnosis(loopId: string) {
  archiveOpen.value = false;
  drawerOpen.value = false;
  diagWorkbenchLoopId.value = loopId;
  diagWorkbenchOpen.value = true;
}

// ---- 导出 ----
async function handleExport() {
  // D2 修复（2026-10-01）：透传时间范围/回路筛选（reviewStatus 后端导出
  // 端点不支持，不透传）；后端 5000 行上限——当前筛选超出时前置显式提示
  // （静默截断违反诚实化红线）
  const EXPORT_LIMIT = 5000;
  if (total.value > EXPORT_LIMIT) {
    message.warning(
      `当前筛选 ${total.value} 条，导出仅包含前 ${EXPORT_LIMIT} 条（上限）`,
    );
  }
  exporting.value = true;
  try {
    const blob = await exportDiagnosisRunsApi({
      category: query.category,
      severity: query.severity,
      status: query.status,
      loopId: query.loopId,
      startTime: query.range
        ? query.range[0].startOf('day').utc().format('YYYY-MM-DDTHH:mm:ss')
        : undefined,
      endTime: query.range
        ? query.range[1].endOf('day').utc().format('YYYY-MM-DDTHH:mm:ss')
        : undefined,
    });
    const url = URL.createObjectURL(
      new Blob([blob as unknown as BlobPart], {
        type: 'text/csv;charset=utf-8',
      }),
    );
    const a = document.createElement('a');
    a.href = url;
    a.download = `diagnosis_runs_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    message.error('导出失败');
  } finally {
    exporting.value = false;
  }
}

// ---- 指标透明化（2026-10-05 第一批+第二批）----
/** 算子注册表缓存：列头中文（算子显示名·特征含义）动态取自注册表，键名变化免改前端 */
const opMeta = ref<Record<string, DiagnosisApi.OperatorInfo>>({});

/** 指标列组目录：族 → 算子 → 核心特征键（宽表对比用精选，全量在指标抽屉/导出） */
const FAMILY_LABELS: Record<string, string> = {
  oscillation: '振荡',
  stiction: '阀门粘滞',
  sensor: '仪表故障',
  link: '数据质量',
  tuning: '整定',
  saturation: '输出饱和',
  disturbance: '工艺外扰',
};
const METRIC_FEATURES: Record<string, Record<string, string[]>> = {
  oscillation: {
    oscillation_iae: ['similarity', 'zero_crossing_count', 'mean_period'],
    oscillation_fft: ['index', 'frequency'],
  },
  stiction: {
    stiction_ellipse: ['stiction_index', 'fitting_score'],
    stiction_choudhury: ['ngi', 'nli'],
    stiction_kano: ['stiction_ratio'],
  },
  sensor: {
    sensor_fault: [
      'sensor_subtype',
      'frozen_segment_ratio',
      'noise_std_ratio',
      'drift_magnitude',
    ],
  },
  link: {
    quality_code_rules: ['bad_rate', 'max_consecutive_bad'],
  },
  tuning: {
    step_response_overshoot: ['overshoot', 'decay_ratio', 'steady_state_error'],
    slow_response: ['ratio', 'time_constant', 'expected_time_constant'],
  },
  saturation: {
    output_saturation: ['saturation_rate'],
  },
  disturbance: {
    disturbance_burst: ['shift_frequency'],
  },
};

/** 已展开的指标列组（纯本地列显示，不影响查询） */
const metricFamilies = ref<string[]>([]);
const familyOptions = Object.entries(FAMILY_LABELS).map(([value, label]) => ({
  label,
  value,
}));

/** 指标列（key 约定 m:算子.特征，bodyCell 按前缀分流） */
const metricColumns = computed(() => {
  const cols: Array<{
    key: string;
    title: string;
    width: number;
  }> = [];
  for (const fam of metricFamilies.value) {
    for (const [opName, feats] of Object.entries(METRIC_FEATURES[fam] ?? {})) {
      const meta = opMeta.value[opName];
      for (const f of feats) {
        cols.push({
          key: `m:${opName}.${f}`,
          title: `${meta?.displayName ?? opName}·${meta?.outputsSchema?.[f] ?? f}`,
          width: 110,
        });
      }
    }
  }
  return cols;
});

/** 指标列取值：未执行 → 未执行（悬浮 skipReason）；无值 → — */
function metricValue(
  record: DiagnosisApi.RunListItem,
  opName: string,
  feat: string,
): string {
  const m = record.operatorMetrics?.[opName];
  if (!m || !m.executed) return '未执行';
  const v = m.features?.[feat];
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'number') return Number(v.toFixed(3)).toString();
  return String(v);
}

function metricSkipTitle(
  record: DiagnosisApi.RunListItem,
  opName: string,
): string {
  return record.operatorMetrics?.[opName]?.skipReason ?? '输入缺失/数据不足，算子跳过';
}

/** 数据质量列合并显示：等级 · 有效率 · 缺口率（悬浮完整门禁信息） */
function gateText(g: DiagnosisApi.GateInfo): string {
  return `${g.confidenceLevel} 级 · ${(g.validRate * 100).toFixed(0)}% · 缺口${(g.gapRatio * 100).toFixed(0)}%`;
}

function gateTitle(g: DiagnosisApi.GateInfo): string {
  return `点数 ${g.pointCount}/${g.expectedPoints} · 有效率 ${(g.validRate * 100).toFixed(1)}% · 缺口率 ${(g.gapRatio * 100).toFixed(1)}%${g.reason ? ` · ${g.reason}` : ''}`;
}

// ---- 指标明细（操作列"指标"入口 → 同一抽屉的"诊断指标" Tab） ----
function openMetrics(record: DiagnosisApi.RunListItem) {
  currentRow.value = record;
  metricsRunId.value = record.id;
  detailTab.value = 'metrics';
  drawerOpen.value = true;
}

// 列顺序（2026-10-05 用户裁决）：回路 / 诊断时间 / 主分类 / 触发方式在前；
// 次分类列删除（多数场景单候选命中恒空，2026-10-05 用户裁决——
// 次分类信息保留在详情面板"并存/待复核" chips 与导出 CSV 中）
const baseColumns = [
  // 回路列加宽容纳 18 字符位号（等宽 14px ≈ 170px）；复核结论多值场景
  // 收窄靠换行（2026-10-05 用户裁决）
  { dataIndex: 'loopTagName', title: '回路', width: 170 },
  { dataIndex: 'createdAt', title: '诊断时间', width: 150 },
  { dataIndex: 'primaryCategoryLabel', title: '主分类', width: 150 },
  { dataIndex: 'triggerType', title: '触发方式', width: 88 },
  { dataIndex: 'dataGate', title: '数据质量', width: 150 },
  { dataIndex: 'primaryConfidence', title: '置信度', width: 80 },
  { dataIndex: 'severity', title: '严重度', width: 76 },
  { dataIndex: 'timeWindowStart', title: '时间窗', width: 220 },
  { dataIndex: 'reviewResultLabels', title: '复核结论', width: 110 },
  { dataIndex: 'reviewStatus', title: '复核状态', width: 88 },
  { dataIndex: 'triggeredBy', title: '发起人', width: 100 },
  { dataIndex: 'status', title: '状态', width: 90 },
];
const actionColumn = {
  key: 'action',
  title: '操作',
  width: 70,
  fixed: 'right' as const,
};
const columns = computed(() => [
  ...baseColumns,
  ...metricColumns.value,
  actionColumn,
]);
/** 指标列展开后表格横向滚动宽度（基础列 ~1670 + 指标列×110） */
const tableScrollX = computed(() => 1670 + metricColumns.value.length * 110);

function fmtWindow(record: DiagnosisApi.RunListItem) {
  // 0929 时区收敛：slice 截取的是 UTC 串（差 8 小时），统一走 formatLocalTime 补 Z 转本地
  const s = formatLocalTime(record.timeWindowStart, 'MM-DD HH:mm');
  const e = formatLocalTime(record.timeWindowEnd, 'MM-DD HH:mm');
  return record.timeWindowStart && record.timeWindowEnd ? `${s} ~ ${e}` : '—';
}

function catColor(record: DiagnosisApi.RunListItem) {
  return record.primaryCategory
    ? (CATEGORY_META[record.primaryCategory]?.color ?? '#6c757d')
    : '#6c757d';
}

/** 主分类空值语义（2026-10-05 用户裁决）：NULL = 门禁通过但全部算子未命中
 * （分类引擎 NO_SYMPTOM → 落库 NULL），明确显示"正常"；
 * FAILED 留痕 run 无结论产出，显示 — */
function primaryText(record: DiagnosisApi.RunListItem) {
  if (record.primaryCategory)
    return record.primaryCategoryLabel ?? record.primaryCategory;
  return record.status === 'FAILED' ? '—' : '正常';
}

/** 置信度空值语义：结论=正常时引擎不给数值 → "不适用"；FAILED → — */
function confidenceText(record: DiagnosisApi.RunListItem): string {
  if (record.primaryConfidence != null)
    return `${Math.round(record.primaryConfidence * 100)}%`;
  return record.status === 'FAILED' ? '—' : '不适用';
}

/** 严重度空值语义：结论=正常 → "无"；FAILED → — */
function severityText(record: DiagnosisApi.RunListItem): string {
  if (record.severity) return SEVERITY_TEXT[record.severity] ?? record.severity;
  return record.status === 'FAILED' ? '—' : '无';
}

// ---- 批量删除（2026-10-05 用户需求；权限=诊断触发四角色，后端同口径校验） ----
const selectedRowKeys = ref<string[]>([]);
const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys as string[];
  },
}));
const batchDeleteVisible = ref(false);
const batchDeleteLoading = ref(false);

async function handleBatchDelete() {
  if (selectedRowKeys.value.length === 0) return;
  batchDeleteLoading.value = true;
  try {
    const res = await deleteDiagnosisRunsApi(selectedRowKeys.value);
    batchDeleteVisible.value = false;
    if (res.skipped.length > 0) {
      message.warning(
        `已删除 ${res.deleted} 条，跳过 ${res.skipped.length} 条（${res.skipped[0]?.reason}${res.skipped.length > 1 ? ' 等' : ''}）`,
      );
    } else {
      message.success(`已删除 ${res.deleted} 条诊断记录`);
    }
    selectedRowKeys.value = [];
    load();
  } catch {
    // 错误已由拦截器处理
  } finally {
    batchDeleteLoading.value = false;
  }
}

// ---- 路由 query 初值（追溯矩阵 G6：工作台统计下钻接参） ----
const route = useRoute();

const SEVERITY_VALUES = new Set<string>(['HIGH', 'LOW', 'MEDIUM']);
const RUN_STATUS_VALUES = new Set<string>([
  'FAILED',
  'PARTIAL',
  'RUNNING',
  'SUCCESS',
]);
const REVIEW_STATUS_VALUES = new Set<string>(['PENDING', 'REVIEWED']);

/**
 * 挂载时从 route.query 读取一次筛选初值（不做 watch 同步，之后用户可自由修改）。
 * 支持：startTime/endTime（ISO8601）、category、status、severity、reviewStatus、
 * loopId、focus（focus=runId 时自动打开该记录详情抽屉，契约对齐处置工作台 ?focus=）。
 */
function applyRouteQuery() {
  const q = route.query;
  if (
    typeof q.category === 'string' &&
    CATEGORY_OPTIONS.some((o) => o.value === q.category)
  ) {
    query.category = q.category as DiagnosisApi.Category;
  }
  if (typeof q.severity === 'string' && SEVERITY_VALUES.has(q.severity)) {
    query.severity = q.severity as DiagnosisApi.Severity;
  }
  if (typeof q.status === 'string' && RUN_STATUS_VALUES.has(q.status)) {
    query.status = q.status as DiagnosisApi.RunStatus;
  }
  if (
    typeof q.reviewStatus === 'string' &&
    REVIEW_STATUS_VALUES.has(q.reviewStatus)
  ) {
    query.reviewStatus = q.reviewStatus as DiagnosisApi.ReviewStatus;
  }
  if (typeof q.loopId === 'string' && q.loopId) {
    query.loopId = q.loopId;
  }
  if (typeof q.startTime === 'string' && typeof q.endTime === 'string') {
    const start = dayjs(q.startTime);
    const end = dayjs(q.endTime);
    if (start.isValid() && end.isValid()) {
      query.range = [start, end];
    }
  }
  // focus=runId 深链：直接打开该记录详情抽屉（详情接口按 id 拉取，
  // 失败时拦截器统一弹错误，抽屉落“无详情”空态）
  if (typeof q.focus === 'string' && q.focus) {
    openDetail({ id: q.focus } as DiagnosisApi.RunListItem);
  }
}

/** 算子注册表（指标列头中文 + 指标抽屉共用；失败不阻塞页面，列头退英文键） */
async function loadOperatorMeta() {
  try {
    const ops = await getDiagnosisOperatorsApi();
    const m: Record<string, DiagnosisApi.OperatorInfo> = {};
    for (const o of ops) m[o.name] = o;
    opMeta.value = m;
  } catch (error) {
    console.error('加载算子注册表失败:', error);
  }
}

onMounted(() => {
  applyRouteQuery();
  load();
  loadOperatorMeta();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="loading"
      subtitle="历史诊断记录检索 · 按分类/严重度筛选 · 点击行查看完整结论"
      title="诊断记录"
    >
      <template #actions>
        <Button
          v-permission="['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER']"
          danger
          size="small"
          :disabled="selectedRowKeys.length === 0"
          :loading="batchDeleteLoading && batchDeleteVisible"
          @click="batchDeleteVisible = true"
        >
          批量删除{{ selectedRowKeys.length > 0 ? `（${selectedRowKeys.length}）` : '' }}
        </Button>
        <ClpmToolbarButton
          :loading="exporting"
          icon="ant-design:download-outlined"
          label="导出 CSV"
          @click="handleExport"
        />
        <ClpmToolbarButton
          icon="ant-design:sync-outlined"
          label="刷新"
          @click="load()"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 筛选行 -->
    <div class="mb-3 mt-2 flex flex-wrap items-center gap-3">
      <RangePicker
        v-model:value="query.range"
        style="width: 240px"
        @change="handleFilterChange"
      />
      <Select
        v-model:value="query.category"
        :allow-clear="true"
        :options="CATEGORY_OPTIONS"
        placeholder="主分类"
        style="width: 160px"
        @change="handleFilterChange"
      />
      <Select
        v-model:value="query.severity"
        :allow-clear="true"
        :options="[
          { label: '高', value: 'HIGH' },
          { label: '中', value: 'MEDIUM' },
          { label: '低', value: 'LOW' },
        ]"
        placeholder="严重度"
        style="width: 110px"
        @change="handleFilterChange"
      />
      <Select
        v-model:value="query.status"
        :allow-clear="true"
        :options="[
          { label: '完成', value: 'SUCCESS' },
          { label: '部分完成', value: 'PARTIAL' },
          { label: '失败', value: 'FAILED' },
        ]"
        placeholder="状态"
        style="width: 120px"
        @change="handleFilterChange"
      />
      <Select
        v-model:value="query.reviewStatus"
        :allow-clear="true"
        :options="[
          { label: '待复核', value: 'PENDING' },
          { label: '已复核', value: 'REVIEWED' },
        ]"
        placeholder="复核状态"
        style="width: 110px"
        @change="handleFilterChange"
      />
      <!-- 指标列组（2026-10-05 第二批）：按症状族展开算子特征列做横向对比，
           纯本地列显示不影响查询；全量指标走操作列"指标"抽屉与 CSV 导出 -->
      <Select
        v-model:value="metricFamilies"
        :allow-clear="true"
        :max-tag-count="2"
        :options="familyOptions"
        mode="multiple"
        placeholder="指标列组"
        style="width: 260px"
      />
    </div>

    <Card :body-style="{ padding: '0' }" size="small">
      <ClpmDataCanvas
        :empty="!loading && items.length === 0"
        empty-text="暂无诊断记录"
      >
        <Table
          :columns="columns"
          :custom-row="
            (record: DiagnosisApi.RunListItem) => ({
              onClick: () => openDetail(record),
              style: { cursor: 'pointer' },
            })
          "
          :data-source="items"
          :pagination="{
            current: query.page,
            pageSize: query.pageSize,
            showSizeChanger: true,
            pageSizeOptions: ['10', '20', '50'],
            showTotal: (t: number) => `共 ${t} 条`,
            total,
          }"
          :row-selection="rowSelection"
          :loading="loading"
          :scroll="{ x: tableScrollX }"
          row-key="id"
          size="small"
          @change="handleTableChange"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'createdAt'">
              <span class="clpm-num">{{
                formatLocalTime(record.createdAt, 'YYYY-MM-DD HH:mm')
              }}</span>
            </template>
            <template v-else-if="column.dataIndex === 'primaryCategoryLabel'">
              <span
                v-if="record.primaryCategory"
                :style="{ color: catColor(record as DiagnosisApi.RunListItem) }"
              >
                {{ record.primaryCategoryLabel }}
              </span>
              <!-- 空值语义显式化：未见异常（NO_SYMPTOM 落库 NULL）/ 失败无产出 -->
              <span v-else class="text-neutral-400">
                {{ primaryText(record as DiagnosisApi.RunListItem) }}
              </span>
            </template>
            <template v-else-if="column.dataIndex === 'primaryConfidence'">
              {{ confidenceText(record as DiagnosisApi.RunListItem) }}
            </template>
            <template v-else-if="column.dataIndex === 'severity'">
              {{ severityText(record as DiagnosisApi.RunListItem) }}
            </template>
            <template v-else-if="column.dataIndex === 'timeWindowStart'">
              {{ fmtWindow(record as DiagnosisApi.RunListItem) }}
            </template>
            <template v-else-if="column.dataIndex === 'triggerType'">
              <span
                v-if="record.triggerType"
                :style="{ color: TRIGGER_TYPE_COLOR[record.triggerType] }"
              >
                {{
                  record.triggerTypeLabel ??
                  TRIGGER_TYPE_TEXT[record.triggerType] ??
                  record.triggerType
                }}
              </span>
              <span v-else class="text-neutral-400">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'dataGate'">
              <span
                v-if="record.dataGate"
                :title="gateTitle(record.dataGate as DiagnosisApi.GateInfo)"
                :style="{
                  color:
                    record.dataGate.confidenceLevel === 'E' || !record.dataGate.passed
                      ? '#cf1322'
                      : undefined,
                }"
              >
                {{ gateText(record.dataGate as DiagnosisApi.GateInfo) }}
              </span>
              <span v-else class="text-neutral-400">—</span>
            </template>
            <template v-else-if="String(column.key).startsWith('m:')">
              <span
                class="font-mono"
                :title="
                  metricValue(
                    record as DiagnosisApi.RunListItem,
                    String(column.key).slice(2).split('.')[0]!,
                    String(column.key).split('.')[1]!,
                  ) === '未执行'
                    ? metricSkipTitle(
                        record as DiagnosisApi.RunListItem,
                        String(column.key).slice(2).split('.')[0]!,
                      )
                    : ''
                "
              >
                {{
                  metricValue(
                    record as DiagnosisApi.RunListItem,
                    String(column.key).slice(2).split('.')[0]!,
                    String(column.key).split('.')[1]!,
                  )
                }}
              </span>
            </template>
            <template v-else-if="column.key === 'action'">
              <Button size="small" type="link" @click.stop="openMetrics(record as DiagnosisApi.RunListItem)">
                指标
              </Button>
            </template>
            <template v-else-if="column.dataIndex === 'reviewResultLabels'">
              <span v-if="record.reviewResultLabels?.length">
                {{ record.reviewResultLabels.join('、') }}
              </span>
              <span v-else class="text-neutral-400">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'reviewStatus'">
              <span
                v-if="record.reviewStatus"
                :style="{ color: REVIEW_STATUS_COLOR[record.reviewStatus] }"
              >
                {{
                  REVIEW_STATUS_TEXT[record.reviewStatus] ?? record.reviewStatus
                }}
              </span>
              <span v-else class="text-neutral-400">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'status'">
              {{ RUN_STATUS_TEXT[record.status] ?? record.status }}
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </Card>

    <!-- 诊断详情抽屉（2026-10-05 合并双 Tab：诊断结论 + 诊断指标；
         左缘拖拽手柄可调整宽度 520px~94vw，初始 900px） -->
    <Drawer
      v-model:open="drawerOpen"
      :title="`诊断详情${currentRow?.loopTagName ? ` · ${currentRow.loopTagName}` : ''}`"
      placement="right"
      :width="detailDrawerWidth"
      :body-style="{ padding: '0', position: 'relative', overflow: 'hidden' }"
      :destroy-on-close="true"
    >
      <div class="detail-drawer-resize" @mousedown="startDrawerResize"></div>
      <!-- 16 号文 F1 入口 2：头部工具区"诊断档案"（当前行回路） -->
      <template #extra>
        <Button
          v-if="currentRow?.loopId"
          size="small"
          @click="openArchive"
        >
          诊断档案
        </Button>
      </template>
      <Tabs v-model:active-key="detailTab" class="detail-tabs">
        <Tabs.TabPane key="conclusion" tab="诊断结论">
          <div class="h-full overflow-y-auto p-4">
            <ClpmDataCanvas
              :empty="!detail"
              :loading="detailLoading"
              empty-text="无详情"
            >
              <DiagnosisResultPanel v-if="detail" :detail="detail" />
            </ClpmDataCanvas>
          </div>
        </Tabs.TabPane>
        <Tabs.TabPane key="metrics" tab="诊断指标">
          <div class="h-full overflow-y-auto p-4">
            <DiagnosisMetricsPanel :run-id="metricsRunId" />
          </div>
        </Tabs.TabPane>
      </Tabs>
    </Drawer>

    <!-- 16 号文 F1：回路诊断档案抽屉（open-run 页内降级为刷新 focus 参数） -->
    <DiagnosisLoopArchiveDrawer
      v-model:open="archiveOpen"
      :loop-id="archiveLoopId"
      :loop-tag-name="archiveLoopTagName"
      @open-run="onArchiveOpenRun"
      @trigger-diagnosis="onArchiveTriggerDiagnosis"
    />

    <!-- 批量删除确认（非终态/在办处置建议的记录后端逐条跳过并回报） -->
    <Modal
      v-model:open="batchDeleteVisible"
      title="批量删除诊断记录"
      :confirm-loading="batchDeleteLoading"
      ok-text="确认删除"
      cancel-text="取消"
      :ok-button-props="{ danger: true }"
      @ok="handleBatchDelete"
    >
      <div class="space-y-2">
        <div>
          将删除已选 <strong>{{ selectedRowKeys.length }}</strong> 条诊断记录；
          执行中（非终态）或存在在办处置建议的记录将被跳过并在结果中提示。
        </div>
        <div class="text-sm text-neutral-400">
          删除后不可恢复；复核结论与证据快照随记录一并删除，请谨慎操作。
        </div>
      </div>
    </Modal>

    <!-- 单回路诊断抽屉：内嵌回路工作台诊断剖面（与诊断概览行操作同款） -->
    <Drawer
      v-model:open="diagWorkbenchOpen"
      title="回路诊断"
      placement="right"
      width="min(1560px, 94vw)"
      :body-style="{
        padding: '0',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }"
      destroy-on-close
    >
      <Workbench360
        v-if="diagWorkbenchOpen"
        :embed-loop-id="diagWorkbenchLoopId"
        embed-section="diag"
      />
    </Drawer>
  </Page>
</template>

<style scoped>
/* 诊断详情抽屉：Tabs 撑满高度（TabPane 内部滚动） */
.detail-tabs {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.detail-tabs :deep(.ant-tabs-content-holder) {
  flex: 1;
  min-height: 0;
}

.detail-tabs :deep(.ant-tabs-content) {
  height: 100%;
}

.detail-tabs :deep(.ant-tabs-nav) {
  padding: 0 16px;
  margin-bottom: 0;
}

/* 左缘拖拽调宽手柄 */
.detail-drawer-resize {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 10;
  width: 6px;
  height: 100%;
  cursor: col-resize;
}

.detail-drawer-resize:hover,
.detail-drawer-resize:active {
  background: rgb(0 0 0 / 6%);
}
</style>
