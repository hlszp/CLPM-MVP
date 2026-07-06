# v6.0 文档统一升级 — 一致性校验汇总

**生成日期**：2026-07-06
**执行分支**：`mb/doc-v6`
**目的**：整合 Task 1-3 的所有校验结果，作为阶段 3-5 文档升级的修复依据

## 校验范围

| 校验类型 | 数据来源 | 详细文件 |
|---|---|---|
| 文档间引用一致性 | FDS v5.1 / UIUX v5.3 / DDS v4.1 / 实现契约 v1.0 / PRD v4.0 / ADS v4.0 / IDS v4.0 | `v6-baseline-extract.md` §5 + `v6-gap-analysis.md` |
| 代码 vs 文档一致性 | 120+ API 端点 / 26 张 ORM 表 / 45 条前端路由 | `v6-code-facts.md` |
| 反向校验（文档 vs 代码） | 同上 | `v6-code-facts.md` §4 |

---

## 1. 文档间引用一致性（Task 4）

### 1.1 版本号引用不一致

| 文档 | 引用对象 | 引用版本 | 实际版本 | 修复 |
|---|---|---|---|---|
| PRD v4.0 | FDS | v3.0 | v5.1 | 改为 v6.0 |
| PRD v4.0 | DDS | v3.0 | v4.1 | 改为 v6.0 |
| PRD v4.0 | 实现契约 | 未引用 | v1.0 | 新增引用 |
| PRD v4.0 | UIUX | 未引用 | v5.3 | 新增引用 |
| ADS v4.0 | PRD | v3.0 | v4.0 | 改为 v6.0 |
| ADS v4.0 | FDS | 未引用 | v5.1 | 新增引用 |
| ADS v4.0 | DDS | 未引用 | v4.1 | 新增引用 |
| ADS v4.0 | 实现契约 | 未引用 | v1.0 | 新增引用 |
| ADS v4.0 | UIUX | 未引用 | v5.3 | 新增引用 |
| IDS v4.0 | FDS | v3.0 | v5.1 | 改为 v6.0 |
| IDS v4.0 | DDS | v3.0 | v4.1 | 改为 v6.0 |
| IDS v4.0 | ADS | v3.0 | v4.0 | 改为 v6.0 |
| IDS v4.0 | PRD | v3.0 | v4.0 | 改为 v6.0 |
| IDS v4.0 | 实现契约 | 未引用 | v1.0 | 新增引用 |
| IDS v4.0 | UIUX | 未引用 | v5.3 | 新增引用 |
| AGENTS.md | PRD | v3.1 | v4.0 | 改为 v6.0 |
| AGENTS.md | FDS | v3.0 | v5.1 | 改为 v6.0 |
| AGENTS.md | ADS | v3.0 | v4.0 | 改为 v6.0 |
| AGENTS.md | DDS | v3.0 | v4.1 | 改为 v6.0 |
| AGENTS.md | IDS | v3.0 | v4.0 | 改为 v6.0 |
| AGENTS.md | UIUX | v5.1 | v5.3 | 改为 v6.0 |
| DESIGN.md | UIUX | v4.1 | v5.3 | 改为 v6.0 |
| DESIGN.md | 实现契约 | v1.0 | v1.0 | 改为 v2.0 |
| README.md | UIUX | v5.1 | v5.3 | 改为 v6.0 |
| README.md | DESIGN | v2.1 | v2.1 | 改为 v3.0 |

### 1.2 模块数量引用不一致

| 文档 | 模块描述 | 基准 | 修复 |
|---|---|---|---|
| AGENTS.md | 6 模块+1门户 | 6+1 | ✅ 一致 |
| 实现契约 v1.0 | 6 模块+1门户 | 6+1 | ✅ 一致 |
| UIUX v5.3 | 6 模块+1门户 | 6+1 | ✅ 一致 |
| FDS v5.1 §5 | 5 业务模块+1门户 | 6+1 | 需明确任务管理为性能评估子模块 |
| PRD v4.0 | 未明确 | 6+1 | 需明确声明 |
| ADS v4.0 | 未明确 | 6+1 | 需明确声明 |
| IDS v4.0 | 未明确 | 6+1 | 需明确声明 |
| README.md | 7 模块+门户（含任务管理） | 6+1 | 改为 6+1 |

