# CLPM 重构后实现契约

**文档状态**：active-baseline
**当前版本**：v2.9
**发布日期**：2026-08-09
**适用范围**：重构后 CLPM V1.0 / Phase 1 代码与设计文档对齐（含 IA 重构 Phase A-D + UI/UX 整改 Phase 0-2）
**v2.8 修订摘要（2026-08-09，UI/UX 整改 Phase 1-2 API 增量）**：登记 `GET /loops/monitor` 响应新增 `scoreDelta`/`dayTrend`（较昨日增量巡检）且默认排序改为最新快照评分升序 NULLS LAST（最需关注优先）；登记 `GET /diagnosis/list` aggregates 新增 `verifyOverdueCount`（VERIFYING 超 24h 未闭环计数）；登记 `GET|PUT /configs/algorithm-params` 响应新增 `paramMeta`（参数注册表 min/max/unit/description/category 单源下发）与 `AlgorithmParamsSaveRequest.resetControlTypes`（重置默认=清空覆盖回落算法默认）；登记 `GET /performance/loops/snapshots` 序列化新增 `timeConstant`（F5 时间常数计算器，L1 DISPLAY_ONLY，激励不足窗口为 null）与 `clpm_metric_data_requirement` 新增 time_constant 契约行；确认 Action Tracker P1a 闭环状态机（PENDING→IN_PROGRESS→VERIFYING→CLOSED，VERIFYING 可→REOPENED，存量 IMPLEMENTED 兼容映射 VERIFYING）；审计 `operationType`/`targetType` 为开放式枚举（前端映射见 audit.vue operationOptions/resourceOptions）。整改全貌见 `docs/设计文档/06-UIUX/ui-ux-rectification-checklist-2026-08-08.md` 与 `p2-exit-report-2026-08-09.md`

