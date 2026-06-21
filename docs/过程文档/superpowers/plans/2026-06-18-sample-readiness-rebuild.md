# Sample Readiness Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将“数据导入 / 就绪校验 / 样本冻结”从静态说明页重构为步骤式、状态驱动、可验证阻断项的高保真样本准备工作台。

**Architecture:** 保留当前 demo 数据与样本口径，但新增 `sampleReadiness` 领域模型，把导入方式、字段映射、质量校验、阻断项和冻结状态统一到 session 管理。页面拆为“导入向导”和“就绪校验工作台”两组组件，通过单一 view-model 派生 UI，避免 `sampleLedgerPages.tsx` 继续累积页面内硬编码。

**Tech Stack:** React 19、TypeScript、React Router、Vitest、Testing Library、Playwright、Vite

---

## File Map

**Create:**
- `prototype/src/app/session/sampleReadiness.ts`
- `prototype/src/pages/samples/SampleImportWizard.tsx`
- `prototype/src/pages/samples/SampleReadinessWorkbench.tsx`
- `prototype/src/pages/samples/SampleImportMethodCard.tsx`
- `prototype/src/pages/samples/SampleFieldMappingEditor.tsx`
- `prototype/src/pages/samples/SampleValidationPanel.tsx`
- `prototype/src/pages/samples/SampleFreezePanel.tsx`
- `prototype/src/pages/samples/sampleReadinessModel.ts`
- `prototype/src/pages/samples/SampleImportWizard.test.tsx`
- `prototype/src/pages/samples/SampleReadinessWorkbench.test.tsx`
- `prototype/src/styles/sample-readiness.css`

**Modify:**
- `prototype/src/types/index.ts`
- `prototype/src/app/session/types.ts`
- `prototype/src/app/session/seed.ts`
- `prototype/src/app/session/AppSessionContext.tsx`
- `prototype/src/data/mockData.ts`
- `prototype/src/pages/sampleLedgerPages.tsx`
- `prototype/src/styles/app.css`
- `prototype/src/main.tsx`
- `prototype/tests/smoke.spec.ts`

## Task 1: Model Sample Readiness State In Session

**Files:**
- Modify: `prototype/src/types/index.ts`
- Modify: `prototype/src/app/session/types.ts`
- Modify: `prototype/src/app/session/seed.ts`
- Modify: `prototype/src/app/session/AppSessionContext.tsx`
- Create: `prototype/src/app/session/sampleReadiness.ts`
- Test: `prototype/src/pages/samples/SampleImportWizard.test.tsx`

- [ ] **Step 1: 写失败测试，要求切换导入方式会更新 session 中的样本准备状态**

```tsx
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { SampleImportWizard } from './SampleImportWizard';
import { renderWithSession } from '../../test/renderWithSession';

describe('SampleImportWizard state', () => {
  it('updates import method in session when choosing OPC read-only connection', async () => {
    const user = userEvent.setup();
    renderWithSession(<SampleImportWizard />);

    await user.click(screen.getByRole('button', { name: /OPC 只读连接/i }));

    expect(screen.getByText(/当前导入方式：OPC 只读连接/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/samples/SampleImportWizard.test.tsx
```

Expected:

```text
FAIL
Cannot find module './SampleImportWizard'
```

- [ ] **Step 3: 为样本准备流程增加领域类型**

Modify `prototype/src/types/index.ts`:

```ts
export type SampleImportMethod = 'historian' | 'csv' | 'opc';
export type SampleReadinessState = 'draft' | 'importing' | 'validating' | 'ready' | 'partial' | 'frozen';

export interface MappingFieldStatus {
  source: string;
  target: string;
  coverage: string;
  status: '已映射' | '缺失需确认' | '部分可用';
  note: string;
}
```

- [ ] **Step 4: 扩展 session 类型，增加样本准备状态对象**

Modify `prototype/src/app/session/types.ts`:

```ts
import type { EvidencePackage, SampleBatch, SampleImportMethod, SampleReadinessState, UserRole } from '../../types';

export interface SampleReadinessWorkflow {
  importMethod: SampleImportMethod;
  readinessState: SampleReadinessState;
  selectedMappingField: string;
  isFrozen: boolean;
}

export interface AppSessionValue {
  // existing fields...
  sampleReadiness: SampleReadinessWorkflow;
  setImportMethod: (method: SampleImportMethod) => void;
  setReadinessState: (state: SampleReadinessState) => void;
  freezeSample: () => void;
}
```

