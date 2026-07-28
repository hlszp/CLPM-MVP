"""Health check endpoint tests."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    """GET /health must return status ok and the configured version."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"


def test_openapi_docs_available() -> None:
    """GET /docs must return 200 (Swagger UI)."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema_available() -> None:
    """GET /openapi.json must return the OpenAPI schema."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "CLPM"


# ---------------------------------------------------------------------------
# Readiness probe（health_ready）：依赖故障时必须返回 503
# ---------------------------------------------------------------------------


class _FakeConn:
    async def execute(self, *args: Any, **kwargs: Any) -> None:
        return None


class _FakeConnCtx:
    async def __aenter__(self) -> _FakeConn:
        return _FakeConn()

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeEngine:
    """connect() 返回异步上下文管理器的 engine 替身。"""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def connect(self) -> _FakeConnCtx:
        if self._fail:
            raise RuntimeError("db down")
        return _FakeConnCtx()


def _patch_all_deps_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """将 readiness 三个依赖（Postgres/Redis/TDengine）全部 mock 为可用。"""
    from app.api.v1.endpoints import health as health_ep

    monkeypatch.setattr(health_ep, "engine", _FakeEngine())
    monkeypatch.setattr(health_ep, "redis_client", AsyncMock())
    monkeypatch.setattr("app.core.tdengine.execute_sql", AsyncMock(return_value=[["clpm"]]))


@pytest.mark.asyncio
async def test_readiness_all_ok_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有依赖就绪时 readiness 返回 200 + status=ok。"""
    from app.api.v1.endpoints import health as health_ep

    _patch_all_deps_ok(monkeypatch)
    resp = await health_ep.health_ready()
    assert resp.status_code == 200
    body = json.loads(bytes(resp.body))
    assert body["status"] == "ok"
    assert body["checks"] == {"postgres": "ok", "redis": "ok", "tdengine": "ok"}


@pytest.mark.asyncio
async def test_readiness_degraded_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """任一依赖故障时 readiness 返回 503 + status=degraded。"""
    from app.api.v1.endpoints import health as health_ep

    _patch_all_deps_ok(monkeypatch)
    # PostgreSQL 连接失败
    monkeypatch.setattr(health_ep, "engine", _FakeEngine(fail=True))
    resp = await health_ep.health_ready()
    assert resp.status_code == 503
    body = json.loads(bytes(resp.body))
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"].startswith("fail:")
    assert body["checks"]["redis"] == "ok"
    assert body["checks"]["tdengine"] == "ok"


@pytest.mark.asyncio
async def test_readiness_redis_failure_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 故障同样触发 503。"""
    from app.api.v1.endpoints import health as health_ep

    _patch_all_deps_ok(monkeypatch)
    mock_redis = AsyncMock()
    mock_redis.ping.side_effect = ConnectionError("redis down")
    monkeypatch.setattr(health_ep, "redis_client", mock_redis)
    resp = await health_ep.health_ready()
    assert resp.status_code == 503
    body = json.loads(bytes(resp.body))
    assert body["status"] == "degraded"
    assert body["checks"]["redis"].startswith("fail:")
