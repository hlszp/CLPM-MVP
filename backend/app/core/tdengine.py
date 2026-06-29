"""TDengine async query module.

使用 TDengine REST API（httpx）查询波形数据。
开发环境 TDengine 可能无数据，返回空数组 + 明确状态标识，不报错。

安全：tag_name 白名单校验 + start_time/end_time ISO 格式校验，防止 SQL 注入。

DDL 对齐（db/tdengine/01_supertable.sql v3.0）：
- 超级表名: st_loop_data（非 tag_data）
- 列: ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality（非 val/quality）
- TAGS: loop_id, unit_id（非 tag_name）
- 子表命名: d_loop_<位号小写连字符转下划线>

连接复用：httpx.AsyncClient 单例，避免频繁建连。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# tag_name 白名单：仅允许字母、数字、下划线、连字符、点号（如 HDS-RX-TIC-101.PV）
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

# PV 角色对应的质量码列名
_QUALITY_COLUMN_MAP: dict[str, str | None] = {
    "PV": "pv_quality",
    "SP": None,
    "OP": None,
    "MODE": None,
    "PID_P": None,
    "PID_I": None,
    "PID_D": None,
}

# TDengine REST API 端口（原生端口 + 11）
_TD_REST_PORT = 6041


def _validate_tag_name(tag_name: str) -> str:
    """校验 tag_name 格式，防止 SQL 注入。"""
    if not tag_name or not _TAG_NAME_PATTERN.match(tag_name):
        raise ValueError(f"Invalid tag_name format: {tag_name!r}")
    return tag_name


def _validate_time(time_str: str, field_name: str) -> str:
    """校验时间格式为 ISO 8601，防止 SQL 注入。"""
    try:
        datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid {field_name} format: {time_str!r}") from exc
    return time_str


def _parse_tag_to_table_column(tag_name: str) -> tuple[str, str, str | None]:
    """将 tag_name 解析为子表名、数据列名、质量列名。

    示例: "HDS-RX-TIC-101.PV" → ("d_loop_hds_rx_tic_101", "pv", "pv_quality")
    """
    # 按最后一个 "." 分割：loop_part 和 role
    if "." in tag_name:
        loop_part, role = tag_name.rsplit(".", 1)
    else:
        loop_part, role = tag_name, "PV"

    role_upper = role.upper()
    column = _ROLE_COLUMN_MAP.get(role_upper, "pv")
    quality_col = _QUALITY_COLUMN_MAP.get(role_upper)

    # 子表命名: d_loop_<位号小写连字符转下划线>
    subtable = "d_loop_" + loop_part.lower().replace("-", "_").replace(".", "_")
    # 清理多余下划线
    subtable = re.sub(r"_+", "_", subtable)

    return subtable, column, quality_col


# ---------------------------------------------------------------------------
# httpx.AsyncClient 单例（连接复用）
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None
_client_loop: Any = None  # 记录 client 绑定的 event loop


async def _get_client() -> httpx.AsyncClient:
    """获取全局 httpx.AsyncClient 单例。

    Celery AsyncTask 为每个任务创建新的 event loop 并在任务结束后关闭，
    导致跨任务复用的 httpx.AsyncClient 绑定到已关闭的 loop（"Event loop is closed"）。
    通过检测 loop 变化/关闭自动重建 client 解决此问题。
    """
    import asyncio

    global _client, _client_loop
    current_loop = asyncio.get_running_loop()
    # 检测是否需要重建：client 为空/已关闭/loop 不一致/原 loop 已关闭
    need_recreate = (
        _client is None
        or _client.is_closed
        or _client_loop is not current_loop
        or (_client_loop is not None and getattr(_client_loop, "is_closed", False))
    )
    if need_recreate:
        if _client is not None and not _client.is_closed:
            try:
                await _client.aclose()
            except Exception:  # noqa: BLE001
                pass
        _client = httpx.AsyncClient(
            base_url=f"http://{settings.TDENGINE_HOST}:{_TD_REST_PORT}",
            auth=(settings.TDENGINE_USER, settings.TDENGINE_PASSWORD),
            timeout=httpx.Timeout(10.0, connect=5.0),
            # 禁用 keep-alive 连接池：避免 Celery 跨任务 event loop 复用
            # 导致连接池绑定的 loop 已关闭（"Event loop is closed"）
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        _client_loop = current_loop
    return _client


async def _reset_client() -> None:
    """重置全局 client（请求失败时调用，强制下次重建）。"""
    global _client, _client_loop
    if _client is not None and not _client.is_closed:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001
            pass
    _client = None
    _client_loop = None


async def close_client() -> None:
    """关闭全局 httpx.AsyncClient（应用关闭时调用）。"""
    global _client, _client_loop
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
    _client_loop = None


async def execute_sql(sql: str) -> list[dict[str, Any]]:
    """执行任意 TDengine SQL（仅供内部可信调用，如健康检查、监控）。

    Args:
        sql: SQL 语句（调用方需自行确保安全，不接受外部输入）

    Returns:
        行列表，每项 {column: value}。失败返回空列表。
    """
    try:
        client = await _get_client()
        resp = await client.post("/rest/sql", content=sql)
        if resp.status_code != 200:
            logger.warning(
                "TDengine REST API 返回 %s: %s",
                resp.status_code,
                resp.text[:200],
            )
            return []
        payload = resp.json()
        if payload.get("code") != 0:
            logger.warning("TDengine 执行错误: %s", payload.get("message", ""))
            return []
        column_meta = payload.get("column_meta", [])
        col_names = [c[0] for c in column_meta]
        data_rows = payload.get("data", [])
        return [dict(zip(col_names, row, strict=False)) for row in data_rows]
    except RuntimeError as exc:
        # Celery 跨任务 event loop 关闭后，httpx 连接可能失效，重置后重试一次
        if "Event loop is closed" in str(exc) or "Event loop is not running" in str(exc):
            logger.warning("TDengine 请求失败（%s），重置 client 后重试", exc)
            await _reset_client()
            try:
                client = await _get_client()
                resp = await client.post("/rest/sql", content=sql)
                if resp.status_code != 200:
                    return []
                payload = resp.json()
                if payload.get("code") != 0:
                    return []
                column_meta = payload.get("column_meta", [])
                col_names = [c[0] for c in column_meta]
                data_rows = payload.get("data", [])
                return [dict(zip(col_names, row, strict=False)) for row in data_rows]
            except Exception as exc2:  # noqa: BLE001
                logger.warning("TDengine 重试仍失败: %s", exc2)
                return []
        logger.warning("TDengine 执行失败（返回空列表）: %s", exc)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("TDengine 执行失败（返回空列表）: %s", exc)
        return []


async def query_trend_data(
    tag_name: str,
    start_time: str,
    end_time: str,
) -> list[dict[str, Any]]:
    """从 TDengine 查询 Tag 的趋势数据。

    Args:
        tag_name: Tag 位号名（如 HDS-RX-TIC-101.PV，白名单校验）
        start_time: 起始时间（ISO 格式校验）
        end_time: 结束时间（ISO 格式校验）

    Returns:
        数据点列表，每项 {ts, value, quality}。连接失败或无数据返回空数组。

    Raises:
        ValueError: tag_name 或时间格式不合法
    """
    # 安全校验：防止 SQL 注入
    safe_tag_name = _validate_tag_name(tag_name)
    safe_start = _validate_time(start_time, "start_time")
    safe_end = _validate_time(end_time, "end_time")

    # 解析 tag_name → 子表名 + 列名
    subtable, data_column, quality_column = _parse_tag_to_table_column(safe_tag_name)

    # 构建查询 SQL：从子表查询指定列
    # 子表名和列名通过白名单映射生成，不含用户输入，安全拼接
    if quality_column:
        sql = (
            f"SELECT ts, {data_column}, {quality_column} "
            f"FROM {settings.TDENGINE_DB}.{subtable} "
            f"WHERE ts >= '{safe_start}' AND ts <= '{safe_end}' "
            f"ORDER BY ts ASC"
        )
    else:
        sql = (
            f"SELECT ts, {data_column} "
            f"FROM {settings.TDENGINE_DB}.{subtable} "
            f"WHERE ts >= '{safe_start}' AND ts <= '{safe_end}' "
            f"ORDER BY ts ASC"
        )

    try:
        client = await _get_client()
        resp = await client.post("/rest/sql", content=sql)
        if resp.status_code != 200:
            logger.warning(
                "TDengine REST API 返回 %s: %s",
                resp.status_code,
                resp.text[:200],
            )
            return []
        payload = resp.json()
        # TDengine REST 响应：{"code":0,"data":[[...]],"column_meta":[...]}
        if payload.get("code") != 0:
            logger.warning("TDengine 查询错误: %s", payload.get("message", ""))
            return []
        data_rows = payload.get("data", [])
        rows: list[dict[str, Any]] = []
        for row in data_rows:
            ts_val = row[0]
            value = float(row[1]) if len(row) > 1 and row[1] is not None else None
            quality = str(row[2]) if len(row) > 2 and row[2] is not None else "GOOD"
            rows.append({"ts": str(ts_val), "value": value, "quality": quality})
        return rows
    except RuntimeError as exc:
        # Celery 跨任务 event loop 关闭后，httpx 连接可能失效，重置后重试一次
        if "Event loop is closed" in str(exc) or "Event loop is not running" in str(exc):
            logger.warning("TDengine 查询失败（%s），重置 client 后重试", exc)
            await _reset_client()
            try:
                client = await _get_client()
                resp = await client.post("/rest/sql", content=sql)
                if resp.status_code != 200:
                    return []
                payload = resp.json()
                if payload.get("code") != 0:
                    return []
                data_rows = payload.get("data", [])
                rows: list[dict[str, Any]] = []
                for row in data_rows:
                    ts_val = row[0]
                    value = float(row[1]) if len(row) > 1 and row[1] is not None else None
                    quality = str(row[2]) if len(row) > 2 and row[2] is not None else "GOOD"
                    rows.append({"ts": str(ts_val), "value": value, "quality": quality})
                return rows
            except Exception as exc2:  # noqa: BLE001
                logger.warning("TDengine 查询重试仍失败: %s", exc2)
                return []
        logger.warning("TDengine 查询失败（返回空数组）: %s", exc)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("TDengine 查询失败（返回空数组）: %s", exc)
        return []


# ---------------------------------------------------------------------------
# DataPlanner 适配器（Phase 4：将 query_trend_data 包装为 DataPlanner 所需签名）
# ---------------------------------------------------------------------------


def make_dataplanner_query_fn(db: Any) -> Any:
    """构造 DataPlanner 适配器函数（闭包捕获 db 会话）。

    将现有 ``query_trend_data(tag_name, start, end)`` 包装为 DataPlanner
    所需的 ``TDengineQueryFn`` 签名：
    ``(loop_id, tag_roles, start, end, interval_s) → RawTimeSeries``

    适配器职责：
        1. 查询 LoopTagMapping + TagRegistry 获取每个 tag_role 的 tag_name
        2. 调用 query_trend_data 查询每个 tag 的时序数据
        3. quality 字符串转 int（GOOD→1, BAD→0），对齐预处理 quality_code 映射
        4. 合并所有 tag 的数据为 RawTimeSeries（统一时间轴）

    Args:
        db: 异步数据库会话（用于查询 LoopTagMapping / TagRegistry）

    Returns:
        TDengineQueryFn 闭包（async callable）
    """

    async def _query_fn(
        loop_id: str,
        tag_roles: list[str],
        start: datetime,
        end: datetime,
        interval_s: int,
    ):
        """DataPlanner 适配器闭包：按 tag 角色列表查询 TDengine 原始时序数据。"""
        from sqlalchemy import select

        from app.contracts.data_types import RawTimeSeries
        from app.models.loop import LoopTagMapping
        from app.models.tag import TagRegistry

        # 1. 查询 LoopTagMapping（tag_role 大写存储，DataPlanner 传入小写）
        m_result = await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
        mappings = {m.tag_role.upper(): m for m in m_result.scalars().all()}

        # 2. 查询 TagRegistry 获取 tag_name
        tag_ids = [str(m.tag_id) for m in mappings.values()]
        tags_map: dict[str, Any] = {}
        if tag_ids:
            t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
            for t in t_result.scalars().all():
                tags_map[str(t.id)] = t

        # 3. 对每个 tag_role 查询 TDengine 数据
        # TDengine 存储的时间带 Z 后缀（ISO 8601 UTC），查询时需保持一致
        start_iso = (
            start.isoformat().replace("+00:00", "Z") if start.tzinfo else start.isoformat() + "Z"
        )
        end_iso = end.isoformat().replace("+00:00", "Z") if end.tzinfo else end.isoformat() + "Z"

        # role_lower → rows（每行 {ts, value, quality}）
        role_data: dict[str, list[dict[str, Any]]] = {}
        for role_lower in tag_roles:
            role_upper = role_lower.upper()
            mapping = mappings.get(role_upper)
            if not mapping:
                logger.debug("适配器: 回路 %s 无 %s 角色映射，跳过", loop_id, role_upper)
                continue
            tag = tags_map.get(str(mapping.tag_id))
            if not tag:
                logger.debug("适配器: 回路 %s 的 %s tag 未找到，跳过", loop_id, role_upper)
                continue
            rows = await query_trend_data(tag.tag_name, start_iso, end_iso)
            role_data[role_lower] = rows

        # 4. 构建统一时间轴（所有 tag 共享同一 TDengine 子表，时间戳应一致；
        #    但容错处理：取并集后排序，缺失点填 None）
        ts_set: set[str] = set()
        for rows in role_data.values():
            for row in rows:
                ts_set.add(str(row.get("ts")))
        sorted_ts_str = sorted(ts_set)

        timestamps = [_parse_ts_str(ts) for ts in sorted_ts_str]

        # 5. 构建信号值和质量码字典
        signals: dict[str, list[Any]] = {}
        quality_codes: dict[str, list[int]] = {}

        # 质量码映射：query_trend_data 返回字符串（"0"/"1"/"2" 或 "GOOD"），
        # 对齐 preprocessing/quality_code.py 的 _GOOD_CODES={1,2,3,192} 约定
        _GOOD_QUALITY_STRS = {"1", "2", "3", "192", "GOOD"}

        for role_lower, rows in role_data.items():
            ts_to_value = {str(row.get("ts")): row.get("value") for row in rows}
            signals[role_lower] = [ts_to_value.get(ts) for ts in sorted_ts_str]

            # PV 角色附带质量码（对齐预处理 quality_code.map_quality_code 约定）
            if role_lower.upper() == "PV":
                quality_key = f"{role_lower}_quality"
                ts_to_quality = {
                    str(row.get("ts")): str(row.get("quality", "GOOD")).upper() for row in rows
                }
                quality_codes[quality_key] = [
                    1 if ts_to_quality.get(ts, "GOOD") in _GOOD_QUALITY_STRS else 0
                    for ts in sorted_ts_str
                ]

        logger.debug(
            "适配器: loop=%s, roles=%s, points=%d, signals=%s",
            loop_id,
            list(role_data.keys()),
            len(timestamps),
            {k: len(v) for k, v in signals.items()},
        )

        return RawTimeSeries(
            timestamps=timestamps,
            signals=signals,
            quality_codes=quality_codes,
        )

    return _query_fn


def _parse_ts_str(ts_str: str) -> datetime:
    """将时间戳字符串解析为 datetime（兼容多种格式）。

    Args:
        ts_str: 时间戳字符串（ISO 8601 或 epoch）

    Returns:
        datetime 对象（解析失败时返回当前时间兜底）
    """
    s = ts_str.strip()
    # 尝试 epoch 秒
    try:
        epoch = float(s)
        return datetime.fromtimestamp(epoch, tz=None)
    except (ValueError, TypeError):
        pass
    # 尝试 ISO 8601
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass
    logger.warning("适配器: 无法解析时间戳 %r，使用当前时间兜底", s)
    return datetime.now()


__all__ = [
    "query_trend_data",
    "execute_sql",
    "close_client",
    "make_dataplanner_query_fn",
]
