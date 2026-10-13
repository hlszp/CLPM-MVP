"""P5-04 服务端筛选、稳定排序与 focus 位次测试（03 号文 §8 P5-04 卡）。

覆盖：
- ``_grade_score_ranges``：等级名 → 评分区间展开（前端 resolveGrade 同口径：
  最低档下界不设限 / 相邻档合并 / 自定义阈值兜底）；
- ``grades`` 参数：SQL 谓词下推（最新快照子查询 + 区间 OR）与互斥校验；
- ``modes`` 参数：MODE 原始值分桶过滤（Redis 优先回退 PG）+ scope 收窄
  交集保持（批 α 越权反例：候选集先收窄再解析模式）；
- ``sortBy=scoreDelta``：预计算列排序（NULLS LAST + 次级稳定键）；
- ``focusLoopId``：位次/所在页计算（不在结果集为 None）；
- 参数校验：非法 grades/modes/focusLoopId、grades×评分区间互斥、
  modes×controlMode 互斥；
- ``compute_score_delta``：刷新任务预计算列的纯函数口径。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import BizError
from app.services.monitor import (
    _DEFAULT_GRADE_THRESHOLDS,
    _filter_loop_ids_by_mode_values,
    _grade_score_ranges,
    list_loop_monitor,
)
from app.services.workbench_loop_latest import compute_score_delta

# ===========================================================================
# mock 辅助
# ===========================================================================


def _rows_mock(rows: list) -> MagicMock:
    """构造 execute 返回值，支持 .all() / .scalars().all()。"""
    result = MagicMock()
    result.all.return_value = rows
    result.scalars.return_value.all.return_value = rows
    return result


def _count_mock(count: int) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = count
    return result


def _loop(loop_id: str, tag_name: str = "LIC-101") -> MagicMock:
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.description = "液位控制"
    loop.unit_id = None
    loop.status = "READY"
    loop.is_active = True
    loop.created_at = None
    return loop


def _subscriber_mock(cached: list[dict] | None = None) -> MagicMock:
    subscriber = MagicMock()
    subscriber.get_cached_values = AsyncMock(return_value=cached or [])
    return subscriber


_UUID_A = str(uuid4())
_UUID_B = str(uuid4())
_UUID_C = str(uuid4())


# ===========================================================================
# _grade_score_ranges 纯函数
# ===========================================================================


class TestGradeScoreRanges:
    def test_single_top_grade_unbounded_above(self) -> None:
        assert _grade_score_ranges(_DEFAULT_GRADE_THRESHOLDS, {"EXCELLENT"}) == [(90.0, None)]

    def test_lowest_grade_unbounded_below(self) -> None:
        assert _grade_score_ranges(_DEFAULT_GRADE_THRESHOLDS, {"POOR"}) == [(None, 40.0)]

    def test_adjacent_grades_merge(self) -> None:
        assert _grade_score_ranges(_DEFAULT_GRADE_THRESHOLDS, {"GOOD", "FAIR"}) == [(60.0, 90.0)]
        assert _grade_score_ranges(_DEFAULT_GRADE_THRESHOLDS, {"WARNING", "POOR"}) == [(None, 60.0)]

    def test_non_adjacent_grades_two_ranges(self) -> None:
        assert _grade_score_ranges(_DEFAULT_GRADE_THRESHOLDS, {"GOOD", "POOR"}) == [
            (80.0, 90.0),
            (None, 40.0),
        ]

    def test_all_grades_covers_everything(self) -> None:
        assert _grade_score_ranges(
            _DEFAULT_GRADE_THRESHOLDS,
            {"EXCELLENT", "GOOD", "FAIR", "WARNING", "POOR"},
        ) == [(None, None)]

    def test_custom_thresholds_lowest_fallback(self) -> None:
        """自定义阈值最低档 min>0：低于全部阈值仍归最低档（前端回退语义）。"""
        custom = [{"name": "GOOD", "minScore": 70}, {"name": "POOR", "minScore": 30}]
        assert _grade_score_ranges(custom, {"POOR"}) == [(None, 70.0)]


# ===========================================================================
# grades 参数（SQL 下推 + 互斥校验）
# ===========================================================================


class TestGradesParam:
    @pytest.mark.asyncio
    async def test_grades_predicates_pushed_to_snapshot_subquery(self) -> None:
        """GOOD+FAIR → 合并区间 [60, 90) 下推到最新快照子查询（半开区间）。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _count_mock(1),
                _rows_mock([_loop("loop-001")]),
                _rows_mock([]),  # unit
                _rows_mock([]),  # mappings
                _rows_mock([]),  # tags
                _rows_mock([]),  # snapshots
                _rows_mock([]),  # prev snapshots
                _rows_mock([]),  # mode mappings
            ]
        )
        with (
            patch(
                "app.services.monitor.get_subscriber",
                return_value=_subscriber_mock(),
            ),
            patch(
                "app.services.monitor._load_aggregate_grading_thresholds",
                AsyncMock(return_value=list(_DEFAULT_GRADE_THRESHOLDS)),
            ),
        ):
            result = await list_loop_monitor(db, grades="GOOD,FAIR", with_aggregate=False)
        assert result["total"] == 1
        # 主列表查询 SQL：grade_snap 子查询 + 区间谓词
        sqls = [str(c.args[0]) for c in db.execute.await_args_list]
        main_sql = " ".join(sqls)
        assert "grade_snap" in main_sql
        assert "score >= :score_1" in main_sql
        assert "score < :score_2" in main_sql
        compiled = str(
            db.execute.await_args_list[1].args[0].compile(compile_kwargs={"literal_binds": True})
        )
        assert "score >= 60" in compiled
        assert "score < 90" in compiled

    @pytest.mark.asyncio
    async def test_grades_rejects_unknown_key(self) -> None:
        with pytest.raises(BizError) as exc:
            await list_loop_monitor(AsyncMock(), grades="SUPERB")
        assert exc.value.code == "ERR_VALIDATION"

    @pytest.mark.asyncio
    async def test_grades_conflicts_with_score_range(self) -> None:
        with pytest.raises(BizError) as exc:
            await list_loop_monitor(AsyncMock(), grades="GOOD", min_score=60)
        assert "互斥" in str(exc.value.message)

    @pytest.mark.asyncio
    async def test_grades_conflicts_with_unscored(self) -> None:
        with pytest.raises(BizError):
            await list_loop_monitor(AsyncMock(), grades="GOOD", unscored=True)


