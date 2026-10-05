# CLPM Agent Guidance

> **分工（2026-09-27 用户口径）**：**iCLPM**（`/Users/zhangping/DEV/iCLPM`，远端 `https://github.com/hlszp/iCLPM`）专注**智能体协同层产品（iCLPMagent）**；**CLPM 本体系统（本仓库 CLPM-MVP）的功能开发与缺陷修复仍在本项目进行**，本册（AGENTS.md）是本仓的现行约定与事实来源。两仓关系：iCLPM 继承本仓口径，本册不因 iCLPM 存在而降级为归档说明。

## ⚠️ MVP 覆盖说明（优先级最高）

本仓库是 **CLPM-MVP**（自原 CLPM v6.2 派生的精简 + 闭环重建版），不是原 CLPM 项目。

**现行事实来源**：`docs/MVP设计/`（00~12 设计与实施文档 + README 索引）。MVP 差异要点：

- **模块现状**：闭环六模块 + 管理层视图 + 工作台 v2.0 已落地；路由模块清单：`clpm`（运维工作台）+ `monitor/assess/diagnosis/tuning/handling/reports/alert/config/system/task/loop`；导航顺序：运维工作台(0)-监控-评估-诊断-整定-处置-报告-配置-系统；**工作台规整（2026-10-04 用户裁决 D1~D4，方案 `docs/设计文档/工作台规整方案-2026-10-04.md`）**：全局工作台更名**运维工作台**（D4）；诊断工作台并入回路工作台诊断剖面不保留（D2，诊断三页式=概览/记录/任务，`/diagnosis/workbench` redirect：带 loopId → `/loop/workbench360?loopId=&section=diagnosis`、无 → 概览；「仅可诊断」过滤+预检徽标筛选迁入诊断概览）；整定工作台转型**整定总览** `/tuning/overview`（D3，四锚点流程移回路工作台整定剖面，行操作带 L0/L1 门禁跳转，`/tuning/workbench` redirect）；操作角色统一为回路工作台剖面四角色 ADMIN/IC/PE/EXPERT（D1：后端 `_DIAGNOSIS_TRIGGER_ROLES`+EXPERT、tuning `require_roles`+PE_ENGINEER；模块查看页全 5 角色；SPONSOR 只读）；回路工作台新增 `?section=` 剖面直达协议（一次性消费）；现行形态：**运维工作台**（`/workbench` 单屏 5 Tab：**系统总览/性能评估/回路诊断/参数整定/问题处置**——文案以 `frontend/apps/web-antd/src/views/workbench/index.vue:57-62` 为准，order=0 全角色可见，方案见 `docs/设计文档/CLPM工作台改进方案-v2.0.md`）/ 监控菜单重排为装置总览→回路监视→预警事件→关注队列→回路工作台（列表页标杆 v2.0）/ 诊断三页式 / 整定三页式（总览/记录/验证）/ 处置 v2.0 双实体（loop_action_item + handling_order）/ 统计报告一级菜单（order=6，配置→7、系统→8）/ 模块热插拔（诊断/整定/处置可弹性启用禁用）/ 适用性评估 L0~L4（**2026-10-05 三性裁决：可评估性仅 L0 限制（L1 取消）；可诊断性全档位放行——L0 改警告放行产出正式 DATA_INSUFFICIENT 结论，夜间定时全量仍滤 L0；可整定性 L0/L1 阻断 ERR_TUNING_FITNESS_INSUFFICIENT、L2/L3 提示放行**；三性判定条件说明弹窗=components/clpm/fitness-rules-modal，诊断概览/整定总览入口）/ 系统管理含基础信息+字典管理（MEASURE_TYPE/TAG_TYPE/LOOP_TYPE）+ 模块管理页；IA 细节以 `docs/MVP设计/` 为准，演进历史见 `docs/过程文档/agents-md-history-2026-08-24.md`（按需读取）
- **纪律**：**不删除诊断/整定专属前后端文件**；构建闭环而非屏蔽闭环
- **端口**：后端 API **17101**、前端 **15666**、mock 数据服务 **17106**（原端口 +10000 隔离）；开发容器 `clpm-mvp-*`；生产 compose 仍为原项目口径（隔离改造未执行）
- **远端仓库**：`github` = `https://github.com/hlszp/CLPM-MVP`（**唯一可推送目标**）；`origin` = 原 CLPM gitea（**pushurl 已锁死 DISABLE_PUSH_TO_UPSTREAM，严禁推送**）
- **CI**：GitHub Actions 已启用且通过（Backend：ruff/format/pytest+coverage；Frontend：eslint apps/web-antd/**vitest 全量**/typecheck/build/E2E）。注意 `@vben/web-antd` 包无 lint 脚本，Lint 用 `pnpm exec eslint apps/web-antd --cache`；前端单测本地跑 `cd frontend && pnpm run test:unit`（workspace 全量，2026-08-28 起为 CI 阻塞门禁）
- **已知残留**：存在已知残留（部分聚合 service stub 化等），详见 `docs/MVP设计/README.md` §已知残留；monitor_attention 的 TRACKER/VERIFICATION 来源**已收口**（2026-08-28：HANDLING 来源功能性替代，不再按原样恢复）；诊断双引擎分裂已收口（2026-08-27，14 号文 A1~A4 全落地：工作台 A-03/总览统计迁 `diagnosis_run`，旧引擎唯一活跃写入口 `/algorithms/diagnosis/analyze` 已解除注册退役，旧读方全部为死代码链登记不删，`diagnosis_tag`/`diagnosis_result` 表按 D4=a 保留归档）
- **版本锁定**：当前 **v7.0.0**（2026-08-28 部署前锁定，annotated tag）；已知残留/冗余代码登记/技术基线见根 `CHANGELOG.md`；冗余代码保留待下周期集中清理
- **CLPM-engine/ 目录**：已加入 .gitignore，独立管理不入库

