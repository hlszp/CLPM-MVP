"""点历史仓储（P1-4）：TDengine 测点子表批量写/查 + PG 元数据推进.

设计依据：设计文档 §4（存储、身份与迁移元数据）/§4.3（重复、迟到与写入确认）。

职责边界：
- 本模块是**唯一**直接访问 ``st_point_data_v1`` 的运行时代码
  （算法/endpoints 禁止直达，见设计 §5）；
- 写入路径：payload 归一化 → 已存事实比对（幂等/冲突分流）→ TD 批量写
  → TD 确认后推进 PG 批次/覆盖元数据（设计 §4.3：元数据成功在 TD 确认后）；
- 冲突语义：同点同 ts 同 payload=幂等跳过；不同 payload=登记
  ``history_point_conflict`` 并保留既有事实（默认 skip，不按到达顺序覆盖）；
- 读路径：按点集合批量读窗口事件 + 窗口前最后**状态事件** + 适用锚点，
  SQL 次数有界（不逐点/逐秒查询）。

时间口径：对外接口统一 aware UTC datetime；内部 SQL 输出带 Z 的 UTC 串
（与 tdengine_provider._format_ts 同口径）；ts 毫秒精度保留，不截秒。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.tdengine_native import execute_native

logger = logging.getLogger(__name__)

#: 测点稳定表名（v1 契约，设计 §4.1）
POINT_STABLE = "st_point_data_v1"

#: 质量三态（与 tests/refactor/reference_data 一致；设计 §4.1）
QC_GOOD = 1
QC_BAD = 0
QC_UNKNOWN = -1

#: 质量原码体系
QSCHEMA_AAS = 1
QSCHEMA_OPCDA = 2

#: 来源类别
SOURCE_KIND_COV = 1
SOURCE_KIND_SNAPSHOT = 2
SOURCE_KIND_HISTORY = 3
SOURCE_KIND_REMOTE_GRID = 4

#: 单条 SELECT 最大行数（有界 SQL；超出由调用方按时间游标分页）
READ_CHUNK_ROWS = 50_000

#: 单分片最大翻页数（整改 G12 防死循环兜底）：
#: 7 天分片 × 200 页 × 5 万行 = 1000 万行/分片，远超单点 7 天 1Hz 的 60 万行上限
_READ_MAX_PAGES_PER_CHUNK = 200
#: 单批 INSERT 最大行数（与 settings.TDENGINE_BATCH_SIZE 解耦的仓储内部上限）
WRITE_CHUNK_ROWS = 500

VALID_SOURCE_KINDS = frozenset(
    {SOURCE_KIND_COV, SOURCE_KIND_SNAPSHOT, SOURCE_KIND_HISTORY, SOURCE_KIND_REMOTE_GRID}
)
VALID_QSCHEMAS = frozenset({QSCHEMA_AAS, QSCHEMA_OPCDA})
VALID_QCLASSES = frozenset({QC_GOOD, QC_BAD, QC_UNKNOWN})


def point_subtable(point_id: str) -> str:
    """点身份 → 子表名 ``p_<uuid 去连字符小写>``（设计 §4.1）.

    只接受校验过的 UUID（36 或 32 字符）；拒绝任何非 UUID 输入，
    杜绝从用户可编辑位号拼接表名。
    """
    normalized = point_id.strip().lower()
    try:
        parsed = _uuid.UUID(normalized)
    except ValueError as exc:
        raise ValueError(f"非法点身份（要求 UUID）: {point_id!r}") from exc
    return "p_" + parsed.hex


def format_ts_utc(dt: datetime) -> str:
    """aware/naive datetime → 带 Z 的 UTC 毫秒串（naive 视为 UTC）."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def compute_payload_hash(
    *,
    point_id: str,
    ts_ms: int,
    value: float | int | None,
    quality_class: int,
    quality_raw: int | None,
) -> str:
    """归一化载荷摘要：sha256(pointIdHex|ts_ms|值|质量类|原码)。

    设计 §4.1：语义去重——同一事实经实时与历史重放**不因来源类别不同**被判冲突，
    因此 source_kind 刻意不参与 hash。

    整改 G11：本函数是 hash 公式的**唯一实现**，实时路径（PointEvent.payload_hash）
    与历史导入批量写点表（data_import._write_points_bulk）共用。此前导入侧把
    payload_hash 硬编码为空串，导致该不变量完全失效：同 ts 的实时事件与导入行
    hash 永不相等 → 被判"同 ts 不同 payload"的冲突 → 按默认 conflict_policy=skip
    **静默丢弃实时值**。

    value 的 NaN/Inf 已由 PointEvent.__post_init__ 归一为 None；调用方若绕过
    PointEvent 直接调用，需自行保证同样的归一化（否则 hash 不一致）。
    """
    parts = "|".join(
        [
            str(_uuid.UUID(point_id).hex),
            str(ts_ms),
            "" if value is None else repr(float(value)),
            str(quality_class),
            "" if quality_raw is None else str(quality_raw),
        ]
    )
    return hashlib.sha256(parts.encode()).hexdigest()


