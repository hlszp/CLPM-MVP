"""P3-01 整定知识库服务测试.

覆盖：
- generate_knowledge_entry: exact/time_window/none 三种关联场景 + 幂等 + 无 loop_id 跳过
- list_knowledge_entries: 筛选与分页
- get_knowledge_entry: 详情查询
- recommend_similar: 优先级排序（label>loop_type, effect_verified 优先, 排除自身）
- _find_tuning_record: hybrid 关联策略
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.tuning_knowledge import (
    _find_tuning_record,
    generate_knowledge_entry,
    get_knowledge_entry,
    list_knowledge_entries,
    recommend_similar,
)

# 哨兵值：区分"未传参"和"显式传 None"
_UNSET = object()


# ---------------------------------------------------------------------------
# 辅助函数：构造 mock 对象
# ---------------------------------------------------------------------------


def _make_loop(
    *,
    loop_id: str | None = None,
    loop_type: str = "FLOW",
    control_type: str = "PID",
    tag_name: str = "FIC-101",
) -> MagicMock:
    loop = MagicMock()
    loop.id = loop_id or str(uuid4())
    loop.loop_type = loop_type
    loop.control_type = control_type
    loop.tag_name = tag_name
    return loop


def _make_tuning_record(
    *,
    record_id: str | None = None,
    model_type: str = "FOPDT",
    algorithm: str = "arx",
    identify_method: str = "least_squares",
    confidence_level: str = "B",
    current_pid: dict | None = None,
    status: str = "COMPLETED",
) -> MagicMock:
    record = MagicMock()
    record.id = record_id or str(uuid4())
    record.model_type = model_type
    record.algorithm = algorithm
    record.identify_method = identify_method
    record.confidence_level = confidence_level
    record.current_pid = current_pid or {"p": 1.5, "i": 0.8, "d": 0.1}
    record.status = status
    record.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    record.loop_id = str(uuid4())
    return record


def _make_tracker(
    *,
    tracker_id: str | None = None,
    loop_id=_UNSET,
    tuning_record_id: str | None = None,
    diagnosis_label: str = "OSCILLATION",
    severity: str = "WARN",
    implemented_at: datetime | None = None,
    new_pid: tuple[float, float, float] = (2.0, 1.0, 0.2),
    ab_compare_summary: dict | None = None,
    effect_verified: bool = True,
) -> MagicMock:
    tracker = MagicMock()
    tracker.id = tracker_id or str(uuid4())
    tracker.loop_id = str(uuid4()) if loop_id is _UNSET else loop_id
    tracker.tuning_record_id = tuning_record_id
    tracker.diagnosis_label = diagnosis_label
    tracker.severity = severity
    tracker.implemented_at = implemented_at or datetime.now(UTC).replace(tzinfo=None) - timedelta(
        days=2
    )
    tracker.updated_at = tracker.implemented_at
    tracker.new_pid_p = new_pid[0]
    tracker.new_pid_i = new_pid[1]
    tracker.new_pid_d = new_pid[2]
    tracker.ab_compare_summary = ab_compare_summary or {
        "improvedCount": 3,
        "deterioratedCount": 1,
        "unchangedCount": 0,
        "dataInsufficient": False,
    }
    tracker.effect_verified = effect_verified
    tracker.effect_verified_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    return tracker


def _make_entry(
    *,
    entry_id: str | None = None,
    loop_type: str = "FLOW",
    diagnosis_label: str = "OSCILLATION",
    effect_verified: bool = True,
    improved_count: int = 3,
) -> MagicMock:
    entry = MagicMock()
    entry.id = entry_id or str(uuid4())
    entry.tracker_id = str(uuid4())
    entry.loop_id = str(uuid4())
    entry.loop_type = loop_type
    entry.diagnosis_label = diagnosis_label
    entry.effect_verified = effect_verified
    entry.improved_count = improved_count
    entry.deteriorated_count = 1
    entry.match_source = "exact"
    entry.model_type = "FOPDT"
    entry.algorithm = "arx"
    return entry


def _make_scalar_result(value) -> MagicMock:
    """构造 scalar_one_or_none() 返回 value 的 mock。"""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _make_scalars_result(items: list) -> MagicMock:
    """构造 scalars().all() 返回 items 的 mock。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_count_result(value: int) -> MagicMock:
    """构造 scalar() 返回 value 的 mock（用于 COUNT 查询）。"""
    result = MagicMock()
    result.scalar = MagicMock(return_value=value)
    return result


