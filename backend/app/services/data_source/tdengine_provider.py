"""
TDengine 数据源提供者 — 测点点表查询 + taosrest.

2026-09-25 宽表退役：读取侧唯一路径是测点点表 st_point_data_v1，由
logical_wide_builder.build_logical_wide 组装算法数据（宽表超级表
的查询实现 query_wide_table_native / query_last_values_before 已删除）。
- make_query_fn: 按 manifest 窗口分片（恒 point）调用 LogicalWideBuilder
- query_trend_data: 保留窄表查询（波形展示路径兼容）

性能与并发约束：
- DataPlanner 会并发执行多个查询，共享同一 AsyncSession；SQLAlchemy 不允许
  同一 AsyncSession 并发 execute，故本闭包内不做共享会话的并发取数
- COV 前向填充由 LogicalWideBuilder 内部完成（测点点表稀疏存储）
- 历史窗口的 Redis 实时缓存探测随宽表路径一并移除：实时缓存仅服务"最新值"
  场景，趋势/评估一律回源本地 TDengine 点表

时区口径（P0-3 修复）：
- 写入侧将 ts 转 Asia/Shanghai 墙钟存储，服务器按 +8 解释 naive 字符串
- 查询边界经 _format_ts 统一输出带 Z 的 UTC ISO 串（naive 视为 UTC）
"""

from __future__ import annotations

import logging
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
# loop_id → (subtable, loop_part, expire_ts)，expire_ts 基于 time.monotonic()

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

        # DataPlanner 会并发执行多个 tagGroup 查询；这些查询共享同一个
        # AsyncSession。SQLAlchemy 明确不允许同一 AsyncSession 并发 execute，
        # 因此先串行解析并缓存回路宽表名，后续 TDengine 查询仍可并发执行。
        # 解析结果缓存于模块级 TTL 缓存（见文件头设计说明），跨闭包共享。
        async def _query_fn_point(
            loop_id: str,
            tag_roles: list[str],
            start: Any,
            end: Any,
            interval_s: int,
        ) -> RawTimeSeries:
            """测点点表查询闭包（2026-09-25 宽表退役后唯一读取路径）.

            按 history_layout_manifest 解析窗口分片（恒 point）后统一走
            LogicalWideBuilder —— 唯一从 st_point_data_v1 组装算法数据的实现。
            无法解析时间边界时返回空序列（不静默换源）。
            """
            from app.services.data_source.history_layout_router import resolve_window_layouts

            start_dt = _parse_ts(start)
            end_dt = _parse_ts(end)
            if isinstance(start_dt, datetime) and isinstance(end_dt, datetime):
                # db=None：布局解析用独立短会话，绝不触碰共享 AsyncSession
                # （并发 query_fn 共享 session 是既有红线）
                layouts = await resolve_window_layouts(None, loop_id, start_dt, end_dt)
                return await _point_or_mixed_query(
                    db, loop_id, tag_roles, start_dt, end_dt, interval_s, layouts
                )
            logger.warning("查询时间边界非法（返回空序列）: loop=%s start=%r", loop_id, start)
            return RawTimeSeries(
                timestamps=[], signals={r.lower(): [] for r in tag_roles}, quality_codes={}
            )

        return _query_fn_point

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


async def _point_or_mixed_query(
    db: Any,
    loop_id: str,
    tag_roles: list[str],
    start_dt: datetime,
    end_dt: datetime,
    interval_s: int,
    layouts: list,
) -> Any:
    """测点点表查询：逐分片走 LogicalWideBuilder（2026-09-25 宽表退役）.

    layouts 为按时间升序的窗口分片（history_layout_router.resolve_window_layouts，
    宽表退役后恒为单片 point；保留分片合并逻辑以备将来"按窗口切源"）。
    输出 RawTimeSeries 按 ts 排序去重（同 ts 保留后段）。异常不静默换源。

    AD01：point 片的完整 SeriesContext 随结果透传；多片窗口上下文 layout 改标
    mixed（网格/覆盖口径以 point 片为基准）。
    """
    from app.contracts.data_types import RawTimeSeries
    from app.services.data_source.logical_wide_builder import build_logical_wide

    merged_ts: list[datetime] = []
    merged_signals: dict[str, list[Any]] = {r.lower(): [] for r in tag_roles}
    merged_pv_q: list[int] = []
    roles_lower = [r.lower() for r in tag_roles]
    point_context = None

    for part in layouts:
        raw = await build_logical_wide(db, loop_id, tag_roles, part.start, part.end, interval_s)
        if raw.series_context is not None:
            point_context = raw.series_context
        for i, ts in enumerate(raw.timestamps):
            merged_ts.append(ts)
            for role in roles_lower:
                sig = raw.signals.get(role) or [None] * len(raw.timestamps)
                merged_signals[role].append(sig[i])
            if "pv" in roles_lower:
                qc = raw.quality_codes.get("pv_quality") or [-1] * len(raw.timestamps)
                merged_pv_q.append(qc[i])

    # 排序 + 边界去重（同 ts 保留后段——边界 T 处 point 覆盖 legacy）
    order = sorted(range(len(merged_ts)), key=lambda i: merged_ts[i])
    out_ts: list[datetime] = []
    out_signals: dict[str, list[Any]] = {r: [] for r in roles_lower}
    out_q: list[int] = []
    last_ts: datetime | None = None
    for i in order:
        if last_ts is not None and merged_ts[i] == last_ts:
            for role in roles_lower:
                out_signals[role][-1] = merged_signals[role][i]
            if "pv" in roles_lower:
                out_q[-1] = merged_pv_q[i]
            continue
        out_ts.append(merged_ts[i])
        for role in roles_lower:
            out_signals[role].append(merged_signals[role][i])
        if "pv" in roles_lower:
            out_q.append(merged_pv_q[i])
        last_ts = merged_ts[i]

    if point_context is not None and len(layouts) > 1:
        from app.contracts import series_context as sc

        point_context.layout = sc.LAYOUT_MIXED

    quality_codes: dict[str, list[int]] = {}
    if "pv" in roles_lower:
        quality_codes["pv_quality"] = out_q
    return RawTimeSeries(
        timestamps=out_ts,
        signals=out_signals,
        quality_codes=quality_codes,
        series_context=point_context,
    )


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
