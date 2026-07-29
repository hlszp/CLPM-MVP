# CLPM 重构后实现契约

**文档状态**：active-baseline  
**当前版本**：v2.2
**发布日期**：2026-07-28
**适用范围**：重构后 CLPM V1.0 / Phase 1 代码与设计文档对齐  
**v2.0 修订摘要**：按当前代码重校前端 IA、API、31 张 ORM 表、诊断双状态机与缓存接入状态；D5 口径统一后全库引用为 v2.0（历史 v2.1 摘要并入本版）
**v2.1 修订摘要**：同步诊断中心 Batch 4-6 交付成果——A/B 对比已实现（含 `includeDiagnosis` 扩展）；登记 `GET /diagnosis/algorithms/meta`、`GET /diagnosis/statistics/export`、`GET /tracker/effectiveness`、`GET|PATCH /tracker/verification-config`；补全 Tracker 子路由清单；更新诊断中心路由决策（A/B 对比不再返回 501）；登记诊断任务自动归档机制与 D1-D6 功能扩展
**v2.2 修订摘要**：同步 2026-07-28 全维度优化整改（Phase 0-5）——登记 `GET /performance/grade-distribution` 与 `/loops/snapshots?grade=`；权限码服务端落地（`require_perms`，loop/tuning/diagnosis 读端点收口）替代"待统一"标注；登记首次登录强制改密（`must_change_password`）；前端路由收紧（reports/aas-sync 仅 ADMIN、EXPERT→/diagnosis、SPONSOR→/metric）。整改全貌见 `docs/过程文档/clpm-optimization-review-plan-2026-07-28.md`
**v2.2 增补（2026-07-29）**：登记 `/diagnosis/tasks?includeArchived=`；诊断任务时间戳默认值统一 UTC（迁移 `h8b9c0d1e2f3`）；refresh 轮换幂等窗口（`refresh_rotated`，120s）；整定 Phase 2.1 合并（`tuning_identification` 算法栈 + 异步辨识任务）

## 1. 定位

本文件记录 2026-06 重构后的真实信息架构、路由、API、权限、状态机与阶段口径。后续 PRD、UI/UX、DESIGN、README、测试与代码评审均以本文件作为实现契约入口。

版本分层：产品文档使用 v6.1 表示需求与设计基线；后端 `APP_VERSION`（当前默认 `1.0.0`）用于运行时 API 元数据；Git tag 用于发布追踪。三者职责不同，发布时分别维护，不以数值相等作为一致性条件。

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
| 回路管理 | 链路配置、测点配置、回路配置、监控、历史数据管理与隐藏详情页 | `/loop/aas-sync`、`/tag/list`、`/loop/manage`、`/loop/monitor`、`/loop/data`、`/loop/detail/:id` |
| 性能评估 | 装置性能、回路性能、评估任务、聚合指标配置与 KPI 报表 | `/metric/pid-dashboard`、`/metric/loop-performance`、`/metric/tasks`、`/metric/config`、`/metric/kpi-report` |
| 诊断中心 | 总览、任务、归档记录、可视化、异常跟踪、配置与隐藏详情页 | `/diagnosis/overview`、`/diagnosis/tasks`、`/diagnosis/records`、`/diagnosis/visualization`、`/diagnosis/tracker`、`/diagnosis/config`、`/diagnosis/detail/:loopId` |
| 回路整定 | Phase 1 保留实验/辅助能力入口，承载工作台、模型、算法、仿真、统计 | `/tuning/workbench`、`/tuning/model`、`/tuning/algorithm`、`/tuning/simulation`、`/tuning/stats` |
| 系统管理 | 用户、审计、权限矩阵、报表配置 | `/system/users`、`/system/audit`、`/system/permissions`、`/system/reports` |

## 3. 路由命名决策

