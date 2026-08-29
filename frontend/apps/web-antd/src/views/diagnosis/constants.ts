/**
 * 诊断模块展示常量（纯数据，无请求依赖，便于复用与测试）。
 *
 * 色彩对齐《色彩约定表 v1.0》工业语义色。
 */
import type { DiagnosisApi } from '#/api/diagnosis';

export interface CategoryMeta {
  label: string;
  color: string;
  direction: string;
}

export const CATEGORY_META: Record<DiagnosisApi.Category, CategoryMeta> = {
  TUNING: { label: '参数问题', color: '#0d6efd', direction: '重新整定参数' },
  VALVE: {
    label: '阀门/执行机构问题',
    color: '#b45309',
    direction: '检修/更换配件',
  },
  INSTRUMENT: {
    label: '仪表/测量问题',
    color: '#6f42c1',
    direction: '校验/维护',
  },
  COMMUNICATION: {
    label: '通信链路问题',
    color: '#0284c7',
    direction: '检查通信链路',
  },
  PROCESS: {
    label: '工艺/外扰问题',
    color: '#0d9488',
    direction: '工艺分析/前馈/解耦',
  },
  UTILIZATION: {
    label: '投用/操作问题',
    color: '#ca8a04',
    direction: '恢复自动投用',
  },
  DESIGN: {
    label: '组态/设计问题',
    color: '#795548',
    direction: '重新组态/改造',
  },
  DATA_INSUFFICIENT: {
    label: '数据不足',
    color: '#6c757d',
    direction: '先补齐数据',
  },
};

/** 分类筛选下拉选项 */
export const CATEGORY_OPTIONS: Array<{
  label: string;
  value: DiagnosisApi.Category;
}> = (
  Object.entries(CATEGORY_META) as Array<[DiagnosisApi.Category, CategoryMeta]>
).map(([value, meta]) => ({ label: meta.label, value }));

export const SEVERITY_TEXT: Record<string, string> = {
  HIGH: '高',
  LOW: '低',
  MEDIUM: '中',
};

export const SEVERITY_COLOR: Record<string, string> = {
  HIGH: 'red',
  LOW: 'default',
  MEDIUM: 'orange',
};

export const RUN_STATUS_TEXT: Record<string, string> = {
  FAILED: '失败',
  PARTIAL: '部分完成',
  RUNNING: '进行中',
  SUCCESS: '完成',
};

/** 触发类型标签（§12 三层自动诊断：手动 / 分级定时 / 预警事件） */
export const TRIGGER_TYPE_TEXT: Record<string, string> = {
  EVENT: '事件触发',
  MANUAL: '手动诊断',
  SCHEDULED: '定期诊断',
};

/** 触发类型色（工业语义：定期=青、事件=橙、手动=中性） */
export const TRIGGER_TYPE_COLOR: Record<string, string> = {
  EVENT: '#ea580c',
  MANUAL: '#6c757d',
  SCHEDULED: '#0891b2',
};

/** 回路重要性等级（loop_ledger.importance_level） */
export const IMPORTANCE_LEVEL_TEXT: Record<number, string> = {
  1: '1级',
  2: '2级',
  3: '3级',
};

/** 等级工业语义色：1级关键=红、2级重要=橙、3级一般=中性 */
export const IMPORTANCE_LEVEL_COLOR: Record<number, string> = {
  1: '#dc2626',
  2: '#ea580c',
  3: '#6c757d',
};

/** 复核状态 */
export const REVIEW_STATUS_TEXT: Record<string, string> = {
  PENDING: '待复核',
  REVIEWED: '已复核',
};

/** 复核状态色：待复核=橙（需人工介入）、已复核=绿（闭环完成） */
export const REVIEW_STATUS_COLOR: Record<string, string> = {
  PENDING: '#ea580c',
  REVIEWED: '#16a34a',
};

/**
 * 性能评分五档（对齐 FDS §5.2.4 / GB/T 44693.2 定级阈值）
 */
export const SCORE_GRADES = [
  { key: 'excellent', label: '优秀', min: 90, color: '#16a34a' },
  { key: 'good', label: '良好', min: 80, color: '#65a30d' },
  { key: 'qualified', label: '合格', min: 60, color: '#0891b2' },
  { key: 'warning', label: '警告', min: 40, color: '#ea580c' },
  { key: 'failed', label: '不合格', min: -Infinity, color: '#dc2626' },
] as const;

export type ScoreGradeKey = (typeof SCORE_GRADES)[number]['key'];

/** 评分 → 档位（含色/文案）；null 返回 null */
export function scoreGrade(score: null | number | undefined) {
  if (score == null || Number.isNaN(score)) return null;
  return SCORE_GRADES.find((g) => score >= g.min) ?? null;
}

/**
 * F3 诊断健康度：新鲜度分档展示元数据（16 号文 F3，按最新 SUCCESS 诊断时间分档）
 * 配色语义：越新鲜越绿 → 超期红 → 从未诊断中性灰
 * 色值全部引用本文件既有常量（评分档/分类中性色），零新增 hex 债务。
 */
export const FRESHNESS_META: Record<
  DiagnosisApi.FreshnessBucketKey,
  { color: string; label: string }
> = {
  within24h: { label: '24h 内', color: SCORE_GRADES[0].color },
  within7d: { label: '7 天内', color: SCORE_GRADES[1].color },
  within30d: { label: '30 天内', color: SCORE_GRADES[3].color },
  stale: { label: '超期 >30 天', color: SCORE_GRADES[4].color },
  never: { label: '从未诊断', color: CATEGORY_META.DATA_INSUFFICIENT.color },
};

/**
 * F5 发起前数据充足性预检徽标元数据（16 号文 F5，复用 FitnessBadge 视觉模式）
 * 三态：充足绿 / 疑似不足琥珀 / 不足红；unknown=无评估数据中性灰
 * （数据源缺失，非数据质量结论，不得误报"不足"）。
 * 色值全部引用本文件既有常量（评分档/分类中性色），零新增 hex 债务。
 */
export const PRECHECK_META: Record<
  DiagnosisApi.PrecheckLevel,
  { color: string; icon: string; label: string }
> = {
  sufficient: {
    label: '数据充足',
    color: SCORE_GRADES[0].color,
    icon: 'lucide:check',
  },
  marginal: {
    label: '疑似不足',
    color: SCORE_GRADES[3].color,
    icon: 'lucide:triangle-alert',
  },
  insufficient: {
    label: '数据不足',
    color: SCORE_GRADES[4].color,
    icon: 'lucide:database-off',
  },
  unknown: {
    label: '无评估数据',
    color: CATEGORY_META.DATA_INSUFFICIENT.color,
    icon: 'lucide:help-circle',
  },
};

/**
 * F6 复核反馈：确认率色阶（复用 DgRuleStats 解决率色阶语义：
 * ≥80% 绿 / ≥50% 橙 / <50% 红 / null → 中性灰；色值引用既有常量零新增 hex）
 */
export function confirmRateColor(rate: null | number): string {
  if (rate === null) return CATEGORY_META.DATA_INSUFFICIENT.color;
  if (rate >= 0.8) return SCORE_GRADES[0].color;
  if (rate >= 0.5) return SCORE_GRADES[3].color;
  return SCORE_GRADES[4].color;
}

/** F6 阈值调优提示琥珀色（改判率 >40% 行标色；引用评分档色，零新增 hex） */
export const REVIEW_HINT_COLOR = SCORE_GRADES[3].color;
