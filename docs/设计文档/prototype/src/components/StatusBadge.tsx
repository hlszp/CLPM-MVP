/**
 * 统一状态标签（v4.0 核心组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §7.2
 *
 * 统一标签体系：必须是"颜色 + 文本 + 语义图标"三维组合，绝不能仅靠颜色传递信息。
 *
 * 覆盖：
 * - §7.2.1 计算状态（kpi_snapshot_hourly.status）：SUCCESS/INCONCLUSIVE/PARTIAL
 * - §7.2.2 处理状态（action_tracker.action_status）：PENDING/IN_PROGRESS/IGNORED/RESOLVED
 * - §7.2.3 控制模式（from MODE tag）：Manual/Auto/Cascade
 * - §7.2.5 诊断预诊标签（diagnosis_label）：自由文本
 */

import { Check, HelpCircle, AlertTriangle, Clock, RefreshCw, Minus } from 'lucide-react';

/** 计算状态（§7.2.1） */
export type ComputeStatus = 'SUCCESS' | 'INCONCLUSIVE' | 'PARTIAL';

/** 处理状态（§7.2.2） */
export type ActionStatus = 'PENDING' | 'IN_PROGRESS' | 'IGNORED' | 'RESOLVED';

/** 控制模式（§7.2.3，from MODE tag） */
export type ControlMode = 'Manual' | 'Auto' | 'Cascade';

interface BadgeConfig {
  className: string;
  text: string;
  icon: typeof Check;
}

const COMPUTE_STATUS_CONFIG: Record<ComputeStatus, BadgeConfig> = {
  SUCCESS: { className: 'status-success', text: '正常', icon: Check },
  INCONCLUSIVE: { className: 'status-neutral', text: '数据不足', icon: HelpCircle },
  PARTIAL: { className: 'status-warning', text: '部分计算', icon: AlertTriangle },
};

const ACTION_STATUS_CONFIG: Record<ActionStatus, BadgeConfig> = {
  PENDING: { className: 'status-danger', text: '待处理', icon: Clock },
  IN_PROGRESS: { className: 'status-info', text: '处理中', icon: RefreshCw },
  IGNORED: { className: 'status-neutral', text: '已忽略', icon: Minus },
  RESOLVED: { className: 'status-success', text: '已实施', icon: Check },
};

const CONTROL_MODE_CONFIG: Record<ControlMode, BadgeConfig> = {
  Manual: { className: 'status-danger', text: '手动', icon: Minus },
  Auto: { className: 'status-success', text: '自动', icon: Check },
  Cascade: { className: 'status-info', text: '串级', icon: RefreshCw },
};

interface StatusBadgeProps {
  size?: 'sm' | 'md';
}

export function ComputeStatusBadge({ status, size = 'sm' }: { status: ComputeStatus } & StatusBadgeProps) {
  const config = COMPUTE_STATUS_CONFIG[status];
  const Icon = config.icon;
  return (
    <span className={`badge ${config.className} ${size === 'md' ? 'badge-md' : 'badge-sm'}`}>
      <Icon size={12} />
      <span>{config.text}</span>
    </span>
  );
}

export function ActionStatusBadge({ status, size = 'sm' }: { status: ActionStatus } & StatusBadgeProps) {
  const config = ACTION_STATUS_CONFIG[status];
  const Icon = config.icon;
  return (
    <span className={`badge ${config.className} ${size === 'md' ? 'badge-md' : 'badge-sm'}`}>
      <Icon size={12} />
      <span>{config.text}</span>
    </span>
  );
}

export function ControlModeBadge({ mode, size = 'sm' }: { mode: ControlMode } & StatusBadgeProps) {
  const config = CONTROL_MODE_CONFIG[mode];
  const Icon = config.icon;
  return (
    <span className={`badge ${config.className} ${size === 'md' ? 'badge-md' : 'badge-sm'}`}>
      <Icon size={12} />
      <span>{config.text}</span>
    </span>
  );
}

/**
 * 诊断预诊标签（§7.2.5，自由文本）
 * 根据标签内容推断颜色：
 * - 含"粘滞"→ 琥珀
 * - 含"过激"/"振荡"→ 警示红
 * - 含"不明"/"人工"→ 冷灰
 * - 默认 → 中性
 */
export function DiagnosisLabelBadge({ label, size = 'sm' }: { label: string } & StatusBadgeProps) {
  let className = 'status-neutral';
  if (label.includes('粘滞')) className = 'status-warning';
  else if (label.includes('过激') || label.includes('振荡')) className = 'status-danger';
  else if (label.includes('不明') || label.includes('人工')) className = 'status-neutral';

  return (
    <span className={`badge ${className} ${size === 'md' ? 'badge-md' : 'badge-sm'}`}>
      <AlertTriangle size={12} />
      <span>{label}</span>
    </span>
  );
}

/**
 * 评分色块（§3.1.4 + §10.1）
 * | 区间        | 颜色     | 语义 |
 * |------------|----------|------|
 * | >= 80      | 青绿     | 优良 |
 * | 60-79      | 琥珀     | 关注 |
 * | < 60       | 警示红   | 低效 |
 * | INCONCLUSIVE | 冷灰   | 显示"—"非 0 |
 */
export function ScoreBadge({ score, size = 'sm' }: { score: number | null } & StatusBadgeProps) {
  let className: string;
  let display: string;
  if (score === null) {
    className = 'status-neutral';
    display = '—';
  } else if (score >= 80) {
    className = 'status-success';
    display = String(score);
  } else if (score >= 60) {
    className = 'status-warning';
    display = String(score);
  } else {
    className = 'status-danger';
    display = String(score);
  }
  return (
    <span className={`badge ${className} ${size === 'md' ? 'badge-md' : 'badge-sm'} mono`}>
      {display}
    </span>
  );
}