@dataclass(frozen=True)
class PointEvent:
    """一次待写入的测点状态事件（值或质量变化）。"""

    point_id: str
    ts: datetime  # aware UTC（源记录时间，非接收时间）
    value: float | int | None
    quality_class: int
    quality_raw: int | None = None
    quality_schema: int | None = None
    source_kind: int = SOURCE_KIND_COV
    received_at: datetime | None = None  # 缺省=写入时刻（审计用，不作 ts）
    source_id: str = "clpm-realtime"

    def __post_init__(self) -> None:
        if self.quality_class not in VALID_QCLASSES:
            raise ValueError(f"非法 quality_class: {self.quality_class}")
        if self.source_kind not in VALID_SOURCE_KINDS:
            raise ValueError(f"非法 source_kind: {self.source_kind}")
        if self.quality_schema is not None and self.quality_schema not in VALID_QSCHEMAS:
            raise ValueError(f"非法 quality_schema: {self.quality_schema}")
        if self.ts.tzinfo is None:
            raise ValueError("PointEvent.ts 必须为 aware UTC datetime")
        if self.value is not None:
            fv = float(self.value)
            if fv != fv or fv in (float("inf"), float("-inf")):  # NaN/Inf → None
                object.__setattr__(self, "value", None)

    def payload_hash(self) -> str:
        """归一化载荷摘要（见 compute_payload_hash）。"""
        return compute_payload_hash(
            point_id=self.point_id,
            ts_ms=int(self.ts.timestamp() * 1000),
            value=self.value,
            quality_class=self.quality_class,
            quality_raw=self.quality_raw,
        )


@dataclass
class WriteResult:
    """一批写入的结果对账（P2/P4 观测口径）。"""

    inserted: int = 0
    identical_skipped: int = 0
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    failed: bool = False
    error: str | None = None


async def ensure_schema() -> None:
    """幂等建库建表（隔离/开发/生产同代码路径，库名注入）."""
    db = settings.TDENGINE_DB
    await execute_native(f"CREATE DATABASE IF NOT EXISTS {db} KEEP 365 DURATION 10 PRECISION 'ms'")
    await execute_native(
        f"CREATE STABLE IF NOT EXISTS {db}.{POINT_STABLE} ("
        "ts TIMESTAMP, `value` DOUBLE, quality_raw INT, quality_class TINYINT, "
        "quality_schema TINYINT, received_at TIMESTAMP, source_kind TINYINT, "
        "payload_hash BINARY(64)) TAGS (point_id BINARY(36), source_id BINARY(64))"
    )


def _sql_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _value_literal(value: float | int | None) -> str:
    if value is None:
        return "NULL"
    return repr(float(value))


def _build_insert_sql(rows: list[tuple[PointEvent, str]]) -> str:
    """多子表 INSERT（一条 SQL 多子表多行；USING 自动建子表）."""
    db = settings.TDENGINE_DB
    parts = ["INSERT INTO"]
    for ev, payload_hash in rows:
        table = f"{db}.{point_subtable(ev.point_id)}"
        parts.append(
            f"{table} USING {POINT_STABLE} TAGS ('{_sql_quote(ev.point_id)}', "
            f"'{_sql_quote(ev.source_id)}') VALUES "
            f"('{format_ts_utc(ev.ts)}', {_value_literal(ev.value)}, "
            f"{ev.quality_raw if ev.quality_raw is not None else 'NULL'}, {ev.quality_class}, "
            f"{ev.quality_schema if ev.quality_schema is not None else 'NULL'}, "
            f"'{format_ts_utc(ev.received_at or datetime.now(UTC))}', {ev.source_kind}, "
            f"'{_sql_quote(payload_hash)}')"
        )
    return " ".join(parts)


#: 读列集：builder 热路径只需 3 列（响应体量 ↓60%+）；对账/审计用全列
_EVENT_COLUMNS_MIN = "point_id, ts, `value`, quality_class"
_EVENT_COLUMNS_FULL = (
    "point_id, ts, `value`, quality_class, quality_raw, quality_schema, source_kind, payload_hash"
)


