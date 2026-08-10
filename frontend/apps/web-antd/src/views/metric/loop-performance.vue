<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';
import type { Dayjs } from 'dayjs';

/**
 * 回路性能列表页
 *
 * 对齐后端 GET /api/v1/performance/loops/snapshots
 * - 顶部工具栏：标题 + 刷新
 * - 筛选区：工厂模型节点 + 回路编号 + 控制类型 + 评估状态 + 可信度 + 时间范围
 * - 表格：回路编号 / 名称 / 类型 / 控制类型 / 控制方式 / 评估等级 / 综合评分（服务端排序）/
 *   准确率 / 快速率 / 平稳率 / 有效自控率 / 可信度 / 时间窗口 / 评估时间 /
 *   评估状态 / 操作（详情、历史、诊断）；行点击打开详情抽屉
 * - 详情抽屉：回路基本信息 + 8 大 KPI + 5 项诊断/扩展指标（3+1+8 共 12 指标齐全）+
 *   可信度 + 时间窗 + 评估时间 + 历史快照子表（最近 10 条）
 * - 可信度抽屉：点击可信度单元格打开（不触发行抽屉），最新评估时间 / 数据源
 *   时间区间 / 评估状态 / 综合评分 / 可信度 / 有效数据率 + 12 子指标值与各自
 *   可信度（GET /api/v1/loops/{loopId}/confidence-latest）
 * - 历史 Modal：时间维度切换（8/12/24/72/168h） + ECharts 趋势图
 * - 诊断 Modal（90% 宽）：4 个 Tab（频谱分析 / 时域分析 / 诊断概览 / 评估历史）
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';
import type {
  ConfidenceLevel,
  GradeName,
  KpiSnapshotItem,
  KpiSnapshotQueryParams,
  KpiStatus,
  LoopConfidenceLatestItem,
  MetricApi,
} from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

import {
  computed,
  nextTick,
  onMounted,
  reactive,
  ref,
  shallowRef,
  watch,
} from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Badge,
  Button,
  Card,
  CheckboxGroup,
  Col,
  Descriptions,
  DescriptionsItem,
  Drawer,
  Input,
  message,
  Modal,
  RadioGroup,
  RangePicker,
  Row,
  Select,
  Spin,
  Table,
  Tabs,
  Tag,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisVisualizationApi } from '#/api/diagnosis';
import { getLoopListApi } from '#/api/loop';
import {
  getGradeDistributionApi,
  getGradingThresholdsApi,
  getLoopConfidenceLatestApi,
  getLoopSnapshotsApi,
} from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  ClpmAiDrawer,
  ClpmDataCanvas,
  ClpmInfoTip,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import ConfidenceBadge from '#/components/clpm/confidence-badge.vue';
import ChoudhuryCard from '#/components/diagnosis-visualization/choudhury-card.vue';
import CusumChart from '#/components/diagnosis-visualization/cusum-chart.vue';
import IaeCard from '#/components/diagnosis-visualization/iae-card.vue';
import KanoCard from '#/components/diagnosis-visualization/kano-card.vue';
import QualityTimelineChart from '#/components/diagnosis-visualization/quality-timeline-chart.vue';
import RadarChart from '#/components/diagnosis-visualization/radar-chart.vue';
import SaturationChart from '#/components/diagnosis-visualization/saturation-chart.vue';
import ScatterChart from '#/components/diagnosis-visualization/scatter-chart.vue';
import ScoreBreakdown from '#/components/diagnosis-visualization/score-breakdown.vue';
import SlowResponseCard from '#/components/diagnosis-visualization/slow-response-card.vue';
import SpectrumChart from '#/components/diagnosis-visualization/spectrum-chart.vue';
import StatisticsBarChart from '#/components/diagnosis-visualization/statistics-bar-chart.vue';
import StepResponseChart from '#/components/diagnosis-visualization/step-response-chart.vue';
import { useAiInsightGate } from '#/composables/use-ai-insight-gate';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useConfigAccess } from '#/composables/use-config-access';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import {
  LOOP_TYPE_LABEL_MAP,
  useLoopPalettes,
} from '#/composables/use-loop-palettes';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useScoreColor } from '#/composables/use-score-color';
import { useTableDensity } from '#/composables/use-table-density';
import { KPI_TERM_EXPLANATIONS } from '#/constants/clpm-ui';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';
import { formatLocalTime, normalizeUtcTimestamp } from '#/utils/format';

defineOptions({ name: 'MetricLoopPerformance' });

const { isDark, themeColors } = useClpmTheme();
const { modeLabelColor } = useLoopPalettes();

// ===== 常量映射 =====

// 回路类型 label / Tag 浅色统一走共享色板 use-loop-palettes
// （LOOP_TYPE_LABEL_MAP / LOOP_TYPE_TAG_COLOR_MAP），视图层不再重复定义

/** 控制类型映射 */
const CONTROL_TYPE_MAP: Record<string, string> = {
  STABLE: '稳定型',
  SLOW: '慢速型',
  FAST: '快速型',
  LOGIC: '逻辑型',
};

/** 评估状态映射 */
const STATUS_COLOR_MAP: Record<string, string> = {
  SUCCESS: 'success',
  PARTIAL: 'warning',
  INCONCLUSIVE: 'default',
};

const STATUS_LABEL_MAP: Record<string, string> = {
  SUCCESS: '成功',
  INCONCLUSIVE: '不确定',
  PARTIAL: '部分',
};

/** 可信度徽章颜色 */
const CONFIDENCE_COLOR_MAP: Record<string, string> = {
  A: 'green',
  B: 'blue',
  C: 'gold',
  D: 'orange',
  E: 'red',
};

const CONFIDENCE_LABEL_MAP: Record<string, string> = {
  A: 'A 优秀',
  B: 'B 良好',
  C: 'C 一般',
  D: 'D 较差',
  E: 'E 不足',
};

/** 评估等级颜色（一级~五级） */
const GRADE_COLOR_MAP: Record<number, string> = {
  1: 'green',
  2: 'blue',
  3: 'gold',
  4: 'orange',
  5: 'red',
};

const GRADE_LABEL_MAP: Record<number, string> = {
  1: '一级',
  2: '二级',
  3: '三级',
  4: '四级',
  5: '五级',
};

/** 控制类型筛选选项 */
const controlTypeOptions = [
  { label: '全部', value: undefined },
  { label: '稳定型', value: 'STABLE' },
  { label: '慢速型', value: 'SLOW' },
  { label: '快速型', value: 'FAST' },
  { label: '逻辑型', value: 'LOGIC' },
];

/** 评估状态筛选选项 */
const statusOptions = [
  { label: '全部', value: undefined },
  { label: '成功', value: 'SUCCESS' },
  { label: '不确定', value: 'INCONCLUSIVE' },
  { label: '部分', value: 'PARTIAL' },
];

/** 可信度筛选选项 */
const confidenceOptions = [
  { label: '全部', value: undefined },
  { label: 'A 优秀', value: 'A' },
  { label: 'B 良好', value: 'B' },
  { label: 'C 一般', value: 'C' },
  { label: 'D 较差', value: 'D' },
  { label: 'E 不足', value: 'E' },
];

/** 历史趋势时间窗选项（小时） */
const historyWindowOptions = [
  { label: '8小时', value: 8 },
  { label: '12小时', value: 12 },
  { label: '24小时', value: 24 },
  { label: '72小时', value: 72 },
  { label: '168小时', value: 168 },
];

// ===== 合并行类型：快照 + 回路元数据 =====

interface LoopPerformanceRow extends KpiSnapshotItem {
  /** 关联的回路元数据（来自 loops 列表） */
  loopMeta?: LoopApi.LoopListItem;
  /** 回路描述（来自 loopMeta.description） */
  description?: string;
  /** 回路类型（来自 loopMeta.loopType） */
  loopType?: string;
  /** 控制类型（来自 loopMeta.controlType） */
  controlType?: string;
  /** 控制方式（来自 loopMeta.controlMode） */
  controlMode?: string;
}

// ===== 列表状态 =====

const loading = ref(false);
const loadError = ref(false);
const rows = ref<LoopPerformanceRow[]>([]);
const total = ref(0);
const query = reactive({
  plantNodeId: undefined as string | undefined,
  controlType: undefined as string | undefined,
  status: undefined as KpiStatus | undefined,
  confidenceLevel: undefined as ConfidenceLevel | undefined,
  loopTagName: '' as string,
  /** 时间范围（本地时间，提交时转 UTC ISO） */
  timeRange: undefined as [Dayjs, Dayjs] | undefined,
  page: 1,
  pageSize: 20,
  /** 服务端排序（仅综合评分列；undefined = 默认 tsStart DESC） */
  sortBy: undefined as 'score' | undefined,
  sortOrder: 'desc' as 'asc' | 'desc',
});

/** 回路元数据 Map（loopId → LoopListItem） */
const loopMap = shallowRef<Map<string, LoopApi.LoopListItem>>(new Map());

const route = useRoute();
const { canReadConfig } = useConfigAccess();
const { axisBase, getTooltipPreset } = useEchartsPreset();

/** 工厂节点树（保留层级结构供 TreeSelect 使用） */
const plantNodeTree = ref<PlantNodeApi.PlantNode[]>([]);

// ===== 定级阈值 =====

const gradingThresholds = ref<MetricApi.GradingThresholdItem[]>([]);

/** 国标默认等级名（level → name），阈值配置未加载时兜底 */
const DEFAULT_GRADE_NAME_BY_LEVEL: Record<number, string> = {
  1: 'EXCELLENT',
  2: 'GOOD',
  3: 'FAIR',
  4: 'WARNING',
  5: 'POOR',
};

/** 等级名 → level（动态阈值配置优先，兜底国标默认名；未知名返回 null） */
function gradeLevelByName(name: string): null | number {
  const t = gradingThresholds.value.find((item) => item.name === name);
  if (t) return t.level;
  for (const [level, defaultName] of Object.entries(
    DEFAULT_GRADE_NAME_BY_LEVEL,
  )) {
    if (defaultName === name) return Number(level);
  }
  return null;
}

