"""参评口径统一（2026-10-10）结构性测试。

背景：停用联动置「不参评」且复用不自动恢复，而回路级链路（KPI 小时任务、
等级分布、fitness 门禁、回路页评分）此前不滤参评——切换参评状态后回路级
面与聚合面（雷达/排名/趋势，只聚合参评回路）数字互相矛盾，且不参评回路
以停用前旧快照继续展示评分。本批统一：全量评估只算参评回路，当前态统计
/榜单/评分展示同批过滤（历史快照模式完整呈现，不滤参评）。

覆盖：
- kpi_calc._eval_loop_selection_stmt：全量（loop_ids=None）滤参评，
  显式回路列表不滤（尊重手动任务/backfill 精准重算意图）
- workbench_loop_latest 预计算 SQL / workbench_diagnosis fitness 分母：
  仅参评回路
- performance/dashboard 快照过滤构造器：排除不参评回路旧快照
- update_loop：停用/启用/参评切换 → 驾驶舱聚合缓存即时失效（进程+Redis）
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestEvalLoopSelection:
    """kpi_calc 评估选路：全量仅参评，显式回路不滤。"""

    def test_full_sweep_filters_participation(self) -> None:
        from sqlalchemy import select

        from app.models.loop import LoopLedger
        from app.tasks.kpi_calc import _eval_loop_selection_stmt

        stmt = _eval_loop_selection_stmt(None)
        sql = str(
            select(LoopLedger.id)
            .where(stmt.whereclause)
            .compile(compile_kwargs={"literal_binds": False})
        ).upper()
        assert "IS_ACTIVE" in sql
        assert "INCLUDE_IN_EVALUATION" in sql

    def test_explicit_loop_ids_skip_participation_filter(self) -> None:
        from sqlalchemy import select

        from app.models.loop import LoopLedger
        from app.tasks.kpi_calc import _eval_loop_selection_stmt

        stmt = _eval_loop_selection_stmt(["loop-1", "loop-2"])
        sql = str(
            select(LoopLedger.id)
            .where(stmt.whereclause)
            .compile(compile_kwargs={"literal_binds": False})
        ).upper()
        # 显式指定回路：无参评过滤（手动任务意图优先）
        assert "INCLUDE_IN_EVALUATION" not in sql
        assert "IS_ACTIVE" in sql


class TestPrecalcAndGatesParticipation:
    """预计算表 / fitness 门禁分母仅计参评回路。"""

    def test_wll_latest_snapshot_sql_filters_participation(self) -> None:
        from app.services.workbench_loop_latest import _LATEST_SNAPSHOT_SQL

        assert "include_in_evaluation" in _LATEST_SNAPSHOT_SQL

    @pytest.mark.anyio
    async def test_scope_loop_ids_filters_participation(self) -> None:
        from app.services.workbench_diagnosis import _query_scope_loop_ids

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_rows_mock([]))
        await _query_scope_loop_ids(db, None)
        sql = str(db.execute.call_args[0][0]).lower()
        assert "is_active = true" in sql
        assert "include_in_evaluation = true" in sql


class TestSnapshotFilterConstructors:
    """快照过滤构造器排除不参评回路（当前态统计/榜单）。"""

    def test_performance_apply_snapshot_filters(self) -> None:
        from sqlalchemy import select

        from app.models.metric import KpiSnapshotHourly
        from app.services.performance import _apply_snapshot_filters

        stmt = _apply_snapshot_filters(select(KpiSnapshotHourly.id))
        sql = str(stmt.compile(compile_kwargs={"literal_binds": False})).upper()
        assert "IS_ACTIVE" in sql
        assert "INCLUDE_IN_EVALUATION" in sql

    def test_dashboard_apply_snapshot_filters(self) -> None:
        from sqlalchemy import select

        from app.models.metric import KpiSnapshotHourly
        from app.services.dashboard import _apply_snapshot_filters

        stmt = _apply_snapshot_filters(select(KpiSnapshotHourly.id))
        sql = str(stmt.compile(compile_kwargs={"literal_binds": False})).upper()
        assert "IS_ACTIVE" in sql
        assert "INCLUDE_IN_EVALUATION" in sql

    def test_monitor_eval_participant_ids(self) -> None:
        from sqlalchemy import select

        from app.services.monitor import _eval_participant_ids

        sql = str(
            select(_eval_participant_ids().subquery()).compile(
                compile_kwargs={"literal_binds": False}
            )
        ).upper()
        assert "INCLUDE_IN_EVALUATION" in sql


class TestLoopToggleCacheInvalidation:
    """停用/启用/参评切换 → 驾驶舱聚合缓存即时失效（进程 + Redis 双端）。"""

    @staticmethod
    def _make_loop(is_active: bool = True, include_in_evaluation: bool = True) -> MagicMock:
        loop = MagicMock()
        loop.id = "00000000-0000-0000-0000-000000000001"
        loop.tag_name = "90PIC0001"
        loop.description = "desc"
        loop.unit_id = None
        loop.score_weights = None
        loop.is_active = is_active
        loop.status = "READY"
        loop.loop_type = "TEMPERATURE"
        loop.control_type = "STABLE"
        loop.importance_level = 2
        loop.include_in_evaluation = include_in_evaluation
        loop.modeattr_tag_id = None
        loop.data_retention_days = None
        loop.op_output_lower_limit = None
        loop.op_output_upper_limit = None
        loop.dcs_model_id = None
        loop.ideal_settling_time = None
        loop.remark = None
        loop.complex_loop_group_id = None
        loop.complex_role = None
        loop.updated_at = None
        loop.updated_by = "admin"
        return loop

    @staticmethod
    def _mock_db(loop: MagicMock) -> AsyncMock:
        db = AsyncMock()
        first = MagicMock()
        first.scalar_one_or_none.return_value = loop
        op_range = MagicMock()
        op_range.first.return_value = None
        mappings = MagicMock()
        mappings.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(side_effect=[first, op_range, mappings])
        return db

    async def _run_update(self, loop: MagicMock, **kwargs) -> AsyncMock:
        """在补丁环境内执行 update_loop，返回 invalidate_agg_async 的 mock。"""
        from contextlib import ExitStack

        from app.services.loop import update_loop

        db = self._mock_db(loop)
        inval = AsyncMock()
        with ExitStack() as stack:
            stack.enter_context(
                patch("app.services.loop.derive_loop_status", new=AsyncMock(return_value="READY"))
            )
            stack.enter_context(
                patch(
                    "app.services.alert_rule_engine.service.acknowledge_loop_active_events",
                    new=AsyncMock(return_value=0),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.data_source.realtime_subscriber.notify_subscription_changed",
                    new=AsyncMock(),
                )
            )
            stack.enter_context(patch("app.services.loop._write_audit", new=AsyncMock()))
            stack.enter_context(patch("app.services.agg_cache.invalidate_agg_async", new=inval))
            await update_loop(db, str(loop.id), operator="admin", **kwargs)
        return inval

    @pytest.mark.anyio
    async def test_disable_invalidates_cockpit_cache(self) -> None:
        loop = self._make_loop(is_active=True, include_in_evaluation=True)
        inval = await self._run_update(loop, is_active=False)
        prefixes = [c.args[0] for c in inval.await_args_list]
        assert "cockpit-overview" in prefixes
        assert "board-trend" in prefixes
        assert loop.is_active is False
        # 停用联动：置不参评
        assert loop.include_in_evaluation is False

    @pytest.mark.anyio
    async def test_enable_invalidates_cockpit_cache(self) -> None:
        loop = self._make_loop(is_active=False, include_in_evaluation=False)
        inval = await self._run_update(loop, is_active=True, include_in_evaluation=True)
        assert inval.await_count >= 2
        assert loop.is_active is True
        assert loop.include_in_evaluation is True

    @pytest.mark.anyio
    async def test_unrelated_update_skips_invalidation(self) -> None:
        loop = self._make_loop()
        inval = await self._run_update(loop, remark="仅改备注")
        inval.assert_not_awaited()


def _make_rows_mock(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result
