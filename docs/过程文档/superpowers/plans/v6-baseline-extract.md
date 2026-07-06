# v6.0 文档统一升级 — 基准信息提取

**提取日期**：2026-07-06
**执行分支**：`mb/doc-v6`
**目的**：作为后续所有文档升级的"事实来源"

## 基准文档清单

| # | 文档 | 实际版本 | 发布日期 | 详细提取文件 |
|---|---|---|---|---|
| 1 | FDS（功能设计规范） | v5.1 | 2026-07-04 | `v6-baseline-fds.md` |
| 2 | UIUX（UI/UX 设计规范） | v5.3 | 2026-07-04 | `v6-baseline-uiux.md` |
| 3 | DDS（数据模型设计） | v4.1 | 2026-07-04 | `v6-baseline-dds.md` |
| 4 | 实现契约 | v1.0 | 2026-06-25 | 见下方 §4 |

---

## 1. FDS v5.1 基准摘要

**详细内容见**：`v6-baseline-fds.md`

### 1.1 版本号
- 实际版本：**v5.1（DDS 对齐版）**
- 发布日期：**2026-07-04**
- 注：AGENTS.md 基线表中标注 FDS 为 v3.0（待追认），已严重过期

### 1.2 模块清单（关键发现）
- FDS §5 实际为 **5 个业务模块 + 1 个门户**（工作台门户 + 回路管理/性能评估/诊断中心/回路整定/系统管理）
- FDS 变更记录 v3.0 描述为"6 模块 + 1 门户"，AGENTS.md 也沿用此说法，但 §5 章节结构实为 5+1
- 任务管理（§5.3.11）非独立模块，是性能评估子节

### 1.3 KPI 指标体系
- **3+1+8 体系**：3 核心质量指标（准确率/快速响应率/平稳率）+ 1 折扣因子（有效自控率 R）+ 8 扩展指标
- 12 项指标计算器
- 装置级三大 KPI（自控率/好值率/平稳率）
- 5 级性能定级（EXCELLENT/GOOD/FAIR/WARNING/POOR）
- 4 类权重模板（STABLE/SLOW/FAST/LOGIC）
- A/B/C/D/E 五级可信度（valid_rate 阈值 95/80/60/20%）

### 1.4 关键设计决策
- AAS 模型：同步 tag 位号，回路由用户创建并关联 7 个 OPC tag
- DataPlanner：统一历史数据读取，按控制类型自动降采样
- KEEP_ALL_WITH_VALIDITY：保留所有点 + 质量码
- 3+1+8 体系：核心+扩展指标分离
- 两类评分：装置级 KPI 看板 + 回路级综合评分
- Action Tracker 降级：诊断中心子模块

### 1.5 引用文档
- PRD v3.1、DDS v4.1、关键算法设计说明 v2.0、CLPM 后端研发指导文档、GB/T 44693.2-2024

---

## 2. UIUX v5.3 基准摘要

**详细内容见**：`v6-baseline-uiux.md`

### 2.1 版本号
- 实际版本：**v5.3**
- 发布日期：**2026-07-04**
- 注：AGENTS.md 标注为 v5.1，DESIGN.md 标注为 v4.1，均已过期

### 2.2 IA 信息架构
- **6 模块 + 1 门户**（与实现契约一致）
- v5.3 新增 4 个菜单项（回路管理 1 个 + 性能评估 3 个）
- 完整菜单树见 `v6-baseline-uiux.md` §2

### 2.3 路由清单
- 32 个路由 path
- 路由 name、component 路径、详细 meta 以 `implementation-contract.md` §2-§3 为准

### 2.4 页面清单
- 25+ 页面，按 8 个模块分组
- 工作台门户 1 页 / 回路管理 4 页 / 性能评估 9 页 / 诊断中心 7 页 / 回路整定 5 页 / 系统管理 4 页 / Tag 管理 1 页 / 任务管理 1 页

