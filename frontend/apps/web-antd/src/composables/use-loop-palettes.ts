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
 * 回路类型分类色（整改 A-02：类别中性化，统一 slate-500）
 * 用于统计卡片标识 / ECharts 柱状图等单色相类别场景
 */
export const LOOP_TYPE_COLOR_MAP: Record<string, string> = {
  TEMPERATURE: '#64748b',
  PRESSURE: '#64748b',
  LEVEL: '#64748b',
  FLOW: '#64748b',
  ANALYSIS: '#64748b',
  SPEED: '#64748b',
  OTHER: '#64748b',
};

/** MODE 数值 → 状态语义色（0=手动,1=自动,2=串级,3=远程,4=先控）
 * 整改 A-01 对齐色彩约定表：手动=警示红 / 自动=正常绿 / 串级·远程·先控=工业蓝 */
export const MODE_COLOR_MAP: Record<string, string> = {
  '0': '#dc3545', // 手动-需关注
  '1': '#198754', // 自动-正常
  '2': '#0d6efd', // 串级
  '3': '#0d6efd', // 远程
  '4': '#0d6efd', // 先控
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
