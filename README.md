# CLPM-MVP

> **双仓分工（2026-09-27 用户口径）**：**iCLPM**（`/Users/zhangping/DEV/iCLPM`，远端 `https://github.com/hlszp/iCLPM`）专注智能体协同层产品（iCLPMagent）；**CLPM 本体系统（本仓库）的功能开发与缺陷修复仍在本项目进行**，本 README 与 `AGENTS.md` 为现行口径。

危化企业控制回路性能治理与优化平台（Control Loop Performance Monitoring & Optimization）— **MVP 精简 + 闭环重建版**（自 CLPM v6.2 派生，设计事实来源见 `docs/MVP设计/`）。

产品文档基线：**v6.2**（派生自原项目，MVP 差异以 `docs/MVP设计/` 为准）。产品锁定 tag 为 **v7.5.6**；镜像运行版本由 `APP_VERSION` 标识。最新交付为 **v7.5.6-1046-full**（2026-10-21），待用户通过 ToDesk 手工部署，业务效果仍需现场复验。源码、镜像及部署记录见 [验证报告](docs/过程文档/full-1046-MODE与批量诊断修复-2026-10-21.md)、[交付清单](releases/manifest-1046.json) 和 [逐条部署说明](releases/部署指令-1046-ToDesk人工.md)，变更记录见 `CHANGELOG.md`。

## 项目简介

CLPM-MVP 是面向危化企业控制回路的绩效治理与优化闭环平台，覆盖"**监控 → 评估 → 诊断 → 整定 → 处置 → 统计报告**"完整业务闭环 + 驾驶舱管理决策视图（2026-10-05 驾驶舱整合 P1~P7 落地：6 页签满屏 `/cockpit/*`），提供：