- [ ] **Step 5: 写样本准备状态 seed**

Modify `prototype/src/app/session/seed.ts`:

```ts
import type { SampleReadinessWorkflow } from './types';

export const initialSampleReadinessState: SampleReadinessWorkflow = {
  importMethod: 'csv',
  readinessState: 'partial',
  selectedMappingField: 'mode',
  isFrozen: false,
};
```

- [ ] **Step 6: 在 Provider 中实现样本准备状态与动作**

Create `prototype/src/app/session/sampleReadiness.ts`:

```ts
import type { SampleImportMethod, SampleReadinessState } from '../../types';
import type { SampleReadinessWorkflow } from './types';

export function setImportMethodState(
  current: SampleReadinessWorkflow,
  importMethod: SampleImportMethod,
): SampleReadinessWorkflow {
  return {
    ...current,
    importMethod,
    readinessState: current.isFrozen ? current.readinessState : 'importing',
  };
}

export function setReadinessWorkflowState(
  current: SampleReadinessWorkflow,
  readinessState: SampleReadinessState,
): SampleReadinessWorkflow {
  return {
    ...current,
    readinessState,
  };
}

export function freezeSampleState(current: SampleReadinessWorkflow): SampleReadinessWorkflow {
  return {
    ...current,
    readinessState: 'frozen',
    isFrozen: true,
  };
}
```

Modify `prototype/src/app/session/AppSessionContext.tsx`:

```tsx
const [sampleReadiness, setSampleReadiness] = useState<SampleReadinessWorkflow>(initialSampleReadinessState);

const value = useMemo<AppSessionValue>(() => ({
  // existing fields...
  sampleReadiness,
  setImportMethod: (method) => setSampleReadiness((prev) => setImportMethodState(prev, method)),
  setReadinessState: (state) => setSampleReadiness((prev) => setReadinessWorkflowState(prev, state)),
  freezeSample: () => setSampleReadiness((prev) => freezeSampleState(prev)),
}), [role, workflow, sampleReadiness]);
```

- [ ] **Step 7: 运行测试确认仍在组件缺失阶段**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/samples/SampleImportWizard.test.tsx
```

Expected:

```text
FAIL
Cannot find module './SampleImportWizard'
```

- [ ] **Step 8: 提交样本准备状态基础**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/types/index.ts prototype/src/app/session/types.ts prototype/src/app/session/seed.ts prototype/src/app/session/AppSessionContext.tsx prototype/src/app/session/sampleReadiness.ts
git commit -m "feat: add sample readiness workflow state to app session"
```

## Task 2: Build Sample Import Wizard

**Files:**
- Create: `prototype/src/pages/samples/SampleImportWizard.tsx`
- Create: `prototype/src/pages/samples/SampleImportMethodCard.tsx`
- Create: `prototype/src/pages/samples/SampleFieldMappingEditor.tsx`
- Create: `prototype/src/pages/samples/sampleReadinessModel.ts`
- Modify: `prototype/src/data/mockData.ts`
- Modify: `prototype/src/pages/sampleLedgerPages.tsx`
- Test: `prototype/src/pages/samples/SampleImportWizard.test.tsx`

- [ ] **Step 1: 扩展 mock 数据，提供导入方式与映射编辑所需数据**

Modify `prototype/src/data/mockData.ts`:

```ts
export const sampleImportMethods = [
  { id: 'historian', label: 'Historian 导出', detail: '适合离线导出后导入样本窗口。', availability: 'ready' },
  { id: 'csv', label: 'CSV 模拟数据', detail: '当前 demo-data 已接入并可用于工作流演示。', availability: 'active' },
  { id: 'opc', label: 'OPC 只读连接', detail: '只读接入，不写 DCS，不改变现场参数。', availability: 'ready' },
] as const;

export const sampleMappingMatrix = ledgerMappings;
```

- [ ] **Step 2: 实现导入方式卡片**

Create `prototype/src/pages/samples/SampleImportMethodCard.tsx`:

```tsx
import type { SampleImportMethod } from '../../types';

export function SampleImportMethodCard({
  label,
  detail,
  active,
  onClick,
}: {
  label: string;
  detail: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button className={`sample-import-card ${active ? 'active' : ''}`} onClick={onClick}>
      <strong>{label}</strong>
      <span>{detail}</span>
    </button>
  );
}
```

- [ ] **Step 3: 实现字段映射编辑器**

Create `prototype/src/pages/samples/SampleFieldMappingEditor.tsx`:

```tsx
import type { MappingFieldStatus } from '../../types';

export function SampleFieldMappingEditor({ fields }: { fields: MappingFieldStatus[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>源字段</th>
            <th>目标对象</th>
            <th>覆盖率</th>
            <th>状态</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field.source}>
              <th scope="row">{field.source}</th>
              <td>{field.target}</td>
              <td>{field.coverage}</td>
              <td>{field.status}</td>
              <td>{field.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: 写导入向导 view-model**

Create `prototype/src/pages/samples/sampleReadinessModel.ts`:

```ts
import { dataLineage, mappingGaps, sampleImportMethods, sampleMappingMatrix } from '../../data/mockData';

export function getSampleImportViewModel() {
  return {
    methods: sampleImportMethods,
    mappingMatrix: sampleMappingMatrix,
    mappingGaps,
    dataLineage,
  };
}
```

- [ ] **Step 5: 实现导入向导容器**

Create `prototype/src/pages/samples/SampleImportWizard.tsx`:

```tsx
import { useAppSession } from '../../app/session/AppSessionContext';
import { SampleImportMethodCard } from './SampleImportMethodCard';
import { SampleFieldMappingEditor } from './SampleFieldMappingEditor';
import { getSampleImportViewModel } from './sampleReadinessModel';

export function SampleImportWizard() {
  const { sampleReadiness, setImportMethod } = useAppSession();
  const { methods, mappingMatrix, mappingGaps, dataLineage } = getSampleImportViewModel();

  return (
    <section className="sample-import-wizard">
      <div className="sample-step-rail">
        <span className="active">1. 选择导入方式</span>
        <span>2. 校对字段映射</span>
        <span>3. 查看解析结果</span>
      </div>
      <div className="grid two">
        <section className="panel">
          <h2>导入方式</h2>
          <p>当前导入方式：{methods.find((item) => item.id === sampleReadiness.importMethod)?.label}</p>
          <div className="sample-import-methods">
            {methods.map((method) => (
              <SampleImportMethodCard
                key={method.id}
                label={method.label}
                detail={method.detail}
                active={sampleReadiness.importMethod === method.id}
                onClick={() => setImportMethod(method.id)}
              />
            ))}
          </div>
        </section>
        <section className="panel">
          <h2>解析结果</h2>
          <p>当前已接入 {dataLineage.csvFile}，采样间隔 {dataLineage.sampleIntervalSeconds}s。</p>
          <p>安全边界：{dataLineage.safetyBoundary}</p>
          <SampleFieldMappingEditor fields={mappingMatrix} />
          <ul>
            {mappingGaps.map((gap) => (
              <li key={`${gap.field}-${gap.scope}`}>{gap.field} · {gap.action}</li>
            ))}
          </ul>
        </section>
      </div>
    </section>
  );
}
```

- [ ] **Step 6: 用新导入向导替换原 `ImportPage` 主体**

Modify `prototype/src/pages/sampleLedgerPages.tsx`:

```tsx
import { SampleImportWizard } from './samples/SampleImportWizard';

export function ImportPage({ route }: { route: NavigationItem }) {
  return (
    <>
      <PageHeader route={route} />
      <SampleImportWizard />
    </>
  );
}
```

- [ ] **Step 7: 运行测试确认通过**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/samples/SampleImportWizard.test.tsx
```

Expected:

```text
PASS  SampleImportWizard state
```

- [ ] **Step 8: 提交导入向导**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/pages/samples prototype/src/data/mockData.ts prototype/src/pages/sampleLedgerPages.tsx
git commit -m "feat: rebuild sample import flow as a stateful wizard"
```

## Task 3: Build Readiness Workbench And Freeze Flow

**Files:**
- Create: `prototype/src/pages/samples/SampleReadinessWorkbench.tsx`
- Create: `prototype/src/pages/samples/SampleValidationPanel.tsx`
- Create: `prototype/src/pages/samples/SampleFreezePanel.tsx`
- Modify: `prototype/src/pages/sampleLedgerPages.tsx`
- Test: `prototype/src/pages/samples/SampleReadinessWorkbench.test.tsx`

- [ ] **Step 1: 写失败测试，要求冻结样本后状态变为 `frozen` 且提示只读**

```tsx
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { SampleReadinessWorkbench } from './SampleReadinessWorkbench';
import { renderWithSession } from '../../test/renderWithSession';

