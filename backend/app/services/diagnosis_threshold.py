"""诊断阈值差异化覆盖服务（C3 差异化阈值，FDS §5.4.1 + P3-02 模板化与自适应）。

职责：
- 阈值覆盖 CRUD（loop_type/plant/loop 三级 scope）
- 控制类型模板查询
- 变更审计 + 缓存失效
- P3-02：按回路推荐匹配模板（recommend_for_loop）
- P3-02：一键套用模板到回路/装置（apply_template_to_loop）
- P3-02：ic_engineer 仅可微调 loop scope（operator_role 权限校验）
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.diagnosis import DiagnosisConfig, DiagnosisThresholdOverride
from app.models.loop import LoopLedger
from app.models.plant_node import PlantNode

logger = logging.getLogger(__name__)


async def list_overrides(
    db: AsyncSession,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> list[dict]:
    """列出阈值覆盖（可按 scope 筛选）。"""
    stmt = select(DiagnosisThresholdOverride).order_by(
        DiagnosisThresholdOverride.scope_type,
        DiagnosisThresholdOverride.diag_code,
    )
    if scope_type:
        stmt = stmt.where(DiagnosisThresholdOverride.scope_type == scope_type)
    if scope_id:
        stmt = stmt.where(DiagnosisThresholdOverride.scope_id == scope_id)
    result = await db.execute(stmt)
    return [_override_to_dict(o) for o in result.scalars().all()]


async def list_templates(db: AsyncSession) -> list[dict]:
    """列出控制类型模板（loop_type scope 的覆盖）。"""
    result = await db.execute(
        select(DiagnosisThresholdOverride)
        .where(DiagnosisThresholdOverride.scope_type == "loop_type")
        .order_by(DiagnosisThresholdOverride.scope_id, DiagnosisThresholdOverride.diag_code)
    )
    return [_override_to_dict(o) for o in result.scalars().all()]


async def upsert_override(
    db: AsyncSession,
    operator: str,
    *,
    diag_code: str,
    scope_type: str,
    scope_id: str,
    threshold: dict,
    operator_role: str = "ADMIN",
) -> dict:
    """创建或更新阈值覆盖。

    P3-02：ic_engineer 仅可操作 scope_type="loop"（回路级微调），
    loop_type/plant scope 仍需 ADMIN 权限。

    Raises:
        BizError: ERR_INVALID_SCOPE — scope_type 不合法
        BizError: ERR_PERMISSION_DENIED — ic_engineer 越权操作非 loop scope
    """
    valid_scopes = {"loop_type", "plant", "loop"}
    if scope_type not in valid_scopes:
        raise BizError(
            code="ERR_INVALID_SCOPE",
            message=f"scope_type 必须为 {valid_scopes} 之一",
            status_code=422,
        )

    # P3-02：ic_engineer 权限边界校验
    if operator_role == "IC_ENGINEER" and scope_type != "loop":
        raise BizError(
            code="ERR_PERMISSION_DENIED",
            message="ic_engineer 仅可微调回路级（loop scope）阈值，"
            "回路类型模板与装置级覆盖请联系管理员",
            status_code=403,
        )

    # 查找已有覆盖（唯一约束：diag_code + scope_type + scope_id）
    result = await db.execute(
        select(DiagnosisThresholdOverride).where(
            DiagnosisThresholdOverride.diag_code == diag_code,
            DiagnosisThresholdOverride.scope_type == scope_type,
            DiagnosisThresholdOverride.scope_id == scope_id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        override = DiagnosisThresholdOverride(
            id=str(uuid4()),
            diag_code=diag_code,
            scope_type=scope_type,
            scope_id=scope_id,
            threshold=threshold,
            version=1,
            updated_by=operator,
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(override)
        action = "CREATE"
        before_json = None
    else:
        before_json = json.dumps(_override_to_dict(existing), ensure_ascii=False, default=str)
        existing.threshold = threshold
        existing.version = (existing.version or 1) + 1
        existing.updated_by = operator
        existing.updated_at = datetime.now(UTC).replace(tzinfo=None)
        override = existing
        action = "UPDATE"

    after_json = json.dumps(_override_to_dict(override), ensure_ascii=False, default=str)

    # 审计日志
    audit_log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=f"DIAG_THRESHOLD_{action}",
        target_type="diagnosis_threshold_override",
        target_id=str(override.id),
        before_value=before_json,
        after_value=after_json,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(audit_log)
    await db.commit()

    return _override_to_dict(override)


async def delete_override(
    db: AsyncSession,
    override_id: str,
    operator: str,
    *,
    operator_role: str = "ADMIN",
) -> None:
    """删除阈值覆盖。

    P3-02：ic_engineer 仅可删除 scope_type="loop" 的覆盖。

    Raises:
        BizError: ERR_OVERRIDE_NOT_FOUND
        BizError: ERR_PERMISSION_DENIED — ic_engineer 越权删除非 loop scope 覆盖
    """
    result = await db.execute(
        select(DiagnosisThresholdOverride).where(DiagnosisThresholdOverride.id == override_id)
    )
    override = result.scalar_one_or_none()
    if override is None:
        raise BizError(
            code="ERR_OVERRIDE_NOT_FOUND",
            message="阈值覆盖不存在",
            status_code=404,
        )

    # P3-02：ic_engineer 权限边界校验
    if operator_role == "IC_ENGINEER" and override.scope_type != "loop":
        raise BizError(
            code="ERR_PERMISSION_DENIED",
            message="ic_engineer 仅可删除回路级（loop scope）阈值覆盖",
            status_code=403,
        )

    before_json = json.dumps(_override_to_dict(override), ensure_ascii=False, default=str)
    await db.delete(override)

    audit_log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type="DIAG_THRESHOLD_DELETE",
        target_type="diagnosis_threshold_override",
        target_id=override_id,
        before_value=before_json,
        after_value=None,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(audit_log)
    await db.commit()


def _override_to_dict(o: DiagnosisThresholdOverride) -> dict:
    """覆盖对象转字典。"""
    return {
        "overrideId": str(o.id),
        "diagCode": o.diag_code,
        "scopeType": o.scope_type,
        "scopeId": o.scope_id,
        "threshold": o.threshold or {},
        "version": o.version,
        "updatedBy": o.updated_by,
        "updatedAt": o.updated_at.isoformat() if o.updated_at else None,
    }


# ---------------------------------------------------------------------------
# P3-02: 诊断阈值模板化与自适应
# ---------------------------------------------------------------------------


async def recommend_for_loop(db: AsyncSession, loop_id: str) -> dict:
    """按回路推荐阈值模板（P3-02 自适应推荐核心）。

    返回该回路所有 diag_code 的合并阈值视图：
    - globalDefault: 全局默认（DiagnosisConfig.threshold）
    - loopTypeTemplate: 匹配 loop_type 的模板覆盖（若存在）
    - plantOverride: 装置级覆盖（若存在）
    - loopOverride: 回路级覆盖（若存在）
    - effectiveThreshold: 当前生效阈值（按四级优先级合并）
    - scopeChain: 各级覆盖来源链（用于前端展示"为什么是这个阈值"）

    Raises:
        BizError: ERR_LOOP_NOT_FOUND
    """
    # 加载回路
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    loop_type = loop.loop_type or "OTHER"
    unit_id = str(loop.unit_id) if loop.unit_id else None

    # 加载装置名称
    plant_name = None
    if unit_id:
        plant_result = await db.execute(select(PlantNode).where(PlantNode.id == unit_id))
        plant = plant_result.scalar_one_or_none()
        if plant:
            plant_name = plant.name

    # 加载所有诊断配置（全局默认）
    cfg_result = await db.execute(select(DiagnosisConfig))
    configs = {c.diag_code: c for c in cfg_result.scalars().all()}

    # 加载匹配此回路的覆盖（loop_type + plant + loop）
    scope_ids: dict[str, str] = {"loop_type": loop_type, "loop": str(loop.id)}
    if unit_id:
        scope_ids["plant"] = unit_id

    ov_result = await db.execute(select(DiagnosisThresholdOverride))
    all_overrides = list(ov_result.scalars().all())
    matched = [
        o
        for o in all_overrides
        if o.scope_type in scope_ids and scope_ids[o.scope_type] == o.scope_id
    ]

    # 按 diag_code 分组
    by_diag: dict[str, list[DiagnosisThresholdOverride]] = {}
    for o in matched:
        by_diag.setdefault(o.diag_code, []).append(o)

    # 链路展示顺序：从低优先级到高优先级（loop_type → plant → loop），
    # 与生效阈值合并顺序一致，使 scope_chain[-1] 即最高优先级（生效）层。
    priority_map = {"loop_type": 0, "plant": 1, "loop": 2}
    for items in by_diag.values():
        items.sort(key=lambda o: priority_map.get(o.scope_type, 99))

    recommendations = []
    for diag_code, config in configs.items():
        # MANUAL_REVIEW 无阈值，跳过
        if not config.threshold:
            continue

        global_default = dict(config.threshold)
        loop_type_template = None
        plant_override = None
        loop_override = None

        scope_chain = [
            {
                "scopeType": None,
                "scopeId": None,
                "threshold": global_default,
                "isApplied": True,
                "source": "global_default",
            }
        ]

        for o in by_diag.get(diag_code, []):
            threshold = dict(o.threshold or {})
            if o.scope_type == "loop_type":
                loop_type_template = threshold
                scope_chain.append(
                    {
                        "scopeType": "loop_type",
                        "scopeId": o.scope_id,
                        "threshold": threshold,
                        "isApplied": False,
                        "source": "loop_type_template",
                    }
                )
            elif o.scope_type == "plant":
                plant_override = threshold
                scope_chain.append(
                    {
                        "scopeType": "plant",
                        "scopeId": o.scope_id,
                        "threshold": threshold,
                        "isApplied": False,
                        "source": "plant_override",
                    }
                )
            elif o.scope_type == "loop":
                loop_override = threshold
                scope_chain.append(
                    {
                        "scopeType": "loop",
                        "scopeId": o.scope_id,
                        "threshold": threshold,
                        "isApplied": False,
                        "source": "loop_override",
                    }
                )

        # 计算生效阈值（按优先级合并，与引擎 _merge_threshold_overrides 一致）
        effective = dict(global_default)
        if loop_type_template:
            effective.update(loop_type_template)
        if plant_override:
            effective.update(plant_override)
        if loop_override:
            effective.update(loop_override)

        # 标记最高优先级生效层
        if scope_chain:
            # 重置所有 isApplied，只标记最后一个（最高优先级）
            for item in scope_chain:
                item["isApplied"] = False
            scope_chain[-1]["isApplied"] = True

        recommendations.append(
            {
                "diagCode": diag_code,
                "diagName": config.diag_name,
                "globalDefault": global_default,
                "loopTypeTemplate": loop_type_template,
                "plantOverride": plant_override,
                "loopOverride": loop_override,
                "effectiveThreshold": effective,
                "scopeChain": scope_chain,
            }
        )

    return {
        "loopId": str(loop.id),
        "tagName": loop.tag_name,
        "loopType": loop_type,
        "plantId": unit_id,
        "plantName": plant_name,
        "recommendations": recommendations,
    }


async def apply_template_to_loop(
    db: AsyncSession,
    operator: str,
    *,
    loop_id: str,
    diag_code: str,
    target_scope: str = "loop",
    operator_role: str = "ADMIN",
) -> dict:
    """将 loop_type 模板套用到回路/装置（P3-02 一键套用）。

    读取该回路 loop_type 匹配的模板阈值，复制为 target_scope 的覆盖。
    - target_scope="loop": 创建回路级覆盖（ic_engineer 可用）
    - target_scope="plant": 创建装置级覆盖（仅 ADMIN）

    若目标 scope 已有覆盖则更新（upsert 语义）。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND
        BizError: ERR_NO_TEMPLATE — 该回路 loop_type 无匹配模板
        BizError: ERR_PERMISSION_DENIED — ic_engineer 套用到 plant scope
    """
    # 权限校验：ic_engineer 仅可套用到 loop scope
    if operator_role == "IC_ENGINEER" and target_scope != "loop":
        raise BizError(
            code="ERR_PERMISSION_DENIED",
            message="ic_engineer 仅可套用模板到回路级（loop scope），装置级套用请联系管理员",
            status_code=403,
        )

    # 加载回路
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    loop_type = loop.loop_type or "OTHER"

    # 查找匹配的 loop_type 模板
    template_result = await db.execute(
        select(DiagnosisThresholdOverride).where(
            DiagnosisThresholdOverride.diag_code == diag_code,
            DiagnosisThresholdOverride.scope_type == "loop_type",
            DiagnosisThresholdOverride.scope_id == loop_type,
        )
    )
    template = template_result.scalar_one_or_none()
    if template is None or not template.threshold:
        raise BizError(
            code="ERR_NO_TEMPLATE",
            message=f"回路类型 {loop_type} 无 {diag_code} 的阈值模板，请先在模板库管理中创建",
            status_code=404,
        )

    # 确定目标 scope_id
    if target_scope == "loop":
        target_scope_id = str(loop.id)
    elif target_scope == "plant":
        if not loop.unit_id:
            raise BizError(
                code="ERR_NO_PLANT",
                message="该回路未关联装置，无法套用到装置级",
                status_code=422,
            )
        target_scope_id = str(loop.unit_id)
    else:
        raise BizError(
            code="ERR_INVALID_SCOPE",
            message=f"target_scope 必须为 loop 或 plant，当前为 {target_scope}",
            status_code=422,
        )

    # upsert 目标 scope 覆盖（复用已有函数，传递 operator_role）
    return await upsert_override(
        db,
        operator,
        diag_code=diag_code,
        scope_type=target_scope,
        scope_id=target_scope_id,
        threshold=dict(template.threshold),
        operator_role=operator_role,
    )


# ---------------------------------------------------------------------------
# C4: 配置版本与回滚
# ---------------------------------------------------------------------------


async def list_config_versions(db: AsyncSession, diag_id: str) -> list[dict]:
    """获取诊断配置的版本历史（从 sys_audit_log 读取）。

    返回该配置的所有变更记录（DIAG_CONFIG_UPDATE），按时间倒序排列。
    """
    result = await db.execute(
        select(SysAuditLog)
        .where(SysAuditLog.target_type == "diagnosis_config")
        .where(SysAuditLog.target_id == diag_id)
        .where(SysAuditLog.operation_type == "DIAG_CONFIG_UPDATE")
        .order_by(SysAuditLog.operated_at.desc())
    )
    logs = result.scalars().all()

    versions: list[dict] = []
    for idx, log in enumerate(logs):
        after_dict = None
        if log.after_value:
            try:
                after_dict = json.loads(log.after_value)
            except (json.JSONDecodeError, TypeError):
                pass
        before_dict = None
        if log.before_value:
            try:
                before_dict = json.loads(log.before_value)
            except (json.JSONDecodeError, TypeError):
                pass
        # 从 after_value 提取版本号
        version = (after_dict or {}).get("version", len(logs) - idx)
        versions.append(
            {
                "auditLogId": str(log.id),
                "version": version,
                "beforeValue": before_dict,
                "afterValue": after_dict,
                "operatedBy": log.operator,
                "operatedAt": log.operated_at.isoformat() if log.operated_at else None,
            }
        )
    return versions


async def rollback_config(db: AsyncSession, diag_id: str, audit_log_id: str, operator: str) -> dict:
    """回滚诊断配置到指定版本。

    从 sys_audit_log 读取目标版本的 before_value，恢复到该状态。
    回滚本身也记录审计日志。

    Raises:
        BizError: ERR_AUDIT_LOG_NOT_FOUND / ERR_NO_BEFORE_VALUE
    """
    # 读取目标审计日志
    log_result = await db.execute(select(SysAuditLog).where(SysAuditLog.id == audit_log_id))
    audit_log = log_result.scalar_one_or_none()
    if audit_log is None:
        raise BizError(
            code="ERR_AUDIT_LOG_NOT_FOUND",
            message="审计日志不存在",
            status_code=404,
        )

    if not audit_log.before_value:
        raise BizError(
            code="ERR_NO_BEFORE_VALUE",
            message="目标版本无 before_value（可能是首次创建），无法回滚",
            status_code=422,
        )

    # 解析 before_value
    try:
        before_dict = json.loads(audit_log.before_value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise BizError(
            code="ERR_INVALID_BEFORE_VALUE",
            message=f"before_value 解析失败: {exc}",
            status_code=422,
        ) from exc

    # 加载当前配置
    from app.models.diagnosis import DiagnosisConfig

    config_result = await db.execute(select(DiagnosisConfig).where(DiagnosisConfig.id == diag_id))
    config = config_result.scalar_one_or_none()
    if config is None:
        raise BizError(
            code="ERR_DIAG_CONFIG_NOT_FOUND",
            message="诊断指标配置不存在",
            status_code=404,
        )

    # 记录回滚前的值
    current_before = json.dumps(_config_to_dict(config), ensure_ascii=False, default=str)

    # 恢复字段
    config.diag_name = before_dict.get("diagName", config.diag_name)
    config.algorithm_type = before_dict.get("algorithmType", config.algorithm_type)
    config.calc_method = before_dict.get("calcMethod", config.calc_method)
    config.params = before_dict.get("params", config.params)
    config.threshold = before_dict.get("threshold", config.threshold)
    config.is_enabled = before_dict.get("isEnabled", config.is_enabled)
    config.version = (config.version or 1) + 1
    config.updated_by = operator
    config.updated_at = datetime.now(UTC).replace(tzinfo=None)

    after = _config_to_dict(config)
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    # 回滚审计日志
    rollback_log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type="DIAG_CONFIG_ROLLBACK",
        target_type="diagnosis_config",
        target_id=str(config.id),
        before_value=current_before,
        after_value=after_json,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(rollback_log)
    await db.commit()

    return after


def _config_to_dict(config) -> dict:
    """DiagnosisConfig 转字典（CamelCase）。"""
    return {
        "diagId": str(config.id),
        "diagCode": config.diag_code,
        "diagName": config.diag_name,
        "algorithmType": config.algorithm_type,
        "calcMethod": config.calc_method,
        "params": config.params,
        "threshold": config.threshold,
        "isEnabled": config.is_enabled,
        "version": config.version,
        "updatedBy": config.updated_by,
        "updatedAt": config.updated_at.isoformat() if config.updated_at else None,
    }
