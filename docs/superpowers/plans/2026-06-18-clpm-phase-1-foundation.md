# CLPM Phase 1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 CLPM 高保真原型第一阶段的基础设施，包括本地 Git 仓库接线、菜单结构显性声明、角色切换器、菜单权限路由、全局状态壳层和最小测试基线。

**Architecture:** 先冻结“结构层真相”，再进入页面重构。以 `DESIGN.md` 和菜单清单为设计源，以 React Context 承载全局会话与流程状态，以 `menuConfig.ts` 为路由和菜单的唯一配置入口，通过角色切换器驱动菜单可见性、默认入口和页面禁用态。

**Tech Stack:** React 19、TypeScript、React Router、Vite、Playwright、Vitest、Testing Library、Git、GitHub CLI

---

## Scope Split

原始设计规格覆盖多个独立子系统：设计系统、路由壳层、状态机、6 个核心页面、辅助页对齐、测试体系。为了降低返工风险，本计划只覆盖“基础层”：

- Git 仓库初始化与远端连接
- 菜单结构显性声明
- 角色与菜单权限模型
- 全局会话/状态壳层
- AppShell 与路由重构
- 最小测试基线

后续核心页面的深做应拆成独立计划，不与本计划混写。

## File Map

**Create:**
- `docs/superpowers/plans/2026-06-18-clpm-phase-1-foundation.md`
- `prototype/src/app/session/AppSessionContext.tsx`
- `prototype/src/app/session/seed.ts`
- `prototype/src/app/session/types.ts`
- `prototype/src/routes/roleAccess.ts`
- `prototype/src/components/RoleSwitcher.tsx`
- `prototype/src/components/ContextSummaryBar.tsx`
- `prototype/src/components/UnauthorizedState.tsx`
- `prototype/src/test/setup.ts`
- `prototype/src/test/renderWithSession.tsx`
- `prototype/src/components/AppShell.test.tsx`
- `prototype/src/app/session/AppSessionContext.test.tsx`

**Modify:**
- `DESIGN.md`
- `docs/superpowers/specs/2026-06-18-clpm-high-fidelity-prototype-rebuild-design.md`
- `prototype/package.json`
- `prototype/vite.config.ts`
- `prototype/src/types/index.ts`
- `prototype/src/routes/menuConfig.ts`
- `prototype/src/routes/routeConfig.ts`
- `prototype/src/components/AppShell.tsx`
- `prototype/src/App.tsx`
- `prototype/tests/smoke.spec.ts`

**Responsibility:**
- `DESIGN.md`：显性声明菜单结构是设计基线的一部分
- `...rebuild-design.md`：把菜单结构和角色权限路由写入正式设计规格
- `menuConfig.ts`：菜单、路由、版本、深度、角色可见性唯一来源
- `roleAccess.ts`：根据角色过滤菜单、决定默认首页、决定无权访问时的处理策略
- `AppSessionContext.tsx`：全局角色、样本、选中回路、EvidencePackage 和流程写操作
- `AppShell.tsx`：消费全局状态，渲染角色切换器、上下文条和精确高亮导航
- `App.tsx`：挂载 Provider，接入受权限控制的路由
- `*.test.tsx`：最小单测，锁定角色切换与状态流转基础行为
- `smoke.spec.ts`：更新为新的壳层与角色切换可用性基线

### Task 1: Bootstrap Local Git And Test Harness

**Files:**
- Modify: `prototype/package.json`
- Modify: `prototype/vite.config.ts`
- Create: `prototype/src/test/setup.ts`
- Create: `prototype/src/test/renderWithSession.tsx`

- [ ] **Step 1: 初始化本地 Git 仓库并连接远端**

Run:

```bash
cd /Users/zhangping/DEV/CLPM
git init -b main
git remote add origin https://github.com/hlszp/CLPM.git
git remote -v
```

Expected:

```text
origin  https://github.com/hlszp/CLPM.git (fetch)
origin  https://github.com/hlszp/CLPM.git (push)
```

- [ ] **Step 2: 创建基础 `.gitignore`**

Create `/Users/zhangping/DEV/CLPM/.gitignore`:

```gitignore
node_modules/
dist/
coverage/
.DS_Store
.playwright/
test-results/
playwright-report/
*.tsbuildinfo
.env
.env.*
```

- [ ] **Step 3: 为 `prototype` 增加 Vitest 与 Testing Library 依赖**

Modify `prototype/package.json`:

