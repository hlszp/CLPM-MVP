# CLPM Agent Guidance

本项目是危化企业控制回路性能评估与优化平台（CLPM v6.1），7 阶段系统重构已全部完成，文档体系已统一升级至 v6.1（含 ZL 工业设计规范对齐）。

**拆分文档索引**（按需阅读，不必全读）：

| 场景 | 文档 |
|---|---|
| 排障与运维（网络模式切换、worker 挂死、回填性能、断点续传细节） | `docs/过程文档/ops-runbook.md` |
| v6 交付历史追溯（Phase 0-6、各 PR 清单） | `docs/过程文档/v6-delivery-history.md` |
| 引用旧文档前查是否已失效 | `docs/过程文档/stale-docs.md` |
| **UX/IA审查与整改**（信息架构、交互设计、闭环流程评估） | `docs/过程文档/clpm-ux-ia-audit-report-2026-08-05.md` |
| 数据质量评估报告（完整性/准确性/一致性/时效性四维定量分析） | `docs/过程文档/data-quality-assessment-report-2026-08-05.md` |
| KPI计算方法系统性审查（26指标公式+国标准合度+10项缺陷） | `docs/过程文档/kpi-calculation-review-2026-08-05.md` |

## 必读入口

先读：`README.md`（当前共识与目录说明）、`docs/设计文档/00-BASELINE/implementation-contract.md`、`docs/设计文档/CLPM_v4.0_系统重构实施方案.md` 与 `docs/设计文档/01-PRD/PRD.md` v6.1。

PRD v6.1 是产品需求的事实来源；实现契约 v2.4 是重构后 IA/路由/API/权限/状态机/KPI 事实来源；UI/UX v6.1 是视觉与交互输入文件（已对齐 v6.1 代码，含 ZL 工业设计规范）；`CLPM_v4.0_系统重构实施方案.md` 是 7 阶段重构的实施蓝图。

## 当前基线（2026-08-05 修订 — UX/IA审查启动 + 数据健康指标 + 可信度继承修复）

