"""工程模板服务（P2-04：版本化模板 + TEMPLATE→回路映射）.

职责：
- **模板 CRUD**：``engineering_template`` 实体（template_key = config_override
  TEMPLATE 层 scope_id）；创建/查询/列表（含关联回路数）。
- **不可变版本发布**：``publish_template_version()`` 追加版本行 + 前进
  ``current_version`` 指针，**接 P2-02 统一发布链**（expectedRevision 乐观锁、
  ``config_publication`` 账本 operation=TEMPLATE_VERSION、SysAuditLog 审计、
  广播加速通知）。版本行本身不改不删——发布新版不自动改已固定引用
  （pin 了版本号的回路与记录了 revisionRef 的任务上下文解析结果不变）。
- **版本回拨**：``rollback_template_version()`` 将 current_version 指针回拨到
  既有版本（生成新 revision；版本历史保持追加式）。
- **TEMPLATE→回路映射**：``count_loops_for_template()`` /
  ``list_loops_for_template()`` 经 ``loop_ledger.engineering_template_id``
  精确计数（消除 P2-02 交接登记的 affectedLoops=None 诚实降级成因）。
- **引用解析**：``resolve_template_reference()`` —— loop pin 的版本优先，
  否则跟随 current_version；返回 ``revisionRef``
  （``engineering_template:{id}@v{version}``）供任务/结果上下文固定引用。

不做的事：
- 不在发布路径原地更新版本行（CFG-07 "DCS 模板原地更新" 的反模式）；
- 模板 profile 的算法消费归后续阶段（本批只做载体与不可变性）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.config_publish import ConfigPublication
from app.models.engineering_template import (
    TEMPLATE_CATEGORY_CONTROL,
    TEMPLATE_CATEGORY_RESPONSE,
    EngineeringTemplate,
    EngineeringTemplateVersion,
)
from app.models.loop import LoopLedger

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = (TEMPLATE_CATEGORY_RESPONSE, TEMPLATE_CATEGORY_CONTROL)


def template_revision_ref(template_id: str, version: int) -> str:
    """模板版本的固定引用形态（任务/结果上下文记录；发布新版后不变）."""
    return f"engineering_template:{template_id}@v{version}"


async def get_template_by_key(db: AsyncSession, template_key: str) -> EngineeringTemplate | None:
    result = await db.execute(
        select(EngineeringTemplate).where(EngineeringTemplate.template_key == template_key)
    )
    return result.scalar_one_or_none()


async def get_template_by_id(db: AsyncSession, template_id: str) -> EngineeringTemplate | None:
    result = await db.execute(
        select(EngineeringTemplate).where(EngineeringTemplate.id == template_id)
    )
    return result.scalar_one_or_none()


async def create_template(
    db: AsyncSession,
    *,
    template_key: str,
    name: str,
    category: str = TEMPLATE_CATEGORY_RESPONSE,
    description: str | None = None,
    operator: str = "system",
) -> dict[str, Any]:
    """创建工程模板实体（不发布版本；template_key 全局唯一）."""
    key = template_key.strip()
    if not key:
        raise BizError(
            code="ERR_PARAM_INVALID",
            message="template_key 不能为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if category not in _VALID_CATEGORIES:
        raise BizError(
            code="ERR_PARAM_INVALID",
            message=f"category 必须为 {_VALID_CATEGORIES} 之一，收到 {category}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if await get_template_by_key(db, key) is not None:
        raise BizError(
            code="ERR_TEMPLATE_KEY_DUPLICATE",
            message=f"模板键已存在: {key}",
            status_code=status.HTTP_409_CONFLICT,
        )
    row = EngineeringTemplate(
        template_key=key,
        name=name,
        category=category,
        description=description,
        created_by=operator,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info("[工程模板] 新增 %s(%s) by %s", key, name, operator)
    return _template_to_dict(row, version_count=0)


def _template_to_dict(
    row: EngineeringTemplate, *, version_count: int | None = None, loop_count: int | None = None
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "templateKey": row.template_key,
        "name": row.name,
        "category": row.category,
        "currentVersion": row.current_version,
        "description": row.description,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        **({"versionCount": version_count} if version_count is not None else {}),
        **({"loopCount": loop_count} if loop_count is not None else {}),
    }


async def list_templates(db: AsyncSession) -> list[dict[str, Any]]:
    """全部模板（含版本数与关联回路数——TEMPLATE→回路映射可查）."""
    rows = (
        (await db.execute(select(EngineeringTemplate).order_by(EngineeringTemplate.template_key)))
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        vc = (
            await db.execute(
                select(func.count())
                .select_from(EngineeringTemplateVersion)
                .where(EngineeringTemplateVersion.template_id == row.id)
            )
        ).scalar_one()
        lc = await count_loops_for_template(db, str(row.id))
        out.append(_template_to_dict(row, version_count=int(vc), loop_count=lc))
    return out


# ---------------------------------------------------------------------------
# 不可变版本发布 / 回拨（接 P2-02 统一发布链）
# ---------------------------------------------------------------------------


async def publish_template_version(
    db: AsyncSession,
    *,
    template_id: str,
    profile: dict[str, Any],
    reason: str,
    operator: str,
    expected_revision: int,
) -> dict[str, Any]:
    """发布模板新版本：追加不可变版本行 + 前进 current_version 指针.

    发布链（与 ``config_publish.publish_override`` 同构）：
    expectedRevision 前置 409 → INSERT 版本行（version = 当前最大 + 1）→
    前进指针 → ``config_publication``（operation=TEMPLATE_VERSION）→
    SysAuditLog → 单事务 commit → 广播。

    不可变性：既有版本行**零改动**（本函数对既有行只有 SELECT）；已固定
    引用（loop pin 的版本号 / 记录的 revisionRef）解析结果不变。

    Raises:
        BizError: ERR_TEMPLATE_NOT_FOUND(404) / ERR_CONFIG_REVISION_CONFLICT(409)
    """
    from app.services import config_publish as cp

    template = await get_template_by_id(db, template_id)
    if template is None:
        raise BizError(
            code="ERR_TEMPLATE_NOT_FOUND",
            message=f"工程模板不存在: {template_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    current = await cp._require_revision_or_409(db, expected_revision)
    # commit 后 ORM 属性按 expire_on_commit 过期（异步会话不能同步 lazy load，
    # 否则 MissingGreenlet）——返回值所需属性先快照
    template_id_str = str(template.id)
    template_key_str = template.template_key
    max_version = (
        await db.execute(
            select(func.max(EngineeringTemplateVersion.version)).where(
                EngineeringTemplateVersion.template_id == template.id
            )
        )
    ).scalar_one_or_none()
    new_version = int(max_version or 0) + 1
    new_revision = current + 1

    before_state = {
        "currentVersion": template.current_version,
        # before 摘要携带上一版本内容（无版本时 null）
        "profile": await _version_profile(db, template.id, template.current_version),
    }
    db.add(
        EngineeringTemplateVersion(
            template_id=str(template.id),
            version=new_version,
            profile=dict(profile),
            reason=reason,
            published_by=operator,
        )
    )
    template.current_version = new_version
    template.updated_at = datetime.now(UTC).replace(tzinfo=None)

    scope = {
        "templateId": str(template.id),
        "templateKey": template.template_key,
        "channel": "engineering-template",
    }
    affected_loops = await count_loops_for_template(db, str(template.id))
    db.add(
        ConfigPublication(
            revision=new_revision,
            operation="TEMPLATE_VERSION",
            layer="TEMPLATE",
            scope=scope,
            reason=reason,
            operator=operator,
            before_value=_json(before_state),
            after_value=_json({"currentVersion": new_version, "profile": dict(profile)}),
            affected_loops=affected_loops,
            rollback_revision=current,
        )
    )
    db.add(
        SysAuditLog(
            operator=operator,
            operation_type="TEMPLATE_VERSION_PUBLISH",
            target_type="engineering_template_version",
            # REWORK：target_id 列为 VARCHAR(36)——纯模板 UUID（版本号信息在
            # after_value envelope 与 operation_type 中不丢；与 rollback 同口径）
            target_id=str(template.id),
            before_value=cp._audit_envelope(
                reason=reason,
                scope=scope,
                rollback_revision=current,
                affected_loops=affected_loops,
                note=f"发布版本 v{new_version}（追加式不可变；固定引用不受影响）",
                value=before_state,
            ),
            after_value=cp._audit_envelope(
                reason=reason,
                scope=scope,
                rollback_revision=current,
                affected_loops=affected_loops,
                note=f"发布版本 v{new_version}（追加式不可变；固定引用不受影响）",
                value={"currentVersion": new_version, "profile": dict(profile)},
            ),
            operated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        latest = await cp.get_current_revision(db)
        raise cp._conflict_409(latest) from None

    await cp.broadcast_config_revision(new_revision, source="engineering-template")
    return {
        "revision": new_revision,
        "rollbackRevision": current,
        "templateId": template_id_str,
        "templateKey": template_key_str,
        "version": new_version,
        "revisionRef": template_revision_ref(template_id_str, new_version),
        "affectedLoops": affected_loops,
    }


async def rollback_template_version(
    db: AsyncSession,
    *,
    template_id: str,
    version: int,
    reason: str,
    operator: str,
    expected_revision: int,
) -> dict[str, Any]:
    """回拨 current_version 指针到既有版本（生成新 revision；版本历史不删改）.

    Raises:
        BizError: ERR_TEMPLATE_NOT_FOUND / ERR_TEMPLATE_VERSION_NOT_FOUND(404) /
            ERR_CONFIG_REVISION_CONFLICT(409)
    """
    from app.services import config_publish as cp

    template = await get_template_by_id(db, template_id)
    if template is None:
        raise BizError(
            code="ERR_TEMPLATE_NOT_FOUND",
            message=f"工程模板不存在: {template_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    target = (
        await db.execute(
            select(EngineeringTemplateVersion).where(
                EngineeringTemplateVersion.template_id == template.id,
                EngineeringTemplateVersion.version == version,
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise BizError(
            code="ERR_TEMPLATE_VERSION_NOT_FOUND",
            message=f"模板版本不存在: {template.template_key}@v{version}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    current = await cp._require_revision_or_409(db, expected_revision)
    new_revision = current + 1
    # commit 后 ORM 属性按 expire_on_commit 过期（异步会话不能同步 lazy load，
    # 否则 MissingGreenlet）——返回值所需属性先快照
    template_id_str = str(template.id)
    template_key_str = template.template_key
    target_version_int = int(target.version)
    before_state = {"currentVersion": template.current_version}
    template.current_version = target_version_int
    template.updated_at = datetime.now(UTC).replace(tzinfo=None)

    scope = {
        "templateId": template_id_str,
        "templateKey": template_key_str,
        "channel": "engineering-template-rollback",
        "rollbackToVersion": target_version_int,
    }
    affected_loops = await count_loops_for_template(db, template_id_str)
    db.add(
        ConfigPublication(
            revision=new_revision,
            operation="TEMPLATE_VERSION",
            layer="TEMPLATE",
            scope=scope,
            reason=reason,
            operator=operator,
            before_value=_json(before_state),
            after_value=_json({"currentVersion": target_version_int}),
            affected_loops=affected_loops,
            rollback_revision=current,
        )
    )
    db.add(
        SysAuditLog(
            operator=operator,
            operation_type="TEMPLATE_VERSION_ROLLBACK",
            target_type="engineering_template",
            target_id=template_id_str,
            before_value=cp._audit_envelope(
                reason=reason,
                scope=scope,
                rollback_revision=current,
                affected_loops=affected_loops,
                note=f"current_version 指针回拨至 v{target_version_int}（版本历史保持追加式）",
                value=before_state,
            ),
            after_value=cp._audit_envelope(
                reason=reason,
                scope=scope,
                rollback_revision=current,
                affected_loops=affected_loops,
                note=f"current_version 指针回拨至 v{target_version_int}（版本历史保持追加式）",
                value={"currentVersion": target_version_int},
            ),
            operated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        latest = await cp.get_current_revision(db)
        raise cp._conflict_409(latest) from None

    await cp.broadcast_config_revision(new_revision, source="engineering-template-rollback")
    return {
        "revision": new_revision,
        "rollbackRevision": current,
        "templateId": template_id_str,
        "templateKey": template_key_str,
        "version": target_version_int,
        "revisionRef": template_revision_ref(template_id_str, target_version_int),
        "affectedLoops": affected_loops,
    }


async def _version_profile(
    db: AsyncSession, template_id: str, version: int | None
) -> dict[str, Any] | None:
    if version is None:
        return None
    row = (
        await db.execute(
            select(EngineeringTemplateVersion.profile).where(
                EngineeringTemplateVersion.template_id == template_id,
                EngineeringTemplateVersion.version == version,
            )
        )
    ).scalar_one_or_none()
    return dict(row) if row else None


async def get_version(db: AsyncSession, template_id: str, version: int) -> dict[str, Any] | None:
    """读取指定不可变版本（固定引用解析入口）."""
    row = (
        await db.execute(
            select(EngineeringTemplateVersion).where(
                EngineeringTemplateVersion.template_id == template_id,
                EngineeringTemplateVersion.version == version,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "templateId": str(row.template_id),
        "version": int(row.version),
        "profile": dict(row.profile or {}),
        "reason": row.reason,
        "publishedBy": row.published_by,
        "publishedAt": row.published_at.isoformat() if row.published_at else None,
        "revisionRef": template_revision_ref(str(row.template_id), int(row.version)),
    }


async def list_versions(db: AsyncSession, template_id: str) -> list[dict[str, Any]]:
    """版本历史（追加式账本，按版本倒序）."""
    rows = (
        (
            await db.execute(
                select(EngineeringTemplateVersion)
                .where(EngineeringTemplateVersion.template_id == template_id)
                .order_by(EngineeringTemplateVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "templateId": str(r.template_id),
            "version": int(r.version),
            "profile": dict(r.profile or {}),
            "reason": r.reason,
            "publishedBy": r.published_by,
            "publishedAt": r.published_at.isoformat() if r.published_at else None,
            "revisionRef": template_revision_ref(str(r.template_id), int(r.version)),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# TEMPLATE→回路映射（影响回路数精确计数）
# ---------------------------------------------------------------------------


async def count_loops_for_template(db: AsyncSession, template_id: str) -> int:
    """模板关联回路数（经 loop_ledger.engineering_template_id 精确计数）."""
    result = await db.execute(
        select(func.count())
        .select_from(LoopLedger)
        .where(LoopLedger.engineering_template_id == template_id)
    )
    return int(result.scalar_one())


async def list_loops_for_template(
    db: AsyncSession, template_id: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    """模板关联回路清单（发布预览受影响清单；默认前 50 条）."""
    rows = (
        await db.execute(
            select(LoopLedger.id, LoopLedger.tag_name, LoopLedger.is_active)
            .where(LoopLedger.engineering_template_id == template_id)
            .order_by(LoopLedger.tag_name.asc())
            .limit(limit)
        )
    ).all()
    return [
        {"loopId": str(r.id), "tagName": r.tag_name, "isActive": bool(r.is_active)} for r in rows
    ]


async def resolve_template_reference(db: AsyncSession, loop: LoopLedger) -> dict[str, Any] | None:
    """解析回路生效的模板版本（固定引用语义）.

    优先级：loop.engineering_template_version（pin，发布新版不自动改）>
    template.current_version（跟随当前指针）。返回::

        {"templateKey", "templateId", "version", "pinned": bool,
         "revisionRef": "engineering_template:{id}@v{n}", "profile"}

    未关联模板 / pin 或指针指向的版本行缺失（理论不一致）→ None（显式
    未知，不伪补 profile）。
    """
    template_id = getattr(loop, "engineering_template_id", None)
    if not template_id:
        return None
    template = await get_template_by_id(db, str(template_id))
    if template is None:
        return None
    pinned_version = getattr(loop, "engineering_template_version", None)
    version = int(pinned_version) if pinned_version is not None else template.current_version
    if version is None:
        return None
    row = (
        await db.execute(
            select(EngineeringTemplateVersion).where(
                EngineeringTemplateVersion.template_id == template.id,
                EngineeringTemplateVersion.version == version,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "templateKey": template.template_key,
        "templateId": str(template.id),
        "version": int(row.version),
        "pinned": pinned_version is not None,
        "revisionRef": template_revision_ref(str(template.id), int(row.version)),
        "profile": dict(row.profile or {}),
    }


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


async def validate_loop_template_ref(
    db: AsyncSession,
    *,
    template_id: str | None,
    version: int | None,
) -> None:
    """校验回路的模板引用（CRUD/批量配置共用）：模板存在 + pin 版本存在.

    pin 版本（version 非 None）必须同时指定模板；模板存在性 404、版本
    存在性 404。template_id 为 None 时视为"解除关联"，version 必须为 None。
    """
    from fastapi import status as http_status

    if version is not None and template_id is None:
        raise BizError(
            code="ERR_PARAM_INVALID",
            message="固定引用模板版本时必须同时指定 engineering_template_id",
            status_code=http_status.HTTP_400_BAD_REQUEST,
        )
    if template_id is None:
        return
    template = await get_template_by_id(db, template_id)
    if template is None:
        raise BizError(
            code="ERR_TEMPLATE_NOT_FOUND",
            message=f"工程模板不存在: {template_id}",
            status_code=http_status.HTTP_404_NOT_FOUND,
        )
    if version is not None:
        row = (
            await db.execute(
                select(EngineeringTemplateVersion).where(
                    EngineeringTemplateVersion.template_id == template.id,
                    EngineeringTemplateVersion.version == int(version),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise BizError(
                code="ERR_TEMPLATE_VERSION_NOT_FOUND",
                message=f"模板版本不存在: {template.template_key}@v{version}",
                status_code=http_status.HTTP_404_NOT_FOUND,
            )


__all__ = [
    "count_loops_for_template",
    "create_template",
    "get_template_by_id",
    "get_template_by_key",
    "get_version",
    "list_loops_for_template",
    "list_templates",
    "list_versions",
    "publish_template_version",
    "resolve_template_reference",
    "rollback_template_version",
    "template_revision_ref",
    "validate_loop_template_ref",
]
