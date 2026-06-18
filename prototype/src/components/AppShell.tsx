import { Link, NavLink, useLocation } from 'react-router-dom';
import { Activity, FileDown, Menu, PanelLeftClose, PanelLeftOpen, ShieldCheck, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useAppSession } from '../app/session/AppSessionContext';
import { filterMenuByRole } from '../routes/roleAccess';
import { ContextSummaryBar } from './ContextSummaryBar';
import { RoleSwitcher } from './RoleSwitcher';

export function AppShell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const { role, currentSample, currentPackage } = useAppSession();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  const closeMobileNav = () => setMobileOpen(false);
  const shellClass = `app-shell ${collapsed ? 'nav-collapsed' : ''} ${mobileOpen ? 'mobile-nav-open' : ''}`;
  const visibleMenu = useMemo(() => filterMenuByRole(role), [role]);
  const visibleItems = useMemo(() => visibleMenu.flatMap((group) => group.children ?? []), [visibleMenu]);
  const processSteps = useMemo(
    () =>
      [
        { id: 'readiness', label: '样本' },
        { id: 'ranking', label: '排行' },
        { id: 'loop-evidence', label: '证据' },
        { id: 'review', label: '审核' },
        { id: 'evidence-package', label: '证据包' },
        { id: 'sponsor', label: '汇报' },
      ]
        .map((step) => {
          const route = visibleItems.find((item) => item.id === step.id);
          return route ? { ...step, path: route.path } : null;
        })
        .filter((step): step is { id: string; label: string; path: string } => step !== null),
    [visibleItems]
  );

  const isItemActive = (path: string) => {
    if (path.startsWith('/diagnosis/loop/')) {
      return location.pathname.startsWith('/diagnosis/loop/');
    }
    return location.pathname === path;
  };

  return (
    <div className={shellClass}>
      <button className="mobile-nav-toggle" type="button" onClick={() => setMobileOpen(true)} aria-label="打开导航">
        <Menu size={20} />
      </button>
      {mobileOpen ? <button className="nav-scrim" type="button" aria-label="关闭导航遮罩" onClick={closeMobileNav} /> : null}
      <aside className="side-nav" aria-label="主导航">
        <div className="nav-topline">
          <Link className="brand" to="/" onClick={closeMobileNav} aria-label="CLPM 工程首页">
            <Activity size={24} aria-hidden="true" />
            <span>CLPM</span>
          </Link>
          <button className="icon-button desktop-collapse" type="button" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? '展开导航' : '折叠导航'}>
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          </button>
          <button className="icon-button mobile-close" type="button" onClick={closeMobileNav} aria-label="关闭导航">
            <X size={18} />
          </button>
        </div>
        <nav className="process-rail" aria-label="P0 主链快捷入口">
          {processSteps.map((step, index) => (
            <NavLink key={step.path} to={step.path} onClick={closeMobileNav} className={() => `process-step ${isItemActive(step.path) ? 'active' : ''}`}>
              <span className="step-index">{index + 1}</span>
              <span className="step-label">{step.label}</span>
            </NavLink>
          ))}
        </nav>
        <Link className="brand compact-brand" to="/" onClick={closeMobileNav} aria-label="CLPM 工程首页">
          <Activity size={24} aria-hidden="true" />
          <span>CLPM</span>
        </Link>
        <nav className="nav-groups">
          {visibleMenu.map((group) => (
            <section key={group.id} className="nav-group" aria-label={group.label}>
              <div className="nav-group-title">
                <span>{group.label}</span>
                <VersionBadge value={group.version} />
              </div>
              {(group.children ?? []).map((item) => (
                <NavLink key={item.id} to={item.path} onClick={closeMobileNav} className={() => `nav-link ${isItemActive(item.path) ? 'active' : ''}`}>
                  <span>{item.label}</span>
                  <VersionBadge value={item.version} compact />
                </NavLink>
              ))}
            </section>
          ))}
        </nav>
      </aside>
      <div className="main-zone">
        <header className="top-bar" aria-label="全局状态栏">
          <button className="icon-button top-mobile-menu" type="button" onClick={() => setMobileOpen(true)} aria-label="打开导航">
            <Menu size={20} />
          </button>
          <div>
            <strong>控制回路性能治理原型</strong>
            <span>{currentSample?.name}</span>
          </div>
          <div className="top-actions">
            <RoleSwitcher />
            <span className="risk-pill"><ShieldCheck size={16} />只读 DCS / 人工实施</span>
            <span className="risk-pill">证据包 {currentPackage?.packageStatus}</span>
            <Link className="button ghost" to="/evidence"><FileDown size={16} />证据包</Link>
          </div>
        </header>
        <ContextSummaryBar />
        <main className="content" aria-label="页面主体">{children}</main>
      </div>
    </div>
  );
}

export function VersionBadge({ value, compact = false }: { value: string; compact?: boolean }) {
  return <span className={`version-badge ${compact ? 'compact' : ''}`}>{value}</span>;
}