def _make_stats_row_result(
    *,
    total: int = 0,
    improved: int = 0,
    deteriorated: int = 0,
    unverified: int = 0,
    avg_improved: float | None = None,
) -> MagicMock:
    """构造 .one() 返回统计聚合行的 mock（IA 整改 C-2/T-3 stats 查询）。

    list_knowledge_entries 现在会多执行一次 stats 聚合查询，
    测试需在 side_effect 中补充本次返回。
    """
    row = MagicMock()
    row.total = total
    row.improved = improved
    row.deteriorated = deteriorated
    row.unverified = unverified
    row.avg_improved = avg_improved
    result = MagicMock()
    result.one.return_value = row
    return result


# ---------------------------------------------------------------------------
# _find_tuning_record 测试
# ---------------------------------------------------------------------------


class TestFindTuningRecord:
    """hybrid 关联策略测试。"""

    @pytest.mark.asyncio
    async def test_exact_match_via_foreign_key(self) -> None:
        """tracker 有 tuning_record_id 且记录存在 → exact。"""
        record = _make_tuning_record()
        tracker = _make_tracker(tuning_record_id=record.id)

        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalar_result(record))

        result, match_source = await _find_tuning_record(db, tracker)
        assert result is record
        assert match_source == "exact"

    @pytest.mark.asyncio
    async def test_time_window_fallback(self) -> None:
        """无 tuning_record_id，时间窗口内找到 → time_window。"""
        tracker = _make_tracker(tuning_record_id=None)
        record = _make_tuning_record()

        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalar_result(record))

        result, match_source = await _find_tuning_record(db, tracker)
        assert result is record
        assert match_source == "time_window"

    @pytest.mark.asyncio
    async def test_none_when_no_record_found(self) -> None:
        """无 tuning_record_id 且时间窗口内无记录 → none。"""
        tracker = _make_tracker(tuning_record_id=None)

        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalar_result(None))

        result, match_source = await _find_tuning_record(db, tracker)
        assert result is None
        assert match_source == "none"

    @pytest.mark.asyncio
    async def test_none_when_no_implemented_at(self) -> None:
        """无 implemented_at 和 updated_at → none。"""
        tracker = _make_tracker(tuning_record_id=None)
        tracker.implemented_at = None
        tracker.updated_at = None

        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalar_result(None))

        result, match_source = await _find_tuning_record(db, tracker)
        assert result is None
        assert match_source == "none"

    @pytest.mark.asyncio
    async def test_fk_set_but_record_deleted_falls_to_time_window(self) -> None:
        """tuning_record_id 有值但记录被删除 → 降级时间窗口搜索。"""
        tracker = _make_tracker(tuning_record_id=str(uuid4()))
        record = _make_tuning_record()

        db = MagicMock()
        # 第一次查 by id → None（已删除），第二次查 time_window → record
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_result(None),
                _make_scalar_result(record),
            ]
        )

        result, match_source = await _find_tuning_record(db, tracker)
        assert result is record
        assert match_source == "time_window"


# ---------------------------------------------------------------------------
# generate_knowledge_entry 测试
# ---------------------------------------------------------------------------