### 2.5 组件清单
- 15 个 §7 核心业务组件
- 3 个 §6.7 算法配置组件
- 共享组件目录：`frontend/apps/web-antd/src/components/clpm/`

### 2.6 角色权限矩阵
- 5 角色（ADMIN/IC_ENGINEER/PE_ENGINEER/EXPERT/SPONSOR）
- 14 项操作权限
- 任务管理权限详见 `v6-baseline-uiux.md` §6

### 2.7 状态机定义
- 12 类状态机对象
- **关键发现**：§7.2.2 与 §8.2.3 关于 Action Tracker 状态枚举存在冲突（PENDING/IN_PROGRESS/IGNORED/IMPLEMENTED vs ACTIVE/RESOLVED/SUPPRESSED）

### 2.8 设计 Tokens
- 颜色/字体/字阶/间距/圆角/阴影/动效 7 类
- 详见 `v6-baseline-uiux.md` §8

### 2.9 验收标准
- 6 类验收项
- 详见 `v6-baseline-uiux.md` §13

---

## 3. DDS v4.1 基准摘要

**详细内容见**：`v6-baseline-dds.md`

### 3.1 版本号
- 实际版本：**v4.1**
- 发布日期：**2026-07-04**
- 变更历史：v3.0 → v3.1 → v4.0 → v4.1
- 注：AGENTS.md 标注为 v3.0（待追认），已严重过期

### 3.2 PostgreSQL 表清单
- **17 张表**，每张表含完整字段表、主键、外键、约束
- 详见 `v6-baseline-dds.md` §2

| 表名 | 用途 |
|---|---|
| `loop_ledger` | 回路台账 |
| `tag_registry` | AAS Tag 注册表 |
| `loop_tag_mapping` | 回路-Tag 关联 |
| `plant_node` | 工厂层级 |
| `metric_config` | 指标配置 |
| `kpi_snapshot_hourly` | KPI 小时快照 |
| `kpi_snapshot_custom` | KPI 自定义快照 |
| `unit_kpi_summary` | 装置级 KPI 汇总 |
| `clpm_metric_data_requirement` | 指标数据需求 |
| `action_tracker` | 异常跟踪 |
| `tuning_record` | 整定记录 |
| `report_record` | 报表记录 |
| `sys_user` | 系统用户 |
| `sys_role` | 系统角色 |
| `sys_user_role` | 用户-角色关联 |
| `sys_audit_log` | 审计日志 |
| `report_schedule` | 报表计划 |

### 3.3 TDengine 超级表
- 1 个超级表 `st_loop_data`
- 2 个 Tag 列（loop_id, plant_node_id）
- 9 个 Field 列（ts, pv, sp, op, mode, quality, pid_p, pid_i, pid_d）
- 子表命名规则：`loop_{loop_id}`

### 3.4 枚举值定义
- **27 类枚举**，含中文显示名与业务含义
- 详见 `v6-baseline-dds.md` §4

### 3.5 数据血缘字段
- DDS v4.1 §5.1 明确指出 `kpi_snapshot_hourly` 与 `kpi_snapshot_custom` 均包含 **5 个** 数据血缘字段
- 字段：`sampling_freq`、`quality_policy`、`valid_rate`、`confidence_level`、`data_lineage`
- 其中 `data_lineage`（JSONB）内部包含 6 个子字段
- **重要修正**：原计划提到的"8 字段"实际不存在，文档明确为 5 字段

### 3.6 v4.1 主要新增字段
- `loop_ledger` 新增 `control_type` / `importance_level` / `include_in_evaluation`
- `metric_config` 新增 `grading_thresholds`（5 级性能定级）
- `unit_kpi_summary` 新增 `excluded_loops` / `status`
- `kpi_snapshot_custom.stability_rate` 修正为 `steady_rate`

### 3.7 关键术语统一
- loop-level 字段名：`steady_rate`
- unit-level 聚合字段名：`stability_rate`

