"""Observability tests (S3-B1~B5).

测试覆盖：
- S3-B5: _sanitize_message() 各种脱敏模式
- S3-B4: RequestIdMiddleware 添加 X-Request-ID 响应 header
- S3-B3: /metrics 端点返回 200
- S3-B2: send_alert() webhook 为空时仅记录日志
- S3-B1: check_aas_connection() mock 模式返回 ok
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.logging import _sanitize_message
from app.services.alerting import send_alert
from app.services.data_link_monitor import check_aas_connection

# ===========================================================================
# S3-B5: 日志敏感信息脱敏
# ===========================================================================


class TestSanitizeMessage:
    """测试 _sanitize_message() 各种脱敏模式。"""

    def test_password_equals(self) -> None:
        """password=xxx → password=***"""
        result = _sanitize_message("user login password=secret123")
        assert "secret123" not in result
        assert "password=***" in result

    def test_password_json(self) -> None:
        """JSON password 字段脱敏。"""
        result = _sanitize_message('{"username": "admin", "password": "mypwd"}')
        assert "mypwd" not in result
        assert '"password": "***"' in result

    def test_token_equals(self) -> None:
        """token=xxx → token=***"""
        result = _sanitize_message("auth token=abc123def456")
        assert "abc123def456" not in result
        assert "token=***" in result

    def test_bearer_token(self) -> None:
        """Bearer xxx → Bearer ***"""
        result = _sanitize_message("Authorization: Bearer eyJhbGciOiJIUzI1")
        assert "eyJhbGciOiJIUzI1" not in result
        assert "Bearer ***" in result

    def test_jwt_token(self) -> None:
        """eyJxxx → eyJ***"""
        result = _sanitize_message("jwt payload: eyJhbGciOiJIUzI1NiJ9.payload.sig")
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "eyJ***" in result

    def test_no_sensitive_info(self) -> None:
        """无敏感信息的消息不修改。"""
        original = "正常日志消息，无敏感信息"
        result = _sanitize_message(original)
        assert result == original

    def test_multiple_sensitive_patterns(self) -> None:
        """多种敏感信息同时存在时全部脱敏。"""
        msg = "password=secret token=abc Bearer xyz eyJpayload"
        result = _sanitize_message(msg)
        assert "secret" not in result
        assert "abc" not in result
        assert "xyz" not in result
        assert "payload" not in result.replace("eyJ***", "")


# ===========================================================================
# S3-B4: RequestIdMiddleware
# ===========================================================================


class TestRequestIdMiddleware:
    """测试 RequestIdMiddleware。"""

    def test_response_has_request_id_header(self, client: TestClient) -> None:
        """响应包含 X-Request-ID header。"""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers
        assert resp.headers["X-Request-ID"]  # 非空

    def test_custom_request_id_is_echoed(self, client: TestClient) -> None:
        """自定义 X-Request-ID 被回显。"""
        custom_id = "my-custom-request-id-12345"
        resp = client.get("/health", headers={"X-Request-ID": custom_id})
        assert resp.status_code == 200
        assert resp.headers["X-Request-ID"] == custom_id

    def test_generated_request_id_is_uuid_like(self, client: TestClient) -> None:
        """未提供 X-Request-ID 时生成 UUID 格式的 ID。"""
        resp = client.get("/health")
        assert resp.status_code == 200
        request_id = resp.headers["X-Request-ID"]
        # UUID 格式：8-4-4-4-12
        parts = request_id.split("-")
        assert len(parts) == 5


# ===========================================================================
# S3-B3: Prometheus /metrics 端点
# ===========================================================================


class TestMetricsEndpoint:
    """测试 /metrics 端点。"""

    @pytest.fixture(autouse=True)
    def _allow_metrics_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """默认放行 /metrics 访问（TestClient 来源 host 为 'testclient'，非 IP）。"""
        monkeypatch.setattr("app.core.metrics._is_internal_client", lambda host: True)

    def test_metrics_returns_200(self, client: TestClient) -> None:
        """GET /metrics 返回 200。"""
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_contains_http_requests_total(self, client: TestClient) -> None:
        """/metrics 响应包含 http_requests_total 指标。"""
        # 先发一个请求产生指标数据
        client.get("/health")
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        assert "http_requests_total" in body

    def test_metrics_forbidden_for_external_client(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非内网白名单客户端访问 /metrics 返回 403。"""
        monkeypatch.setattr("app.core.metrics._is_internal_client", lambda host: False)
        resp = client.get("/metrics")
        assert resp.status_code == 403

    def test_metrics_label_uses_route_template(self, client: TestClient) -> None:
        """指标 label 使用路由模板而非原始路径（防基数爆炸）。

        请求 /api/v1/loops/{uuid}（未认证返回 401/403，但路由已匹配），
        /metrics 中应出现模板 label（/loops/{loop_id}，当前 starlette 分支的
        _IncludedRouter 延迟包含使 route.path 不含外层 /api/v1 前缀，
        同一端点所有请求的 label 恒定，基数有界），且原始 UUID 不得作为 label 出现。
        """
        loop_uuid = "12345678-1234-1234-1234-1234567890ab"
        client.get(f"/api/v1/loops/{loop_uuid}")
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        assert 'path="/loops/{loop_id}"' in body
        assert loop_uuid not in body

    def test_metrics_label_unknown_for_unmatched_route(self, client: TestClient) -> None:
        """未匹配路由（404）的指标 label 归一为 'unknown'。"""
        client.get("/no-such-endpoint-observability-test")
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert 'path="unknown"' in resp.text


