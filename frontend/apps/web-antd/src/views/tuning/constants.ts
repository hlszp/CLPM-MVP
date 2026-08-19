/**
 * 整定模块共享常量（09 设计方案）
 */

/** 整定算法显示名：中文 + 英文缩写（口径对齐后端 TUNING_METHODS_INFO） */
export const TUNING_ALGO_LABELS: Record<string, string> = {
  IMC: '内模控制（IMC）',
  LAMBDA: 'Lambda 整定（LAMBDA）',
  ZN: 'Z-N 整定（ZN）',
  COHEN_COON: 'Cohen-Coon（COHEN_COON）',
  SIMC: '简化内模控制（SIMC）',
  MANUAL_TUNING: '手动整定（MANUAL）',
};

/** 算法 key → 「中文（英文缩写）」；未知 key 原样返回，空值显示 — */
export function tuningAlgoLabel(algo: null | string | undefined): string {
  if (!algo) return '—';
  return TUNING_ALGO_LABELS[algo] ?? algo;
}

/** 数值统一两位小数显示（过程模型/PID 参数口径）；空值显示 — */
export function fmtNum2(v: null | number | undefined): string {
  return v == null || Number.isNaN(v) ? '—' : v.toFixed(2);
}

/** 回路重要性等级（loop_ledger.importance_level；口径同诊断模块） */
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

/** 性能评分五档（对齐 FDS §5.2.4 / GB/T 44693.2 定级阈值，与诊断模块同口径） */
export const SCORE_GRADES = [
  { key: 'excellent', label: '优秀', min: 90, color: '#16a34a' },
  { key: 'good', label: '良好', min: 80, color: '#65a30d' },
  { key: 'qualified', label: '合格', min: 60, color: '#0891b2' },
  { key: 'warning', label: '警告', min: 40, color: '#ea580c' },
  { key: 'failed', label: '不合格', min: -Number.MAX_SAFE_INTEGER, color: '#dc2626' },
] as const;

/** 评分 → 档位（含色/文案）；null 返回 null */
export function scoreGrade(score: null | number | undefined) {
  if (score == null || Number.isNaN(score)) return null;
  return SCORE_GRADES.find((g) => score >= g.min) ?? null;
}
