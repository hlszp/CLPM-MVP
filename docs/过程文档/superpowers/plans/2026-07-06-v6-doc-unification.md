# CLPM v6.0 文档统一升级 实施计划

> **For agentic workers:** 本计划针对文档工程任务（非典型代码 TDD），采用"差距分析 → 逐项更新 → 一致性校验"工作流。每个 Task 包含操作步骤、验证方式、提交点。

**Goal:** 将 PRD/ADS/IDS/FDS/DDS/UIUX/实现契约/DESIGN.md/AGENTS.md/CLAUDE.md/README.md 等所有核心文档统一升级到 v6.0，并确保与代码实现完全一致。

**Architecture:** 以 FDS v5.1 / UIUX v5.3 / DDS v4.1 / 实现契约 v1.0 为基准（已是 2026-07-04 最新版），向上推导 PRD/ADS/IDS，向下派生 DESIGN.md/AGENTS.md/README.md，最后做全量一致性校验。

**Tech Stack:** Markdown 文档 + Python 脚本（一致性校验）+ git 工作流

**执行分支:** `mb/doc-v6`（已创建并推送到 origin）

---

## 阶段总览

| 阶段 | 任务 | 输出 |
|---|---|---|
| 阶段 1 基准文档梳理 | T1-T3 | 基准要求清单 + 待更新文档差距清单 |
| 阶段 2 一致性校验 | T4-T9 | 代码 vs 文档差距清单（含反向校验） |
| 阶段 3 核心文档升级 | T10-T12 | PRD/ADS/IDS → v6.0 |
| 阶段 4 派生文档升级 | T13-T17 | 实现契约/DESIGN/FDS/DDS/UIUX → v6.0 引用对齐 |
| 阶段 5 项目文档更新 | T18-T20 | AGENTS/CLAUDE/README → v6.0 |
| 阶段 6 质量审核 | T21-T24 | CI 通过 + 引用关系最终校验 + PR |

---

## 阶段 1：基准文档梳理

### Task 1: 读取 4 份基准文档（FDS v5.1 / UIUX v5.3 / DDS v4.1 / 实现契约 v1.0）

**Files:**
- Read: `docs/设计文档/02-FDS/FDS.md`（v5.1, 2026-07-04）
- Read: `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md`（v5.3, 2026-07-04）
- Read: `docs/设计文档/04-DDS/DDS.md`（v4.1, 2026-07-04）
- Read: `docs/设计文档/00-BASELINE/implementation-contract.md`（v1.0, 2026-06-25）
- Create: `docs/过程文档/superpowers/plans/v6-baseline-extract.md`

- [ ] **Step 1: 读取 FDS v5.1 全文，提取术语表、模块清单、功能项**

操作：使用 Read 工具读取 FDS.md，将以下内容提取到 `v6-baseline-extract.md`：
- §术语表（所有名词定义）
- §模块清单（7 模块 + 1 门户）
- §功能项列表（每模块的"配置/运行/分析"三态功能）
- §数据流图引用
- §版本号声明

- [ ] **Step 2: 读取 UIUX v5.3 全文，提取 IA/路由/页面/组件清单**

操作：使用 Read 工具读取 ui-ux-design-guidelines.md（162KB，需分段读取），提取：
- §IA 信息架构（菜单树、路由清单）
- §页面清单（25+ 页面）
- §组件清单（共享组件、业务组件）
- §角色权限矩阵
- §状态机定义
- §设计 tokens（颜色/字体/间距）

- [ ] **Step 3: 读取 DDS v4.1 全文，提取表/字段清单**

操作：使用 Read 工具读取 DDS.md，提取：
- §所有 PostgreSQL 表名 + 字段名 + 类型
- §所有 TDengine 超级表 + 子表
- §枚举值定义
- §索引/约束
- §数据血缘 8 字段

- [ ] **Step 4: 读取实现契约 v1.0 全文，提取 IA/路由/API/权限/状态机/KPI 清单**