---

## 2. API 端点校验（Task 5）

### 2.1 实现契约 vs 代码

| 实现契约声明 | 代码实际 | 状态 | 修复方案 |
|---|---|---|---|
| `/api/v1/performance/*` | ✅ 存在 | 一致 | — |
| `/api/v1/diagnosis/*` | ✅ 存在 | 一致 | — |
| `/api/v1/tuning/*` | ✅ 存在 | 一致 | — |
| `/api/v1/users/*` | ✅ 存在 | 一致 | — |
| `/api/v1/audit-logs/*` | ✅ 存在 | 一致 | — |
| `/api/v1/reports/*` | ✅ 存在 | 一致 | — |
| 不新增 `/api/v1/configs/metrics` | ❌ 代码存在 | **不一致** | 实现契约 v2.0 追认存在 |
| 不新增 `/api/v1/configs/diagnosis` | ❌ 代码存在 | **不一致** | 实现契约 v2.0 追认存在 |

### 2.2 代码额外有的 API 领域（实现契约未提及）

| API 领域 | prefix | 修复方案 |
|---|---|---|
| `/api/v1/auth/*` | auth | 实现契约 v2.0 补充 |
| `/api/v1/loops/*` | loops | 实现契约 v2.0 补充 |
| `/api/v1/tags/*` | tags | 实现契约 v2.0 补充 |
| `/api/v1/plant-nodes/*` | plant-nodes | 实现契约 v2.0 补充 |
| `/api/v1/dashboard/*` | dashboard | 实现契约 v2.0 补充 |
| `/api/v1/realtime/*` | realtime | 实现契约 v2.0 补充 |
| `/api/v1/ws/*` | WebSocket | 实现契约 v2.0 补充 |
| `/api/v1/aas/*` | aas | 实现契约 v2.0 补充 |
| `/api/v1/configs/*` | configs（含 metrics/diagnosis/weights/grading） | 实现契约 v2.0 追认 |
| `/api/v1/algorithms/*` | algorithms（含 kpi/diagnosis/tuning/dataplanner） | 实现契约 v2.0 补充 |
| `/api/v1/tasks/*` | tasks | 实现契约 v2.0 补充 |
| `/api/v1/performance/nodes/*` | node_performance | 实现契约 v2.0 补充 |
| `/api/v1/tracker/*` | tracker（在 diagnosis.py 内） | 实现契约 v2.0 补充 |
| `/api/v1/diagnosis/tags/*` | diagnosis-tags | 实现契约 v2.0 补充 |
| `/api/v1/timeseries/*` | timeseries（2 个 router） | 实现契约 v2.0 补充 |
| `/api/v1/health` | health | 实现契约 v2.0 补充 |

### 2.3 IDS vs 代码

| IDS 声明 | 代码实际 | 状态 | 修复方案 |
|---|---|---|---|
| `/api/v1/system/users` | `/api/v1/users` | **不一致** | IDS 改为 `/api/v1/users` |
| `/api/v1/tracker/*` 独立 | 在 diagnosis.py 内的 `/api/v1/tracker/*` | 部分一致 | IDS 标注为 diagnosis 子路由 |
| `/api/v1/configs/metrics` | 代码存在 | 一致 | — |
| `/api/v1/configs/diagnosis` | 代码存在 | 一致 | — |

---

## 3. 数据模型字段校验（Task 6）

### 3.1 DDS v4.1 vs 代码 ORM

