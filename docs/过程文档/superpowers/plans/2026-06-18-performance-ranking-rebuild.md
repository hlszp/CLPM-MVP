# Performance Ranking Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将“指标总览 / 低效排行”从静态 KPI + 表格页重构为可筛选、可排序、可批量选择、与当前回路上下文联动的高保真绩效分析工作台。

**Architecture:** 保留现有 demo 数据与 `loops/kpis` 数据源，但新增 `performanceRanking` 视图模型层，统一封装 KPI 概览、筛选条件、分组结果、当前选中对象与批量操作状态。页面拆为“指标总览板”“筛选栏”“高密度排行表”“未参与排序对象”“右侧详情上下文”五个单元，并通过 `AppSessionContext.selectLoop()` 与首页、证据中心共享当前回路。

**Tech Stack:** React 19、TypeScript、React Router、Vitest、Testing Library、Playwright、Vite

---

## File Map

**Create:**
- `prototype/src/app/session/performanceRanking.ts`
- `prototype/src/pages/performance/PerformanceOverviewBoard.tsx`
- `prototype/src/pages/performance/PerformanceFilterBar.tsx`
- `prototype/src/pages/performance/PerformanceRankingTable.tsx`
- `prototype/src/pages/performance/PerformanceHeldOutTable.tsx`
- `prototype/src/pages/performance/PerformanceContextPanel.tsx`
- `prototype/src/pages/performance/PerformanceRankingWorkbench.tsx`
- `prototype/src/pages/performance/performanceRankingModel.ts`
- `prototype/src/pages/performance/PerformanceRankingWorkbench.test.tsx`
- `prototype/src/pages/performance/PerformanceOverviewBoard.test.tsx`
- `prototype/src/styles/performance-ranking.css`

**Modify:**
- `prototype/src/types/index.ts`
- `prototype/src/app/session/types.ts`
- `prototype/src/app/session/seed.ts`
- `prototype/src/app/session/AppSessionContext.tsx`
- `prototype/src/data/mockData.ts`
- `prototype/src/components/ui.tsx`
- `prototype/src/pages/overviewPerformancePages.tsx`
- `prototype/src/main.tsx`
- `prototype/tests/smoke.spec.ts`

## Task 1: Model Ranking Filters And Selection In Session

**Files:**
- Modify: `prototype/src/types/index.ts`
- Modify: `prototype/src/app/session/types.ts`
- Modify: `prototype/src/app/session/seed.ts`
- Modify: `prototype/src/app/session/AppSessionContext.tsx`
- Create: `prototype/src/app/session/performanceRanking.ts`
- Test: `prototype/src/pages/performance/PerformanceRankingWorkbench.test.tsx`

- [ ] **Step 1: 写失败测试，要求切换风险筛选后只显示高风险回路**

```tsx
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { PerformanceRankingWorkbench } from './PerformanceRankingWorkbench';
import { renderWithSession } from '../../test/renderWithSession';

describe('PerformanceRankingWorkbench filters', () => {
  it('filters ranking table to high risk loops only', async () => {
    const user = userEvent.setup();
    renderWithSession(<PerformanceRankingWorkbench />);

    await user.selectOptions(screen.getByLabelText('风险等级'), 'high');

    expect(screen.getByRole('row', { name: /TIC-1115/i })).toBeInTheDocument();
    expect(screen.queryByRole('row', { name: /FIC-1101/i })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/performance/PerformanceRankingWorkbench.test.tsx
```

Expected:

```text
FAIL
Cannot find module './PerformanceRankingWorkbench'
```

- [ ] **Step 3: 为绩效排行增加领域类型**

Modify `prototype/src/types/index.ts`:

```ts
export interface PerformanceRankingFilters {
  risk: 'all' | 'high' | 'medium' | 'low';
  status: 'all' | '可评估' | '可诊断' | '可整定' | '需现场核实';
  keyword: string;
  sortBy: 'score' | 'risk' | 'loop';
}
```

- [ ] **Step 4: 扩展 session 类型**

Modify `prototype/src/app/session/types.ts`:

```ts
import type { PerformanceRankingFilters } from '../../types';

export interface PerformanceRankingState {
  filters: PerformanceRankingFilters;
  selectedLoopIds: string[];
}

export interface AppSessionValue {
  // existing fields...
  performanceRanking: PerformanceRankingState;
  setPerformanceFilters: (filters: Partial<PerformanceRankingFilters>) => void;
  toggleRankedLoopSelection: (loopId: string) => void;
  clearRankedLoopSelection: () => void;
}
```

- [ ] **Step 5: 写 seed**

Modify `prototype/src/app/session/seed.ts`:

```ts
import type { PerformanceRankingState } from './types';

export const initialPerformanceRankingState: PerformanceRankingState = {
  filters: {
    risk: 'all',
    status: 'all',
    keyword: '',
    sortBy: 'score',
  },
  selectedLoopIds: [],
};
```

- [ ] **Step 6: 实现状态迁移**

Create `prototype/src/app/session/performanceRanking.ts`:

```ts
import type { PerformanceRankingFilters } from '../../types';
import type { PerformanceRankingState } from './types';

export function mergePerformanceFilters(
  current: PerformanceRankingState,
  filters: Partial<PerformanceRankingFilters>,
): PerformanceRankingState {
  return {
    ...current,
    filters: {
      ...current.filters,
      ...filters,
    },
  };
}

export function toggleLoopSelection(current: PerformanceRankingState, loopId: string): PerformanceRankingState {
  return current.selectedLoopIds.includes(loopId)
    ? { ...current, selectedLoopIds: current.selectedLoopIds.filter((id) => id !== loopId) }
    : { ...current, selectedLoopIds: [...current.selectedLoopIds, loopId] };
}

export function clearLoopSelection(current: PerformanceRankingState): PerformanceRankingState {
  return { ...current, selectedLoopIds: [] };
}
```

- [ ] **Step 7: 接入 Provider**

Modify `prototype/src/app/session/AppSessionContext.tsx`:

```tsx
const [performanceRanking, setPerformanceRanking] = useState<PerformanceRankingState>(initialPerformanceRankingState);

const value = useMemo<AppSessionValue>(() => ({
  // existing fields...
  performanceRanking,
  setPerformanceFilters: (filters) => setPerformanceRanking((prev) => mergePerformanceFilters(prev, filters)),
  toggleRankedLoopSelection: (loopId) => setPerformanceRanking((prev) => toggleLoopSelection(prev, loopId)),
  clearRankedLoopSelection: () => setPerformanceRanking((prev) => clearLoopSelection(prev)),
}), [role, workflow, sampleReadiness, performanceRanking, currentSample, currentPackage]);
```

- [ ] **Step 8: 提交 session 基础**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/types/index.ts prototype/src/app/session/types.ts prototype/src/app/session/seed.ts prototype/src/app/session/AppSessionContext.tsx prototype/src/app/session/performanceRanking.ts
git commit -m "feat: add performance ranking state to app session"
```

## Task 2: Build Ranking View-Model And Filter Bar

**Files:**
- Create: `prototype/src/pages/performance/performanceRankingModel.ts`
- Create: `prototype/src/pages/performance/PerformanceFilterBar.tsx`
- Modify: `prototype/src/data/mockData.ts`
- Test: `prototype/src/pages/performance/PerformanceOverviewBoard.test.tsx`

- [ ] **Step 1: 写失败测试，要求 view-model 能返回 held-out loops**

```tsx
import { describe, expect, it } from 'vitest';
import { getPerformanceRankingViewModel } from './performanceRankingModel';

