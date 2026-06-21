/**
 * 角色上下文（v4.0 全局状态）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §5
 *
 * 将角色状态从 App.tsx 提升到全局 Context，
 * 使各页面可以读取当前角色来决定操作按钮的可见性（§5.2 权限矩阵）。
 */

import { createContext, useContext, useState, type ReactNode } from 'react';
import type { Role } from '../routes/menuConfig';
import { ROLE_DEFAULT_HOME } from '../routes/menuConfig';

interface RoleContextValue {
  role: Role;
  setRole: (role: Role) => void;
}

const RoleContext = createContext<RoleContextValue | null>(null);

export function RoleProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<Role>('仪控工程师');

  const setRole = (r: Role) => {
    setRoleState(r);
    // 角色切换时跳转到默认首页（由 App.tsx 的 useEffect 处理实际跳转）
    // 这里只负责状态更新，跳转逻辑保留在 App.tsx 以访问 navigate
    const event = new CustomEvent('role-change', { detail: { role: r, home: ROLE_DEFAULT_HOME[r] } });
    window.dispatchEvent(event);
  };

  return (
    <RoleContext.Provider value={{ role, setRole }}>
      {children}
    </RoleContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useRole() {
  const ctx = useContext(RoleContext);
  if (!ctx) throw new Error('useRole must be used within RoleProvider');
  return ctx;
}
