/**
 * 空状态/Partial 状态/错误状态（v4.0 通用组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §8.1
 *
 * 五态覆盖：Loading / Empty / Error / Success / Partial
 */

import type { ReactNode } from 'react';
import { Inbox, AlertTriangle, RefreshCw, AlertCircle } from 'lucide-react';

interface EmptyStateProps {
  type: 'empty' | 'partial' | 'error';
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ type, title, description, action }: EmptyStateProps) {
  const Icon = type === 'empty' ? Inbox : type === 'partial' ? AlertCircle : AlertTriangle;
  const className = `empty-state empty-state-${type}`;

  return (
    <div className={className}>
      <Icon size={40} className="empty-state-icon" />
      <h3 className="empty-state-title">{title}</h3>
      {description && <p className="empty-state-desc">{description}</p>}
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  );
}

/** Loading 状态 */
export function LoadingState({ text = '加载中...' }: { text?: string }) {
  return (
    <div className="loading-state">
      <RefreshCw size={24} className="loading-spinner" />
      <span>{text}</span>
    </div>
  );
}

/** Partial 警告横幅（工作台用，§6.1.1） */
export function PartialBanner({ count, onAction }: { count: number; onAction?: () => void }) {
  if (count === 0) return null;
  return (
    <div className="partial-banner">
      <AlertTriangle size={16} />
      <span>
        当前有 <strong>{count}</strong> 个回路 KPI 计算结果为 PARTIAL（部分指标数据不足），评分仅供参考
      </span>
      {onAction && (
        <button type="button" className="partial-banner-action" onClick={onAction}>
          查看详情
        </button>
      )}
    </div>
  );
}
