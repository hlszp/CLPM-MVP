"""Loop batch operations services (配置增强).

提供：
- ``batch_update_loops``: 批量更新回路（监控/统计/重要等级/参评 + P2-04
  工程属性：控制作用方向/OP 限位/DCS 型号/工程模板引用），支持 ``dry_run``
  预览（逐回路差异 / 部分失败状态 / 受影响清单，零写入）
- ``batch_delete_loops``: 批量硬删除回路（解绑 Tag 映射 + 级联清理关联数据）

所有写操作均记录审计日志。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.dcs_model import DcsModel
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.tag import TagRegistry

logger = logging.getLogger(__name__)

# 允许批量更新的字段白名单（v5.3：level → importance_level；
# P2-04：工程属性批次扩入——控制作用方向/OP 限位/DCS 型号/工程模板引用）
_BATCH_UPDATABLE_FIELDS = {
    "is_monitored",
    "is_stat_enabled",
    "importance_level",
    "include_in_evaluation",
    "control_action_direction",
    "op_output_lower_limit",
    "op_output_upper_limit",
    "dcs_model_id",
    "engineering_template_id",
    "engineering_template_version",
}


async def _write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    target_type: str,
    target_id: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    """写入审计日志。"""
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        before_value=before_value,
        after_value=after_value,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(log)


def _make_field_before_getter(loop):
    """构造回路的批量字段取值器（差异计算用；显式传 loop 避免闭包晚绑定）."""

    def _field_before(field: str):
        mapping = {
            "is_monitored": loop.is_active,
            "is_stat_enabled": loop.is_active,
            "importance_level": loop.importance_level,
            "include_in_evaluation": loop.include_in_evaluation,
            "control_action_direction": getattr(loop, "control_action_direction", "UNKNOWN"),
            "op_output_lower_limit": loop.op_output_lower_limit,
            "op_output_upper_limit": loop.op_output_upper_limit,
            "dcs_model_id": str(loop.dcs_model_id) if loop.dcs_model_id else None,
            "engineering_template_id": (
                str(loop.engineering_template_id) if loop.engineering_template_id else None
            ),
            "engineering_template_version": loop.engineering_template_version,
        }
        return mapping[field]

    return _field_before


# ---------------------------------------------------------------------------
# SVC-09: 批量更新回路
# ---------------------------------------------------------------------------


async def batch_update_loops(
    db: AsyncSession,
    loop_ids: list[str],
    updates: dict,
    operator: str,
    *,
    dry_run: bool = False,
    commit: bool = True,
    allow_null_engineering: bool = False,
) -> dict:
    """批量更新回路配置（支持 dry-run 预览：差异/部分失败状态/受影响清单）.

    Args:
        db: 异步数据库会话
        loop_ids: 回路 ID 列表
        updates: 更新字段字典，支持：
            - is_monitored: bool — 是否监控（写入 is_active 字段，True=监控/启用）
            - is_stat_enabled: bool — 是否纳入统计（复用 is_active 语义）
            - importance_level: int — 回路重要等级 1/2/3
            - include_in_evaluation: bool — 是否参与评估
            - P2-04 工程属性：control_action_direction（DIRECT/REVERSE/UNKNOWN）、
              op_output_lower_limit / op_output_upper_limit、dcs_model_id、
              engineering_template_id、engineering_template_version
        operator: 操作人
        dry_run: True=只预览不写入（不审计、不 commit、不触发停用联动与
            缓存失效——"读取与发布能力分开"，03 §5 P2-04）

    Returns:
        ``{"affected": int, "dryRun": bool, "results": [
            {"loopId", "tagName", "status": "OK"|"UNCHANGED"|"FAIL"|"NOT_FOUND",
             "changes": [{"field", "before", "after"}], "error"}]}``

    部分失败语义：全局非法（空列表/白名单外字段/全局枚举值）仍 422 原子
    拒绝；回路级失败（OP 限位与该回路量程冲突、模板/型号不存在等）逐回路
    报告为 FAIL 并跳过，其余回路正常应用——批量结果可追溯。

    Raises:
        BizError: ERR_BATCH_EMPTY / ERR_BATCH_INVALID_FIELD（全局校验）
    """
    if not loop_ids:
        raise BizError(
            code="ERR_BATCH_EMPTY",
            message="批量更新回路列表不能为空",
            status_code=422,
        )

    # 校验 updates 字段白名单
    invalid_fields = set(updates.keys()) - _BATCH_UPDATABLE_FIELDS
    if invalid_fields:
        raise BizError(
            code="ERR_BATCH_INVALID_FIELD",
            message=f"批量更新不支持的字段: {','.join(sorted(invalid_fields))}",
            status_code=422,
        )

    # 校验 importance_level 取值（全局）
    if "importance_level" in updates and updates["importance_level"] is not None:
        level = updates["importance_level"]
        if level not in (1, 2, 3):
            raise BizError(
                code="ERR_BATCH_INVALID_FIELD",
                message=f"importance_level 必须为 1/2/3，当前为 {level}",
                status_code=422,
            )

    from app.services.engineering_facts import VALID_CONTROL_ACTION_DIRECTIONS

    if (
        "control_action_direction" in updates
        and updates["control_action_direction"] is not None
        and updates["control_action_direction"] not in VALID_CONTROL_ACTION_DIRECTIONS
    ):
        raise BizError(
            code="ERR_BATCH_INVALID_FIELD",
            message=(
                f"control_action_direction 必须为 {VALID_CONTROL_ACTION_DIRECTIONS} 之一，"
                f"当前为 {updates['control_action_direction']}"
            ),
            status_code=422,
        )

    # 全局预检：dcs_model / engineering_template 存在性（不存在 → 422 全批拒绝，
    # 与 dcs_model 同口径；404 资源语义保留给单资源 CRUD 路径）。存在则模板
    # 版本逐回路校验生效值。
    if updates.get("dcs_model_id") is not None:
        model = (
            await db.execute(select(DcsModel).where(DcsModel.id == str(updates["dcs_model_id"])))
        ).scalar_one_or_none()
        if model is None:
            raise BizError(
                code="ERR_DCS_MODEL_NOT_FOUND",
                message=f"DCS 型号不存在: {updates['dcs_model_id']}",
                status_code=422,
            )
    if updates.get("engineering_template_id") is not None:
        from app.services.engineering_template import validate_loop_template_ref

        try:
            await validate_loop_template_ref(
                db,
                template_id=str(updates["engineering_template_id"]),
                version=updates.get("engineering_template_version"),
            )
        except BizError as exc:
            # REWORK：批量全局预检统一 422（全批原子拒绝口径与 dcs_model 一致）
            raise BizError(
                code=exc.code,
                message=f"批量更新全局预检失败：{exc.message}",
                status_code=422,
            ) from exc
    elif updates.get("engineering_template_version") is not None:
        raise BizError(
            code="ERR_BATCH_INVALID_FIELD",
            message="固定引用模板版本时必须同时指定 engineering_template_id",
            status_code=422,
        )

    # 查询待更新回路
    result = await db.execute(select(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
    loops = result.scalars().all()
    found_ids = {str(row.id) for row in loops}

    results: list[dict] = []
    for lid in loop_ids:
        if str(lid) not in found_ids:
            results.append(
                {
                    "loopId": str(lid),
                    "tagName": None,
                    "status": "NOT_FOUND",
                    "changes": [],
                    "error": "回路不存在",
                }
            )

    # 回路级 OP 量程缓存（限位校验基准；同批多回路共享查询结果）
    _op_range_cache: dict[str, tuple[float | None, float | None]] = {}

    async def _op_tag_range(loop_id: str) -> tuple[float | None, float | None]:
        if loop_id not in _op_range_cache:
            row = (
                await db.execute(
                    select(TagRegistry.range_min, TagRegistry.range_max)
                    .join(LoopTagMapping, LoopTagMapping.tag_id == TagRegistry.id)
                    .where(LoopTagMapping.loop_id == loop_id, LoopTagMapping.tag_role == "OP")
                )
            ).first()
            _op_range_cache[loop_id] = (
                (
                    float(row[0]) if row[0] is not None else None,
                    float(row[1]) if row[1] is not None else None,
                )
                if row is not None
                else (None, None)
            )
        return _op_range_cache[loop_id]

    from app.services.loop import _validate_op_output_limits, derive_loop_status

    applied: list[LoopLedger] = []
    disabled_loop_ids: list[str] = []
    for loop in loops:
        entry: dict = {
            "loopId": str(loop.id),
            "tagName": loop.tag_name,
            "status": "OK",
            "changes": [],
            "error": None,
        }
        error: str | None = None

        # 回路级校验（生效值 = 更新值优先，否则保持原值）
        eff_lower = (
            updates["op_output_lower_limit"]
            if "op_output_lower_limit" in updates
            and (allow_null_engineering or updates["op_output_lower_limit"] is not None)
            else loop.op_output_lower_limit
        )
        eff_upper = (
            updates["op_output_upper_limit"]
            if "op_output_upper_limit" in updates
            and (allow_null_engineering or updates["op_output_upper_limit"] is not None)
            else loop.op_output_upper_limit
        )
        if "op_output_lower_limit" in updates or "op_output_upper_limit" in updates:
            range_min, range_max = await _op_tag_range(str(loop.id))
            try:
                _validate_op_output_limits(
                    lower=eff_lower, upper=eff_upper, op_range_min=range_min, op_range_max=range_max
                )
            except BizError as exc:
                error = exc.message

        # 差异计算（dry-run 预览与非 dry-run 审计共用；显式传 loop 避免闭包晚绑定）
        _field_before = _make_field_before_getter(loop)

        for field, value in updates.items():
            if value is None and not (
                allow_null_engineering
                and field
                in {
                    "op_output_lower_limit",
                    "op_output_upper_limit",
                    "dcs_model_id",
                    "engineering_template_id",
                    "engineering_template_version",
                }
            ):
                continue
            if field in ("is_monitored", "is_stat_enabled"):
                # 二者均写 is_active（P1 #10 互斥由 Schema 层保证）
                before_v, after_v = bool(loop.is_active), bool(value)
            elif field == "include_in_evaluation":
                before_v, after_v = bool(loop.include_in_evaluation), bool(value)
            else:
                before_v = _field_before(field)
                after_v = value
            if before_v != after_v:
                entry["changes"].append({"field": field, "before": before_v, "after": after_v})

        if error is not None:
            entry["status"] = "FAIL"
            entry["error"] = error
            results.append(entry)
            continue
        if not entry["changes"]:
            entry["status"] = "UNCHANGED"
            results.append(entry)
            continue

        if dry_run:
            results.append(entry)
            continue

        # 应用更新（写入路径）
        before = {
            "loopId": str(loop.id),
            "tagName": loop.tag_name,
            "is_active": loop.is_active,
            "importance_level": loop.importance_level,
            "include_in_evaluation": loop.include_in_evaluation,
        }
        was_active = loop.is_active

        if "is_monitored" in updates and updates["is_monitored"] is not None:
            loop.is_active = bool(updates["is_monitored"])
        if "is_stat_enabled" in updates and updates["is_stat_enabled"] is not None:
            loop.is_active = bool(updates["is_stat_enabled"])
        if "importance_level" in updates and updates["importance_level"] is not None:
            loop.importance_level = updates["importance_level"]
        if "include_in_evaluation" in updates and updates["include_in_evaluation"] is not None:
            loop.include_in_evaluation = bool(updates["include_in_evaluation"])
        # P2-04 工程属性
        if updates.get("control_action_direction") is not None:
            loop.control_action_direction = updates["control_action_direction"]
        if "op_output_lower_limit" in updates and (
            allow_null_engineering or updates["op_output_lower_limit"] is not None
        ):
            loop.op_output_lower_limit = updates["op_output_lower_limit"]
        if "op_output_upper_limit" in updates and (
            allow_null_engineering or updates["op_output_upper_limit"] is not None
        ):
            loop.op_output_upper_limit = updates["op_output_upper_limit"]
        if "dcs_model_id" in updates and (
            allow_null_engineering or updates["dcs_model_id"] is not None
        ):
            loop.dcs_model_id = updates["dcs_model_id"]
        if "engineering_template_id" in updates:
            loop.engineering_template_id = updates["engineering_template_id"]
        if "engineering_template_version" in updates:
            loop.engineering_template_version = updates["engineering_template_version"]
        # 停用联动（2026-10-10 裁决，与 update_loop 同口径）
        if was_active and not loop.is_active:
            if loop.include_in_evaluation:
                loop.include_in_evaluation = False
            disabled_loop_ids.append(str(loop.id))
        # 整改 0928：is_active 变化后重算派生状态
        if "is_monitored" in updates or "is_stat_enabled" in updates:
            loop.status = await derive_loop_status(db, loop)
        loop.updated_by = operator

        after = {
            "loopId": str(loop.id),
            "tagName": loop.tag_name,
            "is_active": loop.is_active,
            "importance_level": loop.importance_level,
            "include_in_evaluation": loop.include_in_evaluation,
        }
        await _write_audit(
            db=db,
            operator=operator,
            operation_type="LOOP_BATCH_UPDATE",
            target_type="loop_ledger",
            target_id=str(loop.id),
            before_value=json.dumps(before, ensure_ascii=False, default=str),
            after_value=json.dumps(after, ensure_ascii=False, default=str),
        )
        applied.append(loop)
        results.append(entry)

    if dry_run:
        would_update = sum(1 for r in results if r["status"] == "OK")
        would_fail = sum(1 for r in results if r["status"] in ("FAIL", "NOT_FOUND"))
        logger.info(
            "[批量更新 dry-run] 总数=%d 将更新=%d 将失败=%d（操作人: %s, 字段: %s）",
            len(loop_ids),
            would_update,
            would_fail,
            operator,
            list(updates.keys()),
        )
        return {"affected": would_update, "dryRun": True, "results": results}

    # 停用联动：批量关闭本次停用回路的全部 ACTIVE 预警（同事务，随 commit 生效）
    if disabled_loop_ids:
        from app.services.alert_rule_engine.service import acknowledge_loop_active_events

        closed = await acknowledge_loop_active_events(
            db, disabled_loop_ids, operator="system:loop-disabled", note="回路停用自动关闭"
        )
        if closed:
            logger.info(
                "[批量更新] 停用联动关闭 ACTIVE 预警: loops=%d count=%d",
                len(disabled_loop_ids),
                closed,
            )
    # 快照批次在调用方把操作前/后状态与业务变更放进同一事务后提交。
    if commit:
        await db.commit()

    # 2026-10-10 参评口径统一：批量停用/启用/参评切换改变聚合分母 → 即时
    # 失效驾驶舱总览/趋势缓存（进程 + Redis 双端）。
    if any(
        k in updates and updates[k] is not None
        for k in ("is_monitored", "is_stat_enabled", "include_in_evaluation")
    ):
        from app.services.agg_cache import invalidate_agg_async

        await invalidate_agg_async("cockpit-overview")
        await invalidate_agg_async("board-trend")

    logger.info(
        "[批量更新] 已更新 %d 个回路（操作人: %s, 字段: %s）",
        len(applied),
        operator,
        list(updates.keys()),
    )

    return {"affected": len(applied), "dryRun": False, "results": results}


# ---------------------------------------------------------------------------
# SVC-09: 批量硬删除回路
# ---------------------------------------------------------------------------


async def batch_delete_loops(
    db: AsyncSession,
    loop_ids: list[str],
    operator: str,
) -> dict:
    """批量硬删除回路（与单个删除口径一致，不可恢复）。

    每个回路：
    1. 删除 LoopTagMapping 关联记录（通常 7 条：PV/SP/OP/MODE/PID_P/PID_I/PID_D），
       解除关联后不再被任何回路引用的 Tag 其 is_linked 一并清除；
    2. 硬删除回路本体，ON DELETE CASCADE 自动级联清理 kpi_snapshot /
       loop_confidence_latest / diagnosis_run / action_tracker / tuning_record /
       alert_event 等 18 张关联表；
    3. 写审计日志。

    单事务提交（任一回路失败全部回滚）。

    Args:
        db: 异步数据库会话
        loop_ids: 回路 ID 列表
        operator: 操作人

    Returns:
        {"deleted": 硬删除数量, "skipped": [{"loopId", "reason"}]}
        skipped 为请求中但未找到的回路 ID

    Raises:
        BizError: ERR_BATCH_EMPTY (loop_ids 为空)
    """
    if not loop_ids:
        raise BizError(
            code="ERR_BATCH_EMPTY",
            message="批量删除回路列表不能为空",
            status_code=422,
        )

    result = await db.execute(select(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
    loops = result.scalars().all()

    found_ids = {str(loop.id) for loop in loops}
    skipped = [{"loopId": lid, "reason": "回路不存在"} for lid in loop_ids if lid not in found_ids]

    if not loops:
        return {"deleted": 0, "skipped": skipped}

    for loop in loops:
        loop_id = str(loop.id)
        before = {
            "loopId": loop_id,
            "tagName": loop.tag_name,
            "is_active": loop.is_active,
            "status": loop.status,
        }

        # 1. 级联解绑：删除本回路的 Tag 映射
        mappings_result = await db.execute(
            select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id)
        )
        mappings = mappings_result.scalars().all()
        mapped_tag_ids = [str(m.tag_id) for m in mappings]
        if mappings:
            await db.execute(delete(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
            # is_linked 由映射派生：仅当 Tag 不再被任何回路引用时才清除
            for tag_id in mapped_tag_ids:
                ref_count_result = await db.execute(
                    select(func.count())
                    .select_from(LoopTagMapping)
                    .where(LoopTagMapping.tag_id == tag_id)
                )
                if (ref_count_result.scalar() or 0) > 0:
                    continue
                t_result = await db.execute(select(TagRegistry).where(TagRegistry.id == tag_id))
                tag = t_result.scalar_one_or_none()
                if tag:
                    tag.is_linked = False

        # 2. 硬删除回路本体（ON DELETE CASCADE 级联清理关联表）
        await db.delete(loop)

        # 3. 审计
        await _write_audit(
            db=db,
            operator=operator,
            operation_type="LOOP_BATCH_DELETE",
            target_type="loop_ledger",
            target_id=loop_id,
            before_value=json.dumps(before, ensure_ascii=False, default=str),
            after_value=json.dumps(
                {"tagName": loop.tag_name, "deleted": True},
                ensure_ascii=False,
            ),
        )

    await db.commit()

    logger.info(
        "[批量删除] 已硬删除 %d 个回路（操作人: %s）",
        len(loops),
        operator,
    )

    # 级联解绑已清除 is_linked：通知实时订阅 Leader 刷新订阅集合（免重启生效）
    from app.services.data_source.realtime_subscriber import notify_subscription_changed

    await notify_subscription_changed(source="loop-batch-delete")

    return {"deleted": len(loops), "skipped": skipped}


# ---------------------------------------------------------------------------
# SVC-10 位号触发监控（check_node_monitor_trigger）已于 2026-08-20 移除：
# 该功能从未接线（全代码库无调用方），plant_node.monitor_tag_id /
# monitor_trigger_value 字段全 NULL，属死代码 + 死字段。迁移
# e1f2a3b4c5d6 已同步删除两列。若将来需要"按位号值启停节点监控"，
# 按实际需求重新设计。
# ---------------------------------------------------------------------------


__all__ = [
    "batch_delete_loops",
    "batch_update_loops",
]
