"""P3-02 自动任务 ID、幂等与恢复单测（mock，无外部依赖）。

覆盖（任务书【三】/03§6 P3-02 卡 / C17-D02 / INT-03 诊断侧）：
- 统一 create_task 返回 ID：定时/事件入口 run 行 task_id=任务记录 ID（三方对应）；
- 批次/checkpoint 幂等：重试跳过已完成回路（不重复 run/建议）、遗留 FAILED 行脱链重做；
- 协作取消：回间/进度点检查，取消审计行（FAILED+CANCELLED，NOT_ASSESSED），
  取消后不发布后续结果、不生成建议，不覆盖 CANCELLED 终态；
- 08§4.2 终结统计互斥：取消→运行失败→不可判定→可判定，排队/运行另报，
  六类守恒（INT-03），PARTIAL 与算子覆盖独立，取消不计失败一次，分母不缩小；
- 人工/SYSTEM 身份分别记录（created_by_id 空=SYSTEM，不冒充个人）；
- 事件/定时批次键：并发/双发只派发一次，失败释放占位；
- SYSTEM 建议幂等守卫（不重复建议）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import UniqueConstraint

from app.models.diagnosis_run import DiagnosisRun
from app.services.diagnosis_batch_accounting import (
    CLASS_CANCELLED,
    CLASS_DETERMINABLE,
    CLASS_FAILED,
    CLASS_UNDETERMINABLE,
    classify_run,
    summarize_batch,
)
from app.services.diagnosis_system_actions import generate_system_actions
from app.tasks import diagnosis_v2 as dv

_WINDOW = ("2026-10-11T00:00:00", "2026-10-11T01:00:00")


def _meta(
    judgement: str | None = None,
    *,
    cancelled: bool = False,
    coverage: dict | None = None,
) -> dict:
    meta: dict = {}
    if judgement is not None:
        meta["judgementStatus"] = judgement
    if cancelled:
        meta["cancelled"] = True
    if coverage is not None:
        meta["coverage"] = coverage
    return meta


def _run(
    loop_id: str,
    status: str = "SUCCESS",
    meta: dict | None = None,
) -> DiagnosisRun:
    return DiagnosisRun(
        loop_id=loop_id,
        status=status,
        assessment_meta=meta,
        created_at=datetime(2026, 10, 11, 12, 0, 0),
    )


# ---------------------------------------------------------------------------
# 1. 模型：批次幂等唯一约束
# ---------------------------------------------------------------------------


class TestModelUniqueConstraint:
    def test_task_loop_unique_registered(self) -> None:
        """(task_id, loop_id) 唯一约束登记（同任务同回路一条 run）。"""
        uqs = [
            c
            for c in DiagnosisRun.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == "uq_diagnosis_run_task_loop"
        ]
        assert len(uqs) == 1
        assert [c.name for c in uqs[0].columns] == ["task_id", "loop_id"]


# ---------------------------------------------------------------------------
# 2. 08§4.2 终结统计（互斥 + 六类守恒 + 独立另报）
# ---------------------------------------------------------------------------


class TestBatchAccounting:
    def test_cancel_priority_over_failed(self) -> None:
        """取消审计行（status=FAILED + meta.cancelled）计取消，不再计失败一次。"""
        assert classify_run(_run("l1", "FAILED", _meta(cancelled=True))) == CLASS_CANCELLED

    def test_failed_without_cancel_is_failed(self) -> None:
        assert classify_run(_run("l1", "FAILED", None)) == CLASS_FAILED

    def test_judgement_splits_determinable(self) -> None:
        assert classify_run(_run("l1", "SUCCESS", _meta("SYMPTOM_DETECTED"))) == CLASS_DETERMINABLE
        assert classify_run(_run("l2", "SUCCESS", _meta("VALID_NEGATIVE"))) == CLASS_DETERMINABLE
        assert classify_run(_run("l3", "SUCCESS", _meta("INCONCLUSIVE"))) == CLASS_UNDETERMINABLE
        # 历史行无 assessment_meta → 不可判定（不伪称可判定）
        assert classify_run(_run("l4", "SUCCESS", None)) == CLASS_UNDETERMINABLE

    def test_int03_six_class_conservation(self) -> None:
        """INT-03 混合批次：合格/不足/错误/取消/未跑混合，六类守恒互斥。"""
        runs = [
            _run("l1", "SUCCESS", _meta("SYMPTOM_DETECTED")),
            _run("l2", "SUCCESS", _meta("INCONCLUSIVE")),
            _run("l3", "FAILED", _meta(cancelled=True)),
            _run("l4", "FAILED", None),
        ]
        stats = summarize_batch(
            task_status="CANCELLED",  # l5/l6 未开始即取消
            loop_ids=["l1", "l2", "l3", "l4", "l5", "l6"],
            runs=runs,
        )
        assert stats["objects"] == 6
        assert stats["terminal"] == {
            "cancelled": 3,  # l3 审计行 + l5/l6 未开始（取消优先，l3 不重复计失败）
            "failed": 1,
            "undeterminable": 1,
            "determinable": 1,
        }
        assert stats["pending"] == 0
        assert stats["missing"] == 0
        assert stats["invariantObjects"] is True  # 六类之和 = 对象总数

    def test_pending_reported_separately(self) -> None:
        """任务未终结：无 run 行对象计排队/运行（另报，不计终结）。"""
        stats = summarize_batch(
            task_status="RUNNING",
            loop_ids=["l1", "l2"],
            runs=[_run("l1", "SUCCESS", _meta("VALID_NEGATIVE"))],
        )
        assert stats["terminal"]["determinable"] == 1
        assert stats["pending"] == 1
        assert stats["invariantObjects"] is True

    def test_missing_when_task_success_without_run(self) -> None:
        stats = summarize_batch(task_status="SUCCESS", loop_ids=["l1"], runs=[])
        assert stats["missing"] == 1
        assert stats["terminal"] == {
            "cancelled": 0,
            "failed": 0,
            "undeterminable": 0,
            "determinable": 0,
        }

    def test_partial_and_coverage_independent(self) -> None:
        """PARTIAL 与 selected 算子覆盖独立另报；未执行不缩小分母。"""
        cov = {
            "selected": 4,
            "positive": 1,
            "negative": 0,
            "notApplicable": 0,
            "insufficient": 1,
            "error": 0,
            "cancelled": 0,
            "notRun": 2,
        }
        runs = [_run("l1", "PARTIAL", _meta("SYMPTOM_DETECTED", coverage=cov))]
        stats = summarize_batch(task_status="SUCCESS", loop_ids=["l1"], runs=runs)
        assert stats["partialRuns"] == 1
        # PARTIAL 归入可判定（有阳性），同时独立另报，不并入成功数
        assert stats["terminal"]["determinable"] == 1
        assert stats["operatorCoverage"]["selected"] == 4  # 分母=任务开始固定值
        assert stats["operatorCoverage"]["notRun"] == 2
        assert stats["legacyUnverifiableRuns"] == 0

    def test_legacy_runs_counted_separately(self) -> None:
        runs = [_run("l1", "SUCCESS", None)]
        stats = summarize_batch(task_status="SUCCESS", loop_ids=["l1"], runs=runs)
        assert stats["legacyUnverifiableRuns"] == 1
        assert stats["terminal"]["undeterminable"] == 1


# ---------------------------------------------------------------------------
# 3. 取消审计行元信息（D1/D2 预留参数位消费）
# ---------------------------------------------------------------------------


class TestCancelledAuditMeta:
    def test_cancelled_assessment_meta(self) -> None:
        meta = dv._cancelled_assessment_meta()
        assert meta["judgementStatus"] == "NOT_ASSESSED"  # 不发布正式结论
        assert meta["causeStatus"] == "NOT_ASSESSED"
        assert meta["cancelled"] is True
        assert meta["scope"]["gateReason"] == "任务取消"
        cov = meta["coverage"]
        assert cov["selected"] == 0 and cov["invariantSelected"] is True


# ---------------------------------------------------------------------------
# 4. 批任务：checkpoint 幂等 + 协作取消（_do_run_batch，边界 mock）
# ---------------------------------------------------------------------------


class _BatchEnv:
    """统一 patch _do_run_batch 边界依赖。"""

    def __init__(
        self,
        *,
        find_results: dict[str, DiagnosisRun | None] | None = None,
        diag_side_effect=None,
        status_sequence: list[str] | None = None,
    ) -> None:
        self.find = AsyncMock(
            side_effect=lambda task_id, loop_id: (find_results or {}).get(loop_id)
        )
        if diag_side_effect is None:
            diag_side_effect = lambda db, loop_id, **kw: MagicMock(status="SUCCESS")  # noqa: E731
        self.diag = AsyncMock(side_effect=diag_side_effect)
        self.update = AsyncMock()
        self.get_status = AsyncMock(side_effect=status_sequence or ["RUNNING"] * 64)
        self.detach = AsyncMock()
        self.record_failed = AsyncMock()
        self.record_cancelled = AsyncMock()
        self.converge = AsyncMock()
        self.finalize = AsyncMock(return_value={"objects": 0})
        self.patches = [
            patch.object(dv, "_find_batch_run", self.find),
            patch.object(dv, "run_diagnosis_for_loop", self.diag),
            patch.object(dv, "update_status", self.update),
            patch.object(dv, "get_task_status", self.get_status),
            patch.object(dv, "_detach_stale_attempt", self.detach),
            patch.object(dv, "_record_failed_run", self.record_failed),
            patch.object(dv, "_record_cancelled_run", self.record_cancelled),
            patch.object(dv, "_converge_running_placeholders", self.converge),
            patch.object(dv, "_finalize_accounting", self.finalize),
        ]

    def __enter__(self) -> _BatchEnv:
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc) -> None:
        for p in self.patches:
            p.stop()

    def final_task_statuses(self) -> list[str]:
        return [c.args[1].value for c in self.update.await_args_list]


class TestCheckpointIdempotency:
    @pytest.mark.asyncio
    async def test_worker_records_actual_start_before_first_loop(self) -> None:
        before = datetime.now(UTC)
        with _BatchEnv() as env:
            await dv._do_run_batch(["l1"], *_WINDOW, "task-clock", "full", "user")
        started = datetime.fromisoformat(env.update.await_args_list[0].kwargs["started_at"])
        assert before <= started <= datetime.now(UTC)

    @pytest.mark.asyncio
    async def test_retry_skips_completed_and_redoes_failed(self) -> None:
        """中途失败重试：已完成回路跳过（不重复 run/建议），失败回路脱链重做。"""

        async def _diag_mixed(db, loop_id, **kw):
            if loop_id == "l2":
                raise ValueError("transient")
            return MagicMock(status="SUCCESS")

        # 第一次执行：l1 成功、l2 异常留 FAILED 行
        with _BatchEnv(diag_side_effect=_diag_mixed) as env1:
            result1 = await dv._do_run_batch(["l1", "l2"], *_WINDOW, "task-A", "full", "user")
            assert result1["ok"] == 1
            env1.record_failed.assert_awaited_once()
            # 混合结果（1/2 完成）→ 任务 SUCCESS，失败明细进摘要（既有语义）
            statuses1 = env1.final_task_statuses()
            assert statuses1[-1] == "SUCCESS"
            summary = env1.update.await_args_list[-1].kwargs.get("current_stage", "")
            assert "失败 1" in summary

        # 第二次执行（重投递/重试）：l1 已 SUCCESS → 幂等跳过；l2 FAILED → 脱链重做
        with _BatchEnv(
            find_results={
                "l1": _run("l1", "SUCCESS", _meta("VALID_NEGATIVE")),
                "l2": _run("l2", "FAILED", None),
            },
        ) as env2:
            result2 = await dv._do_run_batch(["l1", "l2"], *_WINDOW, "task-A", "full", "user")
            # l1 不重复执行（checkpoint），l2 重做成功
            assert env2.diag.await_count == 1
            assert env2.diag.await_args_list[0].args[1] == "l2"
            env2.detach.assert_awaited_once()  # l2 旧 FAILED 行脱链
            assert result2["ok"] == 1
            assert result2["resumedDone"] == 1
            assert env2.final_task_statuses()[-1] == "SUCCESS"  # 幂等续跑 → SUCCESS


class TestCooperativeCancel:
    @pytest.mark.asyncio
    async def test_all_loops_failed_marks_task_failed(self) -> None:
        """全部回路失败（无 checkpoint 命中）→ 任务 FAILED（留痕行齐全）。"""

        async def _diag_fail(db, loop_id, **kw):
            raise ValueError("fatal")

        with _BatchEnv(diag_side_effect=_diag_fail) as env:
            result = await dv._do_run_batch(["l1"], *_WINDOW, "task-F", "full", "user")
        assert result["ok"] == 0
        assert env.final_task_statuses()[-1] == "FAILED"
        env.record_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_between_loops_stops_dispatch(self) -> None:
        """回间检查：取消后未开始回路不再执行、不发布结果、不覆盖终态。"""
        with _BatchEnv(status_sequence=["CANCELLED"] * 8) as env:
            result = await dv._do_run_batch(["l1", "l2", "l3"], *_WINDOW, "task-C", "full", "user")
        env.diag.assert_not_awaited()  # 未开始回路不执行
        env.record_cancelled.assert_not_awaited()  # 无在途回路 → 无审计行
        env.converge.assert_awaited_once()  # RUNNING 占位收敛（防御）
        statuses = env.final_task_statuses()
        assert "SUCCESS" not in statuses and "FAILED" not in statuses  # 不覆盖 CANCELLED
        assert result["cancelled"] == 3

    @pytest.mark.asyncio
    async def test_cancel_mid_loop_writes_audit_row(self) -> None:
        """在途回路取消：写取消审计行（不计失败、不发布建议），余下取消。"""

        async def _diag(db, loop_id, **kw):
            progress_cb = kw["progress_cb"]
            await progress_cb(0.1, "读取历史数据")  # RUNNING → 正常上报
            await progress_cb(0.5, "算子执行")  # CANCELLED → 抛取消信号
            raise AssertionError("不应执行到此处")

        with _BatchEnv(
            diag_side_effect=_diag,
            status_sequence=["RUNNING", "RUNNING", "CANCELLED", "CANCELLED"],
        ) as env:
            result = await dv._do_run_batch(["l1", "l2"], *_WINDOW, "task-D", "full", "user")
        # l1 写取消审计行；l2 未开始不执行
        env.record_cancelled.assert_awaited_once()
        assert env.record_cancelled.await_args.args[0] == "l1"
        env.record_failed.assert_not_awaited()  # 取消不计失败一次
        env.converge.assert_awaited_once()
        assert result["cancelled"] == 2

    @pytest.mark.asyncio
    async def test_cancelled_audit_row_no_system_actions(self) -> None:
        """取消审计行不生成 SYSTEM 建议（取消后不发布后续结果）。"""
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = "loop-row"  # 回路存在
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=exec_result)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_db)
        cm.__aexit__ = AsyncMock(return_value=False)
        gen_actions = AsyncMock()
        with (
            patch.object(dv, "AsyncSessionLocal", MagicMock(return_value=cm)),
            patch.object(dv, "generate_system_actions_best_effort", gen_actions),
        ):
            await dv._record_cancelled_run(
                "l1", datetime(2026, 10, 11), datetime(2026, 10, 11, 1), "task-E", "user"
            )
        gen_actions.assert_not_awaited()
        added = mock_db.add.call_args.args[0]
        assert isinstance(added, DiagnosisRun)
        assert added.status == "FAILED"  # 08§4.2 行 8：FAILED + reasonCode=CANCELLED
        assert added.assessment_meta["cancelled"] is True
        assert added.assessment_meta["judgementStatus"] == "NOT_ASSESSED"
        assert added.data_gate["reasonCode"] == "CANCELLED"


# ---------------------------------------------------------------------------
# 5. 入口：统一 ID / 身份记录 / 批次键（schedule + dispatcher）
# ---------------------------------------------------------------------------


class TestScheduledEntry:
    def _patch_env(self, fake_redis):
        loops = ["l1", "l2"]
        celery_mock = MagicMock()
        celery_mock.delay.return_value = MagicMock(id="celery-s1")
        celery_mock.apply_async.return_value = MagicMock(id="celery-s1")
        create_mock = AsyncMock(return_value="task-S77")
        set_ids_mock = AsyncMock()
        patches = [
            patch("app.tasks.diagnosis_schedule.redis_client", fake_redis),
            patch(
                "app.tasks.diagnosis_schedule._loops_by_importance",
                AsyncMock(return_value=loops),
            ),
            patch(
                "app.tasks.diagnosis_schedule._batch_get_loop_data",
                AsyncMock(
                    return_value={
                        "l1": {"role_tag_map": {"pv": "x"}},
                        "l2": {"role_tag_map": {"pv": "x"}},
                    }
                ),
            ),
            patch(
                "app.tasks.diagnosis_schedule._fitness_blocked_ids",
                AsyncMock(return_value=set()),
            ),
            patch("app.tasks.diagnosis_schedule._density_ok", AsyncMock(return_value=True)),
            patch(
                "app.services.diagnosis_titles.next_diagnosis_title",
                AsyncMock(return_value="回路诊断-261011-1"),
            ),
            patch("app.tasks.diagnosis_v2.run_diagnosis_batch", celery_mock),
            patch("app.tasks.diagnosis_schedule.create_task", create_mock),
            patch("app.tasks.diagnosis_schedule.set_celery_task_ids", set_ids_mock),
        ]
        return patches, create_mock, celery_mock

    @pytest.mark.asyncio
    async def test_dispatch_uses_create_task_id_and_system_identity(self, fake_redis) -> None:
        """定时入口：celery task_id=create_task 返回 ID；SYSTEM 身份（空用户 ID）。"""
        from app.tasks.diagnosis_schedule import _run_scheduled

        patches, create_mock, celery_mock = self._patch_env(fake_redis)
        with (
            patch("app.services.task_tracker.redis_client", fake_redis),
        ):
            for p in patches:
                p.start()
            try:
                result = await _run_scheduled(None, timedelta(hours=24))
            finally:
                for p in patches:
                    p.stop()
        kwargs = celery_mock.apply_async.call_args.kwargs["kwargs"]
        assert kwargs["task_id"] == "task-S77"
        assert kwargs["trigger_type"] == "SCHEDULED"
        assert create_mock.call_args.kwargs["created_by_id"] == ""  # SYSTEM 不冒充个人
        assert result["taskIds"] == ["task-S77"]

    @pytest.mark.asyncio
    async def test_round_guard_blocks_double_dispatch(self, fake_redis) -> None:
        """同轮双发（Beat 重启）只派发一次（批次键幂等）。"""
        from app.tasks.diagnosis_schedule import _run_scheduled

        patches, create_mock, celery_mock = self._patch_env(fake_redis)
        for p in patches:
            p.start()
        try:
            first = await _run_scheduled(None, timedelta(hours=24))
            second = await _run_scheduled(None, timedelta(hours=24))
        finally:
            for p in patches:
                p.stop()
        assert first["dispatched"] == 2  # 2 回路 1 批
        assert second.get("skippedRound") is True
        assert second["dispatched"] == 0
        create_mock.assert_awaited_once()
        # P5-02：派发渠道 apply_async（夜间优先级）
        assert celery_mock.apply_async.call_count == 1


class TestEventDispatchGuard:
    def _patch_env(self, fake_redis, create_side_effect=None, create_return="task-E9"):
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None  # 无近期 EVENT run
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=exec_result)
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_db)
        cm.__aexit__ = AsyncMock(return_value=False)
        create_mock = AsyncMock(return_value=create_return)
        if create_side_effect is not None:
            create_mock.side_effect = create_side_effect
        celery_mock = MagicMock()
        celery_mock.delay.return_value = MagicMock(id="celery-e1")
        celery_mock.apply_async.return_value = MagicMock(id="celery-e1")
        patches = [
            patch("app.services.alert_rule_engine.dispatcher.redis_client", fake_redis),
            patch("app.core.db.AsyncSessionLocal", MagicMock(return_value=cm)),
            patch("app.services.task_tracker.create_task", create_mock),
            patch("app.tasks.diagnosis_v2.run_diagnosis_batch", celery_mock),
            patch("app.services.task_tracker.set_celery_task_ids", AsyncMock()),
            patch(
                "app.services.diagnosis_titles.next_diagnosis_title",
                AsyncMock(return_value="回路诊断-261011-2"),
            ),
        ]
        return patches, create_mock, celery_mock

    @pytest.mark.asyncio
    async def test_concurrent_dispatch_once(self, fake_redis) -> None:
        """并发事件命中（占位未夺得）只派发一次；统一 create_task 返回 ID。"""
        from app.services.alert_rule_engine.dispatcher import _trigger_diagnosis

        patches, create_mock, celery_mock = self._patch_env(fake_redis)
        for p in patches:
            p.start()
        try:
            first = await _trigger_diagnosis(
                {"ruleCode": "R1", "dsl": {"condition": {"windowSeconds": 7200}}}, "l1"
            )
            second = await _trigger_diagnosis(
                {"ruleCode": "R2", "dsl": {"condition": {"windowSeconds": 7200}}}, "l1"
            )
        finally:
            for p in patches:
                p.stop()
        assert first == "task-E9"
        assert second is None  # 占位未夺得 → 跳过
        create_mock.assert_awaited_once()
        kwargs = celery_mock.apply_async.call_args.kwargs["kwargs"]
        assert kwargs["task_id"] == "task-E9"
        assert create_mock.call_args.kwargs["created_by_id"] == ""  # SYSTEM 身份

    @pytest.mark.asyncio
    async def test_guard_released_on_failure(self, fake_redis) -> None:
        """派发失败释放占位：后续事件命中可重试。"""
        from app.services.alert_rule_engine.dispatcher import (
            _event_dispatch_guard_key,
            _trigger_diagnosis,
        )

        patches, _, _ = self._patch_env(fake_redis, create_side_effect=RuntimeError("redis down"))
        for p in patches:
            p.start()
        try:
            with pytest.raises(RuntimeError):
                await _trigger_diagnosis({"ruleCode": "R1", "dsl": {}}, "l1")
        finally:
            for p in patches:
                p.stop()
        assert await fake_redis.get(_event_dispatch_guard_key("l1")) is None  # 占位已释放

        # 重试：派发成功
        patches2, create_mock2, _ = self._patch_env(fake_redis, create_return="task-E10")
        for p in patches2:
            p.start()
        try:
            retried = await _trigger_diagnosis({"ruleCode": "R1", "dsl": {}}, "l1")
        finally:
            for p in patches2:
                p.stop()
        assert retried == "task-E10"
        create_mock2.assert_awaited_once()


# ---------------------------------------------------------------------------
# 6. INT-03：不重复建议（SYSTEM 幂等守卫）
# ---------------------------------------------------------------------------


class TestSystemActionIdempotency:
    @pytest.mark.asyncio
    async def test_existing_system_actions_skip(self) -> None:
        """该 run 已有 SYSTEM 建议 → 跳过（不重复建议）。"""
        exec_result = MagicMock()
        exec_result.scalar.return_value = 2  # 已有 2 条
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=exec_result)
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        count = await generate_system_actions(mock_db, _run(str(uuid4())))
        assert count == 0
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_run_without_category_no_actions(self) -> None:
        """无分类 run（失败留痕/取消审计）→ 生成路径为空操作（不发布建议）。"""
        exec_result = MagicMock()
        exec_result.scalar.return_value = 0
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=exec_result)
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        count = await generate_system_actions(mock_db, _run(str(uuid4()), "FAILED", None))
        assert count == 0
        mock_db.add.assert_not_called()
