/**
 * 全局 Toast 通知（v4.0 通用组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §8.2
 *
 * 用于配置保存成功、Tracker 状态变更等操作的即时反馈。
 * 3 秒后自动消失，支持 success/warning/error 三种类型。
 */

import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';
import { CheckCircle2, AlertTriangle, XCircle, X } from 'lucide-react';

type ToastType = 'success' | 'warning' | 'error';

interface ToastItem {
  id: number;
  type: ToastType;
  message: string;
}

interface ToastContextValue {
  show: (type: ToastType, message: string) => void;
  success: (message: string) => void;
  warning: (message: string) => void;
  error: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const remove = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback((type: ToastType, message: string) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, type, message }]);
    setTimeout(() => remove(id), 3000);
  }, [remove]);

  const value: ToastContextValue = {
    show,
    success: (msg) => show('success', msg),
    warning: (msg) => show('warning', msg),
    error: (msg) => show('error', msg),
  };

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-container">
        {toasts.map((t) => {
          const Icon = t.type === 'success' ? CheckCircle2 : t.type === 'warning' ? AlertTriangle : XCircle;
          return (
            <div key={t.id} className={`toast toast-${t.type}`}>
              <Icon size={18} />
              <span>{t.message}</span>
              <button type="button" className="toast-close" onClick={() => remove(t.id)}>
                <X size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}
