"""P5-02 批量 IO、队列与计算预算单元测试（批 β）.

覆盖《03-阶段任务计划》§8 P5-02 卡可单测部分 + 10 号文等价基线增量：

1. **等价基线**：批级 runtime_overrides 预载与逐回路加载同值同 digest——
   ``_calculate_loop_kpi`` 两条路径同输入结果恒等（不改分值/参考状态），
   configRevision 摘要恒等。
2. **IO 计数**：回路级元数据 IO 从 2 次（fitness LIKE + kpi 观测阈值单键）
   降为 0（批级共享）；TEMPLATE 键批级 1 次查询替代逐回路 N 次。
3. **attempt 表/FAILED 终态**（G2 遗留③，P0-02 冻结 v2 §2.2）：attempt
   序号推导、终末判定、FAILED record 追加（status/payload 错误摘要）、
   显式重评链 sourceRecordId 自动回链、3 次 attempt 上限（max_retries=2）。
4. **队列优先级**：Redis 低数值先消费：EVENT(0)、MANUAL(3)、NIGHTLY(9)，default 队列
   max_priority、beat 条目夜间优先级、派发点接线断言。

IO/队列峰值实测归联测批（批 γ）；迁移/真实 PG 行为由 alembic check +
dev 库 upgrade 验证（见交接文件）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.sql.dml import Insert

from app.contracts.data_types import MetricResult
from app.models.calculation_result import CalculationResultAttempt
from app.services import result_ledger
from app.tasks import kpi_calc
from app.tasks.celery_app import (
    PRIORITY_EVENT,
    PRIORITY_MANUAL,
    PRIORITY_NIGHTLY,
    celery_app,
)

_TS0 = datetime(2026, 10, 13, 8, 0, 0, tzinfo=UTC)
_TS1 = _TS0 + timedelta(hours=1)

_FITNESS_ROWS = [
    SimpleNamespace(key="fitness.manual_dominant_pct", value="80"),
    SimpleNamespace(key="fitness.low_auto_rate_pct", value="20"),
]


# ---------------------------------------------------------------------------
# mock db：按语句分派（sys_config 双查询按 whereclause 字面值区分）
# ---------------------------------------------------------------------------


def _result(**kw):
    r = MagicMock()
    for name, value in kw.items():
        getattr(r, name).return_value = value
    return r


class _DispatchDB:
    """按语句分派的 db mock：记录查询计数并返回各路径期望的结果形状."""

    def __init__(self) -> None:
        self.execute = AsyncMock(side_effect=self._dispatch)
        self.add = MagicMock()
        self.rollback = AsyncMock()
        self.commit = AsyncMock()
        self.calls: list[object] = []
        # 计数器（按表名）
        self.counts: dict[str, int] = {}

    async def _dispatch(self, stmt, *args, **kwargs):  # noqa: ANN001
        self.calls.append(stmt)
        if isinstance(stmt, Insert):
            table = stmt.table.name
            self.counts[table] = self.counts.get(table, 0) + 1
            if table == "kpi_snapshot_hourly":
                return _result(first=("snap-hourly-id",))
            # calculation_result_record insert RETURNING → None（并发竞态回读也 None）
            return _result(scalar_one_or_none=None)

        tables = {f.name for f in stmt.get_final_froms()}
        for table in tables:
            self.counts[table] = self.counts.get(table, 0) + 1
        if "sys_config" in tables:
            # 区分 fitness LIKE 查询与 kpi.min_observed_ratio 单键查询：
            # 按 whereclause 右值字面量分派（同款 mock 两轮行为必须一致）
            for crit in getattr(stmt, "_where_criteria", ()):
                literal = getattr(getattr(crit, "right", None), "value", None)
                if isinstance(literal, str) and literal.startswith("fitness."):
                    scalars = MagicMock()
                    scalars.all.return_value = list(_FITNESS_ROWS)
                    return _result(scalars=scalars)
            # min_observed_ratio：scalars().first() → 带 .value 的行
            scalars = MagicMock()
            scalars.first.return_value = SimpleNamespace(value="0.75")
            return _result(scalars=scalars)
        if "calculation_result_record" in tables:
            return _result(scalar_one_or_none=None)
        if "engineering_template" in tables:
            return _result(first=("tpl-00000000-0000-0000-0000-000000000001", "TPL_KEY_A"))
        return _result(scalar_one_or_none=None)

    def count_sys_config(self) -> int:
        return sum(
            1
            for stmt in self.calls
            if not isinstance(stmt, Insert)
            and "sys_config" in {f.name for f in stmt.get_final_froms()}
        )


class _SingleLoopRecalcDB(_DispatchDB):
    """REWORK-D1 场景 db mock：单回路重算入口的查询按语句形状分派.

    - ``loop_ledger`` 单行 → 待重算回路（scalar_one_or_none）
    - ``metric_config`` → 完整指标配置行（供 ``{c.metric_code.lower(): c}`` 重建）
    - ``calculation_result_record`` 按 select 列分派：
      ``object_id``（窗口级门查询 DISTINCT）→ ``scalars().all() = gate_ids``；
      ``id``（latest_failed_source_id 点查）→ ``scalar_one_or_none = point_id``；
      全实体（append 同键 SELECT / 冲突回读）→ None（不存在 → 走 INSERT）
    """

    def __init__(self, loop, gate_ids: set[str], point_id: str) -> None:  # noqa: ANN001
        super().__init__()
        self._loop = loop
        self._gate_ids = gate_ids
        self._point_id = point_id

    async def _dispatch(self, stmt, *args, **kwargs):  # noqa: ANN001
        from tests.test_kpi_calc import _make_full_metric_configs

        if isinstance(stmt, Insert):
            return await super()._dispatch(stmt, *args, **kwargs)
        tables = {f.name for f in stmt.get_final_froms()}
        if not ({"loop_ledger", "metric_config", "calculation_result_record"} & tables):
            return await super()._dispatch(stmt, *args, **kwargs)
        # 拦截分支自登记（与基类同口径：先 append 再计数，避免 super 重复登记）
        self.calls.append(stmt)
        for table in tables:
            self.counts[table] = self.counts.get(table, 0) + 1
        if "loop_ledger" in tables:
            return _result(scalar_one_or_none=self._loop)
        if "metric_config" in tables:
            scalars = MagicMock()
            scalars.all.return_value = list(_make_full_metric_configs().values())
            return _result(scalars=scalars)
        col_name = stmt.column_descriptions[0]["name"]
        if col_name == "object_id":
            scalars = MagicMock()
            scalars.all.return_value = list(self._gate_ids)
            return _result(scalars=scalars)
        if col_name == "id":
            return _result(scalar_one_or_none=self._point_id)
        return _result(scalar_one_or_none=None)


def _make_metric_result(code: str, value: float | None = 70.0) -> MetricResult:
    from tests.test_kpi_calc import _make_data_lineage

    return MetricResult(
        metric_code=code,
        value=value,
        confidence_level="B",
        lineage=_make_data_lineage(),
    )


async def _run_calculate_loop_kpi(db, runtime_overrides=None):  # noqa: ANN001
    """同输入运行 _calculate_loop_kpi（patch 三层计算，隔离算法依赖）.

    两轮调用唯一差异 = runtime_overrides（批级共享 vs None 逐回路加载），
    其余输入（loop/bundles/指标结果/时间窗）逐字节一致——等价基线证据的
    载体。
    """
    from tests.test_kpi_calc import _make_bundle, _make_full_metric_configs, _make_loop

    metric_results = {
        "good_value_rate": _make_metric_result("good_value_rate", 95.0),
        "auto_mode_rate": _make_metric_result("auto_mode_rate", 88.0),
        "stability_rate": _make_metric_result("stability_rate", 70.0),
    }
    composite_result = _make_metric_result("composite_score", 76.5)
    mock_planner = AsyncMock()
    mock_planner.request_bundles = AsyncMock(return_value=[_make_bundle("accuracy_rate")])

    with patch(
        "app.tasks.kpi_calc._compute_kpis_three_layer",
        return_value=(metric_results, composite_result),
    ):
        return await kpi_calc._calculate_loop_kpi(
            db=db,
            loop=_make_loop(),
            metric_configs=_make_full_metric_configs(),
            ts_start=_TS0,
            ts_end=_TS1,
            data_planner=mock_planner,
            runtime_overrides=runtime_overrides,
        )


# ===========================================================================
# 1. 等价基线（10 号文：优化不改分值/参考状态）
# ===========================================================================


class TestEquivalenceBaseline:
    """批级预载 vs 逐回路加载：同输入同 profile 结果恒等."""

    @pytest.mark.asyncio
    async def test_batch_overrides_values_equal_per_loop_loads(self) -> None:
        """_load_batch_runtime_overrides 与逐回路两加载函数同 mock 数据同值."""
        db = _DispatchDB()
        overrides = await kpi_calc._load_batch_runtime_overrides(db)
        assert overrides["fitness"] == await kpi_calc._load_fitness_sys_configs(db)
        assert overrides["kpiMinObservedRatio"] == 0.75

    @pytest.mark.asyncio
    async def test_calculate_loop_kpi_identical_result(self) -> None:
        """同输入两路径结果恒等（_timing 计时键除外）——分值/状态不变."""
        db_legacy = _DispatchDB()
        result_legacy = await _run_calculate_loop_kpi(db_legacy, runtime_overrides=None)

        db_batch = _DispatchDB()
        overrides = {
            "fitness": {r.key: r.value for r in _FITNESS_ROWS},
            "kpiMinObservedRatio": 0.75,
        }
        result_batch = await _run_calculate_loop_kpi(db_batch, runtime_overrides=overrides)

        assert result_legacy.pop("_timing", None) is not None
        assert result_batch.pop("_timing", None) is not None
        assert result_legacy == result_batch, "批级共享与逐回路加载结果必须恒等"
        assert result_legacy["status"] == "SUCCESS"
        assert result_legacy["score"] == 76.5

    def test_config_revision_digest_equivalence(self) -> None:
        """批级 FAILED-record digest 与逐回路 COMPLETED digest 同输入恒等."""
        type_weights = {"FLOW": {"w": 1}}
        overrides = {"fitness": {"fitness.manual_dominant_pct": "80"}, "kpiMinObservedRatio": None}
        # 逐回路公式（_calculate_loop_kpi 内联口径）
        per_loop = result_ledger.config_revision_digest(
            {"typeWeights": type_weights, "fitness": overrides["fitness"]}
        )
        assert kpi_calc._batch_runtime_config_revision(overrides, type_weights, None) == per_loop
        # pinned 前缀两分支同构
        pinned = {"configRevision": 7}
        assert (
            kpi_calc._batch_runtime_config_revision(overrides, type_weights, pinned)
            == f"rev:7:{per_loop.removeprefix('digest:')}"
        )


# ===========================================================================
# 2. IO 计数（1000 回路 → 回路级元数据 IO 常数 0）
# ===========================================================================


class TestMetadataIoCount:
    """回路级元数据 IO：legacy 2 次 → 批级共享 0 次（批级 2 次一次性）."""

    @pytest.mark.asyncio
    async def test_loop_level_sys_config_io_drops_to_zero(self) -> None:
        db_legacy = _DispatchDB()
        await _run_calculate_loop_kpi(db_legacy, runtime_overrides=None)
        db_batch = _DispatchDB()
        await _run_calculate_loop_kpi(
            db_batch,
            runtime_overrides={
                "fitness": {r.key: r.value for r in _FITNESS_ROWS},
                "kpiMinObservedRatio": 0.75,
            },
        )
        assert db_legacy.count_sys_config() == 2, "legacy：fitness LIKE + 观测阈值单键"
        assert db_batch.count_sys_config() == 0, "批级共享：回路级 sys_config IO=0"

    @pytest.mark.asyncio
    async def test_batch_runtime_overrides_two_queries_total(self) -> None:
        """批级预载合计 2 次 sys_config 查询（与回路数无关，常数级）."""
        db = _DispatchDB()
        await kpi_calc._load_batch_runtime_overrides(db)
        assert db.count_sys_config() == 2

    @pytest.mark.asyncio
    async def test_template_keys_single_query_no_loop_io(self) -> None:
        """TEMPLATE 覆盖存在时：批级 1 次查询，回路级 0 次查询."""
        db = _DispatchDB()
        loops = [
            SimpleNamespace(engineering_template_id=f"tpl-{i:04d}", unit_id="u1", id=f"loop-{i}")
            for i in range(1000)
        ]
        pinned = {"overrides": {"TEMPLATE|TPL_KEY_A": {"x": 1}}}
        keys = await kpi_calc._batch_load_template_keys(db, pinned, loops)  # type: ignore[arg-type]
        assert db.counts.get("engineering_template", 0) == 1
        # 映射消费：_resolve_loop_pinned_params 不再发模板查询
        db2 = _DispatchDB()
        loop0 = loops[0]
        with patch("app.services.config_publish.resolve_loop_params_map") as mock_resolve:
            mock_resolve.return_value = {"params": {}, "sources": {}}
            await kpi_calc._resolve_loop_pinned_params(db2, pinned, loop0, template_keys=keys)  # type: ignore[arg-type]
        assert db2.counts.get("engineering_template", 0) == 0
        # keys 缺失（模板行不存在）→ None，与逐回路 scalar_one_or_none 缺行等价
        assert keys.get("tpl-nonexistent") is None


# ===========================================================================
# 3. attempt 表 / FAILED 终态 / 显式重评链（G2 遗留③）
# ===========================================================================


class TestAttemptLedger:
    def test_derive_attempt_no_and_final(self) -> None:
        req = SimpleNamespace(retries=0)
        assert result_ledger.derive_attempt_no(req) == 1
        assert not result_ledger.is_final_attempt(req, 2)
        req2 = SimpleNamespace(retries=2)
        assert result_ledger.is_final_attempt(req2, 2)
        req_none = SimpleNamespace()
        assert result_ledger.derive_attempt_no(req_none) == 1

    @pytest.mark.asyncio
    async def test_start_attempt_conflict_do_nothing(self) -> None:
        """attempt 插入 ON CONFLICT DO NOTHING（重投递同 attempt_no 幂等）."""
        db = _DispatchDB()
        with patch("app.core.db.AsyncSessionLocal") as factory:
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=db)
            ctx.__aexit__ = AsyncMock(return_value=False)
            factory.return_value = ctx
            await result_ledger.start_calculation_attempt("lrid-1", 1, worker_pid=42)
        stmt = db.calls[-1]
        assert isinstance(stmt, Insert)
        assert stmt.table.name == "calculation_result_attempt"
        # on_conflict_do_nothing 挂 post_values_clause（无 update_values_to_set）
        assert stmt._post_values_clause is not None
        assert getattr(stmt._post_values_clause, "update_values_to_set", None) is None

    def test_attempt_model_unique_constraint(self) -> None:
        """(logical_run_id, attempt_no) 唯一约束在元数据中声明（迁移同批）."""
        table = CalculationResultAttempt.__table__
        uq = [c for c in table.constraints if c.name == "uq_calc_attempt_run_no"]
        assert uq, "缺少 uq_calc_attempt_run_no"

    @pytest.mark.asyncio
    async def test_append_failed_result_record_semantics(self) -> None:
        """FAILED record：status=FAILED + payload 含最终错误摘要."""
        db = _DispatchDB()
        record = await result_ledger.append_failed_result_record(
            db,
            logical_run_id="lrid-final",
            object_kind="LOOP",
            object_id="loop-1",
            ts_start=_TS0,
            ts_end=_TS1,
            algorithm_version=kpi_calc.ALGORITHM_VERSION,
            config_revision="digest:abc",
            error=RuntimeError("boom"),
        )
        assert record is None  # mock 插入竞态回读 None（真实库由迁移/PG 测试覆盖）
        insert_stmt = next(
            s
            for s in db.calls
            if isinstance(s, Insert) and s.table.name == "calculation_result_record"
        )
        values = insert_stmt.compile().params
        assert values["status"] == "FAILED"
        payload = values["payload"]
        assert payload["snapshotStatus"] == "FAILED"
        assert payload["error"]["errorClass"] == "RuntimeError"
        assert payload["error"]["errorMessage"] == "boom"

    @pytest.mark.asyncio
    async def test_completed_record_links_failed_source_when_gated(self) -> None:
        """显式重评链（门控路径）：failed_source_ids 命中 → COMPLETED record
        经 latest_failed_source_id 解析并回链；未命中/None → 零额外查询."""
        failed_id = "rec-failed-0001"

        def _universal(**kw):
            r = _result(**kw)
            r.first.return_value = ("snap-id",)
            return r

        db = AsyncMock()
        seq = [
            _universal(scalar_one_or_none=failed_id),  # latest_failed_source_id 点查
            _universal(scalar_one_or_none=None),  # 同键 record 不存在
            _universal(scalar_one_or_none=None),  # INSERT RETURNING（竞态回读 None）
            _universal(scalar_one_or_none=None),  # 冲突回读 SELECT（复用判空）
        ]
        db.execute = AsyncMock(side_effect=seq)
        record = SimpleNamespace(id="rec-new-0002")
        with patch.object(
            result_ledger, "append_result_record", AsyncMock(return_value=record)
        ) as append_spy:
            await kpi_calc._persist_snapshot(
                db,
                loop_id="loop-1",
                ts_start=_TS0.replace(tzinfo=None),
                ts_end=_TS1.replace(tzinfo=None),
                status="SUCCESS",
                logical_run_id="lrid-new",
                config_revision="digest:abc",
                failed_source_ids={"loop-1"},
            )
            append_spy.assert_awaited_once()
            assert append_spy.await_args.kwargs["source_record_id"] == failed_id

        # 未命中门：集合不含本回路 → 不做 FAILED 点查（execute 仅投影写入链）
        db2 = AsyncMock()
        db2.execute = AsyncMock(return_value=_result(first=("snap-id",)))
        with patch.object(
            result_ledger, "append_result_record", AsyncMock(return_value=record)
        ) as append_spy2:
            await kpi_calc._persist_snapshot(
                db2,
                loop_id="loop-other",
                ts_start=_TS0.replace(tzinfo=None),
                ts_end=_TS1.replace(tzinfo=None),
                status="SUCCESS",
                logical_run_id="lrid-new",
                config_revision="digest:abc",
                failed_source_ids={"loop-1"},
            )
            assert append_spy2.await_args.kwargs["source_record_id"] is None

        # append_result_record 本体不内敛查找（保持 P1-05 唯一幂等汇合点）：
        # 显式传 source_record_id 透传 insert values
        db3 = AsyncMock()
        seq3 = [
            _result(scalar_one_or_none=None),  # INSERT RETURNING
            _result(scalar_one_or_none=None),  # 冲突回读
        ]
        db3.execute = AsyncMock(side_effect=seq3)
        await result_ledger.append_result_record(
            db3,
            logical_run_id="lrid-new",
            object_kind="LOOP",
            object_id="loop-1",
            ts_start=_TS0,
            ts_end=_TS1,
            algorithm_version=kpi_calc.ALGORITHM_VERSION,
            config_revision="digest:abc",
            payload={"score": 80},
            source_record_id="rec-explicit",
        )
        insert_stmt = db3.execute.call_args_list[0][0][0]
        assert isinstance(insert_stmt, Insert)
        assert insert_stmt.compile().params["source_record_id"] == "rec-explicit"
        assert db3.execute.await_count == 2, "冲突路径只追加+回读，不内联查找前身"

    @pytest.mark.asyncio
    async def test_single_loop_recalc_completed_links_failed_source(self) -> None:
        """REWORK-D1 回归：单回路 FAILED 后手动重算成功 → 入口做窗口级门查询，
        COMPLETED record 的 sourceRecordId 指向该 FAILED record（P0-02 §2.2）."""
        from tests.test_kpi_calc import _make_bundle, _make_loop

        loop = _make_loop()
        failed_rec_id = "rec-failed-0001"
        db = _SingleLoopRecalcDB(loop, gate_ids={loop.id}, point_id=failed_rec_id)
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=db)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        metric_results = {
            "good_value_rate": _make_metric_result("good_value_rate", 95.0),
            "auto_mode_rate": _make_metric_result("auto_mode_rate", 88.0),
            "stability_rate": _make_metric_result("stability_rate", 70.0),
        }
        composite_result = _make_metric_result("composite_score", 76.5)
        planner = AsyncMock()
        planner.request_bundles = AsyncMock(return_value=[_make_bundle("accuracy_rate")])

        with (
            patch("app.core.db.AsyncSessionLocal", return_value=session_ctx),
            patch(
                "app.services.loop_config.get_loop_type_weights_map",
                new=AsyncMock(return_value={}),
            ),
            patch("app.tasks.kpi_calc._build_data_planner", return_value=planner),
            patch(
                "app.tasks.kpi_calc._compute_kpis_three_layer",
                return_value=(metric_results, composite_result),
            ),
        ):
            snap = await kpi_calc._do_calculate_single_loop(
                loop.id, ts_start=_TS0.replace(tzinfo=None).isoformat()
            )

        assert snap["status"] == "SUCCESS"
        # 单回路入口先做窗口级 FAILED 门查询（DISTINCT object_id，对象域过滤）
        gate_calls = [
            s
            for s in db.calls
            if not isinstance(s, Insert)
            and "calculation_result_record" in {f.name for f in s.get_final_froms()}
            and s.column_descriptions[0]["name"] == "object_id"
        ]
        assert gate_calls, "单回路入口应做窗口级 FAILED 门查询（REWORK-D1）"
        # COMPLETED record INSERT 的 sourceRecordId 指向该 FAILED record
        insert_stmt = next(
            s
            for s in db.calls
            if isinstance(s, Insert) and s.table.name == "calculation_result_record"
        )
        values = insert_stmt.compile().params
        assert values["status"] == "COMPLETED"
        assert values["source_record_id"] == failed_rec_id

    def test_kpi_tasks_three_attempt_ceiling(self) -> None:
        """冻结契约：KPI 写链四任务 max_retries=2（首跑+2 重试=3 次 attempt）."""
        for task in (
            kpi_calc.calculate_hourly_kpi,
            kpi_calc.calculate_loop_kpi,
            kpi_calc.calculate_custom_loop_kpi,
            kpi_calc.calculate_custom_batch_kpi,
        ):
            assert task.max_retries == 2, f"{task.name} max_retries 应为 2"


# ===========================================================================
# 4. 批内 final_attempt 接线（终末失败落 FAILED record）
# ===========================================================================


class TestFinalAttemptWiring:
    @pytest.mark.asyncio
    async def test_final_attempt_failure_writes_failed_record(self) -> None:
        """终末尝试失败回路 → append_failed_result_record 被调用；暂态不落."""
        loop = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000201",
            tag_name="101-FC-1023",
        )
        loop_cfgs = {
            "00000000-0000-0000-0000-000000000201": {
                "op_lower": 0.0,
                "op_upper": 100.0,
            }
        }
        db = _DispatchDB()
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=db)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.core.db.AsyncSessionLocal", return_value=session_ctx),
            patch(
                "app.tasks.kpi_calc._calculate_loop_kpi",
                new=AsyncMock(side_effect=RuntimeError("loop exploded")),
            ),
            patch("app.tasks.kpi_calc._make_config_loader", return_value=AsyncMock()),
            patch("app.tasks.kpi_calc._build_data_planner", return_value=MagicMock()),
            patch.object(
                kpi_calc.result_ledger,
                "append_failed_result_record",
                new=AsyncMock(return_value=None),
            ) as failed_spy,
        ):
            results = await kpi_calc._run_batch_loop_calculations(
                loops=[loop],  # type: ignore[arg-type]
                loop_configs=loop_cfgs,
                metric_configs={},
                ts_start=_TS0.replace(tzinfo=None),
                ts_end=_TS1.replace(tzinfo=None),
                type_weights=None,
                logical_run_id="lrid-batch",
                final_attempt=True,
            )
        assert all(isinstance(r, RuntimeError) for r in results)
        assert failed_spy.await_count == 1
        kwargs = failed_spy.await_args.kwargs
        assert kwargs["object_id"] == "00000000-0000-0000-0000-000000000201"
        assert kwargs["logical_run_id"] == "lrid-batch"

    @pytest.mark.asyncio
    async def test_transient_attempt_failure_no_terminal_record(self) -> None:
        """非终末尝试失败不落终态（等待 Celery 重试；attempt 日志另行覆盖）."""
        loop = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000201",
            tag_name="101-FC-1023",
        )
        loop_cfgs = {"00000000-0000-0000-0000-000000000201": {"op_lower": 0.0, "op_upper": 100.0}}
        db = _DispatchDB()
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=db)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.core.db.AsyncSessionLocal", return_value=session_ctx),
            patch(
                "app.tasks.kpi_calc._calculate_loop_kpi",
                new=AsyncMock(side_effect=RuntimeError("transient")),
            ),
            patch("app.tasks.kpi_calc._make_config_loader", return_value=AsyncMock()),
            patch("app.tasks.kpi_calc._build_data_planner", return_value=MagicMock()),
            patch.object(
                kpi_calc.result_ledger,
                "append_failed_result_record",
                new=AsyncMock(return_value=None),
            ) as failed_spy,
        ):
            results = await kpi_calc._run_batch_loop_calculations(
                loops=[loop],  # type: ignore[arg-type]
                loop_configs=loop_cfgs,
                metric_configs={},
                ts_start=_TS0.replace(tzinfo=None),
                ts_end=_TS1.replace(tzinfo=None),
                type_weights=None,
                logical_run_id="lrid-batch",
                final_attempt=False,
            )
        assert all(isinstance(r, RuntimeError) for r in results)
        assert failed_spy.await_count == 0


# ===========================================================================
# 5. 队列优先级（复用既有 Celery 设施，不新增 Worker/Beat）
# ===========================================================================


class TestQueuePriority:
    def test_priority_ordering(self) -> None:
        assert PRIORITY_EVENT < PRIORITY_MANUAL < PRIORITY_NIGHTLY
        assert (PRIORITY_EVENT, PRIORITY_MANUAL, PRIORITY_NIGHTLY) == (0, 3, 9)
        assert celery_app.conf.task_default_priority == PRIORITY_NIGHTLY

    def test_default_queue_max_priority_declared(self) -> None:
        queues = {q.name: q for q in celery_app.conf.task_queues}
        assert queues["default"].max_priority == 10
        assert celery_app.conf.task_queue_max_priority == 10

    def test_manual_only_tasks_keep_manual_priority(self) -> None:
        from app.tasks.report_generator import (
            export_diagnosis_statistics,
            generate_diagnosis_pdf_task,
        )
        from app.tasks.tuning import identify_model_task

        for task in (identify_model_task, export_diagnosis_statistics, generate_diagnosis_pdf_task):
            assert task.priority == PRIORITY_MANUAL

    def test_beat_entries_nightly_priority(self) -> None:
        schedule = kpi_calc.celery_app.conf.beat_schedule
        for name in ("kpi-calc-hourly", "node-kpi-daily", "node-kpi-monthly"):
            entry = schedule[name]
            options = (
                entry.get("options") if isinstance(entry, dict) else getattr(entry, "options", None)
            )
            assert options == {"priority": PRIORITY_NIGHTLY}, f"{name} 应为夜间优先级"

    def test_dispatch_sites_wired_with_priority(self) -> None:
        """派发点接线断言：事件/人工/夜间三类来源均显式带 priority."""
        import inspect

        from app.api.v1.endpoints import diagnosis_v2 as diag_ep
        from app.api.v1.endpoints import loop_data
        from app.api.v1.endpoints import tasks as tasks_ep
        from app.services.alert_rule_engine import dispatcher as alert_dp
        from app.tasks import diagnosis_schedule

        assert "PRIORITY_EVENT" in inspect.getsource(alert_dp), (
            "事件诊断派发（dispatcher）应带事件优先级"
        )
        assert "PRIORITY_MANUAL" in inspect.getsource(diag_ep), "手动诊断派发应带人工优先级"
        assert "PRIORITY_MANUAL" in inspect.getsource(tasks_ep), "手动评估/回填派发应带人工优先级"
        assert "PRIORITY_MANUAL" in inspect.getsource(loop_data), "导入回算派发应带人工优先级"
        assert "PRIORITY_NIGHTLY" in inspect.getsource(diagnosis_schedule), (
            "定时诊断派发应带夜间优先级"
        )


# ===========================================================================
# 6. 既有幂等链不回退（P3-02 消费声明）
# ===========================================================================


def test_ledger_unique_key_unchanged() -> None:
    """结果账本唯一键（同 logicalRunId 重试幂等）不被本批改动."""
    from app.models.calculation_result import CalculationResultRecord

    uq = [
        c.name
        for c in CalculationResultRecord.__table__.constraints
        if c.name == "uq_calc_result_record_run_object_window"
    ]
    assert uq