/** level → 服务端 grade 筛选等级名（动态阈值配置优先，兜底国标默认名） */
function gradeNameByLevel(level: number): GradeName {
  return (gradingThresholds.value.find((item) => item.level === level)?.name ??
    DEFAULT_GRADE_NAME_BY_LEVEL[level] ??
    'POOR') as GradeName;
}

/**
 * 根据综合评分判定评估等级（1~5；无评分返回 null）。
 * 统一走 useScoreColor 判定链：动态 gradingThresholds 定档，
 * 配置未加载时降级 GB/T 44693.2-2024 §6.3 默认阈值。
 */
function getGrade(score: null | number | undefined): null | number {
  const level = useScoreColor(score, gradingThresholds).level.value;
  return level === null ? null : Number(level);
}

/**
 * 综合评分颜色。统一走 useScoreColor：动态阈值定档，
 * null/NaN → ZL 中性灰（INCONCLUSIVE 是"数据不足"，严禁渲染为故障红）。
 */
function scoreColor(val: null | number | undefined): string {
  return useScoreColor(val, gradingThresholds).color.value;
}

// ===== 表格列定义 =====

/**
 * 列定义（computed：综合评分列的 sortOrder 受控于 query 状态）。
 * 注意：ClpmDataCanvas 默认 skeleton loading 会卸载 Table，
 * 非受控排序态会在每次加载后丢失，因此这里显式受控。
 */
const columns = computed<TableColumnsType>(() => [
  {
    title: '回路编号',
    key: 'loopTagName',
    dataIndex: 'loopTagName',
    width: 144,
    fixed: 'left',
    ellipsis: true,
  },
  {
    title: '回路名称',
    key: 'description',
    dataIndex: 'description',
    width: 160,
    ellipsis: true,
  },
  {
    title: '回路类型',
    key: 'loopType',
    width: 72,
    align: 'center' as const,
  },
  {
    title: '控制类型',
    key: 'controlType',
    width: 80,
    align: 'center' as const,
  },
  {
    title: '控制方式',
    key: 'controlMode',
    width: 68,
    align: 'center' as const,
  },
  {
    title: '评估等级',
    key: 'grade',
    width: 80,
    align: 'center' as const,
  },
  {
    title: '综合评分',
    key: 'score',
    dataIndex: 'score',
    width: 90,
    sorter: true,
    align: 'center' as const,
    sortOrder: (() => {
      if (query.sortBy !== 'score') return null;
      return query.sortOrder === 'asc' ? 'ascend' : 'descend';
    })(),
  },
  // P1-04：准确率/快速率/平稳率/有效自控率 4 个 KPI 列从表格移除，
  // 避免横向滚动；完整 8 大 KPI 在详情抽屉展示（含 Tooltip 解释）
  {
    title: '可信度',
    key: 'confidenceLevel',
    dataIndex: 'confidenceLevel',
    width: 68,
    align: 'center' as const,
  },
  {
    title: '时间窗口',
    key: 'tsRange',
    width: 140,
    align: 'center' as const,
  },
  {
    title: '评估时间',
    key: 'tsEnd',
    dataIndex: 'tsEnd',
    width: 116,
    align: 'center' as const,
  },
  {
    title: '评估状态',
    key: 'status',
    dataIndex: 'status',
    width: 68,
    align: 'center' as const,
  },
  {
    title: '操作',
    key: 'action',
    width: 184,
    fixed: 'right' as const,
    align: 'center' as const,
  },
]);

// ===== 统计卡片状态 =====

/** 评估等级统计（1~5 → 数量），来自服务端 /grade-distribution 聚合 */
const gradeStats = ref<Record<number, number>>({
  1: 0,
  2: 0,
  3: 0,
  4: 0,
  5: 0,
});

/** 统计总数（服务端聚合 total，含 INCONCLUSIVE；不随等级卡片筛选变化） */
const statsTotal = ref(0);

/** 当前选中的等级筛选（null = 全部；服务端 grade 参数过滤） */
const selectedGrade = ref<null | number>(null);

/** 优良率（一级+二级占比，即默认 score≥80 档，随定级阈值配置联动） */
const excellentRate = computed(() => {
  if (statsTotal.value === 0) return 0;
  const count = (gradeStats.value[1] ?? 0) + (gradeStats.value[2] ?? 0);
  return Math.round((count / statsTotal.value) * 100);
});

/** 合格率（一~三级占比，即默认 score≥60 档，随定级阈值配置联动） */
const passRate = computed(() => {
  if (statsTotal.value === 0) return 0;
  const count =
    (gradeStats.value[1] ?? 0) +
    (gradeStats.value[2] ?? 0) +
    (gradeStats.value[3] ?? 0);
  return Math.round((count / statsTotal.value) * 100);
});

/**
 * 等级卡片配色：阈值项自带 color 优先，未配置按档位降级 ZL 语义色
 * （降级链与 use-score-color 一致）
 */
function gradeCardColor(level: number): string {
  const t = gradingThresholds.value.find((item) => item.level === level);
  if (t?.color) return t.color;
  const fallbackByLevel: Record<number, string> = {
    1: themeColors.value.SUCCESS,
    2: themeColors.value.INFO,
    3: themeColors.value.WARNING,
    4: themeColors.value.DANGER,
    5: themeColors.value.DANGER,
  };
  return fallbackByLevel[level] ?? themeColors.value.NEUTRAL;
}

/** 等级饼状图 */
const gradeChartRef = ref<EchartsUIType>();
const { renderEcharts: renderGradeChart } = useEcharts(gradeChartRef);

/** 渲染等级分布饼状图 */
function updateGradeChart() {
  const grades = [1, 2, 3, 4, 5];
  const labels = ['一级', '二级', '三级', '四级', '五级'];
  const data = grades.map((g) => ({
    value: gradeStats.value[g] || 0,
    itemStyle: { color: gradeCardColor(g) },
    name: labels[g - 1],
  }));

  renderGradeChart({
    animation: false,
    series: [
      {
        data,
        type: 'pie',
        radius: ['40%', '75%'],
        label: { show: false },
        labelLine: { show: false },
        emphasis: { label: { show: false } },
      },
    ],
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
  });
}

/** 合并快照与回路元数据 */
function mergeLoopMeta(snap: KpiSnapshotItem): LoopPerformanceRow {
  const meta = snap.loopId ? loopMap.value.get(snap.loopId) : undefined;
  return {
    ...snap,
    loopMeta: meta,
    description: meta?.description,
    loopType: meta?.loopType,
    controlType: meta?.controlType,
    controlMode: meta?.controlMode,
  };
}

/**
 * 组装快照查询参数（loadList / loadStats 共用；不含分页/排序/等级筛选）。
 * 返回 null 表示控制类型筛选无匹配回路（结果必为空，无需请求）。
 */
function buildSnapshotParams(): KpiSnapshotQueryParams | null {
  const params: KpiSnapshotQueryParams = {};
  if (query.plantNodeId) params.plantNodeId = query.plantNodeId;
  if (query.status) params.status = query.status;
  if (query.confidenceLevel) params.confidenceLevel = query.confidenceLevel;
  if (query.loopTagName) params.loopTagName = query.loopTagName;
  if (query.timeRange?.[0]) params.startTime = query.timeRange[0].toISOString();
  if (query.timeRange?.[1]) params.endTime = query.timeRange[1].toISOString();
  // 按控制类型筛选：先在 loopMap 中找到匹配的 loopId，再传给快照接口
  if (query.controlType) {
    const matchedIds: string[] = [];
    for (const [id, loop] of loopMap.value.entries()) {
      if (loop.controlType === query.controlType) matchedIds.push(id);
    }
    if (matchedIds.length === 0) return null;
    params.loopId = matchedIds.join(',');
  }
  return params;
}

/** 加载等级分布统计（服务端 SQL 聚合，替代全量拉取客户端统计） */
async function loadStats() {
  try {
    const params = buildSnapshotParams();
    if (params === null) {
      gradeStats.value = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
      statsTotal.value = 0;
      updateGradeChart();
      return;
    }
    const dist = await getGradeDistributionApi(params);
    // 等级名 → level 归集（INCONCLUSIVE/total 不计入 1~5 级卡片）
    const gStats: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
    for (const [name, count] of Object.entries(dist)) {
      if (name === 'total' || name === 'INCONCLUSIVE') continue;
      const level = gradeLevelByName(name);
      if (level !== null) gStats[level] = (gStats[level] ?? 0) + count;
    }
    gradeStats.value = gStats;
    statsTotal.value = dist.total ?? 0;
    updateGradeChart();
  } catch {
    // 统计卡片保留旧数据；错误 toast 由 api/request.ts 拦截器统一弹出
  }
}

/** 点击等级卡片筛选 */
function handleGradeCardClick(grade: null | number) {
  selectedGrade.value = selectedGrade.value === grade ? null : grade;
  query.page = 1;
  loadList();
}

// ===== 工具函数 =====

/** 时间字符串规范化（PostgreSQL timestamp without timezone 假定为 UTC） */
function normalizeTime(ts: null | string | undefined): null | string {
  if (!ts) return null;
  return normalizeUtcTimestamp(ts);
}

function formatTsRange(start: null | string, end: null | string): string {
  const s = normalizeTime(start);
  const e = normalizeTime(end);
  if (!s && !e) return '—';
  const fmt = 'MM-DD HH:mm';
  if (s && e) {
    const ds = dayjs(s);
    const de = dayjs(e);
    // 同一天：MM-DD HH:mm~HH:mm（省略第二个日期）
    if (ds.isSame(de, 'day')) {
      return `${ds.format(fmt)}~${de.format('HH:mm')}`;
    }
    return `${ds.format(fmt)} ~ ${de.format(fmt)}`;
  }
  return dayjs(e || s).format(fmt);
}

function formatTime(ts: null | string | undefined): string {
  return formatLocalTime(ts, 'YYYY-MM-DD HH:mm:ss');
}

function formatShortTime(ts: null | string | undefined): string {
  return formatLocalTime(ts, 'MM-DD HH:mm');
}

function formatNumber(val: null | number | undefined, suffix = ''): string {
  if (val === null || val === undefined) return '—';
  return `${val.toFixed(2)}${suffix}`;
}

