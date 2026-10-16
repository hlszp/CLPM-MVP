"""个人关系分片 API Schema（P6-05，契约 01a §3）.

请求/响应均继承 ``CamelModel``（snake_case 字段 ↔ camelCase JSON 别名）。
所有写请求显式携带 ``expectedRevision`` + ``requestId``（设定语义，非
toggle）；work-resume 写/删另携 ``generation``（清空栅栏对齐）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# 回路关注
# ---------------------------------------------------------------------------


class LoopFollowItem(CamelModel):
    """关注列表单项（回路名称/描述读取自 loop_ledger，不冗余存储）."""

    loop_id: str
    tag_name: str
    description: str | None = None
    unit_id: str | None = None
    is_followed: bool
    revision: int
    updated_at: datetime


class LoopFollowListData(CamelModel):
    """GET /me/loop-follows 响应数据.

    - ``inaccessible_count``: 已关注但当前不可访问（撤权）的回路数——只可
      提示存在不可访问对象，不返回其名称/状态/证据
    - ``read_status``: COMPLETE / PARTIAL（存在不可访问对象时为 PARTIAL）
    """

    items: list[LoopFollowItem]
    total: int
    page: int
    page_size: int
    inaccessible_count: int
    read_status: str
    read_at: datetime


class LoopFollowSetRequest(CamelModel):
    """PUT /me/loop-follows/{loopId} 请求体（设定语义，非 toggle）.

    - ``is_followed``: 期望状态（true=关注 / false=取消）
    - ``expected_revision``: 期望当前版本；首次创建=0；失配 409 携当前版本
    - ``request_id``: 客户端请求身份；同 requestId 重试幂等返回已有结果
    """

    is_followed: bool
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=64)


class LoopFollowSetResult(CamelModel):
    """PUT /me/loop-follows/{loopId} 响应数据."""

    loop_id: str
    is_followed: bool
    revision: int
    updated_at: datetime
    #: true=同 requestId 幂等重放，未推进版本
    replayed: bool = False


# ---------------------------------------------------------------------------
# 账号工作偏好
# ---------------------------------------------------------------------------


class PreferenceItem(CamelModel):
    """单 key 偏好视图（只含白名单键；保留键不对外返回）."""

    payload: dict[str, Any]
    schema_version: int
    revision: int
    updated_at: datetime


class PreferenceListData(CamelModel):
    """GET /me/workspace-preferences 响应数据（按 key 索引的映射）."""

    items: dict[str, PreferenceItem]
    read_at: datetime


class PreferencePutRequest(CamelModel):
    """PUT /me/workspace-preferences/{key} 请求体."""

    payload: dict[str, Any]
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=64)


class PreferencePutResult(CamelModel):
    """PUT /me/workspace-preferences/{key} 响应数据."""

    preference_key: str
    payload: dict[str, Any]
    schema_version: int
    revision: int
    updated_at: datetime
    replayed: bool = False


# ---------------------------------------------------------------------------
# 最近继续
# ---------------------------------------------------------------------------


class WorkResumeItem(CamelModel):
    """最近继续单项（仅可访问对象；context 不含业务结果副本）."""

    resource_kind: str
    resource_id: str
    context: dict[str, Any]
    revision: int
    generation: int
    last_visited_at: datetime


class WorkResumeListData(CamelModel):
    """GET /me/work-resume 响应数据.

    - ``current_generation``: 本人当前清空栅栏代际——后续 PUT/DELETE 必须
      携带该代际，旧代请求 409 拒绝
    """

    items: list[WorkResumeItem]
    current_generation: int
    inaccessible_count: int
    read_status: str
    read_at: datetime


class WorkResumePutRequest(CamelModel):
    """PUT /me/work-resume/{kind}/{resourceId} 请求体."""

    context: dict[str, Any]
    expected_revision: int = Field(ge=0)
    generation: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=64)


class WorkResumePutResult(CamelModel):
    """PUT /me/work-resume/{kind}/{resourceId} 响应数据."""

    resource_kind: str
    resource_id: str
    revision: int
    generation: int
    last_visited_at: datetime
    replayed: bool = False


class WorkResumeDeleteResult(CamelModel):
    """DELETE /me/work-resume/{kind}/{resourceId} 响应数据（保留版本墓碑）."""

    resource_kind: str
    resource_id: str
    deleted: bool
    revision: int
    current_generation: int


class WorkResumeClearResult(CamelModel):
    """DELETE /me/work-resume（清空）响应数据."""

    current_generation: int
    cleared_count: int


__all__ = [
    "LoopFollowItem",
    "LoopFollowListData",
    "LoopFollowSetRequest",
    "LoopFollowSetResult",
    "PreferenceItem",
    "PreferenceListData",
    "PreferencePutRequest",
    "PreferencePutResult",
    "WorkResumeClearResult",
    "WorkResumeDeleteResult",
    "WorkResumeItem",
    "WorkResumeListData",
    "WorkResumePutRequest",
    "WorkResumePutResult",
]
