"""Data link monitoring service (S3-B1).

监控数据采集链路健康度：
- AAS OPC UA 连接状态检查
- TDengine 数据新鲜度检查
- 发现问题时触发告警
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.services.alerting import send_alert

logger = logging.getLogger(__name__)


async def check_aas_connection() -> dict[str, Any]:
    """检查 AAS OPC UA 连接状态。

    Returns:
        Mock 模式: {"status": "ok", "mode": "mock"}
        真实模式成功: {"status": "ok", "mode": "real"}
        真实模式失败: {"status": "fail", "mode": "real", "error": "..."}
    """
    # Mock 模式直接返回 ok
    if settings.AAS_MOCK_MODE:
        return {"status": "ok", "mode": "mock"}

    try:
        import asyncua

        client = asyncua.Client(settings.AAS_ENDPOINT)
        await client.connect()
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return {"status": "ok", "mode": "real"}
    except Exception as exc:  # noqa: BLE001
        logger.critical("AAS OPC UA 连接失败: %s", exc)
        return {"status": "fail", "mode": "real", "error": str(exc)}


async def check_tdengine_freshness(threshold_minutes: int = 30) -> dict[str, Any]:
    """检查 TDengine 数据新鲜度。

    Args:
        threshold_minutes: 新鲜度阈值（分钟），默认 30 分钟

    Returns:
        数据新鲜: {"status": "ok", "threshold_minutes": N, "count": M}
        数据陈旧: {"status": "stale", "threshold_minutes": N, "last_data_time": None}
        检查失败: {"status": "fail", "error": "..."}
    """
    from app.core.tdengine import _pool

    # 校验 threshold 为整数，防止 SQL 注入（TDengine 不支持参数化占位符）
    threshold = int(threshold_minutes)

    conn = None
    healthy = True
    try:
        conn = await _pool.acquire()
        # threshold 已校验为整数，db 来自可信配置，安全拼接
        sql = (
            f"SELECT COUNT(*) FROM {settings.TDENGINE_DB}.st_loop_data "
            f"WHERE ts >= NOW - {threshold}m"
        )
        result = await conn.query(sql)
        count = 0
        for row in result:
            count = int(row[0]) if row[0] is not None else 0
            break

        if count == 0:
            logger.warning("TDengine 数据不新鲜：最近 %d 分钟无数据", threshold)
            return {
                "status": "stale",
                "threshold_minutes": threshold,
                "last_data_time": None,
            }
        return {
            "status": "ok",
            "threshold_minutes": threshold,
            "count": count,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("TDengine 数据新鲜度检查失败: %s", exc)
        healthy = False
        return {"status": "fail", "error": str(exc)}
    finally:
        if conn is not None:
            await _pool.release(conn, healthy=healthy)


async def run_data_link_check() -> dict[str, Any]:
    """执行完整数据采集链路检查。

    Returns:
        {"aas": {...}, "tdengine": {...}, "overall": "ok"/"degraded"}
    """
    aas_result = await check_aas_connection()
    tdengine_result = await check_tdengine_freshness()

    # 判断整体状态
    aas_ok = aas_result.get("status") == "ok"
    td_ok = tdengine_result.get("status") == "ok"
    overall = "ok" if (aas_ok and td_ok) else "degraded"

    # 发现问题时发送告警
    if not aas_ok:
        await send_alert(
            title="AAS 连接异常",
            message=f"AAS OPC UA 连接失败: {aas_result.get('error', 'unknown')}",
            severity="critical",
        )
    if not td_ok:
        await send_alert(
            title="TDengine 数据不新鲜",
            message=f"TDengine 状态: {tdengine_result.get('status')}",
            severity="warning",
        )

    return {
        "aas": aas_result,
        "tdengine": tdengine_result,
        "overall": overall,
    }


__all__ = [
    "check_aas_connection",
    "check_tdengine_freshness",
    "run_data_link_check",
]