| DDS 表名 | 代码 ORM 模型 | 状态 | 修复方案 |
|---|---|---|---|
| `loop_ledger` | `LoopLedger` | ✅ | — |
| `tag_registry` | `TagRegistry` | ✅ | — |
| `loop_tag_mapping` | `LoopTagMapping` | ✅ | — |
| `plant_node` | `PlantNode` | ✅ | — |
| `metric_config` | `MetricConfig` | ✅ | — |
| `kpi_snapshot_hourly` | `KpiSnapshotHourly` | ✅ | — |
| `kpi_snapshot_custom` | `KpiSnapshotCustom` | ✅ | — |
| `unit_kpi_summary` | `UnitKpiSummary` | ✅ | — |
| `clpm_metric_data_requirement` | `ClpmMetricDataRequirement` | ✅ | — |
| `action_tracker` | `ActionTracker` | ✅ | — |
| `tuning_record` | `TuningRecord` | ✅ | — |
| `report_record` | `ReportRecord` | ✅ | — |
| `sys_user` | `SysUser` | ✅ | — |
| `sys_audit_log` | `SysAuditLog` | ✅ | — |
| `report_schedule` | — | ❌ | DDS 补充 `report_config` 或代码补 `report_schedule` |
| `sys_role` | — | ❌ | DDS 删除或代码补 |
| `sys_user_role` | — | ❌ | DDS 删除或代码补 |

### 3.2 代码额外有的表（DDS 未列出）

| 代码 ORM 模型 | __tablename__ | 修复方案 |
|---|---|---|
| `KpiNodeSnapshotHourly` | `kpi_node_snapshot_hourly` | DDS 补充 |
| `KpiNodeSnapshotDaily` | `kpi_node_snapshot_daily` | DDS 补充 |
| `KpiNodeSnapshotMonthly` | `kpi_node_snapshot_monthly` | DDS 补充 |
| `EngineRule` | `engine_rule` | DDS 补充 |
| `DiagnosisConfig` | `diagnosis_config` | DDS 补充 |
| `DiagnosisResult` | `diagnosis_result` | DDS 补充 |
| `DiagnosisTag` | `diagnosis_tag` | DDS 补充 |
| `LoopModeMapping` | `loop_mode_mapping` | DDS 补充 |
| `LoopTypeWeight` | `loop_type_weight` | DDS 补充 |
| `LoopLevelWeight` | `loop_level_weight` | DDS 补充 |
| `SysConfig` | `sys_config` | DDS 补充 |
| `ReportConfig` | `report_config` | DDS 补充（替代 `report_schedule`） |

### 3.3 字段名不一致

| 对象 | DDS 字段名 | 代码字段名 | 状态 |
|---|---|---|---|
| TDengine 超级表 | `quality` | 需验证 | 待确认 |
| loop-level 平稳率 | `steady_rate` | 需验证 | 待确认 |
| unit-level 平稳率 | `stability_rate` | 需验证 | 待确认 |

---

## 4. 状态机校验（Task 7）

### 4.1 状态机枚举一致性

| 对象 | 实现契约 v1.0 | DDS v4.1 | UIUX v5.3 | PRD v4.0 | ADS v4.0 | IDS v4.0 | 代码 |
|---|---|---|---|---|---|---|---|
| Action Tracker | PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED | ✅ | ⚠️ 冲突 | ❌ RESOLVED | ❌ 缺枚举 | ❌ RESOLVED | 需验证 |
| Loop | READY/PARTIAL/INACTIVE | ✅ | ✅ | ❌ 缺失 | ❌ 缺失 | ❌ 误用 | 需验证 |
| KPI 快照 | SUCCESS/PARTIAL/INCONCLUSIVE | ✅ | ✅ | ❌ 缺失 | ❌ 缺失 | ❌ 混用 | 需验证 |
| Tuning | DRAFT/RUNNING/COMPLETED/ROLLED_BACK | ✅ | ✅ | ❌ 缺失 | ❌ 缺失 | ❌ PENDING/IN_PROGRESS/FAILED | 需验证 |
| PV Quality | GOOD/BAD/UNCERTAIN | ✅ | ✅ | ✅ | ✅ | ✅ | 需验证 |

### 4.2 修复方案

所有文档统一使用实现契约 v1.0 的枚举值：
- Action Tracker：`PENDING` → `IN_PROGRESS` → `IMPLEMENTED` / `IGNORED`
- Loop：`READY` / `PARTIAL` / `INACTIVE`
- KPI 快照：`SUCCESS` / `PARTIAL` / `INCONCLUSIVE`
- Tuning：`DRAFT` / `RUNNING` / `COMPLETED` / `ROLLED_BACK`
- PV Quality：`GOOD` / `BAD` / `UNCERTAIN`