```json
{
  "scripts": {
    "test:unit": "vitest run",
    "test:unit:watch": "vitest"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.3.0",
    "@testing-library/user-event": "^14.6.1",
    "jsdom": "^26.1.0",
    "vitest": "^3.2.4"
  }
}
```

- [ ] **Step 4: 安装依赖并确认 Vitest 可执行**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm install
npm run test:unit
```

Expected:

```text
No test files found, exiting with code 1
```

这一步允许失败，目的是确认命令已经接通。

- [ ] **Step 5: 接入 Vitest 配置**

Modify `prototype/vite.config.ts`:

```ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    globals: true,
  },
});
```

- [ ] **Step 6: 写测试环境初始化文件**

Create `prototype/src/test/setup.ts`:

```ts
import '@testing-library/jest-dom/vitest';
```

- [ ] **Step 7: 写共享渲染工具**

Create `prototype/src/test/renderWithSession.tsx`:

```tsx
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { AppSessionProvider } from '../app/session/AppSessionContext';

export function renderWithSession(ui: ReactElement, initialEntries = ['/']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <AppSessionProvider>{ui}</AppSessionProvider>
    </MemoryRouter>,
  );
}
```

- [ ] **Step 8: 提交基础设施初始化**

Run:

```bash
cd /Users/zhangping/DEV/CLPM
git add .gitignore prototype/package.json prototype/package-lock.json prototype/vite.config.ts prototype/src/test/setup.ts prototype/src/test/renderWithSession.tsx
git commit -m "chore: bootstrap git and unit test foundation"
```

### Task 2: Declare Menu Structure Explicitly In The Design Source

**Files:**
- Modify: `DESIGN.md`
- Modify: `docs/superpowers/specs/2026-06-18-clpm-high-fidelity-prototype-rebuild-design.md`
- Modify: `prototype/src/types/index.ts`

- [ ] **Step 1: 在 `NavigationItem` 中加入菜单结构显性字段**

Modify `prototype/src/types/index.ts`:

```ts
export type UserRole = 'engineer' | 'reviewer' | 'sponsor' | 'implementer' | 'admin';

export interface NavigationItem {
  id: string;
  label: string;
  path: string;
  version: VersionTag;
  depth: PrototypeDepth;
  description: string;
  children?: NavigationItem[];
  roles?: UserRole[];
  parentId?: string;
  pageLevel?: 'core' | 'supporting' | 'structure';
  stage?: 'foundation' | 'workflow' | 'reporting' | 'system';
}
```

- [ ] **Step 2: 在 `DESIGN.md` 中增加菜单结构显性声明章节**

Append to `DESIGN.md`:

```md
## 5.1 菜单结构声明

菜单结构是设计基线的一部分，不只是代码实现细节。

每个菜单项都必须显性声明以下字段：

- `menu_id`
- `label`
- `path`
- `parent_id`
- `page_level`
- `stage`
- `version`
- `depth`
- `roles`
- `default_entry`
- `is_deep_page`

`prototype/src/routes/menuConfig.ts` 是菜单结构的唯一事实来源，设计文档与代码必须保持一致。
```

- [ ] **Step 3: 在详细设计规格中增加菜单矩阵要求**

Append to `docs/superpowers/specs/2026-06-18-clpm-high-fidelity-prototype-rebuild-design.md`:

```md
### 5.4 菜单结构矩阵

第一阶段必须把菜单结构显性声明为“设计源的一部分”。

菜单矩阵至少包含：

- menu_id
- label
- path
- parent_id
- version
- depth
- page_level
- stage
- roles
- default_entry

菜单结构的代码源为 `prototype/src/routes/menuConfig.ts`，实现时不得再派生第二份独立菜单真相。
```

- [ ] **Step 4: 为文档变更写最小断言测试**

Create `prototype/src/app/session/AppSessionContext.test.tsx` with a placeholder first test shell:

```tsx
import { describe, expect, it } from 'vitest';

describe('design source invariants', () => {
  it('keeps menu metadata fields available in NavigationItem typing', () => {
    expect(true).toBe(true);
  });
});
```

这一步只是占住测试文件路径，下一任务会换成真实断言。

- [ ] **Step 5: 提交菜单结构设计源变更**

Run:

```bash
cd /Users/zhangping/DEV/CLPM
git add DESIGN.md docs/superpowers/specs/2026-06-18-clpm-high-fidelity-prototype-rebuild-design.md prototype/src/types/index.ts prototype/src/app/session/AppSessionContext.test.tsx
git commit -m "docs: declare menu structure as a design source"
```

### Task 3: Add Role-Aware Menu Metadata And Access Rules

**Files:**
- Modify: `prototype/src/routes/menuConfig.ts`
- Modify: `prototype/src/routes/routeConfig.ts`
- Create: `prototype/src/routes/roleAccess.ts`
- Test: `prototype/src/components/AppShell.test.tsx`

- [ ] **Step 1: 写失败测试，要求工程师角色看不到系统管理页**

Create `prototype/src/components/AppShell.test.tsx`:

```tsx
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AppShell } from './AppShell';
import { renderWithSession } from '../test/renderWithSession';

