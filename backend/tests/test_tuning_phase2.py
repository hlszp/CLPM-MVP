"""Phase 2 测试 — 多 PID 仿真对比 + 进度跟踪 + API 端点.

覆盖：
- simulate_closed_loop 多 PID 扩展（pid_candidates）
- _simulate_multi_pid 辅助函数
- run_simulation 透传 pid_candidates
- tuning_progress 进度跟踪（init/update/get）
- API 端点：/identify/history, /identify/segments, /simulate（多 PID）,
  /compare, /tasks/{id}/status, /tasks/{id}/cancel
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tuning import _simulate_multi_pid, run_simulation
from app.services.tuning_algorithms import PIDParams, simulate_closed_loop
from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 多 PID 仿真对比测试
# ---------------------------------------------------------------------------


class TestMultiPidSimulation:
    """simulate_closed_loop 多 PID 扩展测试。"""

    def test_no_candidates_backward_compatible(self):
        """无 pid_candidates 时行为与原版完全一致。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=PIDParams(kp=0.5, ti=20.0, td=0.0),
            recommended_pid=PIDParams(kp=2.0, ti=15.0, td=2.0),
            sim_duration=100.0,
        )
        assert "candidateResponses" not in result
        assert "currentResponse" in result
        assert "recommendedResponse" in result

    def test_with_pid_candidates(self):
        """有 pid_candidates 时返回 candidateResponses。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        candidates = [
            ("IMC", PIDParams(kp=1.0, ti=10.0, td=0.5)),
            ("LAMBDA", PIDParams(kp=0.8, ti=12.0, td=0.0)),
            ("SIMC", PIDParams(kp=1.2, ti=8.0, td=1.0)),
        ]
        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=PIDParams(kp=0.5, ti=20.0, td=0.0),
            recommended_pid=PIDParams(kp=2.0, ti=15.0, td=2.0),
            sim_duration=100.0,
            pid_candidates=candidates,
        )
        assert "candidateResponses" in result
        assert len(result["candidateResponses"]) == 3
        for i, (label, _) in enumerate(candidates):
            assert result["candidateResponses"][i]["label"] == label
            assert "response" in result["candidateResponses"][i]
            assert "metrics" in result["candidateResponses"][i]
            assert "pv" in result["candidateResponses"][i]["response"]

    def test_candidate_metrics_extracted(self):
        """每组候选 PID 都有性能指标。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        candidates = [
            ("IMC", PIDParams(kp=1.0, ti=10.0, td=0.5)),
            ("ZN", PIDParams(kp=2.0, ti=12.0, td=3.0)),
        ]
        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=PIDParams(kp=0.3, ti=50.0, td=0.0),
            recommended_pid=PIDParams(kp=1.5, ti=20.0, td=1.0),
            sim_duration=200.0,
            pid_candidates=candidates,
        )
        for cr in result["candidateResponses"]:
            metrics = cr["metrics"]
            assert "riseTime" in metrics
            assert "overshoot" in metrics
            assert "settlingTime" in metrics
            assert "itae" in metrics

    def test_empty_candidates_list_no_candidate_responses(self):
        """空 candidates 列表不产生 candidateResponses。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=PIDParams(kp=0.5, ti=20.0, td=0.0),
            recommended_pid=PIDParams(kp=2.0, ti=15.0, td=2.0),
            sim_duration=50.0,
            pid_candidates=[],
        )
        assert "candidateResponses" not in result


class TestSimulateMultiPid:
    """_simulate_multi_pid 辅助函数测试。"""

    def test_basic_multi_pid(self):
        """基本多 PID 对比。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        candidates = [
            {"label": "IMC", "pid": {"kp": 1.0, "ti": 10.0, "td": 0.5}},
            {"label": "LAMBDA", "pid": {"kp": 0.8, "ti": 12.0, "td": 0.0}},
            {"label": "SIMC", "pid": {"kp": 1.2, "ti": 8.0, "td": 1.0}},
        ]
        result = _simulate_multi_pid(
            model_type="FOPDT",
            model_params=model_params,
            current_pid={"kp": 0.5, "ti": 20.0, "td": 0.0},
            pid_candidates=candidates,
            sim_duration=100.0,
        )
        assert "candidateResponses" in result
        assert len(result["candidateResponses"]) == 3
        labels = [cr["label"] for cr in result["candidateResponses"]]
        assert "IMC" in labels
        assert "LAMBDA" in labels
        assert "SIMC" in labels

    def test_no_current_pid_uses_first_candidate(self):
        """无 current_pid 时用第一个候选作为基准。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        candidates = [
            {"label": "IMC", "pid": {"kp": 1.0, "ti": 10.0, "td": 0.5}},
            {"label": "LAMBDA", "pid": {"kp": 0.8, "ti": 12.0, "td": 0.0}},
        ]
        result = _simulate_multi_pid(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=None,
            pid_candidates=candidates,
            sim_duration=50.0,
        )
        assert "currentResponse" in result
        assert "recommendedResponse" in result


class TestRunSimulationMultiPid:
    """run_simulation 透传 pid_candidates 测试。"""

    @pytest.mark.asyncio
    async def test_run_simulation_with_candidates(self):
        """run_simulation 透传多 PID 候选。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        pid_candidates = [
            {"label": "IMC", "kp": 1.0, "ti": 10.0, "td": 0.5},
            {"label": "LAMBDA", "kp": 0.8, "ti": 12.0, "td": 0.0},
        ]
        result = await run_simulation(
            model_type="FOPDT",
            model_params=model_params,
            current_pid={"kp": 0.5, "ti": 20.0, "td": 0.0},
            recommended_pid={"kp": 2.0, "ti": 15.0, "td": 2.0},
            sim_duration=50.0,
            pid_candidates=pid_candidates,
        )
        assert "candidateResponses" in result
        assert len(result["candidateResponses"]) == 2

    @pytest.mark.asyncio
    async def test_run_simulation_without_candidates(self):
        """无候选时向后兼容。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        result = await run_simulation(
            model_type="FOPDT",
            model_params=model_params,
            current_pid={"kp": 0.5, "ti": 20.0, "td": 0.0},
            recommended_pid={"kp": 2.0, "ti": 15.0, "td": 2.0},
            sim_duration=50.0,
        )
        assert "candidateResponses" not in result


# ---------------------------------------------------------------------------
# 进度跟踪测试
# ---------------------------------------------------------------------------


class TestTuningProgress:
    """tuning_progress 进度跟踪测试。"""

    @pytest.mark.asyncio
    async def test_init_and_get_progress(self):
        """初始化进度后可读取。"""
        from app.services.tuning_progress import get_progress, init_progress

        with patch("app.services.tuning_progress.redis_client") as mock_redis:
            mock_redis.hset = AsyncMock(return_value=True)
            mock_redis.expire = AsyncMock(return_value=True)
            mock_redis.hgetall = AsyncMock(
                return_value={
                    "task_id": "test-task-1",
                    "task_type": "identify",
                    "loop_id": "loop-1",
                    "status": "PENDING",
                    "progress": "0.0",
                    "stage": "",
                    "message": "任务已提交",
                    "result": "",
                    "error": "",
                    "created_at": "2026-07-28T00:00:00+00:00",
                    "updated_at": "2026-07-28T00:00:00+00:00",
                }
            )

            await init_progress("test-task-1", task_type="identify", loop_id="loop-1")
            data = await get_progress("test-task-1")

            assert data is not None
            assert data["taskId"] == "test-task-1"
            assert data["status"] == "PENDING"
            assert data["taskType"] == "identify"

    @pytest.mark.asyncio
    async def test_update_progress_with_stage(self):
        """按阶段更新进度时自动查表得到 progress。"""
        from app.services.tuning_progress import STAGE_PROGRESS, update_progress

        with patch("app.services.tuning_progress.redis_client") as mock_redis:
            mock_redis.hset = AsyncMock(return_value=True)
            mock_redis.expire = AsyncMock(return_value=True)

            await update_progress("test-task-2", status="RUNNING", stage="identify")

            # 验证 hset 被调用，且 progress 来自 STAGE_PROGRESS["identify"]=50
            call_args = mock_redis.hset.call_args
            mapping = call_args.kwargs.get("mapping", {})
            assert mapping.get("stage") == "identify"
            assert mapping.get("progress") == str(STAGE_PROGRESS["identify"])

    @pytest.mark.asyncio
    async def test_get_progress_not_found(self):
        """不存在的任务返回 None。"""
        from app.services.tuning_progress import get_progress

        with patch("app.services.tuning_progress.redis_client") as mock_redis:
            mock_redis.hgetall = AsyncMock(return_value={})
            data = await get_progress("nonexistent")
            assert data is None


# ---------------------------------------------------------------------------
# V62-P1-013/014: TaskTracker 桥接测试
# ---------------------------------------------------------------------------


class TestTuningProgressTaskTrackerBridge:
    """tuning_progress → TaskTracker 桥接测试（V62-P1-013/014）."""

    @pytest.mark.asyncio
    async def test_init_progress_bridges_to_task_tracker(self):
        """created_by_id 非空时，init_progress 在 TaskTracker 创建 TUNING 任务."""
        from app.services.tuning_progress import init_progress

        with (
            patch("app.services.tuning_progress.redis_client") as mock_redis,
            patch("app.services.task_tracker.redis_client"),
            patch("app.services.task_tracker.create_task", new_callable=AsyncMock) as mock_create,
        ):
            mock_redis.hset = AsyncMock(return_value=True)
            mock_redis.expire = AsyncMock(return_value=True)
            mock_create.return_value = "tracker-task-123"

            await init_progress(
                "celery-task-1",
                task_type="identify",
                loop_id="loop-1",
                created_by="engineer1",
                created_by_id="user-uuid-1",
                ts_start="2026-07-28T00:00:00Z",
                ts_end="2026-07-28T01:00:00Z",
            )

            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["created_by"] == "engineer1"
            assert call_kwargs["created_by_id"] == "user-uuid-1"
            assert call_kwargs["celery_task_id"] == "celery-task-1"
            assert call_kwargs["loop_ids"] == ["loop-1"]
            # 验证 tracker_task_id 写入 tuning_progress hash
            hset_mapping = mock_redis.hset.call_args.kwargs.get("mapping", {})
            assert hset_mapping.get("tracker_task_id") == "tracker-task-123"

    @pytest.mark.asyncio
    async def test_init_progress_without_user_id_skips_bridge(self):
        """created_by_id 为空时（定时任务/旧调用方），跳过 TaskTracker 桥接."""
        from app.services.tuning_progress import init_progress

        with (
            patch("app.services.tuning_progress.redis_client") as mock_redis,
            patch("app.services.task_tracker.create_task", new_callable=AsyncMock) as mock_create,
        ):
            mock_redis.hset = AsyncMock(return_value=True)
            mock_redis.expire = AsyncMock(return_value=True)

            await init_progress(
                "celery-task-2",
                task_type="identify",
                loop_id="loop-1",
                created_by="system",
                created_by_id="",
            )

            mock_create.assert_not_called()
            hset_mapping = mock_redis.hset.call_args.kwargs.get("mapping", {})
            assert "tracker_task_id" not in hset_mapping

    @pytest.mark.asyncio
    async def test_terminal_status_syncs_to_task_tracker(self):
        """update_progress 进入 SUCCESS 终态时同步 TaskTracker."""
        from app.services.tuning_progress import update_progress

        with (
            patch("app.services.tuning_progress.redis_client") as mock_redis,
            patch("app.services.task_tracker.update_status", new_callable=AsyncMock) as mock_update,
        ):
            mock_redis.hset = AsyncMock(return_value=True)
            mock_redis.expire = AsyncMock(return_value=True)
            mock_redis.hgetall = AsyncMock(
                return_value={
                    "tracker_task_id": "tracker-task-456",
                    "stage": "discrete_to_continuous",
                }
            )

            await update_progress(
                "celery-task-3",
                status="SUCCESS",
                progress=100.0,
                message="辨识完成",
            )

            mock_update.assert_called_once()
            call_args = mock_update.call_args
            assert call_args.args[0] == "tracker-task-456"
            assert call_args.args[1].value == "SUCCESS"

    @pytest.mark.asyncio
    async def test_non_terminal_status_does_not_sync(self):
        """RUNNING 状态不同步 TaskTracker（粗粒度状态只在终态同步）."""
        from app.services.tuning_progress import update_progress

        with (
            patch("app.services.tuning_progress.redis_client") as mock_redis,
            patch("app.services.task_tracker.update_status", new_callable=AsyncMock) as mock_update,
        ):
            mock_redis.hset = AsyncMock(return_value=True)
            mock_redis.expire = AsyncMock(return_value=True)

            await update_progress(
                "celery-task-4",
                status="RUNNING",
                stage="identify",
            )

            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_bridge_failure_does_not_block_progress(self):
        """TaskTracker 桥接失败不阻断 tuning_progress 自身初始化."""
        from app.services.tuning_progress import init_progress

        with (
            patch("app.services.tuning_progress.redis_client") as mock_redis,
            patch(
                "app.services.task_tracker.create_task",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Redis down"),
            ),
        ):
            mock_redis.hset = AsyncMock(return_value=True)
            mock_redis.expire = AsyncMock(return_value=True)

            # 不应抛异常
            await init_progress(
                "celery-task-5",
                task_type="identify",
                loop_id="loop-1",
                created_by="engineer1",
                created_by_id="user-uuid-1",
            )

            # tuning_progress hash 仍被写入（无 tracker_task_id）
            mock_redis.hset.assert_called_once()


# ---------------------------------------------------------------------------
# API 端点测试
# ---------------------------------------------------------------------------


class TestPhase2API:
    """Phase 2 API 端点测试。"""

    def test_simulate_with_pid_candidates_api(self, client):
        """/simulate 端点接受 pid_candidates。"""
        payload = {
            "modelType": "FOPDT",
            "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
            "currentPid": {"kp": 0.5, "ti": 20.0, "td": 0.0},
            "recommendedPid": {"kp": 2.0, "ti": 15.0, "td": 2.0},
            "pidCandidates": [
                {"label": "IMC", "kp": 1.0, "ti": 10.0, "td": 0.5},
                {"label": "LAMBDA", "kp": 0.8, "ti": 12.0, "td": 0.0},
            ],
            "simDuration": 50.0,
            "simStep": 1.0,
            "setpointStep": 1.0,
            "disturbanceType": "step",
            "modelSource": "MANUAL",
            "riskConfirmed": True,
        }
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post("/api/v1/tuning/simulate", json=payload)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "candidateResponses" in data
        assert len(data["candidateResponses"]) == 2

    def test_compare_endpoint_requires_min_two_candidates(self, client):
        """/compare 端点要求至少 2 组候选 PID。"""
        payload = {
            "modelType": "FOPDT",
            "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
            "currentPid": {"kp": 0.5, "ti": 20.0, "td": 0.0},
            "recommendedPid": {"kp": 2.0, "ti": 15.0, "td": 2.0},
            "pidCandidates": [
                {"label": "IMC", "kp": 1.0, "ti": 10.0, "td": 0.5},
            ],
            "simDuration": 50.0,
        }
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post("/api/v1/tuning/compare", json=payload)
        assert resp.status_code == 400

    def test_compare_endpoint_success(self, client):
        """/compare 端点多 PID 对比成功。"""
        payload = {
            "modelType": "FOPDT",
            "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
            "currentPid": {"kp": 0.5, "ti": 20.0, "td": 0.0},
            "recommendedPid": {"kp": 2.0, "ti": 15.0, "td": 2.0},
            "pidCandidates": [
                {"label": "IMC", "kp": 1.0, "ti": 10.0, "td": 0.5},
                {"label": "LAMBDA", "kp": 0.8, "ti": 12.0, "td": 0.0},
                {"label": "SIMC", "kp": 1.2, "ti": 8.0, "td": 1.0},
            ],
            "simDuration": 50.0,
            "simStep": 1.0,
            "setpointStep": 1.0,
            "disturbanceType": "step",
            "modelSource": "MANUAL",
            "riskConfirmed": True,
        }
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post("/api/v1/tuning/compare", json=payload)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "candidateResponses" in data
        assert len(data["candidateResponses"]) == 3

    def test_simulate_backward_compatible_no_candidates(self, client):
        """/simulate 无 pid_candidates 时向后兼容。"""
        payload = {
            "modelType": "FOPDT",
            "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
            "currentPid": {"kp": 0.5, "ti": 20.0, "td": 0.0},
            "recommendedPid": {"kp": 2.0, "ti": 15.0, "td": 2.0},
            "simDuration": 50.0,
            "simStep": 1.0,
            "setpointStep": 1.0,
            "disturbanceType": "step",
            "modelSource": "MANUAL",
            "riskConfirmed": True,
        }
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post("/api/v1/tuning/simulate", json=payload)
        assert resp.status_code == 200
        data = resp.json()["data"]
        # 无候选时 candidateResponses 为 None（Schema 字段默认值）
        assert data.get("candidateResponses") is None

    def test_task_status_not_found(self, client):
        """/tasks/{taskId}/status 不存在时返回 404。"""
        with (
            patch("app.services.tuning_progress.redis_client") as mock_redis,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            mock_redis.hgetall = AsyncMock(return_value={})
            resp = client.get("/api/v1/tuning/tasks/nonexistent-task/status")
        assert resp.status_code == 404

    # ------------------------------------------------------------------
    # POST /identify/history — 异步历史辨识端点
    # ------------------------------------------------------------------

    def test_identify_history_returns_task_id(self, client):
        """/identify/history 提交异步任务返回 taskId（AUTO 策略）。"""
        mock_task = MagicMock()
        mock_task.id = "celery-task-abc123"

        with (
            patch("app.tasks.tuning.identify_model_task") as mock_celery_task,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            mock_celery_task.delay.return_value = mock_task
            resp = client.post(
                "/api/v1/tuning/identify/history",
                json={
                    "loopId": "loop-1",
                    "startTime": "2026-07-28T00:00:00Z",
                    "endTime": "2026-07-28T01:00:00Z",
                    "identifyStrategy": "AUTO",
                    "candidateModelTypes": ["FOPDT", "SOPDT"],
                },
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["taskId"] == "celery-task-abc123"
        assert data["status"] == "PENDING"
        # 验证 delay 被调用，参数透传
        mock_celery_task.delay.assert_called_once()
        call_kwargs = mock_celery_task.delay.call_args.kwargs
        assert call_kwargs["loop_id"] == "loop-1"
        assert call_kwargs["created_by"] == "ic_engineer"

    def test_identify_history_history_only_strategy(self, client):
        """/identify/history HISTORY_ONLY 策略也走异步任务。"""
        mock_task = MagicMock()
        mock_task.id = "celery-task-hist-001"

        with (
            patch("app.tasks.tuning.identify_model_task") as mock_celery_task,
            mock_current_user(TEST_USERS["admin"]),
        ):
            mock_celery_task.delay.return_value = mock_task
            resp = client.post(
                "/api/v1/tuning/identify/history",
                json={
                    "loopId": "loop-1",
                    "startTime": "2026-07-28T00:00:00Z",
                    "endTime": "2026-07-28T01:00:00Z",
                    "identifyStrategy": "HISTORY_ONLY",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["data"]["taskId"] == "celery-task-hist-001"

    def test_identify_history_accepts_ipdt_candidate(self, client):
        """P2-008：历史辨识接受 IPDT 候选（差分辨识链已接入）."""
        mock_task = MagicMock()
        mock_task.id = "celery-task-ipdt-001"
        with (
            patch("app.tasks.tuning.identify_model_task") as mock_celery_task,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            mock_celery_task.delay.return_value = mock_task
            resp = client.post(
                "/api/v1/tuning/identify/history",
                json={
                    "loopId": "loop-1",
                    "startTime": "2026-07-28T00:00:00Z",
                    "endTime": "2026-07-28T01:00:00Z",
                    "identifyStrategy": "HISTORY_ONLY",
                    "candidateModelTypes": ["IPDT"],
                },
            )

        assert resp.status_code == 200
        assert resp.json()["data"]["taskId"] == "celery-task-ipdt-001"
        mock_celery_task.delay.assert_called_once()

    def test_identify_history_preserves_explicit_zero_theta(self, client):
        """显式 thetaEstimate=0 必须原样传给异步任务."""
        mock_task = MagicMock()
        mock_task.id = "celery-task-zero-theta"

        with (
            patch("app.tasks.tuning.identify_model_task") as mock_celery_task,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            mock_celery_task.delay.return_value = mock_task
            resp = client.post(
                "/api/v1/tuning/identify/history",
                json={
                    "loopId": "loop-1",
                    "startTime": "2026-07-28T00:00:00Z",
                    "endTime": "2026-07-28T01:00:00Z",
                    "identifyStrategy": "HISTORY_ONLY",
                    "candidateModelTypes": ["FOPDT"],
                    "thetaEstimate": 0,
                },
            )

        assert resp.status_code == 200
        assert mock_celery_task.delay.call_args.kwargs["theta_estimate"] == 0

    def test_identify_history_rejects_negative_theta(self, client):
        """纯滞后预估值不能为负数."""
        with (
            patch("app.tasks.tuning.identify_model_task") as mock_celery_task,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/tuning/identify/history",
                json={
                    "loopId": "loop-1",
                    "startTime": "2026-07-28T00:00:00Z",
                    "endTime": "2026-07-28T01:00:00Z",
                    "identifyStrategy": "HISTORY_ONLY",
                    "thetaEstimate": -1,
                },
            )

        assert resp.status_code == 422
        mock_celery_task.delay.assert_not_called()

    def test_identify_history_step_only_sync_path(self, client):
        """/identify/history STEP_ONLY 策略走同步阶跃路径（不经 Celery）。"""
        sync_result = {
            "modelType": "FOPDT",
            "params": {"K": 2.0, "tau": 30.0, "theta": 5.0},
            "fittingScore": 90.0,
            "algorithmVersion": "v1.0",
            "dataPoints": 600,
            "fittedCurve": None,
            "stepValidationPassed": True,
        }

        with (
            patch("app.tasks.tuning.identify_model_task") as mock_celery_task,
            patch(
                "app.api.v1.endpoints.tuning.identify_model",
                AsyncMock(return_value=sync_result),
            ),
            patch(
                "app.api.v1.endpoints.tuning.persist_step_identification_record",
                AsyncMock(return_value="step-record-1"),
            ) as mock_persist,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/tuning/identify/history",
                json={
                    "loopId": "loop-1",
                    "startTime": "2026-07-28T00:00:00Z",
                    "endTime": "2026-07-28T01:00:00Z",
                    "identifyStrategy": "STEP_ONLY",
                },
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["modelType"] == "FOPDT"
        assert data["recordId"] == "step-record-1"
        mock_persist.assert_awaited_once()
        # STEP_ONLY 不经 Celery
        mock_celery_task.delay.assert_not_called()

    def test_identify_history_requires_auth(self, client):
        """/identify/history 未登录返回 401/403。"""
        resp = client.post(
            "/api/v1/tuning/identify/history",
            json={
                "loopId": "loop-1",
                "startTime": "2026-07-28T00:00:00Z",
                "endTime": "2026-07-28T01:00:00Z",
            },
        )
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # POST /identify/segments — 可辨识片段预览端点
    # ------------------------------------------------------------------

    def test_identify_segments_success(self, client):
        """/identify/segments 返回可辨识片段列表。"""
        segments_result = {
            "loopId": "loop-1",
            "totalSegments": 1,
            "segments": [
                {
                    "startIdx": 0,
                    "endIdx": 600,
                    "mode": "AUTO",
                    "excitationScore": 0.85,
                    "conditionNumber": 120.5,
                    "isSufficient": True,
                }
            ],
            "sufficientCount": 1,
        }

        with (
            patch(
                "app.api.v1.endpoints.tuning.preview_identify_segments",
                AsyncMock(return_value=segments_result),
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/tuning/identify/segments",
                json={
                    "loopId": "loop-1",
                    "startTime": "2026-07-28T00:00:00Z",
                    "endTime": "2026-07-28T01:00:00Z",
                },
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["loopId"] == "loop-1"
        assert data["totalSegments"] == 1
        assert data["sufficientCount"] == 1
        assert data["segments"][0]["isSufficient"] is True

    def test_identify_segments_empty_window(self, client):
        """/identify/segments 数据不足时返回 0 片段。"""
        segments_result = {
            "loopId": "loop-2",
            "totalSegments": 0,
            "segments": [],
            "sufficientCount": 0,
        }

        with (
            patch(
                "app.api.v1.endpoints.tuning.preview_identify_segments",
                AsyncMock(return_value=segments_result),
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/tuning/identify/segments",
                json={
                    "loopId": "loop-2",
                    "startTime": "2026-07-28T00:00:00Z",
                    "endTime": "2026-07-28T00:05:00Z",
                },
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["totalSegments"] == 0
        assert data["sufficientCount"] == 0

    # ------------------------------------------------------------------
    # GET /tasks/{taskId}/status — 成功路径
    # ------------------------------------------------------------------

    def test_task_status_success(self, client):
        """/tasks/{taskId}/status 成功返回进度数据。"""
        progress_data = {
            "task_id": "celery-task-running-001",
            "task_type": "identify",
            "loop_id": "loop-1",
            "status": "RUNNING",
            "progress": "50.0",
            "stage": "identify",
            "message": "参数化辨识中...",
            "result": "",
            "error": "",
            "created_at": "2026-07-28T10:00:00+00:00",
            "updated_at": "2026-07-28T10:01:00+00:00",
        }

        with (
            patch("app.services.tuning_progress.redis_client") as mock_redis,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            mock_redis.hgetall = AsyncMock(return_value=progress_data)
            resp = client.get("/api/v1/tuning/tasks/celery-task-running-001/status")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["taskId"] == "celery-task-running-001"
        assert data["status"] == "RUNNING"
        assert data["progress"] == 50.0
        assert data["stage"] == "identify"

    def test_task_status_success_with_result(self, client):
        """/tasks/{taskId}/status SUCCESS 状态含 result JSON。"""
        import json

        progress_data = {
            "task_id": "celery-task-done-001",
            "task_type": "identify",
            "loop_id": "loop-1",
            "status": "SUCCESS",
            "progress": "100.0",
            "stage": "discrete_to_continuous",
            "message": "辨识完成",
            "result": json.dumps({"recordId": "rec-001", "modelType": "FOPDT"}),
            "error": "",
            "created_at": "2026-07-28T10:00:00+00:00",
            "updated_at": "2026-07-28T10:02:00+00:00",
        }

        with (
            patch("app.services.tuning_progress.redis_client") as mock_redis,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            mock_redis.hgetall = AsyncMock(return_value=progress_data)
            resp = client.get("/api/v1/tuning/tasks/celery-task-done-001/status")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "SUCCESS"
        assert data["progress"] == 100.0
        assert data["result"]["recordId"] == "rec-001"

    # ------------------------------------------------------------------
    # POST /tasks/{taskId}/cancel — 取消任务端点
    # ------------------------------------------------------------------

    def test_cancel_task_running_state(self, client):
        """/tasks/{taskId}/cancel 对 RUNNING 状态任务执行 revoke。"""
        mock_result = MagicMock()
        mock_result.state = "RUNNING"
        mock_result.revoke = MagicMock()

        with (
            # endpoint 函数内 `from celery.result import AsyncResult` 懒导入
            patch("celery.result.AsyncResult", return_value=mock_result),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post("/api/v1/tuning/tasks/celery-task-running/cancel")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "CANCELLED"
        mock_result.revoke.assert_called_once_with(terminate=True, signal="SIGTERM")

    def test_cancel_task_pending_state(self, client):
        """/tasks/{taskId}/cancel 对 PENDING 状态也执行 revoke。"""
        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_result.revoke = MagicMock()

        with (
            patch("celery.result.AsyncResult", return_value=mock_result),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post("/api/v1/tuning/tasks/celery-task-pending/cancel")

        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "CANCELLED"
        mock_result.revoke.assert_called_once()

    def test_cancel_task_already_success(self, client):
        """/tasks/{taskId}/cancel 对已 SUCCESS 任务不执行 revoke。"""
        mock_result = MagicMock()
        mock_result.state = "SUCCESS"
        mock_result.revoke = MagicMock()

        with (
            patch("celery.result.AsyncResult", return_value=mock_result),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post("/api/v1/tuning/tasks/celery-task-done/cancel")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "SUCCESS"
        mock_result.revoke.assert_not_called()

    def test_cancel_task_requires_auth(self, client):
        """/tasks/{taskId}/cancel 未登录返回 401/403。"""
        resp = client.post("/api/v1/tuning/tasks/some-task/cancel")
        assert resp.status_code in (401, 403)
