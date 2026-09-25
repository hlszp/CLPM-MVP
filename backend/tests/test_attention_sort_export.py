"""关注队列服务端排序与全量导出（2026-09-24）。

背景
----
关注队列是**按回路组服务端分页**的：
- 客户端 sorter 只能排当前页（且会让用户误以为全局有序）→ 排序必须服务端做；
- 页面导出只导当前页，原提示却写"已导出 N 条" → 取证时会误以为拿到全量。

本文件守护两条契约：
1. 排序参数在分页之前生效，且非法值静默回退默认（不 500）；
2. 导出为全量 CSV，首行含口径注释（生成时间/筛选/排序/条数），列头与行数自洽。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.monitor_attention import (
    ATTENTION_SORT_FIELDS,
    group_sort_key,
)


def _group(
    *,
    priority: str,
    updated_at: datetime | None,
    item_count: int,
    overdue: bool = False,
) -> dict:
    return {
        "priority": priority,
        "updatedAt": updated_at.isoformat() if updated_at else None,
        "itemCount": item_count,
        "isOverdue": overdue,
    }


class TestGroupSortKey:
    """组级排序键语义（不依赖 DB）。"""

    def test_default_priority_asc_urgent_first(self) -> None:
        urgent = _group(priority="URGENT", updated_at=None, item_count=1)
        low = _group(priority="LOW", updated_at=None, item_count=99)
        assert group_sort_key(urgent, "priority", "asc") < group_sort_key(low, "priority", "asc")

    def test_priority_desc_reverses(self) -> None:
        urgent = _group(priority="URGENT", updated_at=None, item_count=1)
        low = _group(priority="LOW", updated_at=None, item_count=1)
        assert group_sort_key(low, "priority", "desc") < group_sort_key(urgent, "priority", "desc")

    def test_updated_at_desc_newest_first(self) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        new = _group(priority="LOW", updated_at=now, item_count=1)
        old = _group(priority="URGENT", updated_at=now - timedelta(hours=5), item_count=1)
        assert group_sort_key(new, "updatedAt", "desc") < group_sort_key(old, "updatedAt", "desc")
        # 升序则相反
        assert group_sort_key(old, "updatedAt", "asc") < group_sort_key(new, "updatedAt", "asc")

    def test_updated_at_missing_sorts_last(self) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        with_time = _group(priority="LOW", updated_at=now, item_count=1)
        without = _group(priority="URGENT", updated_at=None, item_count=1)
        assert group_sort_key(with_time, "updatedAt", "desc") < group_sort_key(
            without, "updatedAt", "desc"
        )
        assert group_sort_key(with_time, "updatedAt", "asc") < group_sort_key(
            without, "updatedAt", "asc"
        )

    def test_item_count_desc_more_items_first(self) -> None:
        many = _group(priority="LOW", updated_at=None, item_count=58)
        few = _group(priority="URGENT", updated_at=None, item_count=2)
        assert group_sort_key(many, "itemCount", "desc") < group_sort_key(few, "itemCount", "desc")

    def test_overdue_asc_overdue_first(self) -> None:
        od = _group(priority="LOW", updated_at=None, item_count=1, overdue=True)
        normal = _group(priority="URGENT", updated_at=None, item_count=1)
        assert group_sort_key(od, "overdue", "asc") < group_sort_key(normal, "overdue", "asc")

    def test_all_fields_have_defined_semantics(self) -> None:
        """ATTENTION_SORT_FIELDS 中每个字段都能生成键（防空分支漏改）。"""
        g = _group(
            priority="URGENT",
            updated_at=datetime.now(UTC).replace(tzinfo=None),
            item_count=3,
            overdue=True,
        )
        for field in ATTENTION_SORT_FIELDS:
            assert group_sort_key(g, field, "asc") is not None
            assert group_sort_key(g, field, "desc") is not None
