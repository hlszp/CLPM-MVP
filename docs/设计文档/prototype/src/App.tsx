/**
 * CLPM Prototype (v4.0)
 *
 * 唯一设计输入：docs/设计文档/06-UIUX/ui-ux-design-guidelines.md (v4.0)
 *
 * - 菜单结构单一来源：src/routes/menuConfig.ts
 * - 25 条路由对齐 UI/UX §12.2
 * - 5 角色权限过滤对齐 UI/UX §5.2
 * - 角色默认首页对齐 UI/UX §5.1 + §12.4
 */

import { useEffect } from 'react';
import { Routes, Route, Link, useLocation, useNavigate } from 'react-router-dom';
import { Bell, UserCircle } from 'lucide-react';
import {
  ROLES,
  ROLE_DEFAULT_HOME,
  getVisibleMenuGroups,
  canAccess,
  type Role,
} from './routes/menuConfig';
import { useRole } from './components/RoleContext';

// 工作台
import { DashboardPage } from './pages/DashboardPage';

// 回路管理
import { FactoryPage } from './pages/loop/FactoryPage';
import { LoopLedgerPage } from './pages/loop/LoopLedgerPage';
import { TagMappingPage } from './pages/loop/TagMappingPage';
import { LoopMonitorPage } from './pages/loop/LoopMonitorPage';
import { LoopDetailPage } from './pages/loop/LoopDetailPage';

// 性能评估
import KpiDashboardPage from './pages/performance/KpiDashboardPage';
import { RankingPage } from './pages/performance/RankingPage';
import KpiConfigPage from './pages/performance/KpiConfigPage';
import EngineConfigPage from './pages/performance/EngineConfigPage';
import ScoreHistoryPage from './pages/performance/ScoreHistoryPage';

// 诊断中心
import DiagnosisListPage from './pages/diagnosis/DiagnosisListPage';
import DiagnosisConfigPage from './pages/diagnosis/DiagnosisConfigPage';
import WaveformPage from './pages/diagnosis/WaveformPage';
import TrackerPage from './pages/diagnosis/TrackerPage';
import ABComparePage from './pages/diagnosis/ABComparePage';

// 回路整定（Phase 2 原型）
import TuningPrototypePage from './pages/tuning/TuningPrototypePage';
import ModelIdentifyPage from './pages/tuning/ModelIdentifyPage';
import TuningAlgorithmPage from './pages/tuning/TuningAlgorithmPage';
import SimulationPage from './pages/tuning/SimulationPage';
import TuningStatsPage from './pages/tuning/TuningStatsPage';

// 系统管理
import UsersPage from './pages/system/UsersPage';
import AuditLogPage from './pages/system/AuditLogPage';
import ReportsPage from './pages/system/ReportsPage';
import PermissionsPage from './pages/system/PermissionsPage';
import AasConnectionPage from './pages/system/AasConnectionPage';

