"""TDengine 数据源提供者 — 宽表查询 + taosrest.

数据架构优化 Phase 2：从窄表 7 次查询改为宽表 1 次查询。
- make_query_fn: 使用 query_wide_table_native（宽表一次查 7 列）
- query_trend_data: 保留窄表查询（波形展示路径兼容）

回填性能优化：
- 回路 → 宽表名解析缓存为模块级 TTL 缓存（见下方设计说明），跨查询闭包共享
- 历史窗口（end 早于 now-65min）跳过 Redis 实时 1 小时缓存探测（必然 miss）
- COV 前向填充 + RawTimeSeries 转换移入 ``asyncio.to_thread``，
  避免 3600 行 × dict 的纯 CPU 处理阻塞事件循环内其他并发回路

时区口径（P0-3 修复）：
- 写入侧将 ts 转 Asia/Shanghai 墙钟存储，服务器按 +8 解释 naive 字符串
- 查询边界经 ``_format_ts`` 统一输出带 Z 的 UTC ISO 串（naive 视为 UTC）
- Redis 1 小时缓存行 ts 为 +8 墙钟字符串，经 ``_stored_ts_to_utc_naive``
  转 UTC 后再与窗口比较（直接字符串比较恒假，缓存永不命中）
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta, timezone
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
# 并发安全：不使用 asyncio.Lock。历史教训（2026-07-28 定位）：模块级
# asyncio.Lock 在 Python 3.10+ 于首次竞争时绑定当前事件循环，而 Celery
# worker 每个任务可能运行在新事件循环——一旦发生过一次竞争，后续所有任务的
# 解析都会抛 "bound to a different event loop"，导致 DataPlanner 全回路取数
# 失败、KPI 快照批量 INCONCLUSIVE，且只能重启 worker 恢复（2026-07-20 起
# 反复出现）。去掉锁的最坏后果是两个并发解析重复执行相同的 PG 查询并写入
# 相同的缓存值，无害；正确性不依赖互斥。
# ---------------------------------------------------------------------------
_SUBTABLE_CACHE_TTL_S = 300.0
# loop_id → (subtable, loop_part, expire_ts)，expire_ts 基于 time.monotonic()
_subtable_cache: dict[str, tuple[str, str, float]] = {}

# Redis 实时缓存只保存最近 1 小时数据；窗口 end 早于 (now - 65 分钟) 时
# 判定为历史窗口并跳过探测（1 小时 + 5 分钟余量，容忍时钟偏差与边界窗口）
_REDIS_REALTIME_SKIP_S = 65 * 60

# 存储侧时区（Asia/Shanghai）：写入侧（realtime_subscriber._normalize_ts /
# data_import）统一将 ts 转为 +8 墙钟字符串落库，TDengine 服务器按 +8 解释
# naive 时间字符串（实证：CAST('2026-07-28 10:00:00' AS TIMESTAMP) →
# 2026-07-28T02:00:00Z）。Redis 1 小时缓存行的 ts 同为 +8 墙钟字符串，
# 与 UTC 查询窗口比较前必须显式按此时区解析，不能直接字符串比较。
_STORED_TZ = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------
# Redis 实时缓存完整性命中条件（R13，2026-09-06）
#
# 旧逻辑仅校验"首尾距窗口边界 ≤60s"即命中——缓存只有首尾两点、中间整段
# 缺失时仍会遮蔽本地 TDengine 完整数据。新逻辑双条件：
#   1) 排序去重后窗口内点数 ≥ 期望点数 × (1 - 10%)；
#      期望点数 = 窗口时长 / interval_s + 1（含首尾）。
#   2) 首尾仍在 60s 边界容差内（保留：写入节奏与查询边界天然不对齐）。
#
# 10% 容差依据：实时链路按秒级节奏写入，正常缺口（单条丢失/秒级抖动）
# 占比 <1%；断线/重启类中间缺口通常分钟级起步（10 分钟即 ~17%）。10%
# 阈值可有效区分"正常抖动"（命中，省一次本地查询）与"中间缺口"
# （未命中，回源本地 TD 宽表核查），不以扩大容差换取命中率。
# ---------------------------------------------------------------------------
_REDIS_CACHE_COVERAGE_TOLERANCE = 0.10
_REDIS_CACHE_EDGE_TOLERANCE_S = 60.0


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

            # 无锁解析（见文件头设计说明）：并发重复解析无害，禁止使用
            # 模块级 asyncio.Lock（跨事件循环绑定会拖垮全部取数）。
            from sqlalchemy import select

            from app.models.loop import LoopLedger

            # 子表名唯一权威来源：回路台账 tag_name（天然不含测点角色后缀）。
            # 历史 bug（2026-08-20 修复）：此前取「第一个测点名」rsplit('.') 反推，
            # 但测点名用下划线分隔角色（xx_PV）→ 剥离失败且顺序不稳定 → 子表名漂移
            loop_result = await db.execute(
                select(LoopLedger.tag_name).where(LoopLedger.id == loop_id)
            )
            loop_tag_name = loop_result.scalar_one_or_none()
            if not loop_tag_name:
                logger.debug("宽表查询: 回路 %s 不存在或无 tag_name", loop_id)
                return None

            loop_part = loop_tag_name
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
                            start_dt = _parse_ts(start)
                            end_dt = _parse_ts(end)
                            if isinstance(start_dt, datetime) and isinstance(end_dt, datetime):
                                # 缓存行 ts 为 +8 墙钟字符串（与落库口径一致），
                                # 须逐行解析为 UTC 时刻再与窗口（naive UTC）比较；
                                # 命中行的 ts 就地改写为 UTC naive 字符串，使
                                # 下游 _rows_to_raw_series 输出与宽表路径一致。
                                filtered_rows = []
                                for row in redis_rows:
                                    row_ts = _stored_ts_to_utc_naive(row.get("ts", ""))
                                    if row_ts is not None and start_dt <= row_ts <= end_dt:
                                        row["ts"] = row_ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                                        filtered_rows.append(row)
                            else:
                                filtered_rows = []
                            if filtered_rows:
                                # R13：排序去重 + 覆盖完整性校验通过才允许命中，
                                # 否则回源本地 TD 宽表（本地 TD 是计算唯一权威源）
                                filtered_rows = _dedupe_sort_redis_rows(filtered_rows)
                                if _redis_cache_meets_completeness(
                                    filtered_rows, start_dt, end_dt, interval_s
                                ):
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
    """格式化查询时间边界为带 Z 的 UTC ISO 串（毫秒精度）。

    时区口径（P0-3 修复）：写入侧将 ts 转为 Asia/Shanghai 墙钟存储，
    TDengine 服务器按 +8 解释 naive 字符串（实证：naive '10:00:00' →
    存储为 02:00Z）。naive datetime 在本代码库约定为 UTC，若直接
    strftime 成 naive 字符串拼接 WHERE，会被服务器当成 +8 墙钟，
    过滤窗口比意图早 8 小时。因此统一输出带 Z 的 UTC ISO 串，让
    服务器按 UTC 解释（与 trend_service 趋势路径透传 Z 串的口径一致，
    该路径已验证正常工作）。

    - naive datetime：视为 UTC
    - aware datetime：先转 UTC
    - 字符串：原样透传（调用方自带时区信息，如趋势路径的 Z 后缀 ISO 串）
    """
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return str(dt)


def _stored_ts_to_utc_naive(ts_val: Any) -> datetime | None:
    """解析存储侧（+8 墙钟）时间字符串并转为 naive UTC，失败返回 None。

    Redis 1 小时缓存行与 TDengine 落库行的 ts 均为 Asia/Shanghai 墙钟
    字符串（见 _STORED_TZ 注释），与 UTC 查询窗口比较前必须显式按
    存储时区解析再转 UTC；字符串直接比较会导致缓存永不命中。
    带时区信息的字符串（含 Z）按其实际时区转换。
    """
    try:
        dt = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_STORED_TZ)
    return dt.astimezone(UTC).replace(tzinfo=None)


def _parse_ts(ts_val: Any) -> Any:
    """解析 TDengine 返回的时间戳为 naive UTC datetime。

    taosrest 连接已固定 timezone=UTC（见 TDengineConnectionPool._create_connection），
    TIMESTAMP 列返回 aware UTC datetime；aware 输入先 astimezone 到 UTC 再去
    tzinfo（而非直接丢弃时区），避免 aware 非 UTC 时间被错当成 UTC。
    """
    if isinstance(ts_val, datetime):
        # 保持 naive UTC，对齐 DB TIMESTAMP WITHOUT TIME ZONE
        return ts_val.astimezone(UTC).replace(tzinfo=None) if ts_val.tzinfo else ts_val
    if isinstance(ts_val, str):
        try:
            dt = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return ts_val
        # naive 字符串按调用方约定视为 UTC（存储侧 +8 墙钟串由
        # _stored_ts_to_utc_naive 专门处理，不走这里）
        return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt
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


def _dedupe_sort_redis_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 ts 升序排序并去重（R13 缓存完整性前置）.

    入参行已在探测阶段把 ts 改写为 naive UTC 字符串（均可解析）。
    重复 ts 保留**后出现**的行——Redis list 已按写入时间升序还原，
    同 ts 的后写值代表更新的状态。

    Args:
        rows: 探测过滤后的缓存行（ts 为 naive UTC 字符串）

    Returns:
        排序去重后的行列表（稳定排序，同 ts 保留最后出现的行）
    """
    keyed = [(_parse_ts(row.get("ts", "")), row) for row in rows]
    keyed.sort(key=lambda item: item[0])
    deduped: list[dict[str, Any]] = []
    last_key: Any = None
    for key, row in keyed:
        if deduped and key == last_key:
            deduped[-1] = row
        else:
            deduped.append(row)
        last_key = key
    return deduped


