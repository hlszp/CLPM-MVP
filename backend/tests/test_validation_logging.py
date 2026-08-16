"""422 参数校验日志埋点测试（全局 RequestValidationError handler）。

排障场景：非 DEBUG 下响应体已脱敏，服务端 warning 日志是唯一详细现场。
用独立最小 app 验证（不依赖业务 mock 链），等价覆盖 GET /loops 等所有端点。
"""

import logging

from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from app.core.exceptions import register_exception_handlers

LOGGER_NAME = "app.core.exceptions"


def _make_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/_probe/echo")
    async def _probe(
        page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    ) -> dict:
        return {"pageSize": page_size}

    return TestClient(app)


def test_validation_failure_logs_detail(caplog):
    """超限参数（等价 pageSize=200 打 /loops）→ 422 且日志含 loc/type/ctx/input。"""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        resp = _make_client().get("/_probe/echo", params={"pageSize": 200})

    assert resp.status_code == 422
    text = caplog.text
    assert "Validation failed" in text
    assert "GET" in text and "/_probe/echo" in text
    # 字段路径 + 约束类型 + 约束上限 + 原始输入值，四要素齐全
    assert "pageSize" in text
    assert "less_than_equal" in text
    assert "'le': 100" in text
    assert "200" in text


def test_validation_ok_no_warning_log(caplog):
    """合法参数不应产生校验告警日志。"""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        resp = _make_client().get("/_probe/echo", params={"pageSize": 50})

    assert resp.status_code == 200
    assert "Validation failed" not in caplog.text
