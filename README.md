# CLPM-MVP

危化企业控制回路性能治理与优化平台（Control Loop Performance Monitoring & Optimization）— **MVP 精简 + 闭环重建版**（自 CLPM v6.2 派生，设计事实来源见 `docs/MVP设计/`）。

产品文档基线：**v6.2**（派生自原项目，MVP 差异以 `docs/MVP设计/` 为准）。后端运行时版本由 `APP_VERSION` 管理（当前 `7.0.0`，与锁定 tag 对齐），发布版本由 Git tag 管理（当前 `v7.0.0`，2026-08-28 锁定），变更记录见 `CHANGELOG.md`。

## 项目简介

CLPM-MVP 是面向危化企业控制回路的绩效治理与优化闭环平台，覆盖"**监控 → 评估 → 诊断 → 整定 → 处置 → 统计报告**"完整业务闭环 + 管理决策视图（2026-08-23 IA 优化 P0~P4 完成）+ 单屏工作台驾驶舱（2026-08-25 v2.0），提供：

- **工作台**（v2.0，order=0，全角色可见）：`/workbench` 单屏 5 Tab（总览/评估/诊断/整定/处置，button+v-show 切换），范围+时间窗跨 Tab 共享；总览三窗口 KPI + 帕累托 + 漏斗 + 单元平稳率条形图；评估排行/热力矩阵/趋势/分布；诊断异常回路表 + 结论时间线 + 适用性门禁 + 规则命中统计；整定批次/队列/前后散点；处置看板 + 漏斗泳道 + 人员负载；模块 4 态 dot/面纱/横幅联动热插拔
- **监控**：装置总览（监控默认首页，L0~L4 适用性 5 段堆叠横条）/ 回路监视（列表页标杆 v2.0，新增适用性列+筛选）/ 预警事件 / 关注队列（三来源聚合 + 四级优先级 + 标杆 v2.0 分组详情抽屉）/ 回路工作台单页多区（运行态 + 评分趋势 + 诊断卡 + 活跃关注项 + L2 适用性横幅）/ Tag 关联 / 实时监控
- **性能评估**：KPI 看板 / **指标分析页**（6 卡概览 + TOP10 + 装置对比 + 行动清单，2026-08-25）/ 节点排名 / 统计分析 / 指标配置（指标定义 / 引擎规则 / 类型权重 / 级别权重 / 异常值检测参数 / KPI 算法参数 / **适用性阈值 Tab**）/ 可信度标识；L0/L1 回路灰色"不适用"分类独立不计入"差"
- **诊断**（MVP 重建，2026-08-16）：诊断工作台（发起+结果一体，两页式 IA）/ 诊断记录（历史+导出）；6+1 原因分类体系 + 元算子架构 + 证据污染链；仅手动触发；**适用性门禁**：L0/L1 阻止发起、L2 返回 conditionWarning 条件异常横幅
- **回路整定**（MVP 重建，2026-08-19）：整定工作台（辨识→矩阵→仿真→确认单页流程）/ 整定记录 / 效果验证（前后窗曲线对比 + X-Y）；后端复用 Phase 2 算法栈（ARX/ARMAX/IV 辨识 + 全算法矩阵 + 闭环仿真）；**适用性门禁**：L3 以下入口 disabled + 返回 `ERR_TUNING_FITNESS_INSUFFICIENT`，与数据不足区分
- **处置**（MVP 新建 2026-08-19，升 v2.0 双实体 2026-08-20）：处置工作台双 Tab（建议审核：接受/驳回/忽略 + 批量转工单；工单执行：清单 + 详情抽屉含反馈追加/提交验证/KPI 对比/闭环重开）；建议实体（loop_action_item，5 态审核状态机）+ 处置工单（handling_order，六态状态机 + 编号 HD-YYYYMMDD-NNN + 多建议合一单）；KPI 前后对比验证
- **统计报告**（一级菜单 2026-08-23 IA 优化 P0 落地；报告模块优化 P0/P1 已落地 2026-08-28）：管理总览（固定 12 格骨架 + 成熟度 S1/S2/S3 自适应 + S2/S3 阶段内容）/ 绩效报告（由评估-KPI 报表迁入）/ **数据质量**（参评率/数据健康率/INCONCLUSIVE 率/可信度分布/未参评归因，P1）/ 诊断报告（基于 DiagnosisRun 重建）/ 处置报告（由处置-统计迁入）/ 收益报告（整定前后 KPI 对比 + 自控率提升曲线 + 装置标杆）/ **预警统计**（MTTA/MTTR/误报率/severity 趋势/TOP 规则回路，P1）/ 订阅配置（由系统-自动报表迁入；自动生成为占位实现 P0-1 止血，「立即生成」置灰，P3 做实后恢复）；报告页接入模块热插拔降级横幅；旧路径全局 redirect 就位
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
| 测试 | pytest（4934 collected，2026-08-28）+ vitest（workspace 全量 549 passed / 62 文件，2026-08-28 已纳入 CI 阻塞门禁）+ Playwright E2E（92 用例声明，2026-08-28 新增指标矩阵 3 例 + 报告 P1 2 例；环境类既有失败见 06-UIUX 出口报告） |

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
| pe_engineer | PE_ENGINEER | 监控/评估/工作台 |
| expert | EXPERT | 监控工作台（只读）/诊断/整定 |
| sponsor | SPONSOR | 工作台只读 |

