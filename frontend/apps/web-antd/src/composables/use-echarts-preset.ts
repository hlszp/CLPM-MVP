/**
 * UI-06 ECharts 工业主题 preset（v6.1 §15.2 UI-06 / §14 C-01 C-03）
 *
 * 提供 ECharts 通用配置 preset，统一图表视觉：
 * - 网格 grid 紧凑、低饱和分割线
 * - 轴标签等宽 + 中性色
 * - tooltip 去装饰阴影
 * - 状态色映射到 --status-* token
 * - 禁用 shadowBlur（仅 tooltip/选中态允许）
 *
 * 用法：
 * ```ts
 * const { getEchartsBase, getSeriesColor, getTooltipPreset } = useEchartsPreset();
 * const options = {
 *   ...getEchartsBase(),
 *   tooltip: getTooltipPreset(),
 *   series: [{ type: 'line', itemStyle: { color: getSeriesColor('ok') } }],
 * };
 * ```
 */
import { computed } from 'vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';

/** 业务状态 → ECharts 系列色（响应式） */
export function useEchartsPreset() {
  const { isDark, chartColors, themeColors } = useClpmTheme();

  /** ECharts 基础网格配置 */
  const gridBase = computed(() => ({
    top: 32,
    right: 16,
    bottom: 32,
    left: 48,
    containLabel: true,
  }));

  /** ECharts 轴通用样式 */
  const axisBase = computed(() => ({
    axisLine: {
      lineStyle: {
        color: chartColors.value.splitLine,
        width: 1,
      },
    },
    axisTick: {
      lineStyle: {
        color: chartColors.value.splitLine,
      },
    },
    axisLabel: {
      color: chartColors.value.text,
      fontFamily: 'var(--font-mono)',
      fontSize: 11,
      hideOverlap: true,
    },
    splitLine: {
      lineStyle: {
        color: chartColors.value.splitLine,
        type: 'dashed' as const,
        opacity: 0.6,
      },
    },
  }));

  /** ECharts 基础配置（grid + xAxis + yAxis + legend + textStyle） */
  function getEchartsBase() {
    return {
      grid: gridBase.value,
      textStyle: {
        fontFamily:
          'Inter, ui-sans-serif, system-ui, -apple-system, sans-serif',
        color: chartColors.value.textStrong,
      },
      legend: {
        textStyle: {
          color: chartColors.value.text,
          fontSize: 12,
        },
        itemWidth: 12,
        itemHeight: 8,
        icon: 'roundRect',
      },
      xAxis: {
        ...axisBase.value,
        type: 'category' as const,
        boundaryGap: false,
      },
      yAxis: {
        ...axisBase.value,
        type: 'value' as const,
      },
    };
  }

  /** tooltip 配置（去装饰阴影，使用边框优先） */
  function getTooltipPreset() {
    return {
      backgroundColor: isDark.value ? '#1f2937' : '#ffffff',
      borderColor: chartColors.value.splitLine,
      borderWidth: 1,
      borderRadius: 4,
      padding: [8, 10],
      textStyle: {
        color: chartColors.value.textStrong,
        fontSize: 12,
        fontFamily: 'var(--font-mono)',
      },
      extraCssText: 'box-shadow: none; backdrop-filter: blur(4px);',
    };
  }

  /** 系列色映射（业务状态 → ECharts 颜色） */
  function getSeriesColor(
    status: 'error' | 'info' | 'neutral' | 'ok' | 'warning',
  ) {
    const map = {
      ok: themeColors.value.SUCCESS,
      warning: themeColors.value.WARNING,
      error: themeColors.value.DANGER,
      info: themeColors.value.INFO,
      neutral: themeColors.value.NEUTRAL,
    } as const;
    return map[status];
  }

  /** 线条样式 preset（去阴影，统一线宽） */
  function getLineSeriesPreset(color: string) {
    return {
      type: 'line' as const,
      smooth: false,
      symbol: 'circle',
      symbolSize: 4,
      showSymbol: false,
      lineStyle: {
        width: 1.5,
        color,
      },
      itemStyle: {
        color,
        borderColor: 'transparent',
        borderWidth: 0,
      },
      areaStyle: undefined,
      emphasis: {
        focus: 'series' as const,
        lineStyle: {
          width: 2,
        },
      },
    };
  }

  /** 柱状样式 preset（去阴影） */
  function getBarSeriesPreset(color: string) {
    return {
      type: 'bar' as const,
      barWidth: '60%',
      itemStyle: {
        color,
        borderRadius: [2, 2, 0, 0],
        shadowBlur: 0,
        shadowColor: 'transparent',
      },
      emphasis: {
        itemStyle: {
          color,
          opacity: 0.85,
        },
      },
    };
  }

  /** 禁用 shadowBlur 的全局配置（仅 tooltip/选中态允许） */
  const noShadow = {
    shadowBlur: 0,
    shadowColor: 'transparent',
  } as const;

  return {
    isDark,
    chartColors,
    themeColors,
    gridBase,
    axisBase,
    getEchartsBase,
    getTooltipPreset,
    getSeriesColor,
    getLineSeriesPreset,
    getBarSeriesPreset,
    noShadow,
  };
}
