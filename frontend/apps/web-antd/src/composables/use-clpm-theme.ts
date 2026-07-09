import { computed } from 'vue';

/**
 * CLPM 主题响应式色板
 *
 * 为 ECharts 图表和业务组件提供随深色模式自动切换的颜色取值。
 * vben 框架已内置 dark mode 切换能力（CSS 变量 + Ant Design darkAlgorithm +
 * ECharts 'dark' 主题），但 CLPM 业务代码中存在大量硬编码色值，在深色背景
 * 下会出现对比度不足、元素消失等问题。
 *
 * v6.1 §3.1.3 对齐 ZL 致联工业设计套件（参考 ZL-MES-UI-Design-Kit/
 * IndustrialDesignReference.html §1）：
 * - Emerald = 运行/成功/在线
 * - Amber   = 警告/待机/部分
 * - Rose    = 故障/严重/不可逆
 * - Blue    = 主操作/信息/待处理
 * - Slate   = 中性/无数据/未知
 * - Teal    = 工业强调色（侧边栏激活、KPI 装饰、品牌强调）
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

/** ZL 工业色板（浅色模式，对齐 Tailwind slate/emerald/amber/rose/blue） */
const LIGHT_COLORS = {
  SUCCESS: '#10b981', // emerald-500（运行/成功/在线）
  WARNING: '#f59e0b', // amber-500（警告/待机/部分）
  DANGER: '#f43f5e', // rose-500（故障/严重/不可逆）
  INFO: '#3b82f6', // blue-500（主操作/信息）
  NEUTRAL: '#64748b', // slate-500（中性/无数据）
  ACCENT: '#0d9488', // Teal 强调色（侧边栏激活/KPI 装饰/品牌强调）
} as const;

/** ZL 工业色板（深色模式，亮度提升对齐 Tailwind 400/500） */
const DARK_COLORS = {
  SUCCESS: '#34d399', // emerald-400
  WARNING: '#fbbf24', // amber-400
  DANGER: '#fb7185', // rose-400
  INFO: '#60a5fa', // blue-400
  NEUTRAL: '#94a3b8', // slate-400
  ACCENT: '#2dd4bf', // teal-400
} as const;

/**
 * 可信度等级色板（浅色模式，对齐 confidence-badge.vue §7.15 A/B/C/D/E 五级）
 *
 * 设计文档 §7.15 颜色映射要求用 `--status-*` 语义变量响应主题切换。
 * v6.1 对齐 ZL 工业色板：A=emerald, B=teal, C=amber, D=orange, E=slate
 */
const LIGHT_CONFIDENCE = {
  A: '#10b981', // emerald-500（对齐 SUCCESS）
  B: '#14b8a6', // teal-500（与 ACCENT #0d9488 同色相但更亮，区分 A/B）
  C: '#f59e0b', // amber-500（对齐 WARNING）
  D: '#f97316', // orange-500（介于 amber 与 rose 之间）
  E: '#64748b', // slate-500（对齐 NEUTRAL）
} as const;

/** 可信度等级色板（深色模式，亮度提升对齐 ZL Tailwind 400） */
const DARK_CONFIDENCE = {
  A: '#34d399', // emerald-400
  B: '#2dd4bf', // teal-400
  C: '#fbbf24', // amber-400
  D: '#fb923c', // orange-400
  E: '#94a3b8', // slate-400
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
