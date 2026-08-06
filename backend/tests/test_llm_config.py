"""LLM 配置管理接口测试（P3-04 配套）.

测试覆盖：
- _mask_api_key：脱敏逻辑（长 key/短 key/空）
- GET /configs/llm：返回脱敏配置 + apiKeyConfigured 标识
- POST /configs/llm：保存配置（apiKey 空=保留原值，非空=更新）+ 审计日志
- POST /configs/llm/test：连接测试（成功/超时/HTTP错误/配置不完整）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.v1.endpoints import llm_config
from app.core.exceptions import BizError
from app.schemas.config import LlmConfigSaveRequest

# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_sys_config(key: str, value: str, updated_by: str = "admin") -> MagicMock:
    """构造 SysConfig mock。"""
    cfg = MagicMock()
    cfg.key = key
    cfg.value = value
    cfg.updated_by = updated_by
    return cfg


def _make_db_with_configs(configs: dict[str, str | None]) -> AsyncMock:
    """构造 db mock，按 key 返回对应 SysConfig。

    configs: {key: value}，value=None 表示该 key 不存在。
    """
    db = AsyncMock()

    def _execute(stmt):
        # 提取 where 条件中的 key（简化：按调用顺序无法稳定，改用 stmt 解析）
        # 这里用魔法：stmt 是 Select 对象，编译后含 key 值
        stmt_str = str(stmt)
        r = MagicMock()
        for key, value in configs.items():
            if f"'{key}'" in stmt_str:
                if value is None:
                    r.scalar_one_or_none.return_value = None
                else:
                    r.scalar_one_or_none.return_value = _make_sys_config(key, value)
                return r
        # 默认返回 None
        r.scalar_one_or_none.return_value = None
        return r

    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# ===========================================================================
# _mask_api_key 脱敏逻辑
# ===========================================================================


class TestMaskApiKey:
    """测试 API Key 脱敏。"""

    def test_long_key_masks_middle(self) -> None:
        """长 key 保留前 3 + 尾 4，中间 ***。"""
        assert llm_config._mask_api_key("sk-abcdefghij1234") == "sk-***1234"

    def test_short_key_all_masked(self) -> None:
        """短 key（<8 位）全部 ***。"""
        assert llm_config._mask_api_key("sk-ab") == "***"

    def test_none_returns_none(self) -> None:
        """None 返回 None。"""
        assert llm_config._mask_api_key(None) is None

    def test_empty_returns_none(self) -> None:
        """空字符串返回 None。"""
        assert llm_config._mask_api_key("") is None

    def test_boundary_8_chars(self) -> None:
        """恰好 8 位：保留前 3 + 尾 4。"""
        assert llm_config._mask_api_key("sk-abcd1234") == "sk-***1234"


# ===========================================================================
# GET /configs/llm
# ===========================================================================


class TestGetLlmConfig:
    """测试 GET /configs/llm。"""

    @pytest.mark.asyncio
    async def test_returns_masked_api_key_when_configured(self) -> None:
        """已配置 API Key 时返回脱敏值 + apiKeyConfigured=True。"""
        db = AsyncMock()
        raw = {
            "enabled": "true",
            "endpoint": "https://api.openai.com",
            "api_key": "sk-abcdefghij1234",
            "model": "gpt-4o",
            "timeout": "30",
        }
        # updated_by 查询返回
        res = MagicMock()
        cfg = MagicMock()
        cfg.updated_by = "admin"
        res.scalar_one_or_none.return_value = cfg
        db.execute = AsyncMock(return_value=res)

        with patch.object(llm_config, "_load_raw_config", new=AsyncMock(return_value=raw)):
            result = await llm_config.get_llm_config(db=db)
        data = result["data"]

        assert data["enabled"] is True
        assert data["endpoint"] == "https://api.openai.com"
        assert data["apiKey"] == "sk-***1234"  # 脱敏
        assert data["apiKeyConfigured"] is True
        assert data["model"] == "gpt-4o"
        assert data["timeout"] == 30

    @pytest.mark.asyncio
    async def test_returns_none_api_key_when_not_configured(self) -> None:
        """未配置 API Key 时 apiKey=None + apiKeyConfigured=False。"""
        db = AsyncMock()
        raw = {
            "enabled": "false",
            "endpoint": None,
            "api_key": None,
            "model": None,
            "timeout": None,
        }
        res = MagicMock()
        res.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=res)

        with patch.object(llm_config, "_load_raw_config", new=AsyncMock(return_value=raw)):
            result = await llm_config.get_llm_config(db=db)
        data = result["data"]

        assert data["enabled"] is False
        assert data["apiKey"] is None
        assert data["apiKeyConfigured"] is False
        assert data["timeout"] == 30  # 默认值


# ===========================================================================
# POST /configs/llm
# ===========================================================================


class TestSaveLlmConfig:
    """测试 POST /configs/llm。

    通过 patch _load_raw_config（before/after 两次读取）+ _set_config_value +
    _write_audit 隔离 db，专注测试编排逻辑。
    """

    @pytest.mark.asyncio
    async def test_save_new_api_key(self) -> None:
        """apiKey 非空时写入新值，响应 apiKeyConfigured=True。"""
        db = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()
        user = MagicMock()
        user.username = "admin"

        # before: 全空；after: 已配置（_set_config_value 写入后 _load_raw_config 读到新值）
        raw_before = {
            "enabled": "false",
            "endpoint": "",
            "api_key": "",
            "model": "",
            "timeout": "30",
        }
        raw_after = {
            "enabled": "true",
            "endpoint": "https://api.openai.com",
            "api_key": "sk-newkey123456",
            "model": "gpt-4o",
            "timeout": "45",
        }

        body = LlmConfigSaveRequest(
            enabled=True,
            endpoint="https://api.openai.com",
            apiKey="sk-newkey123456",
            model="gpt-4o",
            timeout=45,
        )
        with (
            patch.object(
                llm_config,
                "_load_raw_config",
                new=AsyncMock(side_effect=[raw_before, raw_after]),
            ),
            patch.object(llm_config, "_set_config_value", new=AsyncMock()),
            patch.object(llm_config, "_write_audit", new=AsyncMock()),
        ):
            result = await llm_config.save_llm_config(body=body, db=db, user=user)
        data = result["data"]

        assert data["enabled"] is True
        assert data["apiKeyConfigured"] is True
        assert data["apiKey"] == "sk-***3456"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_empty_api_key_preserves_original(self) -> None:
        """apiKey 为空时保留原值，响应仍显示已配置。"""
        db = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()
        user = MagicMock()
        user.username = "admin"

        # before/after 都保留原 api_key（因 apiKey="" 不更新）
        raw_before = {
            "enabled": "true",
            "endpoint": "https://api.openai.com",
            "api_key": "sk-originalsecret",
            "model": "gpt-4o",
            "timeout": "30",
        }
        raw_after = dict(raw_before, model="gpt-4o-mini")

        body = LlmConfigSaveRequest(
            enabled=True,
            endpoint="https://api.openai.com",
            apiKey="",  # 空=保留原值
            model="gpt-4o-mini",
            timeout=30,
        )
        set_calls: list[str] = []

        async def _track_set(db_, key, value, desc, op):
            set_calls.append(key)

        with (
            patch.object(
                llm_config,
                "_load_raw_config",
                new=AsyncMock(side_effect=[raw_before, raw_after]),
            ),
            patch.object(llm_config, "_set_config_value", new=_track_set),
            patch.object(llm_config, "_write_audit", new=AsyncMock()),
        ):
            result = await llm_config.save_llm_config(body=body, db=db, user=user)
        data = result["data"]

        assert data["apiKeyConfigured"] is True
        assert data["apiKey"] == "sk-***cret"  # 原值脱敏（前3+尾4）
        # api_key 不应被 _set_config_value 调用（保留原值）
        assert "llm.api_key" not in set_calls

    @pytest.mark.asyncio
    async def test_save_disabled_state(self) -> None:
        """enabled=False 时响应 enabled=False。"""
        db = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()
        user = MagicMock()
        user.username = "admin"

        raw_before = {
            "enabled": "true",
            "endpoint": "https://api.openai.com",
            "api_key": "sk-xxx",
            "model": "gpt-4o",
            "timeout": "30",
        }
        raw_after = dict(raw_before, enabled="false")

        body = LlmConfigSaveRequest(
            enabled=False,
            endpoint="https://api.openai.com",
            apiKey="",
            model="gpt-4o",
            timeout=30,
        )
        with (
            patch.object(
                llm_config,
                "_load_raw_config",
                new=AsyncMock(side_effect=[raw_before, raw_after]),
            ),
            patch.object(llm_config, "_set_config_value", new=AsyncMock()),
            patch.object(llm_config, "_write_audit", new=AsyncMock()),
        ):
            result = await llm_config.save_llm_config(body=body, db=db, user=user)
        data = result["data"]

        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_save_writes_audit_log(self) -> None:
        """保存时调用 _write_audit。"""
        db = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()
        user = MagicMock()
        user.username = "admin"

        raw = {
            "enabled": "false",
            "endpoint": "",
            "api_key": "",
            "model": "",
            "timeout": "30",
        }

        body = LlmConfigSaveRequest(
            enabled=True,
            endpoint="https://x.com",
            apiKey="sk-test123456",
            model="m",
            timeout=30,
        )
        with (
            patch.object(
                llm_config,
                "_load_raw_config",
                new=AsyncMock(side_effect=[raw, dict(raw, enabled="true")]),
            ),
            patch.object(llm_config, "_set_config_value", new=AsyncMock()),
            patch.object(llm_config, "_write_audit", new=AsyncMock()) as mock_audit,
        ):
            await llm_config.save_llm_config(body=body, db=db, user=user)

        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_rollback_on_commit_failure(self) -> None:
        """提交失败时回滚并抛 BizError。"""
        db = AsyncMock()
        db.commit = AsyncMock(side_effect=Exception("db down"))
        db.rollback = AsyncMock()
        db.add = MagicMock()
        user = MagicMock()
        user.username = "admin"

        raw = {
            "enabled": "false",
            "endpoint": "",
            "api_key": "",
            "model": "",
            "timeout": "30",
        }
        body = LlmConfigSaveRequest(
            enabled=True,
            endpoint="x",
            apiKey="sk-test123456",
            model="m",
            timeout=30,
        )
        with (
            patch.object(
                llm_config,
                "_load_raw_config",
                new=AsyncMock(return_value=raw),
            ),
            patch.object(llm_config, "_set_config_value", new=AsyncMock()),
            patch.object(llm_config, "_write_audit", new=AsyncMock()),
        ):
            with pytest.raises(BizError) as exc_info:
                await llm_config.save_llm_config(body=body, db=db, user=user)

        assert exc_info.value.code == "ERR_INTERNAL"
        db.rollback.assert_awaited_once()


# ===========================================================================
# POST /configs/llm/test
# ===========================================================================


class TestTestLlmConnection:
    """测试 POST /configs/llm/test 连接测试。"""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """连接成功返回 success=True + latency。"""
        db = AsyncMock()
        config = {
            "endpoint": "https://api.openai.com",
            "apiKey": "sk-xxx",
            "model": "gpt-4o",
            "timeout": 30.0,
        }
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with (
            patch(
                "app.services.llm_provider._load_llm_config",
                new=AsyncMock(return_value=config),
            ),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await llm_config.test_llm_connection(db=db)
            data = result["data"]

        assert data["success"] is True
        assert data["model"] == "gpt-4o"
        assert data["latencyMs"] is not None
        assert "成功" in data["message"]

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        """超时返回 success=False + 超时说明。"""
        db = AsyncMock()
        config = {
            "endpoint": "https://api.openai.com",
            "apiKey": "sk-xxx",
            "model": "gpt-4o",
            "timeout": 5.0,
        }

        with (
            patch(
                "app.services.llm_provider._load_llm_config",
                new=AsyncMock(return_value=config),
            ),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await llm_config.test_llm_connection(db=db)
            data = result["data"]

        assert data["success"] is False
        assert "超时" in data["message"]

    @pytest.mark.asyncio
    async def test_http_error(self) -> None:
        """HTTP 错误返回 success=False + 状态码。"""
        db = AsyncMock()
        config = {
            "endpoint": "https://api.openai.com",
            "apiKey": "sk-bad",
            "model": "gpt-4o",
            "timeout": 30.0,
        }
        err_response = MagicMock()
        err_response.status_code = 401
        err_response.text = "unauthorized"

        with (
            patch(
                "app.services.llm_provider._load_llm_config",
                new=AsyncMock(return_value=config),
            ),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=err_response)
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await llm_config.test_llm_connection(db=db)
            data = result["data"]

        assert data["success"] is False
        assert "401" in data["message"]

    @pytest.mark.asyncio
    async def test_incomplete_config(self) -> None:
        """配置不完整时返回 success=False + 配置不完整说明（不抛错）。"""
        db = AsyncMock()

        with patch(
            "app.services.llm_provider._load_llm_config",
            new=AsyncMock(side_effect=BizError(code="ERR_LLM_UNAVAILABLE", message="LLM 未启用")),
        ):
            result = await llm_config.test_llm_connection(db=db)
            data = result["data"]

        assert data["success"] is False
        assert "配置不完整" in data["message"]
        assert data["latencyMs"] is None
