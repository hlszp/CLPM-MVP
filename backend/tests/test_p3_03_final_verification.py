"""P3-03 终验节点：独立连续模型终验（模型级终验结论 + 最佳解释/可整定分离）.

任务书：执行状态/P3第十波-任务书-20261012.md §二。范围与判据：
- 模型级终验结论：消费 06a finalTest（尾部 20% 终验段）+ 06c finalEventTest
  （独立动态事件终验）双通道合成四态 PASSED/FAILED/NO_DYNAMICS/NOT_RUN +
  依据链（跨窗稳定性 P-25/重选不确定性 P-26 入链）；落 evidence.
  finalVerification + manifest resultEvidence.finalVerification（加性）。
- 可整定判定 = 终验通过（PASSED）+ 结构支持（FOPDT）——B1 modelRole 字段的
  判定语义在本节点定稿；SOPDT/IPDT 可整定限制保留（P3-07 未过）。
- 最后 20% 实际参与评价：训练好但终验差不提升可信度（降级/标注语义，
  03 号文 P3-03 验收行；C19-ID2）。
- 终验不参与选模（07§6）：只降不升，不因终验失败回取次优候选。

oracle 独立性（冻结草案 §0.2）：生成器独立于被测实现——FOPDT/SOPDT 用
scipy cont2discrete+dlsim 精确 ZOH（非 ARX 递归），并以解析阶跃响应核对
生成器自身；容差常量命名常驻（禁失败后调整，R15）。
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import signal as sp_signal

from app.services.tuning_identification.pipeline import (
    _apply_closed_loop_finalization,
    identify_from_history,
)
from app.services.tuning_identification.types import (
    AlgorithmConfidenceLevel,
    CandidateModel,
    IdentificationResult,
    IdentifyMethod,
    ModelEvidence,
    ModelParams,
    ModelType,
)

# ---------------------------------------------------------------------------
# 独立生成器（不 import 被测实现的合成路径；scipy ZOH 为外部参考）
# ---------------------------------------------------------------------------


def _white_u(n: int, seed: int, amp: float = 5.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(-amp, amp, n)


def _zoh_fopdt(
    K: float,
    tau: float,
    theta: float,
    u: np.ndarray,
    ts: float,
    noise_std: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """FOPDT 精确 ZOH 离散仿真（scipy cont2discrete + dlsim，外部参考实现）."""
    n_d = max(int(round(theta / ts)), 0)
    sysd = sp_signal.cont2discrete(([K], [tau, 1.0]), ts, method="zoh")
    b_c, a_c = sysd[0].flatten(), sysd[1]
    u_d = np.zeros_like(u, dtype=float)
    if n_d > 0:
        u_d[n_d:] = u[:-n_d]
    else:
        u_d = u.astype(float)
    _, y = sp_signal.dlsim((b_c, a_c, 1), u_d)
    y = y.flatten()
    if noise_std > 0:
        rng = np.random.default_rng(seed)
        y = y + rng.normal(0.0, noise_std, len(y))
    return y


def _zoh_sopdt(
    K: float,
    T1: float,
    T2: float,
    theta: float,
    u: np.ndarray,
    ts: float,
    noise_std: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """SOPDT 精确 ZOH 离散仿真（scipy cont2discrete + dlsim）."""
    n_d = max(int(round(theta / ts)), 0)
    sysd = sp_signal.cont2discrete(([K], [T1 * T2, T1 + T2, 1.0]), ts, method="zoh")
    b_c, a_c = sysd[0].flatten(), sysd[1]
    u_d = np.zeros_like(u, dtype=float)
    if n_d > 0:
        u_d[n_d:] = u[:-n_d]
    else:
        u_d = u.astype(float)
    _, y = sp_signal.dlsim((b_c, a_c, 1), u_d)
    y = y.flatten()
    if noise_std > 0:
        rng = np.random.default_rng(seed)
        y = y + rng.normal(0.0, noise_std, len(y))
    return y


class TestGeneratorSelfCheck:
    """生成器解析自证：ZOH 一阶/二阶阶跃响应末值=K·Δu（独立参考）."""

    def test_fopdt_step_response_matches_analytic(self):
        K, tau, theta, ts = 1.5, 40.0, 8.0, 1.0
        step = np.ones(4000)
        y = _zoh_fopdt(K, tau, theta, step, ts)
        # 解析：t→∞ 末值 = K；t = 100τ 处应达 K·(1-e^{-100}) ≈ K
        assert abs(y[-1] - K) < 1e-3
        # 解析中间点（t=τ+θ 处达 63.2%）
        k = int(tau + theta)
        expected = K * (1.0 - np.exp(-(k * ts - theta) / tau))
        assert abs(y[k] - expected) < 0.02 * K

    def test_sopdt_step_response_matches_analytic(self):
        K, T1, T2, theta, ts = 1.0, 20.0, 8.0, 6.0, 1.0
        step = np.ones(6000)
        y = _zoh_sopdt(K, T1, T2, theta, step, ts)
        assert abs(y[-1] - K) < 1e-3


# ---------------------------------------------------------------------------
# 管线级：模型级终验结论四态（任务书【4】"四态各自例"）
# ---------------------------------------------------------------------------


class TestModelLevelFinalVerificationStates:
    """四态各自例 + 最后 20% 实际参与评价（C19-ID2）."""

    def test_passed_consistent_system(self):
        """一致系统：模型级终验 PASSED + 可整定（终验通过+结构支持）."""
        K, tau, theta, ts, n = 1.5, 40.0, 8.0, 1.0, 4000
        u = _white_u(n, seed=501)
        y0 = _zoh_fopdt(K, tau, theta, u, ts)
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=0.01 * np.ptp(y0), seed=502)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None and ev.final_verification is not None
        fv = ev.final_verification
        assert fv["status"] == "PASSED"
        # 双通道明细 + 依据链（finalTest 必有；finalEventTest 无 SP 事件=未运行）
        assert fv["channels"]["finalTest"] is not None
        assert fv["channels"]["finalTest"]["status"] == "PASSED"
        assert any(s.startswith("finalTest: PASSED") for s in fv["basis"])
        assert any("finalEventTest: 未运行" in s for s in fv["basis"])
        # to_dict 加性键（API/快照消费面）
        d = result.to_dict()
        assert d["evidence"]["finalVerification"]["status"] == "PASSED"
        # 可整定判定：终验通过 + 结构支持 → tunable 输出
        role = d["modelRole"]
        assert role["tunable"] is not None
        assert role["tunable"]["isBestModel"] is True
        assert role["tunable"]["finalVerification"] == "PASSED"

    def test_failed_time_varying_gain_not_promoted(self):
        """终验差降级反例（C19-ID2）：训练好（R²_train≈1.0）终验差 → 模型级
        FAILED、可信度封顶 C 不晋升、tunable=null（终验差不提升可信度）."""
        K, tau, theta, ts, n = 1.2, 25.0, 8.0, 1.0, 3000
        u = _white_u(n, seed=801, amp=10.0)
        y1 = _zoh_fopdt(K, tau, theta, u, ts, noise_std=0.02, seed=802)
        y2 = _zoh_fopdt(K / 3.0, tau, theta, u, ts, noise_std=0.02, seed=803)
        n_change = n - int(n * 0.2)
        y = np.concatenate([y1[:n_change], y2[n_change:]])
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None and ev.final_verification is not None
        assert ev.final_verification["status"] == "FAILED"
        # 训练好（训练分数高）——但终验差，可信度不得晋升 A/B
        assert ev.r2_train >= 0.90, "构造前提：训练段拟合好"
        rank = {
            AlgorithmConfidenceLevel.A: 5,
            AlgorithmConfidenceLevel.B: 4,
            AlgorithmConfidenceLevel.C: 3,
            AlgorithmConfidenceLevel.D: 2,
            AlgorithmConfidenceLevel.E: 1,
        }
        assert rank.get(result.best_model.confidence, 0) <= 3, "终验差不提升可信度"
        # 可整定判定：终验未通过 → tunable=null + 显式终验状态
        role = result.to_dict()["modelRole"]
        assert role["tunable"] is None
        assert "FAILED" in role["note"]
        assert "终验" in role["note"]

    def test_no_dynamics_stationary_tail_not_claimed_passed(self):
        """近稳态终验段：模型级 NO_DYNAMICS 不伪称通过——tunable=null
        （终验未实际验证到动态，不可整定）."""
        K, tau, theta, ts, n = 1.2, 25.0, 8.0, 1.0, 3000
        u = _white_u(n, seed=521)
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=0.02, seed=522)
        n_test = n - int(n * 0.8)
        y[-n_test:] = y[-n_test] + 0.1 * np.sin(np.arange(n_test) * 0.01)
        u[-n_test:] = u[-n_test] * 0.01  # 输入近稳态（物理一致）
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None and ev.final_verification is not None
        assert ev.final_verification["status"] == "NO_DYNAMICS"
        role = result.to_dict()["modelRole"]
        assert role["tunable"] is None, "无动态终验不伪称通过——不可整定"
        assert "NO_DYNAMICS" in role["note"]

    def test_not_run_short_data(self):
        """短数据（70/30 退化无终验段）+ 无独立事件：模型级 NOT_RUN
        （与 NO_DYNAMICS 显式区分；终验未运行同样不可整定）."""
        K, tau, theta, ts, n = 1.2, 8.0, 2.0, 1.0, 90
        u = _white_u(n, seed=531)
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=0.01, seed=532)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None
        assert ev.final_test is None  # 短数据 70/30：终验段不存在（06a 口径）
        assert ev.final_verification is not None
        fv = ev.final_verification
        assert fv["status"] == "NOT_RUN"
        assert fv["channels"]["finalTest"] is None
        role = result.to_dict()["modelRole"]
        assert role["tunable"] is None
        assert "NOT_RUN" in role["note"]


# ---------------------------------------------------------------------------
# 函数级：四态合成矩阵（双通道组合语义）
# ---------------------------------------------------------------------------


def _ev(
    ft: dict | None = None,
    fe: dict | None = None,
    reselect: dict | None = None,
) -> ModelEvidence:
    return ModelEvidence(final_test=ft, final_event_test=fe, reselect_uncertainty=reselect)


class TestVerificationSynthesisMatrix:
    """双通道合成规则（保守，只降不升）：任一 FAILED 即 FAILED；否则任一
    PASSED 即 PASSED；否则运行通道全 NO_DYNAMICS → NO_DYNAMICS；否则
    NOT_RUN。"""

    @staticmethod
    def _sum(ev: ModelEvidence, cw: dict | None = None) -> str:
        from app.services.tuning_identification.pipeline import _summarize_final_verification

        return _summarize_final_verification(ev, cw)["status"]

    def test_any_failed_dominates(self):
        assert (
            self._sum(
                _ev(
                    ft={"status": "FAILED", "r2": 0.1, "n": 100, "ptpRange": 10.0},
                    fe={"status": "PASSED", "r2": 0.9, "nrmse": 0.05, "eventId": "e1"},
                )
            )
            == "FAILED"
        )

    def test_single_channel_passed(self):
        assert self._sum(_ev(ft={"status": "PASSED", "r2": 0.9, "n": 100, "ptpRange": 10.0})) == (
            "PASSED"
        )

    def test_passed_plus_no_dynamics(self):
        assert (
            self._sum(
                _ev(
                    ft={"status": "NO_DYNAMICS", "r2": 0.0, "n": 100, "ptpRange": 0.1},
                    fe={"status": "PASSED", "r2": 0.9, "nrmse": 0.05, "eventId": "e1"},
                )
            )
            == "PASSED"
        )

    def test_all_no_dynamics(self):
        assert self._sum(
            _ev(ft={"status": "NO_DYNAMICS", "r2": 0.0, "n": 100, "ptpRange": 0.1})
        ) == ("NO_DYNAMICS")

    def test_no_channels_not_run(self):
        assert self._sum(_ev()) == "NOT_RUN"

    def test_evidence_chain_contains_all_sources(self):
        """依据链：双通道 + 跨窗稳定性（P-25）+ 重选不确定性（P-26）全记录."""
        from app.services.tuning_identification.pipeline import _summarize_final_verification

        ev = _ev(
            ft={"status": "PASSED", "r2": 0.92, "n": 600, "ptpRange": 30.0},
            fe={"status": "PASSED", "r2": 0.88, "nrmse": 0.05, "eventId": "ev-9"},
            reselect={
                "status": "COMPLETED",
                "nRepetitions": 48,
                "theta": {"ci95": [7.0, 9.0]},
            },
        )
        fv = _summarize_final_verification(
            ev, {"stable": True, "maxRelativeDeviation": 0.12, "threshold": 0.25}
        )
        joined = " | ".join(fv["basis"])
        assert "finalTest: PASSED" in joined
        assert "finalEventTest: PASSED" in joined
        assert "crossWindowStability: stable" in joined
        assert "reselectUncertainty: COMPLETED" in joined
        assert fv["crossWindowStability"]["stable"] is True
        assert fv["reselectUncertainty"]["nRepetitions"] == 48


# ---------------------------------------------------------------------------
# 单元级：modelRole 可整定判定语义矩阵（终验通过 + 结构支持）
# ---------------------------------------------------------------------------


def _cand(
    model_type: ModelType,
    score: float,
    conf: str = "A",
    fv_status: str | None = "__unset__",
) -> CandidateModel:
    params = ModelParams(
        model_type=model_type, K=1.0, tau=30.0 if model_type == ModelType.FOPDT else 0.0, theta=5.0
    )
    evidence = None
    if fv_status != "__unset__":
        evidence = ModelEvidence(
            final_verification=None if fv_status is None else {"status": fv_status}
        )
    return CandidateModel(
        params=params,
        fitting_score=score,
        confidence=AlgorithmConfidenceLevel(conf),
        identify_method=IdentifyMethod.HISTORICAL_ARX,
        residual_test_passed=True,
        excitation_score=80.0,
        evidence=evidence,
    )


class TestTunableGateSemantics:
    """可整定判定 = 终验通过（PASSED）+ 结构支持（FOPDT），缺一不可."""

    def test_passed_fopdt_tunable(self):
        best = _cand(ModelType.FOPDT, 95.0, fv_status="PASSED")
        role = IdentificationResult(success=True, best_model=best, candidates=[best]).to_dict()[
            "modelRole"
        ]
        assert role["tunable"] is not None
        assert role["tunable"]["isBestModel"] is True
        assert role["tunable"]["finalVerification"] == "PASSED"

    @pytest.mark.parametrize("status", ["FAILED", "NO_DYNAMICS", "NOT_RUN"])
    def test_fopdt_blocked_by_non_passed_verification(self, status):
        best = _cand(ModelType.FOPDT, 95.0, fv_status=status)
        role = IdentificationResult(success=True, best_model=best, candidates=[best]).to_dict()[
            "modelRole"
        ]
        assert role["tunable"] is None
        assert status in role["note"]

    def test_missing_verification_keeps_b1_structure_only_behavior(self):
        """终验结论缺失（未装配：内层调用/旧结果/单元构造）→ 维持 B1 结构
        判定 + 显式标注缺失（不静默拒绝/放行）."""
        best = _cand(ModelType.FOPDT, 95.0, fv_status=None)
        role = IdentificationResult(success=True, best_model=best, candidates=[best]).to_dict()[
            "modelRole"
        ]
        assert role["tunable"] is not None
        assert role["tunable"]["finalVerification"] is None
        assert "终验结论缺失" in role["note"]

    def test_separation_sopdt_best_fopdt_tunable_both_passed(self):
        best = _cand(ModelType.SOPDT, 97.0, fv_status="PASSED")
        fopdt = _cand(ModelType.FOPDT, 90.0, conf="B", fv_status="PASSED")
        role = IdentificationResult(
            success=True, best_model=best, candidates=[best, fopdt]
        ).to_dict()["modelRole"]
        assert role["bestExplanatory"]["modelType"] == "SOPDT"
        assert role["bestExplanatory"]["finalVerification"] == "PASSED"
        assert role["tunable"]["modelType"] == "FOPDT"
        assert role["tunable"]["isBestModel"] is False
        assert role["tunable"]["finalVerification"] == "PASSED"

    def test_separation_tunable_candidate_failed_blocks(self):
        """结构支持的可整定候选终验 FAILED → tunable=null（终验不参与选模，
        不回取次优候选）."""
        best = _cand(ModelType.SOPDT, 97.0, fv_status="PASSED")
        fopdt = _cand(ModelType.FOPDT, 90.0, conf="B", fv_status="FAILED")
        role = IdentificationResult(
            success=True, best_model=best, candidates=[best, fopdt]
        ).to_dict()["modelRole"]
        assert role["tunable"] is None
        assert "FAILED" in role["note"]

    def test_structure_restriction_retained(self):
        """SOPDT/IPDT 可整定限制保留（P3-07 未过；终验通过也不放开结构）."""
        assert IdentificationResult.TUNABLE_MODEL_TYPES == ("FOPDT",)
        best_sopdt = _cand(ModelType.SOPDT, 97.0, fv_status="PASSED")
        role = IdentificationResult(
            success=True, best_model=best_sopdt, candidates=[best_sopdt]
        ).to_dict()["modelRole"]
        # 候选池无 FOPDT：终验通过的 SOPDT 也不可整定（结构不支持）
        assert role["tunable"] is None
        assert "P3-07" in role["note"]


# ---------------------------------------------------------------------------
# 函数级：终验分离装配（可整定候选独立事件终验）
# ---------------------------------------------------------------------------


class TestTunableCandidateIndependentEventTest:
    """终验分离：可整定候选（≠胜出模型）独立运行 P-24 事件终验并汇总
    自己的模型级终验结论；终验失败只记证据不改可信度（不参与选择）."""

    @staticmethod
    def _make_case():
        K, tau, theta, ts = 1.5, 30.0, 6.0, 1.0
        n = 12000
        u = _white_u(n, seed=541, amp=8.0)
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=0.05, seed=542)
        # 事件窗裁剪规则（_event_window_bounds）：段 200min → 120min 档
        # w=7200；事件 9000 → start=9000-1800=7200，end 被 12000 截断后
        # start=4800 → 窗 [4800:12000]，与选模窗 (0, 2000) 不重叠
        events = [{"eventId": "ev-t1", "type": "SP_STEP", "index": 9000}]
        qualified = [{"indexStart": 0, "indexEnd": n, "segmentId": "seg-1"}]
        # 胜出模型（最佳解释，SOPDT）与可整定候选（FOPDT）的参数均与生成
        # 数据一致（终验自由仿真消费候选参数；SOPDT 取近一阶 T2=0.5）
        best = CandidateModel(
            params=ModelParams(model_type=ModelType.SOPDT, K=1.5, theta=6.0, T1=30.0, T2=0.5),
            fitting_score=96.0,
            confidence=AlgorithmConfidenceLevel.A,
            identify_method=IdentifyMethod.HISTORICAL_ARX,
            residual_test_passed=True,
            excitation_score=80.0,
            evidence=ModelEvidence(
                final_test={"status": "PASSED", "r2": 0.93, "n": 600, "ptpRange": 25.0}
            ),
        )
        tunable = CandidateModel(
            params=ModelParams(model_type=ModelType.FOPDT, K=1.5, tau=30.0, theta=6.0),
            fitting_score=91.0,
            confidence=AlgorithmConfidenceLevel.B,
            identify_method=IdentifyMethod.HISTORICAL_ARX,
            residual_test_passed=True,
            excitation_score=80.0,
            evidence=ModelEvidence(
                final_test={"status": "PASSED", "r2": 0.90, "n": 600, "ptpRange": 25.0}
            ),
        )
        result = IdentificationResult(success=True, best_model=best, candidates=[best, tunable])
        return result, best, tunable, u, y, events, qualified, ts

    def test_tunable_candidate_gets_own_event_test_and_verification(self):
        result, best, tunable, u, y, events, qualified, ts = self._make_case()
        conf_before = tunable.confidence
        _apply_closed_loop_finalization(
            result=result,
            best=best,
            u_full=u,
            y_full=y,
            sp_full=None,
            events=events,
            qualified=qualified,
            selection_windows=[(0, 2000)],
            reselect_segments=[],
            ts=ts,
            lowpass_meta=None,
        )
        # 胜出模型：模型级终验结论就位
        assert best.evidence.final_verification is not None
        # 可整定候选：独立事件终验已运行（其对象上的 final_event_test 非空）
        assert tunable.evidence.final_event_test is not None
        ev_status = tunable.evidence.final_event_test["status"]
        assert ev_status in {"PASSED", "FAILED", "NO_DYNAMICS"}
        # 一致系统构造 → 可整定候选应通过独立事件终验（对独立参考自由仿真）
        assert ev_status == "PASSED", (
            f"一致系统可整定候选事件终验应为 PASSED，实得 {ev_status}"
            f"（nrmse={tunable.evidence.final_event_test['nrmse']}）"
        )
        # 可整定候选自己的模型级终验结论（终验分离：与胜出模型各自汇总）
        assert tunable.evidence.final_verification is not None
        assert tunable.evidence.final_verification["status"] == "PASSED"
        # 终验不参与选择：可整定候选可信度不被终验改变
        assert tunable.confidence == conf_before
        # modelRole 消费：终验通过 + 结构支持 → tunable 输出
        role = result.to_dict()["modelRole"]
        assert role["tunable"] is not None
        assert role["tunable"]["modelType"] == "FOPDT"
        assert role["tunable"]["finalVerification"] == "PASSED"

    def test_failed_verification_keeps_candidate_but_blocks_role(self):
        """可整定候选事件终验失败：证据记录在案（reason code），modelRole
        判 tunable=null；候选本体保留在候选池可审计（不静默丢弃）."""
        result, best, tunable, u, y, events, qualified, ts = self._make_case()
        # 直接注入失败终验结论（模拟终验失败场景），验证 modelRole 判定
        tunable.evidence.final_event_test = {
            "status": "FAILED",
            "r2": 0.1,
            "nrmse": 0.9,
            "nrmseThreshold": 0.10,
            "n": 1700,
            "window": {"start": 3300, "end": 5000},
            "segmentId": "seg-1",
        }
        from app.services.tuning_identification.pipeline import _summarize_final_verification

        tunable.evidence.final_verification = _summarize_final_verification(tunable.evidence)
        role = result.to_dict()["modelRole"]
        assert role["tunable"] is None
        assert "FAILED" in role["note"]
        # 候选仍留在候选池（终验不参与选模，不静默丢弃）
        assert tunable in result.candidates


# ---------------------------------------------------------------------------
# 管线级：最佳解释 vs 可整定分离（SOPDT 胜出 + FOPDT 回取）
# ---------------------------------------------------------------------------


class TestRoleSeparationPipeline:
    def test_sopdt_best_fopdt_tunable_with_separate_verification(self):
        """noiseless SOPDT 开环：SOPDT 胜出（最佳解释，终验 PASSED）+ 候选池
        FOPDT 回取为可整定候选——但后者终验独立判定为 FAILED → tunable=null。

        终验分离的管线级落地：最佳解释模型与可整定候选**各自持有独立的
        模型级终验结论**，gate 按各自状态执行（不混淆、不代偿）。回取
        正例（可整定候选终验 PASSED → tunable 输出）由函数级
        TestTunableCandidateIndependentEventTest 覆盖。
        """
        K, T1, T2, theta, ts, n = 1.0, 20.0, 8.0, 6.0, 1.0, 4000
        u = _white_u(n, seed=551, amp=6.0)
        y = _zoh_sopdt(K, T1, T2, theta, u, ts)  # noiseless：结构选择不被噪声扰动
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            candidate_models=[ModelType.FOPDT, ModelType.SOPDT],
        )
        assert result.success, result.reason
        role = result.to_dict()["modelRole"]
        # 最佳解释 = SOPDT（二阶真系统，noiseless 下 SOPDT 拟合优于一阶）
        assert role["bestExplanatory"]["modelType"] == "SOPDT"
        assert role["bestExplanatory"]["finalVerification"] == "PASSED"
        # 终验分离：可整定候选（候选池 FOPDT）有自己的终验结论
        tunable_cand = result.select_tunable_candidate()
        assert tunable_cand is not None and tunable_cand is not result.best_model
        assert tunable_cand.params.model_type == ModelType.FOPDT
        assert tunable_cand.evidence is not None
        tc_fv = tunable_cand.evidence.final_verification
        assert tc_fv is not None, "可整定候选应有独立模型级终验结论（终验分离）"
        # noiseless 强二阶真系统：一阶近似模型终验段解释力不足 → FAILED
        # → 可整定判定（终验通过+结构支持）不成立，tunable=null
        assert tc_fv["status"] == "FAILED"
        assert role["tunable"] is None
        assert "FAILED" in role["note"]
        # SOPDT/IPDT 可整定结构限制保留（结构集断言见单元级
        # test_structure_restriction_retained：终验通过的最佳解释 SOPDT 也不放开）


# ---------------------------------------------------------------------------
# schema 承接（evidence.to_dict / 旧结果兼容）
# ---------------------------------------------------------------------------


class TestFinalVerificationSchema:
    def test_to_dict_additive_key_and_legacy_none(self):
        """evidence.to_dict 加性键 finalVerification；final_verification=None
        （旧结果/未装配）时键存在值 None（不伪补）."""
        ev = ModelEvidence()
        d = ev.to_dict()
        assert "finalVerification" in d
        assert d["finalVerification"] is None
        ev.final_verification = {"status": "PASSED", "basis": ["..."]}
        assert ev.to_dict()["finalVerification"]["status"] == "PASSED"

    def test_pipeline_evidence_dict_carries_final_verification(self):
        """管线级：to_dict 的 evidence.finalVerification 与四态枚举一致."""
        K, tau, theta, ts, n = 1.5, 40.0, 8.0, 1.0, 4000
        u = _white_u(n, seed=561)
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=0.05, seed=562)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        fv = result.to_dict()["evidence"]["finalVerification"]
        assert fv is not None
        assert fv["status"] in {"PASSED", "FAILED", "NO_DYNAMICS", "NOT_RUN"}
        assert isinstance(fv["basis"], list) and len(fv["basis"]) >= 2
        assert set(fv.keys()) >= {"status", "channels", "crossWindowStability", "basis", "note"}