操作：使用 Read 工具读取 implementation-contract.md，提取：
- §IA 信息架构（6 模块+1门户 + 子菜单）
- §路由清单（前端路由路径）
- §API 端点清单
- §权限矩阵（5 角色 × 模块）
- §状态机清单（Action Tracker / Task / Loop 等）
- §KPI 12 指标清单

- [ ] **Step 5: 汇总到 v6-baseline-extract.md**

操作：把 Step 1-4 的提取结果合并到 `docs/过程文档/superpowers/plans/v6-baseline-extract.md`，作为后续所有更新的"事实来源"。

验证：`wc -l docs/过程文档/superpowers/plans/v6-baseline-extract.md` 应 ≥ 200 行

- [ ] **Step 6: Commit**

```bash
git add docs/过程文档/superpowers/plans/v6-baseline-extract.md
git commit -m "doc(v6): 提取 FDS/UIUX/DDS/实现契约基准信息"
```

---

### Task 2: 读取 3 份待更新文档（PRD v4.0 / ADS v4.0 / IDS v4.0）

**Files:**
- Read: `docs/设计文档/01-PRD/PRD.md`（v4.0, 2026-06-25）
- Read: `docs/设计文档/03-ADS/ADS.md`（v4.0, 2026-06-26）
- Read: `docs/设计文档/05-IDS/IDS.md`（v4.0, 2026-06-26）
- Create: `docs/过程文档/superpowers/plans/v6-gap-analysis.md`

- [ ] **Step 1: 读取 PRD v4.0，对比基准提取差距**

操作：读取 PRD.md 全文，对照 v6-baseline-extract.md，记录到 v6-gap-analysis.md：
- 术语差异（PRD 用词 vs FDS 用词）
- 模块清单差异（PRD 说"6 模块+1门户"vs FDS 说"7 模块+1门户"）
- 功能项差异
- 数据模型差异（PRD 描述 vs DDS v4.1 表/字段）
- KPI 指标差异
- 版本号声明差异

- [ ] **Step 2: 读取 ADS v4.0，对比基准提取差距**

操作：读取 ADS.md 全文，对照 v6-baseline-extract.md，记录：
- 架构组件差异
- 数据流差异
- 与 DDS v4.1 表名/字段名差异
- 与实现契约 v1.0 路由/API 差异

- [ ] **Step 3: 读取 IDS v4.0，对比基准提取差距**

操作：读取 IDS.md 全文，对照 v6-baseline-extract.md，记录：
- API 端点清单差异（IDS vs 实现契约）
- 请求/响应 Schema 差异
- 错误码差异
- 状态机差异

- [ ] **Step 4: 汇总差距清单到 v6-gap-analysis.md**

操作：把 Step 1-3 的差距合并，每条差距标记：
- 来源文档（PRD/ADS/IDS）
- 差距类型（术语/模块/功能/数据模型/API/状态机/版本号）
- 当前值
- 基准值
- 修复方案

验证：`grep -c "^|" docs/过程文档/superpowers/plans/v6-gap-analysis.md` 应 ≥ 30 条

- [ ] **Step 5: Commit**

```bash
git add docs/过程文档/superpowers/plans/v6-gap-analysis.md
git commit -m "doc(v6): 生成 PRD/ADS/IDS 与基准的差距清单"
```

---

### Task 3: 读取实际代码，提取"代码事实"

**Files:**
- Read: `backend/app/api/v1/endpoints/*.py`（所有 API 端点）
- Read: `backend/app/models/*.py`（所有 ORM 模型）
- Read: `backend/app/schemas/*.py`（所有 Pydantic Schema）
- Read: `frontend/apps/web-antd/src/router/routes/modules/*.ts`（前端路由）
- Create: `docs/过程文档/superpowers/plans/v6-code-facts.md`

- [ ] **Step 1: 提取后端 API 端点清单**

操作：用 Grep 搜索所有 `@router.{get,post,put,delete}` 装饰器，提取路径+方法+函数名，记录到 v6-code-facts.md。

验证：`grep -c "^- \`(GET|POST|PUT|DELETE)" v6-code-facts.md` 应 ≥ 40 条

- [ ] **Step 2: 提取后端 ORM 模型清单**