| 决策点 | 当前决策 | 说明 |
|---|---|---|
| 首页 | 使用 `/dashboard/workbench` | `/` 可作为部署层默认入口，但产品路由以工作台路由为准。 |
| 性能评估 | 保留 `/metric/*` | 不再强制回退到旧 UI/UX 的 `/performance/*`。如需兼容，可后续增加 redirect，不在菜单暴露。 |
| 指标配置 Tab 聚合 | `/metric/config` | 指标定义、权重、定级、可信度等配置在聚合页内以 Tab 呈现；不再为内部 Tab 暴露独立主菜单路由。 |
| 回路管理 | 保留 `/loop/manage` 聚合页 | `/loop/factory`、`/loop/ledger` 仅保留到 `/loop/manage` 的兼容重定向，不作为主菜单页。 |
| Tag 管理 | 使用 `/tag/list` | AAS Tag 是独立资源入口，不强行塞回 `/loop/mapping`。 |
| 诊断中心 | 以当前聚合 IA 为准 | waveform 合入详情，统计合入总览，A/B 对比合入 Tracker 抽屉；旧视图文件可保留但不构成路由。A/B 对比已实现（`GET /diagnosis/ab-compare`，含 `includeDiagnosis` 扩展，Batch 4）；诊断报告导出已实现（CSV `GET /diagnosis/statistics/export` + PDF `POST /tracker/{loopId}/export`，D5）。 |
| 系统安全说明 | 暂并入权限/审计/README | 是否新增 `/system/safety` 另行评审。 |

## 4. API 契约

### 4.1 v1.0 已声明且代码存在的 API 领域

| 领域 | 当前实现路径 | 说明 |
|---|---|---|
| 性能配置与看板 | `/api/v1/performance/*` | KPI 看板、排行、分析、回路快照、实时自控率。 |
| 诊断配置与跟踪 | `/api/v1/diagnosis/*` | 诊断列表、详情、任务、记录、统计；含 `/api/v1/tracker/*`（异常跟踪，子路由见 §4.5）与 `/api/v1/diagnosis/tags/*`（诊断标签）子路由。A/B 对比已实现（`GET /diagnosis/ab-compare`，含 `includeDiagnosis` 扩展，Batch 4）；算法元数据已实现（`GET /diagnosis/algorithms/meta`，Batch 4）；诊断统计导出已实现（`GET /diagnosis/statistics/export`，D5）。 |
| 整定算法 | `/api/v1/tuning/*` | Phase 1 实验/辅助能力，不代表自动下写 DCS。 |
| 用户管理 | `/api/v1/users/*` | 不强制改为 `/api/v1/system/users`。 |
| 审计日志 | `/api/v1/audit-logs/*` | 系统管理 UI 可消费该路径。 |
| 报表管理 | `/api/v1/reports/*` | 报表配置 CRUD、生成、任务状态查询。 |

### 4.2 v2.0 追认存在的 API 领域（v1.0 声明禁止但代码已存在）

| 领域 | 当前实现路径 | 说明 |
|---|---|---|
| 指标配置聚合 | `/api/v1/configs/metrics` | v1.0 声明"不新增"，v2.0 追认代码已存在批量指标配置接口。 |
| 诊断配置聚合 | `/api/v1/configs/diagnosis` | v1.0 声明"不新增"，v2.0 追认代码已存在批量诊断配置接口。 |

### 4.3 v2.0 补全的代码已有 API 领域（v1.0 未提及）

