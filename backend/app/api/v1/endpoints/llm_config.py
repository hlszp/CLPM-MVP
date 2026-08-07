"""LLM 配置管理接口（P3-04 自然语言诊断解读配套）.

提供 LLM 服务配置的查询、更新与连接测试，让用户在系统管理中自助配置
BaseURL / API Key / 模型 / 超时，而非代码写死。

配置存储在 ``sys_config`` 表（6 个 key）：
- ``llm.enabled``     — 是否启用（"true"/"false"）
- ``llm.endpoint``    — BaseURL（API 根地址，不含 /v1，如 https://api.openai.com）
- ``llm.api_key``     — API Key（明文存储，**GET 返回时脱敏**）
- ``llm.model``       — 模型名（如 gpt-4o / deepseek-chat / qwen-plus）
- ``llm.timeout``     — 超时秒数（如 "30"）
- ``llm.max_tokens``  — 最大输出 token 数（如 "4096"，默认 4096）

遵循 OpenAI 兼容接口协议，任何兼容服务均可接入。

路由清单：
- GET  /api/v1/configs/llm      — 获取当前 LLM 配置（API Key 脱敏）
- POST /api/v1/configs/llm      — 更新 LLM 配置（仅 ADMIN，apiKey 空=保留原值）
- POST /api/v1/configs/llm/test — 连接测试（发一条 ping 请求）
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.sys_config import SysConfig
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.config import (
    LlmConfigSaveRequest,
    LlmConfigSchema,
    LlmTestResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/llm", tags=["llm-config"])

# ---------------------------------------------------------------------------
# sys_config 键常量（与 app.services.llm_provider.LLM_CONFIG_KEYS 对齐）
# ---------------------------------------------------------------------------

_KEYS = {
    "enabled": "llm.enabled",
    "endpoint": "llm.endpoint",
    "api_key": "llm.api_key",
    "model": "llm.model",
    "timeout": "llm.timeout",
    "max_tokens": "llm.max_tokens",
}

_KEY_DESC = "LLM 配置（P3-04 自然语言诊断解读）"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _now_naive() -> datetime:
    """当前 UTC naive datetime（对齐 ORM 字段无时区）."""
    return datetime.now(UTC).replace(tzinfo=None)


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串."""
    return datetime.now(UTC).isoformat()


def _mask_api_key(key: str | None) -> str | None:
    """API Key 脱敏：保留前 3 位 + 尾 4 位，中间用 *** 代替。

    例：sk-abcdefghij1234 → sk-***1234
    短 key（<8 位）：全部 ***。
    """
    if not key:
        return None
    if len(key) < 8:
        return "***"
    return f"{key[:3]}***{key[-4:]}"


async def _get_config_value(db: AsyncSession, key: str) -> str | None:
    """读取 sys_config 表中某个 key 的值."""
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    return cfg.value if cfg else None


async def _set_config_value(
    db: AsyncSession,
    key: str,
    value: str,
    description: str | None,
    operator: str,
) -> None:
    """写入 sys_config 表（upsert，不提交）."""
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    now = _now_naive()
    if cfg is None:
        cfg = SysConfig(
            key=key,
            value=value,
            description=description,
            updated_by=operator,
            updated_at=now,
        )
        db.add(cfg)
    else:
        cfg.value = value
        cfg.description = description or cfg.description
        cfg.updated_by = operator
        cfg.updated_at = now


async def _write_audit(
    db: AsyncSession,
    operator: str,
    before_value: str,
    after_value: str,
) -> None:
    """写入审计日志."""
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type="LLM_CONFIG_UPDATE",
        target_type="sys_config",
        target_id="llm.*",
        before_value=before_value,
        after_value=after_value,
        operated_at=_now_naive(),
    )
    db.add(log)


async def _load_raw_config(db: AsyncSession) -> dict[str, str | None]:
    """加载 6 个 key 的原始值（明文）。"""
    return {
        "enabled": await _get_config_value(db, _KEYS["enabled"]),
        "endpoint": await _get_config_value(db, _KEYS["endpoint"]),
        "api_key": await _get_config_value(db, _KEYS["api_key"]),
        "model": await _get_config_value(db, _KEYS["model"]),
        "timeout": await _get_config_value(db, _KEYS["timeout"]),
        "max_tokens": await _get_config_value(db, _KEYS["max_tokens"]),
    }


def _build_schema(raw: dict[str, str | None], updated_by: str | None) -> LlmConfigSchema:
    """从原始值构建响应 Schema（API Key 脱敏）。"""
    api_key_raw = raw.get("api_key")
    return LlmConfigSchema(
        enabled=(raw.get("enabled") or "").lower() == "true",
        endpoint=raw.get("endpoint") or None,
        apiKey=_mask_api_key(api_key_raw),
        apiKeyConfigured=bool(api_key_raw),
        model=raw.get("model") or None,
        timeout=int(raw["timeout"]) if raw.get("timeout") else 30,
        maxTokens=int(raw["max_tokens"]) if raw.get("max_tokens") else 4096,
        updatedAt=_now_iso(),
        updatedBy=updated_by,
    )