describe('performanceRankingModel', () => {
  it('separates held out loops from ranked loops', () => {
    const model = getPerformanceRankingViewModel({
      risk: 'all',
      status: 'all',
      keyword: '',
      sortBy: 'score',
    });

    expect(model.heldOutLoops.length).toBeGreaterThan(0);
    expect(model.heldOutLoops.every((loop) => ['数据不足', '不可判定'].includes(loop.status))).toBe(true);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/performance/PerformanceOverviewBoard.test.tsx
```

Expected:

```text
FAIL
Cannot find module './performanceRankingModel'
```

- [ ] **Step 3: 为 mock 数据增加绩效摘要块**

Modify `prototype/src/data/mockData.ts`:

```ts
export const performanceSummaryCards = [
  { key: 'auto', label: '样本自控率', value: '94.5%', delta: '24 回路 / 1h 窗口 / 1s 采样' },
  { key: 'effective', label: '有效自控率', value: '62.5%', delta: '6 条诊断样例' },
  { key: 'smooth', label: '平稳率', value: '62.5%', delta: '9 条低效' },
  { key: 'closure', label: '闭环候选率', value: '50%', delta: '3 条需现场核实' },
] as const;
```

- [ ] **Step 4: 写 view-model**

Create `prototype/src/pages/performance/performanceRankingModel.ts`:

```ts
import { loops, performanceSummaryCards } from '../../data/mockData';
import type { LoopRecord, PerformanceRankingFilters } from '../../types';

const rankedStatuses = ['可评估', '可诊断', '可整定', '需现场核实'] as const;

export function getPerformanceRankingViewModel(filters: PerformanceRankingFilters) {
  const heldOutLoops = loops.filter((loop) => ['数据不足', '不可判定'].includes(loop.status));

  const rankedLoops = loops
    .filter((loop) => rankedStatuses.includes(loop.status as (typeof rankedStatuses)[number]))
    .filter((loop) => (filters.risk === 'all' ? true : loop.risk === filters.risk))
    .filter((loop) => (filters.status === 'all' ? true : loop.status === filters.status))
    .filter((loop) => {
      const keyword = filters.keyword.trim().toLowerCase();
      if (!keyword) return true;
      return [loop.id, loop.device, loop.type, loop.nextAction].some((value) => value.toLowerCase().includes(keyword));
    })
    .sort((left, right) => {
      if (filters.sortBy === 'loop') return left.id.localeCompare(right.id);
      if (filters.sortBy === 'risk') return riskRank(left.risk) - riskRank(right.risk) || left.score - right.score;
      return left.score - right.score;
    });

  return {
    summaryCards: performanceSummaryCards,
    rankedLoops,
    heldOutLoops,
  };
}

function riskRank(risk: LoopRecord['risk']) {
  return risk === 'high' ? 0 : risk === 'medium' ? 1 : 2;
}
```

- [ ] **Step 5: 写筛选栏**

Create `prototype/src/pages/performance/PerformanceFilterBar.tsx`:

```tsx
import type { PerformanceRankingFilters } from '../../types';

export function PerformanceFilterBar({
  filters,
  onChange,
  selectedCount,
  onClearSelection,
}: {
  filters: PerformanceRankingFilters;
  onChange: (filters: Partial<PerformanceRankingFilters>) => void;
  selectedCount: number;
  onClearSelection: () => void;
}) {
  return (
    <section className="panel performance-filter-bar" aria-label="排行筛选栏">
      <label>
        <span>风险等级</span>
        <select aria-label="风险等级" value={filters.risk} onChange={(event) => onChange({ risk: event.target.value as PerformanceRankingFilters['risk'] })}>
          <option value="all">全部</option>
          <option value="high">高风险</option>
          <option value="medium">中风险</option>
          <option value="low">低风险</option>
        </select>
      </label>
      <label>
        <span>对象状态</span>
        <select aria-label="对象状态" value={filters.status} onChange={(event) => onChange({ status: event.target.value as PerformanceRankingFilters['status'] })}>
          <option value="all">全部</option>
          <option value="可评估">可评估</option>
          <option value="可诊断">可诊断</option>
          <option value="可整定">可整定</option>
          <option value="需现场核实">需现场核实</option>
        </select>
      </label>
      <label>
        <span>关键词</span>
        <input aria-label="关键词" value={filters.keyword} onChange={(event) => onChange({ keyword: event.target.value })} />
      </label>
      <label>
        <span>排序方式</span>
        <select aria-label="排序方式" value={filters.sortBy} onChange={(event) => onChange({ sortBy: event.target.value as PerformanceRankingFilters['sortBy'] })}>
          <option value="score">按评分</option>
          <option value="risk">按风险</option>
          <option value="loop">按回路</option>
        </select>
      </label>
      <div className="performance-batch-actions">
        <span>已选 {selectedCount} 条</span>
        <button type="button" className="button ghost" onClick={onClearSelection}>清空选择</button>
      </div>
    </section>
  );
}
```

- [ ] **Step 6: 运行测试确认通过**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/performance/PerformanceOverviewBoard.test.tsx
```

Expected:

```text
PASS
```

- [ ] **Step 7: 提交 view-model 与筛选栏**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/data/mockData.ts prototype/src/pages/performance/performanceRankingModel.ts prototype/src/pages/performance/PerformanceFilterBar.tsx prototype/src/pages/performance/PerformanceOverviewBoard.test.tsx
git commit -m "feat: add performance ranking view model and filter bar"
```

## Task 3: Build Overview Board And Ranking Workbench

**Files:**
- Create: `prototype/src/pages/performance/PerformanceOverviewBoard.tsx`
- Create: `prototype/src/pages/performance/PerformanceRankingTable.tsx`
- Create: `prototype/src/pages/performance/PerformanceHeldOutTable.tsx`
- Create: `prototype/src/pages/performance/PerformanceContextPanel.tsx`
- Create: `prototype/src/pages/performance/PerformanceRankingWorkbench.tsx`
- Modify: `prototype/src/components/ui.tsx`
- Modify: `prototype/src/pages/overviewPerformancePages.tsx`
- Test: `prototype/src/pages/performance/PerformanceRankingWorkbench.test.tsx`

- [ ] **Step 1: 扩展 `LoopTable` 支持勾选列和批量选择**

Modify `prototype/src/components/ui.tsx`:

```tsx
export function LoopTable({
  loops,
  onSelect,
  selectedId,
  selectedIds = [],
  onToggleSelection,
  showSelection = false,
}: {
  loops: LoopRecord[];
  onSelect?: (loop: LoopRecord) => void;
  selectedId?: string;
  selectedIds?: string[];
  onToggleSelection?: (loopId: string) => void;
  showSelection?: boolean;
}) {
  return (
    <div className="table-wrap" role="region" aria-label="回路清单表格">
      <table>
        <thead>
          <tr>
            {showSelection ? <th>选择</th> : null}
            <th>回路</th><th>装置</th><th>类型</th><th>状态</th><th>风险</th><th>评分</th><th>下一步</th>
          </tr>
        </thead>
        <tbody>
          {loops.map((loop) => (
            <tr key={loop.id} className={selectedId === loop.id ? 'selected-row' : undefined}>
              {showSelection ? (
                <td>
                  <input
                    type="checkbox"
                    aria-label={`选择 ${loop.id}`}
                    checked={selectedIds.includes(loop.id)}
                    onChange={() => onToggleSelection?.(loop.id)}
                  />
                </td>
              ) : null}
              <th scope="row">{loop.id}</th>
              <td>{loop.device}</td>
              <td>{loop.type}</td>
              <td><StatusBadge value={loop.status} /></td>
              <td><RiskBadge value={loop.risk} /></td>
              <td>{loop.score}</td>
              <td>{loop.nextAction}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: 实现 5 个页面单元**

Create `prototype/src/pages/performance/PerformanceOverviewBoard.tsx`:

```tsx
import { MetricCard } from '../../components/ui';

export function PerformanceOverviewBoard({
  cards,
}: {
  cards: Array<{ key: string; label: string; value: string; delta: string }>;
}) {
  return (
    <section className="grid four">
      {cards.map((card) => (
        <MetricCard key={card.key} label={card.label} value={card.value} delta={card.delta} />
      ))}
    </section>
  );
}
```

Create `prototype/src/pages/performance/PerformanceRankingTable.tsx`:

```tsx
import { LoopTable } from '../../components/ui';
import type { LoopRecord } from '../../types';

export function PerformanceRankingTable({
  loops,
  selectedLoopId,
  selectedIds,
  onSelect,
  onToggleSelection,
}: {
  loops: LoopRecord[];
  selectedLoopId: string;
  selectedIds: string[];
  onSelect: (loop: LoopRecord) => void;
  onToggleSelection: (loopId: string) => void;
}) {
  return (
    <section className="panel">
      <h2>低效排行</h2>
      <p>仅对可评估、可诊断、可整定、需现场核实对象排序；数据不足与不可判定不会被当作真实 0 分。</p>
      <LoopTable
        loops={loops}
        onSelect={onSelect}
        selectedId={selectedLoopId}
        selectedIds={selectedIds}
        onToggleSelection={onToggleSelection}
        showSelection
      />
    </section>
  );
}
```

Create `prototype/src/pages/performance/PerformanceHeldOutTable.tsx`:

```tsx
import type { LoopRecord } from '../../types';

export function PerformanceHeldOutTable({ loops }: { loops: LoopRecord[] }) {
  return (
    <section className="panel warning-panel">
      <h2>未参与真实排序对象</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>回路</th><th>状态</th><th>原因</th></tr>
          </thead>
          <tbody>
            {loops.map((loop) => (
              <tr key={loop.id}>
                <th scope="row">{loop.id}</th>
                <td>{loop.status}</td>
                <td>{loop.nextAction}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
```

Create `prototype/src/pages/performance/PerformanceContextPanel.tsx`:

```tsx
import { Link } from 'react-router-dom';
import type { LoopRecord } from '../../types';

export function PerformanceContextPanel({
  selectedLoop,
  selectedCount,
}: {
  selectedLoop: LoopRecord | undefined;
  selectedCount: number;
}) {
  if (!selectedLoop) {
    return (
      <aside className="panel">
        <h2>当前上下文</h2>
        <p>当前没有命中筛选结果，请调整筛选条件。</p>
      </aside>
    );
  }

  return (
    <aside className="panel performance-context-panel">
      <h2>当前回路上下文</h2>
      <p><strong>{selectedLoop.id}</strong> · {selectedLoop.device} · {selectedLoop.type}</p>
      <p>下一步：{selectedLoop.nextAction}</p>
      <p>批量已选：{selectedCount} 条</p>
      <Link className="button" to={`/diagnosis/loop/${selectedLoop.id}`}>进入证据链</Link>
      <Link className="button ghost" to="/closure/review">进入建议审核</Link>
    </aside>
  );
}
```

Create `prototype/src/pages/performance/PerformanceRankingWorkbench.tsx`:

```tsx
import { useNavigate } from 'react-router-dom';
import { useAppSession } from '../../app/session/AppSessionContext';
import { PerformanceContextPanel } from './PerformanceContextPanel';
import { PerformanceFilterBar } from './PerformanceFilterBar';
import { PerformanceHeldOutTable } from './PerformanceHeldOutTable';
import { PerformanceOverviewBoard } from './PerformanceOverviewBoard';
import { PerformanceRankingTable } from './PerformanceRankingTable';
import { getPerformanceRankingViewModel } from './performanceRankingModel';

export function PerformanceRankingWorkbench() {
  const navigate = useNavigate();
  const {
    currentLoopId,
    selectLoop,
    performanceRanking,
    setPerformanceFilters,
    toggleRankedLoopSelection,
    clearRankedLoopSelection,
  } = useAppSession();

  const { summaryCards, rankedLoops, heldOutLoops } = getPerformanceRankingViewModel(performanceRanking.filters);
  const selectedLoop = rankedLoops.find((loop) => loop.id === currentLoopId) ?? rankedLoops[0];

  return (
    <section className="performance-ranking-workbench">
      <PerformanceOverviewBoard cards={summaryCards} />
      <PerformanceFilterBar
        filters={performanceRanking.filters}
        onChange={setPerformanceFilters}
        selectedCount={performanceRanking.selectedLoopIds.length}
        onClearSelection={clearRankedLoopSelection}
      />
      <section className="performance-ranking-layout">
        <div className="performance-ranking-main">
          <PerformanceRankingTable
            loops={rankedLoops}
            selectedLoopId={selectedLoop?.id ?? ''}
            selectedIds={performanceRanking.selectedLoopIds}
            onSelect={(loop) => {
              selectLoop(loop.id);
              navigate(`/diagnosis/loop/${loop.id}`);
            }}
            onToggleSelection={toggleRankedLoopSelection}
          />
          <PerformanceHeldOutTable loops={heldOutLoops} />
        </div>
        <PerformanceContextPanel
          selectedLoop={selectedLoop}
          selectedCount={performanceRanking.selectedLoopIds.length}
        />
      </section>
    </section>
  );
}
```

- [ ] **Step 3: 挂接到页面入口**

Modify `prototype/src/pages/overviewPerformancePages.tsx`:

```tsx
import { PerformanceOverviewBoard } from './performance/PerformanceOverviewBoard';
import { PerformanceRankingWorkbench } from './performance/PerformanceRankingWorkbench';
import { performanceSummaryCards } from '../data/mockData';

export function PerformancePage({ route }: { route: NavigationItem }) {
  return (
    <>
      <PageHeader route={route} state="success" />
      <PerformanceOverviewBoard cards={performanceSummaryCards} />
      <section className="panel">
        <h2>相关低性能回路</h2>
        <p>继续进入排行工作台查看筛选、批量选择与当前回路上下文。</p>
        <Link className="button" to="/performance/ranking">查看排行</Link>
        <Link className="button ghost" to="/performance/lineage">查看溯源</Link>
      </section>
    </>
  );
}

export function RankingPage({ route }: { route: NavigationItem }) {
  return (
    <>
      <PageHeader route={route} />
      <PerformanceRankingWorkbench />
    </>
  );
}
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/performance/PerformanceRankingWorkbench.test.tsx src/pages/performance/PerformanceOverviewBoard.test.tsx
```

Expected:

```text
PASS
```

- [ ] **Step 5: 提交排行工作台主体**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/components/ui.tsx prototype/src/pages/performance prototype/src/pages/overviewPerformancePages.tsx
git commit -m "feat: rebuild performance ranking as a stateful workbench"
```

## Task 4: Split Styles And Add Smoke Coverage

**Files:**
- Create: `prototype/src/styles/performance-ranking.css`
- Modify: `prototype/src/main.tsx`
- Modify: `prototype/tests/smoke.spec.ts`

- [ ] **Step 1: 样式分域**

Create `prototype/src/styles/performance-ranking.css`:

```css
.performance-ranking-workbench {
  display: grid;
  gap: var(--space-4);
}

.performance-filter-bar {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--space-3);
  align-items: end;
}

.performance-filter-bar label {
  display: grid;
  gap: var(--space-2);
}

.performance-ranking-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(280px, .8fr);
  gap: var(--space-4);
  align-items: start;
}

.performance-ranking-main {
  display: grid;
  gap: var(--space-4);
}

.performance-context-panel {
  position: sticky;
  top: var(--space-4);
}

@media (max-width: 1279px) {
  .performance-filter-bar,
  .performance-ranking-layout {
    grid-template-columns: 1fr;
  }

  .performance-context-panel {
    position: static;
  }
}
```

- [ ] **Step 2: 引入样式**

Modify `prototype/src/main.tsx`:

```ts
import './styles/performance-ranking.css';
```

- [ ] **Step 3: 新增 smoke**

Append to `prototype/tests/smoke.spec.ts`:

```ts
test('filters ranking table and keeps selected loop context in sync', async ({ page }) => {
  await page.goto('/performance/ranking');
  await page.getByLabel('风险等级').selectOption('high');
  await expect(page.getByRole('row', { name: /TIC-1115/i })).toBeVisible();
  await expect(page.getByRole('row', { name: /FIC-1101/i })).toHaveCount(0);

  await page.getByRole('checkbox', { name: /选择 TIC-1115/i }).check();
  await expect(page.getByText(/批量已选：1 条/i)).toBeVisible();
});
```

- [ ] **Step 4: 运行测试、构建、smoke**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/performance/PerformanceRankingWorkbench.test.tsx src/pages/performance/PerformanceOverviewBoard.test.tsx
npm run build
npx playwright test tests/smoke.spec.ts -g "filters ranking table and keeps selected loop context in sync"
```

Expected:

```text
PASS
vite v...
1 passed
```

- [ ] **Step 5: 提交并推送**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/styles/performance-ranking.css prototype/src/main.tsx prototype/tests/smoke.spec.ts
git commit -m "test: cover performance ranking workflow"
git push
```

## Spec Coverage Check

- 指标总览：Task 2、Task 3
- 筛选/排序/关键词过滤：Task 1、Task 2、Task 3
- 批量选择：Task 1、Task 3
- 当前回路上下文联动：Task 3
- 样式分域与 smoke：Task 4

未覆盖项：

- 图形化分布图
- 分页
- 真正跨页持久化的批量动作执行

这些进入下一轮排行页增强计划。

## Placeholder Scan

- 无 `TBD`
- 无 `TODO`
- 无“稍后实现”类步骤

## Type Consistency Check

- 筛选统一使用 `PerformanceRankingFilters`
- session 状态统一使用 `performanceRanking`
- 排行 view-model 统一使用 `getPerformanceRankingViewModel`
