/**
 * CLPM Prototype Baseline Skeleton
 * 唯一设计输入：docs/设计文档/06-UIUX/ui-ux-design-guidelines.md (v3.0)
 *
 * 本文件仅提供壳层布局 + 路由骨架 + 页面占位。
 * 各页面具体实现按 UI/UX 文档 §6（页面规范）与 §7（核心组件）逐步展开。
 */

import { useState } from 'react';
import { Routes, Route, Link, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Network,
  BarChart3,
  Activity,
  ListChecks,
  Settings2,
  ShieldCheck,
  ScrollText,
  UserCircle,
  Bell,
  type LucideIcon,
} from 'lucide-react';

/** 角色定义（UI/UX §5.1） */
type Role = '仪控工程师' | '工艺/设备工程师' | 'Sponsor' | '系统管理员' | '外部专家';

/** 菜单项定义（UI/UX §4.2 主导航菜单结构） */
interface MenuItem {
  label: string;
  path: string;
  icon: LucideIcon;
  phase: 1 | 2 | 3;
  /** 可见角色；undefined 表示全角色可见 */
  roles?: Role[];
}

interface MenuGroup {
  group: string;
  items: MenuItem[];
}

const MENU: MenuGroup[] = [
  {
    group: '工作台',
    items: [
      { label: '性能总览首页', path: '/', icon: LayoutDashboard, phase: 1 },
    ],
  },
  {
    group: '工厂模型',
    items: [
      { label: '层级与回路台账', path: '/plant', icon: Network, phase: 1, roles: ['系统管理员', '仪控工程师'] },
      { label: '位号映射', path: '/plant/mapping', icon: Network, phase: 1, roles: ['系统管理员', '仪控工程师'] },
      { label: '引擎规则配置', path: '/plant/rules', icon: Network, phase: 1, roles: ['系统管理员'] },
    ],
  },
  {
    group: '性能评估',
    items: [
      { label: '全局看板', path: '/performance', icon: BarChart3, phase: 1 },
      { label: '低效回路排行', path: '/performance/ranking', icon: BarChart3, phase: 1 },
      { label: '报表中心', path: '/performance/reports', icon: BarChart3, phase: 1 },
    ],
  },
  {
    group: '诊断中心',
    items: [
      { label: '诊断列表', path: '/diagnosis', icon: Activity, phase: 1, roles: ['仪控工程师', '工艺/设备工程师', '系统管理员', '外部专家'] },
      { label: '回路诊断详情', path: '/diagnosis/:loopId', icon: Activity, phase: 1, roles: ['仪控工程师', '工艺/设备工程师', '系统管理员', '外部专家'] },
    ],
  },
  {
    group: '异常跟踪',
    items: [
      { label: 'Action Tracker', path: '/tracker', icon: ListChecks, phase: 1, roles: ['仪控工程师'] },
    ],
  },
  {
    group: '回路整定',
    items: [
      { label: '整定与仿真', path: '/tuning', icon: Settings2, phase: 2, roles: ['仪控工程师', '外部专家'] },
    ],
  },
  {
    group: '系统管理',
    items: [
      { label: '用户与角色', path: '/system/users', icon: UserCircle, phase: 1, roles: ['系统管理员'] },
      { label: '审计日志', path: '/system/audit', icon: ScrollText, phase: 1, roles: ['系统管理员'] },
      { label: '安全边界说明', path: '/system/safety', icon: ShieldCheck, phase: 1 },
    ],
  },
];

/** 页面占位组件 */
function PagePlaceholder({ title, module, section }: { title: string; module: string; section: string }) {
  return (
    <div className="page-placeholder">
      <h2>{title}</h2>
      <p>本页面为基线占位，待按 UI/UX 设计规范实现。</p>
      <p className="ref">
        参考规范：ui-ux-design-guidelines.md {section}（{module}）
      </p>
    </div>
  );
}