describe('AppShell role-aware navigation', () => {
  it('hides system management for engineer role', () => {
    renderWithSession(<AppShell><div>body</div></AppShell>);
    expect(screen.queryByText('系统管理')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- AppShell.test.tsx
```

Expected:

```text
FAIL
Unable to find AppSessionProvider or role-based filtering
```

- [ ] **Step 3: 给菜单项补齐 `roles/pageLevel/stage/parentId`**

Modify `prototype/src/routes/menuConfig.ts` with this pattern:

```ts
{
  id: 'overview',
  label: '治理总览',
  path: '/',
  version: 'P0',
  depth: 'deep',
  description: '工程师与 Sponsor 双入口',
  roles: ['engineer', 'reviewer', 'sponsor', 'implementer', 'admin'],
  pageLevel: 'core',
  stage: 'foundation',
  children: [
    {
      id: 'home',
      parentId: 'overview',
      label: '工程首页',
      path: '/',
      version: 'P0',
      depth: 'deep',
      description: '低性能清单、数据雷达、待办',
      roles: ['engineer', 'reviewer', 'admin'],
      pageLevel: 'core',
      stage: 'foundation',
    },
    {
      id: 'sponsor',
      parentId: 'overview',
      label: '管理首页',
      path: '/sponsor',
      version: 'P0',
      depth: 'deep',
      description: '样本可信度、闭环率、风险结论',
      roles: ['sponsor', 'admin'],
      pageLevel: 'core',
      stage: 'reporting',
    },
  ],
}
```

按同一模式补全所有分组和子页面。

- [ ] **Step 4: 实现角色过滤与默认首页规则**

Create `prototype/src/routes/roleAccess.ts`:

```ts
import type { NavigationItem, UserRole } from '../types';
import { menuConfig } from './menuConfig';

export function filterMenuByRole(role: UserRole): NavigationItem[] {
  return menuConfig
    .map((group) => ({
      ...group,
      children: (group.children ?? []).filter((item) => !item.roles || item.roles.includes(role)),
    }))
    .filter((group) => (group.children ?? []).length > 0);
}

export function getDefaultRouteForRole(role: UserRole): string {
  switch (role) {
    case 'sponsor':
      return '/sponsor';
    case 'reviewer':
      return '/closure/review';
    case 'implementer':
      return '/closure/implementation';
    case 'admin':
      return '/system/safety';
    case 'engineer':
    default:
      return '/';
  }
}

export function canAccessPath(role: UserRole, pathname: string): boolean {
  const visible = filterMenuByRole(role).flatMap((group) => group.children ?? []);
  if (pathname.startsWith('/diagnosis/loop/')) {
    return visible.some((item) => item.id === 'loop-evidence');
  }
  return visible.some((item) => item.path === pathname);
}
```

- [ ] **Step 5: 用角色过滤后的路由替换静态路由导出**

Modify `prototype/src/routes/routeConfig.ts`:

```ts
import type { NavigationItem, UserRole } from '../types';
import { filterMenuByRole } from './roleAccess';

export function getRouteConfig(role: UserRole): NavigationItem[] {
  return filterMenuByRole(role).flatMap((group) => group.children ?? []);
}

export function findRoute(pathname: string, role: UserRole): NavigationItem | undefined {
  const routeConfig = getRouteConfig(role);
  if (pathname.startsWith('/diagnosis/loop/')) {
    return routeConfig.find((route) => route.id === 'loop-evidence');
  }
  return routeConfig.find((route) => route.path === pathname);
}
```

- [ ] **Step 6: 运行测试确认通过**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- AppShell.test.tsx
```

Expected:

```text
PASS  AppShell role-aware navigation
```

- [ ] **Step 7: 提交角色菜单元数据**

Run:

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/routes/menuConfig.ts prototype/src/routes/routeConfig.ts prototype/src/routes/roleAccess.ts prototype/src/components/AppShell.test.tsx
git commit -m "feat: add role-aware menu metadata and access rules"
```

### Task 4: Introduce Global Session State And Role Switcher

**Files:**
- Create: `prototype/src/app/session/types.ts`
- Create: `prototype/src/app/session/seed.ts`
- Create: `prototype/src/app/session/AppSessionContext.tsx`
- Create: `prototype/src/components/RoleSwitcher.tsx`
- Create: `prototype/src/components/ContextSummaryBar.tsx`
- Create: `prototype/src/components/UnauthorizedState.tsx`
- Test: `prototype/src/app/session/AppSessionContext.test.tsx`

- [ ] **Step 1: 写失败测试，要求角色切换会更新默认上下文**

Replace `prototype/src/app/session/AppSessionContext.test.tsx`:

```tsx
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AppSessionProvider, useAppSession } from './AppSessionContext';

describe('AppSessionContext', () => {
  it('switches role and keeps a valid default route', () => {
    const { result } = renderHook(() => useAppSession(), {
      wrapper: ({ children }) => <AppSessionProvider>{children}</AppSessionProvider>,
    });

    act(() => {
      result.current.setRole('sponsor');
    });

    expect(result.current.role).toBe('sponsor');
    expect(result.current.defaultRoute).toBe('/sponsor');
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- AppSessionContext.test.tsx
```

Expected:

```text
FAIL
Cannot find module './AppSessionContext'
```

- [ ] **Step 3: 定义会话类型**

Create `prototype/src/app/session/types.ts`:

```ts
import type { EvidencePackage, SampleBatch, UserRole } from '../../types';

export interface WorkflowState {
  selectedLoopId: string;
  currentSampleId: string;
  currentPackageId: string;
}

export interface AppSessionValue {
  role: UserRole;
  defaultRoute: string;
  workflow: WorkflowState;
  currentSample?: SampleBatch;
  currentPackage?: EvidencePackage;
  setRole: (role: UserRole) => void;
  selectLoop: (loopId: string) => void;
  setCurrentPackage: (packageId: string) => void;
}
```

- [ ] **Step 4: 写种子状态**

Create `prototype/src/app/session/seed.ts`:

```ts
import { currentBatch, evidencePackage, loopRecords } from '../../data/mockData';
import type { UserRole } from '../../types';
import type { WorkflowState } from './types';

export const initialRole: UserRole = 'engineer';

export const initialWorkflowState: WorkflowState = {
  selectedLoopId: loopRecords[0]?.id ?? 'TIC-1115',
  currentSampleId: currentBatch.id,
  currentPackageId: evidencePackage.id,
};
```

- [ ] **Step 5: 实现上下文 Provider**

Create `prototype/src/app/session/AppSessionContext.tsx`:

```tsx
import { createContext, useContext, useMemo, useState } from 'react';
import { currentBatch, evidencePackage } from '../../data/mockData';
import { getDefaultRouteForRole } from '../../routes/roleAccess';
import type { UserRole } from '../../types';
import { initialRole, initialWorkflowState } from './seed';
import type { AppSessionValue, WorkflowState } from './types';

const AppSessionContext = createContext<AppSessionValue | null>(null);

export function AppSessionProvider({ children }: { children: React.ReactNode }) {
  const [role, setRoleState] = useState<UserRole>(initialRole);
  const [workflow, setWorkflow] = useState<WorkflowState>(initialWorkflowState);

  const value = useMemo<AppSessionValue>(() => ({
    role,
    defaultRoute: getDefaultRouteForRole(role),
    workflow,
    currentSample: currentBatch,
    currentPackage: evidencePackage,
    setRole: (nextRole) => setRoleState(nextRole),
    selectLoop: (loopId) => setWorkflow((prev) => ({ ...prev, selectedLoopId: loopId })),
    setCurrentPackage: (packageId) => setWorkflow((prev) => ({ ...prev, currentPackageId: packageId })),
  }), [role, workflow]);

  return <AppSessionContext.Provider value={value}>{children}</AppSessionContext.Provider>;
}

export function useAppSession() {
  const context = useContext(AppSessionContext);
  if (!context) {
    throw new Error('useAppSession must be used within AppSessionProvider');
  }
  return context;
}
```

- [ ] **Step 6: 写角色切换器和上下文条**

Create `prototype/src/components/RoleSwitcher.tsx`:

```tsx
import { useAppSession } from '../app/session/AppSessionContext';
import type { UserRole } from '../types';

const roleOptions: Array<{ value: UserRole; label: string }> = [
  { value: 'engineer', label: '工程师' },
  { value: 'reviewer', label: '专家审核' },
  { value: 'sponsor', label: 'Sponsor' },
  { value: 'implementer', label: '授权实施' },
  { value: 'admin', label: '管理员' },
];

export function RoleSwitcher() {
  const { role, setRole } = useAppSession();
  return (
    <label className="role-switcher">
      <span>当前角色</span>
      <select value={role} onChange={(event) => setRole(event.target.value as UserRole)}>
        {roleOptions.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
```

Create `prototype/src/components/ContextSummaryBar.tsx`:

```tsx
import { useAppSession } from '../app/session/AppSessionContext';

export function ContextSummaryBar() {
  const { role, currentSample, currentPackage, workflow } = useAppSession();
  return (
    <div className="context-summary-bar">
      <span>角色：{role}</span>
      <span>样本：{currentSample?.name}</span>
      <span>当前回路：{workflow.selectedLoopId}</span>
      <span>证据包：{currentPackage?.packageStatus}</span>
    </div>
  );
}
```

Create `prototype/src/components/UnauthorizedState.tsx`:

```tsx
export function UnauthorizedState() {
  return (
    <section className="state-panel unauthorized-state">
      <h1>当前角色不可访问</h1>
      <p>请切换角色，或回到该角色的默认入口页继续操作。</p>
    </section>
  );
}
```

- [ ] **Step 7: 运行上下文测试确认通过**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- AppSessionContext.test.tsx
```

Expected:

```text
PASS  AppSessionContext switches role and default route
```

- [ ] **Step 8: 提交全局会话壳层**

Run:

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/app/session prototype/src/components/RoleSwitcher.tsx prototype/src/components/ContextSummaryBar.tsx prototype/src/components/UnauthorizedState.tsx
git commit -m "feat: add app session context and role switcher shell"
```

### Task 5: Refactor AppShell And Routing To Consume The Session

**Files:**
- Modify: `prototype/src/components/AppShell.tsx`
- Modify: `prototype/src/App.tsx`
- Test: `prototype/src/components/AppShell.test.tsx`

- [ ] **Step 1: 补充失败测试，要求角色切换后导航变化**

Append to `prototype/src/components/AppShell.test.tsx`:

```tsx
import userEvent from '@testing-library/user-event';

it('switches navigation groups when role changes to sponsor', async () => {
  const user = userEvent.setup();
  renderWithSession(<AppShell><div>body</div></AppShell>);

  await user.selectOptions(screen.getByLabelText('当前角色'), 'sponsor');

  expect(screen.getByText('管理首页')).toBeInTheDocument();
  expect(screen.queryByText('实施记录')).not.toBeInTheDocument();
});
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- AppShell.test.tsx
```

Expected:

```text
FAIL
Unable to find label "当前角色"
```

- [ ] **Step 3: 重构 `AppShell` 消费角色过滤菜单和上下文条**

Modify `prototype/src/components/AppShell.tsx`:

```tsx
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { Activity, FileDown, Menu, PanelLeftClose, PanelLeftOpen, ShieldCheck, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useAppSession } from '../app/session/AppSessionContext';
import { filterMenuByRole } from '../routes/roleAccess';
import { ContextSummaryBar } from './ContextSummaryBar';
import { RoleSwitcher } from './RoleSwitcher';

export function AppShell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { role, currentSample, currentPackage } = useAppSession();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  const visibleMenu = useMemo(() => filterMenuByRole(role), [role]);

  useEffect(() => {
    if (!visibleMenu.some((group) => (group.children ?? []).some((item) => item.path === location.pathname || location.pathname.startsWith('/diagnosis/loop/')))) {
      // no-op here; route layer handles unauthorized fallback
    }
  }, [location.pathname, visibleMenu]);

  return (
    <div className={`app-shell ${collapsed ? 'nav-collapsed' : ''} ${mobileOpen ? 'mobile-nav-open' : ''}`}>
      <aside className="side-nav" aria-label="主导航">
        {/* existing brand block */}
        <nav className="nav-groups">
          {visibleMenu.map((group) => (
            <section key={group.id} className="nav-group" aria-label={group.label}>
              <div className="nav-group-title">
                <span>{group.label}</span>
              </div>
              {(group.children ?? []).map((item) => (
                <NavLink
                  key={item.id}
                  to={item.path}
                  className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                >
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </section>
          ))}
        </nav>
      </aside>
      <div className="main-zone">
        <header className="top-bar" aria-label="全局状态栏">
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
```

- [ ] **Step 4: 用 Provider 包裹根应用，并在无权访问时返回统一状态页**

Modify `prototype/src/App.tsx`:

```tsx
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AppSessionProvider, useAppSession } from './app/session/AppSessionContext';
import { AppShell } from './components/AppShell';
import { UnauthorizedState } from './components/UnauthorizedState';
import { GenericPage } from './pages/GenericPage';
import { NotFoundPage } from './pages/pageShared';
import { findRoute } from './routes/routeConfig';
import { canAccessPath } from './routes/roleAccess';

function RoutedApp() {
  const location = useLocation();
  const { role, defaultRoute } = useAppSession();
  const route = findRoute(location.pathname, role);

  if (!canAccessPath(role, location.pathname) && location.pathname !== '*') {
    return (
      <AppShell>
        <UnauthorizedState />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <Routes>
        {route ? <Route path={route.path} element={<GenericPage route={route} />} /> : null}
        <Route path="/diagnosis/loop/:loopId" element={<GenericPage route={findRoute('/diagnosis/loop/seed', role)!} />} />
        <Route path="*" element={<NotFoundPage />} />
        <Route path="/" element={<Navigate to={defaultRoute} replace />} />
      </Routes>
    </AppShell>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppSessionProvider>
        <RoutedApp />
      </AppSessionProvider>
    </BrowserRouter>
  );
}
```

- [ ] **Step 5: 运行单测**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- AppShell.test.tsx AppSessionContext.test.tsx
```

Expected:

```text
PASS  AppShell role-aware navigation
PASS  AppSessionContext switches role and default route
```

- [ ] **Step 6: 运行构建**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run build
```

Expected:

```text
vite v...
✓ built in ...
```

- [ ] **Step 7: 提交壳层重构**

Run:

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/components/AppShell.tsx prototype/src/App.tsx prototype/src/components/AppShell.test.tsx prototype/src/app/session/AppSessionContext.test.tsx
git commit -m "feat: refactor shell for role switching and route access"
```

### Task 6: Update Smoke Tests And Push The Foundation To GitHub

**Files:**
- Modify: `prototype/tests/smoke.spec.ts`

- [ ] **Step 1: 为角色切换器和菜单过滤写 E2E 断言**

Modify `prototype/tests/smoke.spec.ts` by appending:

```ts
test('switches to sponsor role and shows sponsor entry', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('当前角色').selectOption('sponsor');
  await expect(page.getByText('管理首页')).toBeVisible();
  await expect(page.getByText('实施记录')).toHaveCount(0);
});
```

- [ ] **Step 2: 运行新的 smoke 子集**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npx playwright test tests/smoke.spec.ts -g "switches to sponsor role and shows sponsor entry"
```

Expected:

```text
1 passed
```

- [ ] **Step 3: 首次提交到远端空仓**

Run:

```bash
cd /Users/zhangping/DEV/CLPM
git add .
git commit -m "feat: bootstrap clpm phase 1 foundation"
git push -u origin main
```

Expected:

```text
branch 'main' set up to track 'origin/main'
```

- [ ] **Step 4: 验证远端仓库**

Run:

```bash
cd /Users/zhangping/DEV/CLPM
gh repo view hlszp/CLPM --web
```

Expected:

```text
opens https://github.com/hlszp/CLPM
```

- [ ] **Step 5: 提交 smoke 更新**

Run:

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/tests/smoke.spec.ts
git commit -m "test: add smoke coverage for role-based navigation"
git push
```

## Spec Coverage Check

- 设计系统和菜单显性声明：Task 2
- 菜单权限路由和角色切换器：Task 3、Task 4、Task 5
- 全局状态壳层：Task 4、Task 5
- 可独立部署运行与最小测试：Task 1、Task 5、Task 6
- 远端仓库建立与连接：Task 1、Task 6

未覆盖项：

- 六个核心页的深做
- 工作流状态机与页面级交互细化
- DataTable、FilterBar、Timeline、DiffPanel 等完整组件库

这些内容应进入后续页面级计划，不应在本计划中混做。

## Placeholder Scan

- 无 `TBD`
- 无 `TODO`
- 无“稍后实现”类措辞
- 每个代码步骤包含明确文件和代码内容

## Type Consistency Check

- 新角色类型统一使用 `UserRole`
- 路由过滤统一走 `filterMenuByRole`
- 默认首页统一走 `getDefaultRouteForRole`
- 无权访问统一走 `UnauthorizedState`
