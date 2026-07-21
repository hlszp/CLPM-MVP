"""datasource_config service — 链路配置安全与一致性测试.

覆盖（回路管理整改 阶段 8）：
- historyApiToken 打码（GET 响应 / 审计日志，保留前后各 4 位）
- 更新语义：不传=不变、空串=清空、打码值回传=忽略
- _cast_value 脏数据容错（回退原字符串 + 记日志）
- signalrSubscriberRunning 接订阅器真实运行状态
- Tailscale 切换失败回滚 sys_config / settings networkMode
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.datasource_config import (
    _TOKEN_MASK,
    _cast_value,
    _mask_token,
    get_datasource_config,
    update_datasource_config,
)


def _mock_get_config_rows(stored: dict[str, str]):
    """构造 _get_config_rows 的 AsyncMock，按 IN 查询语义返回 stored 中已存在的行."""

    async def _impl(_db, keys):
        return {key: SimpleNamespace(key=key, value=stored[key]) for key in keys if key in stored}

    return AsyncMock(side_effect=_impl)


def _set_mock_settings(mock_settings) -> None:
    """设置 settings mock 的默认值（与网络模式测试保持同一基线）."""
    mock_settings.DATA_SOURCE_TYPE = "remote_api"
    mock_settings.NETWORK_MODE = "lan"
    mock_settings.HISTORY_DATA_API_URL = ""
    mock_settings.HISTORY_DATA_API_TOKEN = ""
    mock_settings.HISTORY_DATA_API_TIMEOUT = 30.0
    mock_settings.SIGNALR_HUB_URL = ""
    mock_settings.SIGNALR_ENABLED = False
    mock_settings.SIGNALR_RECONNECT_INTERVAL = 5
    mock_settings.REALTIME_WRITEBACK_ENABLED = False


class TestMaskToken:
    """_mask_token 打码规则."""

    def test_none_and_empty_passthrough(self) -> None:
        """None / 空串原样返回（未配置无需打码）."""
        assert _mask_token(None) is None
        assert _mask_token("") == ""

    def test_short_token_fully_masked(self) -> None:
        """长度 ≤ 8 的 Token 全打码（保留前后 4 位会泄露全部内容）."""
        assert _mask_token("short") == _TOKEN_MASK
        assert _mask_token("12345678") == _TOKEN_MASK

    def test_long_token_keeps_front_back_4(self) -> None:
        """长 Token 保留前后各 4 位."""
        assert _mask_token("abcdef1234567890") == "abcd****7890"


class TestGetConfigTokenMasking:
    """GET 配置时 Token 打码."""

    @pytest.mark.asyncio
    async def test_get_masks_sys_config_token(self) -> None:
        """sys_config 中的原始 Token 在 GET 响应中打码."""
        db = AsyncMock()
        stored = {"datasource.history_api_token": "abcdef1234567890"}
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows(stored),
            ),
        ):
            _set_mock_settings(mock_settings)
            data = await get_datasource_config(db)
            assert data["historyApiToken"] == "abcd****7890"

    @pytest.mark.asyncio
    async def test_get_masks_settings_fallback_token(self) -> None:
        """sys_config 缺失时 settings 兜底 Token 同样打码."""
        db = AsyncMock()
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows({}),
            ),
        ):
            _set_mock_settings(mock_settings)
            mock_settings.HISTORY_DATA_API_TOKEN = "abcdef1234567890"
            data = await get_datasource_config(db)
            assert data["historyApiToken"] == "abcd****7890"

    @pytest.mark.asyncio
    async def test_get_raw_token_when_mask_disabled(self) -> None:
        """mask_token=False 时返回原始 Token（内部调用：连通性测试 / 启动预载）."""
        db = AsyncMock()
        stored = {"datasource.history_api_token": "abcdef1234567890"}
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows(stored),
            ),
        ):
            _set_mock_settings(mock_settings)
            data = await get_datasource_config(db, mask_token=False)
            assert data["historyApiToken"] == "abcdef1234567890"


class TestSignalrSubscriberRunning:
    """signalrSubscriberRunning 接订阅器真实运行状态（非 settings 镜像）."""

    @pytest.mark.asyncio
    async def test_running_state_from_real_subscriber(self) -> None:
        """订阅器实例 _running=True 时返回 True，即使 settings.SIGNALR_ENABLED=False."""
        db = AsyncMock()
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows({}),
            ),
            patch(
                "app.services.data_source.realtime_subscriber.get_subscriber",
                return_value=SimpleNamespace(_running=True),
            ),
        ):
            _set_mock_settings(mock_settings)
            data = await get_datasource_config(db)
            assert data["signalrSubscriberRunning"] is True

    @pytest.mark.asyncio
    async def test_not_running_state_from_real_subscriber(self) -> None:
        """订阅器实例 _running=False 时返回 False，即使 settings.SIGNALR_ENABLED=True.

        这是保存 signalrEnabled 后未重启的场景：配置已开但订阅器未启动，
        "需重启"提示必须保持显示。
        """
        db = AsyncMock()
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows({}),
            ),
            patch(
                "app.services.data_source.realtime_subscriber.get_subscriber",
                return_value=SimpleNamespace(_running=False),
            ),
        ):
            _set_mock_settings(mock_settings)
            mock_settings.SIGNALR_ENABLED = True
            data = await get_datasource_config(db)
            assert data["signalrSubscriberRunning"] is False


class TestCastValueFallback:
    """_cast_value 脏数据容错：转换失败回退原字符串并记日志."""

    def test_dirty_float_falls_back_to_raw(self, caplog) -> None:
        """historyApiTimeout 写入非数值脏数据时回退原字符串，不再抛异常."""
        with caplog.at_level(logging.WARNING, logger="app.services.datasource_config"):
            assert _cast_value("historyApiTimeout", "not-a-float") == "not-a-float"
        assert "脏数据" in caplog.text

    def test_dirty_int_falls_back_to_raw(self, caplog) -> None:
        """signalrReconnectInterval 脏数据回退原字符串."""
        with caplog.at_level(logging.WARNING, logger="app.services.datasource_config"):
            assert _cast_value("signalrReconnectInterval", "abc") == "abc"
        assert "脏数据" in caplog.text

    def test_valid_values_still_cast(self) -> None:
        """合法值仍正常转换."""
        assert _cast_value("historyApiTimeout", "45.5") == 45.5
        assert _cast_value("signalrReconnectInterval", "10") == 10
        assert _cast_value("signalrEnabled", "true") is True
        assert _cast_value("signalrEnabled", "false") is False


class TestUpdateTokenSemantics:
    """historyApiToken 更新语义：不传=不变、空串=清空、打码值=忽略."""

    @pytest.mark.asyncio
    async def test_token_not_provided_unchanged(self) -> None:
        """不传 historyApiToken 时不写入该配置项（不填即不变）."""
        db = AsyncMock()
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows({}),
            ),
            patch("app.services.datasource_config._set_config_values", new=AsyncMock()) as mock_set,
            patch("app.services.datasource_config._write_audit", new=AsyncMock()),
        ):
            _set_mock_settings(mock_settings)
            await update_datasource_config(db, "admin", historyApiUrl="http://new.example.com")
            items = mock_set.call_args.args[1]
            assert "datasource.history_api_token" not in items
            assert items["datasource.history_api_url"][0] == "http://new.example.com"

    @pytest.mark.asyncio
    async def test_token_empty_string_clears(self) -> None:
        """historyApiToken 传空串 = 显式清空已存 Token."""
        db = AsyncMock()
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows({}),
            ),
            patch("app.services.datasource_config._set_config_values", new=AsyncMock()) as mock_set,
            patch("app.services.datasource_config._write_audit", new=AsyncMock()),
        ):
            _set_mock_settings(mock_settings)
            await update_datasource_config(db, "admin", historyApiToken="")
            items = mock_set.call_args.args[1]
            assert items["datasource.history_api_token"][0] == ""
            # settings 同步清空
            assert mock_settings.HISTORY_DATA_API_TOKEN == ""

    @pytest.mark.asyncio
    async def test_token_new_value_overwrites(self) -> None:
        """传入新 Token 正常覆盖."""
        db = AsyncMock()
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows({}),
            ),
            patch("app.services.datasource_config._set_config_values", new=AsyncMock()) as mock_set,
            patch("app.services.datasource_config._write_audit", new=AsyncMock()),
        ):
            _set_mock_settings(mock_settings)
            await update_datasource_config(db, "admin", historyApiToken="brand-new-token-123")
            items = mock_set.call_args.args[1]
            assert items["datasource.history_api_token"][0] == "brand-new-token-123"
            assert mock_settings.HISTORY_DATA_API_TOKEN == "brand-new-token-123"

    @pytest.mark.asyncio
    async def test_masked_token_ignored(self) -> None:
        """回传打码 Token（含 ****）按误回传处理忽略，防止打码值覆盖真实 Token."""
        db = AsyncMock()
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows({}),
            ),
            patch("app.services.datasource_config._set_config_values", new=AsyncMock()) as mock_set,
            patch("app.services.datasource_config._write_audit", new=AsyncMock()),
        ):
            _set_mock_settings(mock_settings)
            await update_datasource_config(db, "admin", historyApiToken="abcd****7890")
            items = mock_set.call_args.args[1]
            assert "datasource.history_api_token" not in items

    @pytest.mark.asyncio
    async def test_url_empty_string_clears(self) -> None:
        """historyApiUrl / signalrHubUrl 传空串 = 显式清空."""
        db = AsyncMock()
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows({}),
            ),
            patch("app.services.datasource_config._set_config_values", new=AsyncMock()) as mock_set,
            patch("app.services.datasource_config._write_audit", new=AsyncMock()),
        ):
            _set_mock_settings(mock_settings)
            await update_datasource_config(db, "admin", historyApiUrl="", signalrHubUrl="")
            items = mock_set.call_args.args[1]
            assert items["datasource.history_api_url"][0] == ""
            assert items["datasource.signalr_hub_url"][0] == ""


class TestAuditTokenMasked:
    """审计日志中的 Token 打码."""

    @pytest.mark.asyncio
    async def test_audit_json_contains_masked_token_only(self) -> None:
        """DATASOURCE_CONFIG_UPDATE 审计 before/after JSON 不含原始 Token."""
        db = AsyncMock()
        stored = {"datasource.history_api_token": "abcdef1234567890"}
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows(stored),
            ),
            patch("app.services.datasource_config._set_config_values", new=AsyncMock()),
            patch("app.services.datasource_config._write_audit", new=AsyncMock()) as mock_audit,
        ):
            _set_mock_settings(mock_settings)
            await update_datasource_config(db, "admin", historyApiUrl="http://new.example.com")
            update_calls = [
                c
                for c in mock_audit.call_args_list
                if c.kwargs["operation_type"] == "DATASOURCE_CONFIG_UPDATE"
            ]
            assert len(update_calls) == 1
            for payload in (
                update_calls[0].kwargs["before_value"],
                update_calls[0].kwargs["after_value"],
            ):
                assert "abcdef1234567890" not in payload
                assert "abcd****7890" in payload
                # JSON 可解析，确认是配置快照
                assert json.loads(payload)["historyApiToken"] == "abcd****7890"


class TestTailscaleRollback:
    """Tailscale 切换失败回滚 sys_config / settings networkMode."""

    @pytest.mark.asyncio
    async def test_failed_switch_rolls_back_network_mode(self) -> None:
        """切换失败时 networkMode 回滚为原值，DB 与实际链路保持一致."""
        db = AsyncMock()
        # stored 不含 network_mode → before 回退 settings.NETWORK_MODE=lan；
        # setattr 后 after=wan，触发切换
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=True),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows({}),
            ),
            patch("app.services.datasource_config.switch_network_mode") as mock_switch,
            patch("app.services.datasource_config._set_config_values", new=AsyncMock()) as mock_set,
            patch("app.services.datasource_config._write_audit", new=AsyncMock()),
        ):
            _set_mock_settings(mock_settings)
            mock_switch.return_value = {
                "status": "failed",
                "message": "tailscale 命令超时（5s）",
                "latencyMs": 5000,
            }

            data = await update_datasource_config(db, "admin", networkMode="wan")

            # 第一次写入 wan，第二次回滚 lan
            assert mock_set.call_count == 2
            first_items = mock_set.call_args_list[0].args[1]
            rollback_items = mock_set.call_args_list[1].args[1]
            assert first_items["datasource.network_mode"][0] == "wan"
            assert rollback_items["datasource.network_mode"][0] == "lan"
            # settings 已回滚
            assert mock_settings.NETWORK_MODE == "lan"
            # 响应反映回滚后的状态
            assert data["networkMode"] == "lan"
            assert data["tailscaleSwitch"]["status"] == "failed"
            assert data["tailscaleSwitch"]["rolledBack"] is True
            assert "回滚" in data["tailscaleSwitch"]["message"]

    @pytest.mark.asyncio
    async def test_success_switch_no_rollback(self) -> None:
        """切换成功时不回滚."""
        db = AsyncMock()
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=True),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows({}),
            ),
            patch("app.services.datasource_config.switch_network_mode") as mock_switch,
            patch("app.services.datasource_config._set_config_values", new=AsyncMock()) as mock_set,
            patch("app.services.datasource_config._write_audit", new=AsyncMock()),
        ):
            _set_mock_settings(mock_settings)
            mock_switch.return_value = {
                "status": "success",
                "message": "Tailscale 已切换到公网",
                "latencyMs": 100,
            }

            data = await update_datasource_config(db, "admin", networkMode="wan")

            assert mock_set.call_count == 1
            assert data["tailscaleSwitch"]["status"] == "success"
            assert "rolledBack" not in data["tailscaleSwitch"]