# ===========================================================================
# modes 参数（原始值分桶 + scope 收窄保持）
# ===========================================================================


class TestModesParam:
    @pytest.mark.asyncio
    async def test_filter_by_mode_values_redis_first(self) -> None:
        """AUTO/CAS 分桶：原始值 1/2 命中，0/未知/无值不命中（Redis 优先）。"""
        db = AsyncMock()
        mapping_rows = [
            (loop_id, f"TAG-{i}", None) for i, loop_id in enumerate(["A", "B", "C", "D", "E"])
        ]
        db.execute = AsyncMock(return_value=_rows_mock(mapping_rows))
        cached = [
            {"tagCode": "TAG-0", "value": "1"},  # AUTO 命中
            {"tagCode": "TAG-1", "value": "2"},  # CAS 命中
            {"tagCode": "TAG-2", "value": "0"},  # MANUAL 不选
            {"tagCode": "TAG-3", "value": "9"},  # 未知值不命中
            # TAG-4 无缓存且 PG current_value=None → 不命中
        ]
        with patch(
            "app.services.monitor.get_subscriber",
            return_value=_subscriber_mock(cached),
        ):
            matched = await _filter_loop_ids_by_mode_values(db, list("ABCDE"), {1, 2})
        assert matched == ["A", "B"]

    @pytest.mark.asyncio
    async def test_filter_by_mode_values_pg_fallback(self) -> None:
        """Redis 不可用回退 PG current_value（异常被吞、值仍生效）。"""
        db = AsyncMock()
        mapping_rows = [("A", "TAG-A", 3.0), ("B", "TAG-B", 0.0)]
        db.execute = AsyncMock(return_value=_rows_mock(mapping_rows))
        broken = MagicMock()
        broken.get_cached_values = AsyncMock(side_effect=RuntimeError("redis down"))
        with patch(
            "app.services.monitor.get_subscriber",
            return_value=broken,
        ):
            matched = await _filter_loop_ids_by_mode_values(db, ["A", "B"], {3})
        assert matched == ["A"]

    @pytest.mark.asyncio
    async def test_modes_candidate_set_narrowed_by_scope(self) -> None:
        """批 α 收窄保持：modes 候选集先与 scope 交集（SQL 含 unit_id IN）。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _rows_mock([]),  # 候选 id 查询（范围收窄后为空 → 空结果短路）
            ]
        )
        with patch(
            "app.services.monitor.get_subscriber",
            return_value=_subscriber_mock(),
        ):
            result = await list_loop_monitor(
                db,
                scope_node_ids=["uA1", "uA2"],
                modes="AUTO",
                with_aggregate=False,
            )
        assert result["total"] == 0
        assert result["items"] == []
        assert result["focusPage"] is None
        compiled = str(
            db.execute.await_args_list[0].args[0].compile(compile_kwargs={"literal_binds": True})
        ).upper()
        assert "UNIT_ID IN" in compiled

    @pytest.mark.asyncio
    async def test_modes_rejects_unknown_key(self) -> None:
        with pytest.raises(BizError) as exc:
            await list_loop_monitor(AsyncMock(), modes="TURBO")
        assert exc.value.code == "ERR_VALIDATION"

    @pytest.mark.asyncio
    async def test_modes_conflicts_with_control_mode(self) -> None:
        with pytest.raises(BizError):
            await list_loop_monitor(AsyncMock(), modes="AUTO", control_mode="Auto")


# ===========================================================================
# sortBy=scoreDelta（预计算列 + NULLS LAST + 稳定次级键）
# ===========================================================================


class TestScoreDeltaSort:
    @pytest.mark.asyncio
    async def test_score_delta_order_uses_wll_column_nulls_last(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _count_mock(0),
                _rows_mock([]),  # 主列表（空）
            ]
        )
        await list_loop_monitor(db, sort_by="scoreDelta", sort_order="asc", with_aggregate=False)
        compiled = str(
            db.execute.await_args_list[1].args[0].compile(compile_kwargs={"literal_binds": True})
        ).upper()
        assert "SCORE_DELTA" in compiled
        assert "NULLS LAST" in compiled
        assert "WLL_LATEST" in compiled  # 外连接预计算表

    @pytest.mark.asyncio
    async def test_invalid_sort_by_rejected(self) -> None:
        with pytest.raises(BizError):
            await list_loop_monitor(AsyncMock(), sort_by="degrade")


# ===========================================================================
# focusLoopId（位次 / 所在页）
# ===========================================================================


class TestFocusLoopId:
    @pytest.mark.asyncio
    async def test_focus_rank_and_page(self) -> None:
        """位次与页码：page_size=2、目标在第 3 位 → focusIndex=2 / focusPage=2。"""
        ids = [(_UUID_A,), (_UUID_B,), (_UUID_C,)]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _count_mock(3),
                _rows_mock([_loop(_UUID_C)]),  # 当前页数据
                _rows_mock(ids),  # focus 位次 id 查询（主列表后立即执行）
                _rows_mock([]),  # unit
                _rows_mock([]),  # mappings
                _rows_mock([]),  # tags
                _rows_mock([]),  # snapshots
                _rows_mock([]),  # prev snapshots
                _rows_mock([]),  # mode mappings
            ]
        )
        with patch(
            "app.services.monitor.get_subscriber",
            return_value=_subscriber_mock(),
        ):
            result = await list_loop_monitor(
                db,
                focus_loop_id=_UUID_C,
                page=1,
                page_size=2,
                with_aggregate=False,
            )
        assert result["focusIndex"] == 2
        assert result["focusPage"] == 2

    @pytest.mark.asyncio
    async def test_focus_not_in_result_set_returns_none(self) -> None:
        ids = [(_UUID_A,), (_UUID_B,)]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _count_mock(2),
                _rows_mock([_loop(_UUID_A)]),
                _rows_mock(ids),  # focus 位次 id 查询
                _rows_mock([]),
                _rows_mock([]),
                _rows_mock([]),
                _rows_mock([]),
                _rows_mock([]),
                _rows_mock([]),
            ]
        )
        with patch(
            "app.services.monitor.get_subscriber",
            return_value=_subscriber_mock(),
        ):
            result = await list_loop_monitor(db, focus_loop_id=_UUID_C, with_aggregate=False)
        assert result["focusIndex"] is None
        assert result["focusPage"] is None

    @pytest.mark.asyncio
    async def test_invalid_focus_uuid_rejected(self) -> None:
        with pytest.raises(BizError) as exc:
            await list_loop_monitor(AsyncMock(), focus_loop_id="not-a-uuid")
        assert exc.value.code == "ERR_VALIDATION"

    @pytest.mark.asyncio
    async def test_no_focus_returns_null_fields(self) -> None:
        """未传 focusLoopId：响应仍携带 focusIndex/focusPage=null（形状稳定）。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _count_mock(0),
                _rows_mock([]),
            ]
        )
        result = await list_loop_monitor(db, with_aggregate=False)
        assert result["focusIndex"] is None
        assert result["focusPage"] is None


# ===========================================================================
# compute_score_delta（刷新任务预计算列）
# ===========================================================================


class TestComputeScoreDelta:
    def test_decimal_subtraction(self) -> None:
        assert compute_score_delta(Decimal("78.50"), Decimal("82.00")) == Decimal("-3.50")

    def test_none_inputs(self) -> None:
        assert compute_score_delta(None, Decimal("82.00")) is None
        assert compute_score_delta(Decimal("78.50"), None) is None
        assert compute_score_delta(None, None) is None
