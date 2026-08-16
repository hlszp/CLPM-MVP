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

    例外：``model_validator`` 抛出的 ``value_error`` 包含面向用户的业务提示
    （如"tsEnd 不得晚于当前时间前 5 分钟"），直接透传 msg 内容——这些消息由
    开发者编写，不含敏感技术细节，脱敏反而损害用户体验。
    """
    err_type = str(err.get("type", ""))
    if "missing" in err_type:
        return "缺少必填字段"
    if err_type.startswith("value_error"):
        # model_validator 业务校验：透传面向用户的具体提示
        msg = str(err.get("msg", ""))
        # Pydantic v2 格式："Value error, <原始消息>"
        if msg.startswith("Value error, "):
            return msg[len("Value error, ") :]
        return msg or "字段格式不正确"
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


def _brief_validation_errors(
    errors: list[dict[str, Any]], *, max_input_len: int = 100
) -> list[dict[str, Any]]:
    """压缩校验错误用于日志：截断 input 原始值，避免大 body 刷屏。

    保留 loc/type/msg/ctx（约束详情如 {"le": 100}），这些是排查参数
    校验问题的关键线索；响应体在非 DEBUG 下已脱敏，服务端日志是唯一
    的详细现场（如 GET /loops?pageSize=200 → loc=query.pageSize、
    type=less_than_equal、ctx.le=100）。
    """
    brief: list[dict[str, Any]] = []
    for err in errors:
        item: dict[str, Any] = {
            "type": err.get("type"),
            "loc": err.get("loc"),
            "msg": err.get("msg"),
        }
        ctx = err.get("ctx")
        if ctx is not None:
            item["ctx"] = jsonable_encoder(ctx)
        input_val = err.get("input")
        if isinstance(input_val, str) and len(input_val) > max_input_len:
            item["input"] = f"{input_val[:max_input_len]}...(len={len(input_val)})"
        else:
            item["input"] = input_val
        brief.append(item)
    return brief


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
        # 参数校验失败留痕：记录 method/path/query 与完整校验详情
        # （loc/type/msg/ctx），非 DEBUG 响应体已脱敏，此日志是唯一现场
        logger.warning(
            "Validation failed: %s %s query=%s errors=%s",
            request.method,
            request.url.path,
            dict(request.query_params),
            _brief_validation_errors(exc.errors()),
        )
        if settings.DEBUG:
            response = JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=jsonable_encoder(
                    _error_body("ERR_VALIDATION", "请求参数校验失败", exc.errors())
                ),
            )
        else:
            sanitized = _sanitize_validation_errors(exc.errors())
            # 当存在面向用户的具体提示时（如 model_validator 业务校验），
            # 用第一条作为 message，让前端全局拦截器直接展示具体原因
            # 而非笼统的"输入校验失败"
            msg = sanitized[0] if sanitized and sanitized[0] != "字段格式不正确" else "输入校验失败"
            response = JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=jsonable_encoder(_error_body("ERR_VALIDATION", msg, sanitized)),
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
