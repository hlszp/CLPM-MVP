# CLPM Agent Guidance

## ⚠️ MVP 覆盖说明（2026-08-20，优先级最高）

本仓库是 **CLPM-MVP**（自原 CLPM v6.2 派生的精简 + 闭环重建版），不是原 CLPM 项目。

**现行事实来源**：`docs/MVP设计/`（01~11 设计与实施文档 + README 索引）。MVP 差异要点：

- **模块现状**："监控 → 评估 → 诊断 → 整定 → 处置"完整闭环已重建（2026-08-19）：诊断两页式（07 方案，2026-08-16）/ 整定三页式（09 方案恢复一级模块，2026-08-19）/ 处置已升 **v2.0 双实体**（08 处置方案：loop_action_item 收敛为建议实体 + 新建 handling_order 处置工单表，2026-08-20）；前端路由模块 `monitor/assess/diagnosis/tuning/handling/alert/config/system/task/loop`，左侧导航按闭环顺序排列（监控-评估-诊断-整定-处置-配置-系统，2026-08-22）；系统管理含可配置字典管理页（MEASURE_TYPE/TAG_TYPE/LOOP_TYPE 三类字典，2026-08-21）
- **纪律**：**不删除诊断/整定专属前后端文件**；构建闭环而非屏蔽闭环
- **端口**：后端 API **17101**、前端 **15666**、mock 数据服务 **17106**（原端口 +10000 隔离）；开发容器 `clpm-mvp-*`；生产 compose 仍为原项目口径（隔离改造未执行）
- **远端仓库**：`github` = `https://github.com/hlszp/CLPM-MVP`（**唯一可推送目标**）；`origin` = 原 CLPM gitea（**pushurl 已锁死 DISABLE_PUSH_TO_UPSTREAM，严禁推送**）
- **CI**：GitHub Actions 已启用且通过（Backend：ruff/format/pytest+coverage；Frontend：eslint apps/web-antd/typecheck/build/E2E）。注意 `@vben/web-antd` 包无 lint 脚本，Lint 用 `pnpm exec eslint apps/web-antd --cache`（限定应用代码，避免扫描 vben 框架包）
- **已知残留**：精简阶段 5 个聚合 service stub 化（monitor_attention 关注队列三来源 / workbench_summary 诊断/整定/tracker 摘要恒 None / dashboard 与 anomaly_prediction 计数恒零），部分已被新 API 路径绕过；是否恢复 monitor_attention 的 TRACKER/VERIFICATION 来源待人工决策（详见 `docs/MVP设计/README.md` §已知残留）
- **CLPM-engine/ 目录**：已加入 .gitignore，独立管理不入库

## 历史基线（v6.2 归档，按需读取）

原 CLPM v6.2 的文档基线、核心架构组件、历史决策与下阶段规则已迁至 **`docs/历史基线/AGENTS-v6.2-archive.md`**。**读取时机**：仅当任务涉及 v6.2 架构溯源、历史交付核对、基线文档版本核对时读取，日常会话不加载。**修改纪律**：仅在用户显式指令或重大架构变更（如基线文档版本升级）时更新，日常不维护。

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

> lefthook 已配置 pre-push 自动门禁（ruff + `pytest -x` + check:type，2026-07-28 起），`pnpm install` 后生效。

## 关键注意事项

行为红线（始终遵守）：

- **Celery Worker 和 Beat 随后端自动启动**（lifespan）：后端启动时自动拉起 Worker 和 Beat 子进程，无需手动启动；**严禁手工再启动**，多个 worker/beat 并存会导致任务重复消费或双触发
- **后端代码更新后需重启后端**：`uvicorn --reload` 只重载 Python 文件，不会重新执行 lifespan，也不会重启 Worker/Beat 子进程；修改 Celery 任务代码后需重启后端让新代码生效
- **计算类历史数据查询一律本地 TDengine**：`get_provider()` 恒返回 TDengineProvider，禁止计算任务自动降级到远端 API；远端历史接口仅 `data_import.py` 调用。决策记录：`docs/过程文档/data-architecture-decision-local-first-2026-07-20.md`
- **模型变更必须与迁移同批应用**：ORM 改动与 alembic 迁移同批提交，且先应用迁移再让代码进入运行环境（2026-07-21 教训）
- **热路径禁止对 naive datetime 逐点调 `.timestamp()`**（macOS fork 时区慢路径陷阱，背景见 ops-runbook）
- **禁止模块级 asyncio.Lock / Semaphore / Event**：首次竞争即绑定当前事件循环，Celery 每任务新循环后全部抛 "bound to a different event loop"（2026-07-28 全回路 INCONCLUSIVE 事故根因，ops-runbook 已记录；回归测试结构性断言守护）
- **断点续传禁止 overwrite**：gap backfill 复用 `import_history_data` 时必须 `conflict_strategy="skip"`（overwrite 会先 DELETE 误删实时行）；手工导入 overwrite 强制 `tsEnd ≤ now-5min`
- **断点续传配置运行时可调**（2026-08-06）：总开关 `gapBackfillEnabled`（默认**关闭**）与缺口阈值 `gapBackfillMinGapSeconds`（默认 600s=10 分钟）已纳入 `sys_config`，经 UI 链路配置页修改即时生效；`.env` 中 `GAP_BACKFILL_*` 仅作启动兜底默认值
- **默认账号**：admin / admin123（5 个种子用户详见 README.md）
- **前端端口是 15666**，后端 API 为 17101（MVP 隔离端口，见顶部 MVP 覆盖说明）

