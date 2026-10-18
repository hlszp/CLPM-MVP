"""Common response envelope schemas (IDS v3.2 §6)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ResponseEnvelope(BaseModel):
    """Unified response envelope: ``{code, message, data}``."""

    code: str = "0"
    message: str = "success"
    data: Any = None


class ApiResponse[T](BaseModel):
    """统一响应格式泛型包装器（S2-C4）。

    用于端点 ``response_model`` 声明，使 OpenAPI 文档展示响应结构：
    - ``code``: 业务状态码，``"0"`` 表示成功
    - ``message``: 描述信息
    - ``data``: 业务数据，类型由泛型参数 ``T`` 决定
    """

    code: str = "0"
    message: str = "success"
    data: T | None = None


def success(data: Any = None, message: str = "success") -> dict[str, Any]:
    """Build a success response dict."""
    return {"code": "0", "message": message, "data": data}