| 类型 | 文件 | 版本 |
|---|---|---|
| 产品需求规范 PRD | `docs/设计文档/01-PRD/PRD.md` | v6.1 |
| 重构后实现契约 | `docs/设计文档/00-BASELINE/implementation-contract.md` | **v2.5**（IA 整改 P3-04：AI 洞察全局赋能/LLM 配置 API/`POST /ai-insight/{scene}` 4 场景统一入口/`ClpmAiInsight` 通用组件/推理模型空输出修复；v2.4 P3-01：整定知识库不可变快照/38 表；v2.3 Phase 0 Truth First：状态机/模型来源门禁/37 表/bootstrap 收敛/安全边界） |
| **v4.0 重构实施方案** | `docs/设计文档/CLPM_v4.0_系统重构实施方案.md` | v1.0（Phase 0-6 全部完成） |
| 功能设计规范 FDS | `docs/设计文档/02-FDS/FDS.md` | v6.0 |
| 应用设计规范 ADS | `docs/设计文档/03-ADS/ADS.md` | v6.0 |
| 数据模型设计 DDS | `docs/设计文档/04-DDS/DDS.md` | v6.0 |
| API 接口设计 IDS | `docs/设计文档/05-IDS/IDS.md` | v6.0 |
| UI/UX 设计规范 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` | **v6.1**（已对齐 v6.1 代码，含 ZL 工业设计规范） |
| 设计基线 | `DESIGN.md` | v3.0（对齐实现契约 v2.3） |
| 原型代码入口 | `docs/设计文档/prototype/README.md` | 已重置为干净基线 |
| 文档索引 | `docs/过程文档/design-documents-index-2026-06-16.md` | v3.0（对齐 v6.0） |
| v6 交付历史 | `docs/过程文档/v6-delivery-history.md` | Phase 0-6 + 后续全部 PR |
| 优化整改计划 | `docs/过程文档/clpm-optimization-review-plan-2026-07-28.md` | v1.0；**六阶段全部完成**（2026-07-28）。后续（07-29）：3 诊断标签误报修复、整定 Phase 2.1 合并+评审返工、生产部署实弹验证（R1-R6，ops-runbook）、诊断中心问题串修复（版本号竞态/散点回退/fusedConfidence 落库/任务时区迁移 `h8b9c0d1e2f3`/任务页已归档开关/刷新轮换幂等窗口/详情页 SPA 导航白屏）；白屏根因与修复见 ops-runbook §【已结】。后续（08-03）：种子数据 v1.4 整合（指标契约/诊断规则/DCS/回路精简，`02_seed_data.sql` 自动加载）、算法参数配置从系统管理迁移至性能评估-指标配置（KPI 算法参数 Tab）、lefthook pre-push 门禁修复（FakeRedis 增强 + conftest 全模块 patch，详见 ops-runbook §lefthook pre-push 门禁修复） |
| **v6.2 可信辨识改造** | `docs/过程文档/clpm-v6.2-product-and-identification-optimization-plan-2026-07-29.md` | **Phase 0 Truth First 已完成**（分支 `codex/v6.2-phase0-truth-first`，2026-07-29）：P0-01~06 安全门禁（盲 fallback/固定 θ/IPDT 错配/低可信放行/闭环 IV 降级/伪 0 风险）+ P0-07/08/10 生产 bootstrap DDL 收敛至 37 表 + P0-039 DCS 下写安全静态门禁；契约基线见 `clpm-v6.2-phase0-contract-baseline-2026-07-29.md`，任务清单见 `clpm-v6.2-implementation-task-checklist-2026-07-29.md`。后端 3456 passed、前端 vitest 434 passed、ruff/alembic/typecheck 全绿。待收口：V62-P0-033 设计文档逐项同步（契约已升 v2.3）、V62-P0-034/037/038 OpenAPI/E2E 基线、Phase 1 数据同轴与 IA 减负 |
| **回路整定 Phase 2 技术方案** | `docs/过程文档/tuning-phase2-technical-plan-2026-07-28.md` | **已执行**（分支 `feat/tuning-phase2`，2026-07-28）：历史数据过程对象辨识（ARX/ARMAX/IV 算法栈）+ 异步任务化 + 多 PID 对比仿真；pytest 2840 全绿、`alembic check` 退出码 0 |
| **UX/IA 专业评估报告** | `docs/过程文档/clpm-ux-ia-audit-report-2026-08-05.md` | v1.0（2026-08-05）：以仪控工程师视角从认知负担/指导辅助/工作流程契合度/闭环管理4维度全面审查，识别P0-P3问题共21项，附分优先级整改路线图（P0:4项/P1:6项/P2:6项/P3:5项）；核心结论：技术闭环已通但用户操作闭环存在断点，重点补齐跨模块跳转、表格信息密度、流程向导、验证环节 |
| 数据质量评估报告 | `docs/过程文档/data-quality-assessment-report-2026-08-05.md` | v1.0（2026-08-05）：27个种子回路33天数据四维定量分析，近7天valid_rate~97%达A级可信度，7/2-7/7全空行需清理，7/22-7/28过密写入需排查 |
| KPI计算方法审查 | `docs/过程文档/kpi-calculation-review-2026-08-05.md` | v1.0（2026-08-05）：26指标+综合评分+节点聚合全公式审查，3核心+1折扣指标完全符合GB/T 44693.2-2024，识别OP量程、饱和度分母、可信度继承等10项关键缺陷 |

## v6.0 核心架构组件

| 组件 | 路径 | 职责 |
|---|---|---|
| DataPlanner | `app/services/data_planner.py` | 统一历史数据读取，按控制类型自动降采样，分发 MetricDataBundle |
| ConfidenceEvaluator | `app/services/confidence_evaluator.py` | 可信度评估 A/B/C/D/E（valid_rate 阈值 95/80/60/20%），INCONCLUSIVE 处理 |
| TaskTracker | `app/services/task_tracker.py` | 任务全生命周期跟踪（create/update_status），Redis 状态存储 + 通知 |
| 预处理 Pipeline | `app/services/preprocessing/` (quality_code/thresholds/outlier_detection/validity_mask/quality_summary/pipeline) | 8 步流水线 + 8 类异常值检测 |
| MetricCalculator | `app/tasks/kpi_calc.py` | 12 个 KPI 指标计算器（3 核心 + 1 综合 + 8 辅助），通过 DataPlanner.request_bundles() 获取数据 |
| 数据完整性检查 | `app/services/data_integrity.py` | 本地 TDengine 宽表完整性检查：按小时分桶对 7 列分别 `COUNT(col)` 统计列级缺失；缺失=无记录或列 NULL，质量码非 Good 但有值不算缺失；首尾不足整点桶按实际秒数算预期点数。API：`POST /loops/data-import/integrity-check` |
| 过程对象辨识算法栈 | `app/services/tuning_identification/` (excitation/nonparametric/arx/armax/iv/order_selection/discrete_to_continuous/pipeline) | 回路整定 Phase 2：基于历史 OP/PV 时序辨识过程对象 G(s)=PV/OP；分层算法栈（激励检测→非参数粗估→ARX/ARMAX/IV 参数化辨识→阶次选择 AIC/BIC→离散→连续转换→可信度评估）；接入 DataPlanner 8 步预处理 + ConfidenceEvaluator A/B/C/D/E 等级 |
| AI 洞察服务 | `app/services/ai_insight/` (context/base/service/scenes/diagnosis/performance/tuning/workbench) + `app/services/llm_provider.py` | P3-04 AI 洞察全局赋能：`SceneStrategy` 抽象基类 + 4 场景策略（诊断/性能/整定/工作台），`POST /ai-insight/{scene}` 统一入口，`mode=auto/llm/template`，LLM 失败自动 fallback 规则模板；`AiInsightContext.knowledgeContext` 为 RAG 扩展点（第一期恒 None）；LLM 配置 6 键存 sys_config（`llm.enabled/endpoint/api_key/model/timeout/max_tokens`），max_tokens 可配修复推理模型空输出；前端通用组件 `ClpmAiInsight`（LLM 未启用时按 hideWhenDisabled 隐藏或显示启用提示），4 场景嵌入 |

## 开发环境运行指南

### 启动服务

```bash
# 1. 基础设施
docker compose -f deploy/docker/docker-compose.dev.yml up -d

