"""整定 Phase 2.1 合并评审 P1 修复验证测试（任务 H2）.

覆盖：
- P1-3 SOPDT 仿真参数契约：接受 {K,T1,T2,theta} 标准形（双一阶惯性串联
  G(s)=K·e^(-θs)/((T1s+1)(T2s+1))），兼容旧 τ/ξ 形，T1/T2 优先
- P1-6 AUTO 策略兜底：历史辨识失败/数据不足降级阶跃实验路径，
  标注 dataSource=fallback_step；HISTORY_ONLY 不兜底
- P1-5 + 数据迁移：g7a8b9c0d1e2 迁移链与内容断言；
  schemas DataSource 接受 fallback_step

期望值说明（手算核实，禁止实现输出反推）：
- P 控制（ti=0, td=0）闭环稳态由终值定理：PV_ss = K·Kp·SP/(1+K·Kp)。
  K=2, Kp=1, SP=1 → PV_ss = 2/3 ≈ 0.666667（增量式 P 控制器
  op_k = Kp·e_k 望远镜求和成立，θ 不影响稳态）。
- T1/T2 串联与 τ/ξ 形数学等价：τ=√(T1·T2)，ξ=(T1+T2)/(2·√(T1·T2))。
  T1=10, T2=20 → τ=√200≈14.14214，ξ=30/(2·√200)≈1.06066；
  同一 sim_step=1.0 下两侧 n_sub=1（4/14.14<1，4/10=0.4<1），
  RK4 逐点结果应完全一致（浮点零差异量级）。
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tuning_algorithms import PIDParams, simulate_closed_loop
from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# P1-3：SOPDT 仿真参数契约
# ---------------------------------------------------------------------------


class TestSopdtSimulationParamContract:
    """SOPDT 仿真接受 {K,T1,T2,theta} 标准形，不再静默回退默认 tau/xi。"""

    @staticmethod
    def _simulate(model_params: dict, sim_duration: float = 400.0) -> list[float]:
        result = simulate_closed_loop(
            model_type="SOPDT",
            model_params=model_params,
            current_pid=PIDParams(kp=1.0, ti=0.0, td=0.0),
            recommended_pid=PIDParams(kp=1.0, ti=0.0, td=0.0),
            sim_duration=sim_duration,
            sim_step=1.0,
            setpoint_step=1.0,
        )
        return result["recommendedResponse"]["pv"]

    def test_t1_t2_steady_state_matches_final_value_theorem(self):
        """{K,T1,T2,theta} 参数形稳态 = K·Kp·SP/(1+K·Kp) = 2/3（手算终值定理）。"""
        pv = self._simulate({"K": 2.0, "T1": 10.0, "T2": 20.0, "theta": 2.0})
        assert abs(pv[-1] - 2.0 / 3.0) < 1e-3

    def test_t1_t2_equivalent_to_tau_xi_form(self):
        """T1/T2 串联与等价 τ/ξ 形逐点一致（τ=√(T1T2)，ξ=(T1+T2)/(2√(T1T2))）。"""
        t1, t2 = 10.0, 20.0
        tau_eq = math.sqrt(t1 * t2)
        xi_eq = (t1 + t2) / (2.0 * tau_eq)
        pv_t1t2 = self._simulate({"K": 2.0, "T1": t1, "T2": t2, "theta": 2.0})
        pv_tauxi = self._simulate({"K": 2.0, "tau": tau_eq, "xi": xi_eq, "theta": 2.0})
        max_diff = max(abs(a - b) for a, b in zip(pv_t1t2, pv_tauxi, strict=True))
        assert max_diff < 1e-9

    def test_t1_t2_takes_precedence_over_conflicting_tau_xi(self):
        """T1/T2 与 tau/xi 同时存在时优先 T1/T2（冲突 tau=999 被忽略）。"""
        pv_ref = self._simulate({"K": 2.0, "T1": 10.0, "T2": 20.0, "theta": 2.0})
        pv_conflict = self._simulate(
            {"K": 2.0, "T1": 10.0, "T2": 20.0, "theta": 2.0, "tau": 999.0, "xi": 0.1}
        )
        max_diff = max(abs(a - b) for a, b in zip(pv_ref, pv_conflict, strict=True))
        assert max_diff < 1e-12

    def test_no_silent_fallback_to_default_tau(self):
        """修复点：{K,T1,T2,theta} 曲线必须显著区别于默认 tau=30 曲线。"""
        pv_t1t2 = self._simulate({"K": 2.0, "T1": 10.0, "T2": 20.0, "theta": 2.0})
        pv_default = self._simulate({"K": 2.0, "theta": 2.0})  # 无 T1/T2/tau → 默认 tau=30
        max_diff = max(abs(a - b) for a, b in zip(pv_t1t2, pv_default, strict=True))
        assert max_diff > 0.05

    def test_legacy_tau_xi_form_backward_compatible(self):
        """旧 τ/ξ 形仍可仿真且稳态正确（2/3）。"""
        pv = self._simulate({"K": 2.0, "tau": 14.1421, "xi": 1.0607, "theta": 2.0})
        assert abs(pv[-1] - 2.0 / 3.0) < 1e-3

    def test_unpaired_t1_falls_back_to_tau_xi_form(self):
        """T1/T2 不成对（仅 T1）时回退 τ/ξ 形，与纯 τ/ξ 默认曲线一致。"""
        pv_unpaired = self._simulate({"K": 2.0, "T1": 10.0, "theta": 2.0})
        pv_default = self._simulate({"K": 2.0, "theta": 2.0})
        max_diff = max(abs(a - b) for a, b in zip(pv_unpaired, pv_default, strict=True))
        assert max_diff < 1e-12

    def test_non_positive_t1_t2_clamped(self):
        """T1/T2 ≤ 0 时箝位为 1.0（与 tau ≤ 0 → 1.0 既有口径一致），仿真仍收敛。"""
        pv = self._simulate({"K": 2.0, "T1": 0.0, "T2": -5.0, "theta": 2.0})
        assert math.isfinite(pv[-1])
        # 箝位后 T1=T2=1 → 等价 τ=1, ξ=1，与显式 τ/ξ 形一致
        pv_ref = self._simulate({"K": 2.0, "tau": 1.0, "xi": 1.0, "theta": 2.0})
        max_diff = max(abs(a - b) for a, b in zip(pv, pv_ref, strict=True))
        assert max_diff < 1e-9


# ---------------------------------------------------------------------------
# P1-6：AUTO 策略阶跃兜底
# ---------------------------------------------------------------------------

_HISTORY_FAILED = {"success": False, "reason": "激励不足", "validRate": 0.97}

_STEP_RESULT = {
    "modelType": "FOPDT",
    "params": {"K": 1.5, "tau": 25.0, "theta": 3.0},
    "fittingScore": 88.5,
    "stepValidationPassed": True,
    "algorithmVersion": "TUNE_ENGINE_v1.0",
    "dataPoints": 300,
    "fittedCurve": None,
    "tagName": "TIC-101",
    "mvStep": 5.0,
}

_HISTORY_SUCCESS = {
    "success": True,
    "modelType": "FOPDT",
    "params": {"K": 1.8, "tau": 30.0, "theta": 4.0},
    "fittingScore": 92.0,
    "confidenceLevel": "B",
    "confidenceReason": "data_quality=B, algorithm=B",
    "identifyMethod": "HISTORICAL_ARX",
    "excitationScore": 0.8,
    "residualTestPassed": True,
}


def _make_db_mock(db_record: MagicMock) -> tuple[MagicMock, MagicMock]:
    """构造 AsyncSessionLocal/会话 mock（endpoint/task 内懒导入，patch 源模块）。"""
    mock_session = MagicMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.get = AsyncMock(return_value=db_record)
    mock_session_local = MagicMock()
    mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_session_local, mock_session


async def _run_do_identify(identify_strategy: str) -> tuple[dict, MagicMock, MagicMock, MagicMock]:
    """以指定策略运行 _do_identify（外部依赖全 mock），返回结果与 mock。"""
    from app.tasks.tuning import _do_identify

    db_record = MagicMock()
    mock_session_local, _ = _make_db_mock(db_record)

    # V62-P3-005：辨识成功后创建 process_model_version CANDIDATE
    mock_version = MagicMock(id="version-p3-005")

    with (
        patch("app.core.db.AsyncSessionLocal", mock_session_local),
        patch("app.services.tuning_progress.init_progress", new=AsyncMock()),
        patch("app.services.tuning_progress.update_progress", new=AsyncMock()),
        patch(
            "app.services.tuning.identify_model_from_history",
            new=AsyncMock(return_value=dict(_HISTORY_FAILED)),
        ) as mock_history,
        patch(
            "app.services.tuning.identify_model",
            new=AsyncMock(return_value=dict(_STEP_RESULT)),
        ) as mock_step,
        patch(
            "app.tasks.tuning.create_candidate_version",
            new=AsyncMock(return_value=mock_version),
        ),
    ):
        result = await _do_identify(
            task_id="task-p16",
            loop_id="loop-1",
            start_time="2026-07-28T00:00:00Z",
            end_time="2026-07-28T01:00:00Z",
            candidate_model_types=None,
            theta_estimate=None,
            created_by="tester",
            identify_strategy=identify_strategy,
        )
    return result, db_record, mock_history, mock_step


class TestAutoStrategyFallback:
    """AUTO 策略：历史辨识失败/数据不足 → 降级阶跃实验路径并标注 fallback_step。"""

    async def test_auto_fallback_on_history_failure(self):
        """历史辨识 success=False + AUTO → 阶跃兜底成功，标注 dataSource=fallback_step。"""
        result, db_record, _, mock_step = await _run_do_identify("AUTO")

        mock_step.assert_awaited_once()
        assert result["success"] is True
        assert result["dataSource"] == "fallback_step"
        assert result["fallbackReason"] == "激励不足"
        assert result["identifyMethod"] == "STEP_TWO_POINT"
        assert result["params"] == {"K": 1.5, "tau": 25.0, "theta": 3.0}
        # 落库标记
        assert db_record.status == "IDENTIFIED"
        assert db_record.data_source == "fallback_step"
        assert db_record.model_type == "FOPDT"
        # V62-P3-005：model_params 不再写入 tuning_record，改为引用 process_model_version
        assert db_record.process_model_version_id == "version-p3-005"
        assert db_record.identify_method == "STEP_TWO_POINT"
        assert "AUTO 兜底" in db_record.confidence_reason

    async def test_auto_rejects_unvalidated_step_result(self):
        """identify_model 未提供单阶跃验证凭据时，AUTO 必须保持 INCONCLUSIVE。"""
        from app.tasks.tuning import _do_identify

        db_record = MagicMock()
        mock_session_local, _ = _make_db_mock(db_record)
        unvalidated = dict(_STEP_RESULT)
        unvalidated.pop("stepValidationPassed")

        with (
            patch("app.core.db.AsyncSessionLocal", mock_session_local),
            patch("app.services.tuning_progress.init_progress", new=AsyncMock()),
            patch("app.services.tuning_progress.update_progress", new=AsyncMock()),
            patch(
                "app.services.tuning.identify_model_from_history",
                new=AsyncMock(return_value=dict(_HISTORY_FAILED)),
            ),
            patch(
                "app.services.tuning.identify_model",
                new=AsyncMock(return_value=unvalidated),
            ),
        ):
            result = await _do_identify(
                task_id="task-p16-unvalidated",
                loop_id="loop-1",
                start_time="2026-07-28T00:00:00Z",
                end_time="2026-07-28T01:00:00Z",
                candidate_model_types=None,
                theta_estimate=None,
                created_by="tester",
                identify_strategy="AUTO",
            )

        assert result["success"] is False
        assert "单阶跃验证" in result["reason"]
        assert db_record.status == "INCONCLUSIVE"

    @pytest.mark.parametrize(
        "params,fitting_score",
        [
            ({"K": None, "tau": 25.0, "theta": 3.0}, 88.5),
            ({"K": math.nan, "tau": 25.0, "theta": 3.0}, 88.5),
            ({"K": math.inf, "tau": 25.0, "theta": 3.0}, 88.5),
            ({"K": 1.5, "tau": 0.0, "theta": 3.0}, 88.5),
            ({"K": 1.5, "tau": 25.0, "theta": -1.0}, 88.5),
            ({"K": 1.5, "tau": 25.0, "theta": 3.0}, math.nan),
        ],
    )
    async def test_auto_rejects_invalid_step_parameters(self, params, fitting_score):
        """空值、非有限值或非物理参数不得被 AUTO 包装成成功。"""
        from app.tasks.tuning import _do_identify

        db_record = MagicMock()
        mock_session_local, _ = _make_db_mock(db_record)
        invalid = {**_STEP_RESULT, "params": params, "fittingScore": fitting_score}

        with (
            patch("app.core.db.AsyncSessionLocal", mock_session_local),
            patch("app.services.tuning_progress.init_progress", new=AsyncMock()),
            patch("app.services.tuning_progress.update_progress", new=AsyncMock()),
            patch(
                "app.services.tuning.identify_model_from_history",
                new=AsyncMock(return_value=dict(_HISTORY_FAILED)),
            ),
            patch(
                "app.services.tuning.identify_model",
                new=AsyncMock(return_value=invalid),
            ),
        ):
            result = await _do_identify(
                task_id="task-p16-invalid-params",
                loop_id="loop-1",
                start_time="2026-07-28T00:00:00Z",
                end_time="2026-07-28T01:00:00Z",
                candidate_model_types=None,
                theta_estimate=None,
                created_by="tester",
                identify_strategy="AUTO",
            )

        assert result["success"] is False
        assert "参数无效" in result["reason"]
        assert db_record.status == "INCONCLUSIVE"

    async def test_history_only_never_falls_back(self):
        """HISTORY_ONLY 失败 → INCONCLUSIVE，不调用阶跃路径。"""
        result, db_record, _, mock_step = await _run_do_identify("HISTORY_ONLY")

        mock_step.assert_not_called()
        assert result["success"] is False
        assert db_record.status == "INCONCLUSIVE"
        assert db_record.confidence_reason == "激励不足"

    async def test_auto_fallback_when_both_paths_fail(self):
        """AUTO 兜底亦失败 → INCONCLUSIVE，reason 合并两条失败原因。"""
        from app.core.exceptions import BizError
        from app.tasks.tuning import _do_identify

        db_record = MagicMock()
        mock_session_local, _ = _make_db_mock(db_record)

        with (
            patch("app.core.db.AsyncSessionLocal", mock_session_local),
            patch("app.services.tuning_progress.init_progress", new=AsyncMock()),
            patch("app.services.tuning_progress.update_progress", new=AsyncMock()),
            patch(
                "app.services.tuning.identify_model_from_history",
                new=AsyncMock(return_value=dict(_HISTORY_FAILED)),
            ),
            patch(
                "app.services.tuning.identify_model",
                new=AsyncMock(
                    side_effect=BizError(
                        code="ERR_TUNING_DATA_INSUFFICIENT",
                        message="波形数据不足（5 点）",
                        status_code=400,
                    )
                ),
            ),
        ):
            result = await _do_identify(
                task_id="task-p16-both-fail",
                loop_id="loop-1",
                start_time="2026-07-28T00:00:00Z",
                end_time="2026-07-28T01:00:00Z",
                candidate_model_types=None,
                theta_estimate=None,
                created_by="tester",
                identify_strategy="AUTO",
            )

        assert result["success"] is False
        assert "激励不足" in result["reason"]
        assert "AUTO 阶跃兜底亦失败" in result["reason"]
        assert db_record.status == "INCONCLUSIVE"

    async def test_auto_fallback_on_history_biz_error(self):
        """历史路径抛 BizError（数据不足）也触发 AUTO 兜底（数据不足场景）。"""
        from app.core.exceptions import BizError
        from app.tasks.tuning import _do_identify

        db_record = MagicMock()
        mock_session_local, _ = _make_db_mock(db_record)
        mock_version = MagicMock(id="version-biz-err")

        with (
            patch("app.core.db.AsyncSessionLocal", mock_session_local),
            patch("app.services.tuning_progress.init_progress", new=AsyncMock()),
            patch("app.services.tuning_progress.update_progress", new=AsyncMock()),
            patch(
                "app.services.tuning.identify_model_from_history",
                new=AsyncMock(
                    side_effect=BizError(
                        code="ERR_TUNING_DATA_INSUFFICIENT",
                        message="预处理后数据不足（PV=20, OP=20 点）",
                        status_code=400,
                    )
                ),
            ),
            patch(
                "app.services.tuning.identify_model",
                new=AsyncMock(return_value=dict(_STEP_RESULT)),
            ) as mock_step,
            patch(
                "app.tasks.tuning.create_candidate_version",
                new=AsyncMock(return_value=mock_version),
            ),
        ):
            result = await _do_identify(
                task_id="task-p16-bizerr",
                loop_id="loop-1",
                start_time="2026-07-28T00:00:00Z",
                end_time="2026-07-28T01:00:00Z",
                candidate_model_types=None,
                theta_estimate=None,
                created_by="tester",
                identify_strategy="AUTO",
            )

        mock_step.assert_awaited_once()
        assert result["success"] is True
        assert result["dataSource"] == "fallback_step"
        assert "ERR_TUNING_DATA_INSUFFICIENT" in result["fallbackReason"]
        assert db_record.status == "IDENTIFIED"
        assert db_record.data_source == "fallback_step"

    async def test_auto_success_skips_fallback(self):
        """历史辨识成功 + AUTO → 不走兜底，数据来源保持 HISTORY 路径。"""
        from app.tasks.tuning import _do_identify

        db_record = MagicMock()
        mock_session_local, _ = _make_db_mock(db_record)
        mock_version = MagicMock(id="version-success")

        with (
            patch("app.core.db.AsyncSessionLocal", mock_session_local),
            patch("app.services.tuning_progress.init_progress", new=AsyncMock()),
            patch("app.services.tuning_progress.update_progress", new=AsyncMock()),
            patch(
                "app.services.tuning.identify_model_from_history",
                new=AsyncMock(return_value=dict(_HISTORY_SUCCESS)),
            ),
            patch(
                "app.services.tuning.identify_model",
                new=AsyncMock(return_value=dict(_STEP_RESULT)),
            ) as mock_step,
            patch(
                "app.tasks.tuning.create_candidate_version",
                new=AsyncMock(return_value=mock_version),
            ),
        ):
            result = await _do_identify(
                task_id="task-p16-success",
                loop_id="loop-1",
                start_time="2026-07-28T00:00:00Z",
                end_time="2026-07-28T01:00:00Z",
                candidate_model_types=None,
                theta_estimate=None,
                created_by="tester",
                identify_strategy="AUTO",
            )

        mock_step.assert_not_called()
        assert result["success"] is True
        assert result.get("dataSource") != "fallback_step"
        assert db_record.status == "IDENTIFIED"
        assert db_record.identify_method == "HISTORICAL_ARX"


class TestIdentifyHistoryEndpointStrategy:
    """端点把 identifyStrategy 透传给异步任务。"""

    def test_auto_strategy_passed_to_task(self, client):
        mock_task = MagicMock()
        mock_task.id = "celery-auto-001"

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
                },
            )

        assert resp.status_code == 200
        call_kwargs = mock_celery_task.delay.call_args.kwargs
        assert call_kwargs["identify_strategy"] == "AUTO"

    def test_history_only_strategy_passed_to_task(self, client):
        mock_task = MagicMock()
        mock_task.id = "celery-hist-001"

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
                },
            )

        assert resp.status_code == 200
        call_kwargs = mock_celery_task.delay.call_args.kwargs
        assert call_kwargs["identify_strategy"] == "HISTORY_ONLY"


# ---------------------------------------------------------------------------
# P1-5 + 数据迁移：g7a8b9c0d1e2 迁移断言与 schemas 契约
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "g7a8b9c0d1e2_tuning_simc_check_and_ngi_threshold.py"
)


def test_simc_ngi_migration_revision_chain() -> None:
    """新迁移存在、revision 链接当前 head 前驱 f6a7b8c9d0e1、upgrade/downgrade 均已实现。"""
    assert _MIGRATION_PATH.exists(), f"缺少迁移 {_MIGRATION_PATH.name}"
    spec = importlib.util.spec_from_file_location("tuning_simc_ngi_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "g7a8b9c0d1e2"
    assert module.down_revision == "f6a7b8c9d0e1"
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_simc_ngi_migration_content() -> None:
    """迁移内容：SIMC 入 algo CHECK、fallback_step 入 data_source CHECK、NGI 0.001↔1.0。"""
    src = _MIGRATION_PATH.read_text(encoding="utf-8")
    # SIMC CHECK（DROP + ADD 重写）
    assert "ck_tuning_record_algo" in src
    assert "'COHEN_COON', 'SIMC'" in src
    # fallback_step CHECK
    assert "ck_tuning_record_data_source" in src
    assert "'STEP_EXPERIMENT', 'fallback_step'" in src
    # NGI 阈值数据修复（upgrade 1.0 / downgrade 0.001，jsonb_set 保留 sibling 键）
    assert "choudhury_ngi_threshold" in src
    assert "'1.0'::jsonb" in src
    assert "'0.001'::jsonb" in src
    assert "VALVE_STICTION" in src


def test_datasource_schema_accepts_fallback_step() -> None:
    """CreateTuningTaskRequest 接受 dataSource=fallback_step（兜底记录可保存）。"""
    from app.schemas.tuning import CreateTuningTaskRequest

    req = CreateTuningTaskRequest(
        loopId="loop-1",
        modelType="FOPDT",
        modelParams={"K": 1.5, "tau": 25.0, "theta": 3.0},
        algorithm="SIMC",
        recommendedPid={"kp": 1.0, "ti": 10.0, "td": 0.0},
        identifyMethod="STEP_TWO_POINT",
        dataSource="fallback_step",
    )
    assert req.dataSource == "fallback_step"
    assert req.algorithm == "SIMC"