操作：用 Grep 搜索 `class XXX(Base):` 模型类，提取表名 `__tablename__` + 字段，记录到 v6-code-facts.md。

验证：`grep -c "^- 表:" v6-code-facts.md` 应 ≥ 15 张表

- [ ] **Step 3: 提取前端路由清单**

操作：读取 `frontend/apps/web-antd/src/router/routes/modules/*.ts`（dashboard/diagnosis/loop/metric/system/task/tuning/vben），提取所有路由 path+name+component。

验证：`grep -c "^- 路由:" v6-code-facts.md` 应 ≥ 25 条

- [ ] **Step 4: 提取前端权限矩阵**

操作：用 Grep 搜索 `roles: [...]` 或 `meta.roles`，提取每个路由的权限角色。

- [ ] **Step 5: Commit**

```bash
git add docs/过程文档/superpowers/plans/v6-code-facts.md
git commit -m "doc(v6): 提取代码事实清单（API/模型/路由/权限）"
```

---

## 阶段 2：一致性校验

### Task 4: 文档间引用一致性检查

**Files:**
- Create: `docs/过程文档/superpowers/plans/v6-doc-cross-check.md`

- [ ] **Step 1: 检查文档间版本号引用**

操作：用 Grep 在所有 `docs/设计文档/**/*.md` 中搜索 "v3.0"、"v3.1"、"v4.0"、"v4.1"、"v5.0"、"v5.1"、"v5.3" 等版本号引用，记录到 v6-doc-cross-check.md：
- 哪个文档引用了哪个版本
- 是否与实际版本一致
- 是否需要更新

- [ ] **Step 2: 检查文档间文件引用**

操作：用 Grep 搜索 `docs/设计文档/` 路径引用，验证被引用的文件是否存在。

- [ ] **Step 3: 检查术语统一性**

操作：用 Grep 搜索关键术语（如 "Action Tracker" vs "诊断跟踪"、"回路整定" vs "PID 整定"），验证全项目术语统一。

- [ ] **Step 4: Commit**

```bash
git add docs/过程文档/superpowers/plans/v6-doc-cross-check.md
git commit -m "doc(v6): 文档间引用一致性检查清单"
```

---

### Task 5: 代码 vs 设计文档 - API 端点校验

**Files:**
- Create: `docs/过程文档/superpowers/plans/v6-api-audit.md`

- [ ] **Step 1: 生成代码 API 清单**

操作：从 Task 3 提取的 v6-code-facts.md 中提取所有 API 端点。

- [ ] **Step 2: 生成 IDS API 清单**

操作：从 IDS v4.0 提取所有 API 端点（与 Task 2 Step 3 一致）。

- [ ] **Step 3: 双向对比**

操作：对每个 API 端点，检查：
- 代码有，IDS 是否有？（若无 → IDS 缺失，需补充）
- IDS 有，代码是否实现？（若无 → IDS 过期，需删除）
- 路径/方法/参数/响应是否一致？

记录到 v6-api-audit.md，标记 ✅/⚠️/❌。

- [ ] **Step 4: Commit**

```bash
git add docs/过程文档/superpowers/plans/v6-api-audit.md
git commit -m "doc(v6): API 端点代码-文档双向校验清单"
```

---

### Task 6: 代码 vs 设计文档 - 数据模型字段校验

**Files:**
- Create: `docs/过程文档/superpowers/plans/v6-model-audit.md`

- [ ] **Step 1: 提取代码 ORM 模型字段**

操作：从 Task 3 提取的所有 ORM 模型，列出每张表的字段名+类型。

- [ ] **Step 2: 提取 DDS 表/字段定义**

操作：从 DDS v4.1 提取所有表/字段定义。

- [ ] **Step 3: 双向对比**

操作：对每张表，检查：
- 代码字段名 vs DDS 字段名
- 代码字段类型 vs DDS 字段类型
- 代码索引/约束 vs DDS 索引/约束
- 代码枚举值 vs DDS 枚举值

记录到 v6-model-audit.md。

