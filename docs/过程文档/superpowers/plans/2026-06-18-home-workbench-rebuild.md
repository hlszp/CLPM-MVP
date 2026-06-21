# Home Workbench Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将首页工作台从页面内局部状态驱动的演示页，升级为接入全局 session、结构拆分清晰、可支撑后续高保真重构的工程师工作台。

**Architecture:** 保留现有 `HomePage` 路由与 demo 数据来源，但把页面拆成 `MissionStrip / PriorityQueue / EvidenceWorkspace / ActionDrawer` 四个单元，并统一通过 `AppSessionContext` 驱动当前回路。样式先从全局 `app.css` 中抽出首页专属分域，避免继续放大单文件耦合。

**Tech Stack:** React 19、TypeScript、React Router、Vitest、Testing Library、Vite

---

## File Map

**Create:**
- `prototype/src/pages/home/HomeWorkbench.tsx`
- `prototype/src/pages/home/HomeMissionStrip.tsx`
- `prototype/src/pages/home/HomePriorityQueue.tsx`
- `prototype/src/pages/home/HomeEvidenceWorkspace.tsx`
- `prototype/src/pages/home/HomeActionDrawer.tsx`
- `prototype/src/pages/home/homeWorkbench.ts`
- `prototype/src/pages/home/HomeWorkbench.test.tsx`
- `prototype/src/styles/home-workbench.css`

**Modify:**
- `prototype/src/pages/overviewPerformancePages.tsx`
- `prototype/src/app/session/AppSessionContext.tsx`
- `prototype/src/app/session/types.ts`
- `prototype/src/test/renderWithSession.tsx`
- `prototype/src/styles/app.css`
- `prototype/src/styles/tokens.css`

## Task 1: Unify Selected Loop With Session

**Files:**
- Modify: `prototype/src/app/session/types.ts`
- Modify: `prototype/src/app/session/AppSessionContext.tsx`
- Create: `prototype/src/pages/home/HomeWorkbench.test.tsx`

- [ ] **Step 1: 写失败测试，要求首页点击优先队列后更新全局当前回路**

```tsx
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { HomeWorkbench } from './HomeWorkbench';
import { renderWithSession } from '../../test/renderWithSession';

describe('HomeWorkbench session sync', () => {
  it('updates session selected loop when choosing another priority loop', async () => {
    const user = userEvent.setup();
    renderWithSession(<HomeWorkbench />);

    await user.click(screen.getByRole('button', { name: /LIC-2204/i }));

    expect(screen.getByText(/当前回路：LIC-2204/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/home/HomeWorkbench.test.tsx
```

Expected:

```text
FAIL
Cannot find module './HomeWorkbench'
```

- [ ] **Step 3: 扩展 session 类型，显式暴露当前回路与选择器**

Modify `prototype/src/app/session/types.ts`:

```ts
export interface AppSessionValue {
  role: UserRole;
  defaultRoute: string;
  workflow: WorkflowState;
  currentLoopId: string;
  currentSample?: SampleBatch;
  currentPackage?: EvidencePackage;
  setRole: (role: UserRole) => void;
  selectLoop: (loopId: string) => void;
  setCurrentPackage: (packageId: string) => void;
}
```

- [ ] **Step 4: 在 Provider 中实现 `currentLoopId`**

Modify `prototype/src/app/session/AppSessionContext.tsx`:

```tsx
const value = useMemo<AppSessionValue>(() => ({
  role,
  defaultRoute: getDefaultRouteForRole(role),
  workflow,
  currentLoopId: workflow.selectedLoopId,
  currentSample: currentBatch,
  currentPackage: evidencePackage,
  setRole: (nextRole) => setRoleState(nextRole),
  selectLoop: (loopId) => setWorkflow((prev) => ({ ...prev, selectedLoopId: loopId })),
  setCurrentPackage: (packageId) => setWorkflow((prev) => ({ ...prev, currentPackageId: packageId })),
}), [role, workflow]);
```