| 领域 | 当前实现路径 | 说明 |
|---|---|---|
| 认证 | `/api/v1/auth/*` | 登录、登出、刷新 token、获取当前用户、修改密码。 |
| 回路管理 | `/api/v1/loops/*` | 回路 CRUD、批量创建、监控、导入导出、Tag 关联、模式映射。 |
| Tag 管理 | `/api/v1/tags/*` | AAS Tag 列表、导入导出、批量删除、匹配回路。 |
| 工厂层级 | `/api/v1/plant-nodes/*` | 工厂树 CRUD、导入导出。 |
| 工作台 | `/api/v1/dashboard/*` | 工作台总览、看板、实时自控率。 |
| 实时数据 | `/api/v1/realtime/*` | 实时数据查询。 |
| WebSocket | `/api/v1/ws/*` | 实时推送通道。 |
| AAS 同步 | `/api/v1/aas/*` | AAS 配置、同步触发、同步状态与日志、Tag 列表。 |
| 配置中心 | `/api/v1/configs/*` | 含 `metrics`/`diagnosis`/`loop-type-weights`/`loop-level-weights`/`weight-templates`/`grading-thresholds`/`confidence-thresholds` 子领域。 |
| 算法独立调用 | `/api/v1/algorithms/*` | 含 `kpi`/`diagnosis`/`tuning`/`dataplanner` 子领域，用于算法独立调试与数据计划。 |
| 任务管理 | `/api/v1/tasks/*` | 标准评估、自定义评估、历史重算、任务通知、取消、删除、结果查询。 |
| 节点级 KPI | `/api/v1/performance/nodes/*` | 节点快照、趋势、排行、对比、总览、监控。 |
| 异常跟踪 | `/api/v1/tracker/*` | diagnosis.py 内的子路由（`tracker_router`），承担 Action Tracker 状态机流转、诊断建议书 PDF 导出、整改有效率统计与验证周期配置。详细子路由见 §4.5。 |
| 诊断标签 | `/api/v1/diagnosis/tags/*` | 诊断标签管理。 |
| 时间序列 | `/api/v1/timeseries/*` | 时间序列数据查询（tags.py 与 diagnosis.py 各一个 router）。 |
| 健康检查 | `/health`、`/health/ready` | 容器存活与就绪检查，挂载在根路径，不使用业务 API 前缀。 |
| 数据源配置 | `/api/v1/datasource/*` | 历史数据源连接测试、状态与配置管理。 |
| DCS 配置 | `/api/v1/dcs/*` | DCS 品牌、型号、MODE 定义与映射矩阵管理；不包含 DCS 参数下写。 |
| 回路历史数据导入 | `/api/v1/loops/data-import/*` | 导入预览、任务提交、状态查询、取消与删除；含数据完整性检查（`POST /loops/data-import/integrity-check`，按小时分桶对 7 列分别 `COUNT(col)` 统计列级缺失，支持非整点时间范围按实际秒数算预期点数，2026-07-22 上线）。 |

### 4.4 API 契约规则

- 所有 API 默认以 `/api/v1/` 为前缀；新增领域不得绕过此前缀。
- 新增 API 领域必须先在本契约 §4 登记路径与说明，再落地代码与测试。
- 算法独立调用接口（`/api/v1/algorithms/*`）仅用于调试与数据计划，不暴露给业务 UI 作为主入口。

### 4.5 Tracker 子路由清单（`tracker_router`，2026-07-27 v2.1 补充）

Tracker 子路由挂载在 `/api/v1/tracker` 前缀下，定义于 `backend/app/api/v1/endpoints/diagnosis.py`。

| 方法 | 路径 | 功能 | 批次 |
|---|---|---|---|
| `PATCH` | `/{loopId}/status` | 更新 Action Tracker 处理状态（PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED）；IMPLEMENTED 时必填 `mocRef` 或 `mocNotApplicable`+`mocReason`（D3 MOC 校验） | D2/D3 |
| `POST` | `/{loopId}/export` | 导出诊断建议书 PDF（reportlab + STSong-Light 中文字体） | D5 |
| `GET` | `/{loopId}` | 查询单回路 Tracker 详情（含 D1 建单来源、D4 整改效果验证字段） | D1/D4 |
| `PATCH` | `/{loopId}` | 更新 Tracker 补充字段（comment/updatedBy 等） | D2 |
| `GET` | `/effectiveness` | 整改有效率统计（支持 `timeWindow` + `plantNodeId` 筛选，返回已实施/已验证/改善/恶化数 + 每日趋势） | D4-3 |
| `GET` | `/verification-config` | 读取整改效果验证周期配置（从 `sys_config` 读取 `tracker.verification_interval_hours`，默认 24h） | D4-2 |
| `PATCH` | `/verification-config` | 更新验证周期（1~720h，可人工调节） | D4-2 |

