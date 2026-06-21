/**
 * PV 数据质量徽章（v4.0 核心组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §3.1.5 + §7.2.4 + §10.5
 *
 * PV tag 质量码（Good/Bad/Uncertain）的视觉表达：
 * | 质量码    | 颜色              | 图标 |
 * |-----------|-------------------|------|
 * | Good      | --status-ok 青绿  | ✓    |
 * | Bad       | --status-danger 红| ✖    |
 * | Uncertain | --status-warning 琥珀 | ? |
 *
 * 规则（§3.1.5）：
 * - Good：PV 实线正常显示
 * - Bad：PV 灰色虚线断线 + 悬浮提示"PV 数据质量不可信"
 * - Uncertain：PV 琥珀色虚线 + 悬浮提示"PV 数据质量不确定"
 */

import { Check, X, HelpCircle } from 'lucide-react';

export type PVQuality = 'Good' | 'Bad' | 'Uncertain';

interface PVQualityBadgeProps {
  quality: PVQuality;
  /** 是否显示图标（默认 true） */
  showIcon?: boolean;
  /** 尺寸（默认 sm） */
  size?: 'sm' | 'md';
}

const QUALITY_CONFIG: Record<PVQuality, {
  className: string;
  icon: typeof Check;
  tooltip: string;
}> = {
  Good: {
    className: 'pv-quality-good',
    icon: Check,
    tooltip: 'PV 数据质量良好',
  },
  Bad: {
    className: 'pv-quality-bad',
    icon: X,
    tooltip: 'PV 数据质量不可信',
  },
  Uncertain: {
    className: 'pv-quality-uncertain',
    icon: HelpCircle,
    tooltip: 'PV 数据质量不确定',
  },
};

export function PVQualityBadge({ quality, showIcon = true, size = 'sm' }: PVQualityBadgeProps) {
  const config = QUALITY_CONFIG[quality];
  const Icon = config.icon;
  return (
    <span
      className={`badge ${config.className} ${size === 'md' ? 'badge-md' : 'badge-sm'}`}
      title={config.tooltip}
    >
      {showIcon && <Icon size={12} />}
      <span>{quality}</span>
    </span>
  );
}
