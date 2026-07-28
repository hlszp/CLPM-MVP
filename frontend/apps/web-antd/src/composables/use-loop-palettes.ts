/**
 * useLoopPalettes — 回路类型 / 控制方式（MODE）共享色板
 *
 * 集中 monitor/detail/manage 等视图重复定义的 LOOP_TYPE / MODE 映射，
 * 行为约定：
 *
 * - 分类色（回路类型、MODE 数值色）是"类别标识"而非状态语义，
 *   无法映射到 ZL 六语义色，因此沉淀为本文件的常量（对齐
 *   `constants/diagnosis.ts` 的 DIAGNOSIS_LABEL_COLOR_HEX_MAP 先例），
 *   视图层禁止再各自重复定义；
 * - 状态语义色（Auto/Manual/Cascade 徽标、评分档位色）一律经
 *   `useClpmTheme().themeColors` 取响应式语义色，随明暗主题切换，
 *   由 `useLoopPalettes()` 返回的 `modeLabelColor` 统一提供；
 * - 分类色常量用于 ECharts 柱状图 / 统计卡片 / Tag 等场景，
 *   明暗主题下均可读，暂不随主题切换（与诊断标签色板口径一致）。
 */
import { useClpmTheme } from '#/composables/use-clpm-theme';

/** 回路类型中文标签映射 */
export const LOOP_TYPE_LABEL_MAP: Record<string, string> = {
  TEMPERATURE: '温度',
  PRESSURE: '压力',
  LEVEL: '液位',
  FLOW: '流量',
  ANALYSIS: '分析',
  SPEED: '速度',
  OTHER: '其他',
};

/**
 * 回路类型分类主色（统计卡片边框/计数、ECharts 柱状图）
 * Tailwind 500 系列分类色，类别间色相区分优先
 */
export const LOOP_TYPE_COLOR_MAP: Record<string, string> = {
  TEMPERATURE: '#ef4444',
  PRESSURE: '#3b82f6',
  LEVEL: '#10b981',
  FLOW: '#06b6d4',
  ANALYSIS: '#8b5cf6',
  SPEED: '#f59e0b',
  OTHER: '#6b7280',
};

/**
 * 回路类型 Tag 浅色（antd Tag 背景用 pastel 色，避免与状态语义色混淆）
 */
export const LOOP_TYPE_TAG_COLOR_MAP: Record<string, string> = {
  TEMPERATURE: '#FCA5A5',
  PRESSURE: '#93C5FD',
  LEVEL: '#86EFAC',
  FLOW: '#67E8F9',
  ANALYSIS: '#D8B4FE',
  SPEED: '#FDBA74',
  OTHER: '#CBD5E1',
};

/** MODE 数值 → 分类色（0=手动,1=自动,2=串级,3=远程,4=先控） */
export const MODE_COLOR_MAP: Record<string, string> = {
  '0': '#ef4444', // 手动-红
  '1': '#10b981', // 自动-绿
  '2': '#3b82f6', // 串级-蓝
  '3': '#f59e0b', // 远程-橙
  '4': '#8b5cf6', // 先控-紫
};

/** MODE 数值 → 英文短标签（柱状图类别轴） */
export const MODE_LABEL_MAP: Record<string, string> = {
  '0': 'MAN',
  '1': 'AUTO',
  '2': 'CAS',
  '3': 'RAC',
  '4': 'APC',
};

/**
 * 回路色板 composable：提供依赖主题语义色的派生函数
 */
export function useLoopPalettes() {
  const { themeColors } = useClpmTheme();

  /**
   * modeLabel（后端权威输出）→ ZL 状态语义色（响应式，随明暗主题切换）
   * Auto=SUCCESS / Manual=WARNING / Cascade=INFO / 其他=NEUTRAL
   */
  function modeLabelColor(modeLabel: null | string | undefined): string {
    if (modeLabel === 'Auto') return themeColors.value.SUCCESS;
    if (modeLabel === 'Manual') return themeColors.value.WARNING;
    if (modeLabel === 'Cascade') return themeColors.value.INFO;
    return themeColors.value.NEUTRAL;
  }

  return { modeLabelColor };
}