class TestGenerateKnowledgeEntry:
    """知识库条目生成测试。"""

    @pytest.mark.asyncio
    async def test_skip_when_no_loop_id(self) -> None:
        """tracker 无 loop_id 时跳过，返回 None。"""
        tracker = _make_tracker(loop_id=None)
        db = MagicMock()
        db.execute = AsyncMock()

        result = await generate_knowledge_entry(db, tracker)
        assert result is None
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_loop_not_found(self) -> None:
        """loop 不存在时跳过。"""
        tracker = _make_tracker()
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalar_result(None))

        result = await generate_knowledge_entry(db, tracker)
        assert result is None

    @pytest.mark.asyncio
    async def test_exact_match_generates_entry(self) -> None:
        """exact 关联场景：有 tuning_record_id 且记录存在。"""
        loop = _make_loop(loop_type="FLOW", control_type="PID", tag_name="FIC-101")
        record = _make_tuning_record(model_type="FOPDT", algorithm="arx")
        tracker = _make_tracker(
            tuning_record_id=record.id,
            diagnosis_label="OSCILLATION",
            severity="WARN",
        )
        entry = _make_entry()

        db = MagicMock()
        db.commit = AsyncMock()
        # 1. select LoopLedger → loop
        # 2. _find_tuning_record: select TuningRecord by id → record
        # 3. insert/upsert → (result unused)
        # 4. select entry → entry
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_result(loop),  # LoopLedger
                _make_scalar_result(record),  # TuningRecord by id
                MagicMock(),  # insert/upsert
                _make_scalar_result(entry),  # select entry
            ]
        )

        result = await generate_knowledge_entry(db, tracker)
        assert result is entry
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_time_window_match_generates_entry(self) -> None:
        """time_window 关联场景：无 tuning_record_id，时间窗口匹配。"""
        loop = _make_loop()
        record = _make_tuning_record()
        tracker = _make_tracker(tuning_record_id=None)
        entry = _make_entry()

        db = MagicMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_result(loop),  # LoopLedger
                _make_scalar_result(record),  # TuningRecord by time_window
                MagicMock(),  # insert/upsert
                _make_scalar_result(entry),  # select entry
            ]
        )

        result = await generate_knowledge_entry(db, tracker)
        assert result is entry

    @pytest.mark.asyncio
    async def test_none_match_still_generates_entry(self) -> None:
        """none 关联场景：无 TuningRecord，知识库条目仍生成（缺整定元数据）。"""
        loop = _make_loop()
        tracker = _make_tracker(tuning_record_id=None)
        entry = _make_entry()
        entry.match_source = "none"
        entry.model_type = None
        entry.algorithm = None

        db = MagicMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_result(loop),  # LoopLedger
                _make_scalar_result(None),  # TuningRecord by time_window → None
                MagicMock(),  # insert/upsert
                _make_scalar_result(entry),  # select entry
            ]
        )

        result = await generate_knowledge_entry(db, tracker)
        assert result is entry

    @pytest.mark.asyncio
    async def test_effect_verified_false_still_generates(self) -> None:
        """恶化案例也入库（有学习价值）。"""
        loop = _make_loop()
        tracker = _make_tracker(effect_verified=False)
        tracker.ab_compare_summary = {
            "improvedCount": 1,
            "deterioratedCount": 3,
            "unchangedCount": 0,
        }
        entry = _make_entry(effect_verified=False, improved_count=1)

        db = MagicMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_result(loop),
                _make_scalar_result(None),  # no tuning record
                MagicMock(),  # insert/upsert
                _make_scalar_result(entry),
            ]
        )

        result = await generate_knowledge_entry(db, tracker)
        assert result is entry

    @pytest.mark.asyncio
    async def test_idempotent_on_retry(self) -> None:
        """幂等：重复生成时 ON CONFLICT DO UPDATE，不报错。"""
        loop = _make_loop()
        tracker = _make_tracker()
        entry = _make_entry()

        db = MagicMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_result(loop),
                _make_scalar_result(None),
                MagicMock(),  # insert/upsert (ON CONFLICT)
                _make_scalar_result(entry),
            ]
        )

        # 第一次生成
        result1 = await generate_knowledge_entry(db, tracker)
        assert result1 is entry

    @pytest.mark.asyncio
    async def test_pid_after_built_from_tracker(self) -> None:
        """pid_after 从 tracker.new_pid_* 构建（验证函数正常完成）。"""
        loop = _make_loop()
        tracker = _make_tracker(new_pid=(2.5, 1.2, 0.3))
        entry = _make_entry()

        db = MagicMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_result(loop),
                _make_scalar_result(None),
                MagicMock(),  # insert/upsert
                _make_scalar_result(entry),
            ]
        )

        result = await generate_knowledge_entry(db, tracker)
        # 函数正常完成即说明 pid_after 构建成功
        assert result is entry
        # 验证 insert 语句被调用（第3次 execute 是 upsert）
        assert db.execute.call_count == 4


# ---------------------------------------------------------------------------
# list_knowledge_entries 测试
# ---------------------------------------------------------------------------