**A/B 对比接口**（`diagnosis_router`，非 `tracker_router`）：

| 方法 | 路径 | 功能 | 批次 |
|---|---|---|---|
| `GET` | `/api/v1/diagnosis/ab-compare` | 实施前后两窗口 KPI 均值对比（`kpi_snapshot_hourly`）；支持 `implementedAt` 自动截取 [T-7d,T) 与 (T,T+7d]，或显式传入 `beforeStartTime/beforeEndTime/afterStartTime/afterEndTime`；`includeDiagnosis=true` 时额外返回 before/after 诊断标签对比（Batch 4 回路分析页增强） | F7/Batch 4 |
| `GET` | `/api/v1/diagnosis/algorithms/meta` | 8 类诊断算法展示元数据 + 当前生效阈值快照（Batch 4 算法价值传递） | F1/Batch 4 |
| `GET` | `/api/v1/diagnosis/statistics/export` | 诊断统计 CSV 导出（支持 `startDate/endDate/plantNodeId` 筛选） | D5 |
| `GET` | `/api/v1/performance/grade-distribution` | 等级分布统计下推：窗口函数取每回路最新快照后 SQL 聚合各等级计数（EXCELLENT/GOOD/FAIR/WARNING/POOR/INCONCLUSIVE + total），阈值读 `sys_config['grading_thresholds.current']`；参数同 `/loops/snapshots`（2026-07-28 优化整改 Phase 4） | 优化整改 |
| `GET` | `/api/v1/performance/loops/snapshots?grade=` | 快照列表新增 `grade` 参数：服务端按等级名筛选+分页（在 latestOnly 取最新之后应用，与分布桶计数一致）；不传时行为不变（2026-07-28） | 优化整改 |
| `GET` | `/api/v1/diagnosis/tasks?includeArchived=` | 诊断任务列表新增 `includeArchived` 参数（默认 false 仅未归档；true 时含已归档历史——SUCCESS 完成即自动归档）（2026-07-29） | 问题修复 |

## 5. 权限契约

| 角色 | 设计口径 |
|---|---|
| ADMIN | 全模块、全配置、全审计。 |
| IC_ENGINEER | 业务模块全流程，可编辑异常跟踪和回路配置。 |
| PE_ENGINEER | 可查看评估、监控、诊断汇总；可参与异常跟踪。 |
| EXPERT | 可查看诊断与整定相关页面，可参与异常跟踪和专家建议。 |
| SPONSOR | 只看工作台、性能汇总、诊断统计等汇总视图；不可进入单回路诊断详情、波形证据或异常跟踪编辑。 |

> **数据管理权限口径（D3，2026-07-21 对齐）**：历史数据导入（`/api/v1/loops/data-import/*`）与删除操作维持现状——允许 ADMIN / IC_ENGINEER 角色执行导入与删除；Tag 编辑/导入（D2）同口径。
>
> **权限码服务端落地（2026-07-28 优化整改 Phase 3）**：`ROLE_PERMISSIONS`（"模块:操作"码）此前只下发不执行；现已新增 `require_perms()` 依赖（通配匹配、ADMIN 全通），回路（`loop:view`）/整定（`tuning:view`）/诊断（`diagnosis:view`）三模块读端点已收口；EXPERT 映射补 `tuning:view`。前端路由同步收紧（`/system/reports`、`/loop/aas-sync` 仅 ADMIN；EXPERT 仅诊断+整定，默认首页 `/diagnosis`；SPONSOR 默认首页 `/metric`）。剩余收敛项：tasks 列表 `metric:view`、`diagnosis:detail` 粒度码（SPONSOR 仅汇总）、tracker 读端点（待 IC 映射补齐）。
>
> **首次登录强制改密（2026-07-28 Phase 5）**：`sys_user.must_change_password` 为 True 时（种子用户初始为 True），登录/ME 响应带 `mustChangePassword`，非 GET 且非改密/登出端点一律 403 `ERR_PASSWORD_CHANGE_REQUIRED`，改密成功清标志并吊销全部 token。