describe('SampleReadinessWorkbench freeze flow', () => {
  it('freezes sample and shows read-only state', async () => {
    const user = userEvent.setup();
    renderWithSession(<SampleReadinessWorkbench />);

    await user.click(screen.getByRole('button', { name: /冻结样本/i }));

    expect(screen.getByText(/当前状态：frozen/i)).toBeInTheDocument();
    expect(screen.getByText(/样本已冻结，字段映射只读/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/samples/SampleReadinessWorkbench.test.tsx
```

Expected:

```text
FAIL
Cannot find module './SampleReadinessWorkbench'
```

- [ ] **Step 3: 实现校验结果面板**

Create `prototype/src/pages/samples/SampleValidationPanel.tsx`:

```tsx
import { MetricCard } from '../../components/ui';
import { currentBatch, dataLineage, primaryLoopId, tuningCase, valveCheckLoopId } from '../../data/mockData';

export function SampleValidationPanel({ readinessState }: { readinessState: string }) {
  return (
    <>
      <section className="grid four">
        <MetricCard label="批次映射率" value={`${Math.round(currentBatch.mappedRate * 100)}%`} delta="字段已映射" />
        <MetricCard label="批次好值率" value={`${Math.round(currentBatch.goodValueRate * 100)}%`} delta="来自 demo-data" />
        <MetricCard label="评审就绪率" value="94%" delta="缺 MODE 3 条" />
        <MetricCard label="当前状态" value={readinessState} delta="状态由 session 驱动" />
      </section>
      <section className="grid two">
        <section className="panel">
          <h2>质量规则</h2>
          <ul>
            <li>GOOD 进入评价</li>
            <li>BAD/FROZEN 降级为数据不足</li>
            <li>MAN 不进入有效自控强结论</li>
          </ul>
          <p>事件可用性：{dataLineage.eventsFile} 已接入，可用于扰动与边界追溯。</p>
        </section>
        <section className="panel">
          <h2>下一步</h2>
          <p>{primaryLoopId}、{valveCheckLoopId}、{tuningCase.loopId} 可进入 P0 主链。</p>
          <p>冻结前请确认字段缺口和现场核实项已显性留痕。</p>
        </section>
      </section>
    </>
  );
}
```

- [ ] **Step 4: 实现冻结面板**

Create `prototype/src/pages/samples/SampleFreezePanel.tsx`:

```tsx
import { currentBatch, evidencePackageView, valveCheckLoopId } from '../../data/mockData';

export function SampleFreezePanel({
  readinessState,
  isFrozen,
  onFreeze,
}: {
  readinessState: string;
  isFrozen: boolean;
  onFreeze: () => void;
}) {
  return (
    <section className={`panel ${isFrozen ? '' : 'warning-panel'}`}>
      <h2>冻结样本</h2>
      <p>当前状态：{readinessState}</p>
      <ul>
        <li>样本窗口固定：{currentBatch.window}</li>
        <li>证据包状态：{evidencePackageView.status}</li>
        <li>现场核实项：{valveCheckLoopId}</li>
      </ul>
      {isFrozen ? (
        <p>样本已冻结，字段映射只读。</p>
      ) : (
        <button type="button" className="button" onClick={onFreeze}>冻结样本</button>
      )}
    </section>
  );
}
```

- [ ] **Step 5: 实现就绪校验工作台容器**

Create `prototype/src/pages/samples/SampleReadinessWorkbench.tsx`:

```tsx
import { useAppSession } from '../../app/session/AppSessionContext';
import { SampleFreezePanel } from './SampleFreezePanel';
import { SampleValidationPanel } from './SampleValidationPanel';

export function SampleReadinessWorkbench() {
  const { sampleReadiness, freezeSample } = useAppSession();

  return (
    <section className="sample-readiness-workbench">
      <SampleValidationPanel readinessState={sampleReadiness.readinessState} />
      <SampleFreezePanel
        readinessState={sampleReadiness.readinessState}
        isFrozen={sampleReadiness.isFrozen}
        onFreeze={freezeSample}
      />
    </section>
  );
}
```

- [ ] **Step 6: 用新工作台替换原 `ReadinessPage` / `FreezePage` 主体**

Modify `prototype/src/pages/sampleLedgerPages.tsx`:

```tsx
import { SampleReadinessWorkbench } from './samples/SampleReadinessWorkbench';

export function ReadinessPage({ route }: { route: NavigationItem }) {
  return (
    <>
      <PageHeader route={route} />
      <SampleReadinessWorkbench />
    </>
  );
}

export function FreezePage({ route }: { route: NavigationItem }) {
  return (
    <>
      <PageHeader route={route} state="success" />
      <SampleReadinessWorkbench />
    </>
  );
}
```

- [ ] **Step 7: 运行测试确认通过**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/samples/SampleReadinessWorkbench.test.tsx
```

Expected:

```text
PASS  SampleReadinessWorkbench freeze flow
```

- [ ] **Step 8: 提交就绪校验工作台**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/pages/samples/SampleReadinessWorkbench.tsx prototype/src/pages/samples/SampleValidationPanel.tsx prototype/src/pages/samples/SampleFreezePanel.tsx prototype/src/pages/sampleLedgerPages.tsx
git commit -m "feat: rebuild sample readiness and freeze workflow"
```

## Task 4: Split Styles And Add Smoke Coverage

**Files:**
- Create: `prototype/src/styles/sample-readiness.css`
- Modify: `prototype/src/styles/app.css`
- Modify: `prototype/src/main.tsx`
- Modify: `prototype/tests/smoke.spec.ts`

- [ ] **Step 1: 写样本准备样式分域文件**

Create `prototype/src/styles/sample-readiness.css`:

```css
.sample-import-wizard,
.sample-readiness-workbench {
  display: grid;
  gap: var(--space-4);
}

.sample-step-rail {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.sample-step-rail span {
  padding: var(--space-2) var(--space-3);
  border-radius: 999px;
  background: var(--bg-muted);
  color: var(--text-secondary);
  font-weight: 700;
}

.sample-step-rail .active {
  background: #dbeafe;
  color: var(--accent-blue);
}

.sample-import-methods {
  display: grid;
  gap: var(--space-3);
}

.sample-import-card {
  width: 100%;
  min-height: 96px;
  display: grid;
  gap: var(--space-2);
  justify-items: start;
  align-content: start;
  padding: var(--space-3);
  text-align: left;
  background: white;
  color: var(--text-primary);
  border: 1px solid var(--border-default);
}

.sample-import-card.active {
  border-color: var(--accent-blue);
  box-shadow: inset 4px 0 0 var(--accent-blue);
  background: #f8fbff;
}
```

- [ ] **Step 2: 从全局样式中保持仅公共规则**

Modify `prototype/src/styles/app.css` to avoid adding sample-page-only layout rules there. If any sample-specific rules are added during implementation, move them out.

- [ ] **Step 3: 引入样式文件**

Modify `prototype/src/main.tsx`:

```ts
import './styles/sample-readiness.css';
```

- [ ] **Step 4: 补样本导入 / 冻结 smoke**

Append to `prototype/tests/smoke.spec.ts`:

```ts
test('switches sample import method and freezes sample', async ({ page }) => {
  await page.goto('/samples/import');
  await page.getByRole('button', { name: /OPC 只读连接/i }).click();
  await expect(page.getByText(/当前导入方式：OPC 只读连接/i)).toBeVisible();

  await page.goto('/samples/readiness');
  await page.getByRole('button', { name: /冻结样本/i }).click();
  await expect(page.getByText(/当前状态：frozen/i)).toBeVisible();
  await expect(page.getByText(/样本已冻结，字段映射只读/i)).toBeVisible();
});
```

- [ ] **Step 5: 运行单测、构建与 smoke**

Run:

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run test:unit -- src/pages/samples/SampleImportWizard.test.tsx src/pages/samples/SampleReadinessWorkbench.test.tsx
npm run build
npx playwright test tests/smoke.spec.ts -g "switches sample import method and freezes sample"
```

Expected:

```text
PASS
vite v...
1 passed
```

- [ ] **Step 6: 提交并推送**

```bash
cd /Users/zhangping/DEV/CLPM
git add prototype/src/styles/sample-readiness.css prototype/src/main.tsx prototype/tests/smoke.spec.ts
git commit -m "test: cover sample import and freeze workflow"
git push
```

## Spec Coverage Check

- 导入向导：Task 2
- 字段映射与解析结果：Task 2
- 就绪校验与阻断项：Task 3
- 样本冻结状态流转：Task 1、Task 3
- 样式分域与 smoke：Task 4

未覆盖项：

- 真正可编辑的字段映射表单
- 阻断项解除后的跨页联动
- 与首页/排行的自动开放能力联动

这些进入下一轮样本模块增强计划。

## Placeholder Scan

- 无 `TBD`
- 无 `TODO`
- 无“后续补实现”型说明

## Type Consistency Check

- 样本流程统一使用 `sampleReadiness`
- 状态统一使用 `SampleReadinessState`
- 导入方式统一使用 `SampleImportMethod`