**v2.9 修订摘要（2026-08-09，监控—工作台 IA 再收敛）**：一级菜单由 7 个收敛为 6 个（监控/评估/诊断/整定/配置/系统），移除独立回路一级菜单；回路工作台归入监控，规范路径为 `/monitor/loop-workbench`，左侧列表承担跨回路扫视，右侧四区承担单回路监控、评估、诊断、整定闭环；高密度实时表保留为隐藏兼容视图 `/loop/monitor`，不再作为主导航入口。预警规则归入配置 `/config/alert-rules`（仅 ADMIN），预警事件归入监控 `/monitor/alerts`（全部角色）；旧 `/loop/workbench`、`/loop/detail/:id`、`/alert/events`、`/alert/rules` 保留重定向，后端 `/alert/*` API 不变。工作台概览改为自适应高度，补齐 PV/SP/OP/模式摘要、空态原因与下一步、非装饰性 Lucide 图标、未知算法/低可信度中性表达；持平趋势不再显示 ±0 噪声。
**v2.0 修订摘要**：按当前代码重校前端 IA、API、31 张 ORM 表、诊断双状态机与缓存接入状态；D5 口径统一后全库引用为 v2.0（历史 v2.1 摘要并入本版）
**v2.1 修订摘要**：同步诊断中心 Batch 4-6 交付成果——A/B 对比已实现（含 `includeDiagnosis` 扩展）；登记 `GET /diagnosis/algorithms/meta`、`GET /diagnosis/statistics/export`、`GET /tracker/effectiveness`、`GET|PATCH /tracker/verification-config`；补全 Tracker 子路由清单；更新诊断中心路由决策（A/B 对比不再返回 501）；登记诊断任务自动归档机制与 D1-D6 功能扩展
**v2.2 修订摘要**：同步 2026-07-28 全维度优化整改（Phase 0-5）——登记 `GET /performance/grade-distribution` 与 `/loops/snapshots?grade=`；权限码服务端落地（`require_perms`，loop/tuning/diagnosis 读端点收口）替代"待统一"标注；登记首次登录强制改密（`must_change_password`）；前端路由收紧（reports/aas-sync 仅 ADMIN、EXPERT→/diagnosis、SPONSOR→/metric）。整改全貌见 `docs/过程文档/clpm-optimization-review-plan-2026-07-28.md`
**v2.2 增补（2026-07-29）**：登记 `/diagnosis/tasks?includeArchived=`；诊断任务时间戳默认值统一 UTC（迁移 `h8b9c0d1e2f3`）；refresh 轮换幂等窗口（`refresh_rotated`，120s）；整定 Phase 2.1 合并（`tuning_identification` 算法栈 + 异步辨识任务）
**v2.3 修订摘要（2026-07-29，Phase 0 Truth First）**：可信辨识安全收口——固化整定目标状态机（`DRAFT→RUNNING→IDENTIFIED→SIMULATED→COMPLETED/INCONCLUSIVE/ROLLED_BACK`，旧值 `PENDING/APPLIED/VERIFIED` 只读兼容）；登记模型来源门禁契约（`ModelSource`、`ThetaSource`、`DataSource` 与 A–E 放行规则）；ORM 表清单校正为 37 张（补 `algorithm_parameter`/`dcs_pid_structure`/`diagnosis_config_change`/`diagnosis_rule`/`diagnosis_threshold_override`/`loop_confidence_latest`）；登记生产 bootstrap DDL 收敛至 37 表与安全边界静态门禁（无 DCS 参数下写端点）。详见 `docs/过程文档/clpm-v6.2-phase0-contract-baseline-2026-07-29.md`
**v2.4 修订摘要（2026-08-05，IA 整改 P3-01）**：整定知识库不可变快照——新增 `TuningKnowledgeEntry` 表（`tuning_knowledge_entry`，第 38 张 ORM 表）；登记 `generate_knowledge_entry` 验证钩子（hybrid 关联 `tuning_record_id` + 幂等 `on_conflict_do_update`）；登记 3 个知识库 API 端点（`GET /tuning/knowledge-base` 列表、`GET /tuning/knowledge-base/{id}` 详情、`GET /tuning/knowledge-base/similar` 相似案例）；`ActionTracker` 新增 `tuning_record_id` 外键（`ondelete=SET NULL`）；`TrackerStatusUpdate` schema 新增 `tuningRecordId` 字段（VERIFYING 时可选，用于知识库生成）。详见 `docs/过程文档/clpm-ia-rectification-task-checklist-2026-08-05.md` §P3-01
**v2.5 修订摘要（2026-08-06，IA 整改 P3-04 AI 洞察全局赋能）**：登记 LLM 配置 API（`/api/v1/configs/llm`，6 个 sys_config 键：`llm.enabled/endpoint/api_key/model/timeout/max_tokens`，API Key 脱敏返回，仅 ADMIN 可改）与 AI 洞察通用服务 API（`POST /api/v1/ai-insight/{scene}`，4 场景 diagnosis/performance/tuning/workbench 统一入口，`mode=auto/llm/template`，LLM 失败自动 fallback 规则模板）；登记 `SceneStrategy` 抽象（`load_context/build_system_prompt/build_user_prompt/generate_template`）与 `AiInsightContext.knowledgeContext` RAG 扩展点（第一期恒 None）；旧 `POST /diagnosis/{loopId}/interpret` 内部代理到 `scene=diagnosis`，字段映射 `insight→interpretation` 向后兼容；前端通用组件 `ClpmAiInsight`（scene/loopId/taskId/variant/hideWhenDisabled props，LLM 未启用时按 `hideWhenDisabled` 决定隐藏或显示启用提示），4 场景嵌入（诊断详情第 5 Tab、性能详情综合评估 Tab、整定仿真右侧栏保存后、工作台 KpiStrip 后）。详见 `docs/过程文档/clpm-ia-rectification-task-checklist-2026-08-05.md` §P3-04
**v2.6 修订摘要（2026-08-07，IA 重构 Phase A-D 全量合入）**：前端信息架构从"6 模块 + 1 门户"重构为"7 模块"（监控/回路/评估/诊断/整定/配置/系统），引入双轴导航（实体轴回路工作台 + 职能轴评估/诊断/整定）与工程/操作分离原则（结构性配置集中到 `/config/*`，操作性调参保留业务模块内联）。Phase A：7 菜单重组 + 配置集中化（9 项结构性配置迁入 `/config/*`）+ AI 右抽屉替换 4 处内嵌 + 跨模块上下文传递基建（`?loopId=`/`?taskId=`）+ 11 条旧路由 legacy redirect；Phase B：回路工作台 6 Tab（概览/评估/诊断/整定/效果对比/时间线），`/loop/detail/:id` 重定向到 `/loop/workbench?loopId=:id`；Phase C：诊断三区重构（结论先行 + 问题定位路径 + 证据折叠）+ 列表页新增 `/diagnosis/loop-analysis` + `/diagnosis/records` 合并入 `/diagnosis/tasks?tab=history` + `/diagnosis/visualization` 合并入详情证据区；Phase D：整定 3 页向导整合为 `/tuning/detail` 单页 + 4 锚点导航（①过程辨识 ②PID推荐 ③闭环仿真 ④方案确认）+ 7 条旧路由重定向 + 方案确认留痕（不下写 DCS）。全方案后端零改动，详见 `docs/过程文档/clpm-ia-refactor-and-optimization-plan-2026-08-06.md`
**v2.7 修订摘要（2026-08-07，回路工作台单页四区重构 + 自动诊断 Beat 停用）**：回路工作台从 v2.6 的 6 Tab 设计重构为**单页四区垂直布局**——概览区(10%) + 性能评估行(30%) + 回路诊断行(30%) + 回路整定行(30%)，一页内聚合评估/诊断/整定概况并可直接发起任务、实时反写显示。路由约束：`/loop/workbench` 的 meta 必须设 `fullPathKey: false`，左侧切换回路用 `router.replace` 更新 `?loopId=` query，避免 vben tab key 基于 fullPath 导致 query 变化新建 tab/面包屑。性能评估行 12 KPI 卡片(6×2) + 评分趋势图(8h/12h/24h/48h/72h 切换)；诊断行支持 PV/OP 波形 ↔ FFT 频谱切换；整定行显示当前 PID/模型(K/τ/θ)/超调量/上升时间/稳定时间 + 推荐 PID + 三按钮(回路辨识/参数整定/模拟仿真)。前端约定：ECharts option 中所有 `type` 字段必须 `as const` 断言，否则 TS 推断为 string 致类型检查失败。后端变更：`diagnosis_engine.py` 中 `diagnosis-engine-hourly` 与 `diagnosis-engine-checkup-8h` 两个 Celery Beat 已注释停用（保留手动触发函数），仅保留小时级自动性能评估。详见 commit `5e216ba8`。

## 1. 定位

本文件记录 2026-06 重构后的真实信息架构、路由、API、权限、状态机与阶段口径。后续 PRD、UI/UX、DESIGN、README、测试与代码评审均以本文件作为实现契约入口。

版本分层：产品文档使用 v6.1 表示需求与设计基线；后端 `APP_VERSION`（当前默认 `1.0.0`）用于运行时 API 元数据；Git tag 用于发布追踪。三者职责不同，发布时分别维护，不以数值相等作为一致性条件。

本文件不是推翻 PRD/UI/UX，而是把重构后的设计意图固化为新的派生基线：

- 保留重构后的主要信息架构与聚合页面。
- 文档追认当前代码中的产品化组织方式。
- 旧设计文档中与本契约冲突的页面路径、页面数量、阶段表述，以本契约为准。
- 算法、安全、审计、权限等业务边界仍以 PRD 为上位约束。

## 2. 信息架构契约

CLPM 当前采用 **6 个一级模块**：监控 / 评估 / 诊断 / 整定 / 配置 / 系统。回路是平台核心实体，但不再单独占用一级菜单；回路运行态统一收敛到监控下的回路工作台，回路结构性配置归配置模块。保留**双轴导航**（监控下的实体轴工作台 + 评估/诊断/整定职能轴）与**工程/操作分离**原则（结构性配置集中到 `/config/*`，操作性调参保留业务模块内联）。

