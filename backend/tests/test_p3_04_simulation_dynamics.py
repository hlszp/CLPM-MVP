"""P3-04 仿真动力学与工作量预算测试（执行包「系统改造优化-2026-10-10」第十波）.

对齐 04 号文 C20 与 C20-ID 验收子例：
- C20：IPDT 恒定输入真实积分；有无扰动响应不同；相同步长收敛；
  候选重复去重；超预算转异步或拒绝。
- C20-ID：真积分响应正确；反馈 OP 随负载变化；步长收敛；
  超预算/取消/失败明确。

oracle 独立性纪律（oracle 冻结草案 §0.2）：全部解析参考为独立闭式推导
（IPDT P-only 闭环 e^{-at} 解、IPDT+P 稳定性边界 Kc·K·θ=π/2、
FOPDT+P+负载阶跃两段指数解），不使用被测仿真器自身生成参考。
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BizError
from app.services.tuning_algorithms import (
    SIM_BUDGET_ASYNC,
    SIM_BUDGET_SYNC,
    SIMULATION_ENGINE_VERSION,
    PIDParams,
    SimulationBudgetError,
    SimulationNotSupportedError,
    simulate_closed_loop,
    simulate_plant_open_loop,
)
from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 独立解析参考（手算闭式解，非被测仿真器输出）
# ---------------------------------------------------------------------------


def _ipdt_p_only_closed_form(k: float, kc: float, sp: float, t: float) -> float:
    """IPDT(θ=0) + P-only 闭环解析解：x(t) = SP·(1 - e^{-K·Kc·t})。

    推导：x' = K·(Kc·(SP - x)) → 闭环一阶系统，时间常数 1/(K·Kc)。
    """
    a = k * kc
    return sp * (1.0 - math.exp(-a * t))


def _fopdt_p_only_with_load_step(
    t: float,
    *,
    k: float,
    tau: float,
    kc: float,
    sp: float,
    d: float,
    t_d: float,
) -> float:
    """FOPDT(θ=0) + P-only + t_d 时刻负载阶跃 d 的两段指数解析解。

    推导：τ·x' = -x + K·(Kc·(SP-x) + d) →
    a = (1+K·Kc)/τ，x_ss(d) = (K·Kc·SP + K·d)/(1+K·Kc)；
    t<t_d: x(t) = x_ss(0)·(1-e^{-at})；t≥t_d:
    x(t) = x_ss(d) + (x(t_d) - x_ss(d))·e^{-a(t-t_d)}。
    """
    a = (1.0 + k * kc) / tau

    def _xss(dd: float) -> float:
        return (k * kc * sp + k * dd) / (1.0 + k * kc)

    x_td = _xss(0.0) * (1.0 - math.exp(-a * t_d))
    if t < t_d:
        return _xss(0.0) * (1.0 - math.exp(-a * t))
    return _xss(d) + (x_td - _xss(d)) * math.exp(-a * (t - t_d))


# ---------------------------------------------------------------------------
# C20：IPDT 真实积分动力学
# ---------------------------------------------------------------------------


class TestIPDTRealIntegratorDynamics:
    """IPDT 必须按真实积分动力学仿真（禁 FOPDT 冒充）。"""

    def test_open_loop_constant_input_exact_linear_ramp(self):
        """C20：积分过程恒定输入 → 精确线性趋势（斜率=K·OP，二阶差分=0）。"""
        k, theta, op = 0.2, 10.0, 5.0
        result = simulate_plant_open_loop("IPDT", {"K": k, "theta": theta}, op, 100.0, 1.0)
        pv = result["pv"]
        # 时滞窗口内 PV 恒为 0
        assert all(v == 0.0 for v in pv[:11])
        # 时滞后精确线性：pv(t) = K·OP·(t-θ)
        slope = k * op
        for i in range(10, len(pv)):
            expected = slope * (i - 10)
            assert abs(pv[i] - expected) < 1e-9, f"t={i}s 处偏离线性趋势"
        # 二阶差分严格为 0（真积分器，非一阶惯性饱和形）
        second_diff = max(abs(pv[i] - 2 * pv[i - 1] + pv[i - 2]) for i in range(12, len(pv)))
        assert second_diff == 0.0

    def test_open_loop_ramp_unbounded_not_saturating(self):
        """反例：IPDT 不产生 FOPDT 型饱和响应（旧缺陷=按 tau=30 FOPDT 仿真）。"""
        result = simulate_plant_open_loop("IPDT", {"K": 0.2, "theta": 0.0}, 5.0, 400.0, 2.0)
        pv = result["pv"]
        # FOPDT(K=0.2,tau=30) 稳态 = K·OP = 1.0；真积分器 400s 已达 0.2·5·400=400
        assert pv[-1] == pytest.approx(0.2 * 5.0 * 400.0, rel=1e-9)
        assert pv[-1] > 10.0  # 远超任何一阶惯性稳态 → 非冒充

    def test_ipdt_p_only_stability_boundary(self):
        """C20-ID：P-only 解析稳定性边界 Kc·K·θ=π/2 两侧一稳一发散。"""
        k, theta = 0.1, 10.0
        boundary_kc = math.pi / (2.0 * k * theta)  # = π/2 ≈ 1.5708
        pid_inner = PIDParams(kp=0.75 * boundary_kc, ti=0.0, td=0.0)
        pid_outer = PIDParams(kp=1.4 * boundary_kc, ti=0.0, td=0.0)

        result_inner = simulate_closed_loop(
            "IPDT", {"K": k, "theta": theta}, pid_inner, pid_inner, 240.0, 0.5
        )
        pv = result_inner["recommendedResponse"]["pv"]
        quarter = len(pv) // 4
        first_peak = max(abs(v) for v in pv[:quarter])
        last_peak = max(abs(v) for v in pv[-quarter:])
        # 界内：振荡衰减（稳定）
        assert last_peak < 0.8 * first_peak, (
            f"Kc·K·θ={0.75 * math.pi / 2:.3f} < π/2 应衰减稳定："
            f"首段峰值 {first_peak:.3f} vs 末段 {last_peak:.3f}"
        )

        result_outer = simulate_closed_loop(
            "IPDT", {"K": k, "theta": theta}, pid_outer, pid_outer, 240.0, 0.5
        )
        pv_o = result_outer["recommendedResponse"]["pv"]
        first_peak_o = max(abs(v) for v in pv_o[:quarter])
        last_peak_o = max(abs(v) for v in pv_o[-quarter:])
        # 界外：振荡发散（不稳定）
        assert last_peak_o > 2.0 * first_peak_o, (
            f"Kc·K·θ={1.4 * math.pi / 2:.3f} > π/2 应发散："
            f"首段峰值 {first_peak_o:.3f} vs 末段 {last_peak_o:.3f}"
        )

    def test_ipdt_step_convergence_vs_analytic(self):
        """C20-ID：步长收敛——对解析解误差随步长减半而下降（一阶 ZOH）。"""
        k, kc, sp, duration = 0.5, 1.0, 1.0, 8.0

        def _max_err(h: float) -> float:
            result = simulate_closed_loop(
                "IPDT",
                {"K": k, "theta": 0.0},
                PIDParams(kc, 0.0, 0.0),
                PIDParams(kc, 0.0, 0.0),
                duration,
                h,
            )
            pv = result["recommendedResponse"]["pv"]
            ts = result["timestamps"]
            return max(
                abs(pv[i] - _ipdt_p_only_closed_form(k, kc, sp, ts[i])) for i in range(len(pv))
            )

        err_coarse = _max_err(0.1)
        err_fine = _max_err(0.05)
        # 误差随步长减半显著下降（ZOH 采样一阶收敛，实测比 ≈0.5）
        assert err_fine < 0.8 * err_coarse, (
            f"步长减半后误差未按收敛阶下降：err(0.1)={err_coarse:.3e}, err(0.05)={err_fine:.3e}"
        )
        # 细步长下对独立解析解偏差 ≤1%（O-D1 容差口径）
        assert err_fine / sp <= 0.01, f"err(0.05)={err_fine:.3e} 超出解析解 1% 容差"

    def test_ipdt_closed_loop_pi_converges_to_setpoint(self):
        """IPDT + PI 闭环 PV 收敛到设定值（积分过程闭环可控）。"""
        result = simulate_closed_loop(
            "IPDT",
            {"K": 0.1, "theta": 5.0},
            PIDParams(0.8, 40.0, 0.0),
            PIDParams(0.8, 40.0, 0.0),
            400.0,
            0.5,
        )
        assert result["recommendedResponse"]["pv"][-1] == pytest.approx(1.0, abs=1e-3)


# ---------------------------------------------------------------------------
# 模型显式分派
# ---------------------------------------------------------------------------


class TestModelExplicitDispatch:
    """模型显式分派：FOPDT/SOPDT/IPDT 各自真实动力学，未知明确拒绝。"""

    def test_unknown_model_type_rejected_at_kernel(self):
        """未知模型类型 → SimulationNotSupportedError（禁止按 FOPDT 冒充）。"""
        with pytest.raises(SimulationNotSupportedError, match="不支持的模型类型"):
            simulate_closed_loop(
                "FOO",
                {"K": 1.0, "tau": 30.0, "theta": 5.0},
                PIDParams(0.5, 20.0, 0.0),
                PIDParams(2.0, 15.0, 0.0),
                100.0,
            )

    def test_ipdt_missing_gain_rejected(self):
        """IPDT 缺积分增益 K → 明确拒绝（禁止默认值冒充积分动力学）。"""
        with pytest.raises(SimulationNotSupportedError, match="积分增益 K"):
            simulate_closed_loop(
                "IPDT",
                {"theta": 5.0},
                PIDParams(0.5, 20.0, 0.0),
                PIDParams(2.0, 15.0, 0.0),
                100.0,
            )

    def test_dispatch_kind_recorded_in_context(self):
        """分派标识随结果携带（IPDT/FOPDT/SOPDT 两形各一）。"""
        cases = [
            ("IPDT", {"K": 0.1, "theta": 5.0}, "IPDT"),
            ("FOPDT", {"K": 1.0, "tau": 30.0, "theta": 5.0}, "FOPDT"),
            ("SOPDT", {"K": 1.0, "T1": 20.0, "T2": 5.0, "theta": 3.0}, "SOPDT_T1T2"),
            ("SOPDT", {"K": 1.0, "tau": 30.0, "xi": 1.0, "theta": 5.0}, "SOPDT_LEGACY"),
        ]
        pid = PIDParams(1.0, 20.0, 0.0)
        for model_type, params, expected_dispatch in cases:
            result = simulate_closed_loop(model_type, params, pid, pid, 100.0)
            ctx = result["simulationContext"]
            assert ctx["modelDispatch"] == expected_dispatch
            assert ctx["engineVersion"] == SIMULATION_ENGINE_VERSION

    def test_ipdt_response_not_fopdt_disguise(self):
        """IPDT 闭环响应不同于旧缺陷行为（按 tau=30 FOPDT 仿真）。"""
        pid = PIDParams(0.8, 40.0, 0.0)
        ipdt = simulate_closed_loop("IPDT", {"K": 0.1, "theta": 5.0}, pid, pid, 200.0, 1.0)
        # 旧缺陷等效对象：IPDT 参数被丢弃，按 FOPDT 默认 tau=30、theta=5 仿真
        fopdt_disguise = simulate_closed_loop(
            "FOPDT", {"K": 0.1, "tau": 30.0, "theta": 5.0}, pid, pid, 200.0, 1.0
        )
        pv_ipdt = ipdt["recommendedResponse"]["pv"]
        pv_disguise = fopdt_disguise["recommendedResponse"]["pv"]
        max_diff = max(abs(a - b) for a, b in zip(pv_ipdt, pv_disguise, strict=True))
        assert max_diff > 0.05, "IPDT 响应与 FOPDT 冒充行为无差异，分派可能未生效"


# ---------------------------------------------------------------------------
# C20-ID：扰动真实施加
# ---------------------------------------------------------------------------


class TestDisturbanceApplication:
    """扰动输入实际施加：有无扰动响应不同且符合独立参考；OP 随负载变化。"""

    @staticmethod
    def _simulate_fopdt_p_only(disturbance: bool):
        k, tau, kc, sp, d, t_d = 2.0, 30.0, 0.5, 1.0, 0.3, 12.0
        kwargs = (
            {
                "disturbance_type": "step",
                "disturbance_amplitude": d,
                "disturbance_time": t_d,
            }
            if disturbance
            else {"disturbance_type": "none"}
        )
        return (
            simulate_closed_loop(
                "FOPDT",
                {"K": k, "tau": tau, "theta": 0.0},
                PIDParams(kc, 0.0, 0.0),
                PIDParams(kc, 0.0, 0.0),
                60.0,
                0.25,
                **kwargs,
            ),
            (k, tau, kc, sp, d, t_d),
        )

    def test_disturbance_changes_response_and_matches_reference(self):
        """有无扰动响应不同；有扰动响应符合独立两段指数解析参考。"""
        result_no, params = self._simulate_fopdt_p_only(disturbance=False)
        result_yes, _ = self._simulate_fopdt_p_only(disturbance=True)
        k, tau, kc, sp, d, t_d = params

        pv_no = result_no["recommendedResponse"]["pv"]
        pv_yes = result_yes["recommendedResponse"]["pv"]
        ts = result_yes["timestamps"]

        # t_d 之前两条响应逐点一致（扰动尚未施加）
        n_before = int(t_d / 0.25)
        assert all(
            abs(a - b) < 1e-12 for a, b in zip(pv_no[:n_before], pv_yes[:n_before], strict=True)
        )
        # t_d 之后响应分离（扰动真实进入）
        assert max(abs(a - b) for a, b in zip(pv_no, pv_yes, strict=True)) > 0.1

        # 与独立解析参考一致（闭式两段指数；容差=2% 稳态幅值，覆盖 ZOH 离散误差）
        x_ss_span = (k * kc * sp + k * d) / (1.0 + k * kc)
        max_err = max(
            abs(
                pv_yes[i]
                - _fopdt_p_only_with_load_step(ts[i], k=k, tau=tau, kc=kc, sp=sp, d=d, t_d=t_d)
            )
            for i in range(len(ts))
        )
        assert max_err <= 0.02 * abs(x_ss_span), (
            f"扰动响应偏离解析参考 {max_err:.3e}（参考幅值 {x_ss_span:.3f}）"
        )

    def test_op_reacts_to_load_disturbance(self):
        """C20-ID：反馈 OP 随负载实际变化（扰动前后 OP 轨迹不同）。"""
        result_no, _ = self._simulate_fopdt_p_only(disturbance=False)
        result_yes, _ = self._simulate_fopdt_p_only(disturbance=True)
        op_no = result_no["recommendedResponse"]["op"]
        op_yes = result_yes["recommendedResponse"]["op"]
        # P-only 稳态：无负载 op→Kc·e；有负载 op 需额外抵消 d（u = Kc·e + 0 = -d? 解析：
        # 稳态对象输入须为 0（θ=0 FOPDT 非积分），op_ss = -d + Kc·e_ss）
        assert op_no[-1] != op_yes[-1]
        assert max(abs(a - b) for a, b in zip(op_no, op_yes, strict=True)) > 0.1

    def test_ipdt_p_only_with_load_disturbance_offset(self):
        """IPDT+P+负载阶跃稳态偏移符合解析值 x_ss = SP + d/Kc。"""
        k, kc, d = 0.2, 1.0, 0.4
        result = simulate_closed_loop(
            "IPDT",
            {"K": k, "theta": 0.0},
            PIDParams(kc, 0.0, 0.0),
            PIDParams(kc, 0.0, 0.0),
            120.0,
            0.25,
            disturbance_type="step",
            disturbance_amplitude=d,
            disturbance_time=20.0,
        )
        pv = result["recommendedResponse"]["pv"]
        expected_final = 1.0 + d / kc
        assert pv[-1] == pytest.approx(expected_final, rel=0.01)

    def test_unknown_disturbance_type_rejected(self):
        """未知扰动类型明确拒绝（旧行为=任意字符串静默忽略）。"""
        with pytest.raises(SimulationNotSupportedError, match="不支持的扰动类型"):
            simulate_closed_loop(
                "FOPDT",
                {"K": 1.0, "tau": 30.0, "theta": 5.0},
                PIDParams(0.5, 20.0, 0.0),
                PIDParams(2.0, 15.0, 0.0),
                100.0,
                disturbance_type="ramp",
            )

    def test_disturbance_time_outside_window_rejected(self):
        """扰动时刻超出仿真窗口 → 明确拒绝。"""
        with pytest.raises(ValueError, match="扰动施加时间"):
            simulate_closed_loop(
                "FOPDT",
                {"K": 1.0, "tau": 30.0, "theta": 5.0},
                PIDParams(0.5, 20.0, 0.0),
                PIDParams(2.0, 15.0, 0.0),
                100.0,
                disturbance_type="step",
                disturbance_time=120.0,
            )

    def test_default_none_equals_legacy_no_disturbance_curve(self):
        """默认 none 与旧行为（扰动被忽略）曲线一致——默认值无隐式行为变化。"""
        pid = PIDParams(1.5, 20.0, 1.0)
        params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        result_default = simulate_closed_loop("FOPDT", params, pid, pid, 120.0)
        result_none = simulate_closed_loop(
            "FOPDT", params, pid, pid, 120.0, disturbance_type="none"
        )
        assert (
            result_default["recommendedResponse"]["pv"] == result_none["recommendedResponse"]["pv"]
        )


# ---------------------------------------------------------------------------
# C20：工作量预算与候选去重
# ---------------------------------------------------------------------------


class TestWorkBudgetAndDedup:
    """超预算明确拒绝/复用异步档；候选重复去重；预算边界不阻塞普通 API。"""

    def test_sync_budget_steps_rejected_async_budget_accepted(self):
        """C20：同一超同步预算的请求，内核异步预算档可承接（超预算转异步）。"""
        params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        pid = PIDParams(0.5, 20.0, 0.0)
        kwargs = {
            "sim_duration": 7200.0,
            "sim_step": 0.1,  # 72000 步 > 同步上限 20000
        }
        with pytest.raises(SimulationBudgetError) as exc_info:
            simulate_closed_loop("FOPDT", params, pid, pid, budget=SIM_BUDGET_SYNC, **kwargs)
        assert exc_info.value.requested["steps"] == 72000
        assert exc_info.value.limits["maxSteps"] == SIM_BUDGET_SYNC.max_steps
        # 异步档（≤100000 步）同一请求通过
        result = simulate_closed_loop("FOPDT", params, pid, pid, budget=SIM_BUDGET_ASYNC, **kwargs)
        assert result["simulationContext"]["budget"]["totalIntegrationSteps"] == 72000 * 2

    def test_substep_budget_rejection(self):
        """子步预算：步长相对最快时间常数过小（子步需求超限）明确拒绝。"""
        params = {"K": 1.0, "T1": 0.01, "T2": 50.0, "theta": 1.0}  # 最快 tc=0.01s
        pid = PIDParams(0.5, 20.0, 0.0)
        with pytest.raises(SimulationBudgetError, match="子步"):
            simulate_closed_loop(
                "SOPDT", params, pid, pid, 600.0, 1.0, budget=SIM_BUDGET_SYNC
            )  # n_sub = ceil(4·1/0.01) = 400 > 256

    def test_candidate_count_budget_rejection(self):
        """候选数预算：17 组候选超同步上限 16 → 明确拒绝。"""
        params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        pid = PIDParams(0.5, 20.0, 0.0)
        candidates = [(f"C{i}", PIDParams(1.0 + i, 20.0, 0.0)) for i in range(17)]
        with pytest.raises(SimulationBudgetError, match="候选 PID 数"):
            simulate_closed_loop(
                "FOPDT",
                params,
                pid,
                pid,
                600.0,
                1.0,
                pid_candidates=candidates,
                budget=SIM_BUDGET_SYNC,
            )

    def test_normal_default_simulation_not_blocked(self):
        """预算边界不阻塞普通 API：默认形态 + 8 候选 + 显式扰动全部通过。"""
        params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        candidates = [(f"C{i}", PIDParams(1.0 + 0.5 * i, 20.0, 0.0)) for i in range(8)]
        result = simulate_closed_loop(
            "FOPDT",
            params,
            PIDParams(0.5, 20.0, 0.0),
            PIDParams(2.0, 15.0, 0.0),
            600.0,
            1.0,
            disturbance_type="step",
            pid_candidates=candidates,
        )
        assert len(result["candidateResponses"]) == 8
        assert all(not c["deduplicated"] for c in result["candidateResponses"])

    def test_candidate_dedup_marks_and_reuses_response(self):
        """C20：候选重复去重——<1% 相对差候选标记不重算，响应复用代表项。"""
        params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        base = PIDParams(2.0, 15.0, 1.0)
        candidates = [
            ("IMC", base),
            ("IMC-dup-0.25pct", PIDParams(2.005, 15.0, 1.0)),  # kp 相对差 0.25%
            ("LAMBDA", PIDParams(1.2, 18.0, 0.5)),
            ("LAMBDA-dup", PIDParams(1.2, 18.0, 0.5)),  # 完全相同
            ("ZN", PIDParams(3.0, 10.0, 2.0)),
            ("ZN-dup-0.49pct", PIDParams(3.0, 10.0, 2.009)),  # td 相对差 0.45%
            ("SIMC", PIDParams(2.5, 12.0, 0.0)),
            ("SIMC-distinct", PIDParams(2.5, 12.0, 0.02)),  # td 相对差 2% > 1% 不去重
        ]
        result = simulate_closed_loop(
            "FOPDT",
            params,
            PIDParams(0.5, 20.0, 0.0),
            PIDParams(2.0, 15.0, 0.0),
            200.0,
            1.0,
            pid_candidates=candidates,
        )
        entries = result["candidateResponses"]
        # 保留全部原始候选（顺序不变）
        assert [e["label"] for e in entries] == [label for label, _ in candidates]
        by_label = {e["label"]: e for e in entries}
        assert by_label["IMC-dup-0.25pct"]["deduplicated"] is True
        assert by_label["IMC-dup-0.25pct"]["duplicateOf"] == "IMC"
        assert by_label["LAMBDA-dup"]["duplicateOf"] == "LAMBDA"
        assert by_label["ZN-dup-0.49pct"]["duplicateOf"] == "ZN"
        assert by_label["SIMC-distinct"]["deduplicated"] is False
        # 重复项响应与代表项一致（复用，未重复仿真）
        assert by_label["LAMBDA-dup"]["response"]["pv"] == by_label["LAMBDA"]["response"]["pv"]
        # 上下文登记去重事实
        ctx = result["simulationContext"]["candidates"]
        assert ctx["requested"] == 8
        assert ctx["simulated"] == 5
        assert ctx["duplicates"] == {
            "IMC-dup-0.25pct": "IMC",
            "LAMBDA-dup": "LAMBDA",
            "ZN-dup-0.49pct": "ZN",
        }
        # 预算按去重后工作量计（2 主响应 + 5 唯一候选）
        assert result["simulationContext"]["budget"]["responses"] == 7

    def test_service_maps_budget_error_to_bizerror_with_guidance(self):
        """服务层映射为 BizError 并携带 requested/limits/异步建议。"""

        async def _run():
            from app.services.tuning import run_simulation

            return await run_simulation(
                model_type="FOPDT",
                model_params={"K": 1.0, "tau": 30.0, "theta": 5.0},
                current_pid={"kp": 0.5, "ti": 20.0, "td": 0.0},
                recommended_pid={"kp": 2.0, "ti": 15.0, "td": 0.0},
                sim_duration=7200.0,
                sim_step=0.1,
            )

        with pytest.raises(BizError) as exc_info:
            import asyncio

            asyncio.run(_run())
        assert exc_info.value.code == "ERR_TUNING_SIMULATION_BUDGET_EXCEEDED"
        assert exc_info.value.data["limits"]["maxSteps"] == SIM_BUDGET_SYNC.max_steps
        assert "异步" in exc_info.value.data["suggestion"]

    def test_service_maps_unsupported_model_to_bizerror(self):
        """服务层：未知模型类型 → ERR_TUNING_SIMULATION_MODEL_NOT_SUPPORTED。"""

        async def _run():
            from app.services.tuning import run_simulation

            return await run_simulation(
                model_type="MYSTERY",
                model_params={"K": 1.0},
                current_pid={"kp": 0.5, "ti": 20.0, "td": 0.0},
                recommended_pid={"kp": 2.0, "ti": 15.0, "td": 0.0},
            )

        with pytest.raises(BizError) as exc_info:
            import asyncio

            asyncio.run(_run())
        assert exc_info.value.code == "ERR_TUNING_SIMULATION_MODEL_NOT_SUPPORTED"


# ---------------------------------------------------------------------------
# C20-ID：超时/取消/失败明确 + 异步上下文 + 无仿真=未验证
# ---------------------------------------------------------------------------


def _make_mock_session(record: MagicMock | None = None) -> MagicMock:
    """构造 mock AsyncSession（与 test_tuning_celery_tasks 同款）。"""
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    captured = record or MagicMock()

    async def _get(_model, _record_id):
        return captured

    session.get = AsyncMock(side_effect=_get)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory, session, captured


class TestAsyncChainSemantics:
    """异步链：结果携带上下文；超时/取消/失败三态明确；无仿真=未验证。"""

    async def test_async_result_carries_simulation_context(self):
        """异步结果携带上下文（recordId/taskId/链标识；B2 冻结链兼容加性键）。"""
        from app.tasks.tuning import _do_tune_and_simulate

        factory, _session, record = _make_mock_session()
        sim_result = simulate_closed_loop(
            "FOPDT",
            {"K": 2.0, "tau": 30.0, "theta": 5.0},
            PIDParams(0.5, 20.0, 0.0),
            PIDParams(1.5, 15.0, 0.0),
            100.0,
            1.0,
            pid_candidates=[("IMC", PIDParams(1.5, 15.0, 0.0))],
        )
        with (
            patch("app.core.db.AsyncSessionLocal", factory),
            patch(
                "app.services.tuning.tune_pid",
                AsyncMock(
                    return_value={
                        "recommendedPid": {"kp": 1.5, "ti": 15.0, "td": 0.0},
                        "algorithm": "IMC",
                    }
                ),
            ),
            patch(
                "app.services.tuning._simulate_multi_pid",
                MagicMock(return_value=sim_result),
            ) as sim_mock,
            patch("app.services.tuning_progress.init_progress", AsyncMock()),
            patch("app.services.tuning_progress.update_progress", AsyncMock()),
        ):
            result = await _do_tune_and_simulate(
                task_id="task-p304-ctx-001",
                loop_id="loop-1",
                model_type="FOPDT",
                model_params={"K": 2.0, "tau": 30.0, "theta": 5.0},
                algorithms=["IMC"],
                current_pid={"kp": 0.5, "ti": 20.0, "td": 0.0},
                sim_duration=100.0,
                sim_step=1.0,
                setpoint_step=1.0,
                created_by="test_user",
                model_source="MANUAL",
                risk_confirmed=True,
            )

        # 异步链使用异步预算档（超预算复用异步任务的结构性前提）
        assert sim_mock.call_args.kwargs.get("budget") is SIM_BUDGET_ASYNC

        ctx = result["simulationResult"]["simulationContext"]
        assert ctx["recordId"] == result["recordId"]
        assert ctx["taskId"] == "task-p304-ctx-001"
        assert ctx["chain"] == "tune_and_simulate_task"
        assert ctx["engineVersion"] == SIMULATION_ENGINE_VERSION
        # 持久化侧同步携带（record.simulation_result 即 sim_result 引用）
        assert record.simulation_result["simulationContext"]["taskId"] == "task-p304-ctx-001"

    async def test_soft_timeout_distinct_from_generic_failure(self):
        """C20-ID：超时与普通失败明确区分（进度 error 码 + 记录原因）。"""
        from celery.exceptions import SoftTimeLimitExceeded

        from app.tasks.tuning import _do_tune_and_simulate

        factory, _session, record = _make_mock_session()
        progress_mock = AsyncMock()
        with (
            patch("app.core.db.AsyncSessionLocal", factory),
            patch(
                "app.services.tuning.tune_pid",
                AsyncMock(
                    return_value={
                        "recommendedPid": {"kp": 1.5, "ti": 15.0, "td": 0.0},
                        "algorithm": "IMC",
                    }
                ),
            ),
            patch(
                "app.services.tuning._simulate_multi_pid",
                side_effect=SoftTimeLimitExceeded("soft time limit (100s) exceeded"),
            ),
            patch("app.services.tuning_progress.init_progress", AsyncMock()),
            patch("app.services.tuning_progress.update_progress", progress_mock),
            pytest.raises(SoftTimeLimitExceeded),
        ):
            await _do_tune_and_simulate(
                task_id="task-p304-timeout-001",
                loop_id="loop-1",
                model_type="FOPDT",
                model_params={"K": 2.0, "tau": 30.0, "theta": 5.0},
                algorithms=["IMC"],
                current_pid=None,
                sim_duration=100.0,
                sim_step=1.0,
                setpoint_step=1.0,
                created_by="test_user",
                model_source="MANUAL",
                risk_confirmed=True,
            )

        assert record.status == "INCONCLUSIVE"
        assert "超时" in record.confidence_reason
        # 进度终态 FAILED 且 error 码明确（非泛化异常文本）
        final_call = progress_mock.call_args_list[-1]
        assert final_call.kwargs.get("status") == "FAILED"
        assert final_call.kwargs.get("error") == "ERR_SIMULATION_TIMEOUT"
        assert "超时" in final_call.kwargs.get("message", "")

    async def test_cancel_task_revokes_and_settles_running_records(self):
        """取消语义：revoke + 进度 CANCELLED + RUNNING 记录落定 INCONCLUSIVE。"""
        from app.services.tuning import cancel_tuning_task

        mock_async_result = MagicMock()
        mock_async_result.state = "RUNNING"
        session = MagicMock()
        session.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
        session.commit = AsyncMock()
        with (
            patch("celery.result.AsyncResult", return_value=mock_async_result),
            patch("app.services.tuning_progress.update_progress", AsyncMock()) as progress,
        ):
            data = await cancel_tuning_task("task-p304-cancel-001", db=session)

        assert data == {"taskId": "task-p304-cancel-001", "status": "CANCELLED"}
        mock_async_result.revoke.assert_called_once_with(terminate=True, signal="SIGTERM")
        progress.assert_awaited_once()
        assert progress.call_args.kwargs.get("status") == "CANCELLED"
        session.commit.assert_awaited()  # 记录落定已提交

    async def test_cancel_terminal_state_is_noop(self):
        """已终态任务不可取消：返回既有状态，不 revoke。"""
        from app.services.tuning import cancel_tuning_task

        mock_async_result = MagicMock()
        mock_async_result.state = "SUCCESS"
        with (
            patch("celery.result.AsyncResult", return_value=mock_async_result),
            patch("app.services.tuning_progress.update_progress", AsyncMock()) as progress,
        ):
            data = await cancel_tuning_task("task-p304-done-001", db=MagicMock())

        assert data["status"] == "SUCCESS"
        mock_async_result.revoke.assert_not_called()
        progress.assert_not_awaited()

    def test_record_without_simulation_shows_not_simulated(self):
        """无仿真即显示未验证：无 simulation_result 的记录 simulationStatus=NOT_SIMULATED。"""
        from app.models.tuning import TuningRecord
        from app.services.tuning import _record_to_dict

        record = MagicMock(spec=TuningRecord)
        record.simulation_result = None
        record.calc_context = {}
        data = _record_to_dict(record)
        assert data["simulationStatus"] == "NOT_SIMULATED"

        record.simulation_result = {"timestamps": [0.0, 1.0]}
        data = _record_to_dict(record)
        assert data["simulationStatus"] == "SIMULATED"

        # 空字典同样视为未验证（不冒充）
        record.simulation_result = {}
        data = _record_to_dict(record)
        assert data["simulationStatus"] == "NOT_SIMULATED"


# ---------------------------------------------------------------------------
# Schema 校验（P3-04 扰动参数与预算边界）
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """SimulateRequest/CompareRequest 扰动与预算边界的 schema 层原子拒绝。"""

    def test_disturbance_time_out_of_window_rejected(self):
        """disturbanceTime ≥ simDuration → 422（schema 层拒绝）。"""
        from pydantic import ValidationError

        from app.schemas.tuning import SimulateRequest

        with pytest.raises(ValidationError, match="disturbanceTime"):
            SimulateRequest(
                modelType="FOPDT",
                modelParams={"K": 1.0, "tau": 30.0, "theta": 5.0},
                currentPid={"kp": 0.5, "ti": 20.0, "td": 0.0},
                recommendedPid={"kp": 2.0, "ti": 15.0, "td": 0.0},
                disturbanceType="step",
                disturbanceTime=600.0,  # = simDuration 默认 600
            )

    def test_disturbance_type_literal_rejected(self):
        """disturbanceType 非 none/step → 422。"""
        from pydantic import ValidationError

        from app.schemas.tuning import SimulateRequest

        with pytest.raises(ValidationError):
            SimulateRequest(
                modelType="FOPDT",
                modelParams={"K": 1.0, "tau": 30.0, "theta": 5.0},
                currentPid={"kp": 0.5, "ti": 20.0, "td": 0.0},
                recommendedPid={"kp": 2.0, "ti": 15.0, "td": 0.0},
                disturbanceType="ramp",
            )

    def test_sim_duration_out_of_bounds_rejected(self):
        """simDuration > 7200 → 422。"""
        from pydantic import ValidationError

        from app.schemas.tuning import SimulateRequest

        with pytest.raises(ValidationError):
            SimulateRequest(
                modelType="FOPDT",
                modelParams={"K": 1.0, "tau": 30.0, "theta": 5.0},
                currentPid={"kp": 0.5, "ti": 20.0, "td": 0.0},
                recommendedPid={"kp": 2.0, "ti": 15.0, "td": 0.0},
                simDuration=8000.0,
            )

    def test_simulation_result_schema_accepts_context_and_dedup(self):
        """SimulationResult/CandidateResponse 接受 P3-04 加性字段。"""
        from app.schemas.tuning import CandidateResponse, SimulationMetrics, SimulationResult

        result = SimulationResult(
            timestamps=[0.0, 1.0],
            currentResponse={"pv": [0.0, 0.5], "op": [0.0, 1.0], "sp": [0.0, 1.0]},
            recommendedResponse={"pv": [0.0, 0.6], "op": [0.0, 1.2], "sp": [0.0, 1.0]},
            currentMetrics=SimulationMetrics(),
            recommendedMetrics=SimulationMetrics(),
            improvement={"riseTime": None},
            candidateResponses=[
                CandidateResponse(
                    label="IMC",
                    response={"pv": [0.0, 0.6]},
                    metrics=SimulationMetrics(),
                    deduplicated=False,
                ),
                CandidateResponse(
                    label="IMC-dup",
                    response={"pv": [0.0, 0.6]},
                    metrics=SimulationMetrics(),
                    deduplicated=True,
                    duplicateOf="IMC",
                ),
            ],
            simulationContext={"engineVersion": SIMULATION_ENGINE_VERSION},
        )
        assert result.simulationContext["engineVersion"] == SIMULATION_ENGINE_VERSION
        assert result.candidateResponses[1].duplicateOf == "IMC"

    def test_http_simulate_rejects_bad_disturbance(self, client) -> None:
        """HTTP 层：非法扰动参数 422（schema 原子拒绝）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/simulate",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "modelType": "FOPDT",
                    "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
                    "currentPid": {"kp": 0.5, "ti": 20.0, "td": 0.0},
                    "recommendedPid": {"kp": 2.0, "ti": 15.0, "td": 0.0},
                    "disturbanceType": "step",
                    "disturbanceTime": 999.0,
                },
            )
        assert resp.status_code == 422