async def read_events(
    point_ids: list[str],
    start: datetime,
    end: datetime,
    *,
    include_end: bool = True,
    full_columns: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """批量读取窗口内事件（半开/闭由 include_end 控制；默认闭=兼容外部契约）.

    Args:
        full_columns: True=全列（冲突比对/审计）；False=最小列集
            （ts/value/quality_class——builder 热路径，减小响应体量）。

    Returns:
        {point_id: [{ts: aware-UTC datetime, value, quality_class, quality_raw,
                     quality_schema, source_kind, payload_hash}, ...]}（ts 升序；
            最小列集下 raw/schema/kind/hash 为 None）
    """
    if not point_ids:
        return {}
    end_op = "<=" if include_end else "<"
    columns = _EVENT_COLUMNS_FULL if full_columns else _EVENT_COLUMNS_MIN
    results: dict[str, list[dict[str, Any]]] = {pid: [] for pid in point_ids}
    tags = ", ".join(f"'{_sql_quote(pid)}'" for pid in point_ids)
    for chunk_start, chunk_end, chunk_inclusive in _time_chunks(start, end):
        # 整改 G12：片内按 ts 游标翻页。
        # 此前每片只发一条带 LIMIT READ_CHUNK_ROWS 的 SQL，返回行数等于上限时
        # 尾部被**静默丢弃**（ORDER BY ts ASC 丢的正是后半段），KPI 会基于只有
        # 前半段的"看似合理"数据得出结论——最伤可信度的一类缺陷。
        # 现改为：满批则以最后一条 ts 为游标推进，直到某批不足上限。
        chunk_op = end_op if chunk_inclusive else "<"
        cursor = chunk_start
        cursor_inclusive = True
        for _page in range(_READ_MAX_PAGES_PER_CHUNK):
            lower_op = ">=" if cursor_inclusive else ">"
            sql = (
                f"SELECT {columns} FROM {settings.TDENGINE_DB}.{POINT_STABLE} "
                f"WHERE point_id IN ({tags}) "
                f"AND ts {lower_op} '{format_ts_utc(cursor)}' "
                f"AND ts {chunk_op} '{format_ts_utc(chunk_end)}' "
                f"ORDER BY ts ASC LIMIT {READ_CHUNK_ROWS}"
            )
            rows = await execute_native(sql)
            for row in rows:
                pid = row.get("point_id")
                if pid in results:
                    results[pid].append(_row_to_event(row))
            if len(rows) < READ_CHUNK_ROWS:
                break
            last_ts = rows[-1].get("ts")
            if last_ts is None or last_ts == cursor:
                # 游标无法推进（ts 解析失败或全为同一时刻）→ 继续翻页会死循环。
                # 宁可显式失败也不静默返回不完整结果。
                raise RuntimeError(
                    "read_events 分页游标无法推进："
                    f"chunk=[{chunk_start}, {chunk_end}] last_ts={last_ts!r}"
                )
            cursor = last_ts
            cursor_inclusive = False
        else:
            raise RuntimeError(
                "read_events 单分片翻页超过上限 "
                f"({_READ_MAX_PAGES_PER_CHUNK} 页 × {READ_CHUNK_ROWS} 行)："
                f"chunk=[{chunk_start}, {chunk_end}]，窗口过大，请缩小查询范围"
            )
    return results


async def read_last_states_before(
    point_ids: list[str],
    before: datetime,
) -> dict[str, dict[str, Any] | None]:
    """窗口前每个点**最后一个有值的状态事件**（不看未来；含 BAD 值，V04）.

    语义修订（2026-09-19，用户口径"慢变信号前向补齐显示"）：NULL 值行 =
    无样本（空壳网格/无效字面量），**不作为**前向补齐的状态种子——否则
    一条 NULL 行会把此前可补齐的真实旧值顶成 NULL（zpdev 0919 事故：空壳
    导入行致 OP/MODE 趋势大面积空白）。有值但质量 BAD 的行仍是有效样本
    （V04：坏值不被跳过沿用旧 Good）。真实 3.3.6.x 行为由
    tests/integration/test_refactor_point_store.py 验证。

    Returns:
        {point_id: 最后有值状态事件 dict 或 None}
    """
    if not point_ids:
        return {}
    tags = ", ".join(f"'{_sql_quote(pid)}'" for pid in point_ids)
    sql = (
        f"SELECT point_id, LAST_ROW(ts) AS last_ts, LAST_ROW(`value`) AS `value`, "
        f"LAST_ROW(quality_class) AS quality_class, LAST_ROW(quality_raw) AS quality_raw, "
        f"LAST_ROW(quality_schema) AS quality_schema, LAST_ROW(source_kind) AS source_kind, "
        f"LAST_ROW(payload_hash) AS payload_hash "
        f"FROM {settings.TDENGINE_DB}.{POINT_STABLE} "
        f"WHERE point_id IN ({tags}) AND ts < '{format_ts_utc(before)}' "
        f"AND `value` IS NOT NULL "
        f"GROUP BY point_id"
    )
    rows = await execute_native(sql)
    results: dict[str, dict[str, Any] | None] = dict.fromkeys(point_ids)
    for row in rows:
        pid = row.get("point_id")
        if pid in results and row.get("last_ts") is not None:
            results[pid] = {
                "ts": _parse_td_ts(row.get("last_ts")),
                "value": row.get("value"),
                "quality_class": row.get("quality_class"),
                "quality_raw": row.get("quality_raw"),
                "quality_schema": row.get("quality_schema"),
                "source_kind": row.get("source_kind"),
                "payload_hash": row.get("payload_hash"),
            }
    return results


async def read_trend_buckets(
    point_ids: list[str],
    start: datetime,
    end: datetime,
    interval_s: int,
) -> dict[str, dict[int, tuple[float | None, int]]]:
    """显示趋势专用的服务端降采样读取（INTERVAL + FILL(PREV)）.

    每点位号按采样窗聚合（窗内 LAST 值/质量），空窗 FILL(PREV) 用前一
    有效值前向补齐——与趋势显示语义（慢变信号前向补齐，0920 用户口径）
    一致。首个数据点之前无前值 → 该窗不产生数据（由调用方按缺处理）。

    Args:
        point_ids: 位号注册 ID（= 点身份 ID）列表
        start/end: 半开时间窗 [start, end)
        interval_s: 采样窗（秒），决定聚合桶宽

    Returns:
        {point_id: {bucket_start_ms: (value, quality_class)}}
    """
    if not point_ids or end <= start:
        return {}
    tags = ", ".join(f"'{_sql_quote(pid)}'" for pid in point_ids)
    interval_ms = max(1, int(interval_s)) * 1000
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    sql = (
        f"SELECT point_id, _wstart, LAST(`value`) AS `value`, "
        f"LAST(quality_class) AS quality_class "
        f"FROM {settings.TDENGINE_DB}.{POINT_STABLE} "
        f"WHERE point_id IN ({tags}) AND ts >= {start_ms} AND ts < {end_ms} "
        f"PARTITION BY point_id INTERVAL({interval_ms}a) FILL(PREV)"
    )
    rows = await execute_native(sql)
    out: dict[str, dict[int, tuple[float | None, int]]] = {pid: {} for pid in point_ids}
    for row in rows:
        pid = row.get("point_id")
        ws = _parse_td_ts(row.get("_wstart"))
        if pid is None or pid not in out or ws is None:
            continue
        v = row.get("value")
        q = row.get("quality_class")
        if v is None and q is None:
            continue  # FILL 无前值的首段：无数据
        out[pid][int(ws.timestamp() * 1000)] = (
            v,
            int(q) if q is not None else -1,
        )
    return out


def _parse_td_ts(ts_val: Any) -> datetime | None:
    """TDengine 返回时间戳 → aware UTC datetime."""
    if isinstance(ts_val, datetime):
        return ts_val.astimezone(UTC) if ts_val.tzinfo else ts_val.replace(tzinfo=UTC)
    if isinstance(ts_val, str):
        try:
            dt = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


def _row_to_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": _parse_td_ts(row.get("ts")),
        "value": row.get("value"),
        "quality_class": row.get("quality_class"),
        "quality_raw": row.get("quality_raw"),
        "quality_schema": row.get("quality_schema"),
        "source_kind": row.get("source_kind"),
        "payload_hash": row.get("payload_hash"),
    }