# ---------------------------------------------------------------------------
# GET /configs/llm — 获取当前 LLM 配置（API Key 脱敏）
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[LlmConfigSchema])
async def get_llm_config(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """获取当前 LLM 配置（API Key 脱敏返回）。

    权限：ADMIN/IC_ENGINEER/PE_ENGINEER 可查看（与可信度阈值配置一致）。
    返回的 apiKey 为脱敏值（sk-***xxxx），apiKeyConfigured 标识是否已配置。
    """
    raw = await _load_raw_config(db)
    # 取 updated_by（5 个 key 的 updated_by 应一致，取 api_key 的）
    api_key_cfg = await db.execute(select(SysConfig).where(SysConfig.key == _KEYS["api_key"]))
    cfg = api_key_cfg.scalar_one_or_none()
    data = _build_schema(raw, cfg.updated_by if cfg else None)
    return success(data=data.model_dump())


# ---------------------------------------------------------------------------
# POST /configs/llm — 更新 LLM 配置（仅 ADMIN）
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse[LlmConfigSchema])
async def save_llm_config(
    body: LlmConfigSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新 LLM 配置（仅 ADMIN）。

    - apiKey 为 None/空字符串时**保留原值**（前端未改 key 场景）
    - apiKey 非空时更新为新值
    - endpoint/model 允许为空（enabled=true 时前端应校验非空，后端 is_llm_available 兜底）

    校验：enabled=true 时 endpoint/model/apiKey 至少有一份已配置记录，
    否则 is_llm_available 会返回 False（auto 模式自动 fallback 模板，不阻断）。
    """
    raw_before = await _load_raw_config(db)
    before_snapshot = str(
        {
            "enabled": raw_before["enabled"],
            "endpoint": raw_before["endpoint"],
            "apiKeyConfigured": bool(raw_before["api_key"]),
            "model": raw_before["model"],
            "timeout": raw_before["timeout"],
            "max_tokens": raw_before["max_tokens"],
        }
    )

    # enabled
    await _set_config_value(
        db,
        _KEYS["enabled"],
        "true" if body.enabled else "false",
        _KEY_DESC,
        user.username,
    )
    # endpoint
    await _set_config_value(
        db,
        _KEYS["endpoint"],
        body.endpoint or "",
        _KEY_DESC,
        user.username,
    )
    # api_key：空=保留原值，非空=更新
    if body.apiKey:
        await _set_config_value(
            db,
            _KEYS["api_key"],
            body.apiKey,
            _KEY_DESC,
            user.username,
        )
    # model
    await _set_config_value(
        db,
        _KEYS["model"],
        body.model or "",
        _KEY_DESC,
        user.username,
    )
    # timeout
    await _set_config_value(
        db,
        _KEYS["timeout"],
        str(body.timeout),
        _KEY_DESC,
        user.username,
    )
    # max_tokens
    await _set_config_value(
        db,
        _KEYS["max_tokens"],
        str(body.maxTokens),
        _KEY_DESC,
        user.username,
    )

    await _write_audit(db, user.username, before_snapshot, str(body.model_dump()))

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("LLM 配置更新事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    logger.info(
        "LLM 配置已更新: enabled=%s, model=%s, operator=%s", body.enabled, body.model, user.username
    )

    # 返回更新后的脱敏配置
    raw_after = await _load_raw_config(db)
    data = _build_schema(raw_after, user.username)
    return success(data=data.model_dump(), message="LLM 配置已保存")


# ---------------------------------------------------------------------------
# POST /configs/llm/test — 连接测试
# ---------------------------------------------------------------------------


@router.post("/test", response_model=ApiResponse[LlmTestResult])
async def test_llm_connection(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """连接测试：向已配置的 LLM 服务发一条 ping 请求。

    用当前 sys_config 中的配置发起 ``{endpoint}/v1/chat/completions`` 请求，
    发送一条最小消息（"ping"），返回成功/失败 + 延迟。

    仅 ADMIN 可用（避免 IC_ENGINEER 频繁测试消耗 token）。
    """
    from app.services.llm_provider import _load_llm_config, build_chat_url

    try:
        config = await _load_llm_config(db)
    except BizError as exc:
        return success(
            data=LlmTestResult(
                success=False,
                latencyMs=None,
                model=None,
                message=f"配置不完整：{exc.message}（请先在配置页填写并启用）",
            ).model_dump()
        )

    url = build_chat_url(str(config["endpoint"]))
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['apiKey']}",
    }
    body = {
        "model": config["model"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "temperature": 0,
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=config["timeout"]) as client:
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()
    except httpx.TimeoutException:
        latency = int((time.monotonic() - start) * 1000)
        logger.warning("LLM 连接测试超时: %sms", latency)
        return success(
            data=LlmTestResult(
                success=False,
                latencyMs=latency,
                model=config["model"],
                message=f"请求超时（{latency}ms，timeout={config['timeout']}s）",
            ).model_dump()
        )
    except httpx.HTTPStatusError as exc:
        latency = int((time.monotonic() - start) * 1000)
        status_code = exc.response.status_code
        body_text = exc.response.text[:200]
        logger.warning("LLM 连接测试 HTTP 错误: status=%s", status_code)
        return success(
            data=LlmTestResult(
                success=False,
                latencyMs=latency,
                model=config["model"],
                message=f"HTTP {status_code}：{body_text}",
            ).model_dump()
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.monotonic() - start) * 1000)
        logger.warning("LLM 连接测试失败: %s", exc)
        return success(
            data=LlmTestResult(
                success=False,
                latencyMs=latency,
                model=config["model"],
                message=f"连接失败：{exc}",
            ).model_dump()
        )

    latency = int((time.monotonic() - start) * 1000)
    logger.info("LLM 连接测试成功: model=%s, latency=%sms", config["model"], latency)
    return success(
        data=LlmTestResult(
            success=True,
            latencyMs=latency,
            model=config["model"],
            message=f"连接成功（{latency}ms）",
        ).model_dump()
    )


__all__ = ["router"]
