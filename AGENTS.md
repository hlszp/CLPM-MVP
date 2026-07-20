# CLPM Agent Guidance

本项目是危化企业控制回路性能评估与优化平台（CLPM v6.1），7 阶段系统重构已全部完成，文档体系已统一升级至 v6.1（含 ZL 工业设计规范对齐）。

## 必读入口

先读：`README.md`（当前共识与目录说明）、`docs/设计文档/00-BASELINE/implementation-contract.md`、`docs/设计文档/CLPM_v4.0_系统重构实施方案.md` 与 `docs/设计文档/01-PRD/PRD.md` v6.0。

PRD v6.0 是产品需求的事实来源；实现契约 v2.0 是重构后 IA/路由/API/权限/状态机/KPI 事实来源；UI/UX v6.1 是视觉与交互输入文件（已对齐 v6.1 代码，含 ZL 工业设计规范）；`CLPM_v4.0_系统重构实施方案.md` 是 7 阶段重构的实施蓝图。

## 当前基线（2026-07-07 修订 — v6.1 ZL 设计对齐完成）

| 类型 | 文件 | 版本 |
|---|---|---|
| 产品需求规范 PRD | `docs/设计文档/01-PRD/PRD.md` | v6.0 |
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
| 已批准产品化架构 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-20260616-072247.md` | 历史参考 |

## v6.1 文档与设计对齐状态（2026-07-07）

7 阶段重构全部交付，后端 1762 测试用例通过，文档体系已统一升级至 v6.1（含 ZL 工业设计规范对齐）：

| 阶段 | 核心交付 | Commit |
|---|---|---|
| Phase 0 | ORM 模型层更新 | `02f3c5a` |
| Phase 1 | 数据预处理模块（8步Pipeline + 8类异常值检测 + 180 单元测试） | `bdde45b` |
| Phase 2+3 | DataPlanner+Cache 与 12 个 KPI 指标计算器（3+1+8 体系） | `11d13e6` |
| Phase 4 | kpi_calc.py 整合 DataPlanner + MetricCalculator | `53fc21f` |
| Phase 5 | API 接口层扩展（17 端点 + 任务跟踪/通知 + OpenAPI 文档） | `39859e5` `0dfd37b` |
| Phase 6 | 前端适配（4层架构：类型/API → 组件 → 页面 → 路由） | `86f356c` `3516641` `4bff65b` |
| 修复 | Celery worker 任务注册修复（include 参数替代 autodiscover_tasks） | `207c882` |
| v6.0 升级 | 文档统一升级：PRD/FDS/ADS/DDS/IDS/UIUX → v6.0；实现契约 v1.0 → v2.0；DESIGN v2.1 → v3.0；测试数 1762；TS 错误 0 | 见 `docs/过程文档/superpowers/plans/v6-consistency-check.md` |
| v6.1 升级 | ZL 工业设计规范对齐：诊断中心/指标管理页面清除硬编码 Tailwind 色类；高危操作确认统一改用 ClpmDangerConfirmModal；监控页面 KPI 指标按时间范围聚合 | `1585a7e` `4aea6b8` `80c38ef` `d5f532f` |
| 网络模式切换 | 链路配置应用层局域网/公网动态切换：Tailscale subnet router + sudoers 免密 + sys_config 真相源 + lifespan 预载；.env 移除业务 URL/Token，统一由 sys_config 管理 | `6730b7f8` `ae0dff0c` `b09c816a` `ce5f4142` `b239b8b` `6a5fa30`（PR #75） |
| 数据导入韧性 | 历史导入 chunk 级重试（502/503/504/429 + 超时/网络异常，指数退避 1/2/4s 最多 3 次）+ 回路并发 5→2 + chunk 跨度 24h→3h + 远端超时默认 30s→120s | `b74a6b4`（PR #74） |
| 数据链路修复 | Celery worker 经 `worker_process_init` 预载 sys_config（此前仅 API lifespan 预载，worker 取不到业务 URL 导致导入/远端取数全失败）；实时自控率/回路状态统计改读 Redis 实时缓存（原只读 PG `tag_registry.current_value`，AAS 停更后数据过期）；装置/单元性能明细表改为当前节点+直接子节点 | PR #79 |
| 远端调用保护 | RemoteApiProvider 全局限流（per-loop 信号量默认 4 并发，覆盖 DataPlanner 无界 gather）+ 熔断器（连续失败 5 次熔断 300s 快速失败、半开探测）；SignalR 重连指数退避 5s→30s 封顶。背景：2026-07-19 回填 8 worker × ~54 并发压垮远端边缘 API | PR #80 |
| 性能评估四页治理 | 全面检查装置性能/回路性能/评估任务/KPI报表 4 页并修复 27 项：DataPlanner 契约查询 steady_rate→stability_rate 别名（快照只剩 PARTIAL 的回归）；装置聚合仪表盘全厂 null；快照服务端排序；_parse_dt 时区；DataLineage snake_case 血缘；策略配置裸数组解析；评估历史复合行键/日期 endOfDay；E 级评分掩码；kpi-report latestOnly=false + UTC 窗口；危险操作统一 ClpmDangerConfirmModal | PR #81 |

## v6.0 核心架构组件

| 组件 | 路径 | 职责 |
|---|---|---|
| DataPlanner | `app/services/data_planner.py` | 统一历史数据读取，按控制类型自动降采样，分发 MetricDataBundle |
| ConfidenceEvaluator | `app/services/confidence_evaluator.py` | 可信度评估 A/B/C/D/E（valid_rate 阈值 95/80/60/20%），INCONCLUSIVE 处理 |
| TaskTracker | `app/services/task_tracker.py` | 任务全生命周期跟踪（create/update_status），Redis 状态存储 + 通知 |
| 预处理 Pipeline | `app/services/preprocessing/` (quality_code/thresholds/outlier_detection/validity_mask/quality_summary/pipeline) | 8 步流水线 + 8 类异常值检测 |
| MetricCalculator | `app/tasks/kpi_calc.py` | 12 个 KPI 指标计算器（3 核心 + 1 综合 + 8 辅助），通过 DataPlanner.request_bundles() 获取数据 |

## 开发环境运行指南

### 启动服务

```bash
# 1. 基础设施
docker compose -f deploy/docker/docker-compose.dev.yml up -d

