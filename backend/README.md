# CLPM 后端服务

危化企业控制回路性能治理与优化平台（Control Loop Performance Monitoring & Optimization，CLPM-MVP）后端 API 服务，基于 FastAPI 构建，提供"监控 → 评估 → 诊断 → 整定 → 处置 → 统计报告"全流程闭环 + 工作台 BFF 的 RESTful 接口。

产品锁定版本：**v7.5.6**；最新交付镜像：**v7.5.6-1043-full**。运行时由 `APP_VERSION` 标识；交付包不代表已部署，详见 [manifest](../releases/manifest-1043.json)。

## 项目简介

CLPM 后端为前端门户与业务模块提供 API 支撑，核心能力包括：

- **认证鉴权**：JWT 双 Token（Access 30min / Refresh 7d）+ RBAC 五角色 + bcrypt + Redis 黑名单
- **驾驶舱与工作台**：驾驶舱 `/cockpit/*` 六页签及回路工作台 `/loop/workbench360` 共用 BFF / 业务服务；原 `/workbench` 前端路由已重定向，旧 BFF 空壳端点已清理
- **回路管理**：AAS Tag 同步 / 回路台账 / Tag 关联 / 实时监控
- **性能评估**：指标配置 / KPI 计算引擎 / 全局看板 / 低效排行 / 指标分析 / 适用性评估 L0~L4
- **诊断中心**（MVP 重建）：6+1 原因分类体系 + 元算子架构 + 证据污染链；手动/定时/预警事件三层触发，手动 L0 警告放行产出 DATA_INSUFFICIENT，夜间全量过滤 L0
- **回路整定**（MVP 重建）：ARX/ARMAX/IV 模型辨识 + 全算法矩阵 + 闭环仿真 RK4 + 效果验证；L0/L1 阻断，L2/L3 提示放行
- **处置**：建议实体（loop_action_item，5 态审核）+ 处置工单（handling_order，六态 + KPI 前后对比验证）
- **统计报告**：绩效/数据质量（含位号体检）/诊断/处置/收益/预警统计及订阅配置；原管理总览前端已下线，驾驶舱承担管理视图
- **智能预警**：规则引擎（阈值/漂移/组合/可信度 DSL + 预设规则 + 三级阈值）+ 事件流 + /ws/alerts 推送
- **系统管理**：基础信息 / 用户管理 / 审计日志 / 权限矩阵 / 字典管理 / 模块管理（热插拔）
- **异步任务**：Celery + Beat 调度；开发环境由后端 lifespan 管理，生产 Compose 使用独立 worker/beat 服务，沿用原启动管理方式，避免重复进程

平台遵循"只读 DCS、只输出建议"的安全边界，不直接写入 DCS。

