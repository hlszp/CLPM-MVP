/**
 * 诊断科学结论标签与读端助手（P3-D6，2026-10-12）。
 *
 * 依据 08 号文 §4.2/§4.5 + 11§3 对账表：run 执行状态（SUCCESS/PARTIAL/FAILED）
 * 与科学结论（judgementStatus）分开——**主分类 NULL 不能推出健康**，
 * 「未见异常」不再作为空值兜底；历史 run 无 assessment_meta 时按
 * LEGACY_UNVERIFIABLE（历史覆盖未验证）口径显示。
 */

export type JudgementStatus =
  | 'INCONCLUSIVE'
  | 'LEGACY_UNVERIFIABLE'
  | 'NOT_ASSESSED'
  | 'SYMPTOM_DETECTED'
  | 'VALID_NEGATIVE';

export interface DiagnosisAssessmentMeta {
  schemaVersion?: string;
  judgementStatus?: JudgementStatus;
  causeStatus?: null | string;
  scope?: null | Record<string, unknown>;
  coverage?: null | Record<string, boolean | number>;
  supportKind?: null | string;
  calibrationStatus?: null | string;
  operatorStates?: null | Record<string, { reasonCode?: null | string; state?: null | string }>;
  cancelled?: boolean;
}

/** judgementStatus → 中文标签（科学结论，非执行状态） */
export const JUDGEMENT_LABELS: Record<JudgementStatus, string> = {
  SYMPTOM_DETECTED: '检出症状',
  VALID_NEGATIVE: '有效阴性（范围内正常）',
  INCONCLUSIVE: '不可判定（证据不足）',
  NOT_ASSESSED: '未评估',
  LEGACY_UNVERIFIABLE: '历史覆盖未验证',
};

/** judgementStatus → 语义色（对齐现有 success/warning/danger 中性口径） */
export const JUDGEMENT_TONES: Record<JudgementStatus, 'danger' | 'default' | 'warning'> = {
  SYMPTOM_DETECTED: 'danger',
  VALID_NEGATIVE: 'default',
  INCONCLUSIVE: 'warning',
  NOT_ASSESSED: 'warning',
  LEGACY_UNVERIFIABLE: 'default',
};

/**
 * 主分类空值兜底（R06 反例修复）：主分类 NULL 不再显示「未见异常」——
 * 按科学结论显示；无 assessment（历史 run）→ 历史覆盖未验证。
 */
export function categoryOrJudgementLabel(
  primaryCategoryLabel: null | string | undefined,
  assessment: DiagnosisAssessmentMeta | null | undefined,
): string {
  if (primaryCategoryLabel) return primaryCategoryLabel;
  return JUDGEMENT_LABELS[judgementOf(assessment)];
}

/** 读端统一口径：无 assessment_meta 的历史 run 视为 LEGACY_UNVERIFIABLE */
export function judgementOf(
  assessment: DiagnosisAssessmentMeta | null | undefined,
): JudgementStatus {
  return assessment?.judgementStatus ?? 'LEGACY_UNVERIFIABLE';
}

/** 执行状态 SUCCESS 且科学结论为症状检出时才是"确定异常"（INT-05 反例） */
export function isConfirmedAbnormal(assessment: DiagnosisAssessmentMeta | null | undefined): boolean {
  return judgementOf(assessment) === 'SYMPTOM_DETECTED';
}