/** 0~1 比率（如 validRate）格式化为百分比 */
function formatRatio(val: null | number | undefined): string {
  if (val === null || val === undefined) return '—';
  return `${(val * 100).toFixed(2)}%`;
}

function getMetricValue(
  record: object,
  dataIndex: string,
): null | number | undefined {
  const value = (record as unknown as Record<string, unknown>)[dataIndex];
  return typeof value === 'number' ? value : undefined;
}

// ===== 数据加载 =====

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodeTree.value = tree || [];
  } catch {
    plantNodeTree.value = [];
  }
}

/** 加载回路列表（构建 loopId → LoopListItem 映射） */
async function loadLoopMap() {
  try {
    const allLoops: LoopApi.LoopListItem[] = [];
    let page = 1;
    const loopPageSize = 100;
    let totalLoops = 0;
    do {
      const result = await getLoopListApi({
        page,
        pageSize: loopPageSize,
      });
      totalLoops = result.total;
      allLoops.push(...(result.items || []));
      page += 1;
    } while ((page - 1) * loopPageSize < totalLoops);
    const map = new Map<string, LoopApi.LoopListItem>();
    for (const l of allLoops) {
      map.set(l.loopId, l);
    }
    loopMap.value = map;
  } catch {
    loopMap.value = new Map();
  }
}

/** 加载定级阈值 */
async function loadGradingThresholds() {
  // 整改 C2-1：SPONSOR/EXPERT 无 /configs/* 读取权限，前置跳过避免 403 toast
  if (!canReadConfig.value) return;
  try {
    const result = await getGradingThresholdsApi();
    gradingThresholds.value = result.thresholds || [];
  } catch {
    gradingThresholds.value = [];
  }
}

/** 加载列表数据（合并快照 + 回路元数据） */
async function loadList() {
  loading.value = true;
  loadError.value = false;
  try {
    const params = buildSnapshotParams();
    if (params === null) {
      rows.value = [];
      total.value = 0;
      return;
    }

    // 等级筛选：服务端 grade 参数按当前定级阈值过滤（level → 等级名映射），
    // 替代原"全量拉取 → 客户端过滤 → 客户端分页"
    if (selectedGrade.value !== null) {
      params.grade = gradeNameByLevel(selectedGrade.value);
    }
    if (query.sortBy) {
      params.sortBy = query.sortBy;
      params.sortOrder = query.sortOrder;
    }

    const result = await getLoopSnapshotsApi({
      ...params,
      page: query.page,
      pageSize: query.pageSize,
    });
    rows.value = (result.items || []).map((snap) => mergeLoopMeta(snap));
    total.value = result.total;
  } catch (error) {
    loadError.value = true;
    // 错误 toast 由 api/request.ts 拦截器统一弹出，视图层只更新本地 error 态
    console.error('加载回路性能列表失败:', error);
  } finally {
    loading.value = false;
  }
}

function handleSearch() {
  query.page = 1;
  selectedGrade.value = null;
  query.loopTagName = query.loopTagName?.trim() || '';
  loadList();
  loadStats();
}

function handleTableChange(
  pagination: TablePaginationConfig,
  _filters: unknown,
  sorter: any,
) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 20;
  // 综合评分列服务端排序（升/降/取消三态）
  const order = Array.isArray(sorter) ? sorter[0]?.order : sorter?.order;
  if (order === 'ascend' || order === 'descend') {
    query.sortBy = 'score';
    query.sortOrder = order === 'ascend' ? 'asc' : 'desc';
  } else {
    query.sortBy = undefined;
    query.sortOrder = 'desc';
  }
  loadList();
}

// ===== 详情抽屉 =====

const drawerVisible = ref(false);
const drawerRecord = ref<LoopPerformanceRow | null>(null);

// AI 洞察两级门禁（performance 场景需 loopId，门禁2 = 已选回路）
const { init: initAiGate, gateStatus, gateTooltip } = useAiInsightGate();
initAiGate();
const aiDrawerOpen = ref(false);

/** #7: 页面级回路选择器——供 AI 洞察使用上下文，无需打开详情抽屉 */
const selectedLoopId = ref<string | undefined>(undefined);
/** 表格单选选中行 key（组合键 loopId-tsStart，供 row-selection 受控） */
const selectedRowKeys = ref<string[]>([]);
/** 表格行单选配置：radio 模式，选中回路后自动设为 AI 分析对象 */
const rowSelection = computed(() => ({
  type: 'radio' as const,
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[], selectedRows: LoopPerformanceRow[]) => {
    selectedRowKeys.value = keys.map(String);
    selectedLoopId.value = selectedRows[0]?.loopId ?? undefined;
  },
}));
/** AI 上下文 loopId：优先页面级选择器，回退详情抽屉选中回路 */
const aiLoopId = computed(
  () => selectedLoopId.value ?? drawerRecord.value?.loopId ?? null,
);
const aiGateStatus = computed(() => gateStatus(aiLoopId.value, true));
const aiGateTooltip = computed(() => gateTooltip(aiGateStatus.value));

/** 统一工具栏（#11: 全业务页部署统一工具栏） */
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: loadList, loading: loading.value },
  ai: {
    onClick: () => {
      aiDrawerOpen.value = true;
    },
    disabled: aiGateStatus.value !== 'active',
    disabledReason: aiGateTooltip.value,
    tooltip: aiGateTooltip.value || 'AI 性能分析',
  },
  help: {
    onClick: () =>
      showPageHelp({
        title: '回路性能',
        content:
          '按回路展示 KPI 评估结果。在筛选区选择回路后可使用 AI 性能分析，点击表格行查看详情。',
      }),
  },
}));

// ===== A-07：表格密度三档（紧凑/标准/宽松，持久化）=====
const { tableSize, densityLabel, cycleDensity } = useTableDensity(
  'metric-loop-performance',
);

/** 抽屉内历史快照子表（最近 10 条） */
const drawerHistory = ref<KpiSnapshotItem[]>([]);
const drawerHistoryLoading = ref(false);

/** 抽屉 Tab：性能详情 / 综合评估 */
const drawerTab = ref<'comprehensive' | 'detail'>('detail');

/** 抽屉综合评估 Tab 所需的诊断可视化数据（PV-OP 散点图等） */
const drawerDiagData = ref<DiagnosisApi.DiagnosisVisualizationData | null>(
  null,
);
const drawerDiagLoading = ref(false);

async function loadDrawerHistory(loopId: string) {
  drawerHistoryLoading.value = true;
  try {
    const result = await getLoopSnapshotsApi({
      loopId,
      latestOnly: false,
      page: 1,
      pageSize: 10,
    });
    drawerHistory.value = result.items || [];
  } catch {
    drawerHistory.value = [];
  } finally {
    drawerHistoryLoading.value = false;
  }
}

/** 加载抽屉综合评估 Tab 所需诊断可视化数据 */
async function loadDrawerDiag() {
  if (!drawerRecord.value?.loopId) return;
  drawerDiagLoading.value = true;
  try {
    drawerDiagData.value = await getDiagnosisVisualizationApi(
      drawerRecord.value.loopId,
    );
  } catch (error: any) {
    console.error('加载综合评估诊断数据失败:', error);
    drawerDiagData.value = null;
  } finally {
    drawerDiagLoading.value = false;
  }
}

/** 抽屉 Tab 切换：首次切到综合评估时懒加载诊断数据 */
function handleDrawerTabChange(key: number | string) {
  if (
    String(key) === 'comprehensive' &&
    drawerDiagData.value === null &&
    !drawerDiagLoading.value
  ) {
    loadDrawerDiag();
  }
}

function openDetail(record: LoopPerformanceRow) {
  drawerRecord.value = record;
  drawerVisible.value = true;
  drawerHistory.value = [];
  drawerTab.value = 'detail';
  drawerDiagData.value = null;
  if (record.loopId) {
    loadDrawerHistory(record.loopId);
  }
}

function closeDetail() {
  drawerVisible.value = false;
  drawerRecord.value = null;
  drawerHistory.value = [];
  drawerTab.value = 'detail';
  drawerDiagData.value = null;
}

/** 表格行点击 → 打开详情抽屉（对齐低效排行页行级交互）。
 *  点击行首 radio 时仅选中回路（由 rowSelection 处理），不打开详情。 */
function rowClick(record: LoopPerformanceRow) {
  return {
    onClick: (event: MouseEvent) => {
      if ((event.target as HTMLElement).closest('.ant-table-selection-column'))
        return;
      openDetail(record);
    },
    style: { cursor: 'pointer' },
  };
}

// ===== 可信度详情抽屉 =====

/** 12 子指标元数据（3+1+8 体系，键为 DB 列名 snake_case） */
const CONFIDENCE_METRIC_META: { key: string; label: string; unit: string }[] = [
  { key: 'accuracy_rate', label: '准确率', unit: '%' },
  { key: 'fast_rate', label: '快速率', unit: '%' },
  { key: 'steady_rate', label: '平稳率', unit: '%' },
  { key: 'effective_auto_rate', label: '有效自控率', unit: '%' },
  { key: 'good_value_rate', label: '好值率', unit: '%' },
  { key: 'auto_mode_rate', label: '自控率', unit: '%' },
  { key: 'settling_time', label: '稳定时间', unit: 's' },
  { key: 'ideal_settling_time', label: '理想稳定时间', unit: 's' },
  { key: 'oscillation_rate', label: '振荡率', unit: '%' },
  { key: 'saturation_rate', label: '饱和率', unit: '%' },
  { key: 'stiction_index', label: '阀门粘滞指数', unit: '' },
  { key: 'output_trip_index', label: '输出跳变率', unit: '' },
];

const confDrawerVisible = ref(false);
const confDrawerLoading = ref(false);
const confDrawerRecord = ref<LoopPerformanceRow | null>(null);
const confDetail = ref<LoopConfidenceLatestItem | null>(null);

const confMetricColumns: TableColumnsType = [
  { title: '指标', key: 'label', dataIndex: 'label' },
  {
    title: '计算值',
    key: 'value',
    dataIndex: 'value',
    width: 120,
    align: 'right' as const,
  },
];