## 技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | Python 3.12 + FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0（async）+ asyncpg |
| 数据校验 | Pydantic v2 + pydantic-settings |
| 关系数据库 | PostgreSQL 16 |
| 时序数据库 | TDengine 3.3.6.6；点表 `st_point_data_v1`，taosrest HTTP + TDengine 原生 SQL |
| 缓存/队列 | Redis 7 |
| 异步任务 | Celery + Celery Beat（Redis Broker） |
| 鉴权 | JWT（PyJWT）+ bcrypt + RBAC |
| 算法 | NumPy + SciPy（模型辨识 / PID 整定 / 闭环仿真 RK4） |
| 数据接入 | SignalR 实时订阅 / HTTP 历史导入 / AAS Tag 元数据同步 |
| 报表 | ReportLab（PDF 生成） |
| 可观测性 | Prometheus + 自定义中间件（request_id / 限流 / 幂等 / 指标） |
| 包管理 | [uv](https://docs.astral.sh/uv/) ≥ 0.4 |
| 测试 | pytest + pytest-asyncio；默认排除 integration，真实服务测试需显式隔离端点，固定交付结果见下方报告 |
| 代码质量 | ruff（lint + format）+ mypy（strict） |

## 目录结构

```
backend/
├── app/                        # 应用主代码
│   ├── main.py                 # FastAPI 应用工厂（create_app）
│   ├── api/                    # API 路由层
│   │   ├── deps.py             # 公共依赖（get_db / get_current_user / require_roles）
│   │   └── v1/endpoints/       # 业务端点（auth/loops/aas/performance/diagnosis/...）
│   ├── core/                   # 核心基础设施
│   │   ├── config.py           # 配置（pydantic-settings，.env 加载）
│   │   ├── db.py               # SQLAlchemy 异步引擎与 Session
│   │   ├── redis.py            # Redis 异步客户端
│   │   ├── tdengine.py         # TDengine REST 工具与兼容入口
│   │   ├── tdengine_native.py  # 原生 SQL 执行（taosrest HTTP，读写线程隔离）
│   │   ├── security.py         # JWT + bcrypt
│   │   ├── exceptions.py       # 全局异常处理
│   │   ├── logging.py          # 结构化日志
│   │   └── metrics.py          # Prometheus 指标
│   ├── middleware/             # 中间件（限流/幂等/request_id/指标）
│   ├── models/                 # SQLAlchemy ORM 模型
│   ├── schemas/                # Pydantic 请求/响应模型
│   ├── services/               # 业务服务层（领域逻辑）
│   └── tasks/                  # Celery 异步任务
│   │   ├── celery_app.py       # Celery 应用与 Beat 调度
│   │   ├── kpi_calc.py         # KPI 计算任务
│   │   ├── diagnosis_v2.py     # 诊断引擎任务（MVP 重建）
│   │   ├── workbench.py        # 工作台预计算/SLA 扫描/MV 刷新（5 beat）
│   │   ├── report_generator.py # 报表生成任务
│   │   └── alert_patrol.py     # 预警巡检（租约互斥、续租与过期消息丢弃）
├── alembic/                    # 数据库迁移脚本
│   ├── env.py
│   └── versions/               # 迁移版本
├── tests/                      # 测试套件
│   ├── conftest.py             # 共享 fixture（mock DB/Redis）
│   ├── golden/                 # 黄金基线数据（FFT/FOPDT/整定）
│   └── test_*.py               # 测试用例
├── scripts/                    # 工具脚本
│   └── export_openapi.py       # 导出 OpenAPI 静态规范
├── .env.example                # 环境变量模板
├── .python-version             # Python 版本锁定
├── alembic.ini                 # Alembic 配置
├── pyproject.toml              # 项目依赖与工具配置
└── uv.lock                     # 依赖锁文件
```

## 开发环境配置

### 环境要求

- Python 3.12（`requires-python = ">=3.12,<3.13"`）
- [uv](https://docs.astral.sh/uv/) ≥ 0.4
- Docker 24+（用于启动 PostgreSQL / TDengine / Redis）

### 1. 启动基础设施

```bash
docker compose -f deploy/docker/docker-compose.dev.yml up -d
```

启动 PostgreSQL（宿主 17102）、TDengine（宿主 17104/17115）、Redis（宿主 17103）、mock 数据服务（17106），容器名 `clpm-mvp-*`（MVP 端口隔离：原项目端口 +10000），并自动执行 `db/postgresql/01_schema.sql` 与 `db/postgresql/02_seed_data.sql` 完成建表与种子数据导入。

### 2. 安装依赖

```bash
cd backend
uv sync
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

`.env` 关键配置项（开发环境默认值已预置）：

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `DEBUG` | 调试模式（开启 /docs） | `True` |
| `POSTGRES_PASSWORD` | PostgreSQL 密码 | `clpm_dev_2026` |
| `TDENGINE_PASSWORD` | TDengine 密码 | `taosdata` |
| `JWT_SECRET_KEY` | JWT 签名密钥 | 开发占位符（生产必须替换） |
| `CELERY_BROKER_URL` | Celery Broker | `redis://localhost:17103/1` |

### 4. 数据库初始化

```bash
uv run alembic upgrade head
```

> 注：容器首次启动导入初始 DDL 与种子数据；`alembic upgrade head` 继续执行尚未应用的增量迁移。

### 5. 启动开发服务器

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 17101 --reload --reload-include '*.py'
```

> MVP 隔离端口：后端 API **17101**（原项目 7101 +10000）；后端启动时 lifespan 自动拉起 Celery Worker 和 Beat 子进程，**严禁手工再启动**。修改 Celery 任务代码后需重启后端让新代码生效。

> ⚠️ 不要改回 `uvicorn[standard]`：其传递依赖 uvloop 在 macOS 26 上会导致
> `uv__stream_io` 空指针崩溃（SIGSEGV，"Python 意外退出"弹窗，每次 uvicorn
> worker 重启时随机触发）。pyproject 已拆出 standard extras 并排除 uvloop，
> uvicorn `--loop auto` 自动使用 CPython asyncio 原生事件循环。
>
> ⚠️ 必须保留 `--reload-include '*.py'`：celery worker/beat 的日志持续写入
> `backend/logs/`，watchfiles 默认监听整个 backend/，日志变化会触发 uvicorn
> --reload 无限重启循环（每 ~25 秒杀 celery 再拉起）。只监听 *.py 后 reload
> 仅对代码变更生效。

### 6. 默认账号

5 个种子用户，密码统一为 `admin123`：

| 用户名 | 角色 | 权限范围 |
|---|---|---|
| admin | ADMIN | 全部模块 |
| ic_engineer | IC_ENGINEER | 全部业务模块 |
| pe_engineer | PE_ENGINEER | 业务查看及诊断/整定操作，按权限矩阵控制 |
| expert | EXPERT | 业务查看及诊断/整定操作，按权限矩阵控制 |
| sponsor | SPONSOR | 业务只读，默认进入驾驶舱 |

## 常用命令

```bash
# 启动开发服务器（热重载；必须带 --reload-include '*.py'，见 §5 注释）
uv run uvicorn app.main:app --host 0.0.0.0 --port 17101 --reload --reload-include '*.py'

# 运行测试
uv run pytest -q

# 运行测试并生成覆盖率报告
uv run pytest --cov=app --cov-report=term-missing

# 代码检查
uv run ruff check .

# 代码格式化
uv run ruff format .

# 类型检查
uv run mypy app

# 数据库迁移
uv run alembic upgrade head          # 应用迁移
uv run alembic revision --autogenerate -m "描述"  # 创建迁移

# schema 漂移检查（提交前必跑，退出码必须为 0）
uv run alembic check

# 导出 OpenAPI 静态规范
uv run python scripts/export_openapi.py
```

> **沿用既有 Celery 进程管理方式**：开发由后端 lifespan 管理，生产使用 Compose 独立 worker/beat 服务；避免额外启动重复进程。

## API 文档

开发环境（`DEBUG=True`）下，FastAPI 自动暴露交互式 API 文档：

| 文档 | URL | 说明 |
|---|---|---|
| Swagger UI | http://localhost:17101/docs | 交互式 API 调试 |
| ReDoc | http://localhost:17101/redoc | 只读 API 文档 |
| OpenAPI JSON | http://localhost:17101/openapi.json | OpenAPI 3.1 规范 |

健康检查端点：`GET /health`（根路径，无业务前缀，用于容器探针）。

业务接口统一前缀：`/api/v1/*`，主要包括：

- `/api/v1/auth` - 认证（登录/登出/刷新）
- `/api/v1/plant-nodes` - 工厂节点
- `/api/v1/loops` - 回路管理
- `/api/v1/realtime` - 实时值查询（SignalR 缓存）
- `/api/v1/performance` - 性能评估
- `/api/v1/dashboard` - 装置总览门户（BFF 聚合）
- `/api/v1/workbench` - 驾驶舱/工作台共用 BFF（现役端点以 OpenAPI 为准）
- `/api/v1/diagnosis` - 诊断中心（v2，热插拔启用时挂载）
- `/api/v1/tuning` - 回路整定（热插拔启用时挂载）
- `/api/v1/handling` - 处置（建议审核 + 工单，热插拔启用时挂载）
- `/api/v1/alert` - 智能预警（规则/事件）
- `/api/v1/monitor` - 监控（关注队列等）
- `/api/v1/reports` - 统计报告（按权限矩阵控制）
- `/api/v1/configs` - 系统配置（sys_config 读写）
- `/api/v1/dicts` - 字典管理
- `/api/v1/site` - 站点基础信息
- `/api/v1/users` / `/api/v1/audit-logs` - 用户管理 / 审计日志
- `/api/v1/ws/realtime` / `/api/v1/ws/alerts` - 实时数据 / 预警 WebSocket

生产环境（`DEBUG=False`）关闭 `/docs`、`/redoc`、`/openapi.json`，仅通过 nginx 反向代理 `/api/v1/*`。

## 部署说明

### Docker 镜像构建

后端镜像定义在项目根目录 `Dockerfile.backend`（多阶段构建）：

```bash
docker build -f Dockerfile.backend -t clpm-backend:latest .
```

### Docker Compose 部署

生产环境使用项目根目录 `docker-compose.prod.yml` 编排 7 个服务：

| 服务 | 容器 | 说明 |
|---|---|---|
| backend | clpm-backend | FastAPI + Uvicorn（仅容器间暴露 7101） |
| frontend | clpm-frontend | Nginx 静态托管 + 反向代理（默认 HTTP 7141） |
| celery-worker | clpm-celery-worker | 异步任务执行 |
| celery-beat | clpm-celery-beat | 定时任务调度 |
| postgres | clpm-postgres | 关系型业务数据 |
| tdengine | clpm-tdengine | 时序数据 |
| redis | clpm-redis | 缓存 + Celery Broker |

一键部署：

```bash
cp .env.prod.example .env.prod   # 编辑真实配置
./deploy/deploy.sh               # 构建并启动
```

源码新部署参见项目根目录 `README.md` 与 `docs/设计文档/07-DEPLOYMENT/部署运维设计.md`；现有现场使用 [full-1043 逐条升级说明](../releases/部署指令-1043-ToDesk人工.md)，保留既有 Compose、数据卷及基础设施。

### 配置要求

生产环境（`ENV=production`）启动时强制校验：

- `JWT_SECRET_KEY` 必须设置且长度 ≥ 32 字符
- `POSTGRES_PASSWORD` / `REDIS_PASSWORD` 必须设置且不得使用开发默认值
- `TDENGINE_PASSWORD` 必须与部署的 TDengine 实例一致（计算类历史数据查询一律本地 TDengine，恒校验）
- `AAS_SECURITY_MODE` 不得为 `None`（必须 Sign 或 SignAndEncrypt）

## 测试

默认测试套件排除 `integration`，多数依赖使用 mock。真实 PostgreSQL、Redis、TDengine 回归需显式启用并指向本机隔离端点；推送门禁中的 `alembic check` 也需要可连接的本地数据库：

```bash
uv run pytest -q                    # 默认测试集合，不含 integration
uv run pytest tests/test_auth.py    # 单模块测试
uv run pytest -k "auth"             # 按关键字筛选
```

黄金基线测试（`tests/golden/`）用于算法回归校验（FFT 振荡检测 / FOPDT 辨识 / PID 整定）。

full-1043 的 7,300 项后端、12 项真实隔离集成及进度页面验证见 [验证报告](../docs/过程文档/full-1043-性能修复与验证-2026-10-21.md)。报告区分本机合成性能、受控接口页面验证与尚未完成的生产复测；不使用测试数量代替现场验收。

## 相关文档

| 文档 | 路径 |
|---|---|
| 项目总览 | `../README.md` |
| PRD | `../docs/设计文档/01-PRD/PRD.md` |
| 功能设计 | `../docs/设计文档/02-FDS/FDS.md` |
| 架构设计 | `../docs/设计文档/03-ADS/ADS.md` |
| 数据模型 | `../docs/设计文档/04-DDS/DDS.md` |
| API 接口设计 | `../docs/设计文档/05-IDS/IDS.md` |
| 关键算法说明 | `../docs/设计文档/03-ADS/关键算法设计说明.md` |
| 部署运维 | `../docs/设计文档/07-DEPLOYMENT/部署运维设计.md` |
| 调度、导入与 TDengine 现行说明 | [运维手册](../docs/过程文档/ops-runbook.md) |
| 冗余检查与清理边界 | [2026-10-21 核查记录](../docs/过程文档/文档对齐与冗余代码核查-2026-10-21.md) |