## 6. 状态机契约

| 对象 | 标准枚举 | 中文显示 |
|---|---|---|
| Action Tracker | `PENDING` → `IN_PROGRESS` → `IMPLEMENTED` / `IGNORED` | 待处理、处理中、已实施、已忽略 |
| Diagnosis Tag | `ACTIVE` / `RESOLVED` / `SUPPRESSED` | 活跃、已处理、已抑制 |
| Diagnosis Task | `PENDING` → `RUNNING` → `SUCCESS` / `FAILED` / `CANCELLED` | 待执行、执行中、成功、失败、已取消 |
| KPI 快照 | `SUCCESS` / `PARTIAL` / `INCONCLUSIVE` | 成功、部分有效、数据不足 |
| Loop | `READY` / `PARTIAL` / `INACTIVE` | 就绪（配置完整可参与 KPI 计算）、部分配置（缺必需 Tag，不参与计算）、已停用（软删除，is_active=False） |
| PV Quality | `GOOD` / `BAD` / `UNCERTAIN` | 好值、坏值、不确定 |
| Tuning | `DRAFT` / `RUNNING` / `COMPLETED` / `ROLLED_BACK` | 草稿、运行中、已完成、已回退 |

`ActionTracker.action_status` 与 `DiagnosisTag.status` 是两个独立状态机。`IMPLEMENTED` 只用于 Action Tracker；`RESOLVED` 仍是 Diagnosis Tag 的当前有效枚举，不得跨对象替换。

**诊断任务自动归档**（v2.1，2026-07-27）：`DiagnosisTask` 状态更新为 `SUCCESS` 时自动设置 `is_archived=True`、`archived_at=now()`、`archived_by="system-auto"`，任务从"诊断任务"列表移入"诊断记录"页面。`FAILED`/`CANCELLED` 任务不自动归档，保留在任务列表供用户排查后手动归档。

P1 #13 修正：历史文档中的 `ACTIVE`/`PAUSED`/`DECOMMISSIONED`（运行/暂停/退役）统一视为旧命名；当前代码与后续文档使用 `READY`/`PARTIAL`/`INACTIVE`（就绪/部分配置/已停用）。代码中的状态反映"配置完整性 + 删除状态"，而非"运行状态"：`READY` = 配置完整可参与 KPI 计算；`PARTIAL` = 缺必需 Tag，不参与计算；`INACTIVE` = 软删除（is_active=False）。

## 7. KPI 契约

### 7.1 体系结构：3 核心质量指标 + 1 折扣因子 + 8 扩展指标

代码实际的 MetricCalculator 体系为 3+1+8 结构，共 12 个独立计算器：

| 类型 | 指标名 | 字段名 | 用途 |
|---|---|---|---|
| 3 核心质量指标 | 准确率 | `accuracy_rate` | 反映 SP 跟踪 PV 的精度 |
| | 快速响应率 | `fast_rate` | 反映扰动恢复速度 |
| | 平稳率 | `steady_rate`（loop 级）/ `stability_rate`（unit 级） | 反映运行平稳程度 |
| 1 折扣因子 | 有效自控率 | `effective_auto_rate`（R） | 综合评分折扣因子 |
| 8 扩展指标 | 好值率 | `good_value_rate` | PV 数据质量 |
| | 自控率 | `auto_mode_rate` | 自动模式时长占比 |
| | 饱和率 | `saturation_rate` | OP 输出饱和占比 |
| | 振荡率 | `oscillation_rate` | 振荡识别占比 |
| | 理想稳定时间 | `ideal_settling_time` | 理论稳定时间 |
| | 实际稳定时间 | `settling_time` | 实测稳定时间 |
| | 输出跳变率 | `output_trip_index` | OP 跳变频率 |
| | 阀门粘滞 | `stiction_index` | 阀门粘滞估计 |

