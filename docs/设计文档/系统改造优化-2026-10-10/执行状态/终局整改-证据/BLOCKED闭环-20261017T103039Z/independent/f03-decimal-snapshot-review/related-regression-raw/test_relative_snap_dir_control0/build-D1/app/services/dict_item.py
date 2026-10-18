"""SysDictItem service — 通用字典项（可配置枚举）.

设计（2026-08-20 测点类型可配置决策）：
- 字典类型用代码常量（DICT_MEASURE_TYPE），不建字典类型表
- 合法性校验以字典为准；字典不可用（查询异常/空）时回退内置枚举
  （内置枚举 = 出厂默认，种子数据与其一致，行为无缝）
- 进程级 TTL 缓存（30s）：避免导入逐行查库；写操作后主动失效
- 引用校验：删除/禁用 MEASURE_TYPE 项时校验 tag_registry.measure_type
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.sys_dict_item import SysDictItem
from app.models.tag import TagRegistry

# 已注册字典类型（代码常量；新增可配置枚举在此注册）
DICT_MEASURE_TYPE = "MEASURE_TYPE"
DICT_TAG_TYPE = "TAG_TYPE"
DICT_LOOP_TYPE = "LOOP_TYPE"

# 字典类型 → 中文标题（前端管理页展示）
DICT_TYPE_TITLES: dict[str, str] = {
    DICT_MEASURE_TYPE: "测点类型",
    DICT_TAG_TYPE: "参数类型",
    DICT_LOOP_TYPE: "回路类型",
}

# MEASURE_TYPE 内置兜底（code, label）——与迁移种子一致；
# 字典表不可用时按此校验（如测试环境 mock DB）
MEASURE_TYPE_FALLBACK: list[tuple[str, str]] = [
    ("TEMPERATURE", "温度"),
    ("PRESSURE", "压力"),
    ("LEVEL", "液位"),
    ("FLOW", "流量"),
    ("ANALYSIS", "分析"),
    ("SPEED", "速度"),
    ("OTHER", "其他"),
]

# TAG_TYPE 内置兜底（code, label）——与迁移种子一致
TAG_TYPE_FALLBACK: list[tuple[str, str]] = [
    ("PV", "测量值"),
    ("SP", "设定值"),
    ("OP", "操作值"),
    ("MODE", "模式"),
    ("PID_P", "比例（P）"),
    ("PID_I", "积分（I）"),
    ("PID_D", "微分（D）"),
    ("OTHER", "其他"),
]

# LOOP_TYPE 内置兜底（code, label）——与迁移种子一致
LOOP_TYPE_FALLBACK: list[tuple[str, str]] = [
    ("TEMPERATURE", "温度"),
    ("PRESSURE", "压力"),
    ("LEVEL", "液位"),
    ("FLOW", "流量"),
    ("ANALYSIS", "分析"),
    ("SPEED", "速度"),
    ("OTHER", "其他"),
]

# 缓存 TTL（秒）：字典低频变更，30s 内多进程各自收敛
_CACHE_TTL_SECONDS = 30.0
# 模块级缓存：dict_type → (过期时间戳, [(code, label), ...])
_dict_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}


def invalidate_dict_cache(dict_type: str | None = None) -> None:
    """失效字典缓存（写操作后调用；None 清全部）。"""
    if dict_type is None:
        _dict_cache.clear()
    else:
        _dict_cache.pop(dict_type, None)


async def get_dict_items(
    db: AsyncSession,
    dict_type: str,
    *,
    enabled_only: bool = True,
) -> list[tuple[str, str]]:
    """读取字典项 [(code, label), ...]（按 sort_order 排序）。

    带 30s TTL 缓存；查询异常或空结果时回退内置枚举（仅 MEASURE_TYPE）。
    """
    now = time.monotonic()
    cached = _dict_cache.get(dict_type)
    if cached and cached[0] > now:
        return cached[1]

    items: list[tuple[str, str]] = []
    try:
        stmt = select(SysDictItem).where(SysDictItem.dict_type == dict_type)
        if enabled_only:
            stmt = stmt.where(SysDictItem.is_enabled.is_(True))
        stmt = stmt.order_by(SysDictItem.sort_order, SysDictItem.item_code)
        rows = (await db.execute(stmt)).scalars().all()
        items = [(r.item_code, r.item_label) for r in rows]
    except Exception:  # noqa: BLE001
        # 查询/转换异常（含测试 mock 环境）→ 空，走 fallback
        items = []

    if items:
        _dict_cache[dict_type] = (now + _CACHE_TTL_SECONDS, items)
        return items

    # 字典为空/查询失败：回退内置；其他类型返回空
    if dict_type == DICT_MEASURE_TYPE:
        return MEASURE_TYPE_FALLBACK
    if dict_type == DICT_TAG_TYPE:
        return TAG_TYPE_FALLBACK
    if dict_type == DICT_LOOP_TYPE:
        return LOOP_TYPE_FALLBACK
    return []


async def normalize_by_dict(
    db: AsyncSession,
    dict_type: str,
    value: str,
) -> str | None:
    """字典归一化：接受 code（大小写不敏感）或 label（中文），返回 code。

    无法识别返回 None。
    """
    v = value.strip()
    if not v:
        return None
    items = await get_dict_items(db, dict_type)
    label_map = {label: code for code, label in items}
    if v in label_map:
        return label_map[v]
    upper = v.upper()
    for code, _label in items:
        if code.upper() == upper:
            return code
    return None


async def dict_items_hint(db: AsyncSession, dict_type: str) -> str:
    """构造字典合法值提示（code(label) 对照，用于错误消息）。"""
    items = await get_dict_items(db, dict_type)
    return "、".join(f"{code}({label})" for code, label in items)


async def _write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    target_id: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=operation_type,
        target_type="sys_dict_item",
        target_id=target_id,
        before_value=before_value,
        after_value=after_value,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(log)


async def check_dict_item_referenced(db: AsyncSession, dict_type: str, item_code: str) -> bool:
    """校验字典项是否被业务表引用（删除/禁用前调用）。

    Returns:
        True = 已被引用（不允许删除/禁用）
    """
    if dict_type == DICT_MEASURE_TYPE:
        cnt = (
            await db.execute(
                select(func.count())
                .select_from(TagRegistry)
                .where(TagRegistry.measure_type == item_code)
            )
        ).scalar() or 0
        return cnt > 0
    if dict_type == DICT_TAG_TYPE:
        cnt = (
            await db.execute(
                select(func.count())
                .select_from(TagRegistry)
                .where(TagRegistry.tag_type == item_code)
            )
        ).scalar() or 0
        return cnt > 0
    if dict_type == DICT_LOOP_TYPE:
        from app.models.loop import LoopLedger

        cnt = (
            await db.execute(
                select(func.count())
                .select_from(LoopLedger)
                .where(LoopLedger.loop_type == item_code)
            )
        ).scalar() or 0
        return cnt > 0
    return False


async def list_dict_items_paged(
    db: AsyncSession,
    dict_type: str,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """字典项分页列表（管理页）。"""
    conditions = [SysDictItem.dict_type == dict_type]
    count_stmt = select(func.count()).select_from(SysDictItem)
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = select(SysDictItem)
    for cond in conditions:
        stmt = stmt.where(cond)
    rows = (
        (
            await db.execute(
                stmt.order_by(SysDictItem.sort_order, SysDictItem.item_code)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    # 引用标记：测点/参数类型被 tag_registry 引用；回路类型被 loop_ledger 引用
    referenced_codes: set[str] = set()
    if dict_type in (DICT_MEASURE_TYPE, DICT_TAG_TYPE, DICT_LOOP_TYPE) and rows:
        codes = [r.item_code for r in rows]
        if dict_type == DICT_LOOP_TYPE:
            from app.models.loop import LoopLedger

            column = LoopLedger.loop_type
        else:
            column = (
                TagRegistry.measure_type if dict_type == DICT_MEASURE_TYPE else TagRegistry.tag_type
            )
        result = await db.execute(select(column).where(column.in_(codes)).distinct())
        referenced_codes = {row[0] for row in result if row[0]}

    items = [
        {
            "id": str(r.id),
            "dictType": r.dict_type,
            "itemCode": r.item_code,
            "itemLabel": r.item_label,
            "sortOrder": r.sort_order,
            "isEnabled": r.is_enabled,
            "isReferenced": r.item_code in referenced_codes,
            "updatedBy": r.updated_by,
            "updatedAt": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


async def create_dict_item(
    db: AsyncSession,
    operator: str,
    dict_type: str,
    item_code: str,
    item_label: str,
    sort_order: int = 0,
    is_enabled: bool = True,
) -> dict:
    """新建字典项。

    Raises:
        BizError: ERR_DICT_ITEM_DUPLICATED（同字典下 code 重复）
    """
    dup = await db.execute(
        select(SysDictItem).where(
            SysDictItem.dict_type == dict_type,
            func.upper(SysDictItem.item_code) == item_code.upper(),
        )
    )
    if dup.scalar_one_or_none() is not None:
        raise BizError(
            code="ERR_DICT_ITEM_DUPLICATED",
            message=f"字典项编码已存在: {item_code}",
            status_code=400,
        )

    item = SysDictItem(
        id=str(uuid4()),
        dict_type=dict_type,
        item_code=item_code,
        item_label=item_label,
        sort_order=sort_order,
        is_enabled=is_enabled,
        updated_by=operator,
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(item)
    after_value = json.dumps(
        {"dictType": dict_type, "itemCode": item_code, "itemLabel": item_label},
        ensure_ascii=False,
    )
    await _write_audit(
        db=db,
        operator=operator,
        operation_type="DICT_ITEM_CREATE",
        target_id=str(item.id),
        after_value=after_value,
    )
    await db.commit()
    invalidate_dict_cache(dict_type)

    return {
        "id": str(item.id),
        "dictType": dict_type,
        "itemCode": item_code,
        "itemLabel": item_label,
    }


async def update_dict_item(
    db: AsyncSession,
    item_id: str,
    operator: str,
    item_label: str | None = None,
    sort_order: int | None = None,
    is_enabled: bool | None = None,
) -> dict:
    """更新字典项（label/排序/启停；code 与 dict_type 不可改）。

    Raises:
        BizError: ERR_DICT_ITEM_NOT_FOUND / ERR_DICT_ITEM_REFERENCED（禁用被引用项）
    """
    result = await db.execute(select(SysDictItem).where(SysDictItem.id == item_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise BizError(
            code="ERR_DICT_ITEM_NOT_FOUND",
            message="字典项不存在",
            status_code=404,
        )

    before = {
        "itemLabel": item.item_label,
        "sortOrder": item.sort_order,
        "isEnabled": item.is_enabled,
    }
    before_json = json.dumps(before, ensure_ascii=False, default=str)

    if item_label is not None:
        item.item_label = item_label
    if sort_order is not None:
        item.sort_order = sort_order
    if is_enabled is not None:
        if not is_enabled:
            # 禁用前校验引用
            if await check_dict_item_referenced(db, item.dict_type, item.item_code):
                raise BizError(
                    code="ERR_DICT_ITEM_REFERENCED",
                    message=f"字典项「{item.item_label}」已被业务数据引用，不可禁用",
                    status_code=400,
                )
        item.is_enabled = is_enabled
    item.updated_by = operator
    item.updated_at = datetime.now(UTC).replace(tzinfo=None)

    after = {
        "itemLabel": item.item_label,
        "sortOrder": item.sort_order,
        "isEnabled": item.is_enabled,
    }
    after_json = json.dumps(after, ensure_ascii=False, default=str)
    await _write_audit(
        db=db,
        operator=operator,
        operation_type="DICT_ITEM_UPDATE",
        target_id=str(item.id),
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()
    invalidate_dict_cache(item.dict_type)

    return {
        "id": str(item.id),
        "dictType": item.dict_type,
        "itemCode": item.item_code,
        "itemLabel": item.item_label,
        "sortOrder": item.sort_order,
        "isEnabled": item.is_enabled,
    }


async def delete_dict_item(db: AsyncSession, item_id: str, operator: str) -> dict:
    """删除字典项（被业务数据引用时拒绝）。

    Raises:
        BizError: ERR_DICT_ITEM_NOT_FOUND / ERR_DICT_ITEM_REFERENCED
    """
    result = await db.execute(select(SysDictItem).where(SysDictItem.id == item_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise BizError(
            code="ERR_DICT_ITEM_NOT_FOUND",
            message="字典项不存在",
            status_code=404,
        )

    if await check_dict_item_referenced(db, item.dict_type, item.item_code):
        raise BizError(
            code="ERR_DICT_ITEM_REFERENCED",
            message=f"字典项「{item.item_label}」已被业务数据引用，不可删除",
            status_code=400,
        )

    before_json = json.dumps(
        {"dictType": item.dict_type, "itemCode": item.item_code, "itemLabel": item.item_label},
        ensure_ascii=False,
    )
    await db.execute(delete(SysDictItem).where(SysDictItem.id == item_id))
    await _write_audit(
        db=db,
        operator=operator,
        operation_type="DICT_ITEM_DELETE",
        target_id=item_id,
        before_value=before_json,
    )
    await db.commit()
    invalidate_dict_cache(item.dict_type)

    return {"id": item_id, "deleted": True}


__all__ = [
    "DICT_MEASURE_TYPE",
    "DICT_TAG_TYPE",
    "DICT_LOOP_TYPE",
    "DICT_TYPE_TITLES",
    "check_dict_item_referenced",
    "create_dict_item",
    "delete_dict_item",
    "dict_items_hint",
    "get_dict_items",
    "invalidate_dict_cache",
    "list_dict_items_paged",
    "normalize_by_dict",
    "update_dict_item",
]