- [ ] **Step 4: 提取 TDengine 超级表/子表，对比 DDS**

操作：从代码中提取 TDengine schema（在 data_source/tdengine_provider.py 等），对比 DDS。

- [ ] **Step 5: Commit**

```bash
git add docs/过程文档/superpowers/plans/v6-model-audit.md
git commit -m "doc(v6): 数据模型代码-文档双向校验清单"
```

---

### Task 7: 代码 vs 设计文档 - 状态机校验

**Files:**
- Create: `docs/过程文档/superpowers/plans/v6-state-machine-audit.md`

- [ ] **Step 1: 提取代码中的状态机**

操作：用 Grep 搜索 `TaskStatus`、`LoopStatus`、`ActionStatus` 等枚举，提取状态机定义。

- [ ] **Step 2: 提取设计文档中的状态机**

操作：从实现契约 v1.0、FDS v5.1 提取状态机定义。

- [ ] **Step 3: 双向对比**

操作：对每个状态机，检查：
- 状态枚举值是否一致
- 状态转换是否一致
- 中文显示是否一致

- [ ] **Step 4: Commit**

```bash
git add docs/过程文档/superpowers/plans/v6-state-machine-audit.md
git commit -m "doc(v6): 状态机代码-文档双向校验清单"
```

---

### Task 8: 代码 vs 设计文档 - 路由/权限校验

**Files:**
- Create: `docs/过程文档/superpowers/plans/v6-route-perm-audit.md`

- [ ] **Step 1: 提取前端路由清单（已在 Task 3 完成）**

- [ ] **Step 2: 提取设计文档路由清单**

操作：从实现契约 v1.0、UIUX v5.3 提取路由清单。

- [ ] **Step 3: 双向对比路由**

操作：对每个路由，检查：
- 路径是否一致
- 名称是否一致
- 组件路径是否一致

- [ ] **Step 4: 提取代码权限矩阵**

操作：用 Grep 搜索 `require_roles`、`roles:` 等，提取每个 API/路由的权限要求。

- [ ] **Step 5: 对比设计文档权限矩阵**

操作：从实现契约 v1.0 §5 提取权限矩阵，对比代码。

- [ ] **Step 6: Commit**

```bash
git add docs/过程文档/superpowers/plans/v6-route-perm-audit.md
git commit -m "doc(v6): 路由权限代码-文档双向校验清单"
```

---

### Task 9: 反向校验 - 代码功能 vs 文档覆盖

**Files:**
- Create: `docs/过程文档/superpowers/plans/v6-reverse-audit.md`

- [ ] **Step 1: 提取代码中所有 API 端点，检查文档覆盖**

操作：从 Task 3 提取的所有 API，对每个端点检查：
- IDS 是否描述？
- FDS 是否描述？
- 实现契约是否列出？

- [ ] **Step 2: 提取代码中所有模型，检查文档覆盖**

操作：从 Task 3 提取的所有 ORM 模型，对每个表检查：
- DDS 是否描述？
- FDS 是否描述？

- [ ] **Step 3: 提取代码中所有前端页面，检查文档覆盖**

操作：从 Task 3 提取的所有路由，对每个页面检查：
- UIUX 是否描述？
- FDS 是否描述？

- [ ] **Step 4: 提取设计文档中所有功能，检查代码实现**

操作：从 FDS 提取所有功能项，对每个功能检查：
- 是否有对应 API？
- 是否有对应前端页面？

- [ ] **Step 5: Commit**

```bash
git add docs/过程文档/superpowers/plans/v6-reverse-audit.md
git commit -m "doc(v6): 反向校验 - 代码功能 vs 文档覆盖"
```

---

## 阶段 3：核心文档升级到 v6.0

### Task 10: PRD v4.0 → v6.0

**Files:**
- Modify: `docs/设计文档/01-PRD/PRD.md`

- [ ] **Step 1: 更新版本号头部**

操作：把头部 `当前版本: v4.0 (数据架构版)` 改为 `当前版本: v6.0 (统一升级版)`，`发布日期: 2026-07-06`，`基线来源` 加入 FDS v5.1/DDS v4.1/UIUX v5.3。

