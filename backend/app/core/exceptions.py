"""Global exception handling aligned with IDS v3.2 unified response spec.

Every error response uses the shape::

    {"code": <error_code>, "message": <error_message>, "data": null}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


def _add_cors_headers(response: JSONResponse, request: Request) -> None:
    """Add CORS headers to response for cross-origin requests."""
    origin = request.headers.get("origin")
    if origin and origin in settings.CORS_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, Accept, Idempotency-Key"
        )


class BizError(Exception):
    """Business-level error carrying a stable error code and HTTP status."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.data = data


def _error_body(code: str, message: str, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data}


def _sanitize_validation_error(err: dict[str, Any]) -> str:
    """将单个 Pydantic 校验错误转换为脱敏的通用提示。

    不暴露 loc（字段路径）、type（内部错误类型）、ctx（上下文）等内部细节，
    仅根据错误类别返回用户友好的通用提示。
    """
    err_type = str(err.get("type", ""))
    if "missing" in err_type:
        return "缺少必填字段"
    if err_type.startswith("value_error"):
        return "字段格式不正确"
    if err_type.startswith("type_error"):
        return "字段类型不正确"
    if "enum" in err_type or "literal_error" in err_type:
        return "字段值不在允许范围内"
    if "max_length" in err_type or "min_length" in err_type:
        return "字段长度不符合要求"
    if "pattern" in err_type:
        return "字段格式不正确"
    return "字段格式不正确"


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[str]:
    """对校验错误列表进行脱敏，返回通用提示列表（S4-C1）。"""
    return [_sanitize_validation_error(e) for e in errors]


def register_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on the FastAPI app."""

    @app.exception_handler(BizError)
    async def _handle_biz_error(request: Request, exc: BizError) -> JSONResponse:
        response = JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder(_error_body(exc.code, exc.message, exc.data)),
        )
        _add_cors_headers(response, request)
        return response

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if settings.DEBUG:
            response = JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=jsonable_encoder(
                    _error_body("ERR_VALIDATION", "请求参数校验失败", exc.errors())
                ),
            )
        else:
            sanitized = _sanitize_validation_errors(exc.errors())
            response = JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=jsonable_encoder(_error_body("ERR_VALIDATION", "输入校验失败", sanitized)),
            )
        _add_cors_headers(response, request)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = f"ERR_HTTP_{exc.status_code}"
        message = str(exc.detail) if exc.detail else "请求错误"
        response = JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder(_error_body(code, message, None)),
        )
        _add_cors_headers(response, request)
        return response

    @app.exception_handler(Exception)
    async def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        response = JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=jsonable_encoder(_error_body("ERR_INTERNAL", "服务内部错误", None)),
        )
        _add_cors_headers(response, request)
        return response