---

## 5. 路由权限校验（Task 8）

### 5.1 前端路由权限字段

| 文档声明 | 代码实际 | 状态 |
|---|---|---|
| `meta.roles` | `meta.authority` | **不一致** |

**修复方案**：所有文档统一使用 `meta.authority`（代码实际字段名）

### 5.2 路由清单一致性

| 实现契约声明 | 代码实际 | 状态 |
|---|---|---|
| `/dashboard/workbench` | ✅ 存在 | 一致 |
| `/loop/manage` | ✅ 存在 | 一致 |
| `/loop/detail/:id` | ✅ 存在（hideInMenu） | 一致 |
| `/loop/monitor` | ✅ 存在 | 一致 |
| `/tag/list` | ✅ 存在 | 一致 |
| `/metric/dashboard` | ✅ 存在 | 一致 |
| `/metric/ranking` | ✅ 存在 | 一致 |
| `/metric/statistics` | ✅ 存在 | 一致 |
| `/metric/snapshots` | ✅ 存在 | 一致 |
| `/metric/recompute` | ✅ 存在 | 一致 |
| `/metric/config` | ✅ 存在 | 一致 |
| `/metric/weight-config` | ✅ 存在 | 一致 |
| `/metric/engine-config` | ✅ 存在 | 一致 |
| `/metric/task-strategy` | ✅ 存在 | 一致 |
| `/metric/tasks` | ✅ 存在 | 一致 |
| `/diagnosis/list` | ✅ 存在 | 一致 |
| `/diagnosis/detail/:loopId` | ✅ 存在（hideInMenu） | 一致 |
| `/diagnosis/waveform` | ✅ 存在 | 一致 |
| `/diagnosis/tracker` | ✅ 存在 | 一致 |
| `/diagnosis/ab-compare` | ✅ 存在（hideInMenu） | 一致 |
| `/diagnosis/statistics` | ✅ 存在 | 一致 |
| `/diagnosis/config` | ✅ 存在 | 一致 |
| `/tuning/workbench` | ✅ 存在 | 一致 |
| `/tuning/model` | ✅ 存在 | 一致 |
| `/tuning/algorithm` | ✅ 存在 | 一致 |
| `/tuning/simulation` | ✅ 存在 | 一致 |
| `/tuning/stats` | ✅ 存在 | 一致 |
| `/system/users` | ✅ 存在 | 一致 |
| `/system/audit` | ✅ 存在 | 一致 |
| `/system/permissions` | ✅ 存在 | 一致 |
| `/system/reports` | ✅ 存在 | 一致 |

### 5.3 代码额外有的路由

| 路由 path | 模块 | 说明 |
|---|---|---|
| `/dashboard` | dashboard | 重定向到 `/dashboard/workbench` |
| `/loop/factory` | loop | 已废弃，重定向 |
| `/loop/ledger` | loop | 已废弃，重定向 |
| `/metric` | metric | 重定向到 `/metric/dashboard` |
| `/metric/config-group` | metric | 重定向到 `/metric/config` |
| `/task` | task | 重定向到 `/metric/tasks` |
| `/task/detail/:taskId` | task | 隐藏详情页 |
| `/profile` | vben | 个人资料（隐藏） |

---

## 6. 反向校验（Task 9）

### 6.1 代码功能 vs 文档描述

| 代码功能 | FDS v5.1 | UIUX v5.3 | 实现契约 v1.0 | 状态 |
|---|---|---|---|---|
| 12 个 MetricCalculator | ✅ 3+1+8 | — | ✅ 6+2 | 一致 |
| 4 类权重模板 | ✅ STABLE/SLOW/FAST/LOGIC | — | — | FDS 一致 |
| 5 级性能定级 | ✅ EXCELLENT/GOOD/FAIR/WARNING/POOR | — | — | FDS 一致 |
| A/B/C/D/E 五级可信度 | ✅ | — | — | FDS 一致 |
| DataPlanner | ✅ | — | ✅ | 一致 |
| ConfidenceEvaluator | ✅ | — | ✅ | 一致 |
| TaskTracker | ✅ | — | ✅ | 一致 |
| 预处理 Pipeline | ✅ `app/services/preprocessing/` | — | — | FDS 一致 |
| 历史重算 | ✅ | ✅ `/metric/recompute` | ✅ | 一致 |
| Tag 管理独立入口 | ✅ | ✅ `/tag/list` | ✅ | 一致 |
| 节点级 KPI（3 张表） | — | — | — | **代码有，文档无** |