| 模块 | 轴 | 当前设计意图 | 当前主要路由 |
|---|---|---|---|
| 监控 | 实体轴 | 系统概览、跨回路扫视、单回路 360° 工作台、预警结果与运行处置 | `/dashboard/workbench`、`/monitor/loop-workbench`、`/monitor/alerts` |
| 评估 | 职能轴 | 性能总览、回路性能趋势、评估任务、KPI 报表（跨回路批量） | `/metric/pid-dashboard`、`/metric/loop-performance`、`/metric/tasks`、`/metric/kpi-report` |
| 诊断 | 职能轴 | 诊断总览、回路分析（列表）、诊断任务（Tabs 含历史记录）、异常跟踪、详情三区 | `/diagnosis/overview`、`/diagnosis/loop-analysis`、`/diagnosis/tasks`、`/diagnosis/tracker`、`/diagnosis/detail/:loopId` |
| 整定 | 职能轴 | 整定工作台、整定任务详情（单页 4 锚点）、整定知识库、效果统计 | `/tuning/workbench`、`/tuning/detail`、`/tuning/knowledge-base`、`/tuning/stats` |
| 配置 | — | 结构性配置集中（链路/测点/回路/数据源/指标/诊断/预警规则）；操作性调参保留业务模块内联 | `/config/link`、`/config/tag`、`/config/loop`、`/config/datasource`、`/config/metric`、`/config/diagnosis`、`/config/alert-rules` |
| 系统 | — | 用户、审计、权限矩阵、自动报表、LLM 配置 | `/system/users`、`/system/audit`、`/system/permissions`、`/system/reports`、`/system/llm-config` |

**模块口径声明（v2.6）**：
- 评估任务为评估模块子页（`/metric/tasks`），任务详情跳转保留 `/tasks/:taskId` 兼容路由（隐藏）。
- 结构性配置（链路/测点/回路/工厂/台账/指标/诊断/数据源/PID 模板）统一归入配置模块；操作性调参（诊断阈值微调、算法参数、时间窗、列设置）保留在各业务页内联。
- 历史数据导入按钮保留在监控/评估工具栏（操作型，INCONCLUSIVE 时补数据），不埋进配置。
- 旧路由（`/loop/aas-sync`、`/tag/list`、`/loop/manage`、`/loop/workbench`、`/alert/events`、`/alert/rules` 等）全部 legacy redirect 到新路径，保护书签与 E2E；`/loop/monitor` 保留为隐藏的高密度实时表视图。

#### IA 再收敛：监控—回路工作台—预警（v2.9）

| 用户任务 | 主入口 | 信息组织 | 设计边界 |
|---|---|---|---|
| 看全厂/跨回路当前状态 | `/monitor/loop-workbench` 左侧回路列表 | PV/SP/OP/模式、评分/较昨日趋势、可信度、搜索与选择 | 只给扫视所需摘要，不在列表复制完整诊断与整定详情 |
| 处置单个回路 | `/monitor/loop-workbench?loopId=` 右侧工作台 | 概览 → 性能评估 → 诊断 → 整定，按同一回路上下文连续推进 | 每区保留摘要、1 个主图、明确动作；任务完成后自动反写 |
| 查看/处置预警结果 | `/monitor/alerts` + 全局通知铃铛 | 事件列表、状态、严重度、回路上下文、确认/处置/归档 | 预警是运行结果，不制造独立一级模块 |
| 配置预警规则 | `/config/alert-rules` | 规则定义、订阅、启停、试运行、审计 | 仅 ADMIN；规则配置不与运行事件混在同一页 |

监控与工作台的合并采用“导航合并、认知任务分层”：跨回路扫视与单回路深度处置不强行压成一张普通表格。左侧列表承载监控入口，右侧工作台承载处置闭环；旧高密度实时表仅作为批量巡检/导出与兼容视图保留。

### 2.1 IA 重构 Phase A-D 变更清单 [v2.6 新增]

#### Phase A — 菜单重组 + 配置集中化 + AI 右抽屉 + 跨模块上下文

**一级菜单 6→7**：新增"配置"模块；原"工作台门户"升级为"监控"（运行驾驶舱）；原"回路管理"拆分为"回路"（实体轴工作台）+ "配置"（结构性配置）；原"性能评估"改名"评估"（职能轴）。

**新增路由**：

| 路由 | 组件 | 权限 | 说明 |
|---|---|---|---|
| `/monitor` | redirect `/dashboard/workbench` | ADMIN/IC/PE/SPONSOR | 监控父路由 |
| `/assess` | redirect `/metric/pid-dashboard` | ADMIN/IC/PE/SPONSOR | 评估父路由（leaf 保留 `/metric/*`） |
| `/config` | redirect `/config/loop` | ADMIN | 配置父路由 |
| `/config/link` | `views/loop/aas.vue` | ADMIN | 链路配置（原 `/loop/aas-sync`） |
| `/config/tag` | `views/tag/list.vue` | ADMIN/IC/PE | 测点配置（原 `/tag/list`） |
| `/config/loop` | `views/loop/manage.vue` | ADMIN/IC/PE | 回路配置（原 `/loop/manage`，含工厂/台账） |
| `/config/datasource` | `views/loop/data.vue` | ADMIN/IC/PE | 数据源管理（原 `/loop/data`） |
| `/config/metric` | `views/metric/config.vue` | ADMIN | 指标配置（原 `/metric/config`） |
| `/config/diagnosis` | `views/diagnosis/config.vue` | ADMIN | 诊断配置（原 `/diagnosis/config`） |
| `/system/llm-config` | `views/system/llm-config.vue` | ADMIN | LLM 配置（P3-04） |

**legacy redirect（11 条）**：`/loop/aas-sync`→`/config/link`、`/tag/list`→`/config/tag`、`/tag`→`/config/tag`、`/loop/manage`→`/config/loop`、`/loop/factory`→`/config/loop`、`/loop/ledger`→`/config/loop`、`/loop/data`→`/config/datasource`、`/metric/config`→`/config/metric`、`/diagnosis/config`→`/config/diagnosis`、`/system/pid-template`→`/config/link`、`/system/algorithm-params`→`/config/metric`。

