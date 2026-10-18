"""回路核查最小实体（P6-05 后续分片一 E1，契约 01a §2.1）.

一条核查 = 一个"待弄清的问题"（来自预警/诊断/评估/人工），挂在一个回路上，
按**来源身份 + 问题身份**去重（不仅按 loopId 合并）：

- 阶段机 4 态：``TODO → IN_PROGRESS ⇄ WAITING``，任意未结束态 ``→ CLOSED``
  （终态）。``WAITING`` 必须同时持有等待原因、复查条件与等待责任三项
  （DB CHECK 强制），离开 ``WAITING`` 必须清空三项。
- 结束结果 4 类：``NO_FURTHER_ACTION / ANSWERED / HANDED_OFF / CANCELLED``，
  仅 ``CLOSED`` 时存在（DB CHECK 强制互斥）；**取消 ≠ 回路正常**——
  ``CANCELLED`` 只表示该核查不再推进，必须留 ``outcome_note`` 取消理由，
  不代表回路健康，也不改任何回路/工单状态。
- 负责人：``owner_id`` 为唯一稳定分派身份（``sys_user.id``）；``owner_name``
  仅为分派时刻的姓名快照，**只作显示**——用户改名后读取以 ``sys_user``
  当前姓名为准，用户删除后 ``owner_id`` SET NULL、快照保留且读方派生
  "待确认责任"标记，不做任何同名猜测关联。
- 移交（HANDED_OFF）双记录原子：源记录 ``CLOSED + HANDED_OFF`` 并保存
  目标回链（``close_target_kind='loop_investigation'`` + 目标 id），目标
  记录同事务建立且接续责任真实落位（``owner_id``=接手人稳定 id，
  ``handoff_source_id`` 回指源记录）；目标记录复制来源身份与问题身份
  （问题延续），不复制任何执行/验证状态。
- 去重：``(loop_id, source_kind, source_id, problem_key)`` 在未结束记录上
  唯一（部分唯一索引）——同一来源同一问题只存在一条进行中核查；结束后
  同问题再现允许新建（CANCELLED 不阻塞重建，因取消≠问题解决）。
- 并发认领/全部写操作：行级 ``revision`` CAS（409 携当前版本）+
  ``last_request_id`` 幂等重放（同 Slice A 个人关系口径）。
- 结束保存目标回链（``close_target_kind/close_target_id``，如处置工单/
  诊断记录 id），**不复制**被引用对象的执行/验证状态。

外键口径：loop 删除沿原生命周期 CASCADE 清理核查；user 删除 SET NULL
（核查是回路工作历史，不随用户删除）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: 核查阶段机（4 态；CLOSED 终态）
INVESTIGATION_STAGES = ("TODO", "IN_PROGRESS", "WAITING", "CLOSED")

#: 核查结束结果（4 类；仅 CLOSED 持有；CANCELLED ≠ 回路正常）
INVESTIGATION_OUTCOMES = ("NO_FURTHER_ACTION", "ANSWERED", "HANDED_OFF", "CANCELLED")


class LoopInvestigation(Base):
    """回路核查最小实体（来源身份+问题身份去重；revision CAS）.

    - ``source_kind``/``source_id``: 来源身份（服务层白名单 MANUAL /
      ALERT_EVENT / DIAGNOSIS_RUN / ASSESSMENT；MANUAL 缺省
      ``manual:{user_id}``）；移交产生的新记录**复制**来源身份（问题延续），
      经 ``handoff_source_id`` 回指上一条
    - ``problem_key``: 问题身份（同一来源内唯一标识该问题；去重键之一）
    - ``responsibility_pending`` 不落库：读方由 ``owner_id IS NULL AND
      owner_name IS NOT NULL`` 派生"待确认责任"（稳定身份缺失但存在旧姓名）
    """

    __tablename__ = "loop_investigation"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    #: 关联回路（核查按回路聚合查询；删除回路沿生命周期 CASCADE）
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 来源身份：类型 + ID（MANUAL/ALERT_EVENT/DIAGNOSIS_RUN/ASSESSMENT）
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 问题身份（来源内唯一标识该问题；去重键，不仅按 loopId 合并）
    problem_key: Mapped[str] = mapped_column(String(128), nullable=False)
    #: 问题标题 / 补充说明（展示用，非状态语义）
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: 阶段机：TODO 待认领 / IN_PROGRESS 核查中 / WAITING 等待中 / CLOSED 已结束（终态）
    stage: Mapped[str] = mapped_column(String(16), nullable=False, server_default="TODO")
    #: 结束结果（仅 CLOSED 持有）：NO_FURTHER_ACTION 无需进一步动作 /
    #: ANSWERED 已答复 / HANDED_OFF 已移交 / CANCELLED 已取消（≠回路正常）
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: 结束说明（CANCELLED 必填取消理由；HANDED_OFF 可填移交说明）
    outcome_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: 负责人稳定身份（sys_user.id；唯一分派身份，用户删除 SET NULL）
    owner_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sys_user.id", ondelete="SET NULL", name="fk_li_owner_user"),
        nullable=True,
    )
    #: 分派时刻姓名快照——只作显示，不作稳定分派身份；同名/改名不猜测关联
    owner_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    #: WAITING 三要素：等待原因 / 复查条件 / 等待责任（非 WAITING 必为 NULL）
    wait_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    wait_review_condition: Mapped[str | None] = mapped_column(String(200), nullable=True)
    wait_responsible: Mapped[str | None] = mapped_column(String(120), nullable=True)

    #: 结束目标回链（HANDED_OFF=目标核查 id；其余可挂处置工单/诊断记录等；
    #: 只存引用，不复制目标执行/验证状态）
    close_target_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    close_target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 移交链：本记录由哪条核查移交而来（源记录删除 SET NULL）
    handoff_source_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_investigation.id", ondelete="SET NULL", name="fk_li_handoff_source"),
        nullable=True,
    )

    #: 行级乐观锁版本（恒 >= 1；每次状态变更 +1；认领/等待/恢复/移交/结束 CAS）
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: 最近一次生效写请求的客户端请求身份（同 requestId 重试幂等返回）
    last_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: 创建人 / 结束操作人（稳定身份；用户删除 SET NULL，姓名读取时联查）
    created_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sys_user.id", ondelete="SET NULL", name="fk_li_created_by"),
        nullable=True,
    )
    closed_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sys_user.id", ondelete="SET NULL", name="fk_li_closed_by"),
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "stage IN ('TODO', 'IN_PROGRESS', 'WAITING', 'CLOSED')",
            name="ck_loop_investigation_stage",
        ),
        # 结束结果仅 CLOSED 持有；CLOSED 必有结果与结束时刻
        # （PG CHECK 对 NULL 求值放行——必须显式 IS NOT NULL 堵 NULL 洞）
        CheckConstraint(
            "(stage = 'CLOSED' AND outcome IS NOT NULL AND outcome IN "
            "('NO_FURTHER_ACTION', 'ANSWERED', 'HANDED_OFF', 'CANCELLED') "
            "AND closed_at IS NOT NULL) "
            "OR (stage <> 'CLOSED' AND outcome IS NULL AND closed_at IS NULL)",
            name="ck_loop_investigation_outcome",
        ),
        # WAITING 必有等待原因+复查条件+责任；非 WAITING 三者必空
        CheckConstraint(
            "(stage = 'WAITING' AND wait_reason IS NOT NULL "
            "AND wait_review_condition IS NOT NULL AND wait_responsible IS NOT NULL) "
            "OR (stage <> 'WAITING' AND wait_reason IS NULL "
            "AND wait_review_condition IS NULL AND wait_responsible IS NULL)",
            name="ck_loop_investigation_waiting",
        ),
        # 移交结束必须保存目标核查回链（接续责任真实建立的物证）
        CheckConstraint(
            "outcome IS NULL OR outcome <> 'HANDED_OFF' OR "
            "(close_target_kind = 'loop_investigation' AND close_target_id IS NOT NULL)",
            name="ck_loop_investigation_handoff_target",
        ),
        CheckConstraint("revision >= 1", name="ck_loop_investigation_revision"),
        # 来源身份+问题身份去重硬保证：同一回路同一来源同一问题至多一条未结束
        Index(
            "uk_loop_investigation_active_identity",
            "loop_id",
            "source_kind",
            "source_id",
            "problem_key",
            unique=True,
            postgresql_where=text("stage <> 'CLOSED'"),
        ),
        Index("idx_loop_investigation_loop_list", "loop_id", "stage", "created_at"),
        Index("idx_loop_investigation_owner", "owner_id", "stage"),
        {
            "comment": "回路核查最小实体（P6-05 E1；来源+问题去重，取消≠回路正常）",
        },
    )


__all__ = [
    "INVESTIGATION_OUTCOMES",
    "INVESTIGATION_STAGES",
    "LoopInvestigation",
]