### 3.8 引用文档
- PRD v3.1、FDS v5.1、ADS v3.1、关键算法设计说明 v2.0、GB/T 44693.2-2024

---

## 4. 实现契约 v1.0 基准摘要

**文档路径**：`docs/设计文档/00-BASELINE/implementation-contract.md`
**版本**：v1.0
**发布日期**：2026-06-25

### 4.1 定位
- 记录 2026-06 重构后的真实信息架构、路由、API、权限、状态机与阶段口径
- 后续 PRD、UI/UX、DESIGN、README、测试与代码评审均以本文件作为实现契约入口
- 旧设计文档中与本契约冲突的页面路径、页面数量、阶段表述，以本契约为准
- 算法、安全、审计、权限等业务边界仍以 PRD 为上位约束

### 4.2 信息架构契约
- **6 模块 + 1 门户**（与 UIUX v5.3 一致）
- 页面组织已从旧版 25 页面清单调整为"聚合工作台 + 隐藏详情页 + 专项配置页"

| 模块 | 主要路由 |
|---|---|
| 工作台门户 | `/dashboard/workbench` |
| 回路管理 | `/loop/manage`、`/loop/detail/:id`、`/loop/monitor`、`/tag/list` |
| 性能评估 | `/metric/dashboard`、`/metric/ranking`、`/metric/statistics`、`/metric/snapshots`、`/metric/recompute`、`/metric/config`、`/metric/weight-config`、`/metric/engine-config`、`/metric/task-strategy`、`/metric/tasks` |
| 诊断中心 | `/diagnosis/list`、`/diagnosis/detail/:loopId`、`/diagnosis/waveform`、`/diagnosis/tracker`、`/diagnosis/ab-compare`、`/diagnosis/statistics`、`/diagnosis/config` |
| 回路整定 | `/tuning/workbench`、`/tuning/model`、`/tuning/algorithm`、`/tuning/simulation`、`/tuning/stats` |
| 系统管理 | `/system/users`、`/system/audit`、`/system/permissions`、`/system/reports` |

### 4.3 路由命名决策
- 首页：`/dashboard/workbench`
- 性能评估：保留 `/metric/*`（不强制回退到 `/performance/*`）
- 指标配置 Tab 聚合：`/metric/config` + `/metric/weight-config` + `/metric/engine-config` + `/metric/task-strategy` + `/metric/tasks`
- 回路管理：保留 `/loop/manage` 聚合页
- Tag 管理：使用 `/tag/list`
- 诊断中心：以实际 7 页面为准
- 系统安全说明：暂并入权限/审计/README

### 4.4 API 契约

| 领域 | 路径 | 说明 |
|---|---|---|
| 性能配置与看板 | `/api/v1/performance/*` | 不新增 `/api/v1/configs/metrics` 聚合接口 |
| 诊断配置与跟踪 | `/api/v1/diagnosis/*` | 不新增 `/api/v1/configs/diagnosis` 聚合接口 |
| 整定算法 | `/api/v1/tuning/*` | Phase 1 实验/辅助能力，不代表自动下写 DCS |
| 用户管理 | `/api/v1/users/*` | 不强制改为 `/api/v1/system/users` |
| 审计日志 | `/api/v1/audit-logs/*` | 系统管理 UI 可消费该路径 |
| 报表管理 | `/api/v1/reports/*` | 系统管理 UI 可消费该路径 |

### 4.5 权限契约

| 角色 | 设计口径 |
|---|---|
| ADMIN | 全模块、全配置、全审计 |
| IC_ENGINEER | 业务模块全流程，可编辑异常跟踪和回路配置 |
| PE_ENGINEER | 可查看评估、监控、诊断汇总；可参与异常跟踪 |
| EXPERT | 可查看诊断与整定相关页面，可参与异常跟踪和专家建议 |
| SPONSOR | 只看工作台、性能汇总、诊断统计等汇总视图；不可进入单回路诊断详情、波形证据或异常跟踪编辑 |