**跨模块上下文基建**：统一 `route.query` 规范（`?loopId=`/`?taskId=`），封装 `useLoopContext()` composable；评估/诊断/整定模块工具栏"发起诊断/评估/整定"按钮携带 loopId 跳转。

**AI 右抽屉**：替换原 4 处内嵌 `ClpmAiInsight`，统一为工具栏 AI 图标 + 右侧 overlay 抽屉（480px，≤300ms 动画，遮罩可关）；两级门禁（LLM 配置 + 页面上下文）控制图标状态；整定场景本轮下线（图标灰显）。

#### Phase B — 回路工作台单页四区（v2.7 设计历史，v2.9 路由已收敛）

**新增路由**：

| 路由 | 组件 | 权限 | 说明 |
|---|---|---|---|
| `/monitor/loop-workbench` | `views/loop/workbench.vue` | ADMIN/IC/PE(只读)/EXPERT | 当前回路工作台主页（单页四区） |
| `/loop` | redirect `/monitor/loop-workbench` | ADMIN/IC/PE/EXPERT | 旧回路父路由兼容入口 |
| `/loop/workbench` | redirect `/monitor/loop-workbench` | ADMIN/IC/PE(只读)/EXPERT | 旧工作台路径兼容入口 |

**单页四区布局**（v2.7，commit `5e216ba8`，原 6 Tab 已废弃）：页面垂直分为四区，左侧回路列表选中后 `router.replace` 更新 `?loopId=` query，**仅更新右侧子页面、不新增 tab/面包屑**（路由 meta `fullPathKey: false`，vben tab key 退化为 route.path）。

| 区 | 高度占比 | 内容 |
|---|---|---|
| ① 回路概览 | 10% | 位号/名称/量程/控制方式/设定值/实时值/数据健康度 + 趋势/历史按钮 |
| ② 性能评估行 | 30% | 回路等级 + 12 KPI 卡片(6×2) + 评分趋势图(8h/12h/24h/48h/72h Segmented 切换) + 发起评估按钮 |
| ③ 回路诊断行 | 30% | 诊断标签+置信度卡片 + PV/OP 波形 ↔ FFT 频谱切换 + 发起诊断按钮 |
| ④ 回路整定行 | 30% | 当前 PID/模型(K/τ/θ)/超调量/上升时间/稳定时间 + 推荐 PID + 三按钮(回路辨识/参数整定/模拟仿真) |

**设计原则**：一个页面聚合评估/诊断/整定概况，可直接发起新任务并实时反写显示；详情通过弹窗展示。每区最多"摘要 + 1 主图 + 动作入口"。

**重定向**：`/loop/detail/:id` → `/monitor/loop-workbench?loopId=:id`（兼容旧书签/monitor 行点击/E2E）。

#### Phase C — 诊断三区重构 + 特征字典

**新增路由**：

| 路由 | 组件 | 权限 | 说明 |
|---|---|---|---|
| `/diagnosis/loop-analysis` | `views/diagnosis/loop-analysis.vue` | ADMIN/EXPERT/IC/PE | 回路分析列表（标签徽章+置信度+严重度+操作列） |

**详情页三区重构**（`/diagnosis/detail/:loopId`）：① 结论区（标签徽章+置信度+严重度+模板小结+AI 洞察入口，顶部一眼可见）② 问题定位路径区（横向流程图：现象→特征→根因，节点可点展开证据）③ 证据区（特征值表带"工程含义"列+波形/散点/频谱，默认折叠）。

**特征字典**：~20 项特征值（oscillationIndex/stictionIndex/timeConstant 等），独立 JSON 配置 `frontend/apps/web-antd/src/clpm/feature-dictionary.json`，非硬编码。

**重定向**：`/diagnosis/records` → `/diagnosis/tasks?tab=history`（合并入诊断任务 Tabs）；`/diagnosis/visualization` → `/diagnosis/detail/:loopId` 或 `/diagnosis/overview`（合并入详情证据区）。

#### Phase D — 整定单页整合 4 锚点

**新增路由**：

| 路由 | 组件 | 权限 | 说明 |
|---|---|---|---|
| `/tuning/detail` | `views/tuning/detail.vue` | ADMIN/IC/EXPERT | 整定任务详情单页（4 锚点，隐藏菜单） |

**4 锚点导航**：① 过程辨识（运行辨识→G(s)+阶次+可信度）② PID 推荐（算法推荐+多组候选对比表）③ 闭环仿真（参数微调+响应曲线实时对比）④ 方案确认（确认建议+导出方案+风险与回退+留痕）。

**双入口**：实体轴入口（回路工作台→"开始整定"，带 loopId）+ 职能轴入口（整定工作台→"新建整定任务"，弹出回路选择器）。

**嵌入式组件机制**：子页面（model/algorithm/simulation）通过 `embedded` prop 条件渲染 `<Page>` 外壳，单页模式下由父容器 `detail.vue` 提供统一导航；`v-show` 保持组件状态，避免整页路由切换；`router.replace` 更新 URL query 同步参数，`watch(route.query)` 实现参数传递。

**安全边界**：第④步命名"方案确认"（非"定稿"），仅输出建议+证据+风险+回退+留痕，**绝不直写 DCS**。

**重定向（7 条）**：`/tuning/flow`、`/tuning/flow/model`、`/tuning/flow/algorithm`、`/tuning/flow/simulation`、`/tuning/model`、`/tuning/algorithm`、`/tuning/simulation` → `/tuning/detail`（保留 query 参数）。

#### IA 再收敛落地（v2.9，2026-08-09）

- **一级菜单**：从 7 个收敛为 6 个，移除独立“回路”和“预警”一级菜单；当前菜单为监控 / 评估 / 诊断 / 整定 / 配置 / 系统。
- **监控主入口**：`/monitor/loop-workbench` 使用 `views/loop/workbench.vue`，统一回路监控、性能评估、诊断和整定的同一回路上下文；`/monitor` 按角色进入系统概览（ADMIN/IC/PE/SPONSOR）或回路工作台（EXPERT），系统概览仍保留 `/dashboard/workbench` 子入口。
- **跨回路扫视**：工作台左侧列表显示位号、PV/SP/OP、模式、评分/较昨日趋势和可信度；原 `/loop/monitor` 高密度实时表改为隐藏视图，保留批量巡检/导出和兼容能力。
- **预警分层**：运行态预警事件使用 `/monitor/alerts`，规则配置使用 `/config/alert-rules`；原 `/alert/events`、`/alert/rules` 仅重定向，后端 `/alert/*` API 不迁移。
- **上下文稳定性**：工作台的 `fullPathKey: false` 与 `router.replace` 继续生效；旧 `/loop/workbench`、`/loop/detail/:id` 统一重定向到 `/monitor/loop-workbench`。