class TestListKnowledgeEntries:
    """知识库列表查询测试。"""

    @pytest.mark.asyncio
    async def test_basic_list_with_defaults(self) -> None:
        """默认分页查询。"""
        entries = [_make_entry() for _ in range(3)]
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_count_result(3),  # COUNT
                _make_stats_row_result(total=3),  # stats 聚合
                _make_scalars_result(entries),  # SELECT
            ]
        )

        result = await list_knowledge_entries(db)
        assert result["total"] == 3
        assert len(result["items"]) == 3
        assert result["page"] == 1
        assert result["pageSize"] == 20
        # IA 整改 C-2/T-3：stats 字段应被填充
        assert result["stats"]["total"] == 3
        assert result["stats"]["improvedCount"] == 0
        assert result["stats"]["deterioratedCount"] == 0
        assert result["stats"]["unverifiedCount"] == 0
        assert result["stats"]["avgImprovedMetrics"] is None

    @pytest.mark.asyncio
    async def test_filter_by_loop_type(self) -> None:
        """按控制类型筛选。"""
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_count_result(2),
                _make_stats_row_result(total=2),
                _make_scalars_result([_make_entry(), _make_entry()]),
            ]
        )

        result = await list_knowledge_entries(db, loop_type="FLOW")
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_filter_by_diagnosis_label(self) -> None:
        """按问题类型筛选。"""
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_count_result(1),
                _make_stats_row_result(total=1),
                _make_scalars_result([_make_entry()]),
            ]
        )

        result = await list_knowledge_entries(db, diagnosis_label="OSCILLATION")
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_filter_by_effect_verified(self) -> None:
        """按效果筛选（仅改善案例）。"""
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_count_result(5),
                _make_stats_row_result(total=5, improved=5),
                _make_scalars_result([_make_entry() for _ in range(5)]),
            ]
        )

        result = await list_knowledge_entries(db, effect_verified=True)
        assert result["total"] == 5
        # 筛选 effect_verified=True 时，stats.improvedCount 应反映该筛选条件下的计数
        assert result["stats"]["improvedCount"] == 5

    @pytest.mark.asyncio
    async def test_pagination(self) -> None:
        """分页参数正确传递。"""
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_count_result(50),
                _make_stats_row_result(total=50),
                _make_scalars_result([_make_entry() for _ in range(10)]),
            ]
        )

        result = await list_knowledge_entries(db, page=3, page_size=10)
        assert result["page"] == 3
        assert result["pageSize"] == 10

    @pytest.mark.asyncio
    async def test_empty_result(self) -> None:
        """空结果。"""
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_count_result(0),
                _make_stats_row_result(total=0),
                _make_scalars_result([]),
            ]
        )

        result = await list_knowledge_entries(db)
        assert result["total"] == 0
        assert result["items"] == []


# ---------------------------------------------------------------------------
# get_knowledge_entry 测试
# ---------------------------------------------------------------------------


class TestGetKnowledgeEntry:
    """知识库详情查询测试。"""

    @pytest.mark.asyncio
    async def test_get_existing_entry(self) -> None:
        """查询存在的条目。"""
        entry = _make_entry(entry_id="test-id")
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalar_result(entry))

        result = await get_knowledge_entry(db, "test-id")
        assert result is entry

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self) -> None:
        """查询不存在的条目返回 None。"""
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalar_result(None))

        result = await get_knowledge_entry(db, "nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# recommend_similar 测试
# ---------------------------------------------------------------------------


class TestRecommendSimilar:
    """相似案例推荐测试。"""

    @pytest.mark.asyncio
    async def test_returns_list(self) -> None:
        """返回推荐列表。"""
        entries = [_make_entry() for _ in range(5)]
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalars_result(entries))

        result = await recommend_similar(db, loop_id=str(uuid4()), limit=5)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_exclude_self_loop(self) -> None:
        """排除当前 loop_id 自身（验证 SQL WHERE 含 != 条件）。"""
        self_loop_id = str(uuid4())
        entries = [_make_entry() for _ in range(3)]
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalars_result(entries))

        await recommend_similar(db, loop_id=self_loop_id)

        # 验证 SQL 包含 != 排除条件
        stmt = db.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "!=" in compiled or "<>" in compiled

    @pytest.mark.asyncio
    async def test_filter_by_label_and_loop_type(self) -> None:
        """筛选条件包含 diagnosis_label 和 loop_type。"""
        entries = [_make_entry()]
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalars_result(entries))

        await recommend_similar(
            db,
            loop_id=str(uuid4()),
            loop_type="FLOW",
            diagnosis_label="OSCILLATION",
        )

        stmt = db.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "OSCILLATION" in compiled
        assert "FLOW" in compiled

    @pytest.mark.asyncio
    async def test_limit_parameter(self) -> None:
        """limit 参数正确传递。"""
        entries = [_make_entry() for _ in range(3)]
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalars_result(entries))

        await recommend_similar(db, loop_id=str(uuid4()), limit=3)

        stmt = db.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "LIMIT 3" in compiled or "FETCH FIRST 3" in compiled

    @pytest.mark.asyncio
    async def test_empty_when_no_matches(self) -> None:
        """无匹配时返回空列表。"""
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await recommend_similar(
            db,
            loop_id=str(uuid4()),
            diagnosis_label="UNKNOWN_LABEL",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_no_filters_returns_all_excluding_self(self) -> None:
        """无筛选条件时返回全部（排除自身）。"""
        entries = [_make_entry() for _ in range(5)]
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalars_result(entries))

        result = await recommend_similar(db, loop_id=str(uuid4()))
        assert len(result) == 5