- [ ] **Step 2: 更新术语表对齐 FDS v5.1**

操作：根据 v6-gap-analysis.md，把 PRD 中的术语统一为 FDS v5.1 的术语表。

- [ ] **Step 3: 更新模块清单**

操作：根据 FDS v5.1 §模块清单，把 PRD 中的"6 模块+1门户"改为"7 模块+1门户"（加入任务管理模块）。

- [ ] **Step 4: 更新数据模型引用对齐 DDS v4.1**

操作：根据 v6-model-audit.md，把 PRD 中所有数据模型描述对齐 DDS v4.1 的表名/字段名。

- [ ] **Step 5: 更新 KPI 指标清单**

操作：根据代码事实（Task 3），把 PRD 中的"8 大 KPI"改为"12 大 KPI"，列出所有 12 个指标。

- [ ] **Step 6: 更新引用的文档版本号**

操作：把 PRD 中所有引用的文档版本号更新为 v6.0（FDS/DDS/ADS/IDS/UIUX）。

- [ ] **Step 7: 添加变更记录**

操作：在 §0 文档变更记录中添加：
```
| v6.0 | 2026-07-06 | 统一升级：对齐 FDS v5.1/DDS v4.1/UIUX v5.3/实现契约 v1.0；模块数 6→7；KPI 数 8→12；所有引用统一到 v6.0 |
```

- [ ] **Step 8: 验证**

操作：
- `head -10 docs/设计文档/01-PRD/PRD.md` 应显示 v6.0
- `grep -c "v3\.\|v4\.0\|v5\.1\|v5\.3" docs/设计文档/01-PRD/PRD.md` 应 ≤ 5（仅历史引用）

- [ ] **Step 9: Commit**

```bash
git add docs/设计文档/01-PRD/PRD.md
git commit -m "doc(v6): PRD v4.0 → v6.0，对齐 FDS/DDS/UIUX 基准"
```

---

### Task 11: ADS v4.0 → v6.0

**Files:**
- Modify: `docs/设计文档/03-ADS/ADS.md`

- [ ] **Step 1: 更新版本号头部**

操作：`v4.0` → `v6.0`，`发布日期: 2026-07-06`，`设计依据` 加入 FDS v5.1/DDS v4.1/UIUX v5.3。

- [ ] **Step 2: 更新架构组件清单**

操作：根据代码事实（Task 3），把 ADS 中的架构组件清单更新为实际代码中的组件（DataPlanner/ConfidenceEvaluator/TaskTracker/12 个 MetricCalculator/预处理 Pipeline 等）。

- [ ] **Step 3: 更新数据流图**

操作：根据实现契约 v1.0 §数据流，更新 ADS 中的数据流图描述。

- [ ] **Step 4: 更新表/字段引用对齐 DDS v4.1**

操作：根据 v6-model-audit.md，把 ADS 中所有表名/字段名对齐 DDS v4.1。

- [ ] **Step 5: 更新 API 引用对齐 IDS v6.0（Task 12 完成后回填）**

操作：先标注 TODO，Task 12 完成后回填。

- [ ] **Step 6: 添加变更记录**

- [ ] **Step 7: 验证**

操作：`head -10 docs/设计文档/03-ADS/ADS.md` 应显示 v6.0

- [ ] **Step 8: Commit**

```bash
git add docs/设计文档/03-ADS/ADS.md
git commit -m "doc(v6): ADS v4.0 → v6.0，对齐 DDS/实现契约基准"
```

---

### Task 12: IDS v4.0 → v6.0

**Files:**
- Modify: `docs/设计文档/05-IDS/IDS.md`

- [ ] **Step 1: 更新版本号头部**

操作：`v4.0` → `v6.0`，`发布日期: 2026-07-06`。

- [ ] **Step 2: 根据 v6-api-audit.md 更新 API 端点清单**

操作：根据 Task 5 的校验结果，把 IDS 中的 API 端点清单更新为与代码完全一致：
- 删除代码中不存在的端点
- 补充代码中存在但 IDS 缺失的端点
- 修正路径/方法/参数/响应不一致的端点