/** 侧边栏导航 */
function Sidebar({ role }: { role: Role }) {
  const location = useLocation();

  const isVisible = (item: MenuItem) => {
    if (!item.roles) return true;
    return item.roles.includes(role);
  };

  return (
    <nav className="app-sidebar-nav">
      {MENU.map((group) => {
        const visibleItems = group.items.filter(isVisible);
        if (visibleItems.length === 0) return null;
        return (
          <div key={group.group}>
            <div className="app-nav-group-label">{group.group}</div>
            {visibleItems.map((item) => {
              const Icon = item.icon;
              const isActive =
                item.path === '/'
                  ? location.pathname === '/'
                  : location.pathname.startsWith(item.path.split('/:')[0]);
              return (
                <Link
                  key={item.path}
                  to={item.path === '/diagnosis/:loopId' ? '/diagnosis/demo-loop' : item.path}
                  className={`app-nav-item ${isActive ? 'active' : ''}`}
                >
                  <Icon size={18} />
                  <span>{item.label}</span>
                  {item.phase !== 1 && <span className="phase-tag">P{item.phase}</span>}
                </Link>
              );
            })}
          </div>
        );
      })}
    </nav>
  );
}

/** 顶部状态栏（UI/UX §4.1） */
function TopBar({ role, setRole }: { role: Role; setRole: (r: Role) => void }) {
  const location = useLocation();
  const breadcrumb = location.pathname === '/' ? '工厂模型 / 加氢联合车间 / 全局看板' : location.pathname;

  return (
    <div className="app-topbar">
      <div className="app-topbar-breadcrumb mono">{breadcrumb}</div>
      <div className="app-topbar-actions">
        <span className="text-muted" style={{ fontSize: 'var(--text-small)' }}>
          视角：
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            style={{ padding: '2px 6px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-default)' }}
          >
            <option>仪控工程师</option>
            <option>工艺/设备工程师</option>
            <option>Sponsor</option>
            <option>系统管理员</option>
            <option>外部专家</option>
          </select>
        </span>
        <Bell size={18} color="var(--text-muted)" style={{ cursor: 'pointer' }} />
        <UserCircle size={20} color="var(--text-muted)" />
      </div>
    </div>
  );
}

export default function App() {
  const [role, setRole] = useState<Role>('仪控工程师');

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-sidebar-brand">CLPM 平台</div>
        <Sidebar role={role} />
      </aside>
      <div className="app-main">
        <TopBar role={role} setRole={setRole} />
        <main className="app-content">
          <Routes>
            <Route path="/" element={<PagePlaceholder title="性能总览首页" module="模块 A：性能总览首页" section="§6.1" />} />
            <Route path="/plant" element={<PagePlaceholder title="层级与回路台账" module="模块 B：工厂模型配置" section="§6.2" />} />
            <Route path="/plant/mapping" element={<PagePlaceholder title="位号映射" module="模块 B：工厂模型配置" section="§6.2" />} />
            <Route path="/plant/rules" element={<PagePlaceholder title="引擎规则配置" module="模块 B：工厂模型配置" section="§6.2" />} />
            <Route path="/performance" element={<PagePlaceholder title="全局看板" module="模块 C：性能评估看板" section="§6.3" />} />
            <Route path="/performance/ranking" element={<PagePlaceholder title="低效回路排行" module="模块 C：性能评估看板" section="§6.3" />} />
            <Route path="/performance/reports" element={<PagePlaceholder title="报表中心" module="模块 F：报表中心" section="§6.6" />} />
            <Route path="/diagnosis" element={<PagePlaceholder title="诊断列表" module="模块 D：诊断中心" section="§6.4" />} />
            <Route path="/diagnosis/:loopId" element={<PagePlaceholder title="回路诊断详情" module="模块 D：诊断中心" section="§6.4" />} />
            <Route path="/tracker" element={<PagePlaceholder title="Action Tracker" module="模块 E：Action Tracker" section="§6.5" />} />
            <Route path="/tuning" element={<PagePlaceholder title="整定与仿真" module="模块 G：回路整定与仿真（Phase 2）" section="§6.7" />} />
            <Route path="/system/users" element={<PagePlaceholder title="用户与角色" module="模块 H：系统管理" section="§6.8" />} />
            <Route path="/system/audit" element={<PagePlaceholder title="审计日志" module="模块 H：系统管理" section="§6.8" />} />
            <Route path="/system/safety" element={<PagePlaceholder title="安全边界说明" module="模块 I：安全边界" section="§6.9" />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
