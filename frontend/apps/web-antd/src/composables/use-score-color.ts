/**
 * useScoreColor — 综合评分配色 composable
 *
 * 统一各业务页"评分 → 颜色"的换算逻辑，行为约定：
 *
 * - 阈值动态化：档位阈值来自算法配置（`getGradingThresholdsApi` 加载的
 *   `GradingThresholdItem[]`，参考 metric/pid-dashboard.vue 既有加载方式），
 *   未传入或为空时降级为 GB/T 44693.2-2024 §6.3 默认阈值，禁止视图层硬编码；
 * - null/undefined/NaN 评分 → 中性灰（ZL `NEUTRAL`），**严禁映射为红色**：
 *   无评分是"数据不足"而非"不合格"，红色会误导用户当作告警处理；
 * - 颜色优先级：命中的阈值项自带 `color` > 按档位映射 ZL 语义色。
 *
 * 用法：
 * ```ts
 * const gradingThresholds = ref<MetricApi.GradingThresholdItem[]>([]);
 * const { color, level, label } = useScoreColor(() => row.score, gradingThresholds);
 * ```
 */
import type { ComputedRef, MaybeRefOrGetter } from 'vue';

import type { MetricApi } from '#/api/metric';

import { computed, toValue } from 'vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';

/**
 * 默认定级阈值（GB/T 44693.2-2024 §6.3），配置未加载时降级使用。
 * 不携带 color：默认阈值的颜色由 color computed 按档位降级到
 * useClpmTheme 语义色（优秀 SUCCESS / 良好 INFO / 合格 WARNING /
 * 警告 DANGER / 不合格 DANGER），随明暗主题响应。
 */
const DEFAULT_THRESHOLDS: MetricApi.GradingThresholdItem[] = [
  { level: 1, name: 'EXCELLENT', label: '优秀', minScore: 90, maxScore: 100 },
  { level: 2, name: 'GOOD', label: '良好', minScore: 80, maxScore: 90 },
  { level: 3, name: 'FAIR', label: '合格', minScore: 60, maxScore: 80 },
  { level: 4, name: 'WARNING', label: '警告', minScore: 40, maxScore: 60 },
  { level: 5, name: 'POOR', label: '不合格', minScore: 0, maxScore: 40 },
];

export interface UseScoreColorReturn {
  /** 评分对应颜色；评分为空时返回 ZL 中性灰 NEUTRAL（随明暗主题响应） */
  color: ComputedRef<string>;
  /** 命中档位中文名（如"优秀"）；评分为空时为 null */
  label: ComputedRef<null | string>;
  /** 命中档位 level（字符串，'1' 最优）；评分为空时为 null */
  level: ComputedRef<null | string>;
}

export function useScoreColor(
  score: MaybeRefOrGetter<null | number | undefined>,
  thresholds?: MaybeRefOrGetter<
    MetricApi.GradingThresholdItem[] | null | undefined
  >,
): UseScoreColorReturn {
  const { themeColors } = useClpmTheme();

  /** 有效阈值集：动态配置优先，为空时降级默认阈值 */
  const effectiveThresholds = computed<MetricApi.GradingThresholdItem[]>(() => {
    const dynamic = thresholds === undefined ? null : toValue(thresholds);
    return dynamic && dynamic.length > 0 ? dynamic : DEFAULT_THRESHOLDS;
  });

  /** 命中档位：按 minScore 降序首个 score >= minScore；都不命中取最低档 */
  const matched = computed<MetricApi.GradingThresholdItem | null>(() => {
    const s = toValue(score);
    if (s === null || s === undefined || Number.isNaN(s)) return null;
    const sorted = [...effectiveThresholds.value].toSorted(
      (a, b) => b.minScore - a.minScore,
    );
    for (const t of sorted) {
      if (s >= t.minScore) return t;
    }
    return sorted.at(-1) ?? null;
  });

  const level = computed<null | string>(() =>
    matched.value ? String(matched.value.level) : null,
  );

  const label = computed<null | string>(() =>
    matched.value ? (matched.value.label ?? matched.value.name) : null,
  );

  const color = computed<string>(() => {
    const m = matched.value;
    if (!m) {
      // 无评分 → 中性灰，严禁红色（"数据不足"不是"不合格"）
      return themeColors.value.NEUTRAL;
    }
    if (m.color) return m.color;
    // 阈值项未配置颜色时按档位降级到 ZL 语义色
    const fallbackByLevel: Record<number, string> = {
      1: themeColors.value.SUCCESS,
      2: themeColors.value.INFO,
      3: themeColors.value.WARNING,
      4: themeColors.value.DANGER,
      5: themeColors.value.DANGER,
    };
    return fallbackByLevel[m.level] ?? themeColors.value.NEUTRAL;
  });

  return { color, label, level };
}