## 3. 路由命名决策

| 决策点 | 当前决策 | 说明 |
|---|---|---|
| 首页 | 使用 `/dashboard/workbench`（监控模块下） | `/` 可作为部署层默认入口，但产品路由以监控模块的系统概览路由为准。 |
| 一级菜单数量 | 6 个模块（v2.9） | 监控/评估/诊断/整定/配置/系统；回路运行态并入监控工作台，结构性配置并入配置，预警规则并入配置。 |
| 双轴导航 | 实体轴（回路工作台）+ 职能轴（评估/诊断/整定） | 实体轴回答"这个回路怎么处置"，职能轴回答"全厂哪些回路有问题"；互为入口，loopId 上下文传递。 |
| 工程/操作分离 | 结构性配置→`/config/*`；操作性调参→业务模块内联 | 结构性配置（链路/测点/回路/指标/诊断）一次性低频，集中到配置模块；操作性调参（阈值微调/算法参数/时间窗/列设置）高频上下文相关，保留内联。 |
| leaf 路径稳定策略 | 评估模块 leaf 保留 `/metric/*` 绝对路径 | 父路由改为 `/assess`，但 leaf 路径（`/metric/pid-dashboard` 等）保持不变，避免硬编码路由大面积破坏；仅重命名高价值配置项（`/metric/config`→`/config/metric`）。 |
| 监控模块 leaf | `/dashboard/workbench` + `/monitor/loop-workbench` + `/monitor/alerts` | `/loop/monitor` 仅保留为隐藏高密度兼容视图；监控主入口统一到回路工作台。 |
| 性能评估 | 保留 `/metric/*` leaf | 不再强制回退到旧 UI/UX 的 `/performance/*`。 |
| 指标配置 Tab 聚合 | 迁移到 `/config/metric` | 指标定义、权重、定级、可信度、KPI 算法参数等配置在聚合页内以 Tab 呈现；从性能评估模块迁入配置模块（工程/操作分离）。 |
| 回路管理 | `/monitor/loop-workbench` 实体轴 + `/config/loop` 配置 | 回路工作台（运行态）归监控模块；回路配置（结构性）归配置模块；`/loop/manage` redirect 到 `/config/loop`。 |
| Tag 管理 | 迁移到 `/config/tag` | AAS Tag 配置归配置模块；`/tag/list` redirect 到 `/config/tag`。 |
| 诊断中心 | 三区重构 + 列表以回路为中心 | waveform 合入详情证据区，统计合入总览，A/B 对比合入 Tracker 抽屉，records 合入 tasks Tabs；详情页三区（结论/路径/证据）。A/B 对比已实现（`GET /diagnosis/ab-compare`，含 `includeDiagnosis` 扩展，Batch 4）；诊断报告导出已实现（CSV `GET /diagnosis/statistics/export` + PDF `POST /tracker/{loopId}/export`，D5）。 |
| 整定单页 | `/tuning/detail` 单页 4 锚点 | 原 3 页向导（model/algorithm/simulation）整合为单页，锚点导航替代整页跳转；旧路由全量重定向。 |
| 旧路由兼容 | legacy redirect 保护书签/E2E | 所有被迁移/合并的旧路由配置 redirect，不直接删除；`route-compat.spec.ts` 守护。 |
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
| LLM 配置 | `/api/v1/configs/llm` | LLM 服务自助配置（BaseURL/API Key/模型/超时/max_tokens），API Key 脱敏返回；`POST /configs/llm/test` 连接测试。仅 ADMIN 可改。 |
| AI 洞察 | `/api/v1/ai-insight/{scene}` | 4 场景统一自然语言洞察生成（diagnosis/performance/tuning/workbench）；`mode=auto/llm/template`，LLM 失败自动 fallback 规则模板；旧 `POST /diagnosis/{loopId}/interpret` 内部代理到 `scene=diagnosis`，字段映射 `insight→interpretation` 向后兼容。 |

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

### 5.1 6 个一级模块权限矩阵 [v2.9]

| 模块 | ADMIN | IC_ENGINEER | PE_ENGINEER | EXPERT | SPONSOR |
|---|---|---|---|---|---|
| 监控（`/monitor`；工作台/事件） | ✅ | ✅ | ✅ | 工作台/事件 | 概览/事件 |
| 评估（`/assess`） | ✅ | ✅ | ✅ | — | ✅ |
| 诊断（`/diagnosis`） | ✅ | ✅ | 只读 | ✅ | 仅总览/任务/记录汇总 |
| 整定（`/tuning`） | ✅ | ✅ | — | ✅ | — |
| 配置（`/config`） | ✅ | 测点/回路/数据源查看 | 测点/回路/数据源查看 | — | — |
| 系统（`/system`） | ✅ | — | — | — | — |

**默认首页口径**：
- ADMIN / IC_ENGINEER / PE_ENGINEER / SPONSOR：默认 `/dashboard/workbench`（监控模块）。
- EXPERT：默认 `/diagnosis`（诊断模块），可按回路上下文进入 `/monitor/loop-workbench`。
- SPONSOR：默认 `/metric`（评估模块），仅看汇总视图。