def _time_chunks(start: datetime, end: datetime) -> list[tuple[datetime, datetime, bool]]:
    """读窗口按自然日分片（控制单次结果集；与宽表大窗分片同风格）."""
    if end <= start:
        return [(start, end, True)]
    chunks: list[tuple[datetime, datetime, bool]] = []
    cursor = start
    max_days = 7
    while True:
        nxt = cursor + timedelta(days=max_days)
        if nxt >= end:
            chunks.append((cursor, end, True))
            break
        chunks.append((cursor, nxt, False))
        cursor = nxt
    return chunks


async def write_events(
    events: list[PointEvent],
    *,
    conflict_policy: str = "skip",  # skip | overwrite_authorized
    source_task: str | None = None,
    db_session: Any | None = None,
) -> WriteResult:
    """写入一批点事件（幂等/冲突分流 + 分块写入 + 有界重试）.

    流程（设计 §4.3）：
    1. 批内自合并：同 (point, ts) 多事件按到达序保留最后一条（同 tick 多事件
       的中间态由 P2 的队列保证不在此丢失——本层合并仅针对重复重放）；
    2. 读回已存事实（窗口=批内 ts 范围，1 次 SQL），逐条比对 payload_hash：
       - 相同 → identical_skipped（幂等）；
       - 不同 → 按 conflict_policy：skip=登记冲突保留既有；
         overwrite_authorized=覆盖并登记 resolved_overwrite；
    3. 新事实分块写 TD（≤WRITE_CHUNK_ROWS 行/SQL，失败重试 3 次后置 failed）。
    """
    result = WriteResult()
    if not events:
        return result

    # 1) 批内归并（同 point+ts 保留最后到达）
    merged: dict[tuple[str, int], PointEvent] = {}
    for ev in events:
        key = (ev.point_id, int(ev.ts.timestamp() * 1000))
        merged[key] = ev
    ordered = sorted(merged.values(), key=lambda e: (e.point_id, e.ts))

    # 2) 已存事实比对（批内 ts 范围一次宽读）
    point_ids = sorted({ev.point_id for ev in ordered})
    ts_min = min(ev.ts for ev in ordered)
    all_ts = max(ev.ts for ev in ordered)
    existing_full = await read_events(point_ids, ts_min, all_ts)
    existing_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for pid, rows in existing_full.items():
        for row in rows:
            if row["ts"] is not None:
                existing_by_key[(pid, int(row["ts"].timestamp() * 1000))] = row

    to_insert: list[tuple[PointEvent, str]] = []
    for ev in ordered:
        key = (ev.point_id, int(ev.ts.timestamp() * 1000))
        ph = ev.payload_hash()
        prior = existing_by_key.get(key)
        if prior is None:
            to_insert.append((ev, ph))
            continue
        prior_hash = prior.get("payload_hash")
        if prior_hash == ph:
            result.identical_skipped += 1
            continue
        if not prior_hash:
            # 整改 G11：既有行的 payload_hash 为空（历史遗留的导入行硬编码 ''），
            # 不构成"与本次载荷不同"的证据。此时若按冲突 skip，实时值会被静默
            # 丢弃且无任何可见信号——宁可覆盖（实时值是更权威的当前状态）。
            to_insert.append((ev, ph))
            result.conflicts.append(
                {"point_id": ev.point_id, "ts_ms": key[1], "resolution": "overwrite_no_hash"}
            )
            continue
        # 同 ts 不同 payload
        if conflict_policy == "overwrite_authorized":
            to_insert.append((ev, ph))
            result.conflicts.append(
                {"point_id": ev.point_id, "ts_ms": key[1], "resolution": "overwrite"}
            )
        else:
            result.conflicts.append(
                {
                    "point_id": ev.point_id,
                    "ts_ms": key[1],
                    "resolution": "skip",
                    "existing": prior,
                    "incoming": {
                        "value": ev.value,
                        "quality_class": ev.quality_class,
                        "quality_raw": ev.quality_raw,
                    },
                }
            )

    # 3) 分块写入（≤WRITE_CHUNK_ROWS 行/SQL；重试后仍失败→failed，不谎报成功）
    for i in range(0, len(to_insert), WRITE_CHUNK_ROWS):
        chunk = to_insert[i : i + WRITE_CHUNK_ROWS]
        ok = False
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                sql = _build_insert_sql(chunk)
                await execute_native(sql)
                ok = True
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
        if ok:
            result.inserted += len(chunk)
        else:
            result.failed = True
            result.error = str(last_exc)
            logger.error("点历史写入失败（chunk %d 行）: %s", len(chunk), last_exc)
            break

    # 冲突登记（PG 侧；db_session 缺省自建，失败不回滚 TD 事实——冲突登记可重试）
    if result.conflicts and db_session is not None:
        from app.services.data_source.point_history_metadata import register_conflicts

        try:
            await register_conflicts(db_session, result.conflicts, source_task=source_task)
        except Exception as exc:  # noqa: BLE001
            logger.warning("冲突登记失败（TD 事实不受影响，可重试）: %s", exc)
    return result


def build_payload_hash(ev: PointEvent) -> str:
    """公开的 hash 计算（测试/对账用）。"""
    return ev.payload_hash()