### 7.2 综合评分公式

```
P = (A·a + F·f + S·s) / (a + f + s) × R
```

其中：
- `A` = accuracy_rate（准确率）
- `F` = fast_rate（快速响应率）
- `S` = steady_rate / stability_rate（平稳率）
- `a / f / s` = 核心指标权重（R2 口径：`metric_config.weight` 为唯一用户入口；4 类权重模板 `loop_type_weight` 为出厂默认兜底。优先级链 `MetricConfig.weight` > `LoopTypeWeight` > `None`）
- `R` = effective_auto_rate（有效自控率，折扣因子）

### 7.3 4 类权重模板（出厂默认 / 兜底，R2 口径）

> **R2 权重口径裁决（2026-07-22）**：`metric_config.weight`（权重配置管理页面）为唯一用户入口；以下 4 类模板降级为出厂默认 / 兜底回退——仅当 `metric_config` 中 3 项核心指标权重缺失时按回路 `control_type` 回退取值。

| 模板 | 适用回路类型 | 权重倾向 |
|---|---|---|
| `STABLE` | 稳定型回路 | 平稳率权重最高 |
| `SLOW` | 慢响应回路 | 准确率权重最高 |
| `FAST` | 快速响应回路 | 快速响应率权重最高 |
| `LOGIC` | 逻辑开关回路 | 自定义权重组合 |

### 7.4 5 级性能定级

| 等级 | 枚举值 | 说明 |
|---|---|---|
| 优秀 | `EXCELLENT` | P ≥ 优秀阈值 |
| 良好 | `GOOD` | P ≥ 良好阈值 |
| 一般 | `FAIR` | P ≥ 一般阈值 |
| 警告 | `WARNING` | P ≥ 警告阈值 |
| 较差 | `POOR` | P < 警告阈值 |

阈值由 `/api/v1/configs/grading-thresholds` 维护，可在 UI 中配置。

### 7.5 对外口径

PRD 对外合规口径仍强调 6 大核心 KPI（好值率、自控率、平稳率、准确率、振荡率、饱和率）；实现以 3+1+8 体系为算法增强、排序与内部诊断的依据，但 UI/报表需明确区分"核心 KPI"与"扩展指标"。

### 7.6 缓存接入口径

- L1 DataBlock 缓存已接入 DataPlanner，负责复用预处理后的数据块。
- L2 MetricDataBundle 缓存已接入 DataPlanner，命中时跳过查询计划与 Bundle 组装。
- L3 Feature Cache 已有实现与单元测试，但尚未接入当前指标计算运行链路，属于预留能力，不计入现行性能验收。

### 7.7 节点聚合去重规则 [v6.1 新增]

节点级 KPI 聚合（`node_performance.py`）在构建加权平均前，对输入回路执行两阶段预处理：`include_in_evaluation` 过滤 + 复杂回路组去重。代码位置：`_fetch_and_aggregate_loops` / `_dedup_complex_groups` / `_pick_group_representative`。

**两阶段预处理流程**：

```
SQL 查询回路级 SUCCESS 快照
  ↓
① S1: WHERE include_in_evaluation = True  — 排除不参评回路
  ↓
② S3: _dedup_complex_groups(rows)  — 复杂组按 complex_loop_group_id 去重
  ↓
Python 按 importance_level 权重加权平均
```

**S1：include_in_evaluation 过滤**

在 SQL 查询阶段追加 `.where(LoopLedger.include_in_evaluation.is_(True))`，排除标记为不参评的回路。被排除回路的单回路 KPI 仍正常计算（供回路详情页展示），仅不进入节点聚合输入。

**S3：复杂回路组去重**

