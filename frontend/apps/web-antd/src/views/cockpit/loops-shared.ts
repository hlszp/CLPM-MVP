/**
 * 驾驶舱页2 回路状态墙共享工具（方案 11 §5.4 / §6）
 *
 * - 五档等级：score → 档位（阈值来自 /configs/grading-thresholds，未加载降级国标默认），
 *   颜色统一引用 theme.css 的 --ck-grade-* 变量名（HTML 直接 var()，ECharts 经
 *   readCockpitColors 解析为具体色值）；
 * - 六维口径（§5.4）：自控率/平稳率/准确率/快速率/好值率/有效率；
 * - 控制模式：MODE 标准值 0~4 → 五档中文标签/筛选键（对齐 backend constants/mode.py）。
 */
import type { CockpitApi } from '#/api/cockpit';
import type { LoopApi } from '#/api/loop';
import type { MetricApi } from '#/api/metric';
import type { CockpitModeKey } from '#/store/cockpit';

// ---------------------------------------------------------------------------
// 五档等级
// ---------------------------------------------------------------------------

export interface GradeInfo {
  /** CSS 变量名（如 --ck-grade-excellent），用于 var() 或 getComputedStyle 解析 */
  colorVar: string;
  key: CockpitApi.GradeKey;
  label: string;
}

/** 档位名 → CSS 变量 + 兜底中文名 */
const GRADE_META: Record<CockpitApi.GradeKey, { colorVar: string; label: string }> =
  {
    EXCELLENT: { colorVar: '--ck-grade-excellent', label: '优秀' },
    GOOD: { colorVar: '--ck-grade-good', label: '良好' },
    FAIR: { colorVar: '--ck-grade-fair', label: '合格' },
    WARNING: { colorVar: '--ck-grade-warning', label: '警告' },
    POOR: { colorVar: '--ck-grade-poor', label: '不合格' },
  };

/** 国标默认阈值（GB/T 44693.2-2024 §6.3），配置未加载时降级使用 */
const DEFAULT_THRESHOLDS: MetricApi.GradingThresholdItem[] = [
  { level: 1, name: 'EXCELLENT', minScore: 90, maxScore: 100 },
  { level: 2, name: 'GOOD', minScore: 80, maxScore: 90 },
  { level: 3, name: 'FAIR', minScore: 60, maxScore: 80 },
  { level: 4, name: 'WARNING', minScore: 40, maxScore: 60 },
  { level: 5, name: 'POOR', minScore: 0, maxScore: 40 },
];

/**
 * 评分 → 五档等级；无评分（数据不足）返回 null（中性，不映射为不合格）
 */
export function resolveGrade(
  score: null | number | undefined,
  thresholds?: MetricApi.GradingThresholdItem[] | null,
): GradeInfo | null {
  if (score === null || score === undefined || Number.isNaN(score)) return null;
  const effective =
    thresholds && thresholds.length > 0 ? thresholds : DEFAULT_THRESHOLDS;
  const sorted = [...effective].toSorted((a, b) => b.minScore - a.minScore);
  const hit = sorted.find((t) => score >= t.minScore) ?? sorted.at(-1);
  if (!hit) return null;
  const meta =
    GRADE_META[hit.name as CockpitApi.GradeKey] ?? GRADE_META.POOR;
  return {
    key: hit.name as CockpitApi.GradeKey,
    label: hit.label ?? meta.label,
    colorVar: meta.colorVar,
  };
}

// ---------------------------------------------------------------------------
// 六维口径（§5.4，0~100 标尺；雷达中心=综合评分）
// ---------------------------------------------------------------------------

/** 六维元数据（ camelCase 键，供雷达组件与最差维度计算共用） */
export const SIX_DIMS = [
  { key: 'autoModeRate', label: '自控率' },
  { key: 'steadyRate', label: '平稳率' },
  { key: 'accuracyRate', label: '准确率' },
  { key: 'fastRate', label: '快速率' },
  { key: 'goodValueRate', label: '好值率' },
  { key: 'effectiveAutoRate', label: '有效率' },
] as const;

