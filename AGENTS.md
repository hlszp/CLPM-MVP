# CLPM Agent Guidance

本项目是危化企业控制回路性能评估与优化平台（CLPM v6.1），7 阶段系统重构已全部完成，文档体系已统一升级至 v6.1（含 ZL 工业设计规范对齐）。

**拆分文档索引**（按需阅读，不必全读）：

| 场景 | 文档 |
|---|---|
| 排障与运维（网络模式切换、worker 挂死、回填性能、断点续传细节） | `docs/过程文档/ops-runbook.md` |
| v6 交付历史追溯（Phase 0-6、各 PR 清单） | `docs/过程文档/v6-delivery-history.md` |
| 引用旧文档前查是否已失效 | `docs/过程文档/stale-docs.md` |

## 必读入口

先读：`README.md`（当前共识与目录说明）、`docs/设计文档/00-BASELINE/implementation-contract.md`、`docs/设计文档/CLPM_v4.0_系统重构实施方案.md` 与 `docs/设计文档/01-PRD/PRD.md` v6.1。

PRD v6.1 是产品需求的事实来源；实现契约 v2.0 是重构后 IA/路由/API/权限/状态机/KPI 事实来源；UI/UX v6.1 是视觉与交互输入文件（已对齐 v6.1 代码，含 ZL 工业设计规范）；`CLPM_v4.0_系统重构实施方案.md` 是 7 阶段重构的实施蓝图。

## 当前基线（2026-07-21 修订 — AGENTS.md 瘦身拆分）

| 类型 | 文件 | 版本 |
|---|---|---|
| 产品需求规范 PRD | `docs/设计文档/01-PRD/PRD.md` | v6.1 |
| 重构后实现契约 | `docs/设计文档/00-BASELINE/implementation-contract.md` | v2.0 |
| **v4.0 重构实施方案** | `docs/设计文档/CLPM_v4.0_系统重构实施方案.md` | v1.0（Phase 0-6 全部完成） |
| 功能设计规范 FDS | `docs/设计文档/02-FDS/FDS.md` | v6.0 |
| 应用设计规范 ADS | `docs/设计文档/03-ADS/ADS.md` | v6.0 |
| 数据模型设计 DDS | `docs/设计文档/04-DDS/DDS.md` | v6.0 |
| API 接口设计 IDS | `docs/设计文档/05-IDS/IDS.md` | v6.0 |
| UI/UX 设计规范 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` | **v6.1**（已对齐 v6.1 代码，含 ZL 工业设计规范） |
| 设计基线 | `DESIGN.md` | v3.0（对齐实现契约 v2.0） |
| 原型代码入口 | `docs/设计文档/prototype/README.md` | 已重置为干净基线 |
| 文档索引 | `docs/过程文档/design-documents-index-2026-06-16.md` | v3.0（对齐 v6.0） |
| v6 交付历史 | `docs/过程文档/v6-delivery-history.md` | Phase 0-6 + 后续全部 PR |

## v6.0 核心架构组件

| 组件 | 路径 | 职责 |
|---|---|---|
| DataPlanner | `app/services/data_planner.py` | 统一历史数据读取，按控制类型自动降采样，分发 MetricDataBundle |
| ConfidenceEvaluator | `app/services/confidence_evaluator.py` | 可信度评估 A/B/C/D/E（valid_rate 阈值 95/80/60/20%），INCONCLUSIVE 处理 |
| TaskTracker | `app/services/task_tracker.py` | 任务全生命周期跟踪（create/update_status），Redis 状态存储 + 通知 |
| 预处理 Pipeline | `app/services/preprocessing/` (quality_code/thresholds/outlier_detection/validity_mask/quality_summary/pipeline) | 8 步流水线 + 8 类异常值检测 |
| MetricCalculator | `app/tasks/kpi_calc.py` | 12 个 KPI 指标计算器（3 核心 + 1 综合 + 8 辅助），通过 DataPlanner.request_bundles() 获取数据 |
| 数据完整性检查 | `app/services/data_integrity.py` | 本地 TDengine 宽表完整性检查：按小时分桶对 7 列分别 `COUNT(col)` 统计列级缺失；缺失=无记录或列 NULL，质量码非 Good 但有值不算缺失；首尾不足整点桶按实际秒数算预期点数。API：`POST /loops/data-import/integrity-check` |

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

# frontend 格式化
cd frontend && pnpm run format
```