- [ ] **Step 5: 运行测试确认仍失败但进入组件缺失阶段**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/home/HomeWorkbench.test.tsx
```

Expected:

```text
FAIL
Cannot find module './HomeWorkbench'
```

- [ ] **Step 6: 提交 session 同步基础**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/app/session/types.ts prototype/src/app/session/AppSessionContext.tsx
git commit -m "feat: expose current loop through app session"
```

## Task 2: Extract Home Workbench Components

**Files:**
- Create: `prototype/src/pages/home/HomeWorkbench.tsx`
- Create: `prototype/src/pages/home/HomeMissionStrip.tsx`
- Create: `prototype/src/pages/home/HomePriorityQueue.tsx`
- Create: `prototype/src/pages/home/HomeEvidenceWorkspace.tsx`
- Create: `prototype/src/pages/home/HomeActionDrawer.tsx`
- Create: `prototype/src/pages/home/homeWorkbench.ts`
- Modify: `prototype/src/pages/overviewPerformancePages.tsx`
- Test: `prototype/src/pages/home/HomeWorkbench.test.tsx`

- [ ] **Step 1: 写最小 view-model**

Create `prototype/src/pages/home/homeWorkbench.ts`:

```ts
import { evidencePackageView, evidenceWindows, loops, primaryLoopId } from '../../data/mockData';

export function getPriorityLoops() {
  return loops.filter((loop) => ['可诊断', '需现场核实', '可整定', '数据不足'].includes(loop.status)).slice(0, 6);
}

export function getSelectedLoop(loopId: string) {
  return loops.find((loop) => loop.id === loopId) ?? loops.find((loop) => loop.id === primaryLoopId) ?? loops[0];
}

export function getEvidenceWindow(loopId: string) {
  return evidenceWindows.find((item) => item.loopId === loopId) ?? evidenceWindows[0];
}

export function getWorkbenchSummary(loopId: string) {
  const selected = getSelectedLoop(loopId);
  return {
    selected,
    evidence: getEvidenceWindow(selected.id),
    packageStatus: evidencePackageView.packageStatus,
  };
}
```

- [ ] **Step 2: 实现 4 个子组件**

Create `prototype/src/pages/home/HomeMissionStrip.tsx`:

```tsx
import { StatusMetric } from '../../components/ui';

export function HomeMissionStrip({
  loopCount,
  loopId,
  risk,
  packageStatus,
  nextStep,
}: {
  loopCount: number;
  loopId: string;
  risk: 'high' | 'medium' | 'low';
  packageStatus: string;
  nextStep: string;
}) {
  return (
    <section className="mission-strip" aria-label="当前治理任务">
      <StatusMetric label="当前样本" value={`${loopCount} 回路`} tone="neutral" />
      <StatusMetric label="优先对象" value={loopId} tone={risk === 'high' ? 'danger' : risk === 'medium' ? 'warning' : 'ok'} />
      <StatusMetric label="证据包" value={packageStatus} tone="warning" />
      <StatusMetric label="下一步" value={nextStep} tone="warning" />
    </section>
  );
}
```

Create `prototype/src/pages/home/HomePriorityQueue.tsx`:

```tsx
import { Link } from 'react-router-dom';
import { LoopCardList } from '../../components/ui';
import type { LoopRecord } from '../../types';

export function HomePriorityQueue({
  loops,
  selectedId,
  onSelect,
}: {
  loops: LoopRecord[];
  selectedId: string;
  onSelect: (loopId: string) => void;
}) {
  return (
    <section className="panel task-queue" aria-label="低性能优先级清单">
      <div className="section-heading">
        <div>
          <h2>今日优先处理队列</h2>
          <p>选中回路后，证据与动作在同屏更新，不打断工程师判断。</p>
        </div>
        <Link className="text-link" to="/performance/ranking">完整排行</Link>
      </div>
      <LoopCardList loops={loops} selectedId={selectedId} onSelect={(loop) => onSelect(loop.id)} />
    </section>
  );
}
```

Create `prototype/src/pages/home/HomeEvidenceWorkspace.tsx`:

```tsx
import { Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { TrendChart } from '../../components/TrendChart';
import { StatusBadge } from '../../components/ui';
import type { EvidenceWindow, LoopRecord } from '../../types';

export function HomeEvidenceWorkspace({ selected, evidence }: { selected: LoopRecord; evidence: EvidenceWindow | undefined }) {
  return (
    <section className="panel evidence-workspace" aria-label="选中回路证据工作区">
      <div className="object-header">
        <div>
          <span className="eyebrow">当前回路</span>
          <h2>{selected.id} 证据摘要</h2>
        </div>
        <div className="object-badges">
          <StatusBadge value={selected.status} />
          <span className={`risk-badge risk-${selected.risk}`}>评分 {selected.score}</span>
        </div>
      </div>
      {evidence ? <TrendChart evidence={evidence} /> : <p>当前回路暂无趋势证据，不会伪装为完整证据链。</p>}
      <div className="evidence-rules">
        {(evidence?.rules ?? ['当前回路暂无规则命中']).map((rule) => <span key={rule}>✓ {rule}</span>)}
      </div>
      <Link className="button" to={`/diagnosis/loop/${selected.id}`}>进入证据链 <ArrowRight size={16} /></Link>
    </section>
  );
}
```

Create `prototype/src/pages/home/HomeActionDrawer.tsx`:

```tsx
import { Link } from 'react-router-dom';
import { ActionList } from '../pageShared';

export function HomeActionDrawer({ currentStatus }: { currentStatus: string }) {
  return (
    <aside className="action-drawer" aria-label="动作与状态影响">
      <h2>动作与待办</h2>
      <ActionList />
      <div className="state-machine-mini">
        <span className="active">诊断</span>
        <span>审核</span>
        <span>实施</span>
        <span>复评</span>
        <span>证据包</span>
      </div>
      <div className="impact-note">
        <strong>{currentStatus === '数据不足' ? '当前回路存在数据缺口' : '选择“需补证据”会保持 partial'}</strong>
        <p>实施、复评和 Sponsor 汇报不会被伪装为完成闭环。</p>
      </div>
      <Link className="button secondary" to="/closure/review">进入闭环治理</Link>
      <Link className="button ghost" to="/samples/readiness">进入样本验证</Link>
    </aside>
  );
}
```

- [ ] **Step 3: 实现组合容器**

Create `prototype/src/pages/home/HomeWorkbench.tsx`:

```tsx
import { currentBatch } from '../../data/mockData';
import { useAppSession } from '../../app/session/AppSessionContext';
import { HomeActionDrawer } from './HomeActionDrawer';
import { HomeEvidenceWorkspace } from './HomeEvidenceWorkspace';
import { HomeMissionStrip } from './HomeMissionStrip';
import { HomePriorityQueue } from './HomePriorityQueue';
import { getPriorityLoops, getWorkbenchSummary } from './homeWorkbench';

export function HomeWorkbench() {
  const { currentLoopId, selectLoop } = useAppSession();
  const priorityLoops = getPriorityLoops();
  const { selected, evidence, packageStatus } = getWorkbenchSummary(currentLoopId);

  return (
    <>
      <HomeMissionStrip
        loopCount={currentBatch.loopCount}
        loopId={selected.id}
        risk={selected.risk}
        packageStatus={packageStatus}
        nextStep={selected.status === '需现场核实' ? '现场核实' : '提交审核'}
      />
      <section className="workspace-layout home-workbench-layout">
        <HomePriorityQueue loops={priorityLoops} selectedId={selected.id} onSelect={selectLoop} />
        <HomeEvidenceWorkspace selected={selected} evidence={evidence} />
        <HomeActionDrawer currentStatus={selected.status} />
      </section>
    </>
  );
}
```

- [ ] **Step 4: 用新容器替换旧首页实现**

Modify `prototype/src/pages/overviewPerformancePages.tsx`:

```tsx
import { HomeWorkbench } from './home/HomeWorkbench';

export function HomePage({ route }: { route: NavigationItem }) {
  return (
    <>
      <PageHeader route={route} />
      <HomeWorkbench />
    </>
  );
}
```

