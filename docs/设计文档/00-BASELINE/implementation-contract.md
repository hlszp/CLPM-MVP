# CLPM 重构后实现契约

**文档状态**：active-baseline  
**当前版本**：v1.0  
**发布日期**：2026-06-25  
**适用范围**：重构后 CLPM V1.0 / Phase 1 代码与设计文档对齐

## 1. 定位

本文件记录 2026-06 重构后的真实信息架构、路由、API、权限、状态机与阶段口径。后续 PRD、UI/UX、DESIGN、README、测试与代码评审均以本文件作为实现契约入口。

本文件不是推翻 PRD/UI/UX，而是把重构后的设计意图固化为新的派生基线：

- 保留重构后的主要信息架构与聚合页面。
- 文档追认当前代码中的产品化组织方式。
- 旧设计文档中与本契约冲突的页面路径、页面数量、阶段表述，以本契约为准。
- 算法、安全、审计、权限等业务边界仍以 PRD 为上位约束。

## 2. 信息架构契约

CLPM 当前采用 **6 模块 + 1 门户**，但页面组织已从旧版 25 页面清单调整为"聚合工作台 + 隐藏详情页 + 专项配置页"。

| 模块 | 当前设计意图 | 当前主要路由 |
|---|---|---|
| 工作台门户 | 全角色入口，聚合 KPI、低效回路、待处理异常与趋势 | `/dashboard/workbench` |
| 回路管理 | 用一个聚合页承载工厂树、回路台账、Tag 关联、评估参数、投用定义 | `/loop/manage`、`/loop/detail/:id`、`/loop/monitor`、`/tag/list` |
| 性能评估 | 保留重构后的 metric 命名，承载 KPI 看板、排行、统计、5 Tab 配置组 | `/metric/dashboard`、`/metric/ranking`、`/metric/statistics`、`/metric/config`、`/metric/weight-config`、`/metric/engine-config`、`/metric/task-strategy`、`/metric/tasks` |
| 诊断中心 | 诊断列表、详情、波形、异常跟踪、A/B 对比、统计、配置分离 | `/diagnosis/list`、`/diagnosis/detail/:loopId`、`/diagnosis/waveform`、`/diagnosis/tracker`、`/diagnosis/ab-compare`、`/diagnosis/statistics`、`/diagnosis/config` |
| 回路整定 | Phase 1 保留实验/辅助能力入口，承载工作台、模型、算法、仿真、统计 | `/tuning/workbench`、`/tuning/model`、`/tuning/algorithm`、`/tuning/simulation`、`/tuning/stats` |
| 系统管理 | 用户、审计、权限矩阵、报表配置 | `/system/users`、`/system/audit`、`/system/permissions`、`/system/reports` |

## 3. 路由命名决策

| 决策点 | 当前决策 | 说明 |
|---|---|---|
| 首页 | 使用 `/dashboard/workbench` | `/` 可作为部署层默认入口，但产品路由以工作台路由为准。 |
| 性能评估 | 保留 `/metric/*` | 不再强制回退到旧 UI/UX 的 `/performance/*`。如需兼容，可后续增加 redirect，不在菜单暴露。 |
| 指标配置 Tab 聚合 | `/metric/config` + `/metric/weight-config` + `/metric/engine-config` + `/metric/task-strategy` + `/metric/tasks` | P2 #31 B8 修正：原契约列 `/metric/type-weight` / `/metric/level-weight` 已被 UI/UX 改造方案 v1.0 §6.1.4 合并为 `/metric/weight-config` 单 Tab，内含"类型权重 + 级别权重"两个子 Tab（子组件 `type-weight.vue` / `level-weight.vue` 作为 `weight-config.vue` 的内容组件，非孤儿视图）。 |
| 回路管理 | 保留 `/loop/manage` 聚合页 | `/loop/factory`、`/loop/ledger` 视为旧设计路径，不再作为主菜单页。 |
| Tag 管理 | 使用 `/tag/list` | AAS Tag 是独立资源入口，不强行塞回 `/loop/mapping`。 |
| 诊断中心 | 以实际 7 页面为准 | waveform、ab-compare、config 是重构后显式页面。 |
| 系统安全说明 | 暂并入权限/审计/README | 是否新增 `/system/safety` 另行评审。 |

