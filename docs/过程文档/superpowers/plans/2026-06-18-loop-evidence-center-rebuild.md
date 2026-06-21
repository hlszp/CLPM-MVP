# Loop Evidence Center Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将“单回路证据中心”从固定读 `primaryLoopId` 的静态串页，重构为跟随当前回路上下文、支持证据摘要/规则命中/事件线/诊断契约联动的高保真证据工作台。

**Architecture:** 先抽出 `loopEvidence` 领域模型，把 `finding`、`evidenceWindow`、`closureState`、`evidencePackageView` 等聚合逻辑从页面中剥离出来；再把 `closureEvidencePages.tsx` 拆成独立页面组件，并统一改为从 session `currentLoopId` 或路由 `loopId` 驱动。最后补组件级单测和最小 smoke，确保首页、排行页和证据中心共用同一回路上下文。

**Tech Stack:** React 19、TypeScript、React Router、Vitest、Testing Library、Playwright、Vite

---

## File Map

**Create:**
- `prototype/src/data/loopEvidence.ts`
- `prototype/src/pages/evidence/DiagnosisListPage.tsx`
- `prototype/src/pages/evidence/LoopEvidenceWorkbench.tsx`
- `prototype/src/pages/evidence/LoopEvidenceSummary.tsx`
- `prototype/src/pages/evidence/LoopEvidenceRules.tsx`
- `prototype/src/pages/evidence/LoopEvidenceActions.tsx`
- `prototype/src/pages/evidence/EvidencePackagePage.tsx`
- `prototype/src/pages/evidence/loopEvidenceModel.ts`
- `prototype/src/pages/evidence/LoopEvidenceWorkbench.test.tsx`
- `prototype/src/pages/evidence/EvidencePackagePage.test.tsx`
- `prototype/src/styles/loop-evidence.css`

**Modify:**
- `prototype/src/types/index.ts`
- `prototype/src/data/mockData.ts`
- `prototype/src/pages/closureEvidencePages.tsx`
- `prototype/src/pages/home/HomeEvidenceWorkspace.tsx`
- `prototype/src/pages/performance/PerformanceContextPanel.tsx`
- `prototype/src/main.tsx`
- `prototype/tests/smoke.spec.ts`

## Task 1: Extract Loop Evidence Domain And Follow Session Context

**Files:**
- Create: `prototype/src/data/loopEvidence.ts`
- Modify: `prototype/src/data/mockData.ts`
- Create: `prototype/src/pages/evidence/LoopEvidenceWorkbench.test.tsx`

- [ ] **Step 1: 写失败测试，要求证据工作台默认跟随 session 当前回路**

```tsx
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LoopEvidenceWorkbench } from './LoopEvidenceWorkbench';
import { renderWithSession } from '../../test/renderWithSession';

describe('LoopEvidenceWorkbench context', () => {
  it('uses currentLoopId from session when route loopId is missing', () => {
    renderWithSession(<LoopEvidenceWorkbench />);

    expect(screen.getByRole('heading', { name: /TIC-1115 回路证据/i })).toBeInTheDocument();
    expect(screen.getByText(/诊断契约/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/evidence/LoopEvidenceWorkbench.test.tsx
```

Expected:

```text
FAIL
Cannot find module './LoopEvidenceWorkbench'
```

- [ ] **Step 3: 抽领域模型类型**

Modify `prototype/src/types/index.ts`:

```ts
export interface LoopEvidenceBundle {
  loopId: string;
  evidence?: EvidenceWindow;
  finding?: FindingRecord;
  reviewTrail: ReviewRecord[];
  closureState: 'success' | 'partial';
  missingRefs: string[];
}
```

- [ ] **Step 4: 抽数据聚合函数**

Create `prototype/src/data/loopEvidence.ts`:

```ts
import { evidenceWindows, findings, reviews } from './mockData';
import type { LoopEvidenceBundle } from '../types';

export function getLoopEvidenceBundle(loopId: string): LoopEvidenceBundle {
  const evidence = evidenceWindows.find((item) => item.loopId === loopId);
  const finding = findings.find((item) => item.loopId === loopId);
  const reviewTrail = reviews.filter((item) => item.loopId === loopId);
  const missingRefs = reviewTrail.some((item) => item.decision === '需补证据')
    ? ['InstrumentCheckRecord', 'PostImplementationObservation']
    : [];

  return {
    loopId,
    evidence,
    finding,
    reviewTrail,
    closureState: missingRefs.length > 0 ? 'partial' : 'success',
    missingRefs,
  };
}
```