- [ ] **Step 3: 更新请求/响应 Schema 对齐代码**

操作：根据 v6-model-audit.md，把 IDS 中的 Schema 对齐代码实际 Pydantic 模型。

- [ ] **Step 4: 更新状态机引用**

操作：根据 v6-state-machine-audit.md，更新 IDS 中的状态机引用。

- [ ] **Step 5: 更新错误码清单**

操作：根据代码中 `BizError` 的所有 code，更新 IDS 的错误码清单。

- [ ] **Step 6: 添加变更记录**

- [ ] **Step 7: 验证**

操作：
- `head -10 docs/设计文档/05-IDS/IDS.md` 应显示 v6.0
- `grep -c "TBD\|TODO" docs/设计文档/05-IDS/IDS.md` 应为 0

- [ ] **Step 8: Commit**

```bash
git add docs/设计文档/05-IDS/IDS.md
git commit -m "doc(v6): IDS v4.0 → v6.0，对齐代码 API 端点"
```

---

## 阶段 4：派生文档升级到 v6.0

### Task 13: 实现契约 v1.0 → v2.0

**Files:**
- Modify: `docs/设计文档/00-BASELINE/implementation-contract.md`

- [ ] **Step 1: 更新版本号 v1.0 → v2.0**

操作：头部 `当前版本：v1.0` → `v2.0`，`发布日期：2026-07-06`，加入"v6.0 文档统一升级基线"。

- [ ] **Step 2: 根据 v6-route-perm-audit.md 更新路由清单**

- [ ] **Step 3: 根据 v6-api-audit.md 更新 API 端点清单**

- [ ] **Step 4: 根据 v6-state-machine-audit.md 更新状态机清单**

- [ ] **Step 5: 更新 KPI 指标清单为 12 个**

- [ ] **Step 6: 验证**

- [ ] **Step 7: Commit**

```bash
git add docs/设计文档/00-BASELINE/implementation-contract.md
git commit -m "doc(v6): 实现契约 v1.0 → v2.0，对齐 v6.0 文档"
```

---

### Task 14: DESIGN.md v2.1 → v3.0

**Files:**
- Modify: `DESIGN.md`

- [ ] **Step 1: 更新头部**

操作：`日期：2026-06-25（对齐 PRD v3.1、UI/UX v4.1 与重构后实现契约 v1.0）` → `日期：2026-07-06（对齐 PRD v6.0、UI/UX v6.0、实现契约 v2.0）`，`版本` 添加 `v3.0`。

- [ ] **Step 2: 更新权威来源声明表**

操作：把表中的版本号全部更新为 v6.0：
- PRD v3.1 → v6.0
- 实现契约 v1.0 → v2.0
- FDS v3.0（待追认）→ v6.0
- ADS v3.0（待校准）→ v6.0
- DDS v3.0（待追认）→ v6.0
- IDS v3.0（待追认）→ v6.0
- UI/UX v4.1 → v6.0

- [ ] **Step 3: Commit**

```bash
git add DESIGN.md
git commit -m "doc(v6): DESIGN.md v2.1 → v3.0，对齐 v6.0 文档基线"
```

---

### Task 15: FDS v5.1 → v6.0（仅版本号 + 引用更新）

**Files:**
- Modify: `docs/设计文档/02-FDS/FDS.md`

- [ ] **Step 1: 更新版本号 v5.1 → v6.0**

- [ ] **Step 2: 更新设计依据中的版本号引用**

- [ ] **Step 3: 添加变更记录**

- [ ] **Step 4: Commit**

```bash
git add docs/设计文档/02-FDS/FDS.md
git commit -m "doc(v6): FDS v5.1 → v6.0，版本号统一升级"
```

---

### Task 16: DDS v4.1 → v6.0（仅版本号 + 引用更新）

**Files:**
- Modify: `docs/设计文档/04-DDS/DDS.md`

- [ ] **Step 1: 更新版本号 v4.1 → v6.0**

- [ ] **Step 2: 更新设计依据中的版本号引用**

