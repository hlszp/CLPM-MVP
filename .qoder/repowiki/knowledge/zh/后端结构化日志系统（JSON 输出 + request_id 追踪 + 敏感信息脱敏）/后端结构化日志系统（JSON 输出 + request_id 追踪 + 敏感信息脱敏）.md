---
kind: logging_system
name: 后端结构化日志系统（JSON 输出 + request_id 追踪 + 敏感信息脱敏）
category: logging_system
scope:
    - '**'
source_files:
    - backend/app/core/logging.py
    - backend/app/middleware/request_id.py
    - backend/app/main.py
    - backend/app/tasks/celery_app.py
    - backend/alembic/env.py
    - backend/tests/test_observability.py
---

## 1. 使用的系统与框架

后端采用 Python 标准库 `logging` 模块，未引入第三方日志框架（如 loguru、structlog）。所有日志通过自定义的 `app/core/logging.py` 统一初始化，以 JSON 格式输出到 stdout，由容器/进程管理器收集；DEBUG 模式下切换为人类可读的行格式。

- **核心文件**：`backend/app/core/logging.py`（格式化器、脱敏、setup_logging）、`backend/app/middleware/request_id.py`（request_id 注入）、`backend/app/main.py`（lifespan 中调用 setup_logging）、`backend/app/tasks/celery_app.py`（Celery worker/beat 子进程独立日志输出到文件）。
- **日志文件位置**：`backend/logs/` 目录下按进程分文件：`uvicorn.log`、`celery-worker.log`、`celery-beat.log`、`mode_fix/*.log` 等。

## 2. 架构与约定

### 初始化流程
FastAPI 应用启动时，在 `lifespan` 最开头调用 `setup_logging()`，配置 root logger：
- 根级别：`DEBUG` 模式 → `logging.DEBUG`；生产/测试 → `logging.INFO`。
- DEBUG 模式下自动抑制高噪音框架日志（sqlalchemy.engine/pool、httpx、httpcore、asyncpg、urllib3、websockets），将其降级为 WARNING，避免 dev 环境日志爆炸。
- 移除已有 handler 防止 reload 重复输出。
- 单一流向：`StreamHandler(sys.stdout)`，由外部采集。

### 双 Formatter
- **生产/测试**：`JsonFormatter` 输出单行 JSON，字段包括 `timestamp`（UTC ISO）、`level`、`logger`、`message`、可选 `request_id`、可选 `exc_info`，以及通过 `extra=` 传入的任意业务字段。
- **DEBUG**：`_DebugFormatter` 输出 `%(asctime)s | %(levelname)-8s | %(name)s | %(message)s` 格式，并在消息前拼接 `[request_id]`。

### 请求追踪（S3-B4）
`RequestIdMiddleware` 从 `X-Request-ID` 请求头读取或生成 UUID，写入 `contextvars.ContextVar`（`_request_id_ctx`），`JsonFormatter` 在序列化时自动读取并附加到每条日志的 `request_id` 字段；响应也回写 `X-Request-ID` 头。该 contextvar 跨 FastAPI 中间件、路由、服务层传播，实现全链路追踪。

### 敏感信息脱敏（S3-B5）
`_sanitize_message()` 使用正则对日志消息进行替换，覆盖以下模式：`password=xxx`、`"password": "xxx"`、`token=xxx`、`Bearer xxx`、JWT (`eyJ...`)。脱敏同时应用于 JSON 和 DEBUG 模式。

### Celery 子进程日志
`main.py` 通过 `subprocess.Popen` 启动 Celery Beat/Worker，将它们的 stdout/stderr 合并写入 `logs/celery-beat.log`、`logs/celery-worker.log`。Beat/Worker 命令行参数 `-l info` 控制其内部日志级别。Celery 任务模块内直接使用 `logging.getLogger(__name__)`，继承 root logger 的配置。

### 业务代码中的使用方式
各 endpoint 和服务模块通过 `import logging; logger = logging.getLogger(__name__)` 获取模块级 logger，然后调用 `logger.info/warning/error/exception/debug`。异常路径普遍使用 `logger.exception(...)` 自动附带 traceback。没有统一的业务 logger 包装类，直接依赖标准库。

## 3. 约定与约束

- **日志输出目标**：所有应用日志统一输出到 stdout（由 uvicorn/Celery 进程捕获到对应文件），禁止业务代码直接写文件。
- **结构化字段**：新增可查询字段应通过 `logger.info("msg", extra={"field": value})` 传递，会被 `JsonFormatter` 自动附加到 JSON 对象中。
- **请求追踪**：所有 HTTP 请求日志必须包含 `request_id`，由中间件自动注入，不得手动设置。
- **敏感信息**：任何可能包含密码、token、Bearer、JWT 的消息都会经 `_sanitize_message` 处理，开发者无需手动脱敏。
- **DEBUG 噪音控制**：DEBUG 模式下 sqlalchemy/httpx 等底层库被强制降级为 WARNING，业务日志不受影响。
- **Celery 日志隔离**：Beat/Worker 子进程有独立日志文件，不混入主进程 stdout，便于区分调度与执行日志。
- **错误记录规范**：可恢复异常用 `warning`，不可恢复/需要排查的用 `error` 或 `exception`（后者自动带 traceback）。
- **Alembic 迁移**：`alembic/env.py` 通过 `logging.config.fileConfig` 单独配置 Alembic 日志，与主应用日志分离。
- **测试验证**：`tests/test_observability.py` 中对 `_sanitize_message` 有专门测试，确保脱敏规则稳定。