# CLPM

危化企业控制回路性能治理与优化平台（Control Loop Performance Monitoring & Optimization）。

产品文档基线：**v6.1**（当前需求、IA 与 ZL 工业设计规范口径）。后端运行时版本由 `APP_VERSION` 管理（当前默认 `1.0.0`），发布版本由 Git tag 管理；三者用途不同，不要求数值相同。

## 项目简介

CLPM 是面向危化企业控制回路的绩效治理与优化闭环平台，覆盖"监控 → 评估 → 诊断 → 整定"全流程，提供：

- **工作台门户**：12 项 KPI 指标看板（3+1+8 体系）+ 低效回路 Top10 + 趋势摘要 + 待办异常
- **回路管理**：AAS Tag 同步 / 回路台账 / Tag 关联 / 实时监控
- **性能评估**：KPI 看板 / 低效排行 / 统计分析 / 指标配置（指标定义 / 引擎规则 / 类型权重 / 级别权重 / 异常值检测参数 / KPI 算法参数 / 执行记录） / 可信度标识 / 工业桌面端驾驶舱样式
- **诊断中心**：诊断配置（阈值/启停真实生效）/ 异常诊断（振荡/阀门粘滞/参数过激过保守/外扰/质量异常/输出饱和 + 传感器故障与 Harris 指数，D-S 证据融合）/ 事件+体检双轨自动诊断 / Action Tracker（KPI A/B 对比 + 同步 PDF 建议书）/ 统计
- **回路整定**：FOPDT/SOPDT/IPDT 模型辨识 + IMC/Lambda/Z-N/Cohen-Coon/SIMC 五种整定算法 + 闭环仿真
- **评估任务**：标准/自定义评估任务全生命周期（触发 → 进度跟踪 → 阶段时间线 → 通知），作为性能评估执行体系的一部分
- **系统管理**：用户管理 / 审计日志 / 权限矩阵 / 自动报表

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
| 测试 | pytest（3409 passed，1 skipped，15 deselected，33 xfailed）+ vitest（415 passed）+ Playwright E2E（59 passed） |

## 快速开始（开发环境）

### 环境要求

- Node.js ≥ 22.18.0 + pnpm ≥ 10.0.0
- Python 3.12 + [uv](https://docs.astral.sh/uv/) ≥ 0.4
- Docker 24+（推荐 [Orbstack](https://orbstack.dev/) 作为容器运行时）

### 1. 启动基础设施（PostgreSQL + TDengine + Redis）

```bash
docker compose -f deploy/docker/docker-compose.dev.yml up -d
```

### 2. 启动后端

```bash
cd backend
cp .env.example .env          # 首次执行
uv sync                        # 安装依赖
uv run alembic upgrade head    # 执行数据库迁移
uv run uvicorn app.main:app --host 0.0.0.0 --port 7101 --reload
```

后端 API 文档：http://localhost:7101/docs

> **v6.1 说明**：后端启动时自动启动 Celery Beat（定时调度）和 Celery Worker（任务执行）子进程，无需手动启动。修改 Celery 任务代码后需重启后端让新代码生效。

### 3. 启动前端

```bash
cd frontend
pnpm install
pnpm run dev:antd              # 默认端口 5666
```

前端访问地址：http://localhost:5666

### 5. 默认账号

5 个种子用户，密码统一为 `admin123`：

| 用户名 | 角色 | 权限范围 |
|---|---|---|
| admin | ADMIN | 全部模块 |
| ic_engineer | IC_ENGINEER | 全部业务模块 |
| pe_engineer | PE_ENGINEER | 监控/评估/工作台 |
| expert | EXPERT | 诊断/整定 |
| sponsor | SPONSOR | 工作台只读 |

> **首次登录强制改密（2026-07-28 起）**：全新部署（含生产 initdb 种子）的 5 个种子用户 `must_change_password=True`，登录后除改密/登出外所有写操作返回 403，改密成功后自动解除；dev 库已改密账户不受影响。前端强制跳转改密页待落地（当前依赖用户自行改密）。

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
PYTHONPATH=/path/to/CLPM python -m uvicorn mock_data_server.main:app --host 0.0.0.0 --port 7106
```

服务启动后：
- 历史数据 API：`POST http://localhost:7106/api/services/v1/HistoryData/Get`（查 TDengine）
- 实时数据 Hub：`WS ws://localhost:7106/signalr/realValueForClpmHub`（正弦波模拟）
- 健康检查：`GET http://localhost:7106/health`

> **注意**：`mock_data_server/` 是独立目录，正式项目可整体删除，不影响主应用。

## 生产部署

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
| UI/UX 设计规范（v6.1） | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` |
| 重构后实现契约（v2.2） | `docs/设计文档/00-BASELINE/implementation-contract.md` |
| v4.0 重构实施方案（历史实施蓝图） | `docs/设计文档/CLPM_v4.0_系统重构实施方案.md` |
| 原型设计基线 | `DESIGN.md`（v3.0；视觉历史基线，现行路由以实现契约 v2.2 为准） |
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
| 当前版本 | 产品文档基线 **v6.1**；后端运行时默认 `1.0.0`；发布版本以 Git tag 为准。全量测试基线（2026-07-29）：pytest 3409 passed / vitest 415 passed / E2E 59 passed / `alembic check` 退出码 0 |
| 首版主线 | Phase 1 (MVP/V1.0)：跑通"自动评估、自动诊断、轻量跟踪"闭环 |
| 首版范围 | 工作台门户、回路管理（AAS tag 同步/回路创建/tag 关联/监控）、性能评估（指标配置/引擎规则/看板/排行/统计）、诊断中心（指标配置/诊断/异常跟踪/统计）、系统管理；回路整定原型页面设计 |
| 模块架构 | 6 模块 + 1 门户：工作台/回路管理/性能评估/诊断中心/回路整定/系统管理（任务管理是性能评估子模块），各模块"配置→运行→分析"三态自包含 |
| AAS 数据模型 | AAS 同步 tag 位号（非回路实体），回路由用户创建并关联 7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D），数据质量主要针对 PV 值 |
| 核心模型 | Action Tracker 轻量跟踪（PENDING → IN_PROGRESS → IMPLEMENTED/IGNORED），诊断中心子模块 |
| 工程主约束 | PRD v6.2 负责产品需求；实现契约 v2.2 负责当前 IA/路由/API/权限/状态机/KPI；UI/UX v6.1 负责视觉与交互 |
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
| `DESIGN.md` | 设计基线 v3.0（视觉/布局/组件历史基线；现行路由与实现口径以实现契约 v2.2 为准） |
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
