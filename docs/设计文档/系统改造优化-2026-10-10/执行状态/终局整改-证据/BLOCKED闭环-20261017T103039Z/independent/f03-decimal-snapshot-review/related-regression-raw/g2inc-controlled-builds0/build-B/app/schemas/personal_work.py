"""统一工作查询 API Schema（P6-05 后续分片二 E2，契约 01a §2.1/§3）.

``GET /api/v1/me/work`` 跨来源统一工作查询的响应模型（纯查询，无写请求体）。
请求/响应均继承 ``CamelModel``（snake_case 字段 ↔ camelCase JSON 别名）。

字段契约（01a §3 末段候选字段落地）：

- item：`workKind/workId/loopId/ownerId/stage/partition/plannedAt/dueAt/
  waitReason/evidenceRefs/nextAction/revision` 为契约字段；`title/reason/
  sourceKind/ownerDisplayName/responsibilityPending/waitReviewCondition/
  priorityReasons/loopTagName/unitId/unitName/context/updatedAt` 为呈现
  必需的同源补充（09§4.3：工作目标/来源/进入清单理由/负责人/证据时间）
- 集合：`items/total/nextCursor/readAt/readStatus/sourceStatuses`；
  `readStatus` = COMPLETE / PARTIAL / ERROR；来源失败或计数未知时
  ``total=None``——**不输出伪造 0**，部分数据仍可见
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.base import CamelModel

#: 工作视图（四 workKind）
WORK_KIND_ASSIGNED = "ASSIGNED"
WORK_KIND_SYSTEM_SUGGESTION = "SYSTEM_SUGGESTION"
WORK_KIND_FOLLOW = "FOLLOW"
WORK_KIND_RESUME = "RESUME"
WORK_KINDS: tuple[str, ...] = (
    WORK_KIND_ASSIGNED,
    WORK_KIND_SYSTEM_SUGGESTION,
    WORK_KIND_FOLLOW,
    WORK_KIND_RESUME,
)

#: 工作分区（互斥；先判等待 → 再判今日计划 → 其余现在可行动；
#: 未来计划/已结束仅经筛选查询）
PARTITION_ACTIONABLE = "ACTIONABLE"
PARTITION_TODAY = "TODAY"
PARTITION_WAITING = "WAITING"
PARTITION_FUTURE = "FUTURE"
PARTITION_CLOSED = "CLOSED"
PARTITIONS: tuple[str, ...] = (
    PARTITION_ACTIONABLE,
    PARTITION_TODAY,
    PARTITION_WAITING,
    PARTITION_FUTURE,
    PARTITION_CLOSED,
)

#: 集合完整性
READ_STATUS_COMPLETE = "COMPLETE"
READ_STATUS_PARTIAL = "PARTIAL"
READ_STATUS_ERROR = "ERROR"

#: 单来源读取状态
SOURCE_STATUS_OK = "OK"
SOURCE_STATUS_ERROR = "ERROR"


class EvidenceRef(CamelModel):
    """证据引用（只存种类+稳定 ID+可选版本，不复制业务结果）."""

    kind: str
    id: str
    version: str | None = None


class NextAction(CamelModel):
    """一个主要下一步（09§4.3；操作本身仍走原业务端点与权限）."""

    kind: str
    label: str


class PersonalWorkItem(CamelModel):
    """统一工作项.

    - ``partition``: 仅分配给我的工作参与互斥分区（等待/今日/可行动/
      未来/已结束）；系统建议恒为 ACTIONABLE（立即可审核）；关注/最近
      继续不承担工作分区（None）
    - ``responsibility_pending``: 稳定身份缺失但存在旧姓名快照时为
      true（不猜测同名关联）；本查询的稳定责任来源（owner_id /
      handler_id）恒有稳定身份，旧姓名记录不会据此归入"分配给我"
    - ``revision``: 来源实体自带乐观锁版本（核查/关注/最近继续）；
      工单/建议无版本列时为 None（其操作仍走原端点自身并发口径）
    """

    work_kind: str
    work_id: str
    loop_id: str
    title: str
    reason: str
    source_kind: str
    owner_id: str | None = None
    owner_display_name: str | None = None
    responsibility_pending: bool = False
    stage: str | None = None
    partition: str | None = None
    planned_at: datetime | None = None
    due_at: datetime | None = None
    wait_reason: str | None = None
    wait_review_condition: str | None = None
    priority_reasons: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    next_action: NextAction
    revision: int | None = None
    loop_tag_name: str | None = None
    unit_id: str | None = None
    unit_name: str | None = None
    context: dict | None = None
    updated_at: datetime | None = None


class WorkSourceStatus(CamelModel):
    """单来源读取状态（失败如实 ERROR；total 未知为 None，不填 0）.

    ``inaccessible_count``: 撤权（数据范围收窄）后仍保留但不回显名称/
    状态/证据的对象数——只提示存在不可访问对象（同 personal_workspace 惯例）。
    """

    status: str
    total: int | None = None
    inaccessible_count: int = 0


class PersonalWorkData(CamelModel):
    """GET /me/work 响应数据.

    - ``total``: 与分页同规则的可返回条目计数；任一来源失败导致计数
      未知时为 None（不输出伪造 0）
    - ``read_status``: COMPLETE（全部来源完整）/ PARTIAL（部分来源失败
      或存在撤权不可访问对象，部分数据仍可见）/ ERROR（全部来源失败）
    - 响应不输出任何"全部回路健康/无待办=健康"断言（空待办 ≠ 健康）
    """

    items: list[PersonalWorkItem]
    total: int | None = None
    next_cursor: str | None = None
    read_at: datetime
    read_status: str
    source_statuses: dict[str, WorkSourceStatus]


__all__ = [
    "PARTITION_ACTIONABLE",
    "PARTITION_CLOSED",
    "PARTITION_FUTURE",
    "PARTITION_TODAY",
    "PARTITION_WAITING",
    "PARTITIONS",
    "READ_STATUS_COMPLETE",
    "READ_STATUS_ERROR",
    "READ_STATUS_PARTIAL",
    "SOURCE_STATUS_ERROR",
    "SOURCE_STATUS_OK",
    "WORK_KIND_ASSIGNED",
    "WORK_KIND_FOLLOW",
    "WORK_KIND_RESUME",
    "WORK_KIND_SYSTEM_SUGGESTION",
    "WORK_KINDS",
    "EvidenceRef",
    "NextAction",
    "PersonalWorkData",
    "PersonalWorkItem",
    "WorkSourceStatus",
]