- [ ] **Step 3: 添加变更记录**

- [ ] **Step 4: Commit**

```bash
git add docs/设计文档/04-DDS/DDS.md
git commit -m "doc(v6): DDS v4.1 → v6.0，版本号统一升级"
```

---

### Task 17: UIUX v5.3 → v6.0（仅版本号 + 引用更新）

**Files:**
- Modify: `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md`

- [ ] **Step 1: 更新版本号 v5.3 → v6.0**

- [ ] **Step 2: 更新设计依据中的版本号引用**

- [ ] **Step 3: 添加变更记录**

- [ ] **Step 4: Commit**

```bash
git add docs/设计文档/06-UIUX/ui-ux-design-guidelines.md
git commit -m "doc(v6): UIUX v5.3 → v6.0，版本号统一升级"
```

---

## 阶段 5：项目文档更新

### Task 18: 统一 AGENTS.md 和 CLAUDE.md（单一来源）

**Files:**
- Modify: `AGENTS.md`
- Delete: `CLAUDE.md`（或改为软链）

- [ ] **Step 1: 更新 AGENTS.md 内容**

操作：根据前 17 个 Task 的变更，更新 AGENTS.md：
- 当前基线表：所有版本号 → v6.0
- v4.0 重构完成状态：保留（历史记录）
- 开发环境运行指南：测试用例数 1239 → 1762
- 关键注意事项：删除 "Git 分支：当前在 remediation/v1.1.0"
- 关键注意事项：删除 "6 个预存在 TypeScript 错误"
- 关键注意事项：更新 "MetricCalculator 8 大 KPI" → "12 大 KPI"
- 核心架构组件：预处理 Pipeline 路径修正为 `app/services/preprocessing/`
- 添加 CLAUDE.md 中独有的"工业桌面端 UI/UX 改造基线"行

- [ ] **Step 2: 删除 CLAUDE.md，建立软链**

操作：
```bash
rm CLAUDE.md
ln -s AGENTS.md CLAUDE.md
ls -la CLAUDE.md  # 应显示 -> AGENTS.md
```

- [ ] **Step 3: 验证**

操作：
- `head -10 AGENTS.md` 应反映 v6.0
- `readlink CLAUDE.md` 应为 `AGENTS.md`

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md CLAUDE.md
git commit -m "doc(v6): 统一 AGENTS.md 为单一来源，CLAUDE.md 改为软链"
```

---

### Task 19: README.md → v6.0

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新版本号**

操作：`版本：**v4.0**` → `版本：**v6.0**（统一升级版）`

- [ ] **Step 2: 更新测试用例数**

操作：`pytest（1239 用例）` → `pytest（1762 用例）`

- [ ] **Step 3: 更新 UI/UX 版本引用**

操作：`UI/UX 设计规范 ... （v5.1）` → `（v6.0）`

- [ ] **Step 4: 更新模块数**

操作：`7 模块+门户` 保留（已是 7 模块）

- [ ] **Step 5: 修正 HTTPS 配置章节**

操作：删除 `deploy/ssl/` 引用（目录不存在），改为"按需配置 SSL 证书"。

- [ ] **Step 6: 更新"当前共识"表**

操作：`当前版本 v4.0 — 7 阶段系统重构全部完成，后端 1239 测试用例通过` → `v6.0 — 文档统一升级版，1762 测试用例通过`

- [ ] **Step 7: 更新"当前有效文档"表**

操作：所有版本号引用 → v6.0

- [ ] **Step 8: 验证**

操作：
- `grep -c "v4\.0\|v5\.1\|v5\.3\|1239" README.md` 应 ≤ 5（仅历史记录）

- [ ] **Step 9: Commit**

```bash
git add README.md
git commit -m "doc(v6): README.md 升级到 v6.0，修正测试数和 SSL 引用"
```

---

### Task 20: 文档索引更新

**Files:**
- Modify: `docs/过程文档/design-documents-index-2026-06-16.md`

- [ ] **Step 1: 更新文档索引**

操作：把所有版本号更新为 v6.0，添加 v6.0 升级记录。

- [ ] **Step 2: Commit**

```bash
git add docs/过程文档/design-documents-index-2026-06-16.md
git commit -m "doc(v6): 文档索引升级到 v6.0"
```

---

## 阶段 6：质量审核

### Task 21: 全量后端测试 + lint

- [ ] **Step 1: 运行 ruff check**

```bash
cd backend && uv run ruff check .
```

预期：All checks passed!

- [ ] **Step 2: 运行 ruff format --check**

```bash
cd backend && uv run ruff format --check .
```

预期：所有文件已格式化

- [ ] **Step 3: 运行 pytest**

```bash
cd backend && uv run pytest -q
```

预期：1762 passed

---

### Task 22: 前端 type check + lint + build

- [ ] **Step 1: 类型检查**

```bash
cd frontend && pnpm run check:type
```

预期：0 errors

- [ ] **Step 2: Lint**

```bash
cd frontend && pnpm run lint
```

预期：通过

- [ ] **Step 3: Build**

```bash
cd frontend && pnpm run build
```

预期：构建成功

---

### Task 23: 文档间引用关系最终校验

**Files:**
- Create: `docs/过程文档/superpowers/plans/v6-final-verification.md`

- [ ] **Step 1: 验证所有文档版本号为 v6.0**

操作：
```bash
grep -l "v6\.0" docs/设计文档/00-BASELINE/implementation-contract.md \
  docs/设计文档/01-PRD/PRD.md \
  docs/设计文档/02-FDS/FDS.md \
  docs/设计文档/03-ADS/ADS.md \
  docs/设计文档/04-DDS/DDS.md \
  docs/设计文档/05-IDS/IDS.md \
  docs/设计文档/06-UIUX/ui-ux-design-guidelines.md \
  DESIGN.md AGENTS.md README.md