## 历史基线（v6.2 归档，按需读取）

仅当任务涉及 v6.2 架构溯源、历史交付核对时读取 `docs/历史基线/AGENTS-v6.2-archive.md`；仅在用户显式指令或重大架构变更时更新。根目录 `DESIGN.md` 为 active-baseline（v3.1）：视觉/布局/组件/状态机横切设计约束；IA 与菜单口径以 `docs/MVP设计/00-信息架构.md` 为准。

## 开发环境运行指南

### 启动服务

```bash
# 1. 基础设施
docker compose -f deploy/docker/docker-compose.dev.yml up -d

# 2. 后端 API (port 17101，MVP 隔离端口)
#    后端启动时自动启动 Celery Beat 调度进程和 Celery Worker 任务执行进程
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 17101 --reload

# 3. 前端 (port 15666，MVP 隔离端口)
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

### CI 提交前本地检查（提交前必跑，本地检查即门禁）

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

> lefthook 已配置 pre-push 自动门禁（ruff + `pytest -x` + `alembic check` schema 漂移 + check:type），`pnpm install` 后生效；本地数据库不可用时该步以连接错误中止推送，不静默跳过。

## 关键注意事项

行为红线（始终遵守）：

- **Celery Worker 和 Beat 随后端自动启动**（lifespan）：后端启动时自动拉起 Worker 和 Beat 子进程，无需手动启动；**严禁手工再启动**，多个 worker/beat 并存会导致任务重复消费或双触发
- **后端代码更新后需重启后端**：`uvicorn --reload` 只重载 Python 文件，不会重新执行 lifespan，也不会重启 Worker/Beat 子进程；修改 Celery 任务代码后需重启后端让新代码生效
- **计算类历史数据查询一律本地 TDengine**：`get_provider()` 恒返回 TDengineProvider，禁止计算任务自动降级到远端 API；远端历史接口仅 `data_import.py` 调用
- **模型变更必须与迁移同批应用**：ORM 改动与 alembic 迁移同批提交，且先应用迁移再让代码进入运行环境
- **热路径禁止对 naive datetime 逐点调 `.timestamp()`**
- **禁止模块级 asyncio.Lock / Semaphore / Event**：首次竞争即绑定当前事件循环，Celery 每任务新循环后全部抛 "bound to a different event loop"；回归测试结构性断言守护
- **断点续传禁止 overwrite**：gap backfill 复用 `import_history_data` 时必须 `conflict_strategy="skip"`（overwrite 会先 DELETE 误删实时行）；手工导入 overwrite 强制 `tsEnd ≤ now-5min`
- **断点续传配置运行时可调**：总开关 `gapBackfillEnabled`（默认**关闭**）与缺口阈值 `gapBackfillMinGapSeconds`（默认 600s=10 分钟）已纳入 `sys_config`，经 UI 链路配置页修改即时生效；`.env` 中 `GAP_BACKFILL_*` 仅作启动兜底默认值
- **默认账号**：admin / admin123（5 个种子用户详见 README.md）
- **前端端口是 15666**，后端 API 为 17101（MVP 隔离端口，见顶部 MVP 覆盖说明）
- **下钻契约（2026-09-29 定稿）**：任何统计卡/看板/断言黄框的下钻入口，目标页**必须消费全部携带参数**（status/时间窗/装置 scope 等）并在筛选区回显且可单独清除；新增下钻入口时同步验收"点过去数字必须对得上"，禁止静默丢弃 query 参数（工作台处置 Tab 曾因此整批断链）
- **诚实化原则（2026-09-29 定稿）**：禁止演示/编造数据渲染给用户；静默截断（分页上限、条数截断）、静默降级（聚合单来源失败按空处理）、恒真状态徽章一律禁止——必须 UI 显式提示（"仅当前页 N 条（共 M 条）""该来源暂不可用"）或移除；任何"看起来有数据其实是假的"展示都是产品事故

排障与背景细节（按需查阅 `docs/过程文档/ops-runbook.md`）：

- worker 静默挂死识别与处置、并发与回填性能、prewarm 废止背景 → ops-runbook §Celery Worker 运维
- 网络模式切换（Tailscale）验证命令、sudoers 免密、lifespan 预载细节 → ops-runbook §网络模式切换
- 实时数据断点续传机制细节 → ops-runbook §数据链路
- 诊断调度细节（**v2 三层触发现行：手动 + 定时 daily/weekly + 预警事件**，16 号文 §12，2026-10-01 裁决确认；08-07 停用的是旧引擎 Beat）→ ops-runbook §诊断调度细节
- **uvicorn 静默挂死排查** → ops-runbook §uvicorn 静默挂死排查
- **生产部署与 TDengine 口令/初始化**（compose 必须带 `--env-file .env.prod` + `--profile tdengine`；口令三处一致；`.td-password-changed` 首次/非首次差异；五闸门判读顺序；建表 DDL 挂载缺口）→ ops-runbook §生产部署与 TDengine 口令/初始化（2026-09-27 现场事故沉淀）

## 核心决策

| 决策 | 当前口径 |
|---|---|
| 产品定位 | 产品化、工具化的控制回路绩效治理与优化闭环平台，非项目型定制化系统；用户（管理员/工程师）可自助完成配置组态，减少开发团队介入 |
| AAS 数据模型 | AAS 同步 tag 位号（非回路实体）；回路由用户创建并关联 7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D）；PID 参数与控制模式从关联 tag 只读读取；数据质量主要针对 PV 值（Good/Bad/Uncertain 质量码） |
| **数据架构** | **导入走远端、计算全本地**：远端 AAS 历史接口仅"数据管理→历史数据导入"手工任务可调用；本地 TDengine 是所有计算任务唯一历史数据源；本地数据不完整按 INCONCLUSIVE 提示，由用户导入补齐；实时数据源唯一为 SignalR Hub。详见 `docs/过程文档/data-architecture-decision-local-first-2026-07-20.md` |
| 技术护城河 | 可信数据 + 可解释诊断 + 可验证整定 + 安全闭环 + 规模化交付 |
| 安全边界 | 平台不直接修改 DCS 的 P/I/D 参数，只输出建议、证据、风险和回退方案；参数由授权人员人工实施并留痕 |
| 原型/前端开发 | 当前生产前端为 Vue 3 + Vite + TypeScript + vue-vben-admin；MVP 路由/页面以 `docs/MVP设计/` 为准 |
| 性能边界 | LTTB 降采样 maxPoints=2000，30 天时间窗口 |
| 网络模式 | 应用层局域网/公网切换：**仅切换网络链路（Tailscale subnet router 透明转发），与数据源选择无关**；sys_config 为配置真相源，.env 已移除业务 URL/Token。细节见 ops-runbook §网络模式切换 |

## Git 工作流（MVP 口径）

- **远端**：`github` = `https://github.com/hlszp/CLPM-MVP`（**主远端，唯一可推送**）；`origin` = 原 CLPM gitea（pushurl 锁死 `DISABLE_PUSH_TO_UPSTREAM`，**严禁任何推送**）；main 跟踪 `github/main`
- **提交**：Conventional Commits `<type>(<scope>): <subject>`，subject ≤50 字符祈使句，body 解释"为什么"，按逻辑单元拆分，单 commit ≤500 行
- **日常开发**：可直接在 main 上小步提交并 `git push github main`（双机并行期例外，见下条）；大改动（>500 行或 DB schema/架构变更）建议开 `<type>/<简述>` 分支
- **双机分支策略**：macbook 机在 `macbook` 分支开发、zpdev 机在 `zpdev` 分支开发，各自 `git push -u github <分支>` 备份；**仅在用户显式要求时**才合并回 main（`--no-ff`）；允许并建议定期把 main 合入各自分支保鲜（main→分支方向不受限）；DB 迁移/种子数据变更尽量集中单机，避免 alembic 多 head 冲突；两机开发环境各自独立（工作区+容器+数据卷），互不干扰
- **红线**：禁止 `git push --force` 共享分支；禁止 `git reset --hard` 后推送共享分支；**禁止对原项目（origin）做任何提交动作**；**提交/推送/CI 仅在用户显式要求时执行**——小改动完成后直接报告结果，不主动提交，不同步等待 CI（报告"已触发"即可）
- **CI 现状**：GitHub Actions 已启用且通过（`.github/workflows/ci.yml`，push/PR 触发 main/develop）；Backend（ruff check + format + pytest --cov 60% 门槛，Redis service 容器）/ Frontend（eslint apps/web-antd + typecheck + build + E2E 非阻塞）；提交前本地检查（ruff + pytest + check:type）仍是第一道门禁

