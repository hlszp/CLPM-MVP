---
kind: error_handling
name: 后端统一异常体系：BizError + FastAPI 全局异常处理器 + 中间件降级
category: error_handling
scope:
    - '**'
source_files:
    - backend/app/core/exceptions.py
    - backend/app/main.py
    - backend/app/api/deps.py
    - backend/app/middleware/rate_limit.py
    - backend/app/middleware/idempotency.py
    - backend/app/middleware/request_id.py
    - backend/app/tasks/celery_app.py
    - backend/app/api/upload_guard.py
    - backend/app/services/data_source/remote_api_provider.py
    - backend/app/services/data_import.py
---

## 1. 整体方案

CLPM 后端基于 **FastAPI**，采用「自定义业务异常类 + 全局异常处理器 + 中间件兜底」的三层错误处理架构。所有 HTTP 响应统一遵循 IDS v3.2 规范体例：

```python
{"code": <error_code>, "message": <error_message>, "data": null}
```

该规范在 `app/core/exceptions.py` 中通过 `_error_body()` 构造，并由 `register_exception_handlers(app)` 集中注册到 FastAPI 应用（见 `app/main.py:934`）。

## 2. 核心异常类型与分类

### 2.1 业务异常 `BizError`
- 定义位置：`backend/app/core/exceptions.py:36`
- 字段：`code`（稳定错误码）、`message`（用户可读消息）、`status_code`（HTTP 状态码）、`data`（可选附加数据）
- 用途：所有业务层、鉴权层、参数校验失败均抛出 `BizError`，由全局 handler 转为统一 JSON 响应
- 使用范围：`api/deps.py`（JWT/权限）、`api/upload_guard.py`、各 `api/v1/endpoints/*.py`、以及大量 service 调用点

### 2.2 领域特定异常
- `TDengineError`（`core/tdengine.py`）：TDengine 连接/查询失败
- `RemoteApiCircuitOpenError`（`services/data_source/remote_api_provider.py`）：远端 API 熔断
- `HistoryDataSourceError`（`services/data_import.py`）：历史数据源不可用
- `NonRetryableError`（`tasks/report_generator.py`）：报告生成任务中的不可重试错误
- `ValidationError`（`services/alert_rule_engine/dsl.py`）：DSL 解析错误

这些异常在各自模块内被捕获并转换为 `BizError` 或直接记录日志后返回，不向上传播。

### 2.3 框架异常统一处理
全局处理器覆盖四类异常：
| 异常类型 | 处理器 | 行为 |
|---|---|---|
| `BizError` | `_handle_biz_error` | 原样透传 code/message/status_code/data |
| `RequestValidationError` | `_handle_validation_error` | 脱敏 Pydantic 校验错误；DEBUG 模式返回完整 errors，生产仅返回通用提示；日志保留完整 loc/type/msg/ctx |
| `StarletteHTTPException` | `_handle_http_exception` | 包装为 `ERR_HTTP_{status}` |
| `Exception`（兜底） | `_handle_unhandled` | 记录 `logger.exception`，返回 `ERR_INTERNAL` 500 |

## 3. 中间件级错误处理

### 3.1 限流中间件 (`middleware/rate_limit.py`)
- 对 `/api/v1/auth/login`、`/refresh`、`/password`、`/users` 等敏感端点进行 Redis 滑动窗口限流
- 超限直接返回 `{"code": "ERR_RATE_LIMITED", ...}` 429，带 `Retry-After` 头
- Redis 不可用时降级放行（不阻断请求），记录 warning 日志

### 3.2 幂等性中间件 (`middleware/idempotency.py`)
- 对 POST/PUT 请求，读取 `Idempotency-Key` header，在 Redis 中缓存首次成功响应 24 小时
- 重复 key 直接返回缓存响应，避免重复执行写操作
- Redis 不可用时降级为正常请求，记录 warning 日志

### 3.3 请求追踪中间件 (`middleware/request_id.py`)
- 注入 `X-Request-ID`（来自 header 或生成 UUID），写入 contextvar 供全链路日志追踪
- 所有异常响应自动携带 CORS 头（通过 `_add_cors_headers`）

## 4. 异步任务错误处理（Celery）

- `AsyncTask.on_failure`：任务重试耗尽后发送死信队列 `dead_letter`，记录 task_id、task_name、exc 详情
- Celery 配置：`task_reject_on_worker_lost=True`（Worker 崩溃重投）、`task_time_limit=1800s`（硬超时）、`task_soft_time_limit=1500s`（软超时）、`result_expires=7*24*3600`（结果过期）
- 任务内部普遍使用 `try/except Exception` 包裹关键步骤，记录 `logger.error/warning` 后继续或 re-raise
- 预载阶段（worker_process_init）失败仅 warning，不阻塞 worker 启动

## 5. 约定与约束

1. **统一响应体**：所有错误必须通过 `BizError` 或全局处理器返回 `{code, message, data}` 结构，禁止直接在 endpoint 中 return JSONResponse
2. **错误码命名**：业务错误码以 `ERR_` 前缀（如 `ERR_TOKEN_INVALID`、`ERR_PERMISSION_DENIED`、`ERR_RATE_LIMITED`、`ERR_INTERNAL`），便于前端按码分类处理
3. **校验错误脱敏**：生产环境不暴露 Pydantic 内部 `loc/type/ctx`，仅返回用户友好提示；详细现场仅在服务端日志保留
4. **中间件降级策略**：Redis 不可用时，限流和幂等性中间件均降级放行而非拒绝请求，保证服务可用性
5. **CORS 头注入**：所有异常响应统一添加 CORS 头，确保跨域场景下错误也能正确传递
6. **未捕获异常兜底**：全局 `Exception` 处理器确保任何未预料异常都返回 500 且记录堆栈，不会让进程崩溃
7. **测试验证**：测试用例（如 `tests/integration/test_grade_distribution_pg.py`、`tests/test_aas.py`）显式断言 `BizError.code` 值，确保错误码契约稳定