/**
 * CLPM 主题响应式色板
 *
 * 为 ECharts 图表和业务组件提供随深色模式自动切换的颜色取值。
 * vben 框架已内置 dark mode 切换能力（CSS 变量 + Ant Design darkAlgorithm +
 * ECharts 'dark' 主题），但 CLPM 业务代码中存在大量硬编码色值（#52c41a / #faad14 /
 * #8c8c8c / #fff 等），在深色背景下会出现对比度不足、元素消失等问题。
 *
 * 本 composable 统一提供响应式色值，使用方式：
 * ```ts
 * const { isDark, chartTextColor, themeColors } = useClpmTheme();
 * // ECharts 配置
 * renderEcharts({
 *   xAxis: { axisLabel: { color: chartTextColor.value } },
 *   series: [{ itemStyle: { color: themeColors.value.SUCCESS } }],
 * });
 * ```
 *
 * 关键约束：由于 ECharts cacheOptions 在 isDark 切换时不会自动重算色值，
 * 调用方需在 watch(isDark) 时手动重新构造 options 并调用 renderEcharts。
 */
import { usePreferences } from '@vben/preferences';

import { computed } from 'vue';

/** 业务语义色板（浅色模式，对齐 THEME_COLORS 原始定义） */
const LIGHT_COLORS = {
  SUCCESS: '#52c41a',
  WARNING: '#faad14',
  DANGER: '#ff4d4f',
  INFO: '#0d6efd',
  NEUTRAL: '#8c8c8c',
} as const;

/** 业务语义色板（深色模式，亮度更高，对齐 dark.css --success/--warning/--destructive） */
const DARK_COLORS = {
  SUCCESS: '#22c55e',
  WARNING: '#fbbf24',
  DANGER: '#f87171',
  INFO: '#60a5fa',
  NEUTRAL: '#9ca3af',
} as const;

/**
 * 可信度等级色板（浅色模式，对齐 confidence-badge.vue §7.15 A/B/C/D/E 五级）
 *
 * 设计文档 §7.15 颜色映射要求用 `--status-*` 语义变量响应主题切换。
 * 此处保留 5 级高区分度色相（绿/青/金/橙/灰），深色模式亮度提升。
 */
const LIGHT_CONFIDENCE = {
  A: '#52c41a', // 绿（对齐 SUCCESS）
  B: '#13c2c2', // 青（INFO 近似）
  C: '#faad14', // 金黄（WARNING）
  D: '#fa8c16', // 橙（介于 WARNING 与 DANGER 之间，保留原视觉区分）
  E: '#8c8c8c', // 灰（NEUTRAL）
} as const;

/** 可信度等级色板（深色模式，亮度提升对齐 DARK_COLORS 同语义色） */
const DARK_CONFIDENCE = {
  A: '#22c55e',
  B: '#2dd4bf',
  C: '#fbbf24',
  D: '#fb923c',
  E: '#9ca3af',
} as const;

/** ECharts 图表通用色（浅色模式） */
const LIGHT_CHART = {
  /** 轴标签、图例、标题等文本色 */
  text: '#8c8c8c',
  /** 主文本色（rich label、emphasis 等） */
  textStrong: '#0f172a',
  /** 网格分割线色 */
  splitLine: '#E5E5E5',
  /** 仪表盘/进度条背景 track 色 */
  track: '#f0f0f0',
  /** 饼图/环形图边框色（与卡片背景融合） */
  border: '#ffffff',
  /** markArea / 无效段半透明色 */
  mutedFill: 'rgba(200, 200, 200, 0.15)',
  /** markLine 辅助线色 */
  markLine: '#999999',
  /** PV 无效段色 */
  invalid: '#cccccc',
} as const;

/** ECharts 图表通用色（深色模式） */
const DARK_CHART = {
  text: '#9ca3af',
  textStrong: '#e5e7eb',
  splitLine: 'rgba(255, 255, 255, 0.08)',
  track: 'rgba(255, 255, 255, 0.06)',
  border: '#1f2937',
  mutedFill: 'rgba(255, 255, 255, 0.08)',
  markLine: 'rgba(255, 255, 255, 0.35)',
  invalid: 'rgba(255, 255, 255, 0.25)',
} as const;

/**
 * CLPM 主题响应式色板
 *
 * 返回的 themeColors 和 chartColors 都是 computed ref，isDark 变化时自动更新。
 * 调用方在 ECharts 配置中应使用 `.value` 取当前色值，并在 isDark 变化时重新渲染。
 */
export function useClpmTheme() {
  const { isDark } = usePreferences();

  /** 业务语义色板（响应式） */
  const themeColors = computed(() =>
    isDark.value ? DARK_COLORS : LIGHT_COLORS,
  );

  /** 可信度等级色板（响应式，A/B/C/D/E 五级，对齐 §7.15） */
  const confidenceColors = computed(() =>
    isDark.value ? DARK_CONFIDENCE : LIGHT_CONFIDENCE,
  );

  /** ECharts 图表通用色（响应式） */
  const chartColors = computed(() => (isDark.value ? DARK_CHART : LIGHT_CHART));

  /** ECharts 轴标签/图例文本色（便捷别名） */
  const chartTextColor = computed(() => chartColors.value.text);

  /** ECharts 主文本色（rich label 等） */
  const chartTextStrongColor = computed(() => chartColors.value.textStrong);

  /** ECharts 网格分割线色 */
  const chartSplitLineColor = computed(() => chartColors.value.splitLine);

  /** ECharts 仪表盘/进度条背景 track 色 */
  const chartTrackColor = computed(() => chartColors.value.track);

  /** ECharts 饼图/环形图边框色 */
  const chartBorderColor = computed(() => chartColors.value.border);

  /** ECharts markArea 半透明填充色 */
  const chartMutedFillColor = computed(() => chartColors.value.mutedFill);

  /** ECharts markLine 辅助线色 */
  const chartMarkLineColor = computed(() => chartColors.value.markLine);

  /** ECharts PV 无效段色 */
  const chartInvalidColor = computed(() => chartColors.value.invalid);

  return {
    isDark,
    themeColors,
    confidenceColors,
    chartColors,
    chartTextColor,
    chartTextStrongColor,
    chartSplitLineColor,
    chartTrackColor,
    chartBorderColor,
    chartMutedFillColor,
    chartMarkLineColor,
    chartInvalidColor,
  };
}

/**
 * 仪表盘 axisLine 三段色（红/黄/绿）
 *
 * 用于 gauge 类型的 axisLine.lineStyle.color，按比例分段。
 * 响应式：深色模式下使用更亮的色值。
 */
export function useGaugeAxisColors() {
  const { themeColors } = useClpmTheme();
  return computed(() => [
    [0.6, themeColors.value.DANGER],
    [0.8, themeColors.value.WARNING],
    [1, themeColors.value.SUCCESS],
  ]);
}
