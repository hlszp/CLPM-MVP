/**
 * PV 质量码波形渲染样式常量
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §3.1.5 + §10.5
 *
 * 根据 PV 质量码返回 ECharts line 样式片段：
 * - Good：实线正常显示
 * - Bad：灰色虚线断线
 * - Uncertain：琥珀色虚线
 *
 * 注意：SP/OP 线不受 PV 质量码影响，始终正常显示。
 */

import type { PVQuality } from './PVQualityBadge';

export const PV_QUALITY_LINE_STYLE: Record<PVQuality, {
  color: string;
  type: 'solid' | 'dashed';
  cssVar: string;
}> = {
  Good: {
    color: '#198754',
    type: 'solid',
    cssVar: '--status-ok',
  },
  Bad: {
    color: '#6C757D',
    type: 'dashed',
    cssVar: '--status-neutral',
  },
  Uncertain: {
    color: '#FFC107',
    type: 'dashed',
    cssVar: '--status-warning',
  },
};
