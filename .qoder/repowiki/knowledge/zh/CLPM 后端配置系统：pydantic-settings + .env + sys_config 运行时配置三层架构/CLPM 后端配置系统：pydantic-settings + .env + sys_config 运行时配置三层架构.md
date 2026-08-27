---
kind: configuration_system
name: CLPM 后端配置系统：pydantic-settings + .env + sys_config 运行时配置三层架构
category: configuration_system
scope:
    - '**'
source_files:
    - backend/app/core/config.py
    - backend/.env.example
    - backend/app/services/datasource_config.py
    - backend/app/models/sys_config.py
    - backend/app/main.py
    - backend/alembic/env.py
    - docker-compose.prod.yml
    - deploy/deploy.sh
    - backend/pyproject.toml
---

## 1. 采用的配置体系

CLPM 后端采用 **三层配置加载与覆盖** 的架构，核心由 `pydantic-settings` 驱动：

- **静态默认值层**：`app/core/config.py` 中的 `Settings(BaseSettings)` 类定义所有配置项、类型、默认值与安全校验（如生产环境强制 JWT_SECRET_KEY ≥32 字符、禁止开发默认密码）。
- **环境变量/文件层**：通过 `model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)` 从 `.env` 加载；`.env.example` 提供完整键清单，`docker-compose.prod.yml` 通过 `env_file: .env.prod` 注入容器环境变量；`deploy/deploy.sh` 在部署前对 `.env.prod` 做占位符与必填字段校验。
- **运行时数据库层**：`sys_config` 表（`app/models/sys_config.py`）作为“运行时真相源”，通过 `app/services/datasource_config.py` 的 `preload_datasource_config()` 在 FastAPI lifespan 启动时预载到 `settings` 内存，覆盖 `.env` 中的业务 URL/Token/SignalR Hub 等动态配置。

此外，`alembic/env.py` 直接复用 `settings.postgres_dsn` 构造数据库连接，避免 alembic.ini 中 ConfigParser 插值导致的 `%` 编码问题。

## 2. 关键文件与包

- `backend/app/core/config.py` — Settings 模型、DSN 生成器、生产安全校验
- `backend/.env.example` — 全部可配置项清单（PostgreSQL/TDengine/Redis/JWT/Celery/SignalR/GAP_BACKFILL 等）
- `backend/app/services/datasource_config.py` — sys_config 读写、settings 内存同步、Tailscale 网络模式切换、连通性测试
- `backend/app/models/sys_config.py` — SysConfig 表模型（key/value/description/updated_by/updated_at，key 唯一索引）
- `backend/app/main.py` — lifespan 中调用 `preload_datasource_config()`、`load_enabled_modules()`、`preload_outlier_params()`、`preload_algorithm_params()` 等预载逻辑
- `backend/alembic/env.py` — 迁移脚本复用 settings，过滤 comment-only ops
- `docker-compose.prod.yml` — 生产编排，通过 `env_file: .env.prod` 注入各服务，Celery worker/beat 独立容器
- `deploy/deploy.sh` — 部署前强制校验 `ENV=production`、JWT_SECRET_KEY、TDENGINE_PASSWORD、SIGNALR_HUB_URL 等
- `backend/pyproject.toml` — 声明依赖 `pydantic-settings>=2.5`、`python-dotenv>=1.0`

## 3. 架构与设计约定

### 3.1 配置来源优先级
运行时生效顺序：`sys_config`（数据库） > `.env` / 环境变量 > `Settings` 默认值。`datasource_config.get_datasource_config()` 先查 sys_config，缺失则回退 `settings` 默认值。

### 3.2 两类配置域
- **基础设施配置**（DB/TDengine/Redis/JWT/CORS/AAS 等）：仅通过 `.env` 或环境变量设置，启动后不变更。
- **运行时数据源配置**（dataSourceType/networkMode/historyApiUrl/historyApiToken/signalrHubUrl/signalrEnabled/gapBackfill* 等）：通过 UI 链路配置页写入 sys_config，`update_datasource_config()` 即时 `setattr(settings, ...)` 同步内存，部分需重启（signalrEnabled 因订阅器在 lifespan 初始化；dataSourceType 因 Provider 单例）。

