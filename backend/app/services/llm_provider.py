"""LLM API 适配层（P3-04）。

从 sys_config 读取配置，调用 OpenAI 兼容接口生成文本。
超时/错误时由调用方（diagnosis_interpretation）fallback 到规则模板。

配置键（sys_config）：
  - llm.enabled: 是否启用（"true"/"false"）
  - llm.endpoint: API endpoint（如 https://api.openai.com）
  - llm.api_key: API key
  - llm.model: 模型名（如 gpt-4o）
  - llm.timeout: 超时秒数（如 "30"）
  - llm.max_tokens: 最大输出 token 数（如 "4096"，默认 4096）
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.sys_config import SysConfig

logger = logging.getLogger(__name__)

# sys_config 键名
LLM_CONFIG_KEYS = {
    "enabled": "llm.enabled",
    "endpoint": "llm.endpoint",
    "apiKey": "llm.api_key",
    "model": "llm.model",
    "timeout": "llm.timeout",
    "maxTokens": "llm.max_tokens",
}


async def _get_config_value(db: AsyncSession, key: str) -> str | None:
    """读取 sys_config 表中某个 key 的值。"""
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    return cfg.value if cfg else None


async def is_llm_available(db: AsyncSession) -> bool:
    """检查 LLM 是否已启用且配置完整。

    Returns:
        True 如果 enabled=true 且 endpoint/api_key/model 均非空
    """
    enabled = await _get_config_value(db, LLM_CONFIG_KEYS["enabled"])
    if not enabled or enabled.lower() != "true":
        return False

    endpoint = await _get_config_value(db, LLM_CONFIG_KEYS["endpoint"])
    api_key = await _get_config_value(db, LLM_CONFIG_KEYS["apiKey"])
    model = await _get_config_value(db, LLM_CONFIG_KEYS["model"])

    return bool(endpoint and api_key and model)


async def _load_llm_config(db: AsyncSession) -> dict[str, str | float | int]:
    """加载 LLM 配置，缺失项抛错。"""
    enabled = await _get_config_value(db, LLM_CONFIG_KEYS["enabled"])
    endpoint = await _get_config_value(db, LLM_CONFIG_KEYS["endpoint"])
    api_key = await _get_config_value(db, LLM_CONFIG_KEYS["apiKey"])
    model = await _get_config_value(db, LLM_CONFIG_KEYS["model"])
    timeout_str = await _get_config_value(db, LLM_CONFIG_KEYS["timeout"])
    max_tokens_str = await _get_config_value(db, LLM_CONFIG_KEYS["maxTokens"])

    if not enabled or enabled.lower() != "true":
        raise BizError(
            code="ERR_LLM_UNAVAILABLE",
            message="LLM 未启用",
            status_code=503,
        )
    if not (endpoint and api_key and model):
        raise BizError(
            code="ERR_LLM_UNAVAILABLE",
            message="LLM 配置不完整（endpoint/api_key/model 缺失）",
            status_code=503,
        )

    timeout = float(timeout_str) if timeout_str else 30.0
    # max_tokens 默认 4096（推理模型需要更大输出空间，旧值 800 不够用）
    try:
        max_tokens = int(max_tokens_str) if max_tokens_str else 4096
    except (ValueError, TypeError):
        max_tokens = 4096
    max_tokens = max(256, min(max_tokens, 32768))

    return {
        "endpoint": endpoint,
        "apiKey": api_key,
        "model": model,
        "timeout": timeout,
        "maxTokens": max_tokens,
    }


async def call_llm(
    db: AsyncSession,
    system_prompt: str,
    user_prompt: str,
) -> tuple[str, str]:
    """调用 LLM API（OpenAI 兼容接口）。

    Args:
        db: 数据库会话（读取 sys_config）
        system_prompt: 系统提示词
        user_prompt: 用户提示词

    Returns:
        (生成的文本, 模型名)

    Raises:
        BizError: ERR_LLM_UNAVAILABLE — 调用失败
    """
    config = await _load_llm_config(db)
    endpoint = str(config["endpoint"])
    api_key = str(config["apiKey"])
    model = str(config["model"])
    timeout = float(config["timeout"])
    max_tokens = int(config["maxTokens"])

    # OpenAI 兼容接口
    url = f"{endpoint.rstrip('/')}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise BizError(
                code="ERR_LLM_UNAVAILABLE",
                message="LLM 返回空响应",
                status_code=502,
            )

        message = choices[0].get("message", {})
        text = message.get("content") or ""
        # 兼容 reasoning 模型（deepseek-r1/qwen-qwq 等）：
        # content 为空时 fallback 到 reasoning_content
        if not text.strip():
            reasoning = message.get("reasoning_content") or ""
            if reasoning.strip():
                logger.info(
                    "LLM content 为空，使用 reasoning_content fallback（length=%s）",
                    len(reasoning),
                )
                text = reasoning
            else:
                raise BizError(
                    code="ERR_LLM_UNAVAILABLE",
                    message="LLM 返回空内容（content 与 reasoning_content 均为空）",
                    status_code=502,
                )

        return text.strip(), model

    except httpx.TimeoutException:
        logger.warning("LLM 调用超时（timeout=%ss）", timeout)
        raise BizError(
            code="ERR_LLM_UNAVAILABLE",
            message=f"LLM 调用超时（{timeout}s）",
            status_code=504,
        ) from None
    except httpx.HTTPStatusError as e:
        logger.warning(
            "LLM HTTP 错误：status=%s, body=%s", e.response.status_code, e.response.text[:200]
        )
        raise BizError(
            code="ERR_LLM_UNAVAILABLE",
            message=f"LLM API 返回错误（HTTP {e.response.status_code}）",
            status_code=502,
        ) from None
    except httpx.HTTPError as e:
        logger.warning("LLM 连接失败：%s", e)
        raise BizError(
            code="ERR_LLM_UNAVAILABLE",
            message=f"LLM 连接失败：{e}",
            status_code=502,
        ) from None
