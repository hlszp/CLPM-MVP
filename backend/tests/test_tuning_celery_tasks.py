"""Celery 异步任务集成测试 — Phase 2.

覆盖 ``app/tasks/tuning.py`` 的核心 async 逻辑：
- ``_do_identify`` — 历史数据辨识任务体（成功/INCONCLUSIVE/异常三条路径）
- ``_do_tune_and_simulate`` — 整定+仿真任务体（成功/异常两条路径）
- ``identify_model_task`` / ``tune_and_simulate_task`` — 任务入口接线验证

测试策略：
- 直接调用 async 函数 ``_do_identify`` / ``_do_tune_and_simulate``（跳过 Celery
  消息中间件，聚焦业务逻辑），mock 掉 AsyncSessionLocal / identify_model_from_history /
  tune_pid / _simulate_multi_pid / tuning_progress
- 任务入口用 ``apply()`` 同步执行，验证参数透传
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# _do_identify 测试
# ---------------------------------------------------------------------------


def _make_mock_session(record: MagicMock | None = None) -> MagicMock:
    """构造 mock AsyncSession，支持 async with + add/commit/get.

    ``db.get(TuningRecord, id)`` 返回 ``record``（默认为新创建的 MagicMock）。
    """
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    # db.get 是 async 方法
    captured_record = record or MagicMock()

    async def _get(_model, _record_id):
        return captured_record

    session.get = AsyncMock(side_effect=_get)
    # async with AsyncSessionLocal() as db → __aenter__ 返回 session
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory, session, captured_record


class TestDoIdentify:
    """_do_identify 辨识任务体测试。"""

    @pytest.mark.asyncio
    async def test_identify_success(self):
        """辨识成功 → TuningRecord 更新为 IDENTIFIED + 进度 SUCCESS。"""
        from app.tasks.tuning import _do_identify

        factory, session, record = _make_mock_session()

        identify_result = {
            "success": True,
            "modelType": "FOPDT",
            "params": {"K": 2.0, "tau": 30.0, "theta": 5.0},
            "fittingScore": 95.5,
            "identifyMethod": "HISTORICAL_IV",
            "confidenceLevel": "A",
            "confidenceReason": "拟合度高",
            "excitationScore": 0.92,
            "residualTestPassed": True,
            "bestModel": {
                "modelType": "FOPDT",
                "params": {"K": 2.0, "tau": 30.0, "theta": 5.0},
            },
        }

        with (
            patch("app.core.db.AsyncSessionLocal", factory),
            patch(
                "app.services.tuning.identify_model_from_history",
                AsyncMock(return_value=identify_result),
            ),
            patch("app.services.tuning_progress.init_progress", AsyncMock()),
            patch("app.services.tuning_progress.update_progress", AsyncMock()),
        ):
            result = await _do_identify(
                task_id="task-success-001",
                loop_id="loop-1",
                start_time="2026-07-28T00:00:00Z",
                end_time="2026-07-28T01:00:00Z",
                candidate_model_types=["FOPDT", "SOPDT"],
                theta_estimate=None,
                created_by="test_user",
            )

        # 返回结果含 recordId
        assert "recordId" in result
        assert result["success"] is True
        # TuningRecord 被更新为 IDENTIFIED
        assert record.status == "IDENTIFIED"
        assert record.model_type == "FOPDT"
        assert record.confidence_level == "A"
        assert record.identify_method == "HISTORICAL_IV"
        assert record.completed_at is not None
        # commit 被调用（创建 + 更新）
        assert session.commit.await_count >= 2

    @pytest.mark.asyncio
    async def test_identify_inconclusive(self):
        """辨识失败（success=False）→ TuningRecord 状态 INCONCLUSIVE。"""
        from app.tasks.tuning import _do_identify

        factory, session, record = _make_mock_session()

        identify_result = {
            "success": False,
            "reason": "激励不足：OP 变化次数 < 3",
        }

        with (
            patch("app.core.db.AsyncSessionLocal", factory),
            patch(
                "app.services.tuning.identify_model_from_history",
                AsyncMock(return_value=identify_result),
            ),
            patch("app.services.tuning_progress.init_progress", AsyncMock()),
            patch("app.services.tuning_progress.update_progress", AsyncMock()),
        ):
            result = await _do_identify(
                task_id="task-inconclusive-001",
                loop_id="loop-1",
                start_time="2026-07-28T00:00:00Z",
                end_time="2026-07-28T01:00:00Z",
                candidate_model_types=None,
                theta_estimate=None,
                created_by="test_user",
            )

        assert result["success"] is False
        assert record.status == "INCONCLUSIVE"
        assert "激励不足" in record.confidence_reason
        assert record.completed_at is not None

    @pytest.mark.asyncio
    async def test_identify_exception_marks_failed(self):
        """辨识过程抛异常 → TuningRecord INCONCLUSIVE + 进度 FAILED + 异常重抛。"""
        from app.tasks.tuning import _do_identify

        factory, session, record = _make_mock_session()

        with (
            patch("app.core.db.AsyncSessionLocal", factory),
            patch(
                "app.services.tuning.identify_model_from_history",
                AsyncMock(side_effect=RuntimeError("TDengine 连接超时")),
            ),
            patch("app.services.tuning_progress.init_progress", AsyncMock()),
            patch("app.services.tuning_progress.update_progress", AsyncMock()),
            pytest.raises(RuntimeError, match="TDengine 连接超时"),
        ):
            await _do_identify(
                task_id="task-failed-001",
                loop_id="loop-1",
                start_time="2026-07-28T00:00:00Z",
                end_time="2026-07-28T01:00:00Z",
                candidate_model_types=None,
                theta_estimate=None,
                created_by="test_user",
            )

        # 异常时 record 标记为 INCONCLUSIVE
        assert record.status == "INCONCLUSIVE"
        assert "TDengine 连接超时" in record.confidence_reason
        # update_progress 被调用并标记 FAILED
        # （最后一次调用应含 status=FAILED）


# ---------------------------------------------------------------------------
# _do_tune_and_simulate 测试
# ---------------------------------------------------------------------------


class TestDoTuneAndSimulate:
    """_do_tune_and_simulate 整定仿真任务体测试。"""

    @pytest.mark.asyncio
    async def test_tune_and_simulate_success(self):
        """多算法整定 + 仿真成功 → TuningRecord 状态 SIMULATED。"""
        from app.tasks.tuning import _do_tune_and_simulate

        factory, session, record = _make_mock_session()

        tune_result_imc = {
            "recommendedPid": {"kp": 1.5, "ti": 15.0, "td": 2.0},
            "algorithm": "IMC",
        }
        tune_result_lambda = {
            "recommendedPid": {"kp": 1.2, "ti": 18.0, "td": 1.0},
            "algorithm": "LAMBDA",
        }

        sim_result = {
            "timestamps": [0, 1, 2],
            "currentResponse": {"pv": [0, 0.5, 1.0]},
            "recommendedResponse": {"pv": [0, 0.8, 1.2]},
            "candidateResponses": [
                {"label": "IMC", "response": {"pv": [0, 0.8, 1.2]}, "metrics": {}},
                {"label": "LAMBDA", "response": {"pv": [0, 0.7, 1.1]}, "metrics": {}},
            ],
        }

        with (
            patch("app.core.db.AsyncSessionLocal", factory),
            patch(
                "app.services.tuning.tune_pid",
                AsyncMock(side_effect=[tune_result_imc, tune_result_lambda]),
            ),
            patch(
                "app.services.tuning._simulate_multi_pid",
                return_value=sim_result,
            ),
            patch("app.services.tuning_progress.init_progress", AsyncMock()),
            patch("app.services.tuning_progress.update_progress", AsyncMock()),
        ):
            result = await _do_tune_and_simulate(
                task_id="task-tune-001",
                loop_id="loop-1",
                model_type="FOPDT",
                model_params={"K": 2.0, "tau": 30.0, "theta": 5.0},
                algorithms=["IMC", "LAMBDA"],
                current_pid={"kp": 0.5, "ti": 20.0, "td": 0.0},
                sim_duration=100.0,
                sim_step=1.0,
                setpoint_step=1.0,
                created_by="test_user",
            )

        # 返回结果
        assert "recordId" in result
        assert "recommendedPid" in result
        assert "pidCandidates" in result
        assert len(result["pidCandidates"]) == 2
        assert "simulationResult" in result
        # TuningRecord 更新为 SIMULATED
        assert record.status == "SIMULATED"
        assert record.recommended_pid == {"kp": 1.5, "ti": 15.0, "td": 2.0}
        assert record.algorithm == "IMC"
        assert record.pid_candidates is not None
        assert record.candidate_results is not None
        assert record.completed_at is not None

    @pytest.mark.asyncio
    async def test_tune_and_simulate_exception_marks_failed(self):
        """整定过程抛异常 → INCONCLUSIVE + 异常重抛。"""
        from app.tasks.tuning import _do_tune_and_simulate

        factory, session, record = _make_mock_session()

        with (
            patch("app.core.db.AsyncSessionLocal", factory),
            patch(
                "app.services.tuning.tune_pid",
                AsyncMock(side_effect=ValueError("模型参数无效")),
            ),
            patch("app.services.tuning_progress.init_progress", AsyncMock()),
            patch("app.services.tuning_progress.update_progress", AsyncMock()),
            pytest.raises(ValueError, match="模型参数无效"),
        ):
            await _do_tune_and_simulate(
                task_id="task-tune-failed-001",
                loop_id="loop-1",
                model_type="FOPDT",
                model_params={"K": 0, "tau": 0, "theta": 0},
                algorithms=["IMC"],
                current_pid=None,
                sim_duration=100.0,
                sim_step=1.0,
                setpoint_step=1.0,
                created_by="test_user",
            )

        assert record.status == "INCONCLUSIVE"
        assert "模型参数无效" in record.confidence_reason


# ---------------------------------------------------------------------------
# Celery 任务入口接线测试
# ---------------------------------------------------------------------------


class TestCeleryTaskWiring:
    """验证 Celery 任务入口正确调用 async 逻辑体。"""

    def test_identify_model_task_calls_do_identify(self):
        """identify_model_task 透传参数到 _do_identify 并执行。"""
        from app.tasks.tuning import identify_model_task

        mock_result = {"recordId": "rec-1", "success": True}

        with patch("app.tasks.tuning._do_identify", new=AsyncMock(return_value=mock_result)):
            # apply() 同步执行 Celery 任务（不经 broker）
            result = identify_model_task.apply(
                kwargs={
                    "loop_id": "loop-1",
                    "start_time": "2026-07-28T00:00:00Z",
                    "end_time": "2026-07-28T01:00:00Z",
                    "candidate_model_types": ["FOPDT"],
                    "theta_estimate": 5.0,
                    "created_by": "admin",
                }
            )

        assert result.successful()
        data = result.result
        assert data["recordId"] == "rec-1"
        assert data["success"] is True

    def test_tune_and_simulate_task_calls_do_tune(self):
        """tune_and_simulate_task 透传参数到 _do_tune_and_simulate。"""
        from app.tasks.tuning import tune_and_simulate_task

        mock_result = {
            "recordId": "rec-2",
            "recommendedPid": {"kp": 1.5, "ti": 15.0, "td": 2.0},
        }

        with patch(
            "app.tasks.tuning._do_tune_and_simulate",
            new=AsyncMock(return_value=mock_result),
        ):
            result = tune_and_simulate_task.apply(
                kwargs={
                    "loop_id": "loop-1",
                    "model_type": "FOPDT",
                    "model_params": {"K": 2.0, "tau": 30.0, "theta": 5.0},
                    "algorithms": ["IMC", "LAMBDA"],
                    "current_pid": {"kp": 0.5, "ti": 20.0, "td": 0.0},
                    "sim_duration": 200.0,
                    "sim_step": 1.0,
                    "setpoint_step": 1.0,
                    "created_by": "admin",
                }
            )

        assert result.successful()
        assert result.result["recordId"] == "rec-2"

    def test_identify_task_has_correct_name(self):
        """任务名称注册正确。"""
        from app.tasks.tuning import identify_model_task, tune_and_simulate_task

        assert identify_model_task.name == "app.tasks.tuning.identify_model_task"
        assert tune_and_simulate_task.name == "app.tasks.tuning.tune_and_simulate_task"


# ---------------------------------------------------------------------------
# 辅助函数测试
# ---------------------------------------------------------------------------


class TestTaskHelpers:
    """_parse_iso_naive / _now_naive / _serialize_result 辅助函数。"""

    def test_parse_iso_naive_strips_tz(self):
        """ISO 8601 带 Z 后缀的时间解析为 naive datetime。"""
        from datetime import datetime

        from app.tasks.tuning import _parse_iso_naive

        dt = _parse_iso_naive("2026-07-28T00:00:00Z")
        assert dt == datetime(2026, 7, 28, 0, 0, 0)
        assert dt.tzinfo is None

    def test_parse_iso_naive_offset(self):
        """带时区偏移的 ISO 时间剥离 tzinfo 为 naive（不做 UTC 换算，与 DB 存储口径一致）.

        实现仅 ``.replace(tzinfo=None)``，调用方应传 ``Z``（UTC）后缀；
        传入 ``+08:00`` 等偏移时保留本地壁钟时间，不自动换算为 UTC。
        """
        from datetime import datetime

        from app.tasks.tuning import _parse_iso_naive

        # +08:00 偏移：剥离 tzinfo 后壁钟时间仍为 08:00（不换算为 UTC 00:00）
        dt = _parse_iso_naive("2026-07-28T08:00:00+08:00")
        assert dt == datetime(2026, 7, 28, 8, 0, 0)
        assert dt.tzinfo is None

        # Z 后缀（UTC）才是推荐用法
        dt_utc = _parse_iso_naive("2026-07-28T00:00:00Z")
        assert dt_utc == datetime(2026, 7, 28, 0, 0, 0)
        assert dt_utc.tzinfo is None

    def test_now_naive_has_no_tzinfo(self):
        from app.tasks.tuning import _now_naive

        dt = _now_naive()
        assert dt.tzinfo is None

    def test_serialize_result_json_serializable(self):
        """可序列化结果原样返回。"""
        from app.tasks.tuning import _serialize_result

        result = {"a": 1, "b": [1, 2], "c": "hello"}
        assert _serialize_result(result) == result

    def test_serialize_result_non_serializable(self):
        """含 ``default=str`` 也无法处理的结构（循环引用）时整体转为 str dict。

        注：普通非 JSON 原生对象（如 ``object()``、numpy 标量）会被 ``default=str``
        优雅转为字符串，``json.dumps`` 不抛异常 → 原样返回；只有循环引用等
        ``default`` 无法兜底的结构才会触发 except 分支，整体转为 ``{k: str(v)}``。
        """
        from app.tasks.tuning import _serialize_result

        # 循环引用：default=str 也无法处理 → 触发 except 分支
        circular: list = []
        circular.append(circular)
        result = {"a": 1, "b": circular}
        serialized = _serialize_result(result)
        # except 分支整体 {k: str(v)}：连 1 也被转为 "1"
        assert serialized["a"] == "1"
        assert isinstance(serialized["b"], str)

    def test_serialize_result_default_str_passes_through(self):
        """普通非原生对象被 default=str 优雅处理 → 原样返回 dict。"""
        from app.tasks.tuning import _serialize_result

        result = {"a": 1, "b": object()}
        serialized = _serialize_result(result)
        # default=str 使 json.dumps 成功 → 原样返回，object 未被转换
        assert serialized is result
        assert serialized["a"] == 1
