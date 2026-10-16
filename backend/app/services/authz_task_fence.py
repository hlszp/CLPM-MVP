"""人工异步任务的主体绑定与提交栅栏（P4-03，01§3.5）.

01§3.5 原文口径：

- 人工异步任务保存 subjectId/action/object scope/policyRevision，**在每
  对象开始与业务提交前重验**；
- 计算在事务外，**写事务对该主体策略 revision 行加共享锁**（P4-03 起为
  单一 ``policy_publication_guard`` 行共享锁），撤权/禁用对同一行加排他
  锁，校验最新有效权限与对象范围后**原子提交**结果、模型及派生建议；
- 发布确认后旧权限计算不能再提交业务产物；失权任务允许必要审计终态
  REVOKED，不发布新结果/建议；
- **SYSTEM 调度使用独立系统身份**，人工来源派生建议不得换 SYSTEM 绕过
  撤权。

实现：

- ``snapshot_task_subject``：任务提交时（HTTP 端点）拍主体快照（subjectId/
  action/policyRevision/loopIds）随任务传递；
- ``validate_for_commit``：**在业务写事务内**调用——guard 共享锁（FOR
  SHARE，持有至 commit）下复核：主体存在且未禁用、动作码仍在角色矩阵、
  对象回路仍在主体范围内；任一不满足 → 抛 ``SubjectRevokedError``（调用
  方转 REVOKED 审计终态，不发布业务产物）；
- 并发线性化：提交栅栏的共享锁与全部策略变更者的排他锁互斥——"撤权先
  完成则后续不得越权，提交先完成则保留之前合法证据"由 guard 获得顺序
  全序保证（账本行序即线性化证据）；
- SYSTEM identity：``subject_user_id`` 为空或 ``is_system=True``（定时/
  事件调度来源）→ 恒放行（SYSTEM 不可撤权；人工来源不得伪造 is_system）。

单测环境（CLPM_TEST_MODE=1）：栅栏短路放行（真实链路由 C25 集成测试
覆盖）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


class SubjectRevokedError(Exception):
    """主体失权（禁用/撤权/范围收紧/动作码收回）——不发布业务产物."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class TaskSubject:
    """人工任务主体绑定（提交时快照；提交前以 DB 实时状态复核）."""

    subject_user_id: str = ""  # 空 = SYSTEM（定时/事件调度）
    subject_role: str = ""
    action: str = ""  # 动作码（如 diagnosis:trigger / tuning:identify）
    policy_revision: int = 0  # 提交时 guard revision（审计用）
    loop_ids: list[str] = field(default_factory=list)
    is_system: bool = False  # SYSTEM 身份（调度来源恒 True，人工恒 False）

    @property
    def is_system_identity(self) -> bool:
        return self.is_system or not self.subject_user_id


def _is_unit_test_env() -> bool:
    return os.environ.get("CLPM_TEST_MODE") == "1"


async def snapshot_task_subject(
    db: AsyncSession,
    *,
    user_id: str,
    role: str,
    action: str,
    loop_ids: list[str],
    is_system: bool = False,
) -> TaskSubject:
    """任务提交时拍主体快照（policyRevision 随 guard 当前值）."""
    from app.services.authz_scope import read_guard_revision

    revision = 0
    if not _is_unit_test_env():
        try:
            revision = await read_guard_revision(db)
        except Exception:  # noqa: BLE001 —— 快照失败不阻断提交（提交前还会复核）
            revision = 0
    return TaskSubject(
        subject_user_id=str(user_id or ""),
        subject_role=str(role or ""),
        action=action,
        policy_revision=revision,
        loop_ids=[str(x) for x in loop_ids],
        is_system=bool(is_system),
    )


def system_subject(loop_ids: list[str] | None = None) -> TaskSubject:
    """SYSTEM 独立身份（定时/事件调度；不可撤权）."""
    return TaskSubject(
        subject_user_id="",
        subject_role="SYSTEM",
        action="",
        policy_revision=0,
        loop_ids=[str(x) for x in (loop_ids or [])],
        is_system=True,
    )


async def validate_for_commit(
    db: AsyncSession,
    subject: TaskSubject,
    loop_id: str,
) -> int:
    """业务提交栅栏（必须在业务写事务内、commit 之前调用）.

    行为：guard 共享锁（FOR SHARE，持有至事务结束——撤权排他锁无法在
    复核与 commit 之间插入）→ 主体复核（存在/未禁用/角色未变）→ 动作码
    复核（角色矩阵仍授予）→ 对象范围复核（回路仍在主体范围内）。

    Returns:
        复核时刻的 guard revision（供审计）。

    Raises:
        SubjectRevokedError: 任一复核不通过（调用方不发布业务产物，
        转 REVOKED 审计终态）。
    """
    if _is_unit_test_env() or subject.is_system_identity:
        return -1

    from sqlalchemy import select

    from app.models.sys_user import SysUser
    from app.services.authz_runtime import get_role_codes
    from app.services.authz_scope import (
        ScopeContext,
        ensure_loop_in_scope,
        lock_guard_for_share,
        resolve_scope,
    )

    revision = await lock_guard_for_share(db)

    row = (
        await db.execute(
            select(SysUser.id, SysUser.role, SysUser.is_active).where(
                SysUser.id == subject.subject_user_id
            )
        )
    ).one_or_none()
    if row is None:
        raise SubjectRevokedError(f"主体不存在: {subject.subject_user_id}")
    if not row.is_active:
        raise SubjectRevokedError(f"主体已禁用: {subject.subject_user_id}")
    current_role = str(row.role)
    if subject.subject_role and current_role != subject.subject_role:
        raise SubjectRevokedError(f"主体角色已变更: {subject.subject_role} → {current_role}")
    if subject.action:
        granted = await get_role_codes(db, current_role)
        if subject.action not in granted:
            raise SubjectRevokedError(f"动作码已被收回: {subject.action}")

    ctx: ScopeContext = await resolve_scope(
        db, subject.subject_user_id, current_role, revision=revision
    )
    try:
        await ensure_loop_in_scope(db, ctx, str(loop_id))
    except Exception as exc:  # noqa: BLE001 —— 统一转撤权语义
        raise SubjectRevokedError(f"对象回路已移出主体数据范围: loop={loop_id} ({exc})") from exc
    return revision


def audit_revoked(db: Any, subject: TaskSubject, loop_id: str, reason: str) -> None:
    """REVOKED 审计行（必要审计终态；不发布业务产物）."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.models.audit import SysAuditLog

    db.add(
        SysAuditLog(
            id=str(uuid4()),
            operator=subject.subject_user_id or "SYSTEM",
            operation_type="TASK_REVOKED",
            target_type="Loop",
            target_id=str(loop_id),
            before_value=None,
            after_value=str(
                {
                    "action": subject.action,
                    "policyRevisionAtSubmit": subject.policy_revision,
                    "reason": reason,
                    "subjectRole": subject.subject_role,
                }
            ),
            operated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )


__all__ = [
    "SubjectRevokedError",
    "TaskSubject",
    "audit_revoked",
    "snapshot_task_subject",
    "system_subject",
    "validate_for_commit",
]