> **改密口径（2026-08-03 调整）**：种子用户 `must_change_password` 统一为 `FALSE`，部署后可直接使用写操作，**无技术强制改密**。生产环境上线后必须由实施人员按 `releases/DEPLOY-GUIDE.md` 验收清单人工修改全部默认密码并验收确认。

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
| 历史数据（采集用） | 远端 AAS 历史数据接口 | 有且仅有「数据管理 → 历史数据导入」任务调用，把远端数据补齐到本地 TDengine |
| 实时数据 | SignalR Hub（唯一） | `ws://<现场>/signalr/realValueForClpmHub`，开发/生产一致；写入 Redis 实时缓存（页面实时监控）+ 可选写回本地 TDengine（KPI 计算） |

#### 7.1 历史数据导入接口（remote_api）

在 UI「链路配置」页（`/loop/aas-sync`）配置一次即持久化到 sys_config：

- 历史数据 API 地址：如 `http://192.168.100.2:81/api/services/v1/HistoryData/Get`
- 鉴权 Token（可选）、请求超时

该接口**只在手工启动历史数据导入任务时**由 `data_import.py` 直接调用，任何计算任务（KPI 整点/回填/诊断/趋势）都不会调用它。对接接口规范见 `docs/设计文档/05-IDS/HisDATA_API.md`。

> 兼容说明：`DATA_SOURCE_TYPE` 环境变量已废止（仅作配置保留），不再影响计算路径的数据源选择。

#### 7.2 实时数据订阅（SignalR/WebSocket，唯一实时数据源）

同样在「链路配置」页配置：

- SignalR Hub URL：如 `ws://192.168.100.2:81/signalr/realValueForClpmHub`
- 断线重连基础间隔（指数退避 5s→30s 封顶）
- 实时数据写回本地 TDengine 宽表（`REALTIME_WRITEBACK_ENABLED`，默认开启）

对接接口规范见 `docs/设计文档/05-IDS/RealDATA_API.md`。
实时值查询 API：`GET /api/v1/realtime?tagCodes=LIC-101.PV,TIC-101.PV`

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

## 生产部署

> **MVP 说明**：`docker-compose.prod.yml` 当前仍沿用原项目容器名（`clpm-*`）与端口（7141/7101），**生产隔离改造尚未执行**；开发环境已完成 `clpm-mvp-*` 隔离。生产部署前需先完成容器名/端口/数据卷的 MVP 隔离改造。

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
| `CELERY_WORKER_CONCURRENCY` | Celery worker 进程并发数 | `2`（应与 CPU/连接池容量联合调整） |
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

> **2026-07-28 部署链路加固**：两条部署路径（`deploy.sh` / `build-and-deploy.sh`）均强制执行 `alembic upgrade head`（公共函数 `deploy/lib-migrate.sh`，失败即中止）；已有部署升级前自动执行 `deploy/backup.sh`（TDengine 备份带 root 凭据，失败硬中止）；构建前跑测试门禁（ruff + pytest + check:type，`--skip-gate` 可紧急跳过）。

#### 3. 验证部署

```bash
# 查看服务状态
docker compose --env-file .env.prod -f docker-compose.prod.yml ps

# 后端健康检查
curl http://localhost:7141/health

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
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f

# 查看指定服务日志
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f backend

# 重启服务
docker compose --env-file .env.prod -f docker-compose.prod.yml restart backend

# 停止所有服务
docker compose --env-file .env.prod -f docker-compose.prod.yml down

# 停止并清除数据卷（慎用，会丢失数据）
docker compose --env-file .env.prod -f docker-compose.prod.yml down -v

# 重新构建并启动
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
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
docker compose --env-file .env.prod -f docker-compose.prod.yml build frontend
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d frontend
```

