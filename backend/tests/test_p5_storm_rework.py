"""PG 连接风暴回归 REWORK 回归测试（2026-10-13）。

对应诊断报告《PG连接风暴-诊断报告》修法对照：
- ① 毒化 session 关闭兜底（close_session_guarded：延迟重试 close → 耗尽
  terminate 底层连接，砍断"毒化→泄漏→占满"链）；
- ② AsyncTask.retry countdown 下限 ≥30s（消除 full_jitter 抽 0 的
  "Retry in 0s" 贴脸重试；backfill BUSY 轮询豁免保留 10s）；
- ③ G29 熔断（BatchSystemicFailure）不触发"回退直算"全量重跑；
- ④ 巡检 Beat */1 → */5 错峰；
- ⑤ 取数失败/回路失败日志带堆栈（exc_info）；
- 首发点修复：applicable_anchors_batch 改独立短会话（G14 红线补全——
  DataPlanner tagGroup gather 并发下唯一残留的共享调用方 session 访问）；
- 修 D：attempt 边界重置任务进度计数（「回路[2109/1030]」超总分显示）。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import InvalidRequestError

import app.core.db as db_module
from app.core.db import _extract_raw_asyncpg_connection, close_session_guarded
from app.tasks import kpi_calc
from app.tasks.celery_app import AsyncTask, celery_app

# ---------------------------------------------------------------------------
# 修①：毒化 session 关闭兜底
# ---------------------------------------------------------------------------


class _FlakySession:
    """close 前 N 次抛 InvalidRequestError（模拟 PROVISIONING_CONNECTION 态）。"""

    def __init__(self, failures_before_success: int, raw_connection=None) -> None:
        self._failures = failures_before_success
        self.close_calls = 0
        self._raw = raw_connection
        self.sync_session = MagicMock()

    async def close(self):
        self.close_calls += 1
        if self.close_calls <= self._failures:
            raise InvalidRequestError(
                "Method 'close()' can't be called here; method "
                "'_connection_for_bind()' is already in progress"
            )


@pytest.fixture()
def fast_retry_delays(monkeypatch):
    """把重试延迟压缩到毫秒级（避免测试真实等待 1+3+8s）。"""
    monkeypatch.setattr(db_module, "_SESSION_CLOSE_RETRY_DELAYS", (0.01, 0.01, 0.01))


class TestCloseSessionGuarded:
    """止血①：任何路径下 close 失败不泄漏、不掩盖原始异常。"""

    async def test_normal_close_single_call(self, fast_retry_delays):
        session = _FlakySession(failures_before_success=0)
        await close_session_guarded(session, label="t")
        assert session.close_calls == 1

    async def test_poisoned_close_retries_then_succeeds(self, fast_retry_delays):
        """前两次 close 抛错（provisioning 态），第三次成功——连接被归还。"""
        session = _FlakySession(failures_before_success=2)
        await close_session_guarded(session, label="t")
        assert session.close_calls == 3

    async def test_poisoned_close_exhausted_terminates_raw(self, fast_retry_delays):
        """close 恒失败 → terminate 底层 asyncpg 连接（断连不泄漏）。"""
        raw = MagicMock()
        session = _FlakySession(failures_before_success=99, raw_connection=raw)
        with (
            patch.object(
                db_module,
                "_extract_raw_asyncpg_connection",
                return_value=raw,
            ) as extract,
            patch.object(raw, "terminate") as terminate,
        ):
            await close_session_guarded(session, label="t")
        assert session.close_calls == 4  # 1 次立即 + 3 次重试
        extract.assert_called_once()
        terminate.assert_called_once()

    async def test_poisoned_close_no_handle_never_raises(self, fast_retry_delays):
        """取不到底层连接句柄（provisioning 从未完成）也不得抛出。"""
        session = _FlakySession(failures_before_success=99)
        with patch.object(db_module, "_extract_raw_asyncpg_connection", return_value=None):
            # 不抛出即通过（兜底防线是服务端 idle 超时/GC）
            await close_session_guarded(session, label="t")

    async def test_extract_raw_connection_walks_chain(self):
        """链路挖取：AsyncSession → sync Connection → 代理 → asyncpg 适配 → raw。"""
        raw = MagicMock()  # 有 terminate 属性
        adapted = SimpleNamespace(_connection=raw)
        proxy = SimpleNamespace(dbapi_connection=adapted)
        sync_conn = SimpleNamespace(connection=proxy)
        session = SimpleNamespace(sync_session=SimpleNamespace(connection=lambda: sync_conn))
        assert _extract_raw_asyncpg_connection(session) is raw

        # 任一环缺失 → None 而非异常
        session.sync_session = SimpleNamespace(
            connection=MagicMock(side_effect=InvalidRequestError("not bound"))
        )
        assert _extract_raw_asyncpg_connection(session) is None

    async def test_calculate_once_guarded_close_on_poison(self, fast_retry_delays, monkeypatch):
        """_calculate_once 异常路径：毒化 session 经 guarded close 兜底回收。

        session 桩首次 close 抛 InvalidRequestError（PROVISIONING_CONNECTION
        形态）——guard 必须重试 close 直到连接被归还，且不掩盖原始计算异常。
        """
        from datetime import datetime

        from app.models.loop import LoopLedger

        poisoned = _FlakySession(failures_before_success=1)
        monkeypatch.setattr("app.core.db.AsyncSessionLocal", lambda: poisoned)
        with (
            patch.object(kpi_calc, "_make_config_loader", return_value=None),
            patch.object(kpi_calc, "_build_data_planner", return_value=MagicMock()),
            patch.object(
                kpi_calc,
                "_calculate_loop_kpi",
                new=AsyncMock(side_effect=ValueError("provisioning poison")),
            ),
        ):
            results = await kpi_calc._run_batch_loop_calculations(
                loops=[LoopLedger(id="loop-1", tag_name="T-001", loop_type="FC")],
                loop_configs={},
                metric_configs={},
                ts_start=datetime(2026, 10, 13, 0),
                ts_end=datetime(2026, 10, 13, 1),
                type_weights=None,
                concurrency=1,
                bundle_cache=False,
                final_attempt=False,
            )
        assert any(isinstance(r, ValueError) for r in results)
        # 首次 close 失败 → 延迟重试成功（连接归还，不泄漏）
        assert poisoned.close_calls >= 2


# ---------------------------------------------------------------------------
# 修②：retry countdown 下限 ≥30s
# ---------------------------------------------------------------------------


class TestRetryCountdownFloor:
    """止血②：full_jitter 抽 0 的 "Retry in 0s" 必须被抬到 ≥30s。"""

    def _capture_retry(self, task_obj, **retry_kwargs):
        captured: dict = {}

        def fake_retry(self, *args, countdown=None, **kwargs):
            captured["countdown"] = countdown
            raise _StopRetry()

        class _StopRetry(Exception):
            pass

        with patch("celery.app.task.Task.retry", fake_retry):
            with pytest.raises(_StopRetry):
                task_obj.retry(exc=RuntimeError("boom"), **retry_kwargs)
        return captured

    def test_zero_countdown_floored_to_30(self):
        captured = self._capture_retry(AsyncTask(), countdown=0)
        assert captured["countdown"] == 30

    def test_small_countdown_floored_to_30(self):
        captured = self._capture_retry(AsyncTask(), countdown=2)
        assert captured["countdown"] == 30

    def test_large_countdown_preserved(self):
        captured = self._capture_retry(AsyncTask(), countdown=120)
        assert captured["countdown"] == 120

    def test_none_countdown_passthrough(self):
        """无 countdown（默认 retry_delay 语义）不注入下限。"""
        captured = self._capture_retry(AsyncTask())
        assert captured["countdown"] is None

    def test_backfill_busy_poll_keeps_10s(self):
        """_backfill_window_batch 的 BUSY 短轮询（10s）是收敛节奏而非故障重试。"""
        task = kpi_calc._backfill_window_batch
        assert task.min_retry_countdown == 10
        captured = self._capture_retry(task, countdown=10)
        assert captured["countdown"] == 10

    def test_kpi_hourly_task_floor_inherited(self):
        assert kpi_calc.calculate_hourly_kpi.min_retry_countdown == 30


# ---------------------------------------------------------------------------
# 修③：G29 熔断不触发"回退直算"
# ---------------------------------------------------------------------------


class TestBatchSystemicFailureNoFallback:
    """止血③：系统性故障熔断必须中止（Celery 退避重试），不得二次全量重跑。"""

    def test_assert_raises_dedicated_type(self):
        """熔断抛 BatchSystemicFailure（RuntimeError 子类，既有语义兼容）。"""
        with pytest.raises(kpi_calc.BatchSystemicFailure, match="系统性故障"):
            kpi_calc._assert_batch_health({"success": 0, "failed": 100}, 100)
        assert issubclass(kpi_calc.BatchSystemicFailure, RuntimeError)

    def _run_shell(self, tracking_side_effect):
        """执行 calculate_hourly_kpi 壳层（autoretry 包装内层）。

        bind=True 任务：celery 已把任务实例预绑为 self（含 run_async/request），
        直接以 kwargs 调用 ``task.run`` 即走真实壳层逻辑。
        返回 (fallback_mock, raised_exception_or_None)。
        """
        fallback = AsyncMock(return_value={"total": 0, "success": 0, "failed": 0})
        raised: BaseException | None = None

        with (
            patch.object(
                kpi_calc,
                "_do_hourly_with_tracking",
                new=AsyncMock(side_effect=tracking_side_effect),
            ),
            patch.object(kpi_calc, "_do_calculate", new=fallback),
            patch.object(
                kpi_calc.result_ledger,
                "start_calculation_attempt",
                new=AsyncMock(),
            ),
            patch.object(
                kpi_calc.result_ledger,
                "finish_calculation_attempt",
                new=AsyncMock(),
            ),
        ):
            try:
                kpi_calc.calculate_hourly_kpi.run(ts_start=None, task_id="t1")
            except BaseException as exc:  # noqa: BLE001 — 壳层抛错即被测行为
                raised = exc
        return fallback, raised

    def test_systemic_failure_propagates_without_fallback(self):
        """G29 熔断异常必须原样抛出，绝不调用 _do_calculate 回退直算。"""
        exc = kpi_calc.BatchSystemicFailure("批量 KPI 计算失败率 80%")
        fallback, raised = self._run_shell(exc)
        assert isinstance(raised, kpi_calc.BatchSystemicFailure)

    def test_systemic_failure_does_not_invoke_fallback(self):
        """正面断言：熔断路径 fallback 调用数为 0（不整批二次全量重跑）。"""
        exc = kpi_calc.BatchSystemicFailure("批量 KPI 计算失败率 80%")
        fallback, raised = self._run_shell(exc)
        assert isinstance(raised, kpi_calc.BatchSystemicFailure)
        assert fallback.await_count == 0

    def test_tracker_failure_still_falls_back(self):
        """真正的 task_tracker 故障仍走回退直算（既有兜底语义不变）。"""
        fallback, raised = self._run_shell(RuntimeError("redis down"))
        assert raised is None
        assert fallback.await_count == 1


# ---------------------------------------------------------------------------
# 修④（已按独立审查裁决撤销降频）：巡检 Beat 频率不变
# ---------------------------------------------------------------------------


class TestPatrolBeatStagger:
    def test_patrol_schedule_unchanged_every_minute(self):
        """降频撤销（独立审查 17 号文落地裁决）：维持 */1 业务时效不变。

        crontab 无相位/偏移参数——启动错峰在现有配置形态下不可行（登记
        待裁决），此处守护"未被顺手降频"。
        """
        sched = celery_app.conf.beat_schedule["alert-patrol"]["schedule"]
        assert "*/1" in repr(sched)


# ---------------------------------------------------------------------------
# 修⑤：首发点观测日志带堆栈
# ---------------------------------------------------------------------------


class TestFailureLogsCarryStack:
    async def test_dataplanner_failure_log_has_exc_info(self, caplog):
        """「DataPlanner 取数失败」必须带 exc_info（首发点钉死的日志缺口）。

        走真实 `_calculate_loop_kpi` 的取数失败分支（request_bundles 抛错），
        只 mock 取数与落库；断言日志记录携带异常堆栈。
        """
        from datetime import datetime

        planner = MagicMock()
        planner.request_bundles = AsyncMock(side_effect=ValueError("provisioning poison"))
        loop = SimpleNamespace(id="l1", tag_name="T-9", loop_type="FC", ideal_settling_time=None)
        with (
            patch.object(kpi_calc, "_persist_snapshot", new=AsyncMock(return_value={})),
            caplog.at_level(logging.WARNING, logger="app.tasks.kpi_calc"),
        ):
            result = await kpi_calc._calculate_loop_kpi(
                db=AsyncMock(),
                loop=loop,
                metric_configs={},
                ts_start=datetime(2026, 10, 13, 0),
                ts_end=datetime(2026, 10, 13, 1),
                data_planner=planner,
                type_weights=None,
                runtime_overrides={"fitness": {}},
            )
        assert result is not None
        records = [r for r in caplog.records if "DataPlanner 取数失败" in r.message]
        assert records, "必须记录取数失败日志"
        assert records[0].exc_info, "必须携带异常堆栈（exc_info）——首发点钉死依赖"
        assert "T-9" in records[0].message


# ---------------------------------------------------------------------------
# 首发点修复：applicable_anchors_batch 独立短会话
# ---------------------------------------------------------------------------


class _AnchorRow:
    def __init__(self, point_id, anchor_ts, coverage_until=None) -> None:
        self.point_id = point_id
        self.anchor_ts = anchor_ts
        self.coverage_until = coverage_until


class TestAnchorsBatchIndependentSession:
    """G14 红线补全：锚点批查不得使用调用方共享 session（gather 并发下毒化首发点）。"""

    async def test_uses_own_session_not_caller_shared(self, monkeypatch):
        from datetime import UTC, datetime

        from app.services.data_source import point_history_metadata as meta

        rows = [
            _AnchorRow("p1", datetime(2026, 10, 12, tzinfo=UTC)),
            _AnchorRow("p2", datetime(2026, 10, 12, tzinfo=UTC)),
        ]
        own_execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: rows)))
        )
        created = []

        class _FakeOwnSession:
            def __init__(self) -> None:
                self.execute = own_execute

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        def _factory():
            s = _FakeOwnSession()
            created.append(s)
            return s

        monkeypatch.setattr("app.core.db.AsyncSessionLocal", _factory)
        shared = AsyncMock()  # 调用方共享 session——必须零使用

        out = await meta.applicable_anchors_batch(
            shared,
            point_ids=["p1", "p2"],
            window_start=datetime(2026, 10, 13, tzinfo=UTC),
        )

        assert shared.execute.await_count == 0, "共享 session 不得被触碰（G14 红线）"
        assert own_execute.await_count == 1
        assert len(created) == 1
        assert set(out) == {"p1", "p2"}

    async def test_empty_point_ids_short_circuit(self, monkeypatch):
        from datetime import UTC, datetime

        from app.services.data_source import point_history_metadata as meta

        monkeypatch.setattr(
            "app.core.db.AsyncSessionLocal",
            MagicMock(side_effect=AssertionError("不应创建会话")),
        )
        out = await meta.applicable_anchors_batch(
            AsyncMock(),
            point_ids=[],
            window_start=datetime(2026, 10, 13, tzinfo=UTC),
        )
        assert out == {}


# ---------------------------------------------------------------------------
# 修 D：attempt 边界重置进度
# ---------------------------------------------------------------------------


class TestProgressResetOnAttempt:
    async def test_retry_attempt_resets_progress(self, monkeypatch):

        reset_mock = AsyncMock(return_value=True)
        calc_mock = AsyncMock(
            return_value={"total": 0, "success": 0, "inconclusive": 0, "failed": 0}
        )
        redis_mock = AsyncMock()
        redis_mock.set = AsyncMock(return_value=True)
        redis_mock.get = AsyncMock(return_value="token-1")

        with (
            patch("app.core.redis.redis_client", redis_mock),
            patch("app.services.task_tracker.update_status", new=AsyncMock()),
            patch("app.services.task_tracker.reset_task_progress", new=reset_mock),
            patch("app.services.task_tracker.create_task", new=AsyncMock()),
            patch.object(kpi_calc, "_do_calculate", new=calc_mock),
        ):
            await kpi_calc._do_hourly_with_tracking(
                ts_start=None,
                task_id="task-retry",
                logical_run_id="lrid-1",
                attempt_no=2,
                final_attempt=False,
            )
        reset_mock.assert_awaited_once_with("task-retry")

    async def test_first_attempt_does_not_reset(self, monkeypatch):
        reset_mock = AsyncMock(return_value=True)
        calc_mock = AsyncMock(
            return_value={"total": 0, "success": 0, "inconclusive": 0, "failed": 0}
        )
        redis_mock = AsyncMock()
        redis_mock.set = AsyncMock(return_value=True)
        redis_mock.get = AsyncMock(return_value="token-1")

        with (
            patch("app.core.redis.redis_client", redis_mock),
            patch("app.services.task_tracker.update_status", new=AsyncMock()),
            patch("app.services.task_tracker.reset_task_progress", new=reset_mock),
            patch.object(kpi_calc, "_do_calculate", new=calc_mock),
        ):
            await kpi_calc._do_hourly_with_tracking(
                ts_start=None,
                task_id="task-first",
                logical_run_id="lrid-1",
                attempt_no=1,
                final_attempt=False,
            )
        assert reset_mock.await_count == 0
