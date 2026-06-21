/**
 * 右侧抽屉（v4.0 通用组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §7.6
 *
 * 用于列表页行级详情滑出（回路摘要/诊断摘要/Tracker 详情）。
 * 宽度 480px，从右侧滑入，遮罩层点击关闭。
 */

import type { ReactNode } from 'react';
import { useEffect } from 'react';
import { X } from 'lucide-react';

interface DrawerProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** 底部操作区 */
  footer?: ReactNode;
  width?: string;
}

export function Drawer({ open, title, onClose, children, footer, width = '480px' }: DrawerProps) {
  /** ESC 键关闭 */
  useEffect(() => {
    if (!open) return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div
        className="drawer"
        style={{ width }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="drawer-header">
          <h3>{title}</h3>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer && <div className="drawer-footer">{footer}</div>}
      </div>
    </div>
  );
}