## Stale docs 防护

引用任何旧文档前，先对照 `docs/过程文档/stale-docs.md`——其中所列文件（archive/、归档文档、v0.1/v2.x/v6.0 前各版本）只用于历史追溯，**不是现行需求输入**。

## 回路工作台新版（2026-10-02 起，现行开发主线）

以回路为对象的单页全生命周期工作台（监视-评估-诊断-整定-处置，零跳转全内嵌）正在 `zp` 分支开发，验收通过后合 main。**事实源优先级：原型 > 设计方案 v3 > DESIGN.md**，三者冲突以原型为准并回写变更记录：

- 原型（交互与视觉事实源，冻结基准，改动须走其变更记录）：`docs/设计文档/原型/回路工作台-原型-2026-10-02.html`
- 设计方案 v3（结构与行为事实源，含裁决记录 D1–D21、实现规格、P1–P4 计划）：`docs/设计文档/回路工作台-设计方案-2026-10-02.md`
- API 对接契约（含后端缺口 G1–G4 核实结论）：`docs/设计文档/回路工作台-API对接契约-2026-10-02.md`
- 实施任务书（P1–P4 阶段提示词，按分阶段工作流骨架）：`docs/设计文档/回路工作台-实施任务书-2026-10-02.md`
- **多智能体协调机制（子任务会话启动必读）**：`docs/设计文档/回路工作台-协调机制-2026-10-02.md`——子分支纪律（`zp-p<N>-<slug>` 从 zp 切出、禁自行合并、禁碰 main）、30 分钟检查点与移交文件、验收与合并由主协调会话承担；状态看板在 `docs/设计文档/回路工作台-任务状态/`（gitignored 本地瞬态）