排障与背景细节（按需查阅 `docs/过程文档/ops-runbook.md`）：

- worker 静默挂死识别与处置、并发与回填性能、prewarm 废止背景 → ops-runbook §Celery Worker 运维
- 网络模式切换（Tailscale）验证命令、sudoers 免密、lifespan 预载细节 → ops-runbook §网络模式切换
- 实时数据断点续传机制细节 → ops-runbook §数据链路
- 诊断调度细节（**2026-08-07 起自动诊断 Beat 已停用**，仅保留手动触发；历史双轨口径备查）→ ops-runbook §诊断调度细节
- **uvicorn 静默挂死排查**（2026-08-09 加固 `2b9fb9d`：SQL echo 关停 + `command_timeout=60` + 噪音日志钳制；现象=进程存活 0% CPU/API 全挂起，多为连接风暴/资源耗竭；取证=PG 连接监控 `scripts/monitor_db_connections.py`（支持 --dsn 直连）+ `/proc/<pid>/net/tcp` TIME_WAIT 计数）→ ops-runbook §uvicorn 静默挂死排查

## 核心决策

| 决策 | 当前口径 |
|---|---|
| 产品定位 | 产品化、工具化的控制回路绩效治理与优化闭环平台，非项目型定制化系统；用户（管理员/工程师）可自助完成配置组态，减少开发团队介入 |
| AAS 数据模型 | AAS 同步 tag 位号（非回路实体）；回路由用户创建并关联 7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D）；PID 参数与控制模式从关联 tag 只读读取；数据质量主要针对 PV 值（Good/Bad/Uncertain 质量码） |
| **数据架构** | **导入走远端、计算全本地**（2026-07-20 定调）：远端 AAS 历史接口仅"数据管理→历史数据导入"手工任务可调用；本地 TDengine 是所有计算任务唯一历史数据源；本地数据不完整按 INCONCLUSIVE 提示，由用户导入补齐；实时数据源唯一为 SignalR Hub。详见 `docs/过程文档/data-architecture-decision-local-first-2026-07-20.md` |
| 技术护城河 | 可信数据 + 可解释诊断 + 可验证整定 + 安全闭环 + 规模化交付 |
| 安全边界 | 平台不直接修改 DCS 的 P/I/D 参数，只输出建议、证据、风险和回退方案；参数由授权人员人工实施并留痕 |
| 原型/前端开发 | 当前生产前端为 Vue 3 + Vite + TypeScript + vue-vben-admin；MVP 路由/页面以 `docs/MVP设计/` 为准 |
| 性能边界 | LTTB 降采样 maxPoints=2000，30 天时间窗口 |
| 网络模式 | 应用层局域网/公网切换（2026-07-19）：**仅切换网络链路（Tailscale subnet router 透明转发），与数据源选择无关**；sys_config 为配置真相源，.env 已移除业务 URL/Token。细节见 ops-runbook §网络模式切换 |

## Git 工作流（MVP 口径，2026-08-20 修订）

- **远端**：`github` = `https://github.com/hlszp/CLPM-MVP`（**主远端，唯一可推送**）；`origin` = 原 CLPM gitea（pushurl 锁死 `DISABLE_PUSH_TO_UPSTREAM`，**严禁任何推送**）；main 跟踪 `github/main`
- **提交**：Conventional Commits `<type>(<scope>): <subject>`，subject ≤50 字符祈使句，body 解释"为什么"，按逻辑单元拆分，单 commit ≤500 行
- **日常开发**：可直接在 main 上小步提交并 `git push github main`（双机并行期例外，见下条）；大改动（>500 行或 DB schema/架构变更）建议开 `<type>/<简述>` 分支
- **双机分支策略**（2026-08-22 起）：macbook 机在 `macbook` 分支开发、zpdev 机在 `zpdev` 分支开发，各自 `git push -u github <分支>` 备份；**仅在用户显式要求时**才合并回 main（`--no-ff`）；允许并建议定期把 main 合入各自分支保鲜（main→分支方向不受限）；DB 迁移/种子数据变更尽量集中单机，避免 alembic 多 head 冲突；两机开发环境各自独立（工作区+容器+数据卷），互不干扰
- **红线**：禁止 `git push --force` 共享分支；禁止 `git reset --hard` 后推送共享分支；**禁止对原项目（origin）做任何提交动作**；**提交/推送/CI 仅在用户显式要求时执行**（2026-08-22 纪律）——小改动完成后直接报告结果，不主动提交，不同步等待 CI（报告"已触发"即可）
- **CI 现状**（2026-08-20 更新）：GitHub Actions 已启用且通过（`.github/workflows/ci.yml`，push/PR 触发 main/develop）；Backend（ruff check + format + pytest --cov 60% 门槛，Redis service 容器）/ Frontend（eslint apps/web-antd + typecheck + build + E2E 非阻塞）；提交前本地检查（ruff + pytest + check:type）仍是第一道门禁

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