> **数据管理权限口径（D3，2026-07-21 对齐）**：历史数据导入（`/api/v1/loops/data-import/*`）与删除操作维持现状——允许 ADMIN / IC_ENGINEER 角色执行导入与删除；Tag 编辑/导入（D2）同口径。
>
> **权限码服务端落地（2026-07-28 优化整改 Phase 3）**：`ROLE_PERMISSIONS`（"模块:操作"码）此前只下发不执行；现已新增 `require_perms()` 依赖（通配匹配、ADMIN 全通），回路（`loop:view`）/整定（`tuning:view`）/诊断（`diagnosis:view`）三模块读端点已收口；EXPERT 映射补 `tuning:view`。前端路由同步收紧（`/system/reports`、`/config/link` 仅 ADMIN；EXPERT 仅诊断+整定，默认首页 `/diagnosis`；SPONSOR 默认首页 `/metric`）。剩余收敛项：tasks 列表 `metric:view`、`diagnosis:detail` 粒度码（SPONSOR 仅汇总）、tracker 读端点（待 IC 映射补齐）。
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
| Tuning | `DRAFT` → `RUNNING` → `IDENTIFIED` → `SIMULATED` → `COMPLETED`；`RUNNING`/`IDENTIFIED`/`SIMULATED` → `INCONCLUSIVE`；`SIMULATED`/`COMPLETED` → `ROLLED_BACK` | 草稿、运行中、已辨识、已仿真、已完成、数据不足、已回退 |

`ActionTracker.action_status` 与 `DiagnosisTag.status` 是两个独立状态机。`IMPLEMENTED` 只用于 Action Tracker；`RESOLVED` 仍是 Diagnosis Tag 的当前有效枚举，不得跨对象替换。

**整定状态机 v2.3 固化（Phase 0）**：目标写入状态机为 `DRAFT → RUNNING → IDENTIFIED → SIMULATED → COMPLETED`，分支 `INCONCLUSIVE`（数据/激励/模型/安全门禁不足）与 `ROLLED_BACK`（人工撤回或按回退方案处理）。旧值 `PENDING`/`APPLIED`/`VERIFIED` 只读兼容一个版本，不再作为新写入值：`PENDING` 查询保留但新建改用 `DRAFT`；`APPLIED`/`VERIFIED` 保留原始审计语义，不折算为 `COMPLETED`，不推断为平台自动实施。详见契约基线 §2。

**诊断任务自动归档**（v2.1，2026-07-27）：`DiagnosisTask` 状态更新为 `SUCCESS` 时自动设置 `is_archived=True`、`archived_at=now()`、`archived_by="system-auto"`，任务从"诊断任务"列表移入"诊断记录"页面。`FAILED`/`CANCELLED` 任务不自动归档，保留在任务列表供用户排查后手动归档。

P1 #13 修正：历史文档中的 `ACTIVE`/`PAUSED`/`DECOMMISSIONED`（运行/暂停/退役）统一视为旧命名；当前代码与后续文档使用 `READY`/`PARTIAL`/`INACTIVE`（就绪/部分配置/已停用）。代码中的状态反映"配置完整性 + 删除状态"，而非"运行状态"：`READY` = 配置完整可参与 KPI 计算；`PARTIAL` = 缺必需 Tag，不参与计算；`INACTIVE` = 软删除（is_active=False）。

### 6.1 模型来源与可信度门禁契约 [v2.3 Phase 0 新增]

整定推荐链（`/tune`、`/simulate`、`/compare`、`/calculate`）必须通过服务端 `authorize_tuning_model` 复核模型来源与可信度，客户端传入的置信度/辨识方法/替代参数不得提升放行等级。

**模型来源 `ModelSource`**（推荐链凭据）：

| 值 | 语义 | 凭据要求 |
|---|---|---|
| `IDENTIFICATION_RECORD` | 历史辨识记录 | 必须携带服务端可验证的 `sourceRecordId` |
| `STEP_EXPERIMENT` | 受控阶跃实验 | 服务端 `stepValidationPassed=true` 证据 |
| `MANUAL` | 人工模型 | 必须显式 `riskConfirmed=true` |

**纯滞后来源 `ThetaSource`**：

| 值 | 语义 | 放行约束 |
|---|---|---|
| `EXPLICIT` | 调用方提供并可追溯 | 正常放行 |
| `HEURISTIC_2TS` | 缺省 2 采样周期启发值 | 可信度封顶 C，不得直接进入整定 |

**数据来源 `DataSource`**：canonical 值 `HISTORY`/`STEP_EXPERIMENT`/`FALLBACK_STEP`；旧值 `fallback_step` 兼容读取一版。`FALLBACK_STEP` 必须含 `stepValidationPassed=true` 服务端证据，禁止用 PV 变化冒充 MV 阶跃。

**A–E 可信度放行规则**：

| 来源/可信度 | 辨识结果展示 | PID 整定/推荐仿真 |
|---|---|---|
| 版本化记录 A/B | 允许 | 允许 |
| 版本化记录 C | 允许 | 仅显式人工风险确认后允许 |
| D/E/INCONCLUSIVE/空 | 允许解释原因 | 禁止 |
| `HEURISTIC_2TS` | 允许，明确标为 2Ts 启发值 | 禁止 |
| CLIVC（可证明闭环一致 IV） | 允许 | 受可信度门禁约束（A/B 放行、C 需确认、D/E/INCONCLUSIVE 拒绝） |
| 受控阶跃实验 | 允许 | 仅服务端阶跃门禁通过后允许 |
| 人工模型 | 明确标为人工输入 | 仅显式风险确认后允许 |

P2-009 已实现 CLIVC（`identify_clivc`/`identify_clivc4`，外生 SP 作工具变量，满足 `E[Z·ε]=0` 闭环一致性），`IV_CAPABILITY_STATUS="CLIVC_PRODUCTION_READY"`，复用 `HISTORICAL_IV` 枚举进入生产候选集，按正常可信度门禁放行。早期 `identify_iv`/`identify_iv4` 实验性原型保留为对照，pipeline 不调用。Phase 0 的"闭环 IV 降级为 EXPERIMENTAL"门禁已随 P2-009 解除。

### 6.2 安全边界静态门禁 [v2.3 Phase 0 新增]

- 平台不得存在 DCS 运行时 PID 参数 `write`/`apply`/`deploy`/`implement` 端点（`test_security_p2.py::TestNoDcsParameterWriteSurface` 静态扫描守护）。
- DCS vendor/model/PID structure API 只管理离线适配配置，不连接控制站下写。
- `COMPLETED`/`APPLIED`/`已实施` 文案不能被解释为平台自动下写。
- 所有模型与 PID 建议必须保留来源、算法版本、可信度、reason code 和人工确认审计。

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