红线：新代码只进 `views/loop/workbench360/`；**旧页面（loop/workbench.vue、metric/*、diagnosis/*、tuning/*、handling/*）一律禁改**（唯一例外：P2 从 loop-performance 抽取详情抽屉组件，原页面行为不变）；整定四步复用 `use-tuning-workbench`、诊断结论复用 `diagnosis-result-panel`，禁分叉第二套实现；新路由 `/loop/workbench360`（旧路由与旧菜单暂留，用户验收后另行裁决隐藏/删除）；后端缺口 G1（fitness 出口）/G2（评估历史 custom 来源）未落定前对应 UI 按缺数据显式提示，禁用演示数据。

## 分阶段实施工作流（2026-08-24 沉淀）

凡"有书面实施方案、按阶段推进、每阶段有验收清单"的阶段型任务（IA 优化 P0–P4 已重复 5 次的程序），**新阶段启动时必须**按 `docs/过程文档/staged-implementation-workflow-2026-08-24.md` 的骨架模板生成阶段任务提示词（必读清单 → 阶段范围 → 执行纪律 → 完成度核验问题 → 人工决策点），并以该文档 §3 第 4 节核验清单作为阶段晋级门槛。是否升级为可自动路由的项目级 Skill/Command 由用户显式授权，未经确认不创建新资产。

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
