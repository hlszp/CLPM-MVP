"""TDengine 数据源提供者 — 宽表查询 + taosrest.

数据架构优化 Phase 2：从窄表 7 次查询改为宽表 1 次查询。
- make_query_fn: 使用 query_wide_table_native（宽表一次查 7 列）
- query_trend_data: 保留窄表查询（波形展示路径兼容）

回填性能优化：
- 回路 → 宽表名解析缓存为模块级 TTL 缓存（见下方设计说明），跨查询闭包共享
- 历史窗口（end 早于 now-65min）跳过 Redis 实时 1 小时缓存探测（必然 miss）
- COV 前向填充 + RawTimeSeries 转换移入 ``asyncio.to_thread``，
  避免 3600 行 × dict 的纯 CPU 处理阻塞事件循环内其他并发回路
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.data_source.base import QueryFn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 回路 → 宽表名解析缓存（模块级，TTL 300s，跨 make_query_fn 闭包共享）
#
# 设计理由：回填场景下 kpi_calc._build_data_planner 为每个回路-窗口新建
# DataPlanner（进而每次调用 make_query_fn 新建查询闭包）。若解析缓存挂在
# 闭包上，同一回路每个小时窗口都要重复 2 次 PG 查询（LoopTagMapping +
# TagRegistry）。宽表名由回路 tag 映射派生，属低频变更的静态配置，因此在
# 进程内跨闭包共享 TTL 缓存（300s，与 DataPlanner 指标契约缓存口径一致）。
# 仅缓存成功解析的结果：解析为 None（未配置映射）时不缓存，避免「先查询
# 后配置映射」的场景被 5 分钟陈旧的 negative 缓存卡住。
# 并发安全：模块级 asyncio.Lock 防止同一回路并发重复解析。锁只会在单进程
# 单事件循环内发生竞争（Celery worker 每进程同一时刻只运行一个 AsyncTask
# 事件循环；跨循环均为顺序使用，无跨环竞争）。
# ---------------------------------------------------------------------------
_SUBTABLE_CACHE_TTL_S = 300.0
# loop_id → (subtable, loop_part, expire_ts)，expire_ts 基于 time.monotonic()
_subtable_cache: dict[str, tuple[str, str, float]] = {}
_subtable_cache_lock = asyncio.Lock()

# Redis 实时缓存只保存最近 1 小时数据；窗口 end 早于 (now - 65 分钟) 时
# 判定为历史窗口并跳过探测（1 小时 + 5 分钟余量，容忍时钟偏差与边界窗口）
_REDIS_REALTIME_SKIP_S = 65 * 60


class TDengineProvider:
    """TDengine 数据源提供者（宽表 + taosrest）.

    Phase 2 改造：
    - make_query_fn 从窄表 7 次查询改为宽表 1 次查询
    - query_trend_data 保留窄表查询（兼容波形展示路径）
    """

    def make_query_fn(self, db: Any) -> QueryFn:
        """构造 TDengine 查询函数（宽表查询）。

        替代原 make_dataplanner_query_fn 的 7 次窄表并行查询，
        改为宽表 1 次查询（一次查 7 列 + 质量码）。

        Args:
            db: 异步数据库会话（查询回路-Tag 映射）

        Returns:
            DataPlanner 兼容的查询闭包
        """
        from app.contracts.data_types import RawTimeSeries
        from app.core.tdengine import make_subtable_name
        from app.core.tdengine_native import query_wide_table_native

        # DataPlanner 会并发执行多个 tagGroup 查询；这些查询共享同一个
        # AsyncSession。SQLAlchemy 明确不允许同一 AsyncSession 并发 execute，
        # 因此先串行解析并缓存回路宽表名，后续 TDengine 查询仍可并发执行。
        # 解析结果缓存于模块级 TTL 缓存（见文件头设计说明），跨闭包共享。
        async def _resolve_subtable(loop_id: str) -> tuple[str, str] | None:
            cached = _subtable_cache.get(loop_id)
            if cached is not None and cached[2] > time.monotonic():
                return cached[0], cached[1]

            async with _subtable_cache_lock:
                cached = _subtable_cache.get(loop_id)
                if cached is not None and cached[2] > time.monotonic():
                    return cached[0], cached[1]

                from sqlalchemy import select

                from app.models.loop import LoopTagMapping
                from app.models.tag import TagRegistry

                mapping_result = await db.execute(
                    select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id)
                )
                mappings = list(mapping_result.scalars().all())
                if not mappings:
                    logger.debug("宽表查询: 回路 %s 无 tag 映射", loop_id)
                    return None

                tag_ids = [str(mapping.tag_id) for mapping in mappings]
                tag_result = await db.execute(
                    select(TagRegistry).where(TagRegistry.id.in_(tag_ids))
                )
                tags = list(tag_result.scalars().all())
                if not tags:
                    logger.debug("宽表查询: 回路 %s 无 tag 记录", loop_id)
                    return None

                tag_name = tags[0].tag_name
                loop_part = tag_name.rsplit(".", 1)[0] if "." in tag_name else tag_name
                subtable = make_subtable_name(loop_part)
                # 仅缓存成功解析的结果（None 不缓存，见文件头设计说明）
                _subtable_cache[loop_id] = (
                    subtable,
                    loop_part,
                    time.monotonic() + _SUBTABLE_CACHE_TTL_S,
                )
                return subtable, loop_part

        async def _query_fn_wide(
            loop_id: str,
            tag_roles: list[str],
            start: Any,
            end: Any,
            interval_s: int,
        ) -> RawTimeSeries:
            """宽表查询闭包：一次查 7 列，替代 7 次窄表查询。"""
            # 1-3. 串行解析并缓存 loop_id → 宽表名，避免共享 session 并发查询。
            resolved_subtable = await _resolve_subtable(loop_id)
            if resolved_subtable is None:
                return RawTimeSeries(timestamps=[], signals={}, quality_codes={})
            subtable, loop_part = resolved_subtable

            # 4. 构造查询时间范围
            start_str = _format_ts(start)
            end_str = _format_ts(end)

            rows = None

            # 5. 尝试从 Redis 1 小时缓存获取数据。
            # 该缓存只保存最近 1 小时数据；历史窗口（end 早于 now-65min）必然
            # miss，直接跳过整个探测块（回填场景可省去每回路-窗口的 lrange +
            # 逐行 json.loads）。近 1 小时窗口保持原有探测行为不变。
            if _is_historical_window(end):
                logger.debug(
                    "跳过 Redis 实时缓存探测（历史窗口）: loop=%s, end=%s",
                    loop_id,
                    end_str,
                )
            else:
                from app.services.data_source.realtime_subscriber import get_subscriber

                subscriber = get_subscriber()
                if subscriber:
                    try:
                        redis_rows = await subscriber.get_history_values(loop_part)
                        if redis_rows:
                            # 过滤指定时间范围
                            filtered_rows = [
                                row for row in redis_rows if start_str <= row["ts"] <= end_str
                            ]
                            if filtered_rows:
                                first_ts = _parse_ts(filtered_rows[0]["ts"])
                                last_ts = _parse_ts(filtered_rows[-1]["ts"])
                                start_dt = _parse_ts(start)
                                end_dt = _parse_ts(end)

                                # 检查缓存是否覆盖了请求的时间范围（容差 60 秒）
                                if (
                                    isinstance(first_ts, datetime)
                                    and isinstance(last_ts, datetime)
                                    and isinstance(start_dt, datetime)
                                    and isinstance(end_dt, datetime)
                                ):
                                    if (first_ts - start_dt).total_seconds() <= 60 and (
                                        end_dt - last_ts
                                    ).total_seconds() <= 60:
                                        rows = filtered_rows
                                        logger.debug(
                                            "命中 Redis 1 小时缓存: loop=%s, points=%d",
                                            loop_id,
                                            len(rows),
                                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("读取 Redis 缓存失败 (loop=%s): %s", loop_id, exc)

            # 6. 如果缓存未命中，回退到宽表查询（一次查 7 列 + 质量码）
            if rows is None:
                try:
                    rows = await query_wide_table_native(subtable, start_str, end_str)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("宽表查询失败 loop=%s subtable=%s: %s", loop_id, subtable, exc)
                    return RawTimeSeries(timestamps=[], signals={}, quality_codes={})

            if not rows:
                logger.debug("宽表查询: 回路 %s 无数据 (subtable=%s)", loop_id, subtable)
                return RawTimeSeries(timestamps=[], signals={}, quality_codes={})

            # 6.5 前向填充 COV 列（sp/mode/pid_p/i/d）
            # 这些角色采用变化时推送（COV），宽表中稀疏存储，需展开为完整曲线。
            # 先查询窗口起点之前的最后有效值作为初始值（解决窗口开头为 NULL 的情况）。
            # 异步 TDengine 调用，保持 await。
            from app.core.tdengine_native import query_last_values_before

            initial = await query_last_values_before(subtable, start_str)

            # 7. COV 前向填充 + RawTimeSeries 转换是纯 CPU 处理
            # （3600 行 × dict），移入线程池避免阻塞事件循环内其他并发回路。
            # 线程内不触碰任何 asyncio 对象 / db session。
            raw = await asyncio.to_thread(_rows_to_raw_series, rows, initial, tag_roles)

            logger.debug(
                "宽表查询: loop=%s, subtable=%s, points=%d, signals=%s",
                loop_id,
                subtable,
                len(raw.timestamps),
                {k: len(v) for k, v in raw.signals.items()},
            )

            return raw

        return _query_fn_wide

    async def query_trend_data(
        self,
        tag_name: str,
        start_time: str,
        end_time: str,
        sample_interval: int = 1,
    ) -> list[dict[str, Any]]:
        """查询单个 tag 的趋势数据（窄表查询，波形展示路径兼容）。

        Args:
            sample_interval: 采样间隔（秒）。TDengine 模式下查询全量数据，
                由上层 LTTB 降采样处理，此参数仅用于日志记录。
        """
        from app.core.tdengine import query_trend_data

        return await query_trend_data(tag_name, start_time, end_time)

    async def close(self) -> None:
        """关闭 TDengine 连接池."""
        from app.core.tdengine import close_client
        from app.core.tdengine_native import TDengineConnectionPool

        await close_client()
        TDengineConnectionPool.close_all()
        logger.info("TDengineProvider 已关闭")


def _format_ts(dt: Any) -> str:
    """格式化 datetime 为 TDengine 查询时间字符串（毫秒精度）。"""
    from datetime import datetime

    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return str(dt)


def _parse_ts(ts_val: Any) -> Any:
    """解析 TDengine 返回的时间戳为 datetime。

    taosrest 默认返回 datetime 对象（convert_timestamp=True）。
    """
    from datetime import datetime

    if isinstance(ts_val, datetime):
        # 保持 naive UTC，对齐 DB TIMESTAMP WITHOUT TIME ZONE
        return ts_val.replace(tzinfo=None) if ts_val.tzinfo else ts_val
    if isinstance(ts_val, str):
        try:
            return datetime.fromisoformat(ts_val.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            pass
    return ts_val


def _is_historical_window(end: Any) -> bool:
    """判断查询窗口是否为历史窗口（end 早于 now - 65 分钟）。

    Redis 实时 1 小时缓存只保存最近 1 小时数据，历史窗口必然 miss，
    调用方据此跳过整个 subscriber 探测块（回填场景的主要收益）。
    end 无法解析为 datetime 时返回 False（保持探测，行为与之前一致）。

    时区说明：``_parse_ts`` 统一返回 naive UTC datetime，阈值同样按
    naive UTC 计算，避免 aware/naive 混比。
    """
    end_dt = _parse_ts(end)
    if not isinstance(end_dt, datetime):
        return False
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=_REDIS_REALTIME_SKIP_S)
    return end_dt < cutoff


def _rows_to_raw_series(
    rows: list[dict[str, Any]],
    initial: dict[str, Any],
    tag_roles: list[str],
) -> Any:
    """COV 前向填充 + 行 → RawTimeSeries 转换（纯 CPU）。

    经 ``asyncio.to_thread`` 在线程池执行，函数内不触碰任何 asyncio
    对象 / db session。会就地修改 ``rows``（填充 COV 列的 None 值）。

    Args:
        rows: 宽表查询返回的行（按 ts 升序）
        initial: 窗口起点之前每个 COV 列的最后有效值（前向填充初始值）
        tag_roles: 需要提取的 tag 角色列表

    Returns:
        RawTimeSeries（timestamps / signals / pv_quality 质量码）
    """
    from app.contracts.data_types import RawTimeSeries
    from app.core.tdengine_native import COV_FILL_COLUMNS

    # 前向填充 COV 列（sp/mode/pid_p/i/d）：变化时推送的角色在宽表中
    # 稀疏存储，用上一次有效值展开为完整曲线
    last_vals: dict[str, Any] = {c: initial.get(c) for c in COV_FILL_COLUMNS}
    for row in rows:
        for c in COV_FILL_COLUMNS:
            v = row.get(c)
            if v is None:
                row[c] = last_vals[c]
            else:
                last_vals[c] = v

    # 转换为 RawTimeSeries
    timestamps = [_parse_ts(row.get("ts")) for row in rows]
    signals: dict[str, list[Any]] = {}
    for role in tag_roles:
        role_lower = role.lower()
        signals[role_lower] = [row.get(role_lower) for row in rows]

    # PV 质量码
    quality_codes: dict[str, list[int]] = {}
    if "pv" in [r.lower() for r in tag_roles]:
        quality_codes["pv_quality"] = [int(row.get("pv_quality") or 0) for row in rows]

    return RawTimeSeries(
        timestamps=timestamps,
        signals=signals,
        quality_codes=quality_codes,
    )