class TestMetricsInternalClientGuard:
    """_is_internal_client 内网白名单判定。"""

    @pytest.mark.parametrize(
        "host",
        ["127.0.0.1", "::1", "10.1.2.3", "172.16.0.9", "192.168.1.10", "100.64.1.1"],
    )
    def test_internal_hosts_allowed(self, host: str) -> None:
        from app.core.metrics import _is_internal_client

        assert _is_internal_client(host) is True

    @pytest.mark.parametrize("host", ["8.8.8.8", "1.1.1.1", None, "", "not-an-ip"])
    def test_external_or_invalid_hosts_denied(self, host: str | None) -> None:
        from app.core.metrics import _is_internal_client

        assert _is_internal_client(host) is False


# ===========================================================================
# S3-B2: 告警通知机制
# ===========================================================================


class TestSendAlert:
    """测试 send_alert()。"""

    @pytest.mark.asyncio
    async def test_send_alert_empty_webhook_no_raise(self) -> None:
        """webhook URL 为空时仅记录日志，不抛出异常。"""
        # ALERT_WEBHOOK_URL 默认为空
        await send_alert("测试告警", "这是一条测试消息", severity="warning")

    @pytest.mark.asyncio
    async def test_send_alert_critical_severity(self) -> None:
        """critical 级别告警不抛出异常。"""
        await send_alert("严重告警", "严重问题", severity="critical")

    @pytest.mark.asyncio
    async def test_send_alert_info_severity(self) -> None:
        """info 级别告警不抛出异常。"""
        await send_alert("信息告警", "信息消息", severity="info")


# ===========================================================================
# S3-B1: 数据采集链路监控
# ===========================================================================


class TestCheckAasConnection:
    """测试 check_aas_connection()。"""

    @pytest.mark.asyncio
    async def test_mock_mode_returns_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mock 模式返回 status=ok, mode=mock。"""
        # 显式启用 mock 模式，避免 .env 中 AAS_MOCK_MODE=False 影响测试
        monkeypatch.setattr("app.services.data_link_monitor.settings.AAS_MOCK_MODE", True)
        result = await check_aas_connection()
        assert result["status"] == "ok"
        assert result["mode"] == "mock"