### 6.2 代码有但文档缺失的功能

| 代码功能 | 缺失文档 | 修复方案 |
|---|---|---|
| 节点级 KPI 快照（3 张表） | FDS/DDS/ADS | 补充到 v6.0 |
| WebSocket 实时推送 `/api/v1/ws/*` | 实现契约/IDS | 补充到 v6.0 |
| 算法独立调用 API `/api/v1/algorithms/*` | 实现契约/IDS | 补充到 v6.0 |
| 数据计划 API `/api/v1/algorithms/dataplanner/*` | 实现契约/IDS | 补充到 v6.0 |
| 诊断标签管理 `/api/v1/diagnosis/tags/*` | 实现契约/IDS | 补充到 v6.0 |
| 时间序列 API `/api/v1/timeseries/*` | 实现契约/IDS | 补充到 v6.0 |

---

## 7. 校验结果汇总

### 7.1 阻断性问题（必须在 v6.0 中修复）

1. **状态机枚举**：PRD/IDS 用 `RESOLVED`，应改为 `IMPLEMENTED`
2. **角色枚举**：IDS 用 `EXECUTOR/COLLABORATOR/VIEWER`，应改为 `IC_ENGINEER/PE_ENGINEER/SPONSOR`
3. **API 路径**：IDS 用 `/api/v1/system/users`，应改为 `/api/v1/users`
4. **权限字段名**：文档用 `meta.roles`，代码用 `meta.authority`
5. **实现契约 vs 代码**：实现契约禁止 `/api/v1/configs/metrics`，但代码存在

### 7.2 数据契约问题

1. **DDS 表数量**：DDS 17 张 vs 代码 26 张
2. **数据血缘字段**：ADS 8 字段 vs DDS 5+JSONB
3. **scoreWeights 结构**：IDS 旧 6 KPI vs 代码 3 核心+R
4. **可信度 D 级阈值**：PRD 40%~60% vs FDS 20%~60%

### 7.3 文档完整性问题

1. **节点级 KPI**：代码有 3 张表，FDS/DDS/ADS 未提及
2. **WebSocket/算法/数据计划 API**：代码有，实现契约/IDS 未提及
3. **性能定级/权重模板**：FDS 有，PRD/ADS/IDS 缺失

### 7.4 文档治理问题

1. **版本号引用**：24 处过期引用
2. **模块数量**：README 说 7 模块，应改为 6+1
3. **术语不一致**：快速率 vs 快速响应率等

---

## 8. v6.0 升级执行计划

基于以上校验结果，v6.0 升级按以下顺序执行：

### 第 1 步：核心文档升级（PRD/ADS/IDS）
- 修复所有 P0 阻断性问题
- 补全数据契约
- 补全功能描述

### 第 2 步：派生文档升级（实现契约/DESIGN/FDS/DDS/UIUX）
- 实现契约 v1.0 → v2.0：追认代码实际 API/表
- DDS v4.1 → v6.0：补全 9 张代码特有表
- FDS v5.1 → v6.0：补全节点级 KPI
- UIUX v5.3 → v6.0：修复 Action Tracker 状态枚举冲突
- DESIGN.md v2.1 → v3.0：对齐 UIUX v6.0

### 第 3 步：项目文档更新（AGENTS/CLAUDE/README）
- 更新所有版本号引用
- 更新测试数（1762）
- 更新分支名（main）
- 更新 TS 错误数（0）
- 更新模块数量（6+1）

### 第 4 步：质量审核
- 后端测试 + lint
- 前端 type check + build
- 最终引用校验
- PR
