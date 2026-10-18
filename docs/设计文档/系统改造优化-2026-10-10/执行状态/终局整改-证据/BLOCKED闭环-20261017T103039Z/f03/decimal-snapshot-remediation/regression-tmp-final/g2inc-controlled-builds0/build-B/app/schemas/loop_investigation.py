"""回路核查最小实体 API Schema（P6-05 后续分片一 E1，契约 01a §2.1）.

请求/响应均继承 ``CamelModel``（snake_case 字段 ↔ camelCase JSON 别名）。
所有写请求显式携带 ``expectedRevision`` + ``requestId``（CAS + 幂等重放，
同 Slice A 个人关系口径）；分派身份只接受稳定 ``sys_user.id``——任何
请求体都没有"按姓名分派"字段（旧姓名只作显示，不作分派身份）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# 核查视图
# ---------------------------------------------------------------------------


class InvestigationView(CamelModel):
    """核查单记录视图.

    - ``owner_display_name``: 负责人姓名——owner 用户存在时取 ``sys_user``
      当前显示名（改名后即刷新），用户已删除时回退分派时刻快照（旧姓名
      只作显示）
    - ``responsibility_pending``: 待确认责任——稳定身份缺失但存在姓名
      快照（用户删除/旧记录）时为 true；不做同名猜测关联
    """

    id: str
    loop_id: str
    source_kind: str
    source_id: str
    problem_key: str
    title: str
    detail: str | None = None
    stage: str
    outcome: str | None = None
    outcome_note: str | None = None
    owner_id: str | None = None
    owner_display_name: str | None = None
    responsibility_pending: bool = False
    wait_reason: str | None = None
    wait_review_condition: str | None = None
    wait_responsible: str | None = None
    close_target_kind: str | None = None
    close_target_id: str | None = None
    handoff_source_id: str | None = None
    revision: int
    created_by: str | None = None
    closed_by: str | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class InvestigationListData(CamelModel):
    """GET /loops/{loopId}/investigations 响应数据."""

    items: list[InvestigationView]
    total: int
    page: int
    page_size: int
    read_at: datetime


class InvestigationResult(CamelModel):
    """单记录写操作响应（``replayed``=同 requestId 幂等重放未推进版本）."""

    item: InvestigationView
    replayed: bool = False


class InvestigationCreateResult(CamelModel):
    """创建响应（``deduped``=命中来源+问题身份去重返回既有未结束记录）."""

    item: InvestigationView
    deduped: bool = False
    replayed: bool = False


class InvestigationHandoffResult(CamelModel):
    """移交响应（双记录原子：源记录 CLOSED/HANDED_OFF + 目标记录 TODO）."""

    source: InvestigationView
    target: InvestigationView
    replayed: bool = False


# ---------------------------------------------------------------------------
# 写请求
# ---------------------------------------------------------------------------


class InvestigationCreateRequest(CamelModel):
    """POST /loops/{loopId}/investigations 请求体.

    - ``source_kind``: 来源身份类型白名单（MANUAL/ALERT_EVENT/
      DIAGNOSIS_RUN/ASSESSMENT）；MANUAL 时 ``source_id`` 可缺省为
      ``manual:{user_id}``
    - ``problem_key``: 问题身份（来源内唯一；与来源身份共同构成去重键）
    """

    source_kind: str = Field(min_length=1, max_length=32)
    source_id: str | None = Field(None, max_length=64)
    problem_key: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(None, max_length=500)
    request_id: str = Field(min_length=1, max_length=64)


class InvestigationClaimRequest(CamelModel):
    """POST /investigations/{id}/claim 请求体（并发认领 CAS）."""

    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=64)


class InvestigationWaitRequest(CamelModel):
    """POST /investigations/{id}/wait 请求体（WAITING 三要素必填）."""

    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=64)
    wait_reason: str = Field(min_length=1, max_length=200)
    wait_review_condition: str = Field(min_length=1, max_length=200)
    wait_responsible: str = Field(min_length=1, max_length=120)


class InvestigationResumeRequest(CamelModel):
    """POST /investigations/{id}/resume 请求体."""

    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=64)


class InvestigationCloseRequest(CamelModel):
    """POST /investigations/{id}/close 请求体.

    - ``outcome``: NO_FURTHER_ACTION / ANSWERED / CANCELLED（HANDED_OFF
      只能经 /handoff 双记录原子路径达成，此处拒绝）
    - ``note``: CANCELLED 必填取消理由（取消 ≠ 回路正常，理由强制留痕）
    - ``close_target_kind/close_target_id``: 可选目标回链（白名单
      handling_order / diagnosis_run / loop_action_item，UUID 形态 id）；
      只存引用，不复制目标执行/验证状态
    """

    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=64)
    outcome: str = Field(min_length=1, max_length=24)
    note: str | None = Field(None, max_length=500)
    close_target_kind: str | None = Field(None, max_length=32)
    close_target_id: str | None = Field(None, max_length=64)


class InvestigationHandoffRequest(CamelModel):
    """POST /investigations/{id}/handoff 请求体.

    - ``new_owner_id``: 接手人稳定身份（``sys_user.id``）——接续责任在
      移交事务内真实建立（目标记录 owner 落位）；拒绝按姓名移交
    """

    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=64)
    new_owner_id: str = Field(min_length=1, max_length=64)
    note: str | None = Field(None, max_length=500)


__all__ = [
    "InvestigationClaimRequest",
    "InvestigationCloseRequest",
    "InvestigationCreateRequest",
    "InvestigationCreateResult",
    "InvestigationHandoffRequest",
    "InvestigationHandoffResult",
    "InvestigationListData",
    "InvestigationResult",
    "InvestigationResumeRequest",
    "InvestigationView",
    "InvestigationWaitRequest",
]
