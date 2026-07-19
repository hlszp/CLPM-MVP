"""app/core/system.py 单元测试 — Tailscale 切换封装.

验证 switch_network_mode 的行为：
- 非法 mode → failed
- tailscale 不可用 → skipped
- lan/wan 模式 → 正确命令参数
- 非零返回码 / 超时 / 异常 → failed 不抛出
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.core.system import _is_tailscale_available, switch_network_mode


class TestSwitchNetworkMode:
    """switch_network_mode 行为测试."""

    def test_invalid_mode_returns_failed(self) -> None:
        """非法 mode 值返回 failed."""
        result = switch_network_mode("invalid")
        assert result["status"] == "failed"
        assert "不支持" in result["message"]
        assert result["latencyMs"] is None

    @patch("app.core.system._is_tailscale_available", return_value=False)
    def test_tailscale_unavailable_returns_skipped(self, _: MagicMock) -> None:
        """tailscale 客户端不存在时返回 skipped（容器环境）."""
        result = switch_network_mode("lan")
        assert result["status"] == "skipped"
        assert "不可用" in result["message"]
        assert result["latencyMs"] is None

    @patch("app.core.system._is_tailscale_available", return_value=True)
    @patch("app.core.system.subprocess.run")
    def test_lan_mode_calls_tailscale_with_accept_routes_false(
        self, mock_run: MagicMock, _: MagicMock
    ) -> None:
        """lan 模式调用 tailscale up --accept-routes=false."""
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        result = switch_network_mode("lan")
        assert result["status"] == "success"
        assert "局域网" in result["message"]
        # 校验命令参数
        cmd = mock_run.call_args[0][0]
        assert "sudo" in cmd
        assert "-n" in cmd
        assert "tailscale" in cmd
        assert "up" in cmd
        assert "--accept-routes=false" in cmd
        assert "--reset=false" in cmd

    @patch("app.core.system._is_tailscale_available", return_value=True)
    @patch("app.core.system.subprocess.run")
    def test_wan_mode_calls_tailscale_with_accept_routes_true(
        self, mock_run: MagicMock, _: MagicMock
    ) -> None:
        """wan 模式调用 tailscale up --accept-routes=true."""
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        result = switch_network_mode("wan")
        assert result["status"] == "success"
        assert "公网" in result["message"]
        cmd = mock_run.call_args[0][0]
        assert "--accept-routes=true" in cmd

    @patch("app.core.system._is_tailscale_available", return_value=True)
    @patch("app.core.system.subprocess.run")
    def test_nonzero_returncode_returns_failed(
        self, mock_run: MagicMock, _: MagicMock
    ) -> None:
        """tailscale 返回非 0 时返回 failed."""
        mock_run.return_value = MagicMock(returncode=1, stderr=b"permission denied")
        result = switch_network_mode("wan")
        assert result["status"] == "failed"
        assert "返回码 1" in result["message"]
        assert "permission denied" in result["message"]

    @patch("app.core.system._is_tailscale_available", return_value=True)
    @patch(
        "app.core.system.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="tailscale", timeout=5),
    )
    def test_timeout_returns_failed(self, _: MagicMock, __: MagicMock) -> None:
        """超时返回 failed."""
        result = switch_network_mode("wan")
        assert result["status"] == "failed"
        assert "超时" in result["message"]

    @patch("app.core.system._is_tailscale_available", return_value=True)
    @patch(
        "app.core.system.subprocess.run",
        side_effect=FileNotFoundError("tailscale not found"),
    )
    def test_exception_returns_failed(self, _: MagicMock, __: MagicMock) -> None:
        """异常返回 failed 不抛出."""
        result = switch_network_mode("wan")
        assert result["status"] == "failed"
        assert "切换异常" in result["message"]

    @patch("app.core.system._is_tailscale_available", return_value=True)
    @patch("app.core.system.subprocess.run")
    def test_success_returns_latency_ms(
        self, mock_run: MagicMock, _: MagicMock
    ) -> None:
        """成功时返回 latencyMs（int 类型）."""
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        result = switch_network_mode("lan")
        assert result["status"] == "success"
        assert isinstance(result["latencyMs"], int)
        assert result["latencyMs"] >= 0


class TestIsTailscaleAvailable:
    """_is_tailscale_available 检测函数测试."""

    @patch("app.core.system.shutil.which", return_value="/usr/bin/tailscale")
    def test_available_when_in_path(self, _: MagicMock) -> None:
        """tailscale 在 PATH 中时返回 True."""
        assert _is_tailscale_available() is True

    @patch("app.core.system.shutil.which", return_value=None)
    def test_unavailable_when_not_in_path(self, _: MagicMock) -> None:
        """tailscale 不在 PATH 中时返回 False（容器环境）."""
        assert _is_tailscale_available() is False