## 10. 代码实际 ORM 表清单（38 张）

当前 `backend/app/models/` 共定义 38 张 ORM 表（v2.4 新增 `tuning_knowledge_entry`；v2.3 校正：v2.0 登记 31 张，补齐 6 张）。以下清单以代码中的 `__tablename__` 为事实来源；DDS 后续修订应同步此口径。生产 bootstrap DDL（`db/postgresql/01_schema.sql`）已收敛至全部 38 张，由 `test_schema_convergence.py` 与 `test_production_bootstrap.py` 守护。

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
| 32 | `AlgorithmParameter` | `algorithm_parameter` | `models/algorithm_parameter.py` | 算法参数配置 |
| 33 | `DcsPidStructure` | `dcs_pid_structure` | `models/dcs_pid_structure.py` | DCS PID 结构 |
| 34 | `DiagnosisConfigChange` | `diagnosis_config_change` | `models/diagnosis.py` | 诊断配置变更审计 |
| 35 | `DiagnosisRule` | `diagnosis_rule` | `models/diagnosis.py` | 诊断规则 |
| 36 | `DiagnosisThresholdOverride` | `diagnosis_threshold_override` | `models/diagnosis.py` | 诊断阈值覆盖 |
| 37 | `LoopConfidenceLatest` | `loop_confidence_latest` | `models/loop_confidence.py` | 回路可信度最新值 |
| 38 | `TuningKnowledgeEntry` | `tuning_knowledge_entry` | `models/tuning_knowledge.py` | 整定知识库不可变快照（P3-01） |

注：DDS v4.1 中声明的 `report_schedule` 实际由代码 `report_config` 承载；`sys_role` / `sys_user_role` 代码无对应模型，角色以枚举形式实现。`time_constant` 为 KPI 快照表持久化列但无 MetricCalculator，状态 `NOT_IMPLEMENTED`，NULL 不得显示为 0 或解释为"无数据"（详见契约基线 §7）。

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

### v2.3 变更项（2026-07-29，Phase 0 Truth First）

| 变更项 | v2.2 口径 | v2.3 口径 | 依据 |
|---|---|---|---|
| 整定状态机 | `DRAFT/RUNNING/COMPLETED/ROLLED_BACK` | 固化 `DRAFT→RUNNING→IDENTIFIED→SIMULATED→COMPLETED` + `INCONCLUSIVE`/`ROLLED_BACK`；旧值只读兼容 | 契约基线 §2、`schemas/tuning.py` |
| 模型来源门禁 | 未声明 | `ModelSource`/`ThetaSource`/`DataSource` + A–E 放行规则，服务端 `authorize_tuning_model` 复核 | §6.1、`services/tuning.py` |
| 闭环 IV | Phase 0 降级为 `EXPERIMENTAL` | P2-009 升级为 CLIVC 生产方法（`CLIVC_PRODUCTION_READY`），按可信度门禁放行 | `tuning_identification/iv.py`、`services/tuning.py` |
| ORM 表清单 | 31 张 | 37 张（补 6 张） | §10、`backend/app/models/` |
| 生产 bootstrap | DDL 21 张，stamp head 跳过缺表 | DDL 收敛至 37 张，专用临时 PG 实测 | `db/postgresql/01_schema.sql`、ADR |
| 安全边界 | 未声明静态门禁 | 无 DCS 参数下写端点，静态扫描守护 | §6.2、`test_security_p2.py` |
| `time_constant` | 未声明 | KPI 列 `NOT_IMPLEMENTED`，NULL ≠ 0 ≠ 无数据 | 契约基线 §7 |

### v2.4 变更项（2026-08-05，IA 整改 P3-01）

| 变更项 | v2.3 口径 | v2.4 口径 | 依据 |
|---|---|---|---|
| ORM 表清单 | 37 张 | 38 张（新增 `tuning_knowledge_entry`） | §10、`models/tuning_knowledge.py` |
| 知识库 API | 未声明 | `GET /tuning/knowledge-base`（列表+筛选+分页）、`GET /tuning/knowledge-base/{id}`（详情）、`GET /tuning/knowledge-base/similar`（相似案例 Top N） | `endpoints/tuning.py`、`services/tuning_knowledge.py` |
| `ActionTracker` | 无整定关联 | 新增 `tuning_record_id` 外键（`ondelete=SET NULL`，`idx_action_tracker_tuning_record` 索引） | `models/tracker.py`、迁移 `f1a2b3c4d5e6` |
| `TrackerStatusUpdate` | 无 `tuningRecordId` | 新增 `tuningRecordId` 字段（VERIFYING 时可选，用于知识库生成） | `schemas/diagnosis.py` |
| 知识库生成钩子 | 未声明 | `generate_knowledge_entry`：验证任务 `_verify_single_tracker` 完成后调用，hybrid 关联（`tuning_record_id` 优先 + 时间窗口兜底），幂等 `on_conflict_do_update(tracker_id)` | `services/tuning_knowledge.py`、`tasks/tracker_verification.py` |
| 整定路由 | 5 路由（workbench/model/algorithm/simulation/stats） | 新增第 6 路由 `/tuning/knowledge-base`（整定知识库，权限 `tuning:view`） | `router/routes/modules/tuning.ts` |

### v2.5 变更项（2026-08-06，IA 整改 P3-04 AI 洞察全局赋能）