# 2. 后端 API (port 7101)
#    v6.1：后端启动时自动启动 Celery Beat 调度进程（定时任务自动执行）
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 7101 --reload

# 3. Celery Worker（独立进程，必须单独启动；Beat 已随后端自动启动）
cd backend && .venv/bin/celery -A app.tasks.celery_app worker -l info -Q default

# 4. 前端 (port 7100)
cd frontend && pnpm run dev:antd
```

### 测试与验证

```bash
# 后端单元测试（1762 用例）
cd backend && uv run pytest -q

# 前端类型检查
cd frontend && pnpm run check:type

# E2E 测试
cd e2e && pnpm exec playwright test
```

### CI 提交前本地检查（避免 CI lint/format 门禁失败）

```bash
# backend ruff check + format（CI 门禁，提交前必跑）
cd backend && uv run ruff check . && uv run ruff format --check .

# 自动修复 ruff 问题
cd backend && uv run ruff check . --fix && uv run ruff format .

# frontend 格式化（CI oxfmt 门禁）
cd frontend && pnpm run format
```

### 网络模式切换验证命令

```bash
# 查看当前路由走哪个接口（en0=局域网直连 / utun4=Tailscale 隧道）
route get 192.168.100.2 | grep interface

# 查看 tailscale accept-routes 状态（不需 sudo，RouteAll=true 即已启用）
tailscale debug prefs | grep RouteAll

# 后端启动日志确认 sys_config 预载
grep "数据源配置已从 sys_config 预载" /tmp/clpm-backend.log