/**
 * 12 子指标表格行（按 3+1+8 顺序合并计算值）。
 *
 * 可信度统一 Phase 2（P2-6 / D2）：子指标可信度已统一为回路级，
 * ``metrics`` JSONB 仅保留 ``value`` 字段（去掉 ``confidence``），
 * 故此处不再合并 confidence。回路级可信度见上方 Descriptions。
 */
const confMetricRows = computed(() => {
  const metrics = confDetail.value?.metrics ?? {};
  return CONFIDENCE_METRIC_META.map((meta) => ({
    ...meta,
    value: metrics[meta.key]?.value ?? null,
  }));
});

/** 点击可信度单元格 → 打开可信度详情抽屉（不触发行抽屉） */
async function openConfidence(record: LoopPerformanceRow) {
  confDrawerRecord.value = record;
  confDrawerVisible.value = true;
  confDetail.value = null;
  if (!record.loopId) return;
  confDrawerLoading.value = true;
  try {
    confDetail.value = await getLoopConfidenceLatestApi(record.loopId);
  } catch (error) {
    // 错误 toast 由 api/request.ts 拦截器统一弹出，抽屉内展示"暂无评估记录"
    console.error('加载可信度详情失败:', error);
  } finally {
    confDrawerLoading.value = false;
  }
}

function closeConfidence() {
  confDrawerVisible.value = false;
  confDrawerRecord.value = null;
  confDetail.value = null;
}

// ===== 历史 Modal =====

const historyModalVisible = ref(false);
const historyLoading = ref(false);
const historyRecord = ref<LoopPerformanceRow | null>(null);
const historyWindow = ref<number>(24);
const historySnapshots = ref<KpiSnapshotItem[]>([]);

// ===== 整改 F3：历史趋势指标多选（≤5） =====
/** 可选指标注册表（综合评分=柱状，其余折线；系列色见 historyMetricColor） */
const HISTORY_METRIC_OPTIONS = [
  { label: '综合评分', value: 'score', kind: 'bar' },
  { label: '准确率', value: 'accuracyRate', kind: 'line' },
  { label: '快速率', value: 'fastRate', kind: 'line' },
  { label: '平稳率', value: 'steadyRate', kind: 'line' },
  { label: '有效自控率', value: 'effectiveAutoRate', kind: 'line' },
  { label: '好值率', value: 'goodValueRate', kind: 'line' },
  { label: '振荡率', value: 'oscillationRate', kind: 'line' },
  { label: '饱和率', value: 'saturationRate', kind: 'line' },
  { label: '仪表故障率', value: 'instrumentFaultRate', kind: 'line' },
] as const;

/**
 * 历史趋势系列色：就近映射 themeColors 语义色（随明暗主题响应）。
 * 默认 5 项（评分/准确/快速/平稳/自控）保持互异；非默认项允许撞色，
 * 取舍同批次 A 图表族（区分色就近语义化，不再维护私有色板）。
 */
function historyMetricColor(metric: string): string {
  const map: Record<string, string> = {
    score: themeColors.value.INFO,
    accuracyRate: themeColors.value.SUCCESS,
    fastRate: themeColors.value.WARNING,
    steadyRate: themeColors.value.NEUTRAL,
    effectiveAutoRate: themeColors.value.DANGER,
    goodValueRate: themeColors.value.INFO,
    oscillationRate: themeColors.value.DANGER,
    saturationRate: themeColors.value.WARNING,
    instrumentFaultRate: themeColors.value.NEUTRAL,
  };
  return map[metric] ?? themeColors.value.NEUTRAL;
}

const HISTORY_METRIC_MAX = 5;

const selectedHistoryMetrics = ref<string[]>([
  'score',
  'accuracyRate',
  'fastRate',
  'steadyRate',
  'effectiveAutoRate',
]);

/** 多选变更：限制最多 5 项后重渲 */
function handleHistoryMetricChange(values: (boolean | number | string)[]) {
  if (values.length > HISTORY_METRIC_MAX) {
    message.warning(`最多同时对比 ${HISTORY_METRIC_MAX} 个指标`);
    return;
  }
  selectedHistoryMetrics.value = values.map(String);
  renderHistoryTrend();
}
const historyChartRef = ref<EchartsUIType>();
const { renderEcharts: renderHistoryChart } = useEcharts(historyChartRef);

async function openHistory(record: LoopPerformanceRow) {
  historyRecord.value = record;
  historyModalVisible.value = true;
  historyWindow.value = 24;
  await loadHistoryData();
}

async function loadHistoryData() {
  if (!historyRecord.value?.loopId) {
    message.warning('该记录缺少回路 ID，无法查询历史');
    return;
  }
  historyLoading.value = true;
  try {
    const endTime = dayjs();
    const startTime = endTime.subtract(historyWindow.value, 'hour');
    // 后端 pageSize 上限 100，循环分页拉取时间窗内全部快照
    const allItems: KpiSnapshotItem[] = [];
    let page = 1;
    const pageLimit = 100;
    let total = 0;
    do {
      const result = await getLoopSnapshotsApi({
        loopId: historyRecord.value.loopId,
        startTime: startTime.toISOString(),
        endTime: endTime.toISOString(),
        latestOnly: false,
        page,
        pageSize: pageLimit,
      });
      allItems.push(...(result.items || []));
      total = result.total ?? 0;
      page += 1;
    } while ((page - 1) * pageLimit < total);
    historySnapshots.value = allItems.toSorted((a, b) => {
      const aTs = normalizeTime(a.tsStart) || '';
      const bTs = normalizeTime(b.tsStart) || '';
      return aTs.localeCompare(bTs);
    });
    await nextTick();
    renderHistoryTrend();
  } catch (error) {
    // 错误 toast 由 api/request.ts 拦截器统一弹出，视图层不重复提示
    console.error('加载历史趋势失败:', error);
  } finally {
    historyLoading.value = false;
  }
}

/** 渲染历史趋势图：综合评分(柱) + 准确率/快速率/平稳率/有效自控率(线) */
function renderHistoryTrend() {
  const data = historySnapshots.value;
  if (data.length === 0) return;

  const xLabels = data.map((s) => {
    const t = normalizeTime(s.tsStart) || normalizeTime(s.tsEnd);
    return t ? dayjs(t).format('MM-DD HH:mm') : '';
  });

  const textColor = themeColors.value.NEUTRAL;

  const selected = HISTORY_METRIC_OPTIONS.filter((o) =>
    selectedHistoryMetrics.value.includes(o.value),
  );

  renderHistoryChart({
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis',
      axisPointer: { type: 'cross' },
    },
    legend: {
      data: selected.map((o) => o.label),
      top: 0,
      textStyle: { color: textColor, fontSize: 12 },
    },
    grid: { top: 40, right: 24, bottom: 40, left: 48, containLabel: true },
    xAxis: {
      ...axisBase.value,
      type: 'category',
      data: xLabels,
    },
    yAxis: {
      ...axisBase.value,
      type: 'value',
      min: 0,
      max: 100,
    },
    series: selected.map((o) => ({
      name: o.label,
      type: o.kind,
      data: data.map((snap) => {
        const v = snap[o.value as keyof KpiSnapshotItem];
        return typeof v === 'number' ? v : null;
      }),
      ...(o.kind === 'bar'
        ? { barWidth: '40%', itemStyle: { color: historyMetricColor(o.value) } }
        : {
            smooth: true,
            symbol: 'circle',
            symbolSize: 4,
            lineStyle: { color: historyMetricColor(o.value), width: 2 },
            itemStyle: { color: historyMetricColor(o.value) },
          }),
    })),
  });
}

function handleHistoryWindowChange() {
  loadHistoryData();
}

// ===== 诊断 Modal =====

const diagModalVisible = ref(false);
const diagLoading = ref(false);
const diagRecord = ref<LoopPerformanceRow | null>(null);
const diagData = ref<DiagnosisApi.DiagnosisVisualizationData | null>(null);
const diagActiveTab = ref<'history' | 'overview' | 'spectrum' | 'time'>(
  'overview',
);

async function openDiagnosis(record: LoopPerformanceRow) {
  diagRecord.value = record;
  diagModalVisible.value = true;
  diagActiveTab.value = 'overview';
  diagData.value = null;
  if (!record.loopId) {
    message.warning('该记录缺少回路 ID，无法获取诊断数据');
    return;
  }
  diagLoading.value = true;
  try {
    diagData.value = await getDiagnosisVisualizationApi(record.loopId);
  } catch (error) {
    // 错误 toast 由 api/request.ts 拦截器统一弹出，Modal 内展示"暂无诊断可视化数据"
    console.error('加载诊断可视化数据失败:', error);
  } finally {
    diagLoading.value = false;
  }
}

// ===== 诊断 Modal - 评估历史 Tab（简化版 snapshots 表格） =====

const diagHistoryLoading = ref(false);
const diagHistorySnapshots = ref<KpiSnapshotItem[]>([]);
const diagHistoryTotal = ref(0);
const diagHistoryPage = ref(1);
const diagHistoryPageSize = ref(10);
const diagHistoryStatus = ref<KpiStatus | undefined>();
const diagHistoryConfidence = ref<ConfidenceLevel | undefined>();

async function loadDiagHistory() {
  if (!diagRecord.value?.loopId) return;
  diagHistoryLoading.value = true;
  try {
    const params: KpiSnapshotQueryParams = {
      loopId: diagRecord.value.loopId,
      latestOnly: false,
      page: diagHistoryPage.value,
      pageSize: diagHistoryPageSize.value,
    };
    if (diagHistoryStatus.value) params.status = diagHistoryStatus.value;
    if (diagHistoryConfidence.value)
      params.confidenceLevel = diagHistoryConfidence.value;
    const result = await getLoopSnapshotsApi(params);
    diagHistorySnapshots.value = result.items || [];
    diagHistoryTotal.value = result.total;
  } catch {
    diagHistorySnapshots.value = [];
    diagHistoryTotal.value = 0;
  } finally {
    diagHistoryLoading.value = false;
  }
}

