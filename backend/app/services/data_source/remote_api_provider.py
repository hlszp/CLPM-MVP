"""远程 HTTP API 数据源提供者 — 对接 HistoryDataAppService.

对接接口：``POST /api/services/v1/HistoryData/Get``

请求体::

    {"tagCodes": ["LIC-101.PV"], "startTime": "...", "endTime": "...", "sampleInterval": 1}

响应体::

    {"code": 200, "data": {"timestamps": [...], "series": [{"tagCode": "...", "values": [...], "qualities": [...]}]}}

质量码映射（外部 API → CLPM 内部）：
- 1 (Good) → 1
- 2 (Bad) / 3 (离线) / 0 (未知) → 0
- 兼容 OPC DA 192 (Good) → 1
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from app.core.config import settings
from app.services.data_source.base import QueryFn

logger = logging.getLogger(__name__)

# 外部 API Good 质量码集合（1=Good, 192=OPC DA Good）
_GOOD_QUALITY_CODES = frozenset({1, 192})


class RemoteApiProvider:
    """远程 HTTP API 数据源提供者.

    通过 ``HistoryDataAppService`` API 查询历史数据，
    返回与 DataPlanner 兼容的 ``RawTimeSeries``。

    连接管理：httpx.AsyncClient 单例，支持 Celery 跨 event loop 复用。
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取 httpx.AsyncClient 单例（Celery-safe）."""
        current_loop = asyncio.get_running_loop()
        # 注意：asyncio loop 的 is_closed 是方法，需要调用
        loop_closed = (
            self._client_loop is not None
            and callable(getattr(self._client_loop, "is_closed", None))
            and self._client_loop.is_closed()
        )
        need_recreate = (
            self._client is None
            or self._client.is_closed
            or self._client_loop is not current_loop
            or loop_closed
        )
        if need_recreate:
            if self._client is not None and not self._client.is_closed:
                try:
                    await self._client.aclose()
                except Exception:  # noqa: BLE001
                    pass
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if settings.HISTORY_DATA_API_TOKEN:
                headers["Authorization"] = f"Bearer {settings.HISTORY_DATA_API_TOKEN}"
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.HISTORY_DATA_API_TIMEOUT, connect=10.0),
                headers=headers,
            )
            self._client_loop = current_loop
        return self._client

    def make_query_fn(self, db: Any) -> QueryFn:
        """构造远程 API 查询函数.

        Args:
            db: 异步数据库会话（查询回路-Tag 映射）

        Returns:
            查询函数闭包
        """

        async def _query_fn(
            loop_id: str,
            tag_roles: list[str],
            start: datetime,
            end: datetime,
            interval_s: int,
        ):
            """远程 API 适配器闭包."""
            from sqlalchemy import select

            from app.contracts.data_types import RawTimeSeries
            from app.models.loop import LoopTagMapping
            from app.models.tag import TagRegistry

            # 1. 查询回路-Tag 映射
            m_result = await db.execute(
                select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id)
            )
            mappings = {m.tag_role.upper(): m for m in m_result.scalars().all()}

            tag_ids = [str(m.tag_id) for m in mappings.values()]
            tags_map: dict[str, Any] = {}
            if tag_ids:
                t_result = await db.execute(
                    select(TagRegistry).where(TagRegistry.id.in_(tag_ids))
                )
                for t in t_result.scalars().all():
                    tags_map[str(t.id)] = t

            # 2. 构建 tagCodes 列表（tag_name 作为 tagCode）
            role_tag_names: dict[str, str] = {}  # role_lower → tag_name
            for role_lower in tag_roles:
                role_upper = role_lower.upper()
                mapping = mappings.get(role_upper)
                if not mapping:
                    continue
                tag = tags_map.get(str(mapping.tag_id))
                if not tag:
                    continue
                role_tag_names[role_lower] = tag.tag_name

            if not role_tag_names:
                logger.warning("远程API: 回路 %s 无有效 Tag 映射", loop_id)
                return RawTimeSeries(timestamps=[], signals={}, quality_codes={})

            # 3. 调用外部 API
            tag_codes = list(role_tag_names.values())
            start_str = start.replace(tzinfo=None).isoformat() if start.tzinfo else start.isoformat()
            end_str = end.replace(tzinfo=None).isoformat() if end.tzinfo else end.isoformat()

            request_body = {
                "tagCodes": tag_codes,
                "startTime": start_str,
                "endTime": end_str,
                "sampleInterval": interval_s,
            }

            try:
                client = await self._get_client()
                resp = await client.post(
                    settings.HISTORY_DATA_API_URL,
                    json=request_body,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "远程API返回 %s: %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    return RawTimeSeries(timestamps=[], signals={}, quality_codes={})

                payload = resp.json()
                if payload.get("code") != 200:
                    logger.warning("远程API业务错误: %s", payload.get("message", ""))
                    return RawTimeSeries(timestamps=[], signals={}, quality_codes={})

                data = payload.get("data", {})
                raw_timestamps: list[str] = data.get("timestamps", [])
                series_list: list[dict] = data.get("series", [])

            except Exception as exc:  # noqa: BLE001
                logger.warning("远程API查询失败（返回空）: %s", exc)
                return RawTimeSeries(timestamps=[], signals={}, quality_codes={})

            # 4. 解析时间戳
            timestamps: list[datetime] = []
            for ts_str in raw_timestamps:
                try:
                    timestamps.append(
                        datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                    )
                except (ValueError, TypeError):
                    try:
                        timestamps.append(datetime.fromtimestamp(float(ts_str)))
                    except (ValueError, TypeError):
                        logger.warning("远程API: 无法解析时间戳 %r", ts_str)

            # 5. 构建 signals + quality_codes
            # tagCode → series 映射
            series_map: dict[str, dict] = {s.get("tagCode", ""): s for s in series_list}

            signals: dict[str, list[Any]] = {}
            quality_codes: dict[str, list[int]] = {}

            for role_lower, tag_name in role_tag_names.items():
                series = series_map.get(tag_name, {})
                values: list[str] = series.get("values", [])
                qualities: list[int] = series.get("qualities", [])

                # 字符串值转 float
                parsed_values: list[Any] = []
                for v in values:
                    if v is None or v == "":
                        parsed_values.append(None)
                    else:
                        try:
                            parsed_values.append(float(v))
                        except (ValueError, TypeError):
                            parsed_values.append(None)
                signals[role_lower] = parsed_values

                # 质量码映射（仅 PV 角色）
                if role_lower.upper() == "PV":
                    quality_key = f"{role_lower}_quality"
                    mapped_qualities: list[int] = []
                    for q in qualities:
                        try:
                            q_int = int(q) if q is not None else 0
                        except (ValueError, TypeError):
                            q_int = 0
                        mapped_qualities.append(
                            1 if q_int in _GOOD_QUALITY_CODES else 0
                        )
                    quality_codes[quality_key] = mapped_qualities

            logger.debug(
                "远程API: loop=%s, roles=%s, points=%d",
                loop_id,
                list(role_tag_names.keys()),
                len(timestamps),
            )

            return RawTimeSeries(
                timestamps=timestamps,
                signals=signals,
                quality_codes=quality_codes,
            )

        return _query_fn

    async def close(self) -> None:
        """关闭 httpx 连接池."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
        self._client_loop = None
        logger.info("RemoteApiProvider 已关闭")