def _redis_cache_meets_completeness(
    rows: list[dict[str, Any]],
    start_dt: datetime,
    end_dt: datetime,
    interval_s: int,
) -> bool:
    """Redis 实时缓存完整性命中判定（R13）.

    双条件（见模块头 _REDIS_CACHE_COVERAGE_TOLERANCE 登记依据）：
    1) 去重后点数 ≥ 期望点数 × (1 - 容差)，期望点数 = 窗口时长 / interval_s + 1；
    2) 首尾仍在边界容差（60s）内。

    Args:
        rows: 排序去重后的缓存行（ts 为 naive UTC 字符串）
        start_dt / end_dt: 查询窗口（naive UTC）
        interval_s: 请求采样间隔（秒），非法值按 1s 处理

    Returns:
        True 表示缓存可作为该窗口的完整数据源
    """
    if not rows:
        return False
    first_ts = _parse_ts(rows[0].get("ts", ""))
    last_ts = _parse_ts(rows[-1].get("ts", ""))
    if not isinstance(first_ts, datetime) or not isinstance(last_ts, datetime):
        return False
    if (first_ts - start_dt).total_seconds() > _REDIS_CACHE_EDGE_TOLERANCE_S:
        return False
    if (end_dt - last_ts).total_seconds() > _REDIS_CACHE_EDGE_TOLERANCE_S:
        return False

    window_s = (end_dt - start_dt).total_seconds()
    if window_s <= 0:
        return True
    effective_interval = float(interval_s) if interval_s and interval_s > 0 else 1.0
    expected_points = window_s / effective_interval + 1.0  # 含首尾两点
    return len(rows) >= expected_points * (1.0 - _REDIS_CACHE_COVERAGE_TOLERANCE)


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