const diagHistoryColumns: TableColumnsType = [
  {
    title: '时间窗',
    key: 'tsRange',
    width: 140,
  },
  {
    title: '综合评分',
    key: 'score',
    dataIndex: 'score',
    width: 90,
  },
  {
    title: '准确率',
    key: 'accuracyRate',
    dataIndex: 'accuracyRate',
    width: 80,
  },
  {
    title: '快速率',
    key: 'fastRate',
    dataIndex: 'fastRate',
    width: 80,
  },
  {
    title: '平稳率',
    key: 'steadyRate',
    dataIndex: 'steadyRate',
    width: 80,
  },
  {
    title: '有效自控率',
    key: 'effectiveAutoRate',
    dataIndex: 'effectiveAutoRate',
    width: 100,
  },
  {
    title: '可信度',
    key: 'confidenceLevel',
    dataIndex: 'confidenceLevel',
    width: 80,
  },
  {
    title: '状态',
    key: 'status',
    dataIndex: 'status',
    width: 90,
  },
];

function handleDiagHistoryTabChange() {
  if (
    diagActiveTab.value === 'history' &&
    diagHistorySnapshots.value.length === 0
  ) {
    loadDiagHistory();
  }
}

function handleDiagHistoryTableChange(p: TablePaginationConfig) {
  diagHistoryPage.value = p.current || 1;
  diagHistoryPageSize.value = p.pageSize || 10;
  loadDiagHistory();
}

// ===== 主题切换重渲图表 =====

watch(isDark, () => {
  nextTick(() => {
    if (historyModalVisible.value) renderHistoryTrend();
  });
});

// ===== 生命周期 =====

onMounted(async () => {
  await Promise.all([loadPlantNodes(), loadLoopMap(), loadGradingThresholds()]);
  // 深链支持：?loopId=xxx → 自动按回路编号过滤（回路工作台"历史"按钮入口，整改 B1）
  const loopIdQuery = route.query.loopId;
  if (typeof loopIdQuery === 'string' && loopIdQuery) {
    const tagName = loopMap.value.get(loopIdQuery)?.tagName;
    if (tagName) query.loopTagName = tagName;
  }
  loadList();
  loadStats();
});
</script>