# 2. 后端 API (port 7101)
#    v6.1：后端启动时自动启动 Celery Beat 调度进程和 Celery Worker 任务执行进程
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 7101 --reload

# 3. 前端 (port 5666)
cd frontend && pnpm run dev:antd
```

### 测试与验证

```bash
# 后端单元测试
cd backend && uv run pytest -q

# 前端类型检查
cd frontend && pnpm run check:type

# E2E 测试
cd e2e && pnpm exec playwright test
```

### CI 提交前本地检查（提交前必跑，gitea 侧无 CI，本地检查即门禁）

```bash
# backend ruff check + format
cd backend && uv run ruff check . && uv run ruff format --check .

# 自动修复 ruff 问题
cd backend && uv run ruff check . --fix && uv run ruff format .

# schema 漂移检查（退出码必须为 0，结构性漂移即失败）
cd backend && uv run alembic check

# frontend 格式化
cd frontend && pnpm run format
```

> lefthook 已配置 pre-push 自动门禁（ruff + `pytest -x` + check:type，2026-07-28 起），`pnpm install` 后生效。

## 关键注意事项

行为红线（始终遵守）：

- **Celery Worker 和 Beat 随后端自动启动**（v6.1 lifespan）：后端启动时自动拉起 Worker 和 Beat 子进程，无需手动启动；**严禁手工再启动**，多个 worker/beat 并存会导致任务重复消费或双触发
- **后端代码更新后需重启后端**：`uvicorn --reload` 只重载 Python 文件，不会重新执行 lifespan，也不会重启 Worker/Beat 子进程；修改 Celery 任务代码后需重启后端让新代码生效
- **计算类历史数据查询一律本地 TDengine**：`get_provider()` 恒返回 TDengineProvider，禁止计算任务自动降级到远端 API；远端历史接口仅 `data_import.py` 调用。决策记录：`docs/过程文档/data-architecture-decision-local-first-2026-07-20.md`
- **模型变更必须与迁移同批应用**：ORM 改动与 alembic 迁移同批提交，且先应用迁移再让代码进入运行环境（2026-07-21 教训）
- **热路径禁止对 naive datetime 逐点调 `.timestamp()`**（macOS fork 时区慢路径陷阱，背景见 ops-runbook）
- **禁止模块级 asyncio.Lock / Semaphore / Event**：首次竞争即绑定当前事件循环，Celery 每任务新循环后全部抛 "bound to a different event loop"（2026-07-28 全回路 INCONCLUSIVE 事故根因，ops-runbook 已记录；回归测试结构性断言守护）
- **断点续传禁止 overwrite**：gap backfill 复用 `import_history_data` 时必须 `conflict_strategy="skip"`（overwrite 会先 DELETE 误删实时行）；手工导入 overwrite 强制 `tsEnd ≤ now-5min`
- **断点续传配置运行时可调**（2026-08-06）：总开关 `gapBackfillEnabled`（默认**关闭**）与缺口阈值 `gapBackfillMinGapSeconds`（默认 600s=10 分钟）已纳入 `sys_config`，经 UI 链路配置页修改即时生效（订阅器每次触发读 settings，无需重启）；`.env` 中 `GAP_BACKFILL_*` 仅作启动兜底默认值
- **默认账号**：admin / admin123（5 个种子用户详见 README.md）
- **前端端口是 5666**，后端 API 为 7101

排障与背景细节（按需查阅 `docs/过程文档/ops-runbook.md`）：

- worker 静默挂死识别与处置、并发与回填性能、prewarm 废止背景 → ops-runbook §Celery Worker 运维
- 网络模式切换（Tailscale）验证命令、sudoers 免密、lifespan 预载细节 → ops-runbook §网络模式切换
- 实时数据断点续传机制细节 → ops-runbook §数据链路
- 诊断双轨调度细节（事件轨整点 10 分 + 体检轨 0/8/16 点 20 分）→ ops-runbook §诊断调度细节

## 核心决策

| 决策 | 当前口径 |
|---|---|
| 产品定位 | 产品化、工具化的控制回路绩效治理与优化闭环平台，非项目型定制化系统；用户（管理员/工程师）可自助完成配置组态，减少开发团队介入 |
| 模块架构 | 6 模块 + 1 门户：工作台 / 回路管理 / 性能评估 / 诊断中心 / 回路整定 / 系统管理；各业务模块遵循"配置→运行→分析"三态自包含原则，减少跨模块依赖 |
| AAS 数据模型 | AAS 同步 tag 位号（非回路实体）；回路由用户创建并关联 7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D）；PID 参数与控制模式从关联 tag 只读读取；数据质量主要针对 PV 值（Good/Bad/Uncertain 质量码） |
| **数据架构** | **导入走远端、计算全本地**（2026-07-20 定调）：远端 AAS 历史接口仅"数据管理→历史数据导入"手工任务可调用；本地 TDengine 是所有计算任务唯一历史数据源；本地数据不完整按 INCONCLUSIVE 提示，由用户导入补齐；实时数据源唯一为 SignalR Hub。详见 `docs/过程文档/data-architecture-decision-local-first-2026-07-20.md` |
| Action Tracker | 诊断中心子模块（子菜单路由），状态机 PENDING → IN_PROGRESS → IMPLEMENTED/IGNORED，中文显示为待处理/处理中/已实施/已忽略 |
| 统计分析 | 不设独立模块，分散到各业务模块的"分析"态；自动报表归入系统管理 |
| 回路整定 | **Phase 2 已完成**（分支 `feat/tuning-phase2`，2026-07-28）：基于历史数据自动辨识过程对象 G(s)=PV/OP（ARX/ARMAX/IV 算法栈 + DataPlanner + ConfidenceEvaluator）+ 异步任务化 + 多 PID 参数响应对比；保留阶跃实验为兜底路径；仍只输出建议、证据、风险和回退方案，不支持 DCS 参数下写 |
| 技术护城河 | 可信数据 + 可解释诊断 + 可验证整定 + 安全闭环 + 规模化交付 |
| 安全边界 | 平台不直接修改 DCS 的 P/I/D 参数，只输出建议、证据、风险和回退方案；参数由授权人员人工实施并留痕 |
| 首版主线 | Phase 1 (MVP/V1.0)：跑通"自动评估、自动诊断、轻量跟踪"闭环 |
| 原型/前端开发 | 当前生产前端为 Vue 3 + Vite + TypeScript + vue-vben-admin；重构后路由/页面以 `docs/设计文档/00-BASELINE/implementation-contract.md` 为准 |
| 性能边界 | LTTB 降采样 maxPoints=2000，30 天时间窗口 |
| 网络模式 | 应用层局域网/公网切换（2026-07-19）：**仅切换网络链路（Tailscale subnet router 透明转发），与数据源选择无关**；sys_config 为配置真相源，.env 已移除业务 URL/Token。细节见 ops-runbook §网络模式切换 |
| 远端仓库 | **gitea 为主远端**（remote 名 `origin`，`https://gitea.zlinfot.xyz:2087/zp/CLPM`）；GitHub 为镜像（remote 名 `github`，`hlszp/CLPM`），main 合并后 `git push github main` 同步 |
| 文档权威性 | PRD v6.1 负责产品需求；实现契约 v2.4 负责重构后 IA/路由/API/权限/状态机/KPI；UI/UX v6.1 负责视觉与交互；v4.0 重构实施方案负责 7 阶段实施蓝图 |

