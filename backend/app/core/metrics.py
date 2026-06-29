"""Prometheus metrics module (S3-B3).

定义应用级 Prometheus 指标并挂载 /metrics 端点：
- http_requests_total: HTTP 请求总数
- http_request_duration_seconds: HTTP 请求耗时
- db_pool_connections: 数据库连接池使用数
- celery_task_total: Celery 任务执行总数
"""

from __future__ import annotations

import time
from typing import Any

from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# 排除指标采集的路径（避免自引用和健康检查噪声）
_EXCLUDED_PATHS = {"/metrics", "/health"}


# ---------------------------------------------------------------------------
# 指标定义
# ---------------------------------------------------------------------------

http_requests_total = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "path", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["method", "path"],
)

db_pool_connections = Gauge(
    "db_pool_connections",
    "数据库连接池使用数",
)

celery_task_total = Counter(
    "celery_task_total",
    "Celery 任务执行总数",
    ["task_name", "status"],
)


# ---------------------------------------------------------------------------
# 中间件
# ---------------------------------------------------------------------------


class MetricsMiddleware(BaseHTTPMiddleware):
    """HTTP 请求指标采集中间件。

    记录请求计数和耗时，排除 /metrics 和 /health 路径。
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path
        method = request.method

        # 排除指标和健康检查路径
        if path in _EXCLUDED_PATHS:
            return await call_next(request)

        start_time = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            duration = time.perf_counter() - start_time
            http_requests_total.labels(method=method, path=path, status=status).inc()
            http_request_duration_seconds.labels(method=method, path=path).observe(duration)


# ---------------------------------------------------------------------------
# 路由注册
# ---------------------------------------------------------------------------


def setup_metrics(app: Any) -> None:
    """注册 /metrics 路由到 FastAPI 应用。"""
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)


__all__ = [
    "MetricsMiddleware",
    "celery_task_total",
    "db_pool_connections",
    "http_request_duration_seconds",
    "http_requests_total",
    "setup_metrics",
]