- `complex_loop_group_id` 为空（普通单回路）：全部保留。
- 同一 `complex_loop_group_id` 的回路组：仅保留 1 个代表，其余剔除。
- **代表选择规则**（`_pick_group_representative`）：
  1. 优先取 `complex_role = MAIN` 的成员（主回路）；
  2. `MAIN` 缺席时，退化取 `confidence_level` 最高的成员（A > B > C > D > E，`None` 最低）。
- 去重后 DEBUG 日志：`[节点级聚合-S3] 输入回路=N, 去重后代表=M, 复杂组=K`

**loop_count 口径**

`loop_count` = 去重后的回路组数（单回路计 1，复杂组计 1，不论组内几行）。小时/日/月快照均沿用此口径；日/月聚合的 `_max_loop_count` 取窗口内各小时快照 loop_count 最大值，无需改动。

历史快照保留当时口径，不回填（RFC 决策点 3/6）。复杂回路配置变更（group 重组）后，历史快照 loop_count 与现状可能不一致，属可接受偏差。

**加权聚合公式**

```
weight_total = Σ(representative.importance_level_weight)    // level 1→3, 2→2, 3→1
avg_score = Σ(representative.score × weight) / weight_total
avg_field = Σ(representative.field × weight) / weight_total  // 对所有 KPI 字段同理
auto_loop_ratio = count(representative.auto_mode_rate > 0) / loop_count × 100
```

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

## 10. 代码实际 ORM 表清单（31 张）

当前 `backend/app/models/` 共定义 31 张 ORM 表。以下清单以代码中的 `__tablename__` 为事实来源；DDS 后续修订应同步此口径。

| # | 类名 | __tablename__ | 文件 | 用途 |
|---|---|---|---|---|
| 1 | `LoopLedger` | `loop_ledger` | `models/loop.py` | 回路台账 |
| 2 | `LoopTagMapping` | `loop_tag_mapping` | `models/loop.py` | 回路-Tag 关联 |
| 3 | `TagRegistry` | `tag_registry` | `models/tag.py` | AAS Tag 注册表 |
| 4 | `PlantNode` | `plant_node` | `models/plant_node.py` | 工厂层级 |
| 5 | `MetricConfig` | `metric_config` | `models/metric.py` | 指标配置 |
| 6 | `KpiSnapshotHourly` | `kpi_snapshot_hourly` | `models/metric.py` | KPI 小时快照 |
| 7 | `KpiSnapshotCustom` | `kpi_snapshot_custom` | `models/metric.py` | KPI 自定义快照 |
| 8 | `ClpmMetricDataRequirement` | `clpm_metric_data_requirement` | `models/metric_data_requirement.py` | 指标数据需求 |
| 9 | `UnitKpiSummary` | `unit_kpi_summary` | `models/unit_kpi_summary.py` | 装置级 KPI 汇总 |
| 10 | `KpiNodeSnapshotHourly` | `kpi_node_snapshot_hourly` | `models/node_kpi.py` | 节点 KPI 小时快照 |
| 11 | `KpiNodeSnapshotDaily` | `kpi_node_snapshot_daily` | `models/node_kpi.py` | 节点 KPI 日快照 |
| 12 | `KpiNodeSnapshotMonthly` | `kpi_node_snapshot_monthly` | `models/node_kpi.py` | 节点 KPI 月快照 |
| 13 | `EngineRule` | `engine_rule` | `models/engine.py` | 引擎规则 |
| 14 | `DiagnosisConfig` | `diagnosis_config` | `models/diagnosis.py` | 诊断配置 |
| 15 | `DiagnosisResult` | `diagnosis_result` | `models/diagnosis.py` | 诊断结果 |
| 16 | `DiagnosisTag` | `diagnosis_tag` | `models/diagnosis.py` | 诊断标签 |
| 17 | `ActionTracker` | `action_tracker` | `models/tracker.py` | 异常跟踪 |
| 18 | `TuningRecord` | `tuning_record` | `models/tuning.py` | 整定记录 |
| 19 | `LoopModeMapping` | `loop_mode_mapping` | `models/loop_config.py` | 回路模式映射 |
| 20 | `LoopTypeWeight` | `loop_type_weight` | `models/loop_config.py` | 回路类型权重 |
| 21 | `LoopLevelWeight` | `loop_level_weight` | `models/loop_config.py` | 回路级别权重 |
| 22 | `SysUser` | `sys_user` | `models/sys_user.py` | 系统用户 |
| 23 | `SysAuditLog` | `sys_audit_log` | `models/audit.py` | 审计日志 |
| 24 | `SysConfig` | `sys_config` | `models/sys_config.py` | 系统配置 |
| 25 | `ReportRecord` | `report_record` | `models/report.py` | 报表记录 |
| 26 | `ReportConfig` | `report_config` | `models/report_config.py` | 报表配置 |
| 27 | `DiagnosisTask` | `diagnosis_task` | `models/diagnosis.py` | 诊断任务与归档状态 |
| 28 | `DcsVendor` | `dcs_vendor` | `models/dcs_vendor.py` | DCS 品牌配置 |
| 29 | `DcsModel` | `dcs_model` | `models/dcs_model.py` | DCS 型号配置 |
| 30 | `ModeDefinition` | `mode_definition` | `models/mode_definition.py` | MODE 语义定义 |
| 31 | `DcsModeMapping` | `dcs_mode_mapping` | `models/dcs_mode_mapping.py` | DCS MODE 映射矩阵 |