- **驾驶舱**（order=0，全角色可见）：满屏 **6 页签** `/cockpit/*`（**总览/回路/性能/诊断/整定/处置**，noBasicLayout + cockpit-header；总览=原驾驶舱总览+系统总览合并重构，全厂装置/单元性能为重点）。原运维工作台 4 Tab 已迁入 `views/cockpit/wb-comps/`（页内 50 处联动全部弹窗化=use-drill.ts E1~E11，禁止跳管理后台）；旧 `/workbench` redirect → `/cockpit`（2026-10-05 D5/D6，旧工作台已退役）；默认落地页：ADMIN/SPONSOR→`/cockpit`，IC/PE/EXPERT→`/my-work` 我的工作（2026-10-14 P6-06）
- **回路工作台**（order=1）：`/loop/workbench360` 单页全生命周期工作台（监视-评估-诊断-整定-处置零跳转全内嵌，`?section=` 剖面直达协议；2026-10-05 D6 升为一级菜单）
- **回路监控**（order=2）：回路监视（列表页标杆 v2.0）/ 预警事件 / 系统建议（原关注队列，2026-10-16 P6 批更名；三来源聚合 + 四级优先级 + 分组详情抽屉）
- **性能评估**（order=3）：装置性能（评估默认首页）/ 指标分析（TOP 排行 + 装置对比 + 行动清单）/ 回路评估 / 评估计算任务（手动/自动统一）；L0/L1 回路灰色"不适用"分类独立不计入"差"
- **回路诊断**（order=4，MVP 重建 2026-08-16）：三页式（**概览/记录/任务**，2026-10-04 D2）；6+1 原因分类体系 + 元算子架构 + 证据污染链；单回路发起/结论并入**回路工作台诊断剖面**（`/diagnosis/workbench` redirect）；**适用性门禁**：L0 警告放行产出 DATA_INSUFFICIENT、夜间定时全量滤 L0（2026-10-05 三性裁决）
- **参数整定**（order=5，MVP 重建 2026-08-19）：三页式（**整定总览/整定记录/效果验证**，2026-10-04 D3 整定工作台转型总览）；四步流程（辨识→矩阵→仿真→确认）移**回路工作台整定剖面**；后端复用 Phase 2 算法栈（ARX/ARMAX/IV 辨识 + 全算法矩阵 + 闭环仿真）；**适用性门禁**：L0/L1 阻断 `ERR_TUNING_FITNESS_INSUFFICIENT`、L2/L3 提示放行
- **运维处置**（order=6，MVP 新建 2026-08-19，升 v2.0 双实体 2026-08-20）：诊断建议（5 态审核状态机：接受/驳回/忽略 + 批量转工单）/ 处置任务 / 处置工单（handling_order 六态状态机 + 编号 HD-YYYYMMDD-NNN + 多建议合一单）/ 处置档案；KPI 前后对比验证
- **统计分析**（order=7，一级菜单 2026-08-23 IA 优化 P0 落地；报告模块优化 P0/P1 已落地 2026-08-28；2026-10-05 驾驶舱整合菜单改名）：绩效报告（由评估-KPI 报表迁入）/ **数据质量**（参评率/数据健康率/INCONCLUSIVE 率/可信度分布/未参评归因，P1；2026-09-26 新增**位号级数据质量体检分区**，只读接口 `GET /api/v1/reports/data-quality/audit` + CLI `scripts/audit_data_quality.py`，四类判定：断流 / 质量码坏 / 密度不足 / 含 HELD 填平）/ 诊断报告（基于 DiagnosisRun 重建）/ 处置报告（由处置-统计迁入）/ 收益报告（整定前后 KPI 对比 + 自控率提升曲线 + 装置标杆）/ **预警统计**（MTTA/MTTR/误报率/severity 趋势/TOP 规则回路，P1）/ 订阅配置（由系统-自动报表迁入；自动生成为占位实现 P0-1 止血，「立即生成」置灰，P3 做实后恢复）；报告页接入模块热插拔降级横幅；旧路径全局 redirect 就位
- **智能预警**：规则引擎（阈值/漂移/组合/可信度 DSL + 预设规则模板 + 三级阈值 + metric_threshold 指标阈值类型，2026-08-26）+ 事件流（确认→处置→归档，误报标记/撤销）+ 顶栏通知铃铛 /ws/alerts 实时推送；预警→诊断联动受热插拔开关控制自动降级
- **系统管理**：基础信息（站点信息/LOGO/授权回路数）/ 用户管理 / 审计日志 / 权限矩阵（未启用模块列虚线灰显）/ 自动报表（→ 已迁统计报告→订阅配置）/ 字典管理（测点类型/参数类型/回路类型三类可配置字典）/ **模块管理**（模块热插拔开关，诊断/整定/处置按客户阶段弹性启用禁用，重启生效，含依赖校验和禁用确认弹窗）

平台遵循"只读 DCS、只输出建议"的安全边界，不直接写入 DCS。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite + vue-vben-admin 5.7.0 + Ant Design Vue + ECharts |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2.0 (async) + Pydantic v2 + uv 包管理 |
| 异步任务 | Celery + Celery Beat（KPI 计算 / 诊断引擎 / 报表生成 / AAS 同步） |
| 关系数据库 | PostgreSQL 16 |
| 时序数据库 | TDengine 3.3.6 |
| 缓存/队列 | Redis 7 |
| 鉴权 | JWT 双 Token（Access 30min / Refresh 7d）+ RBAC 五角色 + bcrypt + Redis 黑名单 |
| 算法 | NumPy + SciPy（模型辨识 / PID 整定 / 闭环仿真 RK4 / ARMA 辨识） |
| v4.0 核心组件 | DataPlanner（统一数据读取，L1/L2 缓存已接入；L3 Feature Cache 预留）+ ConfidenceEvaluator（可信度评估）+ TaskTracker（任务跟踪）+ 预处理 Pipeline（8步+8类异常检测）|
| 部署 | Docker + Docker Compose + Nginx 反向代理 |
| 测试 | pytest（默认排除 integration）+ vitest workspace 全量 + Playwright；真实 PostgreSQL/Redis/TDengine 集成需显式启用。full-1043 的固定结果与检查限制见 [验证报告](docs/过程文档/full-1043-性能修复与验证-2026-10-21.md)，不将历史计数或 CI 配置当作当前现场验收 |