### 4.6 状态机契约

| 对象 | 标准枚举 | 中文显示 |
|---|---|---|
| Action Tracker | `PENDING` → `IN_PROGRESS` → `IMPLEMENTED` / `IGNORED` | 待处理、处理中、已实施、已忽略 |
| KPI 快照 | `SUCCESS` / `PARTIAL` / `INCONCLUSIVE` | 成功、部分有效、数据不足 |
| Loop | `READY` / `PARTIAL` / `INACTIVE` | 就绪、部分配置、已停用 |
| PV Quality | `GOOD` / `BAD` / `UNCERTAIN` | 好值、坏值、不确定 |
| Tuning | `DRAFT` / `RUNNING` / `COMPLETED` / `ROLLED_BACK` | 草稿、运行中、已完成、已回退 |

**关键修正**：
- 历史文档中的 `RESOLVED` 统一视为旧命名；当前代码与后续文档使用 `IMPLEMENTED`
- 历史文档中的 `ACTIVE`/`PAUSED`/`DECOMMISSIONED` 统一视为旧命名；当前使用 `READY`/`PARTIAL`/`INACTIVE`

### 4.7 KPI 契约

| 类型 | 指标 |
|---|---|
| 6 大核心 KPI | 好值率、自控率、平稳率、准确率、振荡率、饱和率 |
| 扩展派生指标 | 有效自控率、快速响应率 |

说明：PRD 对外合规口径仍强调 6 大核心 KPI；实现可保留 2 个扩展派生指标用于算法增强、排序与内部诊断，但 UI/报表需明确区分"核心 KPI"与"扩展指标"。

### 4.8 阶段契约

| 能力 | Phase 1 口径 |
|---|---|
| 自动评估 | 正式能力 |
| 自动诊断 | 正式能力 |
| Action Tracker | 正式能力 |
| 回路整定页面 | 正式入口，Phase 1 可演示 |
| 整定辨识/推荐/仿真接口 | 实验/辅助能力，只输出建议、证据、风险和回退方案 |
| DCS 参数下写 | 明确不支持 |

### 4.9 文档修订规则
- README、CLAUDE、DESIGN、UI/UX 后续修订应引用本契约
- 旧路径可记录为历史兼容路径，但不作为主菜单验收项
- 新增页面必须先更新本契约，再更新路由、权限、测试与 UI/UX 页面清单

---

## 5. 跨文档一致性关键发现

### 5.1 版本号不一致汇总

| 文档 | AGENTS.md 声明 | 实际版本 | 差距 |
|---|---|---|---|
| PRD | v3.1 | v4.0 (2026-06-25) | 落后 1 个大版本 |
| FDS | v3.0（待追认） | v5.1 (2026-07-04) | 落后 2 个大版本 |
| ADS | v3.0（待校准） | v4.0 (2026-06-26) | 落后 1 个大版本 |
| DDS | v3.0（待追认） | v4.1 (2026-07-04) | 落后 1 个大版本 |
| IDS | v3.0（待追认） | v4.0 (2026-06-26) | 落后 1 个大版本 |
| UIUX | v5.1 | v5.3 (2026-07-04) | 落后 1 个小版本 |
| 实现契约 | v1.0 | v1.0 ✅ | 一致 |
| DESIGN.md | v2.1（对齐 UIUX v4.1） | 实际 UIUX 已是 v5.3 | 落后 2 个大版本 |

### 5.2 模块数量口径不一致

| 来源 | 模块数量 | 说明 |
|---|---|---|
| AGENTS.md | 6 模块 + 1 门户 | 当前口径 |
| 实现契约 v1.0 | 6 模块 + 1 门户 | 当前口径 |
| UIUX v5.3 | 6 模块 + 1 门户 | 与实现契约一致 |
| FDS v5.1 §5 | 5 业务模块 + 1 门户 | §5 章节结构实为 5+1，任务管理归入性能评估子节 |
| README.md | 7 模块+门户（含任务管理） | 与实现契约不一致 |