export type SixDimKey = (typeof SIX_DIMS)[number]['key'];

/** 六维得分集（null=该维度无数据） */
export type SixDimValues = Record<SixDimKey, null | number>;

/** 回路监控 kpiSummary（snake_case）→ 六维 */
export function sixDimsFromKpiSummary(
  kpi?: LoopApi.KpiSummary | null,
): null | SixDimValues {
  if (!kpi) return null;
  return {
    autoModeRate: kpi.auto_mode_rate ?? null,
    steadyRate: kpi.steady_rate ?? null,
    accuracyRate: kpi.accuracy_rate ?? null,
    fastRate: kpi.fast_rate ?? null,
    goodValueRate: kpi.good_value_rate ?? null,
    effectiveAutoRate: kpi.effective_auto_rate ?? null,
  };
}

/** 节点快照 / 节点监控快照（camelCase）→ 六维 */
export function sixDimsFromNodeSnapshot(
  snap?: MetricApi.NodeSnapshotItem | null,
): null | SixDimValues {
  if (!snap) return null;
  return {
    autoModeRate: snap.autoModeRate ?? null,
    steadyRate: snap.steadyRate ?? null,
    accuracyRate: snap.accuracyRate ?? null,
    fastRate: snap.fastRate ?? null,
    goodValueRate: snap.goodValueRate ?? null,
    effectiveAutoRate: snap.effectiveAutoRate ?? null,
  };
}

/** 最差维度标签（如「平稳率 46」）；全部 ≥60 或无数据返回 null（无异常不显示） */
export function worstDim(dims: null | SixDimValues): null | string {
  if (!dims) return null;
  let worstLabel: null | string = null;
  let worstValue = Number.POSITIVE_INFINITY;
  for (const dim of SIX_DIMS) {
    const v = dims[dim.key];
    if (typeof v === 'number' && !Number.isNaN(v) && v < worstValue) {
      worstValue = v;
      worstLabel = dim.label;
    }
  }
  if (!worstLabel || worstValue >= 60) return null;
  return `${worstLabel} ${Math.round(worstValue)}`;
}

// ---------------------------------------------------------------------------
// 控制模式（MODE 标准值 0~4）
// ---------------------------------------------------------------------------

/** MODE 数值 → 五档中文标签（默认映射口径） */
export const MODE_ZH_BY_VALUE: Record<number, string> = {
  0: '手动',
  1: '自动',
  2: '串级',
  3: '远程',
  4: '先控',
};

/** MODE 数值 → 筛选键 */
export const MODE_KEY_BY_VALUE: Record<number, CockpitModeKey> = {
  0: 'MANUAL',
  1: 'AUTO',
  2: 'CAS',
  3: 'REMOTE',
  4: 'APC',
};

/** 模式筛选键 → 中文标签 */
export const MODE_KEY_ZH: Record<CockpitModeKey, string> = {
  AUTO: '自动',
  CAS: '串级',
  REMOTE: '远程',
  MANUAL: '手动',
  APC: '先控',
};

/** 模式筛选键固定展示顺序（自动/串级/远程/手动/先控） */
export const MODE_KEY_ORDER: CockpitModeKey[] = [
  'AUTO',
  'CAS',
  'REMOTE',
  'MANUAL',
  'APC',
];

/** 回路实时值 → 模式筛选键（无实时值返回 null） */
export function modeBucket(mode: null | number | undefined): CockpitModeKey | null {
  if (mode === null || mode === undefined) return null;
  return MODE_KEY_BY_VALUE[Math.trunc(mode)] ?? null;
}

/** 回路实时值 → 中文模式标签（优先数值映射；回退 modeLabel） */
export function modeZhLabel(
  mode: null | number | undefined,
  modeLabel?: null | string,
): string {
  if (mode !== null && mode !== undefined) {
    const zh = MODE_ZH_BY_VALUE[Math.trunc(mode)];
    if (zh) return zh;
  }
  if (modeLabel === 'Auto') return '自动';
  if (modeLabel === 'Manual') return '手动';
  if (modeLabel === 'Cascade') return '串级';
  return '—';
}