- [ ] **Step 5: 运行测试确认通过**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/home/HomeWorkbench.test.tsx
```

Expected:

```text
PASS  HomeWorkbench session sync
```

- [ ] **Step 6: 提交首页结构拆分**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/pages/home prototype/src/pages/overviewPerformancePages.tsx prototype/src/pages/home/HomeWorkbench.test.tsx
git commit -m "feat: extract home workbench into focused components"
```

## Task 3: Split Home Styles And Keep Build Green

**Files:**
- Create: `prototype/src/styles/home-workbench.css`
- Modify: `prototype/src/styles/app.css`
- Modify: `prototype/src/styles/tokens.css`
- Test: `prototype/src/pages/home/HomeWorkbench.test.tsx`

- [ ] **Step 1: 抽离首页专属样式**

Create `prototype/src/styles/home-workbench.css`:

```css
.home-workbench-layout {
  display: grid;
  grid-template-columns: minmax(280px, .85fr) minmax(480px, 1.4fr) minmax(280px, .75fr);
  gap: var(--space-4);
  align-items: start;
}

.home-workbench-layout .action-drawer {
  position: sticky;
  top: var(--space-4);
}

@media (max-width: 1279px) {
  .home-workbench-layout {
    grid-template-columns: 1fr;
  }

  .home-workbench-layout .action-drawer {
    position: static;
  }
}
```

- [ ] **Step 2: 在全局样式中减少首页耦合**

Modify `prototype/src/styles/app.css`:

```css
.workspace-layout {
  display: grid;
  gap: var(--space-4);
  align-items: start;
}
```

保留公共布局，删除首页特有的 `grid-template-columns` 与 `action-drawer sticky` 定义。

- [ ] **Step 3: 确保样式文件被引入**

如果当前样式入口集中在 `main.tsx` 或统一样式入口，则补充：

```ts
import './styles/home-workbench.css';
```

- [ ] **Step 4: 运行测试与构建**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/home/HomeWorkbench.test.tsx src/components/AppShell.test.tsx src/App.test.tsx
npm run build
```

Expected:

```text
PASS
vite v...
✓ built in ...
```

- [ ] **Step 5: 提交样式分域**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/styles/home-workbench.css prototype/src/styles/app.css prototype/src/styles/tokens.css prototype/src/main.tsx
git commit -m "refactor: split home workbench styles from global app css"
```

## Task 4: Add First Smoke For Home Workbench Selection

**Files:**
- Modify: `prototype/tests/smoke.spec.ts`

- [ ] **Step 1: 增加首页选择联动 smoke**

Append to `prototype/tests/smoke.spec.ts`:

```ts
test('updates home workbench context when selecting another priority loop', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /LIC-2204/i }).click();
  await expect(page.getByText(/当前回路：LIC-2204/i)).toBeVisible();
  await expect(page.getByRole('link', { name: /进入证据链/i })).toHaveAttribute('href', '/diagnosis/loop/LIC-2204');
});
```

- [ ] **Step 2: 运行 smoke 子集**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npx playwright test tests/smoke.spec.ts -g "updates home workbench context when selecting another priority loop"
```

Expected:

```text
1 passed
```

- [ ] **Step 3: 提交首页 smoke**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/tests/smoke.spec.ts
git commit -m "test: cover home workbench selection flow"
git push
```

## Spec Coverage Check

- 当前回路统一到 session：Task 1
- 首页拆成 4 个职责单元：Task 2
- 样式分域与减少全局耦合：Task 3
- 首页首批交互 smoke：Task 4

本计划未覆盖：

- 首页筛选、批量动作、真实工作流推进
- EvidenceWorkspace 的更深对比与规则解释
- 首页与排行/审核页的完整闭环同步

这些应进入下一轮页面增强计划。

## Placeholder Scan

- 无 `TBD`
- 无 `TODO`
- 无“后续补实现”型步骤

## Type Consistency Check

- 首页统一使用 `currentLoopId` / `selectLoop`
- 工作台数据统一从 `homeWorkbench.ts` 派生
- 页面壳层仍通过 `HomePage` 入口挂接，不破坏现有路由