**v6.0 统一口径**：以实现契约 v1.0 为准，**6 模块 + 1 门户**，任务管理作为性能评估子模块

### 5.3 状态机枚举不一致

| 对象 | 实现契约 v1.0 | DDS v4.1 | UIUX v5.3 |
|---|---|---|---|
| Loop | READY/PARTIAL/INACTIVE | READY/PARTIAL/INACTIVE | READY/PARTIAL/INACTIVE ✅ |
| Action Tracker | PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED | PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED | **存在冲突**：§7.2.2 vs §8.2.3 |
| KPI 快照 | SUCCESS/PARTIAL/INCONCLUSIVE | SUCCESS/PARTIAL/INCONCLUSIVE | SUCCESS/PARTIAL/INCONCLUSIVE ✅ |
| Tuning | DRAFT/RUNNING/COMPLETED/ROLLED_BACK | DRAFT/RUNNING/COMPLETED/ROLLED_BACK | DRAFT/RUNNING/COMPLETED/ROLLED_BACK ✅ |

### 5.4 KPI 指标口径不一致

| 来源 | 口径 |
|---|---|
| 实现契约 v1.0 | 6 大核心 KPI + 2 扩展派生指标 |
| FDS v5.1 | 3+1+8 体系（3 核心+1 折扣+8 扩展）= 12 项指标计算器 |
| DDS v4.1 | 与 FDS 一致 |

**v6.0 统一口径**：以 FDS v5.1 的 3+1+8 体系为准，对外口径仍可强调 6 大核心 KPI

### 5.5 数据血缘字段数量修正

| 来源 | 口径 |
|---|---|
| 原计划预期 | 8 字段 |
| DDS v4.1 实际 | 5 独立字段 + `data_lineage` JSONB 内 6 子字段 |

**v6.0 统一口径**：以 DDS v4.1 为准，5 字段 + JSONB 子字段

---

## 6. v6.0 文档升级优先级

基于以上分析，v6.0 文档升级的优先级排序：

1. **PRD v4.0 → v6.0**（差距最大，需对齐 FDS v5.1 的 3+1+8 体系、DDS v4.1 的表/字段、UIUX v5.3 的页面清单）
2. **ADS v4.0 → v6.0**（需对齐 DDS v4.1 的数据模型、实现契约 v1.0 的 API/路由）
3. **IDS v4.0 → v6.0**（需对齐实现契约 v1.0 的 API 路径、DDS v4.1 的 Schema）
4. **实现契约 v1.0 → v2.0**（需追认 v5.1/v5.3/v4.1 的最新变化）
5. **DESIGN.md v2.1 → v3.0**（需对齐 UIUX v5.3）
6. **FDS v5.1 → v6.0**（版本号统一，内容微调）
7. **DDS v4.1 → v6.0**（版本号统一，内容微调）
8. **UIUX v5.3 → v6.0**（版本号统一，修复 Action Tracker 状态枚举冲突）
9. **AGENTS.md / CLAUDE.md / README.md**（版本号、测试数、分支名等全部更新）

---

## 7. 引用关系图

```
PRD (上位约束)
  ↓
FDS v5.1 ←→ DDS v4.1 ←→ UIUX v5.3
  ↓             ↓             ↓
  └── 实现契约 v1.0 ──┘
          ↓
     DESIGN.md v2.1
          ↓
  AGENTS.md / CLAUDE.md / README.md
```

**v6.0 升级后的引用关系**：

```
PRD v6.0 (上位约束)
  ↓
FDS v6.0 ←→ DDS v6.0 ←→ UIUX v6.0
  ↓             ↓             ↓
  └── 实现契约 v2.0 ──┘
          ↓
     DESIGN.md v3.0
          ↓
  AGENTS.md / CLAUDE.md / README.md
```