// ---------------------------------------------------------------------------
// 回路卡视图模型（loops.vue 组装，loop-card.vue 消费）
// ---------------------------------------------------------------------------

/** 回路卡视图模型；live 直接引用 MonitorListItem.currentValues（WS 局部更新即响应） */
export interface LoopCardModel {
  description: string;
  /** 六维得分（无 KPI 快照为 null） */
  dims: null | SixDimValues;
  grade: GradeInfo | null;
  /** 实时值引用（PV/SP/OP/MODE 随 WS 实时刷新） */
  live: LoopApi.MonitorCurrentValues;
  loopId: string;
  /** 模式筛选键（无实时值为 null） */
  mode: CockpitModeKey | null;
  modeZh: string;
  /** 综合评分（kpiSummary.composite_score 优先，回退列表 score） */
  score: null | number;
  /** 较昨日评分增量（劣化排序依据；null 排最后） */
  scoreDelta: null | number;
  tagName: string;
  unitName: string;
  /** 最差维度标签（如「平稳率 46」；无异常为 null 不显示） */
  worst: null | string;
}

/** MonitorListItem → 回路卡视图模型 */
export function toLoopCardModel(
  item: LoopApi.MonitorListItem,
  thresholds?: MetricApi.GradingThresholdItem[] | null,
): LoopCardModel {
  const dims = sixDimsFromKpiSummary(item.kpiSummary);
  const score = item.kpiSummary?.composite_score ?? item.score ?? null;
  const modeValue = item.currentValues?.mode ?? null;
  return {
    loopId: item.loopId,
    tagName: item.tagName,
    description: item.description ?? '',
    unitName: item.unitName ?? '',
    live: item.currentValues,
    score,
    grade: resolveGrade(score, thresholds),
    dims,
    worst: worstDim(dims),
    mode: modeBucket(modeValue),
    modeZh: modeZhLabel(modeValue, item.currentValues?.modeLabel),
    scoreDelta: item.scoreDelta ?? null,
  };
}

// ---------------------------------------------------------------------------
// ECharts 颜色解析（canvas 不支持 var()，从 .cockpit-root 解析具体色值）
// ---------------------------------------------------------------------------

export interface CockpitChartColors {
  accent: string;
  border: string;
  gradeExcellent: string;
  gradeFair: string;
  gradeGood: string;
  gradePoor: string;
  gradeWarning: string;
  pvLine: string;
  opLine: string;
  text: string;
  text2: string;
  text3: string;
}

const CHART_VAR_MAP: Record<keyof CockpitChartColors, string> = {
  text: '--ck-text',
  text2: '--ck-text-2',
  text3: '--ck-text-3',
  border: '--ck-border',
  accent: '--ck-accent',
  gradeExcellent: '--ck-grade-excellent',
  gradeGood: '--ck-grade-good',
  gradeFair: '--ck-grade-fair',
  gradeWarning: '--ck-grade-warning',
  gradePoor: '--ck-grade-poor',
  pvLine: '--ck-accent',
  opLine: '--ck-grade-fair',
};

/** 从组件所在 .cockpit-root 容器解析当前主题下的具体色值 */
export function readCockpitColors(el?: HTMLElement | null): CockpitChartColors {
  const root = el?.closest('.cockpit-root') ?? document.querySelector('.cockpit-root');
  const styles = root ? getComputedStyle(root) : null;
  const out = {} as CockpitChartColors;
  for (const [key, varName] of Object.entries(CHART_VAR_MAP)) {
    out[key as keyof CockpitChartColors] =
      styles?.getPropertyValue(varName).trim() || '#93a7c4';
  }
  return out;
}

/** 按 CSS 变量名取解析后的色值（如 grade colorVar → hex） */
export function resolveCssVar(
  el: HTMLElement | null | undefined,
  varName: string,
  fallback = '#93a7c4',
): string {
  const root = el?.closest('.cockpit-root') ?? document.querySelector('.cockpit-root');
  if (!root) return fallback;
  return getComputedStyle(root).getPropertyValue(varName).trim() || fallback;
}
