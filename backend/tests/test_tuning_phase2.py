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

from unittest.mock import AsyncMock, patch

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
