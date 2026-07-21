"""datasource_config service — networkMode 字段集成测试.

验证 networkMode 字段在 sys_config / settings / Tailscale 切换链路上的端到端行为。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.datasource_config import (
    _SETTINGS_ATTR_MAP,
    DATASOURCE_CONFIG_KEYS,
    get_datasource_config,
    update_datasource_config,
)


def _mock_get_config_rows(stored: dict[str, str]):
    """构造 _get_config_rows 的 AsyncMock，按 IN 查询语义返回 stored 中已存在的行."""

    async def _impl(_db, keys):
        return {key: SimpleNamespace(key=key, value=stored[key]) for key in keys if key in stored}

    return AsyncMock(side_effect=_impl)


class TestNetworkModeConfigRegistration:
    """networkMode 字段注册到配置映射表."""

    def test_network_mode_in_config_keys(self) -> None:
        """networkMode 已注册到 DATASOURCE_CONFIG_KEYS."""
        assert "networkMode" in DATASOURCE_CONFIG_KEYS
        assert DATASOURCE_CONFIG_KEYS["networkMode"] == "datasource.network_mode"

    def test_network_mode_in_settings_attr_map(self) -> None:
        """networkMode 已映射到 settings.NETWORK_MODE."""
        assert _SETTINGS_ATTR_MAP["networkMode"] == "NETWORK_MODE"


class TestGetDatasourceConfig:
    """get_datasource_config 返回 networkMode + tailscaleAvailable."""

    @pytest.mark.asyncio
    async def test_returns_network_mode_default_lan(self) -> None:
        """sys_config 无 networkMode 时回退 settings 默认值 lan."""
        db = AsyncMock()
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=False),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows({}),
            ),
        ):
            mock_settings.DATA_SOURCE_TYPE = "remote_api"
            mock_settings.NETWORK_MODE = "lan"
            mock_settings.HISTORY_DATA_API_URL = ""
            mock_settings.HISTORY_DATA_API_TOKEN = ""
            mock_settings.HISTORY_DATA_API_TIMEOUT = 30.0
            mock_settings.SIGNALR_HUB_URL = ""
            mock_settings.SIGNALR_ENABLED = False
            mock_settings.SIGNALR_RECONNECT_INTERVAL = 5
            mock_settings.REALTIME_WRITEBACK_ENABLED = False

            data = await get_datasource_config(db)
            assert data["networkMode"] == "lan"
            assert data["tailscaleAvailable"] is False

    @pytest.mark.asyncio
    async def test_returns_network_mode_from_sys_config(self) -> None:
        """sys_config 有 networkMode 时优先返回."""
        db = AsyncMock()
        stored = {
            "datasource.type": "remote_api",
            "datasource.network_mode": "wan",
        }
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=True),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows(stored),
            ),
        ):
            mock_settings.DATA_SOURCE_TYPE = "remote_api"
            mock_settings.NETWORK_MODE = "lan"
            mock_settings.HISTORY_DATA_API_URL = ""
            mock_settings.HISTORY_DATA_API_TOKEN = ""
            mock_settings.HISTORY_DATA_API_TIMEOUT = 30.0
            mock_settings.SIGNALR_HUB_URL = ""
            mock_settings.SIGNALR_ENABLED = False
            mock_settings.SIGNALR_RECONNECT_INTERVAL = 5
            mock_settings.REALTIME_WRITEBACK_ENABLED = False

            data = await get_datasource_config(db)
            assert data["networkMode"] == "wan"
            assert data["tailscaleAvailable"] is True


class TestUpdateDatasourceConfigNetworkMode:
    """update_datasource_config 的 networkMode 校验与 Tailscale 切换触发."""

    @pytest.mark.asyncio
    async def test_invalid_network_mode_raises_value_error(self) -> None:
        """非法 networkMode 抛出 ValueError."""
        db = AsyncMock()
        with pytest.raises(ValueError, match="不支持的 networkMode"):
            await update_datasource_config(db, "admin", networkMode="invalid")

    @pytest.mark.asyncio
    async def test_network_mode_change_triggers_tailscale_switch(self) -> None:
        """networkMode 从 lan 变为 wan 时调用 switch_network_mode.

        模拟场景：sys_config 中无 networkMode（回退 settings），settings.NETWORK_MODE
        初始为 lan，update 后 setattr 改为 wan，before/after 不同触发切换。
        """
        db = AsyncMock()
        # stored 中不包含 network_mode，让 get_datasource_config 回退到 settings
        stored = {
            "datasource.type": "remote_api",
        }
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=True),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows(stored),
            ),
            patch("app.services.datasource_config.switch_network_mode") as mock_switch,
            patch(
                "app.services.datasource_config._set_config_values",
                new=AsyncMock(),
            ),
            patch(
                "app.services.datasource_config._write_audit",
                new=AsyncMock(),
            ),
        ):
            mock_settings.DATA_SOURCE_TYPE = "remote_api"
            mock_settings.NETWORK_MODE = "lan"  # before 时为 lan
            mock_settings.HISTORY_DATA_API_URL = ""
            mock_settings.HISTORY_DATA_API_TOKEN = ""
            mock_settings.HISTORY_DATA_API_TIMEOUT = 30.0
            mock_settings.SIGNALR_HUB_URL = ""
            mock_settings.SIGNALR_ENABLED = False
            mock_settings.SIGNALR_RECONNECT_INTERVAL = 5
            mock_settings.REALTIME_WRITEBACK_ENABLED = False

            mock_switch.return_value = {
                "status": "success",
                "message": "Tailscale 已切换到公网",
                "latencyMs": 100,
            }

            data = await update_datasource_config(db, "admin", networkMode="wan")

            # 验证 setattr 已将 settings.NETWORK_MODE 改为 wan
            assert mock_settings.NETWORK_MODE == "wan"
            # 验证 switch_network_mode 被调用
            mock_switch.assert_called_once_with("wan")
            # 验证返回值包含 tailscaleSwitch
            assert data["tailscaleSwitch"]["status"] == "success"

    @pytest.mark.asyncio
    async def test_network_mode_unchanged_no_switch(self) -> None:
        """networkMode 未变化时不触发 switch_network_mode."""
        db = AsyncMock()
        stored = {
            "datasource.type": "remote_api",
            "datasource.network_mode": "lan",
        }
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=True),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows(stored),
            ),
            patch("app.services.datasource_config.switch_network_mode") as mock_switch,
            patch(
                "app.services.datasource_config._set_config_values",
                new=AsyncMock(),
            ),
            patch(
                "app.services.datasource_config._write_audit",
                new=AsyncMock(),
            ),
        ):
            mock_settings.DATA_SOURCE_TYPE = "remote_api"
            mock_settings.NETWORK_MODE = "lan"
            mock_settings.HISTORY_DATA_API_URL = ""
            mock_settings.HISTORY_DATA_API_TOKEN = ""
            mock_settings.HISTORY_DATA_API_TIMEOUT = 30.0
            mock_settings.SIGNALR_HUB_URL = ""
            mock_settings.SIGNALR_ENABLED = False
            mock_settings.SIGNALR_RECONNECT_INTERVAL = 5
            mock_settings.REALTIME_WRITEBACK_ENABLED = False

            # 传入 networkMode=lan（与当前相同）
            data = await update_datasource_config(db, "admin", networkMode="lan")

            # 验证 switch_network_mode 未被调用
            mock_switch.assert_not_called()
            # 返回值不包含 tailscaleSwitch
            assert "tailscaleSwitch" not in data or data.get("tailscaleSwitch") is None

    @pytest.mark.asyncio
    async def test_network_mode_not_provided_no_switch(self) -> None:
        """未传入 networkMode 时不触发 switch_network_mode."""
        db = AsyncMock()
        stored = {
            "datasource.type": "remote_api",
            "datasource.network_mode": "lan",
            "datasource.history_api_url": "http://old.example.com/api",
        }
        with (
            patch("app.services.datasource_config.settings") as mock_settings,
            patch("app.services.datasource_config._is_tailscale_available", return_value=True),
            patch(
                "app.services.datasource_config._get_config_rows",
                new=_mock_get_config_rows(stored),
            ),
            patch("app.services.datasource_config.switch_network_mode") as mock_switch,
            patch(
                "app.services.datasource_config._set_config_values",
                new=AsyncMock(),
            ),
            patch(
                "app.services.datasource_config._write_audit",
                new=AsyncMock(),
            ),
        ):
            mock_settings.DATA_SOURCE_TYPE = "remote_api"
            mock_settings.NETWORK_MODE = "lan"
            mock_settings.HISTORY_DATA_API_URL = "http://example.com/api"
            mock_settings.HISTORY_DATA_API_TOKEN = ""
            mock_settings.HISTORY_DATA_API_TIMEOUT = 30.0
            mock_settings.SIGNALR_HUB_URL = ""
            mock_settings.SIGNALR_ENABLED = False
            mock_settings.SIGNALR_RECONNECT_INTERVAL = 5
            mock_settings.REALTIME_WRITEBACK_ENABLED = False

            # 只更新 historyApiUrl，不传 networkMode
            data = await update_datasource_config(
                db, "admin", historyApiUrl="http://new.example.com/api"
            )

            mock_switch.assert_not_called()
            assert "tailscaleSwitch" not in data or data.get("tailscaleSwitch") is None