# 查看 sys_config 当前网络模式
docker exec clpm-postgres psql -U clpm -d clpm -t -c \
  "SELECT value FROM sys_config WHERE key='datasource.network_mode';"

# 查看最近 5 条 tailscale 切换审计日志
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT operator, operation_type, before_value, after_value, operated_at \
   FROM sys_audit_log WHERE operation_type='TAILSCALE_SWITCH' \
   ORDER BY operated_at DESC LIMIT 5;"
```

### 关键注意事项

- **Celery worker 是独立进程**：与 FastAPI（`--reload`）分开启动，后端代码更新后需重启 worker；sys_config 业务配置（URL/Token）变更对 worker 生效需重启 worker 或等子进程回收（worker_process_init 每子进程预载一次）
- **Worker 静默挂死的识别与处置**（2026-07-19 实测）：worker 主进程可能静默挂死（进程在、池进程全灭、`celery inspect` 无响应、日志停更数小时），表现为任务卡 PENDING、broker 队列持续积压。诊断：`docker exec clpm-redis redis-cli -n 1 LLEN default`（队列长度）+ `pgrep -P <worker_pid>`（池子进程数）。处置：`kill <主进程pid>`（必要时 -9）后重启 worker；积压消息会在重启后全部追平（导入/回填类重任务注意耗时）
- **计算类历史数据查询一律本地 TDengine**（2026-07-20 架构决策）：`get_provider()` 恒返回 TDengineProvider，KPI/回填/诊断/趋势不再按 `DATA_SOURCE_TYPE` 分支走远端 API；本地数据不完整按 INCONCLUSIVE 提示（禁止自动降级到远端）。远端历史数据接口仅 `data_import.py`（历史数据导入任务）直接调用。历史教训（2026-07-19）：remote_api 模式下回填无界并发曾压垮远端 API、且远端挂死导致全部 INCONCLUSIVE
- **Worker 并发与回填性能**（2026-07-18 性能优化）：prod compose worker 默认 `--concurrency=8`（资源限额 8C/6G），`.env.prod` 中 `CELERY_WORKER_CONCURRENCY` 需按宿主机核数同步；回填任务按"1 窗口 = 1 个 chord 子任务"派发，27 回路 × 24h 实测约 52s（0.08s/回路时）；整点自动任务 27 回路 × 1h 实测约 1.9s（0.07s/回路时）。实测脚本：`backend/scripts/measure_backfill_perf.py`
- **prewarm 预热策略已废止**（2026-07-18）：原"每小时 55 分"预热窗口与整点任务窗口错位一小时（预热的是上一任务已算完的窗口，从未命中），已移除 beat 条目、worker_ready 预热与 L2 兜底预热。整点任务数据来源统一为 **realtime 滚动缓存**（`realtime:history:*`，保留 75 分钟×1Hz=4500 点，provider 对近 1 小时窗口自动探测）+ TDengine 回源兜底；`prewarm_cache` 任务保留供手工/运维调用
- **macOS fork 时区陷阱**：celery prefork 子进程中 naive `datetime.timestamp()`（mktime→localtime）会陷入时区慢路径（单次 ~0.5ms，多线程下有全局 tzlock 竞争），逐点调用会放大 3 个数量级。热路径禁止对 naive datetime 逐点调 `.timestamp()`；重复检测等场景直接用 datetime 对象比较（修复实例：`preprocessing/outlier_detection.py` `detect_ts_anomaly`）
- **Celery Beat 已自动启动**（v6.1）：后端 lifespan 中自动启动 Beat 调度进程，每小时 KPI 计算等定时任务自动执行，无需手动启动 Beat
- **前端端口是 5666**（配置 `frontend/apps/web-antd/.env.development` 中 `VITE_PORT=5666`；项目端口统一规划到 7100-7200 段，后端 API 为 7101）
- **前端 TypeScript 错误已全部修复**（v6.0 升级中清零，原 plant-node-tree.vue 3 个 + workbench.vue 3 个已修复）
- **默认账号**：admin / admin123（5 个种子用户详见 README.md）
- **Git 分支**：当前在 `main` 分支；双机协作时使用 `mb/*`（mb 机器）或 `zp/*`（zp 机器）临时分支，详见 §双机协作开发规范
- **网络模式切换**（2026-07-19，PR #75）：UI 链路配置页（`/loop/aas-sync`）支持局域网/公网动态切换
  - **配置真相源**：sys_config 数据库表（UI 配置一次即持久化）；.env 仅保留基础设施配置 + 合理默认值（TIMEOUT/RECONNECT_INTERVAL），已移除业务 URL/Token
  - **lifespan 预载**：`app/main.py` lifespan startup 调用 `preload_datasource_config(db)` 从 sys_config 读取配置并 `setattr(settings, ...)`，确保 SignalR 订阅器等启动时组件读到运行时配置而非 .env 空值；预载失败不阻塞启动，兜底 .env 默认值
  - **Tailscale 切换**：`app/core/system.py` `switch_network_mode(mode)` 通过 `sudo -n tailscale up --accept-routes={true|false} --reset=false` 动态切换子网路由；`shutil.which("tailscale")` 检测，容器内自动跳过
  - **sudoers 免密**：`deploy/sudoers.d/clpm-tailscale`（Linux，clpm 用户）/ `clpm-tailscale.macos`（Intel Mac，zhangping 用户）/ `clpm-tailscale.macos-arm64`（Apple Silicon Mac，zhangping 用户）；精确匹配命令参数，`tailscale status` 不在白名单（验证用 `sudo -nl | grep tailscale`）
  - **路由验证**：`route get 192.168.100.2` 显示 `interface: en0`（局域网直连）或 `utun4`（Tailscale 隧道）；`tailscale debug prefs` 的 `RouteAll` 字段反映 accept-routes 状态
  - **同模式跳过**：`update_datasource_config` 检测 `before == after` 时跳过 tailscale 命令，避免冗余 sudo 调用

## 核心决策

| 决策 | 当前口径 |
|---|---|
| 产品定位 | 产品化、工具化的控制回路绩效治理与优化闭环平台，非项目型定制化系统；用户（管理员/工程师）可自助完成配置组态，减少开发团队介入 |
| 模块架构 | 6 模块 + 1 门户：工作台 / 回路管理 / 性能评估 / 诊断中心 / 回路整定 / 系统管理；各业务模块遵循"配置→运行→分析"三态自包含原则，减少跨模块依赖 |
| AAS 数据模型 | AAS 同步 tag 位号（非回路实体）；回路由用户创建并关联 7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D）；PID 参数与控制模式从关联 tag 只读读取；数据质量主要针对 PV 值（Good/Bad/Uncertain 质量码） |
| **数据架构** | **导入走远端、计算全本地**（2026-07-20 用户定调，决策记录：`docs/过程文档/data-architecture-decision-local-first-2026-07-20.md`）：① 历史数据有两个源——远端 AAS 历史数据接口（remote_api）有且仅有"数据管理→历史数据导入"手工任务可调用（`data_import.py` 独立客户端，不经 provider 工厂），本地 TDengine 是所有性能评估/回路诊断/回路整定等计算任务的唯一历史数据来源；② 任何计算任务**不得**自动降级/切换到远端 API 取数，本地数据不完整按 INCONCLUSIVE/数据不足提示，由用户导入补齐；③ 实时数据源唯一：SignalR Hub（`signalr_hub_url`），开发/生产一致；④ `DATA_SOURCE_TYPE` 配置项已废止（兼容保留，不再影响计算路径）；`get_provider()` 恒返回本地 TDengineProvider |
| Action Tracker | 诊断中心子模块（子菜单路由），状态机 PENDING → IN_PROGRESS → IMPLEMENTED/IGNORED，中文显示为待处理/处理中/已实施/已忽略 |
| 统计分析 | 不设独立模块，分散到各业务模块的"分析"态；自动报表归入系统管理 |
| 回路整定 | Phase 1 保留页面与实验/辅助接口，只输出建议、证据、风险和回退方案；不支持 DCS 参数下写，Phase 2 再完成生产级算法闭环 |
| 技术护城河 | 可信数据 + 可解释诊断 + 可验证整定 + 安全闭环 + 规模化交付 |
| 安全边界 | 平台不直接修改 DCS 的 P/I/D 参数，只输出建议、证据、风险和回退方案；参数由授权人员人工实施并留痕 |
| 首版主线 | Phase 1 (MVP/V1.0)：跑通"自动评估、自动诊断、轻量跟踪"闭环 |
| 原型/前端开发 | 当前生产前端为 Vue 3 + Vite + TypeScript + vue-vben-admin；重构后路由/页面以 `docs/设计文档/00-BASELINE/implementation-contract.md` 为准 |
| 性能边界 | LTTB 降采样 maxPoints=2000，30 天时间窗口 |
| 网络模式 | 应用层局域网/公网切换（2026-07-19）：**仅切换网络链路（Tailscale subnet router 透明转发），与数据源选择无关**——局域网/公网只是同一组数据源（远端历史导入接口 + SignalR Hub + 本地 TDengine）的两条可达路径。UI 单 URL 输入；.env 移除业务 URL/Token（HISTORY_DATA_API_URL/HISTORY_DATA_API_TOKEN/SIGNALR_HUB_URL），sys_config 为运行时真相源；lifespan 启动通过 `preload_datasource_config()` 预载 sys_config 到 settings 内存；`sudo -n tailscale up --accept-routes={true\|false} --reset=false` 动态切换子网路由，sudoers 免密配置见 `deploy/sudoers.d/` |
| 文档权威性 | PRD v6.0 负责产品需求；实现契约 v2.0 负责重构后 IA/路由/API/权限/状态机/KPI；UI/UX v6.1 负责视觉与交互（已对齐 v6.1 代码，含 ZL 工业设计规范）；v4.0 重构实施方案负责 7 阶段实施蓝图 |

## 双机协作开发规范

本项目由两台机器通过 GitHub 远程仓库协同开发，每台机器配置独立的 AI 智能体（Trae / Claude Code）。为确保协作安全、顺畅、便捷，特制定本规范。

### 1. 机器与智能体配置

| 机器 | 智能体 | 分支前缀 | 主要职责 |
|---|---|---|---|
| mb 机器 | Trae | `mb/*` | 文档治理、设计规范、架构对齐、Bug 修复 |
| zp 机器 | Claude Code | `zp/*` | 功能开发、数据链路、CI 修复、实时仿真 |

> 两台机器均从 `main` 创建临时分支，工作完成后通过 PR 合并，合并后删除分支。

### 2. 分支命名规范

**格式**：`<机器前缀>/<类型>-<简述>[-<编号>]`

**类型枚举**（与 Conventional Commits 对齐）：

| 类型 | 用途 | 示例 |
|---|---|---|
| `feat` | 新功能 | `mb/feat-loop-monitor`、`zp/feat-realtime-sync` |
| `fix` | Bug 修复 | `mb/fix-timezone-display`、`zp/fix-ci-config` |
| `doc` | 文档更新 | `mb/doc-v6-upgrade`、`zp/doc-api-spec` |
| `refactor` | 重构 | `mb/refactor-metric-calc` |
| `test` | 测试补充 | `zp/test-e2e-tasks` |
| `chore` | 构建/配置 | `zp/chore-deploy-config` |

**规则**：
- 分支名使用小写英文 + 连字符，禁止中文和下划线
- 简述不超过 30 字符
- 单次任务的分支生命周期不超过 3 天，合并后立即删除（本地 + 远程）
- 禁止在 `main` 分支上直接开发或提交

### 3. 提交规范（Conventional Commits）

**格式**：

```
<type>(<scope>): <subject>

<body>

<footer>
```

**类型**：`feat` / `fix` / `doc` / `refactor` / `test` / `chore` / `style` / `perf`

**scope 示例**：`v6` / `ui` / `metric` / `diagnosis` / `loop` / `task` / `ci` / `data`

**规则**：
- Subject 不超过 50 字符，使用祈使句（如"修复时区显示"而非"修复了时区显示"）
- Body 解释 "为什么" 而非 "做了什么"（diff 已说明做了什么）
- 涉及多文件大改动时，按逻辑单元拆分多个 commit
- 单个 commit 不超过 500 行 diff（自动化生成文件如 lock 文件除外）

### 4. 推送与 PR 合并流程

**推送规则**：
- 临时分支首次推送：`git push -u origin <branch-name>`
- 后续推送：`git push origin <branch-name>`
- **禁止** `git push --force` 到任何分支（包括临时分支），如需重写历史请先与对方机器沟通
- **禁止**直接 `git push origin main`，所有 main 更新必须通过 PR

**PR 合并流程**：

1. 推送临时分支到 origin
2. 使用 `gh pr create` 创建 PR，标题遵循 Conventional Commits 格式
3. PR Body 必须包含：
   - 变更概述（1-2 句）
   - 变更范围（文件清单或模块清单）
   - 验证结果（测试通过情况、type-check、build 等）
   - 关联 issue（如有）
4. 等待 CI 通过（Backend CI + Frontend CI）
5. CI 通过后，由对方机器或自己 review（小改动可自合并）
6. 使用 `gh pr merge <PR号> --merge` 合并（保留分支历史，不使用 squash）
7. 合并后删除临时分支（本地 + 远程）：
   ```bash
   git checkout main && git pull origin main
   git branch -D <branch-name>
   git push origin --delete <branch-name>
   ```

**PR 合并决策矩阵**：

| 改动规模 | Review 要求 | 合并方式 |
|---|---|---|
| 纯文档（*.md） | 自合并即可 | `gh pr merge --merge` |
| 单文件小改动（<50 行） | 自合并即可 | `gh pr merge --merge` |
| 多文件改动 | 建议对方 review | `gh pr merge --merge` |
| 架构/数据模型变更 | 必须对方 review | `gh pr merge --merge` |
| 紧急修复（hotfix） | 自合并，事后通知对方 | `gh pr merge --merge` |

### 5. 每日同步流程

**目标**：避免分支长期偏离 main，减少冲突积累。

**每日开始工作前（早同步）**：

```bash
# 1. 切到 main 拉取最新
git checkout main && git pull origin main

# 2. 切到工作分支，merge main 进来
git checkout <branch-name>
git merge main -m "chore: 同步 main 到工作分支"

# 3. 解决冲突（如有），运行测试确认
cd backend && uv run pytest -q --tb=no
cd frontend && pnpm run check:type

# 4. 推送同步后的分支
git push origin <branch-name>
```

**每日结束工作前（晚同步）**：

```bash
# 1. 提交当日工作
git add <files>
git commit -m "<type>(<scope>): <subject>"

# 2. 推送到 origin
git push origin <branch-name>

# 3. 如任务完成，创建 PR
gh pr create --base main --head <branch-name> --title "..." --body "..."
```

**冲突解决原则**：
- 同一文件冲突，优先联系对方机器确认意图
- 文档冲突以"版本号更高"或"最近设计决策"为准
- 代码冲突以"测试通过"为最终判据
- 无法判断时，创建临时分支讨论，不强行合并

### 6. 工作分配原则

**职责划分**（柔性，非强制）：

| 模块 | mb 机器（Trae） | zp 机器（Claude Code） |
|---|---|---|
| 设计文档（PRD/FDS/ADS/DDS/IDS/UIUX） | 主 | 协 |
| 实现契约 / DESIGN.md | 主 | 协 |
| 后端 API + 业务逻辑 | 协 | 主 |
| 前端页面 + 组件 | 主或协 | 主或协 |
| 数据链路 + TDengine | 协 | 主 |
| CI/CD + 部署 | 协 | 主 |
| 测试用例 | 协 | 主 |
| Bug 修复 | 主或协 | 主或协 |

**避免并行修改同一文件**：
- 开始任务前，先 `git pull origin main` 确认对方是否有相关改动
- 如对方有未合并的 PR 涉及相同文件，先沟通协调
- 大改动（>500 行）开始前在工作分支创建 issue 或 PR 草稿，标注 "WIP"

### 7. 安全规则（红线）

**绝对禁止**：
- ❌ `git push --force` 到 `main` 或他人分支
- ❌ 直接 `git push origin main`（必须通过 PR）
- ❌ `git reset --hard` 后推送共享分支
- ❌ 在 `main` 分支上直接 commit
- ❌ 删除他人的工作分支（除非已合并且对方确认）
- ❌ 修改 `.git/config` 或 git hooks 不通知对方

**需要确认**：
- ⚠️ 修改 `package.json` / `pyproject.toml` 添加新依赖（可能影响对方环境）
- ⚠️ 修改 `docker-compose*.yml`（可能影响对方容器）
- ⚠️ 修改 `.env.example` 或环境变量约定
- ⚠️ 数据库 schema 变更（Alembic migration）
- ⚠️ 修改 CI workflow（`.github/workflows/*.yml`）

**允许**：
- ✅ 在自己的临时分支上自由实验
- ✅ 修改自己分支的文件
- ✅ 强制推送自己的临时分支（如需重写历史，但建议先沟通）
- ✅ 删除自己创建的临时分支

### 8. 智能体协作约定

不同智能体（Trae / Claude Code）在同一项目协作时：

| 约定 | 说明 |
|---|---|
| 上下文传递 | 通过 GitHub PR、commit message、AGENTS.md 传递上下文，不依赖本地会话 |
| 文档先行 | 设计决策先写入设计文档，再实现代码 |
| 测试驱动 | 代码改动必须有对应测试（后端 pytest、前端 type-check） |
| 提交粒度 | 单次 commit 聚焦一个逻辑单元，便于 review 和 revert |
| 冲突预防 | 开始任务前阅读 AGENTS.md 和相关设计文档，避免与对方方向冲突 |
| 通知机制 | 重要改动（架构/数据模型/CI）通过 PR comment 或 commit footer `Notify: <内容>` 标注 |

### 9. 快速命令参考

```bash
# 开始新任务
git checkout main && git pull origin main
git checkout -b mb/feat-xxx  # 或 zp/feat-xxx

# 日常工作
git add <files> && git commit -m "feat(scope): 描述"
git push origin mb/feat-xxx

# 同步 main
git checkout main && git pull origin main
git checkout mb/feat-xxx && git merge main -m "chore: 同步 main"

# 创建 PR
gh pr create --base main --head mb/feat-xxx --title "feat: xxx" --body "..."

# 合并 PR 并清理
gh pr merge <PR号> --merge
git checkout main && git pull origin main
git branch -D mb/feat-xxx
git push origin --delete mb/feat-xxx

# 查看对方工作
git fetch --all --prune
git log origin/main --oneline -10
gh pr list --state open
```

---

## 下阶段规则

v6.0 文档统一升级已完成，后续工作方向：

| 方向 | 先读 | 关注点 |
|---|---|---|
| Bug 修复 / 功能增强 | README.md → AGENTS.md → 相关设计文档 → 对应代码 | 遵循"问题定位-修复实施-测试验证-效果确认"闭环流程 |
| 前端 lint/格式化整理 | 前端代码 → `pnpm run lint` | 工作区已清理干净（2026-07-19）；如需进一步统一可跑 `pnpm run lint --fix` 后提交 |
| E2E 测试补充 | `e2e/` 目录 → UI/UX v6.0 → v6.0 新增页面 | 任务管理/可信度徽章/INCONCLUSIVE 已补（2026-07-19，PR #78：E2E-TASK-001~006 + E2E-CONF-001~004，含 playwright 端口 7100→5666 修正）；其余页面按 UI/UX v6.0 逐步补 |
| 生产部署 | `docker-compose.prod.yml` → `.env.prod.example` → `deploy/deploy.sh` | Celery worker 容器需验证 include 参数生效 |
| v6.0 文档统一升级 | `docs/过程文档/superpowers/plans/v6-consistency-check.md` → v6.0 各设计文档 | 文档已统一升级至 v6.0，需持续保持文档与代码一致性 |
| 新功能开发 | PRD v6.0 → 实现契约 v2.0 → v4.0 重构实施方案 → 对应设计文档 | 遵循模块"配置→运行→分析"三态自包含原则 |
| 网络模式切换后续改进 | AGENTS.md → §关键注意事项（网络模式切换） | ①②④ 已完成（2026-07-19：wan→lan 反向切换实测通过，`utun4→en0` 路由正确恢复；容器降级经 18 单测确认返回 `skipped` 不阻断配置；新增 `deploy/sudoers.d/clpm-tailscale.macos-arm64` 覆盖 Apple Silicon）；③ 公网模式 ping 延迟抖动大（6-63ms）可优化 DERP 节点或 Tailscale 直连（未做，低优先级） |

## Stale docs 防护

不要把以下文件当作现行需求输入：

- `archive/product-requirements-specification-2026-06-15.md`
- `archive/product-requirements-specification-v0.2-2026-06-16.md`
- `archive/prs-adjustment-assessment-2026-06-15.md`
- `archive/tender-requirements-gap-assessment-2026-06-16.md`
- `docs/归档文档/project-assessment-report.md`（项目现状评估，重构建议已落地）
- `docs/归档文档/prototype-design-spec.md`（被 `DESIGN.md` 取代）
- `docs/归档文档/full-prototype-planning.md`（被 `docs/过程文档/superpowers/plans/` 取代）
- `docs/归档文档/prototype-visual-tokens-v0.1-2026-06-16.md`（被 `06-UIUX/ui-ux-design-guidelines.md` §3 覆盖）
- `docs/归档文档/prototype-responsive-accessibility-v0.1-2026-06-16.md`（被 `06-UIUX/ui-ux-design-guidelines.md` §2 覆盖）
- `product-requirements-specification-v0.4-2026-06-16.md`（PRS v0.4 已被 PRD v3.0 取代）
- `full-product-menu-ia-v0.1-2026-06-16.md`（菜单已并入 UI/UX v4.0 §5）
- `prototype-development-freeze-v0.1-2026-06-16.md`（任务书已被 UI/UX v4.0 25 页面清单取代）
- `p0-contract-backbone-design-v0.1-2026-06-16.md`（P0 模型已被 Phase 1/Action Tracker 模型取代）
- `core-algorithm-confidence-design-v0.1-2026-06-16.md`（可信度设计已并入 ADS v3.0 与 DDS v3.0）

以下 v0.1 文件已于 2026-06-20 删除（概念体系冲突，有价值内容已吸收进 `06-UIUX/ui-ux-design-guidelines.md` v4.0）：
- `docs/设计文档/prototype-state-spec-v0.1-2026-06-16.md`
- `docs/设计文档/prototype-interaction-detail-v0.1-2026-06-16.md`
- `docs/设计文档/prototype-page-wireframes-v0.1-2026-06-16.md`

**v2.x 文档已全部被 v3.0 取代**（2026-06-20 修订）：PRD v2.2、FDS v2.0、ADS v2.0、DDS v2.0、IDS v2.0、UI/UX v3.0 不再作为有效输入；如需追溯历史版本，请使用 git 历史。

**v6.0 升级前的所有版本已全部被 v6.0 取代**（2026-07-06 修订）：PRD v3.1/v4.0、FDS v3.0/v5.1、ADS v3.0/v4.0、DDS v3.0/v4.1、IDS v3.0/v4.0、UI/UX v5.1/v5.3、实现契约 v1.0、DESIGN v2.1 不再作为有效输入；如需追溯历史版本，请使用 git 历史。

这些文件只用于历史追溯。

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
