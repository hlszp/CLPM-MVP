"""统一事件总线 — 跨模块事件归一入口（service 层）。

所有业务状态变更（告警/诊断/整定/处置/系统/趋势/模块）经 ``publish()`` 归一
写入 ``event_bus`` 表，并触发铃铛 WS 推送。

设计要点：
- ``publish()`` 为 async（DB 写入需 async session）；只 ``flush`` 不 ``commit``，
  事务由调用方（API 端点 / Celery task）控制——若调用方后续失败，事件也不残留。
- 双阶段：① INSERT event_bus ② WS broadcast（M1 存根仅日志，不阻塞主流程；
  M2 接入真实 WS hub ``/api/v1/ws/bell``）。
- ``read_by_users`` 初始化为 ``[]``，未读计数由 ``count_unread()`` 查询时
  ``NOT read_by_users @> to_jsonb(uid)`` 计算。
- 调用方均在 async 上下文（API 端点 / Celery AsyncTask）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_bus import EventBus

logger = logging.getLogger(__name__)


async def publish(
    db: AsyncSession,
    *,
    source_module: str,
    event_type: str,
    severity: str,
    title: str,
    occurred_at: datetime | None = None,
    scope_type: str | None = None,
    scope_id: int | None = None,
    loop_id: str | None = None,
    order_id: str | None = None,
    record_id: str | None = None,
    tag_id: str | None = None,
    alert_event_id: str | None = None,
    body: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> EventBus:
    """发布事件到 event_bus + 触发铃铛 WS 推送（存根）。

    Parameters
    ----------
    db:
        外部 async session（调用方控制 commit/rollback）。
    source_module:
        事件来源模块（monitor/assess/diagnosis/tuning/handling/alert/system）。
    event_type:
        事件类型（见 ``EventBus.EVENT_TYPES``）。
    severity:
        严重级别（INFO/WARN/ERROR/CRITICAL）。
    title:
        事件标题（≤200 字符，铃铛 Toast 摘要用）。
    occurred_at:
        事件发生时间；默认当前 UTC 时间。
    metadata:
        扩展元数据（如 ``{sla_level, disposition, reopen_count}``）。

    Returns
    -------
    EventBus
        已 flush 的 ORM 实例（``id`` 可用，但需调用方 commit 后才持久化）。
    """
    event = EventBus(
        source_module=source_module,
        event_type=event_type,
        severity=severity,
        title=title,
        occurred_at=occurred_at or datetime.now(UTC),
        scope_type=scope_type,
        scope_id=scope_id,
        loop_id=loop_id,
        order_id=order_id,
        record_id=record_id,
        tag_id=tag_id,
        alert_event_id=alert_event_id,
        body=body,
        ext_metadata=metadata or {},
        read_by_users=[],
    )
    db.add(event)
    await db.flush()  # 让 event.id 可用（不 commit，由调用方控制事务）

    # 阶段 ②：WS 推送（M1 存根：仅日志；M2 接入真实 WS hub）
    try:
        await _ws_broadcast(event)
    except Exception:
        logger.exception(
            "EventBus WS 推送失败（存根阶段，忽略不阻塞主流程）: event_id=%s type=%s",
            event.id,
            event.event_type,
        )
    return event


async def mark_read(db: AsyncSession, event_ids: list[int], user_id: int) -> int:
    """批量标记已读：将 ``user_id`` 追加到 ``read_by_users``（去重）。

    使用 JSONB ``||`` 拼接 + ``@>`` 包含检查，单条 UPDATE 批量完成。
    返回实际更新的行数（已读的不会重复更新）。
    """
    if not event_ids:
        return 0
    stmt = text(
        """
        UPDATE event_bus
        SET read_by_users = read_by_users || to_jsonb(:uid)::jsonb
        WHERE id IN :ids
          AND NOT read_by_users @> to_jsonb(:uid)::jsonb
        """
    ).bindparams(
        bindparam("uid", user_id),
        bindparam("ids", list(event_ids), expanding=True),
    )
    result = await db.execute(stmt)
    return result.rowcount or 0


async def count_unread(db: AsyncSession, user_id: int) -> int:
    """统计用户未读事件数（``read_by_users`` 不含 ``user_id``）。"""
    result = await db.execute(
        text("SELECT COUNT(*) FROM event_bus WHERE NOT read_by_users @> to_jsonb(:uid)::jsonb"),
        {"uid": user_id},
    )
    return result.scalar_one()


async def _ws_broadcast(event: EventBus) -> None:
    """铃铛 WS 推送（存根：M1 仅日志；M2 接入 ``/api/v1/ws/bell`` hub）。

    M2 替换为真实 WS broadcast 调用（如 ``await ws_manager.broadcast_bell(event)``）。
    存根阶段记录日志，便于联调时确认事件流。
    """
    logger.info(
        "EventBus WS 推送（存根）: event_id=%s module=%s type=%s severity=%s title=%s",
        event.id,
        event.source_module,
        event.event_type,
        event.severity,
        event.title,
    )
