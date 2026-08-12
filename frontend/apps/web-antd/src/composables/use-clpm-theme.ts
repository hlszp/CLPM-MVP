import { computed } from 'vue';

/**
 * CLPM 主题响应式色板
 *
 * 为 ECharts 图表和业务组件提供随深色模式自动切换的颜色取值。
 * vben 框架已内置 dark mode 切换能力（CSS 变量 + Ant Design darkAlgorithm +
 * ECharts 'dark' 主题），但 CLPM 业务代码中存在大量硬编码色值，在深色背景
 * 下会出现对比度不足、元素消失等问题。
 *
 * v6.1 §3.1.3 对齐 CLPM 色彩约定表 v1（docs/设计文档/06-UIUX/color-convention.md，
 * 整改 A-01 单源化 2026-08-08）：
 * - #198754 = 正常/达标/在线
 * - #B45309 = 警告/需关注（深琥珀文字态，浅底配 #FEF3C7）
 * - #DC3545 = 危险/故障/需立即行动
 * - #0D6EFD = 主操作/信息/工业蓝 accent
 * - #6C757D = 中性/无数据/零值/INCONCLUSIVE
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

/** CLPM 语义色板（浅色模式，色彩约定表 v1 / 整改 A-01 单源化，2026-08-08）
 *
 * 值对齐 industrial-light.css 的 --status-* 变量（唯一来源）：
 * SUCCESS=#198754 / WARNING=#B45309 / DANGER=#DC3545 / INFO=ACCENT=#0D6EFD / NEUTRAL=#6C757D
 */
const LIGHT_COLORS = {
  SUCCESS: '#198754', // 运行/成功/在线
  WARNING: '#b45309', // 警告/需关注（深琥珀，浅底下可读）
  DANGER: '#dc3545', // 故障/严重/不可逆
  INFO: '#0d6efd', // 主操作/信息
  NEUTRAL: '#6c757d', // 中性/无数据/零值
  ACCENT: '#0d6efd', // 工业蓝（与 INFO 同值：单蓝 accent 纪律）
} as const;

/** CLPM 语义色板（深色模式，亮度提升；E2 暗色校准时再全量复核对比度） */
const DARK_COLORS = {
  SUCCESS: '#4ade80', // green-400
  WARNING: '#fbbf24', // amber-400
  DANGER: '#fb7185', // rose-400
  INFO: '#60a5fa', // blue-400
  NEUTRAL: '#9ca3af', // gray-400
  ACCENT: '#60a5fa', // blue-400
} as const;

/**
 * 可信度等级色板（浅色模式，UI/UX §3.1.6：A 青绿 / B 深蓝 / C 琥珀 / D 警示红 / E 冷灰）
 */
const LIGHT_CONFIDENCE = {
  A: '#198754',
  B: '#0d6efd',
  C: '#b45309',
  D: '#dc3545',
  E: '#6c757d',
} as const;

/** 可信度等级色板（深色模式，亮度提升对齐 ZL Tailwind 400）
 *
 * P2-01 修正（2026-08-10）：D 级从 orange-400(#fb923c) 改为 rose-400(#fb7185)，
 * 与浅色 D=#dc3545(rose-700) 保持色相一致（均属红色系"警示"语义），
 * 避免暗色下 D 级从红变橙的色相跳变。
 */
const DARK_CONFIDENCE = {
  A: '#34d399', // emerald-400
  B: '#60a5fa', // blue-400（与 INFO 对齐，浅色 B=#0d6efd blue-600）
  C: '#fbbf24', // amber-400
  D: '#fb7185', // rose-400（修正：原 orange-400 色相不一致）
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