## 快速开始（开发环境）

### 环境要求

- Node.js ≥ 22.18.0 + pnpm ≥ 10.0.0
- Python 3.12 + [uv](https://docs.astral.sh/uv/) ≥ 0.4
- Docker 24+（推荐 [Orbstack](https://orbstack.dev/) 作为容器运行时）

### 1. 启动基础设施（PostgreSQL + TDengine + Redis）

```bash
docker compose -f deploy/docker/docker-compose.dev.yml up -d
```

> MVP 端口隔离（与原 CLPM 项目区分，原端口 +10000）：后端 API **17101**、前端 **15666**、mock 数据服务 **17106**；开发容器名 `clpm-mvp-*`、数据卷 `clpm_mvp_*`。

### 2. 启动后端

```bash
cd backend
cp .env.example .env          # 首次执行
uv sync                        # 安装依赖
uv run alembic upgrade head    # 执行数据库迁移
uv run uvicorn app.main:app --host 0.0.0.0 --port 17101 --reload
```

后端 API 文档：http://localhost:17101/docs

> **v6.1 说明**：后端启动时自动启动 Celery Beat（定时调度）和 Celery Worker（任务执行）子进程，无需手动启动。修改 Celery 任务代码后需重启后端让新代码生效。

### 3. 启动前端

```bash
cd frontend
pnpm install
pnpm run dev:antd              # 默认端口 15666（MVP 隔离端口）
```

前端访问地址：http://localhost:15666

### 5. 默认账号

5 个种子用户，密码统一为 `admin123`：

| 用户名 | 角色 | 权限范围 |
|---|---|---|
| admin | ADMIN | 全部模块 |
| ic_engineer | IC_ENGINEER | 全部业务模块 |
| pe_engineer | PE_ENGINEER | 业务查看及诊断/整定操作，按权限矩阵控制 |
| expert | EXPERT | 业务查看及诊断/整定操作，按权限矩阵控制 |
| sponsor | SPONSOR | 业务只读，默认进入驾驶舱 |

> **改密口径（2026-08-31 调整）**：首次登录强制改密功能已移除，系统无改密要求；种子用户默认密码统一为 `admin123`，用户可经个人中心自愿改密。

### 6. 运行测试

```bash
# 后端单元测试
cd backend && uv run pytest -q

# 前端类型检查
cd frontend && pnpm run check:type

# E2E 测试（需先启动前后端）
cd e2e && pnpm install && pnpm exec playwright install chromium
pnpm exec playwright test
```

### 7. 数据源与数据链路

架构决策（2026-07-20）：**导入走远端、计算全本地**。

| 数据 | 来源 | 说明 |
|---|---|---|
| 历史数据（计算用） | **本地 TDengine** | 性能评估、回路诊断、回路整定等所有计算任务唯一历史数据来源；数据不完整时按 INCONCLUSIVE/数据不足提示，**不会**自动降级到远端接口 |
| 历史数据（采集用） | 远端 AAS 历史数据接口 | 手工导入及显式启用后的断点回填经 `data_import.py` 写本地点表；计算任务不直接调用远端 |
| 实时数据 | SignalR Hub（唯一） | `ws://<现场>/signalr/realValueForClpmHub`，开发/生产一致；写入 Redis 实时缓存（页面实时监控）+ 可选写回本地 TDengine（KPI 计算） |

#### 7.1 历史数据导入接口（remote_api）

在 UI「链路配置」页（`/loop/aas-sync`）配置一次即持久化到 sys_config：

- 历史数据 API 地址：如 `http://192.168.100.2:81/api/services/v1/HistoryData/Get`
- 鉴权 Token（可选）、请求超时

该接口由 `data_import.py` 直接调用，供手工导入及启用后的断点回填使用；KPI 整点/历史重算/诊断/趋势均读取本地 TDengine。导入采用单相分块、按位号 UPSERT，保留原始质量码并登记实际写入覆盖；对接规范见 `docs/设计文档/05-IDS/HisDATA_API.md`。

> 兼容说明：`DATA_SOURCE_TYPE` 环境变量已废止（仅作配置保留），不再影响计算路径的数据源选择。

#### 7.2 实时数据订阅（SignalR/WebSocket，唯一实时数据源）

同样在「链路配置」页配置：

- SignalR Hub URL：如 `ws://192.168.100.2:81/signalr/realValueForClpmHub`
- 断线重连基础间隔（指数退避 5s→30s 封顶）
- 实时数据写回本地 TDengine 点表 `st_point_data_v1`（由链路配置中的写回开关控制；`.env` 为启动默认值）

对接接口规范见 `docs/设计文档/05-IDS/RealDATA_API.md`。
实时页面经 `/api/v1/loop-data/*` 与 WebSocket 消费缓存；`GET /api/v1/realtime` 仍是保留的兼容入口。

#### 7.3 网络链路（局域网/公网，与数据源无关）

「链路配置」页的局域网/公网切换**只切换网络链路**（是否经 Tailscale 子网路由转发），数据源不变：公网外出时实时订阅与历史导入经 Tailscale 到达同一现场接口，回局域网后直连。

#### 7.4 模拟远端数据服务（mock_data_server）

开发环境提供模拟远端数据服务，完全模拟工程场景的数据链路：

```bash
# 方式 1：Docker（推荐，随基础设施一起启动）
docker compose -f deploy/docker/docker-compose.dev.yml up -d mock-data-server

# 方式 2：本地运行
cd mock_data_server
pip install -r requirements.txt
PYTHONPATH=/path/to/CLPM-MVP python -m uvicorn mock_data_server.main:app --host 0.0.0.0 --port 7106
```

服务启动后（Docker 方式宿主端口为 **17106**，本地运行方式端口为 7106）：
- 历史数据 API：`POST http://localhost:17106/api/services/v1/HistoryData/Get`（查 TDengine，Docker 方式）
- 实时数据 Hub：`WS ws://localhost:17106/signalr/realValueForClpmHub`（正弦波模拟，Docker 方式）
- 健康检查：`GET http://localhost:17106/health`（Docker 方式）

> **注意**：`mock_data_server/` 是独立目录，正式项目可整体删除，不影响主应用。

#### 7.5 诊断、批量任务与 TDengine

诊断支持手动、定时及预警事件触发；同回路同窗口的独立任务分别保存结果，同任务重投保留幂等检查。诊断任务页每 3 秒刷新执行进度，其他任务每 5 秒；完成后使用真实开始/结束时刻固定计时。

Redis 优先级为事件 0、人工 3、后台 9，子任务继承父优先级，显式指定值优先；优先级不能抢占已执行任务。预警巡检采用续租互斥，过期消息不补跑；自定义批量评估按需加载，不再等待整批预热。

TDengine 已使用 `LAST_ROW`、`LAST/INTERVAL/FILL(PREV)` 和批量 INSERT；诊断原始波形与质量码仍保留。客户端使用 taosrest HTTP，进程内读 8 / 写 2 线程隔离；仓储常规写批次为 500 行，历史直导为 5000 行。源码与测量边界见 [full-1043 验证报告](docs/过程文档/full-1043-性能修复与验证-2026-10-21.md)。

## 生产部署

> **现有现场升级**：沿用已部署的 `clpm-*` 容器、Compose、端口和数据卷；开发环境的 `clpm-mvp-*` 隔离不要求现场改名。full-1043 镜像 tar.gz 的传输、SHA 校验、四个应用容器重建及回退命令见 [ToDesk 人工部署说明](releases/部署指令-1043-ToDesk人工.md)。下面是一套源码新部署流程，不替代现场升级步骤。

### 环境要求

- Docker 24+ 与 Docker Compose v2
- 服务器最低配置：4 核 CPU / 8GB 内存 / 50GB 磁盘
- 宿主机开放端口：7141（前端与 `/api/v1` 反向代理）。PostgreSQL、Redis、后端 API，以及可选的 TDengine 仅在 Compose 网络内可达

### 部署步骤

#### 1. 准备配置文件

```bash
cp .env.prod.example .env.prod
```

编辑 `.env.prod`，**必须修改**以下占位符：

| 配置项 | 说明 | 生成命令 |
|---|---|---|
| `POSTGRES_PASSWORD` | PostgreSQL 密码 | 自定义强密码 |
| `REDIS_PASSWORD` | Redis 密码 | 自定义强密码 |
| `JWT_SECRET_KEY` | JWT 签名密钥（≥32 字符） | `openssl rand -hex 32` |
| `CORS_ORIGINS` | 允许的前端域名 | `["https://your-domain.com"]` |
| `TDENGINE_PASSWORD` | TDengine 密码（**必填**，计算一律本地 TDengine） | 内置 3.3.6.6 实例的 root 初始密码，或外部实例凭据 |
| `CELERY_WORKER_CONCURRENCY` | Celery worker 进程并发数 | `8`（模板与 Compose 默认值，需结合 CPU/内存/连接池容量调整） |
| `AAS_ENDPOINT` | OPC UA 服务地址（`AAS_SYNC_ENABLED=True`） | 现场 OPC UA Endpoint |

#### 2. 一键部署

```bash
./deploy/deploy.sh
```

部署脚本会自动完成：
1. 校验 `.env.prod` 与 `JWT_SECRET_KEY`（含 `ENV=production` 强制校验）
2. 构建 backend / frontend Docker 镜像（多阶段构建，同时打 `latest` + git SHA + 版本号 tag，回滚可用）
3. 启动 7 个服务容器（恒启用 `tdengine` profile——计算类历史数据查询一律本地 TDengine）
4. 等待健康检查并通过（含 celery `inspect ping`/`scheduled` 硬断言，失败即中止）
5. 输出服务访问地址

> **2026-07-28 部署链路加固**：两条部署路径（`deploy.sh` / `build-and-deploy.sh`）共用 `deploy/lib-migrate.sh`：无版本记录时执行 `stamp head`（仅标记，须先确认初始化后的 schema 与 head 一致），有版本记录时执行 `upgrade head` 应用增量迁移，失败即中止；已有部署升级前自动执行 `deploy/backup.sh`（TDengine 备份带 root 凭据，失败硬中止）；构建前跑测试门禁（ruff + pytest + check:type，`--skip-gate` 可紧急跳过）。

#### 3. 验证部署

```bash
# 查看服务状态
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine ps

# 后端健康检查
docker exec clpm-backend curl -fsS http://localhost:7101/health/ready

# 前端访问
curl http://localhost:7141/
```

#### 4. 监控（可选 monitoring profile，2026-07-28 新增）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine --profile monitoring up -d
```

启用 Prometheus（抓 `backend:7101/metrics`，内网白名单口径）+ Grafana（接 `deploy/grafana/` 现成 dashboard）+ node-exporter，告警规则见 `deploy/prometheus/alerts.yml`（后端 down、Celery 失败率、磁盘、抓取失联）。监控服务不暴露宿主端口，经 SSH 隧道或自行加端口映射访问。

### 服务架构

| 服务 | 容器 | 端口 | 说明 |
|---|---|---|---|
| frontend | clpm-frontend | 7141 | Nginx 静态托管 + /api/v1 反代 |
| backend | clpm-backend | 7101 | FastAPI + Uvicorn |
| celery-worker | clpm-celery-worker | - | 异步任务执行 |
| celery-beat | clpm-celery-beat | - | 定时任务调度 |
| postgres | clpm-postgres | 5432 | 关系型业务数据 |
| tdengine | clpm-tdengine | 6030/6041 | 时序数据 |
| redis | clpm-redis | 6379 | 缓存 + Celery Broker |

计算类历史数据查询一律走本地 TDengine（2026-07-20 架构决策），
`deploy.sh` / `rollback.sh` 恒启用 `--profile tdengine`，无需按数据源模式切换。
手工执行 Compose 命令时也应加上该 profile：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d
```

内置实例固定使用 TDengine 3.3.6.6（该版本起镜像支持
`TAOS_ROOT_PASSWORD`），使 `TDENGINE_PASSWORD` 真正应用到 root 账号。

### 常用运维命令

```bash
# 查看实时日志
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine logs -f

# 查看指定服务日志
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine logs -f backend

# 重启服务
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine restart backend

# 停止所有服务
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine down

# 停止并清除数据卷（慎用，会丢失数据）
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine down -v

# 重新构建并启动
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --build
```

### 版本回滚

```bash
./deploy/rollback.sh
```

回滚脚本会列出历史镜像版本，确认后回滚到上一版本。

### HTTPS 配置

当前生产 Nginx 默认使用 7141 端口的 HTTP 模式。`deploy/nginx.conf` 底部提供 HTTPS 升级模板；启用时需配置证书挂载与 80/443 端口：

- `deploy/ssl/fullchain.pem`（需自行创建）
- `deploy/ssl/privkey.pem`（需自行创建）

开启 HTTPS 前需创建 `deploy/ssl/` 并放置证书，同时按模板更新 `deploy/nginx.conf` 和 `docker-compose.prod.yml`。证书或 Nginx 配置更新后重新构建 frontend 镜像：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine build frontend
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d frontend
```

### 数据库初始化

PostgreSQL 容器首次启动会自动执行 `db/postgresql/01_schema.sql` 和 `db/postgresql/02_seed_data.sql` 完成建表与种子数据导入。如需重新初始化，需先清除数据卷：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine down -v
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d
```

## 当前有效文档

| 类型 | 文件 |
|---|---|
| 当前 PRD（v6.2） | `docs/设计文档/01-PRD/PRD.md` |
| 总体 FDS（v6.0） | `docs/设计文档/02-FDS/FDS.md` |
| 交付架构设计（v6.0） | `docs/设计文档/03-ADS/ADS.md` |
| 数据模型设计（v6.0） | `docs/设计文档/04-DDS/DDS.md` |
| API 接口设计（v6.0） | `docs/设计文档/05-IDS/IDS.md` |
| UI/UX 设计规范（v6.2） | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` |
| 重构后实现契约（v2.11） | `docs/设计文档/00-BASELINE/implementation-contract.md` |
| IA 优化实施主方案（v1.2，P0~P4 已完成） | `docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md`（统计报告一级化 / 模块热插拔 / 适用性 L0~L4） |
| IA 评审文档索引 | `docs/设计文档/IA 优化/README.md`（5 视角评审体系 + 四批评分总览 A-） |
| v4.0 重构实施方案（历史实施蓝图） | `docs/设计文档/CLPM_v4.0_系统重构实施方案.md` |
| 原型设计基线 | `DESIGN.md`（v3.1；设计基线，IA 与菜单口径以 `docs/MVP设计/00-信息架构.md` 为准） |
| 工作台 v2.0 方案/实施计划 | `docs/设计文档/CLPM工作台改进方案-v2.0.md` + `docs/过程文档/工作台v2实施计划-2026-08-25.md` |
| 版本锁定基线 | `CHANGELOG.md`（v7.5.6 能力清单/已知残留/冗余登记/技术基线） |
| 诊断/导入性能交付 | [full-1043 验证报告](docs/过程文档/full-1043-性能修复与验证-2026-10-21.md) + [人工部署命令](releases/部署指令-1043-ToDesk人工.md) |
| 冗余检查与文档对齐 | [2026-10-21 核查记录](docs/过程文档/文档对齐与冗余代码核查-2026-10-21.md) + [持续登记表](docs/过程文档/dead-code-registry-2026-09-24.md) |
| 原型代码入口 | `docs/设计文档/prototype/README.md` |

## 推荐阅读顺序

1. `docs/过程文档/design-documents-index-2026-06-16.md`
2. `docs/设计文档/00-BASELINE/implementation-contract.md`
3. `docs/设计文档/01-PRD/PRD.md`
4. `docs/设计文档/02-FDS/FDS.md`
5. `docs/设计文档/04-DDS/DDS.md`
6. `docs/设计文档/05-IDS/IDS.md`
7. `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md`
8. 需要追溯时再读 `docs/归档文档/` 目录中的历史文档

## 当前共识

| 主题 | 当前口径 |
|---|---|
| 产品定位 | 产品化、工具化的控制回路绩效治理与优化闭环平台，非项目型定制化系统 |
| 当前版本 | 产品文档基线 **v6.2**；实现契约 v2.11；锁定 tag **v7.5.6**。最新交付 **full-1046**（源码 `a62a668ef09b468e0ebfda3dcad29098747978be`，现场状态以 `/health/ready` 实测为准）。P0–P7 终局仍为 **G6/G7=CONDITIONAL_PASS，F02 容量验收待实际数据验证**，详见 `docs/设计文档/系统改造优化-2026-10-10/P7汇总与G7结论.md`；交付包不改变此验收结论 |
| 首版主线 | Phase 1 (MVP/V1.0)：跑通"自动评估、自动诊断、轻量跟踪、模块弹性交付、适用性分层"闭环 + 驾驶舱管理决策视图 + 回路工作台 |
| 首版范围 | 驾驶舱 6 页签（/cockpit/*）、回路工作台（/loop/workbench360）、回路监控（回路监视/预警事件/系统建议）、回路结构配置、性能评估（装置性能/指标分析/回路评估/评估计算任务/指标矩阵页/适用性阈值）、诊断中心（概览/记录/任务三页式 + 回路工作台诊断剖面）、参数整定（总览/记录/验证三页式 + 回路工作台整定剖面，门禁 L2+ 提示放行）、运维处置 v2.0（诊断建议/处置任务/处置工单/处置档案）、统计分析（数据质量+绩效/诊断/处置/收益报告+预警统计+订阅配置）、预警规则归配置、系统管理（基础信息/用户/审计/权限矩阵/字典/模块管理） |
| 模块架构 | **10 个一级菜单**：驾驶舱(0)/回路工作台(1)/回路监控(2)/性能评估(3)/回路诊断(4)/参数整定(5)/运维处置(6)/统计分析(7)/参数配置(8)/系统管理(9)（2026-10-05 驾驶舱整合 D6 十项重排改名；另有「我的工作」/my-work order=-1 工程师个人首页，P6）。诊断/整定/处置支持模块热插拔，按客户阶段弹性启用；回路运行态归回路工作台，预警事件归回路监控、规则归参数配置；双轴导航和各模块"配置→运行→分析"三态自包含 |
| AAS 数据模型 | AAS 同步 tag 位号（非回路实体），回路由用户创建并关联 7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D），数据质量主要针对 PV 值 |
| 核心模型 | `diagnosis_run` 诊断结果 → `loop_action_item` 建议审核 → `handling_order` 处置及验证；旧 Tracker 与旧诊断表保留归档，见 MVP 14 号文 |
| 工程主约束 | PRD v6.2 负责产品需求；实现契约 v2.11 负责当前 IA/路由/API/权限/状态机/KPI；UI/UX v6.2 负责视觉与交互（配套色彩约定表 v1.0 + 文案词表 v1.0）；页面标杆设计负责逐页高保真线框图与实施验收 |
| UI/UX 整改 | 2026-08-07~09 三轮（Phase 0 修信任 / Phase 1 立风格 / Phase 2 通动线 + Backlog 清理），Nielsen 20/40→32/40；现行规范 `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md`（整改过程清单/出口报告已于 2026-10-19 基线化清理，见 Git 历史） |
| 性能边界 | LTTB 降采样 maxPoints=2000，30 天时间窗口 |
| 安全边界 | 平台不写 DCS，只输出建议、证据、风险与回退方案 |

## v4.0 重构历史（2026-06-26）

下表记录当时 Phase 0-6 的实施提交，仅用于追溯，不替代当前代码、测试结果与实现契约的验收。

| 阶段 | 内容 | Commit |
|---|---|---|
| Phase 0 | ORM 模型层更新 | `02f3c5a` |
| Phase 1 | 数据预处理模块（8步Pipeline + 8类异常值检测） | `bdde45b` |
| Phase 2+3 | DataPlanner+Cache 与指标计算器并行开发 | `11d13e6` |
| Phase 4 | kpi_calc.py 整合 DataPlanner + MetricCalculator | `53fc21f` |
| Phase 5 | API 接口层扩展（波形批量/DataPlanner/任务管理/诊断标签） | `39859e5` `0dfd37b` |
| Phase 6 | 前端适配（4层架构：类型/API → 组件 → 页面 → 路由） | `86f356c` `3516641` `4bff65b` |
| 修复 | Celery worker 任务注册修复 | `207c882` |
| v6.0 文档统一升级 | PRD/ADS/IDS/FDS/DDS/实现契约/UIUX/DESIGN 全量升级；统一术语、状态机、API 路径与权限字段 | 历史记录 |

## 目录说明

| 文档/目录 | 用途 |
|---|---|
| `docs/预研文档/` | 包含竞品分析、市场研究、行业标准与政策背景预研资料 |
| `docs/设计文档/` | 包含所有核心技术文档，含 PRD、FDS、ADS、DDS、IDS、UIUX 及 Prototype 原型系统 |
| `docs/过程文档/` | 包含需求评审记录、重构计划、任务冻结包等日常过程记录文件 |
| `docs/归档文档/` | 包含历史失效版本的需求文档与过程评估报告，仅供追溯 |
| `docs/设计文档/01-PRD/PRD.md` | 当前唯一有效 PRD |
| `docs/设计文档/02-FDS/FDS.md` | 当前系统功能设计说明总册 |
| `docs/设计文档/03-ADS/ADS.md` | 当前系统架构交付设计 |
| `docs/设计文档/04-DDS/DDS.md` | 当前系统数据模型设计 |
| `docs/设计文档/05-IDS/IDS.md` | 当前系统 API 接口设计 |
| `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` | 当前可视化设计与用户体验规范 |
| `docs/设计文档/00-BASELINE/implementation-contract.md` | 重构后实现契约：IA、路由、API、权限、状态机、KPI 与阶段口径 |
| `docs/设计文档/页面标杆设计/` | 逐页高保真线框图与标杆设计规范（系统概览/关注队列/回路工作台/回路列表），含 HTML 线框图与 PNG 效果图 |
| `DESIGN.md` | 设计基线 v3.1（视觉/布局/组件横切设计约束；IA 与菜单口径以 `docs/MVP设计/00-信息架构.md` 为准） |
| `docs/设计文档/prototype/README.md` | 原型系统代码库入口说明 |
| `backend/` | FastAPI 后端（API + Celery 任务 + 算法引擎） |
| `frontend/` | Vue 3 前端 monorepo（web-antd 为生产应用） |
| `e2e/` | Playwright E2E 测试 |
| `db/` | 数据库 DDL 脚本（PostgreSQL + TDengine） |
| `deploy/` | 部署脚本 + Nginx 配置 + 开发环境容器编排 |
| `docker-compose.prod.yml` | 生产环境 Docker Compose 编排 |
| `Dockerfile.backend` / `Dockerfile.frontend` | 多阶段构建镜像定义 |
| `.env.prod.example` | 生产环境配置模板 |

## 维护规则

- 现行需求、原型、研发拆解和投标响应统一以 `docs/设计文档/01-PRD/PRD.md` 为准。
- 归档目录 (`docs/归档文档/`) 下的文件只用于历史追溯，不作为新一轮评审输入。
- 新一轮架构、设计或专项设计必须先吸收 approved 产品化架构和最新 PRD 的结论，再继续扩写。
- PDF 原始资料及外部参考手册统一存放于 `docs/预研文档/`。
- 版本发布使用 git tag 标记（如 `v1.0.0`），遵循语义化版本规范。
