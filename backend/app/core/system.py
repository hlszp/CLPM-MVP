"""系统级命令封装 — Tailscale 子网路由切换.

封装 tailscale up 命令调用，用于应用层局域网/公网切换。
- 局域网（lan）: tailscale up --accept-routes=false --reset=false  → 移除子网路由，走局域网直连
- 公网（wan）:   tailscale up --accept-routes=true  --reset=false  → 安装子网路由，走 Tailscale

环境约束：
- 容器内（生产）：tailscale 客户端不存在，静默跳过，返回 skipped
- 本地开发（宿主机）：通过 sudoers 免密 sudo 执行（见 deploy/sudoers.d/clpm-tailscale）

参考 app/main.py 的 subprocess.Popen 模式：list 参数 + DEVNULL 重定向，无 shell=True。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# tailscale up 命令超时（秒）— 命令通常秒级返回，5s 足够
_TAILSCALE_TIMEOUT = 5

# 支持的网络模式
_VALID_MODES = {"lan", "wan"}


def _is_tailscale_available() -> bool:
    """检测 tailscale 客户端是否可用（PATH 中存在可执行文件）.

    容器内（生产环境）通常未安装 tailscale，返回 False。
    """
    return shutil.which("tailscale") is not None


def switch_network_mode(mode: str) -> dict:
    """切换 Tailscale 子网路由模式.

    Args:
        mode: "lan" 移除子网路由（直连局域网）; "wan" 安装子网路由（走 Tailscale）

    Returns:
        {"status": "success"|"failed"|"skipped", "message": str, "latencyMs": int|None}
        - success: tailscale 命令执行成功
        - failed: 命令返回非 0 / 超时 / 异常
        - skipped: tailscale 客户端不可用（容器环境或未安装）
    """
    start = datetime.now(UTC).replace(tzinfo=None)

    if mode not in _VALID_MODES:
        return {
            "status": "failed",
            "message": f"不支持的 networkMode: {mode!r}，可选: lan / wan",
            "latencyMs": None,
        }

    if not _is_tailscale_available():
        logger.info("tailscale 客户端不可用，跳过 %s 模式切换（容器环境或未安装）", mode)
        return {
            "status": "skipped",
            "message": "tailscale 客户端不可用（容器环境或未安装），跳过切换",
            "latencyMs": None,
        }

    # 构造命令：sudo -n tailscale up --accept-routes[=true|false] --reset=false
    # sudoers 配置允许免密执行（见 deploy/sudoers.d/clpm-tailscale）
    # -n 非交互式：若 sudoers 未配置免密，立即失败而非挂起等待密码
    accept_routes = "true" if mode == "wan" else "false"
    cmd = [
        "sudo",
        "-n",
        "tailscale",
        "up",
        f"--accept-routes={accept_routes}",
        "--reset=false",
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=_TAILSCALE_TIMEOUT,
            check=False,
        )
        latency_ms = int(
            (datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000
        )
        if result.returncode == 0:
            label = "公网（安装子网路由）" if mode == "wan" else "局域网（移除子网路由）"
            logger.info("Tailscale 切换成功: %s", label)
            return {
                "status": "success",
                "message": f"Tailscale 已切换到{label}",
                "latencyMs": latency_ms,
            }
        stderr = result.stderr.decode("utf-8", errors="replace")[:200]
        logger.warning("Tailscale 切换失败 (rc=%s): %s", result.returncode, stderr)
        return {
            "status": "failed",
            "message": f"tailscale 命令返回码 {result.returncode}: {stderr}",
            "latencyMs": latency_ms,
        }
    except subprocess.TimeoutExpired:
        latency_ms = int(
            (datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000
        )
        logger.warning("Tailscale 切换超时 (%ss)", _TAILSCALE_TIMEOUT)
        return {
            "status": "failed",
            "message": f"tailscale 命令超时（{_TAILSCALE_TIMEOUT}s）",
            "latencyMs": latency_ms,
        }
    except Exception as exc:
        latency_ms = int(
            (datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000
        )
        logger.warning("Tailscale 切换异常: %s", exc)
        return {
            "status": "failed",
            "message": f"切换异常: {exc}",
            "latencyMs": latency_ms,
        }


__all__ = ["switch_network_mode", "_is_tailscale_available"]