/** 侧边栏导航（UI/UX §4.1） */
function Sidebar({ role }: { role: Role }) {
  const location = useLocation();
  const groups = getVisibleMenuGroups(role);

  return (
    <nav className="app-sidebar-nav">
      {groups.map((group) => (
        <div key={group.group}>
          <div className="app-nav-group-label">{group.group}</div>
          {group.items.map((item) => {
            const Icon = item.icon;
            const basePath = item.path.split('/:')[0];
            const isActive =
              item.path === '/'
                ? location.pathname === '/'
                : location.pathname === basePath || location.pathname.startsWith(basePath + '/');
            return (
              <Link
                key={item.path}
                to={item.path.includes(':loopId') ? item.path.replace(':loopId', 'demo-loop') : item.path}
                className={`app-nav-item ${isActive ? 'active' : ''}`}
              >
                <Icon size={18} />
                <span>{item.label}</span>
                {item.phase !== 1 && <span className="phase-tag">P{item.phase}</span>}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

/** 顶部状态栏（UI/UX §4.1） */
function TopBar() {
  const location = useLocation();
  const { role, setRole } = useRole();
  const breadcrumb =
    location.pathname === '/'
      ? '工厂模型 / 加氢联合车间 / 性能总览'
      : `工厂模型 / 加氢联合车间 ${location.pathname}`;

  return (
    <div className="app-topbar">
      <div className="app-topbar-breadcrumb mono">{breadcrumb}</div>
      <div className="app-topbar-actions">
        <span className="text-muted" style={{ fontSize: 'var(--text-small)' }}>
          视角：
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            style={{
              padding: '2px 6px',
              marginLeft: 'var(--space-1)',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-default)',
            }}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </span>
        <Bell size={18} color="var(--text-muted)" style={{ cursor: 'pointer' }} />
        <UserCircle size={20} color="var(--text-muted)" />
      </div>
    </div>
  );
}

export default function App() {
  const { role } = useRole();
  const location = useLocation();
  const navigate = useNavigate();

  /** 角色切换时跳转到该角色默认首页（UI/UX §12.4） */
  useEffect(() => {
    const handleRoleChange = (e: Event) => {
      const detail = (e as CustomEvent).detail as { role: Role; home: string };
      if (location.pathname !== detail.home) {
        navigate(detail.home);
      }
    };
    window.addEventListener('role-change', handleRoleChange);
    return () => window.removeEventListener('role-change', handleRoleChange);
  }, [location.pathname, navigate]);

  /** 路由守卫：当前角色无权限访问时跳转默认首页 */
  useEffect(() => {
    if (!canAccess(role, location.pathname)) {
      navigate(ROLE_DEFAULT_HOME[role]);
    }
  }, [role, location.pathname, navigate]);

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-sidebar-brand">CLPM 平台</div>
        <Sidebar role={role} />
      </aside>
      <div className="app-main">
        <TopBar />
        <main className="app-content">
          <Routes>
            {/* 工作台（门户）— UI/UX §6.1 */}
            <Route path="/" element={<DashboardPage />} />

            {/* 回路管理 — UI/UX §6.2 */}
            <Route path="/loop/factory" element={<FactoryPage />} />
            <Route path="/loop/ledger" element={<LoopLedgerPage />} />
            <Route path="/loop/mapping" element={<TagMappingPage />} />
            <Route path="/loop/monitor" element={<LoopMonitorPage />} />
            <Route path="/loop/monitor/:loopId" element={<LoopDetailPage />} />

            {/* 性能评估 — UI/UX §6.3 */}
            <Route path="/performance" element={<KpiDashboardPage />} />
            <Route path="/performance/ranking" element={<RankingPage />} />
            <Route path="/performance/metrics" element={<KpiConfigPage />} />
            <Route path="/performance/rules" element={<EngineConfigPage />} />
            <Route path="/performance/analytics" element={<ScoreHistoryPage />} />

            {/* 诊断中心 — UI/UX §6.4 */}
            <Route path="/diagnosis" element={<DiagnosisListPage />} />
            <Route path="/diagnosis/metrics" element={<DiagnosisConfigPage />} />
            <Route path="/diagnosis/:loopId" element={<WaveformPage />} />
            <Route path="/diagnosis/tracker" element={<TrackerPage />} />
            <Route path="/diagnosis/analytics" element={<ABComparePage />} />

            {/* 回路整定（Phase 2 原型）— UI/UX §6.5 */}
            <Route path="/tuning" element={<TuningPrototypePage />} />
            <Route path="/tuning/identification" element={<ModelIdentifyPage />} />
            <Route path="/tuning/algorithm" element={<TuningAlgorithmPage />} />
            <Route path="/tuning/simulation" element={<SimulationPage />} />
            <Route path="/tuning/analytics" element={<TuningStatsPage />} />

            {/* 系统管理 — UI/UX §6.6 */}
            <Route path="/system/users" element={<UsersPage />} />
            <Route path="/system/audit" element={<AuditLogPage />} />
            <Route path="/system/reports" element={<ReportsPage />} />
            <Route path="/system/safety" element={<PermissionsPage />} />
            <Route path="/system/aas" element={<AasConnectionPage />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