## 4. API 契约

| 领域 | 当前实现路径 | 说明 |
|---|---|---|
| 性能配置与看板 | `/api/v1/performance/*` | 当前不新增 `/api/v1/configs/metrics` 聚合接口。 |
| 诊断配置与跟踪 | `/api/v1/diagnosis/*` | 当前不新增 `/api/v1/configs/diagnosis` 聚合接口。 |
| 整定算法 | `/api/v1/tuning/*` | 当前作为 Phase 1 实验/辅助能力，不代表自动下写 DCS。 |
| 用户管理 | `/api/v1/users/*` | 不强制改为 `/api/v1/system/users`。 |
| 审计日志 | `/api/v1/audit-logs/*` | 系统管理 UI 可消费该路径。 |
| 报表管理 | `/api/v1/reports/*` | 系统管理 UI 可消费该路径。 |

## 5. 权限契约

| 角色 | 设计口径 |
|---|---|
| ADMIN | 全模块、全配置、全审计。 |
| IC_ENGINEER | 业务模块全流程，可编辑异常跟踪和回路配置。 |
| PE_ENGINEER | 可查看评估、监控、诊断汇总；可参与异常跟踪。 |
| EXPERT | 可查看诊断与整定相关页面，可参与异常跟踪和专家建议。 |
| SPONSOR | 只看工作台、性能汇总、诊断统计等汇总视图；不可进入单回路诊断详情、波形证据或异常跟踪编辑。 |

## 6. 状态机契约

| 对象 | 标准枚举 | 中文显示 |
|---|---|---|
| Action Tracker | `PENDING` → `IN_PROGRESS` → `IMPLEMENTED` / `IGNORED` | 待处理、处理中、已实施、已忽略 |
| KPI 快照 | `SUCCESS` / `PARTIAL` / `INCONCLUSIVE` | 成功、部分有效、数据不足 |
| Loop | `READY` / `PARTIAL` / `INACTIVE` | 就绪（配置完整可参与 KPI 计算）、部分配置（缺必需 Tag，不参与计算）、已停用（软删除，is_active=False） |
| PV Quality | `GOOD` / `BAD` / `UNCERTAIN` | 好值、坏值、不确定 |
| Tuning | `DRAFT` / `RUNNING` / `COMPLETED` / `ROLLED_BACK` | 草稿、运行中、已完成、已回退 |

历史文档中的 `RESOLVED` 统一视为旧命名；当前代码与后续文档使用 `IMPLEMENTED`。

P1 #13 修正：历史文档中的 `ACTIVE`/`PAUSED`/`DECOMMISSIONED`（运行/暂停/退役）统一视为旧命名；当前代码与后续文档使用 `READY`/`PARTIAL`/`INACTIVE`（就绪/部分配置/已停用）。代码中的状态反映"配置完整性 + 删除状态"，而非"运行状态"：`READY` = 配置完整可参与 KPI 计算；`PARTIAL` = 缺必需 Tag，不参与计算；`INACTIVE` = 软删除（is_active=False）。

## 7. KPI 契约

| 类型 | 指标 |
|---|---|
| 6 大核心 KPI | 好值率、自控率、平稳率、准确率、振荡率、饱和率 |
| 扩展派生指标 | 有效自控率、快速响应率 |

说明：PRD 对外合规口径仍强调 6 大核心 KPI；实现可保留 2 个扩展派生指标用于算法增强、排序与内部诊断，但 UI/报表需明确区分"核心 KPI"与"扩展指标"。

## 8. 阶段契约

| 能力 | Phase 1 口径 |
|---|---|
| 自动评估 | 正式能力 |
| 自动诊断 | 正式能力 |
| Action Tracker | 正式能力 |
| 回路整定页面 | 正式入口，Phase 1 可演示 |
| 整定辨识/推荐/仿真接口 | 实验/辅助能力，只输出建议、证据、风险和回退方案 |
| DCS 参数下写 | 明确不支持 |

## 9. 文档修订规则

- README、CLAUDE、DESIGN、UI/UX 后续修订应引用本契约。
- 旧路径可记录为历史兼容路径，但不作为主菜单验收项。
- 新增页面必须先更新本契约，再更新路由、权限、测试与 UI/UX 页面清单。
