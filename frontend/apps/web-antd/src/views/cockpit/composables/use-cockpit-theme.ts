/**
 * 驾驶舱主题色 composable（方案 11 §8）
 *
 * 驾驶舱主题（dark/light）由 cockpit store 驱动、经 .cockpit-root[data-theme]
 * 的 --ck-* CSS 变量呈现，与后台 vben 全局明暗无关。本 composable 为
 * ECharts 图表与行内 style 提供与 theme.css 同源的色值（按 store.theme 取色），
 * 避免 JS 侧硬编码与 CSS 变量漂移。
 *
 * 五档色语义对齐 use-score-color.ts 默认阈值（GB/T 44693.2-2024 §6.3：
 * 优秀≥90 / 良好≥80 / 合格≥60 / 警告≥40 / 不合格<40）。
 */
import type { CockpitApi } from '#/api/cockpit';

import { computed } from 'vue';

import { useCockpitStore } from '#/store/cockpit';

export type GradeName = CockpitApi.GradeKey;

/** 深色主题五档色（与 theme.css .cockpit-root 默认值一致） */
const DARK_GRADES: Record<GradeName, string> = {
  EXCELLENT: '#34d399',
  FAIR: '#f5a623',
  GOOD: '#60a5fa',
  POOR: '#ef4444',
  WARNING: '#f87171',
};

/** 浅色主题五档色（与 theme.css [data-theme='light'] 一致） */
const LIGHT_GRADES: Record<GradeName, string> = {
  EXCELLENT: '#1a7f4b',
  FAIR: '#b45309',
  GOOD: '#2563eb',
  POOR: '#a12222',
  WARNING: '#c23434',
};

/** 五档中文标签 */
export const GRADE_LABELS: Record<GradeName, string> = {
  EXCELLENT: '优秀',
  FAIR: '合格',
  GOOD: '良好',
  POOR: '不合格',
  WARNING: '警告',
};

/** 五档排列顺序（分布条/图例统一） */
export const GRADE_ORDER: GradeName[] = [
  'EXCELLENT',
  'GOOD',
  'FAIR',
  'WARNING',
  'POOR',
];

/** ECharts 图表通用色（对齐 theme.css 文本/边框变量） */
const DARK_CHART = {
  panel: '#13203a',
  radar: '#60a5fa',
  splitLine: 'rgba(148, 176, 220, 0.16)',
  text: '#93a7c4',
  textStrong: '#e8effa',
  track: 'rgba(148, 176, 220, 0.12)',
} as const;

const LIGHT_CHART = {
  panel: '#ffffff',
  radar: '#1d4ed8',
  splitLine: '#e3e8f0',
  text: '#5a6b80',
  textStrong: '#1c2b3a',
  track: '#eef2f8',
} as const;

/** getComputedStyle 解析失败时的中性兜底色（= --ck-text-2 深色值） */
export const CHART_COLOR_FALLBACK = '#93a7c4';

/** 预警级别色（CRITICAL/ERROR 红系、WARN 琥珀、INFO 蓝，深/浅两组） */
const DARK_SEVERITY = {
  CRITICAL: '#ef4444',
  ERROR: '#f87171',
  INFO: '#60a5fa',
  WARN: '#f5a623',
} as const;

const LIGHT_SEVERITY = {
  CRITICAL: '#a12222',
  ERROR: '#c23434',
  INFO: '#2563eb',
  WARN: '#b45309',
} as const;

/** 评分 → 五档（GB/T 44693.2-2024 §6.3 默认阈值；无评分返回 null=数据不足） */
export function gradeOfScore(
  score: null | number | undefined,
): GradeName | null {
  if (score === null || score === undefined || Number.isNaN(score)) {
    return null;
  }
  if (score >= 90) return 'EXCELLENT';
  if (score >= 80) return 'GOOD';
  if (score >= 60) return 'FAIR';
  if (score >= 40) return 'WARNING';
  return 'POOR';
}

export function useCockpitTheme() {
  const cockpitStore = useCockpitStore();

  const isLight = computed(() => cockpitStore.theme === 'light');

  /** 五档色板（响应式） */
  const gradeColors = computed<Record<GradeName, string>>(() =>
    isLight.value ? LIGHT_GRADES : DARK_GRADES,
  );

  /** ECharts 图表通用色（响应式） */
  const chartColors = computed(() =>
    isLight.value ? LIGHT_CHART : DARK_CHART,
  );

  /** 预警级别色板（响应式） */
  const severityColors = computed(() =>
    isLight.value ? LIGHT_SEVERITY : DARK_SEVERITY,
  );

  /** 评分 → 五档色；无评分 → 中性文本色（"数据不足"非"不合格"，严禁染红） */
  function scoreColor(score: null | number | undefined): string {
    const g = gradeOfScore(score);
    return g ? gradeColors.value[g] : chartColors.value.text;
  }

  /** 评分 → 五档中文标签；无评分返回 '' */
  function scoreLabel(score: null | number | undefined): string {
    const g = gradeOfScore(score);
    return g ? GRADE_LABELS[g] : '';
  }

  return {
    chartColors,
    gradeColors,
    isLight,
    scoreColor,
    scoreLabel,
    severityColors,
  };
}
