"""Prometheus metrics module (S3-B3).

定义应用级 Prometheus 指标并挂载 /metrics 端点：
- http_requests_total: HTTP 请求总数
- http_request_duration_seconds: HTTP 请求耗时
- db_pool_connections: 数据库连接池使用数
- celery_task_total: Celery 任务执行总数
"""

from __future__ import annotations

import ipaddress
import time
from typing import Any

from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

# 排除指标采集的路径（避免自引用和健康检查噪声）
_EXCLUDED_PATHS = {"/metrics", "/health"}

# /metrics 访问控制白名单：仅放行内网/环回地址。
# 指标端点不做 JWT 认证（Prometheus 抓取通常无凭证），改用来源 IP 白名单，
# 避免暴露内部指标细节。含 Tailscale CGNAT 段（100.64.0.0/10，公网模式经
# Tailscale subnet router 透明转发时来源为该网段）。
_ALLOWED_CLIENT_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fe80::/10",
        "100.64.0.0/10",
    )
)


def _is_internal_client(host: str | None) -> bool:
    """判断来源 IP 是否属于内网白名单。"""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _ALLOWED_CLIENT_NETWORKS)


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
        raw_path = request.url.path
        method = request.method

        # 排除指标和健康检查路径
        if raw_path in _EXCLUDED_PATHS:
            return await call_next(request)

        start_time = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            duration = time.perf_counter() - start_time
            # label 使用路由模板（如 /api/v1/loops/{loop_id}）而非原始路径，
            # 避免路径实参（UUID 等）导致每个 ID 生成一个新时间序列（基数爆炸）；
            # 404 等未匹配路由无 route，归一为 'unknown'
            route = request.scope.get("route")
            path = getattr(route, "path", None) or "unknown"
            http_requests_total.labels(method=method, path=path, status=status).inc()
            http_request_duration_seconds.labels(method=method, path=path).observe(duration)


# ---------------------------------------------------------------------------
# 路由注册
# ---------------------------------------------------------------------------


class _InternalOnlyASGIMiddleware:
    """纯 ASGI 中间件：仅放行内网白名单客户端，其余返回 403。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            client = scope.get("client")
            host = client[0] if client else None
            if not _is_internal_client(host):
                response = PlainTextResponse("Forbidden", status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def setup_metrics(app: Any) -> None:
    """注册 /metrics 路由到 FastAPI 应用（仅内网客户端可访问）。"""
    metrics_app = make_asgi_app()
    app.mount("/metrics", _InternalOnlyASGIMiddleware(metrics_app))


__all__ = [
    "MetricsMiddleware",
    "celery_task_total",
    "db_pool_connections",
    "http_request_duration_seconds",
    "http_requests_total",
    "setup_metrics",
]