## Git 工作流

- **远端**：`origin` = gitea（主），`github` = GitHub（镜像）；main 跟踪 `origin/main`
- **提交**：Conventional Commits `<type>(<scope>): <subject>`，subject ≤50 字符祈使句，body 解释"为什么"，按逻辑单元拆分，单 commit ≤500 行
- **日常开发**：可直接在 main 上小步提交并 `git push origin main`；大改动（>500 行或 DB schema/架构变更）建议开 `<type>/<简述>` 分支
- **PR**：无需在 gitea 网页端手工发起——对话中显式提出 PR 要求时，agent 直接通过 gitea API 创建并合并（token 在 origin remote URL 中），合并后同步镜像 `git push github main`
- **红线**：禁止 `git push --force` 共享分支；禁止 `git reset --hard` 后推送共享分支
- **CI 现状**：gitea 侧无 CI，以提交前本地检查（ruff + pytest + check:type）为门禁；GitHub Actions 仅在镜像侧运行（当前账户欠费停用，2026-07-21）

## 下阶段规则

| 方向 | 先读 | 关注点 |
|---|---|---|
| **IA/UX 信息架构整改（当前）** | `docs/过程文档/clpm-ux-ia-audit-report-2026-08-05.md` | **P0紧急（1-2周）**：异常跟踪表格扩列（诊断标签/严重度/可信度/发现时间）、跨模块一键跳转、整定上下文传递、Action Tracker增加"验证中"状态+实施记录强制填写；**P1重要（3-4周）**：回路配置向导化（5步进度条）、引导式空状态、专业术语Tooltip、性能看板网格布局、数据链路状态常驻工作台、单回路处置时间线；**P2体验（5-8周）**：结构化诊断报告、A/B对比自动引导、Onboarding引导、表格列配置、全局刷新状态指示；遵循ZL工业设计规范（Calm UI/Poka-Yoke/Glanceability/数据墨水比），不破坏现有API/数据库结构（纯前端+小量后端接口增强） |
| Bug 修复 / 功能增强 | README.md → AGENTS.md → 相关设计文档 → 对应代码 | 遵循"问题定位-修复实施-测试验证-效果确认"闭环流程 |
| 回路整定 Phase 2 后续 | `docs/过程文档/tuning-phase2-technical-plan-2026-07-28.md` | **Phase 2.0-2.5 已完成**（分支 `feat/tuning-phase2`，2026-07-28）：算法栈 + DataPlanner 接入 + 异步任务化 + 多 PID 对比 + 前端重构 + 全量门禁通过；待合并 main 后更新设计文档（PRD/FDS/ADS/IDS/契约 版本号升级）+ GB/T 44693.2 整定用例验证 |
| 诊断整改 Phase C/D/E | `docs/过程文档/diagnosis-module-review-rectification-plan-2026-07-19.md` §5 | Phase A/B 已合并（2026-07-20）；**Batch 4-6 已完成**（F1-F7 回路分析+路径修复、D1-D6 管理闭环+入口整合，2026-07-27）；**Batch 5 页面优化（F8-F13）已完成**（含 P0-P2 专项治理 + E2E/单测修复，2026-07-28，commit `8fc3a2d1`）；E 规范符合性（GB/T 44693.2 用例验证 ≥90%）待启动 |
| E2E 测试补充 | `e2e/` 目录 → UI/UX v6.1 → v6.1 新增页面 | **全量 E2E 55/55 通过**（2026-07-28）：performance/confidence/metric-tasks 已对齐 772d99a0 重构后路由；后续按 UI/UX v6.1 新增页面逐步补 |
| 生产部署 | `docker-compose.prod.yml` → `.env.prod.example` → `deploy/deploy.sh` | Celery worker 容器需验证 include 参数生效 |
| 新功能开发 | PRD v6.1 → 实现契约 v2.4 → v4.0 重构实施方案 → 对应设计文档 | 遵循模块"配置→运行→分析"三态自包含原则 |
| 网络模式切换后续改进 | ops-runbook §网络模式切换 | 仅余 ③ 公网模式 ping 延迟抖动优化（低优先级） |

## Stale docs 防护

引用任何旧文档前，先对照 `docs/过程文档/stale-docs.md`——其中所列文件（archive/、归档文档、v0.1/v2.x/v6.0 前各版本）只用于历史追溯，**不是现行需求输入**。

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
