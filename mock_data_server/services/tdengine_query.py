"""模拟远端数据服务 — TDengine 历史数据查询.

独立实现 TDengine REST API 查询（不依赖主应用代码），
按 tag_name 查询子表数据，返回 timestamps + values + qualities。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import httpx

from mock_data_server.config import config

logger = logging.getLogger(__name__)

# tag_name 白名单
_TAG_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-\s]{1,128}$")

# tag_name 后缀 → DDL 列名映射
_ROLE_COLUMN_MAP: dict[str, str] = {
    "PV": "pv",
    "SP": "sp",
    "OP": "op",
    "MODE": "mode",
    "PID_P": "pid_p",
    "PID_I": "pid_i",
    "PID_D": "pid_d",
}

_QUALITY_COLUMN_MAP: dict[str, str | None] = {
    "PV": "pv_quality",
    "SP": None,
    "OP": None,
    "MODE": None,
    "PID_P": None,
    "PID_I": None,
    "PID_D": None,
}

# httpx 客户端单例
_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    """获取 httpx.AsyncClient 单例."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=config.tdengine_rest_url,
            auth=config.tdengine_auth,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
    return _client


def _parse_tag_to_table_column(tag_name: str) -> tuple[str, str, str | None]:
    """将 tag_name 解析为子表名、数据列名、质量列名.

    示例: "HDS-RX-TIC-101.PV" → ("d_loop_hds_rx_tic_101", "pv", "pv_quality")
    """
    if "." in tag_name:
        loop_part, role = tag_name.rsplit(".", 1)
    else:
        loop_part, role = tag_name, "PV"

    role_upper = role.upper()
    column = _ROLE_COLUMN_MAP.get(role_upper, "pv")
    quality_col = _QUALITY_COLUMN_MAP.get(role_upper)

    subtable = "d_loop_" + loop_part.lower().replace("-", "_").replace(".", "_")
    subtable = re.sub(r"_+", "_", subtable)
    return subtable, column, quality_col


def _validate_tag_name(tag_name: str) -> str:
    """校验 tag_name 格式."""
    if not tag_name or not _TAG_NAME_PATTERN.match(tag_name):
        raise ValueError(f"Invalid tag_name format: {tag_name!r}")
    return tag_name


def _validate_time(time_str: str, field_name: str) -> str:
    """校验时间格式."""
    try:
        datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid {field_name} format: {time_str!r}") from exc
    return time_str


async def query_history_data(
    tag_codes: list[str],
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    """查询多个 tag 的历史数据.

    Args:
        tag_codes: 标签编码列表（tag_name 格式，如 "LIC-101.PV"）
        start_time: 开始时间（ISO 格式）
        end_time: 结束时间（ISO 格式）

    Returns:
        {"timestamps": [...], "series": [{"tagCode": ..., "values": [...], "qualities": [...]}]}
    """
    # 校验
    for tc in tag_codes:
        _validate_tag_name(tc)
    _validate_time(start_time, "startTime")
    _validate_time(end_time, "endTime")

    # 去掉时区后缀（TDengine REST 不支持 Z/+00:00）
    start_clean = start_time.replace("Z", "").split("+")[0]
    end_clean = end_time.replace("Z", "").split("+")[0]

    client = await _get_client()

    # 查询每个 tag 的数据
    all_timestamps: set[str] = set()
    tag_data: dict[str, list[dict[str, Any]]] = {}  # tag_code → [{ts, value, quality}]

    for tag_code in tag_codes:
        subtable, data_column, quality_column = _parse_tag_to_table_column(tag_code)

        if quality_column:
            sql = (
                f"SELECT ts, {data_column}, {quality_column} "
                f"FROM {config.TDENGINE_DB}.{subtable} "
                f"WHERE ts >= '{start_clean}' AND ts <= '{end_clean}' "
                f"ORDER BY ts ASC"
            )
        else:
            sql = (
                f"SELECT ts, {data_column} "
                f"FROM {config.TDENGINE_DB}.{subtable} "
                f"WHERE ts >= '{start_clean}' AND ts <= '{end_clean}' "
                f"ORDER BY ts ASC"
            )

        try:
            resp = await client.post("/rest/sql", content=sql)
            if resp.status_code != 200:
                logger.warning("TDengine REST 返回 %s: %s", resp.status_code, resp.text[:200])
                tag_data[tag_code] = []
                continue

            payload = resp.json()
            if payload.get("code") != 0:
                logger.warning("TDengine 查询错误: %s", payload.get("message", ""))
                tag_data[tag_code] = []
                continue

            data_rows = payload.get("data", [])
            rows: list[dict[str, Any]] = []
            for row in data_rows:
                ts_val = str(row[0])
                value = float(row[1]) if len(row) > 1 and row[1] is not None else None
                quality = int(row[2]) if len(row) > 2 and row[2] is not None else 1
                rows.append({"ts": ts_val, "value": value, "quality": quality})
                all_timestamps.add(ts_val)
            tag_data[tag_code] = rows

        except Exception as exc:  # noqa: BLE001
            logger.warning("查询 tag %s 失败: %s", tag_code, exc)
            tag_data[tag_code] = []

    # 构建统一时间轴
    sorted_timestamps = sorted(all_timestamps)

    # 构建 series
    series: list[dict[str, Any]] = []
    for tag_code in tag_codes:
        rows = tag_data.get(tag_code, [])
        ts_to_value = {r["ts"]: r["value"] for r in rows}
        ts_to_quality = {r["ts"]: r["quality"] for r in rows}

        values: list[str] = []
        qualities: list[int] = []
        for ts in sorted_timestamps:
            v = ts_to_value.get(ts)
            values.append("" if v is None else str(v))
            # TDengine 质量码 1=Good → 外部 API 1=Good
            q = ts_to_quality.get(ts, 1)
            qualities.append(q if q in (0, 1, 2, 3) else 1)

        series.append({
            "tagCode": tag_code,
            "values": values,
            "qualities": qualities,
        })

    return {
        "timestamps": sorted_timestamps,
        "series": series,
    }


async def close_client() -> None:
    """关闭 httpx 连接池."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