```

预期：9 个文件全部匹配

- [ ] **Step 2: 验证文档间引用一致性**

操作：用 Grep 搜索所有"v3." "v4." "v5."，确认仅出现在历史变更记录中。

- [ ] **Step 3: 验证 CLAUDE.md 软链**

操作：`readlink CLAUDE.md` 应为 `AGENTS.md`

- [ ] **Step 4: 生成最终验证报告**

操作：把所有验证结果汇总到 v6-final-verification.md。

- [ ] **Step 5: Commit**

```bash
git add docs/过程文档/superpowers/plans/v6-final-verification.md
git commit -m "doc(v6): v6.0 升级最终验证报告"
```

---

### Task 24: 推送并创建 PR

- [ ] **Step 1: 推送所有 commit**

```bash
git push origin mb/doc-v6
```

- [ ] **Step 2: 创建 PR**

```bash
gh pr create --base main --head mb/doc-v6 \
  --title "doc(v6.0): 全量文档统一升级到 v6.0" \
  --body "..."
```

- [ ] **Step 3: 等待 CI 通过**

- [ ] **Step 4: 通知用户审核**

---

## Self-Review

### Spec coverage
- 用户要求 1：PRD/ADS/IDS 对齐 FDS/UIUX/DDS 2026-07-04 版本 → Task 10/11/12 ✅
- 用户要求 1：代码与设计文档一致性校验 → Task 5/6/7/8/9 ✅
- 用户要求 1：版本号统一 → v6.0 → Task 13-17 ✅
- 用户要求 2：接口契约（实现契约）→ Task 13 ✅
- 用户要求 2：设计资源（DESIGN.md）→ Task 14 ✅
- 用户要求 2：agents 配置（AGENTS.md/CLAUDE.md）→ Task 18 ✅
- 用户要求 2：README → Task 19 ✅
- 用户要求 3：通过质量审核后再处理其他 → Task 21-24 ✅

### Placeholder scan
- 无 TBD/TODO（除了 Task 11 Step 5 ADS 回填 IDS v6.0 引用，将在 Task 12 完成后回填）
- 所有步骤都有具体操作和验证方式

### Type consistency
- 版本号统一为 v6.0
- 文档名引用统一使用相对路径