- [ ] **Step 5: 用聚合函数替换 `mockData.ts` 中的硬编码闭环派生入口**

Modify `prototype/src/data/mockData.ts`:

```ts
import { getLoopEvidenceBundle } from './loopEvidence';

export const primaryLoopEvidenceBundle = getLoopEvidenceBundle(primaryLoopId);

export const closureState = {
  state: primaryLoopEvidenceBundle.closureState,
  blocker: primaryLoopEvidenceBundle.reviewTrail.find((review) => review.decision === '需补证据'),
  missingRefs: primaryLoopEvidenceBundle.missingRefs,
};
```

- [ ] **Step 6: 提交领域模型基础**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/types/index.ts prototype/src/data/loopEvidence.ts prototype/src/data/mockData.ts prototype/src/pages/evidence/LoopEvidenceWorkbench.test.tsx
git commit -m "feat: extract loop evidence domain model"
```

## Task 2: Build Loop Evidence Workbench

**Files:**
- Create: `prototype/src/pages/evidence/LoopEvidenceSummary.tsx`
- Create: `prototype/src/pages/evidence/LoopEvidenceRules.tsx`
- Create: `prototype/src/pages/evidence/LoopEvidenceActions.tsx`
- Create: `prototype/src/pages/evidence/LoopEvidenceWorkbench.tsx`
- Modify: `prototype/src/pages/home/HomeEvidenceWorkspace.tsx`
- Modify: `prototype/src/pages/performance/PerformanceContextPanel.tsx`
- Test: `prototype/src/pages/evidence/LoopEvidenceWorkbench.test.tsx`

- [ ] **Step 1: 实现摘要区**

Create `prototype/src/pages/evidence/LoopEvidenceSummary.tsx`:

```tsx
import { TrendChart } from '../../components/TrendChart';
import type { LoopEvidenceBundle } from '../../types';

export function LoopEvidenceSummary({ bundle }: { bundle: LoopEvidenceBundle }) {
  if (!bundle.evidence) {
    return (
      <section className="panel warning-panel">
        <h2>{bundle.loopId} 回路证据</h2>
        <p>当前 demo-data 未包含该回路的趋势窗口，不会伪装成真实证据链。</p>
      </section>
    );
  }

  return (
    <section className="panel">
      <h2>{bundle.loopId} 回路证据</h2>
      <TrendChart evidence={bundle.evidence} />
    </section>
  );
}
```

- [ ] **Step 2: 实现规则/事件/契约区**

Create `prototype/src/pages/evidence/LoopEvidenceRules.tsx`:

```tsx
import type { LoopEvidenceBundle } from '../../types';

export function LoopEvidenceRules({ bundle }: { bundle: LoopEvidenceBundle }) {
  return (
    <section className="panel">
      <h2>规则命中</h2>
      {(bundle.evidence?.rules ?? ['当前回路暂无规则命中']).map((rule) => (
        <p key={rule}>✓ {rule}</p>
      ))}
      <h2>事件线</h2>
      {(bundle.evidence?.events ?? ['当前回路暂无事件线']).map((event) => (
        <p key={event}>{event}</p>
      ))}
      {bundle.finding ? (
        <>
          <h2>诊断契约</h2>
          <p>{bundle.finding.findingType} · {bundle.finding.severity} · 负责人：{bundle.finding.ownerRole}</p>
          <p>证据引用：{bundle.finding.evidenceRefs.join(' / ')}</p>
        </>
      ) : null}
    </section>
  );
}
```

- [ ] **Step 3: 实现动作区**

Create `prototype/src/pages/evidence/LoopEvidenceActions.tsx`:

```tsx
import { Link } from 'react-router-dom';
import type { LoopEvidenceBundle } from '../../types';