注：DDS v4.1 中声明的 `report_schedule` 实际由代码 `report_config` 承载；`sys_role` / `sys_user_role` 代码无对应模型，角色以枚举形式实现。

## 11. 变更记录

| 变更项 | v1.0 口径 | v2.0 口径 | 依据 |
|---|---|---|---|
| 版本号 | v1.0（2026-06-25） | v2.0（2026-07-06） | — |
| `/api/v1/configs/metrics` | 不新增 | 追认存在 | `endpoints/configs.py` |
| `/api/v1/configs/diagnosis` | 不新增 | 追认存在 | `endpoints/configs.py` |
| API 领域清单 | 6 项 | 6 项（已声明） + 2 项（追认） + 15 项（补全） | `v6-code-facts.md` §1 |
| KPI 体系 | 6 核心 + 2 扩展 | 3 核心 + 1 折扣因子 + 8 扩展（共 12 个计算器） | `v6-consistency-check.md` §6.1 |
| 综合评分公式 | 未声明 | `P = (A·a + F·f + S·s)/(a+f+s) × R` | FDS v6.0 |
| 4 类权重模板 | 未声明 | STABLE / SLOW / FAST / LOGIC | `endpoints/weight_config.py` |
| 5 级性能定级 | 未声明 | EXCELLENT / GOOD / FAIR / WARNING / POOR | `endpoints/grading_config.py` |
| ORM 表清单 | 未声明 | v2.0 按当前代码更新为 31 张（见 §10） | `backend/app/models/` |
| 状态机契约 | 已统一 | 与 v1.0 一致，无变更 | — |
| 前端 IA | v1.0 的旧路由清单 | v2.0 对齐当前路由模块，聚合性能与诊断页面 | `frontend/apps/web-antd/src/router/routes/modules/` |
| 新增 API 领域 | 未登记 | 补充 datasource、dcs、confidence-thresholds、loops/data-import | `backend/app/main.py` |
| 诊断状态机 | RESOLVED 统一视为旧命名 | 区分 Diagnosis Tag 与 Action Tracker 两套枚举 | `models/diagnosis.py`、`models/tracker.py` |
| A/B 对比 | 作为已存在能力列出 | 当前 API 返回 501，标记 P1 未实现 | `endpoints/diagnosis.py` |
| L3 缓存 | 三层均视为已接入 | L3 仅保留实现与测试，未接入运行链路 | `services/data_planner.py` |