### 3.3 启动预载机制
FastAPI lifespan startup 依次执行：
1. 预载数据源配置 → `preload_datasource_config(db)`
2. 预载模块启用状态 → `load_enabled_modules(db)`
3. 预载异常值检测参数 → `preload_outlier_params(db)`
4. 预载指标算法参数 → `preload_algorithm_params(db)`
5. 预载可信度阈值并启动 pub/sub 订阅线程
6. 启动实时数据订阅器
任何一步失败均记录 warning 并继续，不阻塞启动。

### 3.4 生产环境隔离
`main._is_production()` 依据 `ENV=production` 判断：生产环境下 lifespan **跳过** 自动拉起 Celery Beat/Worker（由 docker-compose 独立 celery-beat/celery-worker 容器接管），且 `deploy/deploy.sh` 强制要求 `.env.prod` 必须显式设置 `ENV=production`，否则中止部署以防任务双消费。

### 3.5 安全约束
- `Settings.validate_security()` 在模块加载时执行：生产环境禁止空 JWT_SECRET_KEY、长度 <32、使用开发默认 PG/TDengine/Redis 密码、AAS_SECURITY_MODE=None。
- `deploy/deploy.sh` 在部署阶段二次校验 JWT_SECRET_KEY、POSTGRES_PASSWORD、REDIS_PASSWORD、TDENGINE_PASSWORD、SIGNALR_HUB_URL（当 SIGNALR_ENABLED=true）。
- sys_config 中 historyApiToken 返回一律打码（保留前后各 4 位），内部真实调用通过 `mask_token=False` 取原始值。

### 3.6 配置变更审计
`datasource_config.update_datasource_config()` 每次更新都写入 `SysAuditLog(target_type="sys_config")`，记录 before/after JSON 快照；networkMode 切换 Tailscale 失败时自动回滚 sys_config 与 settings，保持 DB 与实际链路一致。

## 4. 约定与约束

- **新增配置项必须同时出现在三处**：`Settings` 类（含默认值）、`.env.example`、`DATASOURCE_CONFIG_KEYS` / `_SETTINGS_ATTR_MAP`（若为运行时可配置项）。这是 `datasource_config.py` 中集中映射表所强制的模式。
- **敏感信息不得硬编码**：密码字段在 Settings 中默认空字符串，注释明确“必填，通过 .env 设置”；生产环境校验拒绝空值与开发默认值。
- **运行时配置变更语义**：字段不传 = 保持不变；空串 = 显式清空；传入含 `****` 的 token 视为前端误回传打码值，忽略该字段。
- **网络模式切换幂等**：Tailscale 切换失败时回滚 sys_config 与 settings，并在审计日志中标记 `rolledBack`。
- **Alembic 迁移不比较注释**：`env.py` 通过 `_process_revision_directives` 过滤 comment-only ops，避免历史迁移未写注释导致的 schema drift 噪音。
- **Celery 进程管理受 ENV 控制**：非 production 下 lifespan 自动 spawn Beat/Worker 子进程并带看门狗；production 下完全交由独立容器，禁止本进程清理（`_should_skip_exit_hooks` 守卫工具进程）。
- **CORS_ORIGINS 支持 `__AUTO__` 模板替换**：`deploy/deploy.sh` 部署时根据本机 IP 动态替换，避免手动维护服务器地址。

## 5. 适用性说明

该配置系统贯穿后端全部运行期行为：数据库连接、消息队列、缓存、外部 API、SignalR 实时推送、断点续传、熔断限流、鉴权密钥、CORS、模块开关、诊断触发条件、可信度阈值、指标算法参数等均由此统一管理，是 CLPM 平台的核心基础设施之一。