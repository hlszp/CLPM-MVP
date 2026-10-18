"""个人关系分片服务层（P6-05，契约 01a §3 / 设计 03 号文）.

行为红线落点：

- 登录主体只来自 ``get_current_user``——本模块所有函数以 ``user_id`` 形参
  接收服务端已认证主体，**不存在自由 userId 入参的 HTTP 通道**；
- 关注/偏好/最近继续均为**设定语义**（isFollowed + expectedRevision +
  requestId），非 toggle；过期版本 409 并在 ``data`` 携带当前有效版本；
  同 requestId 重试幂等返回已有结果（``replayed=true``，不推进版本）；
- 取消关注保留版本墓碑（``is_followed=false`` 行不物理删）——取消后迟到
  的旧关注请求（含 expectedRevision=0 的"当作新建"重试）被 CAS 拒绝，
  不得复活；
- 偏好按 key 独立版本、白名单 + schema 校验；``resume_generation`` 为
  服务端保留键，客户端写入一律 400；
- 清空最近继续：先对本人 generation 行取 FOR UPDATE 锁并原子递增，再
  清理旧代记录；旧代迟到写/删请求 409 拒绝（ERR_GENERATION_STALE）；
- 写入前服务端校验对象存在/scope（复用 ``ensure_loop_in_scope`` /
  ``loops_in_scope``）；撤权后保留关系但读路径不返回名称/状态/证据——
  仅以 ``inaccessibleCount`` + ``readStatus=PARTIAL`` 提示存在不可访问
  对象。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.loop import LoopLedger
from app.models.personal_workspace import (
    UserLoopFollow,
    UserWorkResume,
    UserWorkspacePreference,
)
from app.services.authz_scope import (
    ScopeContext,
    ensure_loop_in_scope,
    loops_in_scope,
)

# ---------------------------------------------------------------------------
# 白名单与校验常量
# ---------------------------------------------------------------------------

#: 客户端可写偏好键白名单（契约 01a §3：默认首页、允许的筛选预设和排序）
PREF_KEY_DEFAULT_HOME = "default_home"
PREF_KEY_FILTER_PRESETS = "filter_presets"
PREF_KEY_SORT_PRESETS = "sort_presets"
CLIENT_PREFERENCE_KEYS: frozenset[str] = frozenset(
    {PREF_KEY_DEFAULT_HOME, PREF_KEY_FILTER_PRESETS, PREF_KEY_SORT_PRESETS}
)

#: 服务端保留偏好键（不对客户端开放写；resume_generation=清空栅栏代际）
RESUME_GENERATION_KEY = "resume_generation"
RESERVED_PREFERENCE_KEYS: frozenset[str] = frozenset({RESUME_GENERATION_KEY})

#: 最近继续资源类型白名单（首版仅 loop；后续分片扩展 loop_investigation 等）
RESOURCE_KIND_LOOP = "loop"
ALLOWED_RESOURCE_KINDS: frozenset[str] = frozenset({RESOURCE_KIND_LOOP})

PREFERENCE_SCHEMA_VERSION = 1

_MAX_PAYLOAD_BYTES = 8 * 1024
_MAX_JSON_DEPTH = 4
_MAX_DICT_ENTRIES = 64
_MAX_LIST_ITEMS = 64
_SENSITIVE_KEY_FRAGMENTS = ("password", "passwd", "secret", "token", "credential")
_URL_ANYWHERE_RE = re.compile(r"://", re.IGNORECASE)
_URL_SCHEME_PREFIX_RE = re.compile(r"^\s*(javascript|vbscript|data):", re.IGNORECASE)

READ_STATUS_COMPLETE = "COMPLETE"
READ_STATUS_PARTIAL = "PARTIAL"


def _now() -> datetime:
    """服务端时间（naive UTC，与全库时间列写入口径一致）."""
    return datetime.now(UTC).replace(tzinfo=None)


def _conflict_409(data: dict[str, Any], message: str) -> BizError:
    return BizError(
        code="ERR_REVISION_CONFLICT",
        message=message,
        status_code=status.HTTP_409_CONFLICT,
        data=data,
    )


def _generation_stale_409(current_generation: int) -> BizError:
    return BizError(
        code="ERR_GENERATION_STALE",
        message="最近继续代际已推进（可能已执行清空），请刷新后重试",
        status_code=status.HTTP_409_CONFLICT,
        data={"currentGeneration": current_generation},
    )


# ---------------------------------------------------------------------------
# 内容安全校验（不存凭据/任意 URL/脚本；不存业务结果副本靠深度与大小约束）
# ---------------------------------------------------------------------------


def _validate_json_value_safe(value: Any, *, code: str, depth: int = 1) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise BizError(code=code, message="内容嵌套层级过深", status_code=400)
    if isinstance(value, dict):
        if len(value) > _MAX_DICT_ENTRIES:
            raise BizError(code=code, message="内容字段数量超限", status_code=400)
        for k, v in value.items():
            if not isinstance(k, str) or not k:
                raise BizError(code=code, message="内容字段名非法", status_code=400)
            kl = k.lower()
            if any(fragment in kl for fragment in _SENSITIVE_KEY_FRAGMENTS):
                raise BizError(code=code, message="内容不允许包含凭据类字段", status_code=400)
            _validate_json_value_safe(v, code=code, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > _MAX_LIST_ITEMS:
            raise BizError(code=code, message="内容列表长度超限", status_code=400)
        for item in value:
            _validate_json_value_safe(item, code=code, depth=depth + 1)
    elif isinstance(value, str):
        if _URL_ANYWHERE_RE.search(value) or _URL_SCHEME_PREFIX_RE.match(value):
            raise BizError(code=code, message="内容不允许包含任意跳转网址", status_code=400)
    elif value is None or isinstance(value, bool | int | float):
        return
    else:
        raise BizError(code=code, message="内容类型不支持", status_code=400)


def _validate_payload_size(payload: Any, *, code: str) -> None:
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > _MAX_PAYLOAD_BYTES:
        raise BizError(code=code, message="内容大小超限（最大 8KB）", status_code=400)


def validate_preference_key(key: str) -> None:
    """偏好键白名单校验：保留键/未知键一律 400."""
    if key in RESERVED_PREFERENCE_KEYS:
        raise BizError(
            code="ERR_PREFERENCE_KEY_INVALID",
            message=f"偏好键 {key} 为服务端保留，不对客户端开放写",
            status_code=400,
        )
    if key not in CLIENT_PREFERENCE_KEYS:
        raise BizError(
            code="ERR_PREFERENCE_KEY_INVALID",
            message=f"不支持的偏好键: {key}",
            status_code=400,
        )


def validate_preference_payload(key: str, payload: Any) -> None:
    """按 key 校验 payload 结构（白名单键各自的 schema + 通用安全约束）."""
    code = "ERR_PREFERENCE_PAYLOAD_INVALID"
    if not isinstance(payload, dict):
        raise BizError(code=code, message="payload 必须为 JSON 对象", status_code=400)
    _validate_payload_size(payload, code=code)
    _validate_json_value_safe(payload, code=code)

    if key == PREF_KEY_DEFAULT_HOME:
        unknown = set(payload) - {"path"}
        if unknown:
            raise BizError(code=code, message="default_home 仅允许 path 字段", status_code=400)
        path = payload.get("path")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or len(path) > 200
        ):
            raise BizError(
                code=code,
                message="default_home.path 必须为站内路径（以 / 开头，不超过 200 字符）",
                status_code=400,
            )


def validate_resource_kind(kind: str) -> None:
    """最近继续资源类型白名单校验：未知 kind 一律 400."""
    if kind not in ALLOWED_RESOURCE_KINDS:
        raise BizError(
            code="ERR_RESOURCE_KIND_INVALID",
            message=f"不支持的资源类型: {kind}",
            status_code=400,
        )


def validate_resume_context(context: Any) -> None:
    """最近继续 context 校验：仅 ID/窗口/证据引用/恢复位置."""
    code = "ERR_RESUME_CONTEXT_INVALID"
    if not isinstance(context, dict):
        raise BizError(code=code, message="context 必须为 JSON 对象", status_code=400)
    _validate_payload_size(context, code=code)
    _validate_json_value_safe(context, code=code)


def _normalize_loop_id(loop_id: str) -> str:
    """loopId 路径参数 UUID 校验 + 归一化（防非法 UUID 直撞 PG 类型报错；
    归一为 PG UUID 文本口径——小写带连字符，保证 PK/集合比对一致）."""
    try:
        return str(UUID(loop_id))
    except (ValueError, AttributeError, TypeError):
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from None


def _normalize_resource_id(resource_id: str) -> str:
    try:
        return str(UUID(resource_id))
    except (ValueError, AttributeError, TypeError):
        raise BizError(
            code="ERR_RESOURCE_ID_INVALID",
            message="资源 ID 格式非法",
            status_code=400,
        ) from None


async def _ensure_loop_accessible(db: AsyncSession, ctx: ScopeContext, loop_id: str) -> None:
    """写入前服务端校验：对象存在（404）→ 数据范围（403 通用拒绝）."""
    exists = (
        await db.execute(select(LoopLedger.id).where(LoopLedger.id == loop_id))
    ).scalar_one_or_none()
    if exists is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    await ensure_loop_in_scope(db, ctx, loop_id)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# 回路关注
# ---------------------------------------------------------------------------


def _follow_view(row: UserLoopFollow, *, replayed: bool = False) -> dict[str, Any]:
    return {
        "loop_id": str(row.loop_id),
        "is_followed": bool(row.is_followed),
        "revision": int(row.revision),
        "updated_at": row.updated_at,
        "replayed": replayed,
    }


async def list_loop_follows(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: ScopeContext,
    page: int,
    page_size: int,
    search: str | None = None,
) -> dict[str, Any]:
    """本人已关注且当前可访问的回路（分页/搜索/稳定排序）.

    撤权（scope 收窄）后：关系保留，但列表不返回其名称/描述——不可访问
    对象仅计入 ``inaccessible_count``（readStatus=PARTIAL 提示）。
    """
    stmt = (
        select(UserLoopFollow, LoopLedger)
        .join(LoopLedger, LoopLedger.id == UserLoopFollow.loop_id)
        .where(UserLoopFollow.user_id == user_id, UserLoopFollow.is_followed.is_(True))
    )
    if ctx.allowed_node_ids is not None:
        stmt = stmt.where(LoopLedger.unit_id.in_(ctx.allowed_node_ids))
    if search:
        like = f"%{_escape_like(search.strip())}%"
        stmt = stmt.where(
            or_(
                LoopLedger.tag_name.ilike(like, escape="\\"),
                LoopLedger.description.ilike(like, escape="\\"),
            )
        )

    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (
        await db.execute(
            stmt.order_by(UserLoopFollow.updated_at.desc(), UserLoopFollow.loop_id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    inaccessible_count = 0
    if ctx.allowed_node_ids is not None:
        inaccessible_count = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(UserLoopFollow)
                    .join(LoopLedger, LoopLedger.id == UserLoopFollow.loop_id)
                    .where(
                        UserLoopFollow.user_id == user_id,
                        UserLoopFollow.is_followed.is_(True),
                        or_(
                            LoopLedger.unit_id.is_(None),
                            LoopLedger.unit_id.notin_(ctx.allowed_node_ids),
                        ),
                    )
                )
            ).scalar_one()
        )

    items = [
        {
            "loop_id": str(follow.loop_id),
            "tag_name": str(loop.tag_name),
            "description": loop.description,
            "unit_id": str(loop.unit_id) if loop.unit_id is not None else None,
            "is_followed": bool(follow.is_followed),
            "revision": int(follow.revision),
            "updated_at": follow.updated_at,
        }
        for follow, loop in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "inaccessible_count": inaccessible_count,
        "read_status": READ_STATUS_PARTIAL if inaccessible_count else READ_STATUS_COMPLETE,
        "read_at": _now(),
    }


async def set_loop_follow(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: ScopeContext,
    loop_id: str,
    is_followed: bool,
    expected_revision: int,
    request_id: str,
) -> dict[str, Any]:
    """设定关注状态（非 toggle；CAS + requestId 幂等 + 取消墓碑）.

    流程：对象存在（404）→ scope（403）→ 行锁内版本裁决：
    - 同 requestId 重试 → 幂等返回已有结果（replayed=true）；
    - expectedRevision 失配 → 409 携当前有效版本；
    - 行不存在（含从未关注）→ expectedRevision 必须为 0；并发首建由
      ``(user_id, loop_id)`` 主键唯一约束裁决，败方重读后 409/幂等；
    - 取消关注仅置 ``is_followed=false`` 保留墓碑行，版本链延续——
      取消后迟到的旧关注请求（旧版本号或 expectedRevision=0）均 409，
      不得复活。
    """
    loop_id = _normalize_loop_id(loop_id)
    await _ensure_loop_accessible(db, ctx, loop_id)

    row = (
        await db.execute(
            select(UserLoopFollow)
            .where(UserLoopFollow.user_id == user_id, UserLoopFollow.loop_id == loop_id)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if row is not None:
        if row.last_request_id == request_id:
            return _follow_view(row, replayed=True)
        if int(row.revision) != expected_revision:
            raise _conflict_409(
                {
                    "loopId": loop_id,
                    "currentRevision": int(row.revision),
                    "isFollowed": bool(row.is_followed),
                },
                "关注状态版本冲突，请刷新后重试",
            )
        row.is_followed = is_followed
        row.revision = int(row.revision) + 1
        row.last_request_id = request_id
        row.updated_at = _now()
        await db.commit()
        return _follow_view(row)

    if expected_revision != 0:
        raise _conflict_409(
            {"loopId": loop_id, "currentRevision": 0, "isFollowed": False},
            "关注关系尚未建立，期望版本应为 0",
        )
    row = UserLoopFollow(
        user_id=user_id,
        loop_id=loop_id,
        is_followed=is_followed,
        revision=1,
        last_request_id=request_id,
        updated_at=_now(),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        # 唯一约束裁决并发首建：败方重读当前行——同 requestId 幂等，否则 409
        await db.rollback()
        current = (
            await db.execute(
                select(UserLoopFollow).where(
                    UserLoopFollow.user_id == user_id,
                    UserLoopFollow.loop_id == loop_id,
                )
            )
        ).scalar_one_or_none()
        if current is not None and current.last_request_id == request_id:
            return _follow_view(current, replayed=True)
        raise _conflict_409(
            {
                "loopId": loop_id,
                "currentRevision": int(current.revision) if current is not None else 0,
                "isFollowed": bool(current.is_followed) if current is not None else False,
            },
            "关注状态版本冲突，请刷新后重试",
        ) from None
    return _follow_view(row)


# ---------------------------------------------------------------------------
# 账号工作偏好
# ---------------------------------------------------------------------------


async def get_resume_generation(db: AsyncSession, user_id: str) -> int:
    """读取本人当前清空栅栏代际（无保留行=初始代际 1；无锁轻读）."""
    payload = (
        await db.execute(
            select(UserWorkspacePreference.payload).where(
                UserWorkspacePreference.user_id == user_id,
                UserWorkspacePreference.preference_key == RESUME_GENERATION_KEY,
            )
        )
    ).scalar_one_or_none()
    if isinstance(payload, dict):
        gen = payload.get("generation")
        if isinstance(gen, int) and gen >= 1:
            return gen
    return 1


async def _lock_resume_generation(db: AsyncSession, user_id: str) -> int:
    """确保 generation 行存在并取 FOR UPDATE 行锁，返回当前代际.

    写/删/清空共用该锁序列化：写方持锁期间清空无法提交；清空先提交则
    写方在锁后重读到新代际并拒绝（旧代迟到请求不得复活）。
    """
    await db.execute(
        pg_insert(UserWorkspacePreference)
        .values(
            user_id=user_id,
            preference_key=RESUME_GENERATION_KEY,
            payload={"generation": 1},
            schema_version=PREFERENCE_SCHEMA_VERSION,
            revision=1,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "preference_key"])
    )
    row = (
        await db.execute(
            select(UserWorkspacePreference)
            .where(
                UserWorkspacePreference.user_id == user_id,
                UserWorkspacePreference.preference_key == RESUME_GENERATION_KEY,
            )
            .with_for_update()
        )
    ).scalar_one()
    payload = row.payload if isinstance(row.payload, dict) else {}
    gen = payload.get("generation")
    return gen if isinstance(gen, int) and gen >= 1 else 1


async def list_preferences(db: AsyncSession, *, user_id: str) -> dict[str, Any]:
    """本人客户端可写偏好（保留键不对外返回；只返回允许的偏好字段）."""
    rows = (
        (
            await db.execute(
                select(UserWorkspacePreference).where(
                    UserWorkspacePreference.user_id == user_id,
                    UserWorkspacePreference.preference_key.in_(sorted(CLIENT_PREFERENCE_KEYS)),
                )
            )
        )
        .scalars()
        .all()
    )
    items = {
        str(row.preference_key): {
            "payload": row.payload,
            "schema_version": int(row.schema_version),
            "revision": int(row.revision),
            "updated_at": row.updated_at,
        }
        for row in rows
    }
    return {"items": items, "read_at": _now()}


async def put_preference(
    db: AsyncSession,
    *,
    user_id: str,
    key: str,
    payload: dict[str, Any],
    expected_revision: int,
    request_id: str,
) -> dict[str, Any]:
    """按单 key 设定偏好（不整包覆盖；CAS + requestId 幂等）."""
    validate_preference_key(key)
    validate_preference_payload(key, payload)

    row = (
        await db.execute(
            select(UserWorkspacePreference)
            .where(
                UserWorkspacePreference.user_id == user_id,
                UserWorkspacePreference.preference_key == key,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if row is not None:
        if row.last_request_id == request_id:
            return _preference_view(row, replayed=True)
        if int(row.revision) != expected_revision:
            raise _conflict_409(
                {"preferenceKey": key, "currentRevision": int(row.revision)},
                "偏好版本冲突，请刷新后重试",
            )
        row.payload = payload
        row.revision = int(row.revision) + 1
        row.last_request_id = request_id
        row.updated_at = _now()
        await db.commit()
        return _preference_view(row)

    if expected_revision != 0:
        raise _conflict_409(
            {"preferenceKey": key, "currentRevision": 0},
            "偏好尚未建立，期望版本应为 0",
        )
    row = UserWorkspacePreference(
        user_id=user_id,
        preference_key=key,
        payload=payload,
        schema_version=PREFERENCE_SCHEMA_VERSION,
        revision=1,
        last_request_id=request_id,
        updated_at=_now(),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        current = (
            await db.execute(
                select(UserWorkspacePreference).where(
                    UserWorkspacePreference.user_id == user_id,
                    UserWorkspacePreference.preference_key == key,
                )
            )
        ).scalar_one_or_none()
        if current is not None and current.last_request_id == request_id:
            return _preference_view(current, replayed=True)
        raise _conflict_409(
            {
                "preferenceKey": key,
                "currentRevision": int(current.revision) if current is not None else 0,
            },
            "偏好版本冲突，请刷新后重试",
        ) from None
    return _preference_view(row)


def _preference_view(row: UserWorkspacePreference, *, replayed: bool = False) -> dict[str, Any]:
    return {
        "preference_key": str(row.preference_key),
        "payload": row.payload,
        "schema_version": int(row.schema_version),
        "revision": int(row.revision),
        "updated_at": row.updated_at,
        "replayed": replayed,
    }


# ---------------------------------------------------------------------------
# 最近继续
# ---------------------------------------------------------------------------


async def list_work_resume(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: ScopeContext,
    kind: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """本人最近继续列表（服务端时间排序；撤权对象不返回证据）.

    只返回当前代际、未删除且对象仍可访问的条目；不可访问（撤权/对象已删）
    条目仅以 ``inaccessible_count`` 计数提示（readStatus=PARTIAL）。
    """
    if kind is not None:
        validate_resource_kind(kind)
    current_generation = await get_resume_generation(db, user_id)

    stmt = select(UserWorkResume).where(
        UserWorkResume.user_id == user_id,
        UserWorkResume.deleted_at.is_(None),
        UserWorkResume.generation == current_generation,
    )
    if kind is not None:
        stmt = stmt.where(UserWorkResume.resource_kind == kind)
    rows = (
        (
            await db.execute(
                stmt.order_by(
                    UserWorkResume.last_visited_at.desc(),
                    UserWorkResume.resource_kind.asc(),
                    UserWorkResume.resource_id.asc(),
                ).limit(limit)
            )
        )
        .scalars()
        .all()
    )

    loop_ids = [str(r.resource_id) for r in rows if r.resource_kind == RESOURCE_KIND_LOOP]
    accessible_loop_ids = await loops_in_scope(db, ctx, loop_ids) if loop_ids else set()

    items: list[dict[str, Any]] = []
    inaccessible_count = 0
    for row in rows:
        is_loop = row.resource_kind == RESOURCE_KIND_LOOP
        if is_loop and str(row.resource_id) not in accessible_loop_ids:
            inaccessible_count += 1
            continue
        items.append(
            {
                "resource_kind": str(row.resource_kind),
                "resource_id": str(row.resource_id),
                "context": row.context,
                "revision": int(row.revision),
                "generation": int(row.generation),
                "last_visited_at": row.last_visited_at,
            }
        )

    return {
        "items": items,
        "current_generation": current_generation,
        "inaccessible_count": inaccessible_count,
        "read_status": READ_STATUS_PARTIAL if inaccessible_count else READ_STATUS_COMPLETE,
        "read_at": _now(),
    }


async def put_work_resume(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: ScopeContext,
    kind: str,
    resource_id: str,
    context: dict[str, Any],
    expected_revision: int,
    generation: int,
    request_id: str,
) -> dict[str, Any]:
    """记录最近继续（成功验证业务对象后才写入；失败访问不制造条目）.

    版本栅栏：``generation`` 必须与本人当前代际一致（先锁代际行再比对），
    旧代迟到请求 409（ERR_GENERATION_STALE）不得复活已清空记录；行级
    expectedRevision CAS 与 requestId 幂等同关注语义。
    """
    validate_resource_kind(kind)
    validate_resume_context(context)
    resource_id = _normalize_resource_id(resource_id)
    await _ensure_loop_accessible(db, ctx, resource_id)

    current_generation = await _lock_resume_generation(db, user_id)
    if generation != current_generation:
        raise _generation_stale_409(current_generation)

    now = _now()
    row = (
        await db.execute(
            select(UserWorkResume)
            .where(
                UserWorkResume.user_id == user_id,
                UserWorkResume.resource_kind == kind,
                UserWorkResume.resource_id == resource_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if row is not None:
        if row.last_request_id == request_id:
            return _resume_view(row, replayed=True)
        if int(row.revision) != expected_revision:
            raise _conflict_409(
                {
                    "resourceKind": kind,
                    "resourceId": resource_id,
                    "currentRevision": int(row.revision),
                    "currentGeneration": current_generation,
                },
                "最近继续版本冲突，请刷新后重试",
            )
        row.context = context
        row.revision = int(row.revision) + 1
        row.generation = current_generation
        row.last_visited_at = now
        row.deleted_at = None
        row.last_request_id = request_id
        row.updated_at = now
        await db.commit()
        return _resume_view(row)

    if expected_revision != 0:
        raise _conflict_409(
            {
                "resourceKind": kind,
                "resourceId": resource_id,
                "currentRevision": 0,
                "currentGeneration": current_generation,
            },
            "最近继续记录尚未建立，期望版本应为 0",
        )
    row = UserWorkResume(
        user_id=user_id,
        resource_kind=kind,
        resource_id=resource_id,
        context=context,
        revision=1,
        generation=current_generation,
        last_visited_at=now,
        last_request_id=request_id,
        updated_at=now,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        current = (
            await db.execute(
                select(UserWorkResume).where(
                    UserWorkResume.user_id == user_id,
                    UserWorkResume.resource_kind == kind,
                    UserWorkResume.resource_id == resource_id,
                )
            )
        ).scalar_one_or_none()
        if current is not None and current.last_request_id == request_id:
            return _resume_view(current, replayed=True)
        raise _conflict_409(
            {
                "resourceKind": kind,
                "resourceId": resource_id,
                "currentRevision": int(current.revision) if current is not None else 0,
                "currentGeneration": current_generation,
            },
            "最近继续版本冲突，请刷新后重试",
        ) from None
    return _resume_view(row)


def _resume_view(row: UserWorkResume, *, replayed: bool = False) -> dict[str, Any]:
    return {
        "resource_kind": str(row.resource_kind),
        "resource_id": str(row.resource_id),
        "revision": int(row.revision),
        "generation": int(row.generation),
        "last_visited_at": row.last_visited_at,
        "replayed": replayed,
    }


async def delete_work_resume(
    db: AsyncSession,
    *,
    user_id: str,
    kind: str,
    resource_id: str,
    generation: int,
) -> dict[str, Any]:
    """删除单条最近继续（保留版本墓碑，不物理删行）.

    重复删除幂等返回；旧代迟到删除 409（ERR_GENERATION_STALE）——不能
    把新一代记录墓碑化。
    """
    validate_resource_kind(kind)
    if kind == RESOURCE_KIND_LOOP:
        resource_id = _normalize_resource_id(resource_id)
    current_generation = await _lock_resume_generation(db, user_id)
    if generation != current_generation:
        raise _generation_stale_409(current_generation)

    row = (
        await db.execute(
            select(UserWorkResume)
            .where(
                UserWorkResume.user_id == user_id,
                UserWorkResume.resource_kind == kind,
                UserWorkResume.resource_id == resource_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise BizError(
            code="ERR_RESUME_NOT_FOUND",
            message="最近继续记录不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if row.deleted_at is not None:
        return {
            "resource_kind": kind,
            "resource_id": resource_id,
            "deleted": True,
            "revision": int(row.revision),
            "current_generation": current_generation,
        }
    now = _now()
    row.deleted_at = now
    row.revision = int(row.revision) + 1
    row.updated_at = now
    await db.commit()
    return {
        "resource_kind": kind,
        "resource_id": resource_id,
        "deleted": True,
        "revision": int(row.revision),
        "current_generation": current_generation,
    }


async def clear_work_resume(db: AsyncSession, *, user_id: str) -> dict[str, Any]:
    """清空本人最近继续：先原子递增 generation（行锁内），再清理旧代记录.

    代际行 FOR UPDATE 锁与写/删路径共用——清空与在途写入按锁获得顺序
    线性化；清空后旧代记录物理清除，旧代迟到请求被代际比对拒绝，
    不能复活。
    """
    current_generation = await _lock_resume_generation(db, user_id)
    new_generation = current_generation + 1

    gen_row = (
        await db.execute(
            select(UserWorkspacePreference).where(
                UserWorkspacePreference.user_id == user_id,
                UserWorkspacePreference.preference_key == RESUME_GENERATION_KEY,
            )
        )
    ).scalar_one()
    gen_row.payload = {"generation": new_generation}
    gen_row.revision = int(gen_row.revision) + 1
    gen_row.updated_at = _now()

    result = await db.execute(
        sa_delete(UserWorkResume).where(
            UserWorkResume.user_id == user_id,
            UserWorkResume.generation < new_generation,
        )
    )
    await db.commit()
    return {
        "current_generation": new_generation,
        "cleared_count": int(result.rowcount or 0),
    }


__all__ = [
    "ALLOWED_RESOURCE_KINDS",
    "CLIENT_PREFERENCE_KEYS",
    "RESERVED_PREFERENCE_KEYS",
    "RESUME_GENERATION_KEY",
    "clear_work_resume",
    "delete_work_resume",
    "get_resume_generation",
    "list_loop_follows",
    "list_preferences",
    "list_work_resume",
    "put_preference",
    "put_work_resume",
    "set_loop_follow",
    "validate_preference_key",
    "validate_preference_payload",
    "validate_resource_kind",
    "validate_resume_context",
]