### 数据库初始化

PostgreSQL 容器首次启动会自动执行 `db/postgresql/01_schema.sql` 和 `db/postgresql/02_seed_data.sql` 完成建表与种子数据导入。如需重新初始化，需先清除数据卷：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml down -v
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d
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
| 版本锁定基线 | `CHANGELOG.md`（v7.0.0 能力清单/已知残留/冗余登记/技术基线） |
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
| 当前版本 | 产品文档基线 **v6.2**；实现契约 v2.11；后端运行时默认 `1.0.0`；发布版本以 Git tag 为准。本轮基线（2026-08-26，macbook 分支）：**工作台 v2.0 M2 主体落地**（`/workbench` 单屏 5 Tab：总览/评估/诊断/整定/处置，order=0 全角色可见；V3 整定/处置 Tab + Calm UI 优化，报告 `docs/过程文档/workbench-5tab-optimization-report-2026-08-26.html`）+ 监控菜单重排为装置总览→回路监视→预警事件→关注队列→回路工作台 + 列表页标杆 v2.0（关注队列/回路监视/性能总览/任务列表）+ 指标分析页（评估第 5 页）+ 预警预设规则/三级阈值/metric_threshold + 评估任务重构（统一任务列表 + 回填抽屉，legacy recompute 页删除）；上轮（2026-08-23）：IA 优化 P0~P4 全部落地（统计报告升一级菜单、模块热插拔、适用性 L0~L4 分层+诊断整定门禁）；上上轮（2026-08-14）：页面标杆设计落地（系统概览页 R1~R4 重写）；pytest 4812 collected / vitest 434 passed / ruff✅ / check:type✅ |
| 首版主线 | Phase 1 (MVP/V1.0)：跑通"自动评估、自动诊断、轻量跟踪、模块弹性交付、适用性分层"闭环 + 管理决策视图 + 单屏工作台 |
| 首版范围 | 工作台 v2.0（5 Tab 驾驶舱）、监控（装置总览/回路监视/预警事件/关注队列/回路工作台）、平台驾驶舱（/cockpit）、回路结构配置、性能评估（看板/指标分析/排行/任务/统计/指标矩阵页/适用性阈值）、诊断中心（诊断/异常跟踪/统计 + 16 号文 Phase A 档案/复诊对比）、回路整定（门禁 L3+）、处置 v2.0（建议审核+工单执行）、统计报告（管理总览+数据质量+绩效/诊断/处置/收益报告+预警统计+订阅配置）、预警规则归配置、系统管理（基础信息/用户/审计/权限矩阵/字典/模块管理） |
| 模块架构 | **9 个一级菜单**：工作台(order=0)/监控/评估/诊断/整定/处置/报告/配置/系统（工作台为跨模块总览入口，无 meta.module 不参与热插拔过滤；统计报告 2026-08-23 一级化，处置统计从处置迁出、KPI 报表从评估迁出、自动报表从系统迁出统一归口，成熟度 S1/S2/S3 自适应管理员可锁定）；诊断/整定/处置支持模块热插拔，按客户阶段弹性启用；回路运行态归入监控下回路工作台，预警事件归监控、规则归配置；双轴导航和各模块"配置→运行→分析"三态自包含 |
| AAS 数据模型 | AAS 同步 tag 位号（非回路实体），回路由用户创建并关联 7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D），数据质量主要针对 PV 值 |
| 核心模型 | Action Tracker 轻量跟踪（PENDING → IN_PROGRESS → VERIFYING → CLOSED，P1a 闭环），诊断中心子模块 |
| 工程主约束 | PRD v6.2 负责产品需求；实现契约 v2.11 负责当前 IA/路由/API/权限/状态机/KPI；UI/UX v6.2 负责视觉与交互（配套色彩约定表 v1.0 + 文案词表 v1.0）；页面标杆设计负责逐页高保真线框图与实施验收 |
| UI/UX 整改 | 2026-08-07~09 三轮（Phase 0 修信任 / Phase 1 立风格 / Phase 2 通动线 + Backlog 清理），Nielsen 20/40→32/40；进度事实来源 `docs/设计文档/06-UIUX/ui-ux-rectification-checklist-2026-08-08.md`，出口报告 `p2-exit-report-2026-08-09.md` |
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