| 变更项 | v2.4 口径 | v2.5 口径 | 依据 |
|---|---|---|---|
| LLM 配置 API | 未声明 | `GET /configs/llm`（脱敏返回）、`POST /configs/llm`（仅 ADMIN，apiKey 空=保留原值）、`POST /configs/llm/test`（连接测试）；6 个 sys_config 键 `llm.enabled/endpoint/api_key/model/timeout/max_tokens` | `endpoints/llm_config.py`、`schemas/config.py` |
| AI 洞察 API | 未声明 | `POST /ai-insight/{scene}`（4 场景统一入口 diagnosis/performance/tuning/workbench，body `{loopId?, taskId?, mode}`，返回 `{insight, source, model, scene, generatedAt}`）；`GET /ai-insight/scenes` 场景元信息 | `endpoints/ai_insight.py`、`schemas/ai_insight.py` |
| 诊断解读端点 | `POST /diagnosis/{loopId}/interpret` 独立实现 | 内部代理到 `generate_insight(scene="diagnosis")`，字段映射 `insight→interpretation` 向后兼容 | `services/diagnosis_interpretation.py`、`services/ai_insight/service.py` |
| AI 洞察服务 | 未声明 | `SceneStrategy` 抽象基类（`load_context/build_system_prompt/build_user_prompt/generate_template`）+ 4 场景策略实现；`AiInsightContext.knowledgeContext` RAG 扩展点（第一期恒 None，未来从知识库注入） | `services/ai_insight/` |
| LLM 调用层 | `max_tokens` 硬编码 800 | sys_config 可配（`llm.max_tokens` 默认 4096）；`content` 空时 fallback `reasoning_content`（兼容推理模型） | `services/llm_provider.py` |
| 前端组件 | `ClpmInterpretationPanel`（仅诊断） | 新增通用 `ClpmAiInsight`（scene/loopId/taskId/variant/hideWhenDisabled props）；LLM 未启用时按 `hideWhenDisabled` 决定隐藏或显示启用提示；4 场景嵌入（诊断详情第 5 Tab、性能详情综合评估 Tab、整定仿真右侧栏保存后、工作台 KpiStrip 后） | `components/clpm/ai-insight.vue` |
| 文案 | "AI 解读 / 规则模板" | "AI 洞察 / 诊断小结"（来源标签 + 重新生成菜单） | `components/clpm/ai-insight.vue` |

### v2.6 变更项（2026-08-07，IA 重构 Phase A-D 全量合入）

| 变更项 | v2.5 口径 | v2.6 口径 | 依据 |
|---|---|---|---|
| 一级菜单 | 6 模块 + 1 门户 | 7 模块（监控/回路/评估/诊断/整定/配置/系统） | §2、`router/routes/modules/` |
| 双轴导航 | 未声明 | 实体轴（回路工作台）+ 职能轴（评估/诊断/整定） | §3、IA 重构方案 §3.1 |
| 工程/操作分离 | 未声明 | 结构性配置→`/config/*`；操作性调参→业务模块内联 | §3、IA 重构方案 §4.6 |
| 监控模块 | 工作台门户（`/dashboard/workbench`） | 升级为运行驾驶舱，父路由 `/monitor`，含系统概览+回路实时 | §2、`router/routes/modules/monitor.ts` |
| 回路模块 | 回路管理（配置+监控混合） | 实体轴工作台（`/loop/workbench` 单页四区，v2.7）+ 配置拆出到 `/config/loop` | §2、`router/routes/modules/loop.ts` |
| 评估模块 | 性能评估（`/metric/*`） | 改名评估，父路由 `/assess`，leaf 保留 `/metric/*` 绝对路径 | §3、`router/routes/modules/assess.ts` |
| 诊断模块 | 7 页面（总览/任务/记录/可视化/详情/跟踪/配置） | 5 页面 + 任务 Tabs 合并（总览/回路分析/任务/跟踪/详情三区）；配置迁入 `/config/diagnosis` | §2.1 Phase C、`router/routes/modules/diagnosis.ts` |
| 整定模块 | 5 路由（workbench/model/algorithm/simulation/stats） | 4 路由（workbench/detail/knowledge-base/stats）+ 7 条旧路由重定向；detail 单页 4 锚点 | §2.1 Phase D、`router/routes/modules/tuning.ts` |
| 配置模块 | 未声明 | 新增模块，集中 9 项结构性配置（link/tag/loop/datasource/metric/diagnosis + factory/ledger/pid-template 隐藏重定向） | §2、`router/routes/modules/config.ts` |
| 系统模块 | 4 子页（users/audit/permissions/reports） | 5 子页（新增 `llm-config`；`algorithm-params` 重定向到 `/config/metric`） | §2、`router/routes/modules/system.ts` |
| 回路工作台 | 未声明 | v2.6 为 6 Tab（已废弃）；v2.7 重构为单页四区（概览10% + 评估30% + 诊断30% + 整定30%），`fullPathKey: false` 防止 query 变化新建 tab | §2.1 Phase B、`views/loop/workbench.vue` |
| 诊断详情 | 5 层 Tab | 三区重构（结论先行 + 问题定位路径 + 证据折叠）+ 特征字典 20 项 | §2.1 Phase C、`views/diagnosis/detail.vue` |
| 整定向导 | 3 页路由（model→algorithm→simulation） | 单页 `/tuning/detail` + 4 锚点导航（①过程辨识 ②PID推荐 ③闭环仿真 ④方案确认） | §2.1 Phase D、`views/tuning/detail.vue` |
| 嵌入式组件 | 未声明 | `embedded` prop 条件渲染 `<Page>` 外壳 + `v-show` 保状态 + `router.replace` 同步 query | §2.1 Phase D、`views/tuning/{model,algorithm,simulation}.vue` |
| 跨模块上下文 | 未声明 | 统一 `?loopId=`/`?taskId=` query 规范，`useLoopContext()` composable | §2.1 Phase A |
| AI 洞察交互 | 4 处内嵌 `ClpmAiInsight` | 工具栏 AI 图标 + 右侧 overlay 抽屉（480px，≤300ms，遮罩可关）；整定场景下线 | §2.1 Phase A |
| 旧路由兼容 | 部分重定向 | 全量 legacy redirect（Phase A 11 条 + Phase B 1 条 + Phase C 2 条 + Phase D 7 条 = 21 条），`route-compat.spec.ts` 守护 | §2.1、`router/routes/modules/` |
| 后端改动 | — | 零（全方案纯前端） | §2.1、IA 重构方案 §7 |
| 门禁基线 | pytest 3456 / vitest 434 | pytest 3881 / vitest 147 / E2E 79（71 passed） | IA 重构方案 §8 Phase D |