export function LoopEvidenceActions({ bundle }: { bundle: LoopEvidenceBundle }) {
  return (
    <aside className="panel action-drawer">
      <h2>动作与状态传播</h2>
      <p>当前闭环状态：{bundle.closureState}</p>
      <p>缺失引用：{bundle.missingRefs.length > 0 ? bundle.missingRefs.join(' / ') : '无'}</p>
      <Link className="button" to="/closure/review">提交建议审核</Link>
      <Link className="button ghost" to="/evidence">查看证据包</Link>
    </aside>
  );
}
```

- [ ] **Step 4: 实现工作台容器**

Create `prototype/src/pages/evidence/LoopEvidenceWorkbench.tsx`:

```tsx
import { useParams } from 'react-router-dom';
import { useAppSession } from '../../app/session/AppSessionContext';
import { getLoopEvidenceBundle } from '../../data/loopEvidence';
import { LoopEvidenceActions } from './LoopEvidenceActions';
import { LoopEvidenceRules } from './LoopEvidenceRules';
import { LoopEvidenceSummary } from './LoopEvidenceSummary';

export function LoopEvidenceWorkbench() {
  const { loopId } = useParams();
  const { currentLoopId } = useAppSession();
  const bundle = getLoopEvidenceBundle(loopId ?? currentLoopId);

  return (
    <section className="loop-evidence-layout">
      <LoopEvidenceSummary bundle={bundle} />
      <LoopEvidenceRules bundle={bundle} />
      <LoopEvidenceActions bundle={bundle} />
    </section>
  );
}
```

- [ ] **Step 5: 首页和排行页统一跳证据中心，不在组件内拼证据文案**

Modify `prototype/src/pages/home/HomeEvidenceWorkspace.tsx` and `prototype/src/pages/performance/PerformanceContextPanel.tsx` to keep only entry links and current loop context, leaving detailed evidence rendering to `LoopEvidenceWorkbench`.

- [ ] **Step 6: 运行测试确认通过**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/evidence/LoopEvidenceWorkbench.test.tsx
```

Expected:

```text
PASS
```

- [ ] **Step 7: 提交证据工作台**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/pages/evidence prototype/src/pages/home/HomeEvidenceWorkspace.tsx prototype/src/pages/performance/PerformanceContextPanel.tsx
git commit -m "feat: rebuild loop evidence workbench"
```

## Task 3: Split Closure Pages And Evidence Package

**Files:**
- Create: `prototype/src/pages/evidence/DiagnosisListPage.tsx`
- Create: `prototype/src/pages/evidence/EvidencePackagePage.tsx`
- Create: `prototype/src/pages/evidence/EvidencePackagePage.test.tsx`
- Modify: `prototype/src/pages/closureEvidencePages.tsx`

- [ ] **Step 1: 拆出诊断清单页**

Create `prototype/src/pages/evidence/DiagnosisListPage.tsx`:

```tsx
import { Link } from 'react-router-dom';
import { findings } from '../../data/mockData';

export function DiagnosisListPage() {
  return (
    <section className="grid three">
      {findings.map((finding) => (
        <article className="panel" key={finding.id}>
          <h2>{finding.title}</h2>
          <p>{finding.loopId} · {finding.findingType} · {finding.severity} · 置信度 {Math.round(finding.confidence * 100)}%</p>
          <p>{finding.evidence}</p>
          <p>负责人：{finding.ownerRole}；证据引用：{finding.evidenceRefs.join(' / ')}</p>
          <Link className="button" to={`/diagnosis/loop/${finding.loopId}`}>查看证据</Link>
        </article>
      ))}
    </section>
  );
}
```

- [ ] **Step 2: 拆出证据包页**

Create `prototype/src/pages/evidence/EvidencePackagePage.tsx`:

```tsx
import { Link } from 'react-router-dom';
import { EvidencePackageHeader } from '../../components/ui';
import { dataLineage, evidencePackageView } from '../../data/mockData';

export function EvidencePackagePage() {
  return (
    <>
      <EvidencePackageHeader
        id={evidencePackageView.id}
        packageStatus={evidencePackageView.packageStatus}
        validityStatus={evidencePackageView.validityStatus}
        completeness={evidencePackageView.completeness}
        manifestHash={evidencePackageView.manifestHash}
        generatedAt={evidencePackageView.generatedAt}
        missingCount={evidencePackageView.missingRefs.length}
      />
      <section className="grid two">
        <section className="panel warning-panel">
          <h2>结论边界</h2>
          <p>{evidencePackageView.conclusion}</p>
          <p>缺失 refs：{evidencePackageView.missingRefs.join(' / ')}</p>
        </section>
        <section className="panel manifest">
          <h2>Included refs</h2>
          {evidencePackageView.includedRefs.map((ref) => (
            <code className={ref.status === '缺失' ? 'missing-ref' : ''} key={ref.name}>{ref.name} · {ref.status}</code>
          ))}
        </section>
      </section>
      <section className="panel">
        <h2>demo-data 溯源</h2>
        <p>{dataLineage.datasetId} · {dataLineage.sampleWindow} · {dataLineage.sampleIntervalSeconds}s</p>
        <Link className="button ghost" to="/delivery/acceptance">查看交付验收</Link>
      </section>
    </>
  );
}
```

- [ ] **Step 3: 页面入口改为薄封装**

Modify `prototype/src/pages/closureEvidencePages.tsx` so `DiagnosisPage`, `LoopEvidencePage`, `EvidencePage` only keep `PageHeader` + imported page body, reducing page-local data assembly.

- [ ] **Step 4: 写证据包页单测**

Create `prototype/src/pages/evidence/EvidencePackagePage.test.tsx`:

```tsx
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EvidencePackagePage } from './EvidencePackagePage';
import { renderWithSession } from '../../test/renderWithSession';