<template>
  <Page>
    <!-- 顶部工具栏（统一工具栏） -->
    <ClpmPageToolbar
      title="回路性能"
      subtitle="按回路展示 KPI 评估结果，支持详情查看、历史趋势与诊断可视化。"
      :loading="loading"
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

    <!-- 筛选区 -->
    <div class="mb-4 mt-3 flex flex-wrap items-center gap-3">
      <TreeSelect
        v-model:value="query.plantNodeId"
        :tree-data="plantNodeTree"
        :field-names="{ label: 'name', value: 'id', children: 'children' }"
        placeholder="工厂模型节点"
        allow-clear
        tree-default-expand-all
        style="width: 220px"
        @change="handleSearch"
      />
      <Input
        v-model:value="query.loopTagName"
        placeholder="回路编号"
        allow-clear
        style="width: 180px"
        @press-enter="handleSearch"
        @change="
          ($event) =>
            !($event.target as HTMLInputElement).value && handleSearch()
        "
      />
      <Select
        v-model:value="query.controlType"
        :options="controlTypeOptions"
        placeholder="控制类型"
        allow-clear
        style="width: 140px"
        @change="handleSearch"
      />
      <Select
        v-model:value="query.status"
        :options="statusOptions"
        placeholder="评估状态"
        allow-clear
        style="width: 140px"
        @change="handleSearch"
      />
      <Select
        v-model:value="query.confidenceLevel"
        :options="confidenceOptions"
        placeholder="可信度"
        allow-clear
        style="width: 130px"
        @change="handleSearch"
      />
      <RangePicker
        v-model:value="query.timeRange"
        show-time
        format="MM-DD HH:mm"
        :placeholder="['开始时间', '结束时间']"
        style="width: 300px"
        @change="handleSearch"
      />
      <Button type="primary" @click="handleSearch">查询</Button>
    </div>

    <!-- 统计卡片区域（筛选区与列表区之间） -->
    <div class="mb-4">
      <Card :body-style="{ padding: '8px 16px' }" class="h-auto">
        <div class="flex items-center justify-between">
          <!-- 左：评估等级卡片组 -->
          <div class="flex items-center gap-1.5">
            <span class="text-xs text-gray-400 mr-1 whitespace-nowrap"
              >等级</span
            >
            <div
              class="flex items-center gap-1.5 px-2 py-1 rounded-lg cursor-pointer hover:opacity-80 transition-opacity whitespace-nowrap"
              role="button"
              tabindex="0"
              :aria-pressed="selectedGrade === null"
              :style="{
                backgroundColor:
                  selectedGrade === null
                    ? `${themeColors.NEUTRAL}15`
                    : `${themeColors.NEUTRAL}08`,
                borderLeft: `3px solid ${themeColors.NEUTRAL}`,
                borderBottom:
                  selectedGrade === null
                    ? `2px solid ${themeColors.NEUTRAL}`
                    : 'none',
              }"
              @click="handleGradeCardClick(null)"
              @keydown.enter="handleGradeCardClick(null)"
              @keydown.space.prevent="handleGradeCardClick(null)"
            >
              <span
                class="w-2 h-2 rounded-full"
                :style="{ backgroundColor: themeColors.NEUTRAL }"
              ></span>
              <span class="text-sm text-gray-600">全部</span>
              <span class="text-sm" :style="{ color: themeColors.NEUTRAL }">{{
                statsTotal
              }}</span>
            </div>
            <div
              v-for="grade in [1, 2, 3, 4, 5]"
              :key="grade"
              class="flex items-center gap-1.5 px-2 py-1 rounded cursor-pointer hover:opacity-80 transition-opacity whitespace-nowrap"
              role="button"
              tabindex="0"
              :aria-pressed="selectedGrade === grade"
              :style="{
                backgroundColor:
                  selectedGrade === grade
                    ? `${gradeCardColor(grade)}30`
                    : `${gradeCardColor(grade)}15`,
                borderLeft: `3px solid ${gradeCardColor(grade)}`,
                borderBottom:
                  selectedGrade === grade
                    ? `2px solid ${gradeCardColor(grade)}`
                    : 'none',
              }"
              @click="handleGradeCardClick(grade)"
              @keydown.enter="handleGradeCardClick(grade)"
              @keydown.space.prevent="handleGradeCardClick(grade)"
            >
              <span
                class="w-2 h-2 rounded-full"
                :style="{ backgroundColor: gradeCardColor(grade) }"
              ></span>
              <span class="text-sm text-gray-600">{{
                GRADE_LABEL_MAP[grade]
              }}</span>
              <span class="text-sm" :style="{ color: gradeCardColor(grade) }">
                {{ gradeStats[grade] || 0 }}
              </span>
            </div>
          </div>

          <!-- 右：性能概览 + 饼状图（优良/合格率由服务端等级分布推导） -->
          <div class="flex items-center gap-2">
            <div
              class="flex items-center gap-1.5 px-2 py-1 rounded whitespace-nowrap"
              :style="{
                backgroundColor: `${themeColors.SUCCESS}15`,
                borderLeft: `3px solid ${themeColors.SUCCESS}`,
              }"
            >
              <span
                class="w-2 h-2 rounded-full"
                :style="{ backgroundColor: themeColors.SUCCESS }"
              ></span>
              <span class="text-sm text-gray-600">优良率</span>
              <span class="text-sm" :style="{ color: themeColors.SUCCESS }"
                >{{ excellentRate }}%</span
              >
            </div>
            <div
              class="flex items-center gap-1.5 px-2 py-1 rounded whitespace-nowrap"
              :style="{
                backgroundColor: `${themeColors.ACCENT}15`,
                borderLeft: `3px solid ${themeColors.ACCENT}`,
              }"
            >
              <span
                class="w-2 h-2 rounded-full"
                :style="{ backgroundColor: themeColors.ACCENT }"
              ></span>
              <span class="text-sm text-gray-600">合格率</span>
              <span class="text-sm" :style="{ color: themeColors.ACCENT }"
                >{{ passRate }}%</span
              >
            </div>
            <EchartsUI ref="gradeChartRef" style="width: 56px; height: 56px" />
          </div>
        </div>
      </Card>
    </div>

    <!-- 回路性能列表 -->
    <ClpmDataCanvas
      :loading="loading"
      :error="loadError"
      :empty="!loading && !loadError && rows.length === 0"
      empty-text="暂无 KPI 评估快照"
      empty-reason="当前筛选条件下没有评估数据；可调整时间窗/筛选条件，或先在回路工作台发起评估。"
      @retry="loadList"
    >
      <Table
        :columns="columns"
        :data-source="rows"
        :loading="loading"
        :custom-row="rowClick"
        :row-selection="rowSelection"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: ['20', '50', '100'],
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="
          (record: LoopPerformanceRow) => `${record.loopId}-${record.tsStart}`
        "
        :scroll="{ x: 1250 }"
        :size="tableSize"
        @change="handleTableChange"
      >
        <template #headerCell="{ column }">
          <template v-if="column.key === 'score'">
            综合评分
            <ClpmInfoTip
              :term="KPI_TERM_EXPLANATIONS.compositeScore?.term"
              :tip="KPI_TERM_EXPLANATIONS.compositeScore?.short ?? ''"
              :detail="KPI_TERM_EXPLANATIONS.compositeScore?.detail"
            />
          </template>
          <!-- P1-04：准确率/快速率/平稳率/有效自控率列头 Tooltip 已随列移除，
               完整 8 大 KPI 解释集中在详情抽屉「8 大性能评估 KPI 指标」区 -->
        </template>
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'loopType'">
            <span v-if="(record as LoopPerformanceRow).loopType">
              {{
                LOOP_TYPE_LABEL_MAP[
                  (record as LoopPerformanceRow).loopType ?? 'OTHER'
                ] ?? '其他'
              }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'controlType'">
            <span v-if="(record as LoopPerformanceRow).controlType">
              {{
                CONTROL_TYPE_MAP[(record as LoopPerformanceRow).controlType!] ??
                (record as LoopPerformanceRow).controlType
              }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'controlMode'">
            <span v-if="(record as LoopPerformanceRow).controlMode">
              {{ (record as LoopPerformanceRow).controlMode }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'grade'">
            <Tag
              v-if="getGrade((record as LoopPerformanceRow).score)"
              :color="
                GRADE_COLOR_MAP[getGrade((record as LoopPerformanceRow).score)!]
              "
              class="m-0"
            >
              {{
                GRADE_LABEL_MAP[getGrade((record as LoopPerformanceRow).score)!]
              }}
            </Tag>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'score'">
            <span
              class="font-semibold"
              :style="{
                color: scoreColor((record as LoopPerformanceRow).score),
              }"
            >
              {{ formatNumber((record as LoopPerformanceRow).score) }}
            </span>
          </template>
          <template v-else-if="column.key === 'confidenceLevel'">
            <a
              v-if="(record as LoopPerformanceRow).confidenceLevel"
              class="confidence-cell-link"
              title="查看可信度详情"
              @click.stop="openConfidence(record as LoopPerformanceRow)"
            >
              <ConfidenceBadge
                :level="(record as LoopPerformanceRow).confidenceLevel!"
                :valid-rate="(record as LoopPerformanceRow).validRate"
              />
            </a>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'tsRange'">
            <span class="font-mono text-xs">
              {{
                formatTsRange(
                  (record as LoopPerformanceRow).tsStart,
                  (record as LoopPerformanceRow).tsEnd,
                )
              }}
            </span>
          </template>
          <template v-else-if="column.key === 'tsEnd'">
            <span class="font-mono text-xs">
              {{ formatShortTime((record as LoopPerformanceRow).tsEnd) }}
            </span>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag
              :color="
                STATUS_COLOR_MAP[(record as LoopPerformanceRow).status] ||
                'default'
              "
              class="m-0"
            >
              {{
                STATUS_LABEL_MAP[(record as LoopPerformanceRow).status] ||
                (record as LoopPerformanceRow).status
              }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'action'">
            <div
              class="flex items-center justify-center gap-1 whitespace-nowrap"
            >
              <Button
                type="link"
                size="small"
                @click.stop="openDetail(record as LoopPerformanceRow)"
              >
                <template #icon>
                  <IconifyIcon icon="ant-design:info-circle-outlined" />
                </template>
                详情
              </Button>
              <Button
                type="link"
                size="small"
                @click.stop="openHistory(record as LoopPerformanceRow)"
              >
                <template #icon>
                  <IconifyIcon icon="ant-design:history-outlined" />
                </template>
                历史
              </Button>
              <Button
                type="link"
                size="small"
                @click.stop="openDiagnosis(record as LoopPerformanceRow)"
              >
                <template #icon>
                  <IconifyIcon icon="ant-design:stethoscope-outlined" />
                </template>
                诊断
              </Button>
            </div>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 详情抽屉 -->
    <Drawer
      :open="drawerVisible"
      title="回路性能详情"
      placement="right"
      :width="720"
      :mask-closable="true"
      @close="closeDetail"
    >
      <template v-if="drawerRecord">
        <Tabs
          v-model:active-key="drawerTab"
          size="small"
          @change="handleDrawerTabChange"
        >
          <Tabs.TabPane key="detail" tab="性能详情">
            <!-- 回路基本信息 -->
            <div class="mb-2 text-sm font-medium">回路基本信息</div>
            <Descriptions
              :column="2"
              size="small"
              bordered
              :label-style="{ width: '120px' }"
            >
              <DescriptionsItem label="回路编号">
                {{ drawerRecord.loopTagName || '—' }}
              </DescriptionsItem>
              <DescriptionsItem label="回路名称">
                {{ drawerRecord.description || '—' }}
              </DescriptionsItem>
              <DescriptionsItem label="回路类型">
                {{
                  LOOP_TYPE_LABEL_MAP[drawerRecord.loopType ?? 'OTHER'] ?? '—'
                }}
              </DescriptionsItem>
              <DescriptionsItem label="控制类型">
                {{
                  drawerRecord.controlType
                    ? (CONTROL_TYPE_MAP[drawerRecord.controlType] ??
                      drawerRecord.controlType)
                    : '—'
                }}
              </DescriptionsItem>
              <DescriptionsItem label="控制方式">
                <Tag
                  v-if="drawerRecord.controlMode"
                  :color="modeLabelColor(drawerRecord.controlMode)"
                >
                  {{ drawerRecord.controlMode }}
                </Tag>
                <span v-else>—</span>
              </DescriptionsItem>
              <DescriptionsItem label="评估等级">
                <Tag
                  v-if="getGrade(drawerRecord.score)"
                  :color="GRADE_COLOR_MAP[getGrade(drawerRecord.score)!]"
                >
                  {{ GRADE_LABEL_MAP[getGrade(drawerRecord.score)!] }}
                </Tag>
                <span v-else>—</span>
              </DescriptionsItem>
              <DescriptionsItem label="PV 量程">
                {{
                  drawerRecord.loopMeta?.pvRange
                    ? `${drawerRecord.loopMeta.pvRange.min ?? '—'} ~ ${
                        drawerRecord.loopMeta.pvRange.max ?? '—'
                      }${drawerRecord.loopMeta.pvUnit ? ` ${drawerRecord.loopMeta.pvUnit}` : ''}`
                    : '—'
                }}
              </DescriptionsItem>
              <DescriptionsItem label="OP 量程">
                {{
                  drawerRecord.loopMeta?.opRange
                    ? `${drawerRecord.loopMeta.opRange.min ?? '—'} ~ ${
                        drawerRecord.loopMeta.opRange.max ?? '—'
                      }${drawerRecord.loopMeta.opUnit ? ` ${drawerRecord.loopMeta.opUnit}` : ''}`
                    : '—'
                }}
              </DescriptionsItem>
            </Descriptions>

            <!-- 8 大性能评估 KPI -->
            <div class="mb-2 mt-4 text-sm font-medium">
              8 大性能评估 KPI 指标
            </div>
            <Descriptions
              :column="2"
              size="small"
              bordered
              :label-style="{ width: '120px' }"
            >
              <DescriptionsItem>
                <template #label>
                  综合评分
                  <ClpmInfoTip
                    :term="KPI_TERM_EXPLANATIONS.compositeScore?.term"
                    :tip="KPI_TERM_EXPLANATIONS.compositeScore?.short ?? ''"
                    :detail="KPI_TERM_EXPLANATIONS.compositeScore?.detail"
                  />
                </template>
                <span
                  class="font-semibold"
                  :style="{ color: scoreColor(drawerRecord.score) }"
                >
                  {{ formatNumber(drawerRecord.score) }}
                </span>
              </DescriptionsItem>
              <DescriptionsItem>
                <template #label>
                  准确率
                  <ClpmInfoTip
                    :term="KPI_TERM_EXPLANATIONS.accuracyScore?.term"
                    :tip="KPI_TERM_EXPLANATIONS.accuracyScore?.short ?? ''"
                    :detail="KPI_TERM_EXPLANATIONS.accuracyScore?.detail"
                  />
                </template>
                {{ formatNumber(drawerRecord.accuracyRate, '%') }}
              </DescriptionsItem>
              <DescriptionsItem>
                <template #label>
                  快速率
                  <ClpmInfoTip
                    :term="KPI_TERM_EXPLANATIONS.responseScore?.term"
                    :tip="KPI_TERM_EXPLANATIONS.responseScore?.short ?? ''"
                    :detail="KPI_TERM_EXPLANATIONS.responseScore?.detail"
                  />
                </template>
                {{ formatNumber(drawerRecord.fastRate, '%') }}
              </DescriptionsItem>
              <DescriptionsItem>
                <template #label>
                  平稳率
                  <ClpmInfoTip
                    :term="KPI_TERM_EXPLANATIONS.steadyScore?.term"
                    :tip="KPI_TERM_EXPLANATIONS.steadyScore?.short ?? ''"
                    :detail="KPI_TERM_EXPLANATIONS.steadyScore?.detail"
                  />
                </template>
                {{ formatNumber(drawerRecord.steadyRate, '%') }}
              </DescriptionsItem>
              <DescriptionsItem>
                <template #label>
                  有效自控率
                  <ClpmInfoTip
                    :term="KPI_TERM_EXPLANATIONS.effectiveAutoRate?.term"
                    :tip="KPI_TERM_EXPLANATIONS.effectiveAutoRate?.short ?? ''"
                    :detail="KPI_TERM_EXPLANATIONS.effectiveAutoRate?.detail"
                  />
                </template>
                {{ formatNumber(drawerRecord.effectiveAutoRate, '%') }}
              </DescriptionsItem>
              <DescriptionsItem label="自控率">
                {{ formatNumber(drawerRecord.autoModeRate, '%') }}
              </DescriptionsItem>
              <DescriptionsItem label="好值率">
                {{ formatNumber(drawerRecord.goodValueRate, '%') }}
              </DescriptionsItem>
              <DescriptionsItem label="振荡率">
                {{ formatNumber(drawerRecord.oscillationRate, '%') }}
              </DescriptionsItem>
            </Descriptions>

            <!-- 诊断与扩展指标（不参与评分） -->
            <div class="mb-2 mt-4 text-sm font-medium">诊断与扩展指标</div>
            <Descriptions
              :column="2"
              size="small"
              bordered
              :label-style="{ width: '120px' }"
            >
              <DescriptionsItem label="饱和率">
                {{ formatNumber(drawerRecord.saturationRate, '%') }}
              </DescriptionsItem>
              <DescriptionsItem label="输出跳变率">
                {{ formatNumber(drawerRecord.outputTravelIndex) }}
              </DescriptionsItem>
              <DescriptionsItem label="阀门粘滞指数">
                {{ formatNumber(drawerRecord.stictionIndex) }}
              </DescriptionsItem>
              <DescriptionsItem label="理想稳定时间">
                {{ formatNumber(drawerRecord.idealSettlingTime, 's') }}
              </DescriptionsItem>
              <DescriptionsItem label="稳定时间" :span="2">
                {{ formatNumber(drawerRecord.settlingTime, 's') }}
              </DescriptionsItem>
            </Descriptions>

            <!-- 可信度 + 时间窗口 + 评估时间 -->
            <div class="mb-2 mt-4 text-sm font-medium">评估信息</div>
            <Descriptions
              :column="2"
              size="small"
              bordered
              :label-style="{ width: '120px' }"
            >
              <DescriptionsItem label="可信度">
                <Badge
                  v-if="drawerRecord.confidenceLevel"
                  :color="CONFIDENCE_COLOR_MAP[drawerRecord.confidenceLevel]"
                  :text="CONFIDENCE_LABEL_MAP[drawerRecord.confidenceLevel]"
                />
                <span v-else>—</span>
              </DescriptionsItem>
              <DescriptionsItem label="评估状态">
                <Tag
                  :color="STATUS_COLOR_MAP[drawerRecord.status] || 'default'"
                >
                  {{
                    STATUS_LABEL_MAP[drawerRecord.status] || drawerRecord.status
                  }}
                </Tag>
              </DescriptionsItem>
              <DescriptionsItem label="时间窗口">
                <span class="font-mono text-xs">
                  {{ formatTsRange(drawerRecord.tsStart, drawerRecord.tsEnd) }}
                </span>
              </DescriptionsItem>
              <DescriptionsItem label="评估时间">
                <span class="font-mono text-xs">
                  {{ formatTime(drawerRecord.tsEnd) }}
                </span>
              </DescriptionsItem>
              <DescriptionsItem label="有效数据率">
                {{ formatRatio(drawerRecord.validRate) }}
              </DescriptionsItem>
              <DescriptionsItem label="算法版本">
                {{ drawerRecord.algorithmVersion || '—' }}
              </DescriptionsItem>
            </Descriptions>

            <!-- 历史快照子表（该回路最近 10 条评估记录） -->
            <div class="mb-2 mt-4 text-sm font-medium">
              历史快照（最近 10 条）
            </div>
            <Table
              :columns="diagHistoryColumns"
              :data-source="drawerHistory"
              :loading="drawerHistoryLoading"
              :pagination="false"
              row-key="tsStart"
              size="small"
              :scroll="{ x: 680 }"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'tsRange'">
                  <span class="font-mono text-xs">
                    {{
                      formatTsRange(
                        (record as KpiSnapshotItem).tsStart,
                        (record as KpiSnapshotItem).tsEnd,
                      )
                    }}
                  </span>
                </template>
                <template v-else-if="column.key === 'score'">
                  <span
                    class="font-semibold"
                    :style="{
                      color: scoreColor((record as KpiSnapshotItem).score),
                    }"
                  >
                    {{ formatNumber((record as KpiSnapshotItem).score) }}
                  </span>
                </template>
                <template
                  v-else-if="
                    (
                      [
                        'accuracyRate',
                        'fastRate',
                        'steadyRate',
                        'effectiveAutoRate',
                      ] as string[]
                    ).includes(column.key as string)
                  "
                >
                  <span class="font-mono text-xs">
                    {{
                      formatNumber(
                        getMetricValue(
                          record as KpiSnapshotItem,
                          column.dataIndex as string,
                        ),
                        '%',
                      )
                    }}
                  </span>
                </template>
                <template v-else-if="column.key === 'confidenceLevel'">
                  <Badge
                    v-if="(record as KpiSnapshotItem).confidenceLevel"
                    :color="
                      CONFIDENCE_COLOR_MAP[
                        (record as KpiSnapshotItem).confidenceLevel!
                      ]
                    "
                    :text="
                      CONFIDENCE_LABEL_MAP[
                        (record as KpiSnapshotItem).confidenceLevel!
                      ]
                    "
                  />
                  <span v-else class="text-gray-400">—</span>
                </template>
                <template v-else-if="column.key === 'status'">
                  <Tag
                    :color="
                      STATUS_COLOR_MAP[(record as KpiSnapshotItem).status] ||
                      'default'
                    "
                    class="m-0"
                  >
                    {{
                      STATUS_LABEL_MAP[(record as KpiSnapshotItem).status] ||
                      (record as KpiSnapshotItem).status
                    }}
                  </Tag>
                </template>
              </template>
            </Table>
          </Tabs.TabPane>

          <!-- 综合评估 Tab -->
          <Tabs.TabPane key="comprehensive" tab="综合评估">
            <Row :gutter="[12, 12]">
              <!-- Row 1 左：核心指标雷达图 -->
              <Col :span="12">
                <Card :bordered="false" class="chart-card">
                  <RadarChart
                    :accuracy="drawerRecord.accuracyRate"
                    :fast="drawerRecord.fastRate"
                    :stability="drawerRecord.steadyRate"
                  />
                </Card>
              </Col>
              <!-- Row 1 右：综合评分分解 -->
              <!-- 默认权重 40/30/30；可通过 getLoopTypeWeightsApi() 按控制类型获取实际配置权重 -->
              <Col :span="12">
                <Card :bordered="false" class="chart-card">
                  <ScoreBreakdown
                    :score="drawerRecord.score"
                    :accuracy="drawerRecord.accuracyRate"
                    :fast="drawerRecord.fastRate"
                    :stability="drawerRecord.steadyRate"
                    :effective-auto-rate="drawerRecord.effectiveAutoRate"
                    :weight-a="40"
                    :weight-f="30"
                    :weight-s="30"
                  />
                </Card>
              </Col>
              <!-- Row 2 左：PV-OP 散点图（复用诊断组件，需懒加载 diagData） -->
              <Col :span="12">
                <Card :bordered="false" class="chart-card">
                  <ScatterChart
                    v-if="drawerDiagData"
                    :data="drawerDiagData.scatterPlot"
                  />
                  <div
                    v-else
                    class="flex h-full items-center justify-center text-gray-400"
                  >
                    {{ drawerDiagLoading ? '加载中...' : '暂无散点数据' }}
                  </div>
                </Card>
              </Col>
              <!-- Row 2 右：信号统计与阀门诊断 -->
              <Col :span="12">
                <Card :bordered="false" class="chart-card">
                  <StatisticsBarChart
                    :pv-mean="drawerRecord.pvMean"
                    :pv-std="drawerRecord.pvStd"
                    :sp-mean="drawerRecord.spMean"
                    :sp-std="drawerRecord.spStd"
                    :op-mean="drawerRecord.opMean"
                    :op-std="drawerRecord.opStd"
                    :valve-linearity="drawerRecord.valveLinearity"
                    :valve-nonlinearity="drawerRecord.valveNonlinearity"
                    :valve-op-min="drawerRecord.valveOpMin"
                    :valve-op-max="drawerRecord.valveOpMax"
                    :oscillation-amplitude="drawerRecord.oscillationAmplitude"
                    :setpoint-crossing-count="
                      drawerRecord.setpointCrossingCount
                    "
                  />
                </Card>
              </Col>
            </Row>
          </Tabs.TabPane>
        </Tabs>
      </template>
    </Drawer>

    <!-- 可信度详情抽屉 -->
    <Drawer
      :open="confDrawerVisible"
      :title="`可信度详情 - ${confDrawerRecord?.loopTagName ?? ''}`"
      placement="right"
      :width="560"
      :mask-closable="true"
      @close="closeConfidence"
    >
      <Spin :spinning="confDrawerLoading">
        <template v-if="confDetail">
          <!-- 评估概要 -->
          <div class="mb-2 text-sm font-medium">评估概要</div>
          <Descriptions
            :column="2"
            size="small"
            bordered
            :label-style="{ width: '110px' }"
          >
            <DescriptionsItem label="最新评估时间">
              <span class="font-mono text-xs">
                {{ formatTime(confDetail.evalTime) }}
              </span>
            </DescriptionsItem>
            <DescriptionsItem label="数据源时间区间">
              <span class="font-mono text-xs">
                {{
                  formatTsRange(confDetail.dataTsStart, confDetail.dataTsEnd)
                }}
              </span>
            </DescriptionsItem>
            <DescriptionsItem label="评估状态">
              <Tag
                :color="STATUS_COLOR_MAP[confDetail.status] || 'default'"
                class="m-0"
              >
                {{ STATUS_LABEL_MAP[confDetail.status] || confDetail.status }}
              </Tag>
            </DescriptionsItem>
            <DescriptionsItem label="综合评分">
              <span
                class="font-semibold"
                :style="{ color: scoreColor(confDetail.score) }"
              >
                {{ formatNumber(confDetail.score) }}
              </span>
            </DescriptionsItem>
            <DescriptionsItem label="可信度">
              <Badge
                v-if="confDetail.confidenceLevel"
                :color="CONFIDENCE_COLOR_MAP[confDetail.confidenceLevel]"
                :text="CONFIDENCE_LABEL_MAP[confDetail.confidenceLevel]"
              />
              <span v-else>—</span>
            </DescriptionsItem>
            <DescriptionsItem label="有效数据率">
              {{ formatRatio(confDetail.validRate) }}
            </DescriptionsItem>
            <DescriptionsItem label="算法版本" :span="2">
              {{ confDetail.algorithmVersion || '—' }}
            </DescriptionsItem>
          </Descriptions>

          <!-- 12 子指标明细（可信度统一 Phase 2：仅展示计算值，可信度统一为回路级） -->
          <div class="mb-2 mt-4 text-sm font-medium">子指标数值（3+1+8）</div>
          <Table
            :columns="confMetricColumns"
            :data-source="confMetricRows"
            :pagination="false"
            row-key="key"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'value'">
                <span class="font-mono text-xs">
                  {{ formatNumber(record.value, record.unit) }}
                </span>
              </template>
            </template>
          </Table>
        </template>
        <div
          v-else-if="!confDrawerLoading"
          class="py-12 text-center text-gray-400"
        >
          暂无评估记录
        </div>
      </Spin>
    </Drawer>

    <!-- 历史 Modal -->
    <Modal
      v-model:open="historyModalVisible"
      :title="`历史趋势 - ${historyRecord?.loopTagName ?? ''}`"
      width="900px"
      :footer="null"
      destroy-on-close
    >
      <Spin :spinning="historyLoading">
        <div class="space-y-3">
          <!-- 时间维度切换 -->
          <div class="flex items-center gap-2">
            <span class="text-sm text-gray-500">时间范围：</span>
            <RadioGroup
              v-model:value="historyWindow"
              :options="historyWindowOptions"
              option-type="button"
              button-style="solid"
              size="small"
              @change="handleHistoryWindowChange"
            />
          </div>

          <!-- 指标多选（整改 F3：≤5 个指标对比） -->
          <div class="flex items-center gap-2">
            <span class="text-sm text-gray-500">对比指标：</span>
            <CheckboxGroup
              :value="selectedHistoryMetrics"
              :options="
                HISTORY_METRIC_OPTIONS.map((o) => ({
                  label: o.label,
                  value: o.value,
                }))
              "
              @change="handleHistoryMetricChange"
            />
          </div>

          <!-- 趋势图 -->
          <div v-if="historySnapshots.length > 0">
            <EchartsUI ref="historyChartRef" height="380px" />
          </div>
          <div v-else class="py-12 text-center text-gray-400">
            暂无历史趋势数据
          </div>
        </div>
      </Spin>
    </Modal>

    <!-- 诊断 Modal（P3-19：max-width 限制避免超宽屏过大） -->
    <Modal
      v-model:open="diagModalVisible"
      :title="`诊断可视化 - ${diagRecord?.loopTagName ?? ''}`"
      width="90%"
      :wrap-style="{ maxWidth: '1200px', margin: '0 auto' }"
      :footer="null"
      destroy-on-close
    >
      <Spin :spinning="diagLoading">
        <div v-if="diagData" class="space-y-3">
          <!-- 诊断摘要 -->
          <Card :bordered="false" size="small">
            <Row :gutter="[16, 16]">
              <Col :span="6">
                <div class="summary-card">
                  <div class="summary-label">综合评分</div>
                  <div
                    class="summary-value"
                    :style="{ color: scoreColor(diagData.compositeScore) }"
                  >
                    {{ diagData.compositeScore?.toFixed(1) ?? '—' }}
                  </div>
                </div>
              </Col>
              <Col :span="6">
                <div class="summary-card">
                  <div class="summary-label">融合置信度</div>
                  <div class="summary-value">
                    {{ ((diagData.fusedConfidence ?? 0) * 100).toFixed(1) }}%
                  </div>
                </div>
              </Col>
              <Col :span="12">
                <div class="summary-card">
                  <div class="summary-label">诊断标签</div>
                  <div class="summary-tags">
                    <Tag
                      v-for="label in diagData.diagnosisLabels"
                      :key="label.label"
                      :color="
                        (label.label &&
                          DIAGNOSIS_LABEL_COLOR_MAP[
                            label.label as keyof typeof DIAGNOSIS_LABEL_COLOR_MAP
                          ]) ||
                        'default'
                      "
                    >
                      {{
                        label.label &&
                        DIAGNOSIS_LABEL_NAME_MAP[
                          label.label as keyof typeof DIAGNOSIS_LABEL_NAME_MAP
                        ]
                      }}
                      ({{ (label.confidence * 100).toFixed(0) }}%)
                    </Tag>
                  </div>
                </div>
              </Col>
            </Row>
          </Card>

          <!-- 4 个 Tab 页 -->
          <Tabs
            v-model:active-key="diagActiveTab"
            @change="handleDiagHistoryTabChange"
          >
            <!-- Tab 1: 诊断概览 -->
            <Tabs.TabPane key="overview" tab="诊断概览">
              <Row :gutter="[12, 12]">
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <QualityTimelineChart :data="diagData.qualityTimeline" />
                  </Card>
                </Col>
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <SaturationChart :data="diagData.saturationAnalysis" />
                  </Card>
                </Col>
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <SlowResponseCard :data="diagData.slowResponse" />
                  </Card>
                </Col>
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <ChoudhuryCard :data="diagData.choudhury" />
                  </Card>
                </Col>
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <IaeCard :data="diagData.iaeAnalysis" />
                  </Card>
                </Col>
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <KanoCard :data="diagData.kano" />
                  </Card>
                </Col>
              </Row>
            </Tabs.TabPane>

            <!-- Tab 2: 频谱分析 -->
            <Tabs.TabPane key="spectrum" tab="频谱分析">
              <Card :bordered="false">
                <SpectrumChart :data="diagData.spectrum" />
              </Card>
            </Tabs.TabPane>

            <!-- Tab 3: 时域分析 -->
            <Tabs.TabPane key="time" tab="时域分析">
              <Row :gutter="[16, 16]">
                <Col :span="12">
                  <Card :bordered="false" class="chart-card">
                    <StepResponseChart :data="diagData.stepResponse" />
                  </Card>
                </Col>
                <Col :span="12">
                  <Card :bordered="false" class="chart-card">
                    <CusumChart :data="diagData.cusumAnalysis" />
                  </Card>
                </Col>
                <Col :span="12">
                  <Card :bordered="false" class="chart-card">
                    <ScatterChart :data="diagData.scatterPlot" />
                  </Card>
                </Col>
              </Row>
            </Tabs.TabPane>

            <!-- Tab 4: 评估历史 -->
            <Tabs.TabPane key="history" tab="评估历史">
              <!-- 筛选区 -->
              <div class="mb-3 flex flex-wrap items-center gap-2">
                <Select
                  v-model:value="diagHistoryStatus"
                  :options="statusOptions"
                  placeholder="状态"
                  allow-clear
                  style="width: 130px"
                  @change="loadDiagHistory"
                />
                <Select
                  v-model:value="diagHistoryConfidence"
                  placeholder="可信度"
                  allow-clear
                  style="width: 130px"
                  @change="loadDiagHistory"
                >
                  <Select.Option value="A">A 优秀</Select.Option>
                  <Select.Option value="B">B 良好</Select.Option>
                  <Select.Option value="C">C 一般</Select.Option>
                  <Select.Option value="D">D 较差</Select.Option>
                  <Select.Option value="E">E 不足</Select.Option>
                </Select>
                <Button type="primary" size="small" @click="loadDiagHistory">
                  刷新
                </Button>
              </div>

              <Table
                :columns="diagHistoryColumns"
                :data-source="diagHistorySnapshots"
                :loading="diagHistoryLoading"
                :pagination="{
                  current: diagHistoryPage,
                  pageSize: diagHistoryPageSize,
                  total: diagHistoryTotal,
                  showSizeChanger: true,
                  showTotal: (t: number) => `共 ${t} 条`,
                }"
                row-key="tsStart"
                size="small"
                :scroll="{ x: 800 }"
                @change="handleDiagHistoryTableChange"
              >
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'tsRange'">
                    <span class="font-mono text-xs">
                      {{
                        formatTsRange(
                          (record as KpiSnapshotItem).tsStart,
                          (record as KpiSnapshotItem).tsEnd,
                        )
                      }}
                    </span>
                  </template>
                  <template v-else-if="column.key === 'score'">
                    <span
                      class="font-semibold"
                      :style="{
                        color: scoreColor((record as KpiSnapshotItem).score),
                      }"
                    >
                      {{ formatNumber((record as KpiSnapshotItem).score) }}
                    </span>
                  </template>
                  <template
                    v-else-if="
                      (
                        [
                          'accuracyRate',
                          'fastRate',
                          'steadyRate',
                          'effectiveAutoRate',
                        ] as string[]
                      ).includes(column.key as string)
                    "
                  >
                    <span class="font-mono text-xs">
                      {{
                        formatNumber(
                          getMetricValue(
                            record as KpiSnapshotItem,
                            column.dataIndex as string,
                          ),
                          '%',
                        )
                      }}
                    </span>
                  </template>
                  <template v-else-if="column.key === 'confidenceLevel'">
                    <Badge
                      v-if="(record as KpiSnapshotItem).confidenceLevel"
                      :color="
                        CONFIDENCE_COLOR_MAP[
                          (record as KpiSnapshotItem).confidenceLevel!
                        ]
                      "
                      :text="
                        CONFIDENCE_LABEL_MAP[
                          (record as KpiSnapshotItem).confidenceLevel!
                        ]
                      "
                    />
                    <span v-else class="text-gray-400">—</span>
                  </template>
                  <template v-else-if="column.key === 'status'">
                    <Tag
                      :color="
                        STATUS_COLOR_MAP[(record as KpiSnapshotItem).status] ||
                        'default'
                      "
                      class="m-0"
                    >
                      {{
                        STATUS_LABEL_MAP[(record as KpiSnapshotItem).status] ||
                        (record as KpiSnapshotItem).status
                      }}
                    </Tag>
                  </template>
                </template>
              </Table>
            </Tabs.TabPane>
          </Tabs>
        </div>
        <div v-else class="py-12 text-center text-gray-400">
          暂无诊断可视化数据
        </div>
      </Spin>
    </Modal>

    <!-- AI 性能分析右抽屉（工具栏 AI 图标触发，§5.2） -->
    <ClpmAiDrawer
      v-model:open="aiDrawerOpen"
      scene="performance"
      :loop-id="aiLoopId"
    />
  </Page>
</template>

<style scoped>
.summary-card {
  padding: 12px;
  background: var(--ant-color-fill-quaternary, rgb(0 0 0 / 4%));
  border-radius: 8px;
}

.summary-label {
  margin-bottom: 4px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.summary-value {
  font-size: 24px;
  font-weight: 700;
  color: hsl(var(--foreground));
}

.summary-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.mini-card {
  height: 200px;
}

.chart-card {
  height: 300px;
}

:deep(.ant-table-cell) {
  white-space: nowrap;

  /* 隐藏单元格竖线（列分隔线） */
  border-inline-end-width: 0 !important;
}

:deep(.ant-table-cell::before) {
  display: none !important;
}

:deep(.ant-table-cell-wrap-all) {
  white-space: normal;
}

/* 表头文字居中 */
:deep(.ant-table-thead .ant-table-cell) {
  text-align: center !important;
}

.confidence-cell-link {
  display: inline-block;
  cursor: pointer;
  transition: opacity 0.2s;
}

.confidence-cell-link:hover {
  opacity: 0.75;
}
</style>