## 关键注意事项

行为红线（始终遵守）：

- **Celery Worker 和 Beat 随后端自动启动**（v6.1 lifespan）：后端启动时自动拉起 Worker 和 Beat 子进程，无需手动启动；**严禁手工再启动**，多个 worker/beat 并存会导致任务重复消费或双触发
- **后端代码更新后需重启后端**：`uvicorn --reload` 只重载 Python 文件，不会重新执行 lifespan，也不会重启 Worker/Beat 子进程；修改 Celery 任务代码后需重启后端让新代码生效
- **计算类历史数据查询一律本地 TDengine**：`get_provider()` 恒返回 TDengineProvider，禁止计算任务自动降级到远端 API；远端历史接口仅 `data_import.py` 调用。决策记录：`docs/过程文档/data-architecture-decision-local-first-2026-07-20.md`
- **模型变更必须与迁移同批应用**：ORM 改动与 alembic 迁移同批提交，且先应用迁移再让代码进入运行环境（2026-07-21 教训）
- **热路径禁止对 naive datetime 逐点调 `.timestamp()`**（macOS fork 时区慢路径陷阱，背景见 ops-runbook）
- **断点续传禁止 overwrite**：gap backfill 复用 `import_history_data` 时必须 `conflict_strategy="skip"`（overwrite 会先 DELETE 误删实时行）
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
| 回路整定 | Phase 1 保留页面与实验/辅助接口，只输出建议、证据、风险和回退方案；不支持 DCS 参数下写，Phase 2 再完成生产级算法闭环 |
| 技术护城河 | 可信数据 + 可解释诊断 + 可验证整定 + 安全闭环 + 规模化交付 |
| 安全边界 | 平台不直接修改 DCS 的 P/I/D 参数，只输出建议、证据、风险和回退方案；参数由授权人员人工实施并留痕 |
| 首版主线 | Phase 1 (MVP/V1.0)：跑通"自动评估、自动诊断、轻量跟踪"闭环 |
| 原型/前端开发 | 当前生产前端为 Vue 3 + Vite + TypeScript + vue-vben-admin；重构后路由/页面以 `docs/设计文档/00-BASELINE/implementation-contract.md` 为准 |
| 性能边界 | LTTB 降采样 maxPoints=2000，30 天时间窗口 |
| 网络模式 | 应用层局域网/公网切换（2026-07-19）：**仅切换网络链路（Tailscale subnet router 透明转发），与数据源选择无关**；sys_config 为配置真相源，.env 已移除业务 URL/Token。细节见 ops-runbook §网络模式切换 |
| 远端仓库 | **gitea 为主远端**（remote 名 `origin`，`https://gitea.zlinfot.xyz:2087/zp/CLPM`）；GitHub 为镜像（remote 名 `github`，`hlszp/CLPM`），main 合并后 `git push github main` 同步 |
| 文档权威性 | PRD v6.1 负责产品需求；实现契约 v2.0 负责重构后 IA/路由/API/权限/状态机/KPI；UI/UX v6.1 负责视觉与交互；v4.0 重构实施方案负责 7 阶段实施蓝图 |

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
| Bug 修复 / 功能增强 | README.md → AGENTS.md → 相关设计文档 → 对应代码 | 遵循"问题定位-修复实施-测试验证-效果确认"闭环流程 |
| 诊断整改 Phase C/D/E | `docs/过程文档/diagnosis-module-review-rectification-plan-2026-07-19.md` §5 | Phase A/B 已合并（2026-07-20）；C 自助组态、D 管理闭环、E 规范符合性（GB/T 44693.2 用例验证 ≥90%） |
| E2E 测试补充 | `e2e/` 目录 → UI/UX v6.1 → v6.1 新增页面 | 任务管理/可信度徽章/INCONCLUSIVE 已补（PR #78）；其余页面按 UI/UX v6.1 逐步补 |
| 生产部署 | `docker-compose.prod.yml` → `.env.prod.example` → `deploy/deploy.sh` | Celery worker 容器需验证 include 参数生效 |
| 新功能开发 | PRD v6.1 → 实现契约 v2.0 → v4.0 重构实施方案 → 对应设计文档 | 遵循模块"配置→运行→分析"三态自包含原则 |
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