describe('EvidencePackagePage', () => {
  it('renders manifest hash and missing refs', () => {
    renderWithSession(<EvidencePackagePage />);

    expect(screen.getByText(/Included refs/i)).toBeInTheDocument();
    expect(screen.getByText(/缺失 refs/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: 运行测试确认通过**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/evidence/LoopEvidenceWorkbench.test.tsx src/pages/evidence/EvidencePackagePage.test.tsx
```

Expected:

```text
PASS
```

- [ ] **Step 6: 提交页面拆分**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/pages/evidence prototype/src/pages/closureEvidencePages.tsx
git commit -m "refactor: split closure evidence pages into focused modules"
```

## Task 4: Add Styles And Smoke Coverage

**Files:**
- Create: `prototype/src/styles/loop-evidence.css`
- Modify: `prototype/src/main.tsx`
- Modify: `prototype/tests/smoke.spec.ts`

- [ ] **Step 1: 样式分域**

Create `prototype/src/styles/loop-evidence.css`:

```css
.loop-evidence-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(300px, .9fr) minmax(260px, .7fr);
  gap: var(--space-4);
  align-items: start;
}

.loop-evidence-layout .action-drawer {
  position: sticky;
  top: var(--space-4);
}

@media (max-width: 1279px) {
  .loop-evidence-layout {
    grid-template-columns: 1fr;
  }

  .loop-evidence-layout .action-drawer {
    position: static;
  }
}
```

- [ ] **Step 2: 引入样式**

Modify `prototype/src/main.tsx`:

```ts
import './styles/loop-evidence.css';
```

- [ ] **Step 3: 新增 smoke**

Append to `prototype/tests/smoke.spec.ts`:

```ts
test('opens loop evidence from ranking context and shows diagnosis contract', async ({ page }) => {
  await page.goto('/performance/ranking');
  await page.getByRole('row', { name: /TIC-1115/i }).click();
  await expect(page.getByRole('heading', { name: /TIC-1115 回路证据/i })).toBeVisible();
  await expect(page.getByText(/诊断契约/i)).toBeVisible();
  await expect(page.getByRole('link', { name: /提交建议审核/i })).toBeVisible();
});
```

- [ ] **Step 4: 运行测试、构建、smoke**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/evidence/LoopEvidenceWorkbench.test.tsx src/pages/evidence/EvidencePackagePage.test.tsx
npm run build
npx playwright test tests/smoke.spec.ts -g "opens loop evidence from ranking context and shows diagnosis contract"
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
git add prototype/src/styles/loop-evidence.css prototype/src/main.tsx prototype/tests/smoke.spec.ts
git commit -m "test: cover loop evidence workflow"
git push
```

## Spec Coverage Check

- 单回路证据上下文跟随当前回路：Task 1、Task 2
- 证据摘要 / 规则 / 事件 / 契约：Task 2
- 证据包与结论边界：Task 3
- 页面拆分与样式分域：Task 3、Task 4
- smoke 与单测：Task 1、Task 3、Task 4

未覆盖项：

- 审核/实施/复评页的状态机重构
- 更丰富的图表交互和人工纠偏录入

这些进入下一轮闭环治理重构计划。

## Placeholder Scan

- 无 `TBD`
- 无 `TODO`
- 无“后续再补”类步骤

## Type Consistency Check

- 证据中心统一使用 `LoopEvidenceBundle`
- 聚合入口统一使用 `getLoopEvidenceBundle`
- 页面容器统一使用 `LoopEvidenceWorkbench`
