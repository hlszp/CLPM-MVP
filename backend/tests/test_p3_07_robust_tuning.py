"""P3-07 模型到鲁棒 PID 整定 oracle 测试（O-D1~O-D7 + C20-ID/C18-ID）.

事实来源：
- oracle 冻结草案 §5（R12/R13/R14/R15 冻结值一字不差）；
- 03 号文 §6 P3-07 卡（每种支持结构走通"候选模型→独立终验→整定→
  正确模型反馈仿真→DCS 转换→来源保存"；未支持结构保持拒绝、
  不降 D/E 推荐门禁）；
- 04 号文 §2 数值容差（数值内核 ≤1e-6 相对误差）。

oracle 独立性纪律（草案 §0.2）：
- 裕度解析锚点为**测试内独立手算公式**（纯 P 控积分+时滞过程：
  GM = π/(2·KcKθ)、PM = 90°−KcKθ·180/π），禁止用被测实现自产期望值；
- IPDT-PI/SOPDT-IMC 公式核对用测试内独立重写的公式；
- 超调交叉核验采用独立方法-of-steps 位置式 PI 仿真器（见
  TestOracleD3IpdtClosedLoop 的独立实现对照断言）；
- R15 冻结值（GM≥2.0/PM≥30°/超调≤20%/集合≤64）为冻结常量守卫，
  **不得失败后调整**（本文件断言其与冻结文本一致）。

登记（2026-10-12 λ 停点终裁已落地，DEC 清单 λ 终裁行）：
- 原 P3-07 停点（IPDT 冻结公式在 R14 注册 λ 范围 [0.1,5.0] 内无法满足
  R15 超调≤20%）经用户裁决=方案 a：**R14 λ 范围 [0.1,5.0]→[0.1,7.0] +
  IPDT 默认 λ=7.0**（仅 IPDT；SOPDT/FOPDT 不变）；R15 四项冻结指标
  一字不动。落地实测（宽 CI K±5%/θ±10%，64 集合）：λ=7.0 四项全过
  （超调 19.12%，余量 0.88pct）；λ=6.5 超调 19.62% 过；λ=6.0 超调
  20.23% 不过——验证器"如实标记"行为保持（本文件持续断言调小 λ
  仍见不通过标记，防验证器被顺手放宽）。
"""

from __future__ import annotations

import contextlib
import json
import math
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.core.exceptions import BizError
from app.services.tuning import (
    TuningModelAuthorization,
    authorize_tuning_model,
    create_tuning_task,
    tune_pid,
    verify_tuning_robustness,
)
from app.services.tuning_algorithms import (
    ROBUST_SET_MAX_MEMBERS,
    ROBUSTNESS_GAIN_MARGIN_MIN,
    ROBUSTNESS_OVERSHOOT_MAX_PCT,
    ROBUSTNESS_PHASE_MARGIN_MIN_DEG,
    TUNING_METHODS_INFO,
    PIDParams,
    build_trustworthy_set,
    closed_loop_gain_phase_margins,
    simulate_closed_loop,
    tune_imc,
    tune_ipdt_pi,
    tune_sopdt_imc,
    validate_tuning_params,
    verify_robustness_on_set,
)

# ---------------------------------------------------------------------------
# 公共夹具
# ---------------------------------------------------------------------------

_LOOP_ID = "00000000-0000-0000-0000-00000000p307"
_SOURCE_ID = "00000000-0000-0000-0000-000000000c3"
_SNAPSHOT_ID = "snap-p307-0001"

_IPDT_PARAMS = {"K": 0.1, "theta": 10.0}
_SOPDT_PARAMS = {"K": 2.0, "T1": 100.0, "T2": 30.0, "theta": 10.0}
_FOPDT_PARAMS = {"K": 1.2, "tau": 30.0, "theta": 5.0}

_WIDE_OP_LIMITS = (-100.0, 100.0)

_PID_REPR_KNOWN = {
    "status": "KNOWN",
    "modelId": "00000000-0000-0000-0000-000000000d4",
    "modelCode": "TEST-DCS-PB-MIN",
    "pType": "PROPORTION_BAND",
    "iUnit": "MINUTES",
    "dUnit": "MINUTES",
    "dFilterEnabled": False,
    "dcsTemplateRevision": "dcs_model:00000000-0000-0000-0000-000000000d4",
}

_FACTS_ACTUAL = {
    "opLimits": {
        "lower": -100.0,
        "upper": 100.0,
        "source": "LOOP_FIELD",
        "provenance": "ACTUAL",
        "unit": "%",
    },
    "pidRepresentation": dict(_PID_REPR_KNOWN),
}

_FACTS_DEFAULT_OP = {
    "opLimits": {
        "lower": 0.0,
        "upper": 100.0,
        "source": "DEFAULT_0_100",
        "provenance": "DEFAULT",
        "unit": None,
    },
    "pidRepresentation": dict(_PID_REPR_KNOWN),
}

_FACTS_UNKNOWN_DCS = {
    "opLimits": dict(_FACTS_ACTUAL["opLimits"]),
    "pidRepresentation": {
        "status": "UNKNOWN",
        "modelId": None,
        "reason": "回路未绑定 DCS 型号",
    },
}


def _auth_ctx(
    model_type: str,
    params: dict,
    *,
    final_verification: dict | None = None,
) -> TuningModelAuthorization:
    """直接构造门禁解析结果（生产链路由 authorize_tuning_model 构造）."""
    return TuningModelAuthorization(
        model_type=model_type,
        model_params=dict(params),
        loop_id=_LOOP_ID,
        model_source="IDENTIFICATION_RECORD",
        source_record_id=_SOURCE_ID,
        risk_confirmed=True,
        confidence_level="A",
        identify_method="HISTORICAL_ARX",
        data_source="HISTORY",
        model_version_id="pmv-p307-1",
        snapshot_id=_SNAPSHOT_ID,
        final_verification=final_verification,
    )


_FV_PASSED = {"status": "PASSED", "channels": {"finalTest": "PASSED"}}


def _source_record(
    *,
    model_type: str = "IPDT",
    model_params: dict | None = None,
    confidence_level: str | None = "A",
    snapshot_id: str | None = _SNAPSHOT_ID,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=_SOURCE_ID,
        loop_id=_LOOP_ID,
        status="IDENTIFIED",
        task_id="celery-p307",
        model_type=model_type,
        model_params=dict(model_params or _IPDT_PARAMS),
        process_model_version_id="pmv-p307-1",
        confidence_level=confidence_level,
        confidence_reason="拟合与残差检验通过",
        identify_method="HISTORICAL_ARX",
        data_source="HISTORY",
        fitting_score=93.5,
        excitation_score=0.72,
        residual_test_passed=True,
        dataset_snapshot_id=snapshot_id,
        time_window_start=datetime(2026, 10, 1, 0, 0, 0),
        time_window_end=datetime(2026, 10, 1, 2, 0, 0),
    )


def _write_manifest(tmp_path, *, final_verification=None, reselect=None, low_snr=None) -> None:
    """把辨识输入包 manifest 写入临时快照根（月目录结构同 calc_snapshot）."""
    month = tmp_path / "202610"
    month.mkdir(parents=True, exist_ok=True)
    manifest = {
        "resultEvidence": {
            "finalVerification": final_verification,
            "reselectUncertainty": reselect,
        },
        "preprocess": {"lowSnr": low_snr},
    }
    (month / f"{_SNAPSHOT_ID}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _make_db(*, source=None, loop=None) -> MagicMock:
    """verify_tuning_robustness/create_tuning_task 路径 db mock.

    get：来源辨识记录（authorize 与 manifest 复读共用）；
    execute：_get_loop 的 scalar_one_or_none（保存链）。
    """
    db = MagicMock()
    loop_result = MagicMock()
    loop_result.scalar_one_or_none = MagicMock(
        return_value=loop
        or SimpleNamespace(id=_LOOP_ID, tag_name="LIC-307", is_active=True, dcs_model_id=None)
    )
    db.execute = AsyncMock(return_value=loop_result)
    db.get = AsyncMock(return_value=source if source is not None else _source_record())
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@contextlib.contextmanager
def _patched_service_deps(facts: dict, params: dict):
    """服务层依赖打桩：工程事实 + 有效模型参数（版本表读取）."""
    with (
        patch(
            "app.services.engineering_facts.resolve_loop_engineering_facts",
            new=AsyncMock(return_value=facts),
        ),
        patch(
            "app.services.tuning.get_effective_model_params",
            new=AsyncMock(return_value=dict(params)),
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# R12/R13/R14 注册结构 + R15 冻结值守卫
# ---------------------------------------------------------------------------


class TestRegistrationAndFrozenThresholds:
    """R14（λ 终裁后）：IPDT [0.1,7.0] 默认 7.0；SOPDT/FOPDT 不变；R15 冻结值守卫."""

    def test_new_methods_registered_with_applicable_model(self) -> None:
        by_code = {m["code"]: m for m in TUNING_METHODS_INFO}
        assert by_code["IPDT_PI"]["applicableModel"] == "IPDT"
        assert by_code["SOPDT_IMC"]["applicableModel"] == "SOPDT"
        # 五法 FOPDT-only 保留（G21 守卫前提）
        for code in ("IMC", "LAMBDA", "ZN", "COHEN_COON", "SIMC"):
            assert by_code[code]["applicableModel"] == "FOPDT"

    def test_lambda_ratio_structure_reused(self) -> None:
        """λ 终裁（2026-10-12）：IPDT_PI [0.1,7.0] 默认 7.0；SOPDT_IMC 维持 IMC 同构.

        仅 IPDT 扩上限+调默认（SOPDT/FOPDT 不变——DEC 清单 λ 终裁行原文）。
        """
        by_code = {m["code"]: m for m in TUNING_METHODS_INFO}
        ref = {p["name"]: p for p in by_code["IMC"]["params"]}["lambdaRatio"]
        # IPDT_PI：λ 停点终裁落地（[0.1,7.0] 默认 7.0）
        ipdt = {p["name"]: p for p in by_code["IPDT_PI"]["params"]}["lambdaRatio"]
        assert (ipdt["min"], ipdt["max"], ipdt["default"]) == (0.1, 7.0, 7.0)
        assert "λ = lambdaRatio × θ" in ipdt["description"]
        # SOPDT_IMC：与 IMC 同结构不变（[0.1,5.0] 默认 1.0）
        sopdt = {p["name"]: p for p in by_code["SOPDT_IMC"]["params"]}["lambdaRatio"]
        assert sopdt["default"] == ref["default"] == 1.0
        assert sopdt["min"] == ref["min"] == 0.1
        assert sopdt["max"] == ref["max"] == 5.0
        # FOPDT 五法 lambdaRatio 亦不变（IMC/LAMBDA）
        assert (ref["min"], ref["max"], ref["default"]) == (0.1, 5.0, 1.0)

    def test_validate_tuning_params_accepts_and_rejects(self) -> None:
        # IPDT：新范围 [0.1,7.0]（7.0 闭区间端点接受；6.5 边界抽验接受）
        assert validate_tuning_params("IPDT_PI", {"lambdaRatio": 7.0}) == []
        assert validate_tuning_params("IPDT_PI", {"lambdaRatio": 6.5}) == []
        assert validate_tuning_params("IPDT_PI", {"lambdaRatio": 7.5}) != []
        assert validate_tuning_params("IPDT_PI", {"lambdaRatio": 0.05}) != []
        # SOPDT/FOPDT 维持 [0.1,5.0]
        assert validate_tuning_params("SOPDT_IMC", {"lambdaRatio": 0.1}) == []
        assert validate_tuning_params("SOPDT_IMC", {"lambdaRatio": 6.0}) != []
        assert validate_tuning_params("IPDT_PI", {"tauCRatio": 1.0}) != []

    def test_r15_frozen_thresholds_verbatim(self) -> None:
        """R15 冻结值一字不差（GM≥2.0/PM≥30°/超调≤20%/集合≤64）——不得调整."""
        assert ROBUSTNESS_GAIN_MARGIN_MIN == 2.0
        assert ROBUSTNESS_PHASE_MARGIN_MIN_DEG == 30.0
        assert ROBUSTNESS_OVERSHOOT_MAX_PCT == 20.0
        assert ROBUST_SET_MAX_MEMBERS == 64


# ---------------------------------------------------------------------------
# O-D1：裕度计算独立解析锚点（纯 P 控积分+时滞过程）
# ---------------------------------------------------------------------------


class TestOracleD1MarginAnalyticAnchors:
    """O-D1：GM=π/(2·KcKθ)、PM=90°−KcKθ·180/π（测试内独立手算公式核对）.

    L(jω)=KcK·e^{−jωθ}/(jω)：增益穿越 ωc=KcK → PM=90°−degrees(KcKθ)；
    相位穿越 ω180·θ=π/2 → GM=π/(2·KcKθ)。稳定性边界 KcKθ=π/2 处
    GM=1/PM=0（边界两侧一稳一发散的仿真行为断言归 P3-04 O-D1 链，
    此处核对本批新增裕度内核对独立锚点的数值一致性）。
    """

    @pytest.mark.parametrize(
        ("kc_k_theta", "exp_gm", "exp_pm_deg"),
        [
            (math.pi / 4, 2.0, 45.0),
            (math.pi / 6, 3.0, 60.0),
            (math.pi / 8, 4.0, 67.5),
            (math.pi / 2, 1.0, 0.0),  # 稳定性边界
        ],
    )
    def test_p_only_integrator_anchors(self, kc_k_theta, exp_gm, exp_pm_deg) -> None:
        K, theta = 0.1, 10.0
        kc = kc_k_theta / (K * theta)
        m = closed_loop_gain_phase_margins(
            "IPDT", {"K": K, "theta": theta}, PIDParams(kp=kc, ti=0.0, td=0.0)
        )
        assert m["gainMargin"] == pytest.approx(exp_gm, rel=1e-3)
        assert m["phaseMarginDeg"] == pytest.approx(exp_pm_deg, abs=0.05)

    def test_fopdt_margin_matches_manual_sweep(self) -> None:
        """FOPDT+PI 裕度对照独立频响公式（测试内直接实现 |L|/∠L）."""
        K, tau, theta = 1.2, 30.0, 5.0
        pid = tune_imc(K, tau, theta, lambda_ratio=1.5)
        w = np.logspace(-4, 2, 4000)
        c = pid.kp * (1 + 1 / (1j * w * pid.ti) + 1j * w * pid.td)
        g = K * np.exp(-1j * w * theta) / (1j * w * tau + 1)
        mag = np.abs(c * g)
        phase = np.degrees(np.unwrap(np.angle(c * g)))
        # 独立穿越点（线性插值）
        idx = np.where((mag[:-1] >= 1) & (mag[1:] < 1))[0]
        wc = np.interp(0.5, [mag[idx[0] + 1], mag[idx[0]]], [w[idx[0] + 1], w[idx[0]]])
        pm_ref = 180 + np.interp(np.log(wc), np.log(w), phase)
        m = closed_loop_gain_phase_margins("FOPDT", {"K": K, "tau": tau, "theta": theta}, pid)
        assert m["phaseMarginDeg"] == pytest.approx(float(pm_ref), abs=0.1)


# ---------------------------------------------------------------------------
# O-D2：噪声/负载真实进入反馈（C20-ID）
# ---------------------------------------------------------------------------


class TestOracleD2NoiseAndLoadIntoFeedback:
    """O-D2：测量噪声只进反馈通道、负载阶跃进对象输入，OP 实际变化."""

    @staticmethod
    def _run(noise_std=None, disturbance="none", **kw):
        pid = tune_ipdt_pi(0.1, 10.0, 2.0)
        return simulate_closed_loop(
            "IPDT",
            dict(_IPDT_PARAMS),
            pid,
            pid,
            sim_duration=400.0,
            sim_step=1.0,
            noise_std=noise_std,
            disturbance_type=disturbance,
            **kw,
        )

    def test_noise_makes_op_vary(self) -> None:
        clean = self._run()
        noisy = self._run(noise_std=0.05)
        op_clean = np.array(clean["recommendedResponse"]["op"][1:])
        op_noisy = np.array(noisy["recommendedResponse"]["op"][1:])
        # 有噪声时 OP 非恒定轨迹（稳态段仍有波动）；无噪声稳态段近恒定
        assert float(np.std(op_noisy[-100:])) > 1e-3
        assert float(np.std(op_clean[-100:])) < 1e-4
        # 有无扰动响应不同
        assert float(np.max(np.abs(op_noisy - op_clean))) > 0.01

    def test_load_step_shifts_op(self) -> None:
        clean = self._run()
        loaded = self._run(disturbance="step", disturbance_amplitude=0.5, disturbance_time=200.0)
        op_c = np.array(clean["recommendedResponse"]["op"][1:])
        op_l = np.array(loaded["recommendedResponse"]["op"][1:])
        before = slice(150, 199)
        after = slice(300, 399)
        # 负载进入前一致（未施加）；进入后 OP 偏移补偿负载（积分过程：OP 终值 −0.5）
        assert float(np.max(np.abs(op_l[before] - op_c[before]))) < 1e-6
        assert abs(float(np.mean(op_l[after] - op_c[after])) + 0.5) < 0.05
        assert float(np.max(np.abs(op_l[after] - op_c[after]))) > 0.1

    def test_noise_seed_deterministic_and_default_off(self) -> None:
        a = self._run(noise_std=0.05)
        b = self._run(noise_std=0.05)
        assert a["recommendedResponse"]["op"] == b["recommendedResponse"]["op"]
        ctx = self._run()["simulationContext"]
        assert ctx["measurementNoise"] is None
        ctx_n = self._run(noise_std=0.05)["simulationContext"]
        assert ctx_n["measurementNoise"] == {"type": "gaussian", "std": 0.05, "seed": 42}

    def test_invalid_noise_rejected(self) -> None:
        with pytest.raises(Exception, match="噪声标准差"):
            self._run(noise_std=-1.0)


# ---------------------------------------------------------------------------
# O-D3：IPDT-PI 公式（R12）+ 闭环指标
# ---------------------------------------------------------------------------


class TestOracleD3IpdtPiFormula:
    """O-D3 公式核对：测试内独立重写 R12 冻结公式，相对误差 ≤1e-6（04§2）."""

    @pytest.mark.parametrize(
        ("K", "theta", "lr"),
        [(0.1, 10.0, 2.0), (0.05, 20.0, 3.0), (0.2, 30.0, 1.0), (-0.1, 10.0, 2.0)],
    )
    def test_formula_against_independent_rewrite(self, K, theta, lr) -> None:
        lam = lr * theta
        ti_ref = 2.0 * lam + theta
        kc_ref = ti_ref / (K * (lam + theta) ** 2)
        pid = tune_ipdt_pi(K, theta, lambda_ratio=lr)
        assert pid.ti == pytest.approx(ti_ref, abs=1e-6)
        assert pid.kp == pytest.approx(kc_ref, rel=1e-6)
        assert pid.td == 0.0

    def test_negative_gain_control_direction_consistency(self) -> None:
        """负过程增益 → Kc 随 K 变号（负反馈结构保持，B1 物理解释链承接）."""
        p_pos = tune_ipdt_pi(0.1, 10.0, 2.0)
        p_neg = tune_ipdt_pi(-0.1, 10.0, 2.0)
        assert p_neg.kp == pytest.approx(-p_pos.kp, rel=1e-9)
        assert p_neg.ti == p_pos.ti

    def test_zero_or_invalid_gain_rejected(self) -> None:
        with pytest.raises(ValueError, match="非零积分增益"):
            tune_ipdt_pi(0.0, 10.0)
        with pytest.raises(ValueError, match="参数非法"):
            tune_ipdt_pi(0.1, float("nan"))


class TestOracleD3IpdtClosedLoopIndicators:
    """O-D3 闭环：冻结公式 λ 范围内四项指标的诚实判定（含停点登记事实）."""

    @staticmethod
    def _independent_positional_pi_sim(K, theta, Kc, Ti, sp=1.0, T=600.0, dt=0.1):
        """独立仿真器（非产品代码）：位置式理想 PI + 方法-of-steps 显式时滞."""
        n = int(T / dt)
        nd = int(round(theta / dt))
        hist = [0.0] * (nd + 1)
        x = 0.0
        iint = 0.0
        peak = 0.0
        for _ in range(n):
            e = sp - x
            op = Kc * e + (Kc / Ti) * iint
            hist.append(op)
            if len(hist) > nd + 1:
                hist.pop(0)
            u = hist[0]

            def f(s, uu=u):
                return (K * uu, sp - s[0])

            s = [x, iint]
            k1 = f(s)
            k2 = f([s[i] + 0.5 * dt * k1[i] for i in range(2)])
            k3 = f([s[i] + 0.5 * dt * k2[i] for i in range(2)])
            k4 = f([s[i] + dt * k3[i] for i in range(2)])
            x = s[0] + dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
            iint = s[1] + dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
            peak = max(peak, x)
        return peak

    def test_lambda_2_margin_indicators_pass_overshoot_marked_fail(self) -> None:
        """λ=2.0：GM/PM/OP 三项通过；超调 47%>20% 如实标记不通过（O-D5 语义）.

        独立交叉核验（oracle 纪律）：位置式 PI 独立仿真器的超调与产品
        增量式内核一致（差 <2pct）——证明 47% 是冻结公式的真实闭环行为，
        非实现伪影。
        """
        lr = 2.0
        pid = tune_ipdt_pi(0.1, 10.0, lr)
        r = verify_robustness_on_set("IPDT", [dict(_IPDT_PARAMS)], pid, op_limits=_WIDE_OP_LIMITS)
        ind = r["indicators"]
        assert ind["gainMargin"]["pass"] and ind["gainMargin"]["worst"] >= 2.0
        assert ind["phaseMargin"]["pass"] and ind["phaseMargin"]["worst"] >= 30.0
        assert ind["opLimits"]["pass"] and ind["opLimits"]["worstSaturationSteps"] == 0
        assert not ind["overshoot"]["pass"]
        assert ind["overshoot"]["worstPct"] > 40.0
        assert not r["overallPass"]
        # 独立仿真器交叉核验
        lam = lr * 10.0
        ti = 2 * lam + 10
        kc = ti / (0.1 * (lam + 10) ** 2)
        peak = self._independent_positional_pi_sim(0.1, 10.0, kc, ti, dt=0.1)
        assert (peak - 1.0) * 100 == pytest.approx(ind["overshoot"]["worstPct"], abs=2.0)

    def test_lambda_7_default_wide_ci_all_four_pass(self) -> None:
        """λ 终裁落地主断言：λ=7.0（新默认）宽 CI（K±5%/θ±10%）四项全过.

        实测锚点（64 集合最坏成员）：GM≈5.6/PM≈59.3°/超调≈19.12%（余量
        0.88pct）/OP 零饱和——与 DEC 清单 λ 终裁行引用的前任补测数据一致。
        """
        pid = tune_ipdt_pi(0.1, 10.0, 7.0)
        ci = {"K": [0.095, 0.105], "theta": [9.0, 11.0]}
        s = build_trustworthy_set("IPDT", dict(_IPDT_PARAMS), ci)
        assert s["memberCount"] == 64
        r = verify_robustness_on_set("IPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        ind = r["indicators"]
        assert ind["gainMargin"]["pass"] and ind["gainMargin"]["worst"] > 5.0
        assert ind["phaseMargin"]["pass"] and ind["phaseMargin"]["worst"] > 55.0
        assert ind["opLimits"]["pass"] and ind["opLimits"]["worstSaturationSteps"] == 0
        assert ind["overshoot"]["pass"]
        assert 18.5 < ind["overshoot"]["worstPct"] < 20.0
        assert r["overallPass"]

    @pytest.mark.parametrize(
        ("K", "theta"),
        [
            (0.1, 5.0),
            (0.05, 10.0),
            (0.2, 10.0),
            (-0.1, 10.0),  # 负增益：Kc 变号，闭环指标与正增益镜像一致
        ],
    )
    def test_lambda_7_wide_ci_variants_all_pass(self, K, theta) -> None:
        """λ=7.0 宽 CI θ/K 变体+负增益：四项全过（IPDT 规则尺度不变性）.

        R01（2026-10-13 增量纠偏）：θ=20 慢窗变体移至
        ``test_lambda_7_slow_theta20_inconclusive_evidence``——其窗末尾部
        未进入 ±2% 稳定带，超调证据不足不得总体 PASS（本组仅保留
        600s 窗内可完整回稳的快过程变体）。
        """
        pid = tune_ipdt_pi(K, theta, 7.0)
        ci = {
            "K": [K * 0.95, K * 1.05] if K > 0 else [K * 1.05, K * 0.95],
            "theta": [theta * 0.9, theta * 1.1],
        }
        s = build_trustworthy_set("IPDT", {"K": K, "theta": theta}, ci)
        assert s["memberCount"] == 64
        r = verify_robustness_on_set("IPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        assert r["overallPass"], r["indicators"]
        assert r["indicators"]["overshoot"]["worstPct"] < 20.0
        # R01：快过程窗内回稳 → 超调证据充分（非 INCONCLUSIVE）
        assert r["overallStatus"] == "PASS"
        assert r["indicators"]["overshoot"]["status"] == "PASS"

    def test_lambda_7_slow_theta20_inconclusive_evidence(self) -> None:
        """R01 语义变更回归：λ=7.0 θ=20（600s 窗内未回稳）→ INCONCLUSIVE.

        原 all_pass 参数化的 (0.1, 20.0) 变体：Ti=2λ+θ=300s，闭环回稳
        需要 ≈4~5×λ(140s)≈560~700s > 600s 窗——窗末尾部未进入 ±2%
        稳定带，窗内最大超调不能代表全时域峰值，按证据不足处理不得
        总体 PASS（17 号文 R01；GM/PM/OP 指标判定不受影响）。
        """
        pid = tune_ipdt_pi(0.1, 20.0, 7.0)
        ci = {"K": [0.095, 0.105], "theta": [18.0, 22.0]}
        s = build_trustworthy_set("IPDT", {"K": 0.1, "theta": 20.0}, ci)
        assert s["memberCount"] == 64
        r = verify_robustness_on_set("IPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        assert not r["overallPass"]
        assert r["overallStatus"] == "INCONCLUSIVE"
        assert r["indicators"]["overshoot"]["status"] == "INCONCLUSIVE"
        assert r["indicators"]["overshoot"]["worstPct"] < 20.0  # 窗内数值不变
        assert r["simulationEvidence"]["insufficientMembers"] == 64
        assert r["indicators"]["gainMargin"]["pass"]  # 其余三项判定不受证据判据影响
        assert r["indicators"]["phaseMargin"]["pass"]
        assert r["indicators"]["opLimits"]["pass"]

    def test_lambda_7_narrow_ci_recheck_pass(self) -> None:
        """窄 CI（K±2%/θ±5%）复核：λ=7.0 四项全过且裕度优于宽 CI."""
        pid = tune_ipdt_pi(0.1, 10.0, 7.0)
        ci = {"K": [0.098, 0.102], "theta": [9.5, 10.5]}
        s = build_trustworthy_set("IPDT", dict(_IPDT_PARAMS), ci)
        r = verify_robustness_on_set("IPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        assert r["overallPass"], r["indicators"]
        assert r["indicators"]["overshoot"]["worstPct"] < 20.0

    def test_lambda_65_boundary_spot_check_pass(self) -> None:
        """λ=6.5 边界抽验（宽 CI）：超调 19.62% 仍达标（7.0 与 6.0 之间档）."""
        pid = tune_ipdt_pi(0.1, 10.0, 6.5)
        ci = {"K": [0.095, 0.105], "theta": [9.0, 11.0]}
        s = build_trustworthy_set("IPDT", dict(_IPDT_PARAMS), ci)
        r = verify_robustness_on_set("IPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        ind = r["indicators"]
        assert ind["gainMargin"]["pass"] and ind["phaseMargin"]["pass"]
        assert ind["overshoot"]["pass"]
        assert 19.0 < ind["overshoot"]["worstPct"] < 20.0
        assert r["overallPass"]

    def test_lambda_5_overshoot_still_above_frozen_threshold(self) -> None:
        """诚实标注回归（λ 终裁后）：λ=5.0 超调 ≈20.8%——仍不满足 R15 ≤20%.

        GM≈4.9/PM≈56/OP 无饱和均过；唯超调差 ~0.8pct 如实标记不通过。
        验证器"如实标记"行为保持是 λ 终裁落地条件（调小 λ 仍见标注），
        本例锚定该行为不被顺手放宽。
        """
        pid = tune_ipdt_pi(0.1, 10.0, 5.0)
        r = verify_robustness_on_set("IPDT", [dict(_IPDT_PARAMS)], pid, op_limits=_WIDE_OP_LIMITS)
        ind = r["indicators"]
        assert ind["gainMargin"]["pass"]
        assert ind["phaseMargin"]["pass"]
        assert ind["opLimits"]["pass"]
        assert 20.0 < ind["overshoot"]["worstPct"] < 22.0
        assert not r["overallPass"]

    def test_lambda_1_default_margin_fail(self) -> None:
        """诚实标注回归：λ=1.0（激进档，非默认）PM≈21°<30°（GM 1.71<2.0）——如实标记.

        λ 终裁后默认为 7.0；用户显式调小 λ 时验证器仍如实标记不通过
        （不因默认达标而放宽任意 λ 档的判定）。
        """
        pid = tune_ipdt_pi(0.1, 10.0, 1.0)
        r = verify_robustness_on_set("IPDT", [dict(_IPDT_PARAMS)], pid, op_limits=_WIDE_OP_LIMITS)
        assert not r["indicators"]["phaseMargin"]["pass"]
        assert not r["indicators"]["gainMargin"]["pass"]
        assert not r["overallPass"]


# ---------------------------------------------------------------------------
# O-D4：SOPDT 专用 IMC-PID（R13 方向 A）
# ---------------------------------------------------------------------------


class TestOracleD4SopdtChain:
    """O-D4：公式独立核对 + FOPDT 极限 + 闭环指标全过（λ=2.0）."""

    @pytest.mark.parametrize(
        ("K", "t1", "t2", "theta", "lr"),
        [
            (1.0, 100.0, 40.0, 20.0, 1.0),  # 全精确锚点：Ti=150/Kp=5/Td=36
            (2.0, 100.0, 30.0, 10.0, 2.0),
            (-2.0, 100.0, 30.0, 10.0, 1.0),
        ],
    )
    def test_formula_against_independent_rewrite(self, K, t1, t2, theta, lr) -> None:
        lam = lr * theta
        ti_ref = t1 + t2 + theta / 2.0
        kc_ref = ti_ref / (K * (lam + theta / 2.0))
        td_ref = (t1 * t2 + theta * (t1 + t2) / 2.0) / ti_ref
        pid = tune_sopdt_imc(K, t1, t2, theta, lambda_ratio=lr)
        # 实现出口舍入：kp 6 位、ti/td 4 位——与独立公式舍入值精确相等
        assert pid.kp == round(kc_ref, 6)
        assert pid.ti == round(ti_ref, 4)
        assert pid.td == round(td_ref, 4)

    def test_exact_anchor_values(self) -> None:
        """独立手算锚点（K=1,T1=100,T2=40,θ=20,λ=20）：Kp=5, Ti=150, Td=36."""
        pid = tune_sopdt_imc(1.0, 100.0, 40.0, 20.0, lambda_ratio=1.0)
        assert (pid.kp, pid.ti, pid.td) == (5.0, 150.0, 36.0)

    def test_fopdt_limit_property(self) -> None:
        """独立推导佐证：T2→0 时退化为既有 FOPDT IMC（§6.3）逐项一致."""
        for K, tau, theta, lr in [(2.0, 100.0, 10.0, 1.0), (1.0, 50.0, 5.0, 2.0)]:
            fopdt = tune_imc(K, tau, theta, lambda_ratio=lr)
            limit = tune_sopdt_imc(K, tau, 1e-9, theta, lambda_ratio=lr)
            assert limit.kp == pytest.approx(fopdt.kp, rel=1e-3)
            assert limit.ti == pytest.approx(fopdt.ti, rel=1e-3)
            assert limit.td == pytest.approx(fopdt.td, rel=1e-3)

    def test_closed_loop_indicators_pass_lambda_2(self) -> None:
        """λ=2.0 名义模型：四项指标全过（GM/PM/超调/OP 限幅）."""
        pid = tune_sopdt_imc(2.0, 100.0, 30.0, 10.0, lambda_ratio=2.0)
        r = verify_robustness_on_set("SOPDT", [dict(_SOPDT_PARAMS)], pid, op_limits=_WIDE_OP_LIMITS)
        assert r["overallPass"], r["indicators"]
        assert r["indicators"]["gainMargin"]["worst"] > 2.0
        assert r["indicators"]["phaseMargin"]["worst"] > 30.0
        assert r["indicators"]["overshoot"]["worstPct"] < 20.0

    def test_lambda_1_default_gm_marginal_fail_marked(self) -> None:
        """R14 默认 1.0：GM≈1.998<2.0 边际不满足——如实标记（不调冻结值）."""
        pid = tune_sopdt_imc(2.0, 100.0, 30.0, 10.0, lambda_ratio=1.0)
        r = verify_robustness_on_set("SOPDT", [dict(_SOPDT_PARAMS)], pid, op_limits=_WIDE_OP_LIMITS)
        assert not r["indicators"]["gainMargin"]["pass"]
        assert 1.9 < r["indicators"]["gainMargin"]["worst"] < 2.0
        assert not r["overallPass"]

    def test_negative_gain_chain_passes(self) -> None:
        """负增益 SOPDT：Kc 变号、闭环指标与正增益镜像一致（方向一致性）."""
        pid = tune_sopdt_imc(-2.0, 100.0, 30.0, 10.0, lambda_ratio=2.0)
        assert pid.kp < 0
        r = verify_robustness_on_set(
            "SOPDT",
            [{"K": -2.0, "T1": 100.0, "T2": 30.0, "theta": 10.0}],
            pid,
            op_limits=_WIDE_OP_LIMITS,
        )
        assert r["overallPass"], r["indicators"]


# ---------------------------------------------------------------------------
# O-D5：可信模型集合 + 最坏情形判定
# ---------------------------------------------------------------------------


class TestOracleD5RobustSet:
    """O-D5：同一建议在全集合上的最坏情形；任一成员超限即不通过."""

    def test_set_build_deterministic_and_capped(self) -> None:
        ci = {"K": [0.095, 0.105], "theta": [9.0, 11.0]}
        a = build_trustworthy_set("IPDT", dict(_IPDT_PARAMS), ci)
        b = build_trustworthy_set("IPDT", dict(_IPDT_PARAMS), ci)
        assert a["members"] == b["members"]
        assert a["memberCount"] == ROBUST_SET_MAX_MEMBERS
        assert set(a["freeKeys"]) == {"K", "theta"}
        # 角点包含（最坏情形承载）
        lo_k, hi_k = 0.095, 0.105
        k_values = {round(m["K"], 6) for m in a["members"]}
        assert lo_k in k_values and hi_k in k_values

    def test_set_worst_member_degrades_indicators(self) -> None:
        """集合最坏成员劣于名义（θ 上界成员 PM 更低）——集合验证非单点."""
        pid = tune_ipdt_pi(0.1, 10.0, 2.0)
        nominal = verify_robustness_on_set(
            "IPDT", [dict(_IPDT_PARAMS)], pid, op_limits=_WIDE_OP_LIMITS
        )
        ci = {"K": [0.095, 0.105], "theta": [9.0, 11.0]}
        s = build_trustworthy_set("IPDT", dict(_IPDT_PARAMS), ci)
        worst = verify_robustness_on_set("IPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        assert (
            worst["indicators"]["phaseMargin"]["worst"]
            < nominal["indicators"]["phaseMargin"]["worst"]
        )

    def test_sopdt_set64_all_pass(self) -> None:
        pid = tune_sopdt_imc(2.0, 100.0, 30.0, 10.0, lambda_ratio=2.0)
        s = build_trustworthy_set(
            "SOPDT",
            dict(_SOPDT_PARAMS),
            {"K": [1.9, 2.1], "T1": [95.0, 105.0], "T2": [27.0, 33.0], "theta": [9.0, 11.0]},
        )
        assert s["memberCount"] == 64
        r = verify_robustness_on_set("SOPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        assert r["overallPass"], r["indicators"]
        assert r["frozenThresholds"]["maxSetMembers"] == 64

    def test_narrow_op_limits_saturation_marks_fail(self) -> None:
        """OP 限幅非空虚化：窄限位下触发限幅 → opLimits 指标不通过."""
        pid = tune_sopdt_imc(2.0, 100.0, 30.0, 10.0, lambda_ratio=2.0)
        r = verify_robustness_on_set(
            "SOPDT", [dict(_SOPDT_PARAMS)], pid, op_limits=(0.0, 30.0), setpoint_step=50.0
        )
        assert r["indicators"]["opLimits"]["worstSaturationSteps"] > 0
        assert not r["indicators"]["opLimits"]["pass"]
        assert not r["overallPass"]

    def test_degenerate_ci_fixed_keys_no_sign_flip(self) -> None:
        """CI 跨越符号/非法 → 该维度固定（fixedKeys），负增益方向不翻转."""
        s = build_trustworthy_set(
            "IPDT", {"K": -0.1, "theta": 10.0}, {"K": [-0.2, 0.2], "theta": [9.0, 11.0]}
        )
        assert s["fixedKeys"] == ["K"]
        assert all(m["K"] == -0.1 for m in s["members"])

    def test_no_ci_nominal_single_member(self) -> None:
        s = build_trustworthy_set("IPDT", dict(_IPDT_PARAMS), None)
        assert s["memberCount"] == 1
        assert s["members"] == [_IPDT_PARAMS]
        assert s["fixedKeys"] == ["K", "theta"]

    def test_member_cap_rejected_beyond_frozen_limit(self) -> None:
        from app.services.tuning_algorithms import SimulationBudgetError

        pid = tune_ipdt_pi(0.1, 10.0, 2.0)
        with pytest.raises(SimulationBudgetError, match="成员数"):
            verify_robustness_on_set(
                "IPDT", [dict(_IPDT_PARAMS)] * 65, pid, op_limits=_WIDE_OP_LIMITS
            )


# ---------------------------------------------------------------------------
# O-D6：未支持结构/未终验/未知表示明确拒绝；D/E 门禁不降
# ---------------------------------------------------------------------------


class TestOracleD6Rejections:
    """O-D6：明确拒绝 + 原因；不输出参数、不降 D/E 推荐门禁."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("algorithm", ["IMC", "LAMBDA", "ZN", "COHEN_COON", "SIMC"])
    async def test_legacy_algorithms_reject_ipdt(self, algorithm: str) -> None:
        """五法 FOPDT-only 拒绝分支保留（G21 守卫；SOPDT 同款由既有测试覆盖）."""
        ctx = _auth_ctx("IPDT", _IPDT_PARAMS, final_verification=_FV_PASSED)
        with pytest.raises(BizError) as exc:
            await tune_pid("IPDT", dict(_IPDT_PARAMS), algorithm, source_context=ctx)
        assert exc.value.code == "ERR_MODEL_TYPE_NOT_TUNABLE"
        assert "IPDT_PI" in exc.value.message

    @pytest.mark.asyncio
    async def test_unknown_model_type_rejected(self) -> None:
        ctx = _auth_ctx("FOO", {"K": 1.0})
        with pytest.raises(BizError) as exc:
            await tune_pid("FOO", {"K": 1.0}, "IMC", source_context=ctx)
        assert exc.value.code == "ERR_MODEL_TYPE_NOT_TUNABLE"

    @pytest.mark.asyncio
    async def test_ipdt_without_final_verification_rejected(self) -> None:
        ctx = _auth_ctx("IPDT", _IPDT_PARAMS, final_verification=None)
        with pytest.raises(BizError) as exc:
            await tune_pid("IPDT", dict(_IPDT_PARAMS), "IPDT_PI", source_context=ctx)
        assert exc.value.code == "ERR_TUNING_FINAL_VERIFICATION_REQUIRED"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["FAILED", "NO_DYNAMICS", "NOT_RUN"])
    async def test_ipdt_final_verification_not_passed_rejected(self, status: str) -> None:
        ctx = _auth_ctx("IPDT", _IPDT_PARAMS, final_verification={"status": status})
        with pytest.raises(BizError) as exc:
            await tune_pid("IPDT", dict(_IPDT_PARAMS), "IPDT_PI", source_context=ctx)
        assert exc.value.code == "ERR_TUNING_FINAL_VERIFICATION_NOT_PASSED"

    @pytest.mark.asyncio
    async def test_sopdt_missing_params_rejected(self) -> None:
        ctx = _auth_ctx(
            "SOPDT", {"K": 2.0, "T1": 100.0, "theta": 10.0}, final_verification=_FV_PASSED
        )
        with pytest.raises(BizError) as exc:
            await tune_pid("SOPDT", dict(ctx.model_params), "SOPDT_IMC", source_context=ctx)
        assert exc.value.code == "ERR_MODEL_PARAMS_MISSING"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("confidence", ["D", "E", "INCONCLUSIVE"])
    async def test_d_e_confidence_gate_not_lowered(self, confidence: str) -> None:
        """D/E/INCONCLUSIVE 可信度仍被推荐链门禁拒绝（不降门禁）."""
        db = _make_db(source=_source_record(confidence_level=confidence))
        with (
            patch(
                "app.services.tuning.get_effective_model_params",
                new=AsyncMock(return_value=dict(_IPDT_PARAMS)),
            ),
        ):
            with pytest.raises(BizError) as exc:
                await authorize_tuning_model(
                    db=db,
                    requested_model_type="IPDT",
                    requested_model_params=dict(_IPDT_PARAMS),
                    loop_id=_LOOP_ID,
                    source_record_id=_SOURCE_ID,
                    model_source="IDENTIFICATION_RECORD",
                    risk_confirmed=True,
                )
            assert exc.value.code == "ERR_TUNING_CONFIDENCE_BLOCKED"


# ---------------------------------------------------------------------------
# O-D7：单位全链（服务级）+ DCS 往返 + 来源保存
# ---------------------------------------------------------------------------


class TestOracleD7DcsRoundTrip:
    """O-D7 单位基础：标准⇄DCS 私有表示往返（数值内核 1e-6）+ 三结构建议."""

    @pytest.mark.parametrize(
        ("model_type", "params", "algorithm", "algorithm_params"),
        [
            ("FOPDT", _FOPDT_PARAMS, "IMC", {"lambdaRatio": 1.5}),
            ("IPDT", _IPDT_PARAMS, "IPDT_PI", {"lambdaRatio": 2.0}),
            ("SOPDT", _SOPDT_PARAMS, "SOPDT_IMC", {"lambdaRatio": 2.0}),
        ],
    )
    def test_round_trip_all_structures(
        self, model_type, params, algorithm, algorithm_params
    ) -> None:
        from app.services.pid_conversion import (
            StandardPid,
            dcs_pid_from_standard_facts,
            standard_pid_from_facts,
        )

        if model_type == "FOPDT":
            std = StandardPid(**vars(tune_imc(params["K"], params["tau"], params["theta"], 1.5)))
        elif model_type == "IPDT":
            std = StandardPid(**vars(tune_ipdt_pi(params["K"], params["theta"], 2.0)))
        else:
            std = StandardPid(
                **vars(
                    tune_sopdt_imc(params["K"], params["T1"], params["T2"], params["theta"], 2.0)
                )
            )
        dcs = dcs_pid_from_standard_facts(std, _PID_REPR_KNOWN)
        # PB=100/Kp；分钟表示 i=Ti/60、d=Td/60
        assert dcs.p == pytest.approx(100.0 / std.kp, rel=1e-9)
        assert dcs.i == pytest.approx(std.ti / 60.0, rel=1e-9)
        assert dcs.d == pytest.approx(std.td / 60.0, rel=1e-9)
        back = standard_pid_from_facts(dcs, _PID_REPR_KNOWN)
        assert back.kp == pytest.approx(std.kp, rel=1e-9)
        assert back.ti == pytest.approx(std.ti, abs=1e-8)
        assert back.td == pytest.approx(std.td, abs=1e-8)

    def test_unknown_representation_rejected(self) -> None:
        from app.services.pid_conversion import (
            PidRepresentationUnknownError,
            StandardPid,
            dcs_pid_from_standard_facts,
        )

        with pytest.raises(PidRepresentationUnknownError):
            dcs_pid_from_standard_facts(
                StandardPid(1.0, 10.0), _FACTS_UNKNOWN_DCS["pidRepresentation"]
            )


class TestOracleD7ServiceFullChain:
    """O-D7 服务级全链：终验→整定→集合仿真→DCS 转换→来源（IPDT/SOPDT）."""

    @pytest.mark.asyncio
    async def test_ipdt_full_chain_honest_report(self, tmp_path) -> None:
        """IPDT 全链：报告含集合/DCS/扰动/低 SNR 知悉；超调如实标记不通过."""
        _write_manifest(
            tmp_path,
            final_verification=_FV_PASSED,
            reselect={
                "method": "RESELECT_DELAY_STRUCTURE",
                "status": "COMPLETED",
                "K": {"ci95": [0.096, 0.104]},
                "theta": {"ci95": [9.5, 10.5]},
            },
            low_snr={
                "mode": "PARAM_ACCURACY_UNCOMMITTED",
                "snrDb": 14.2,
                "thresholdDb": 20.0,
            },
        )
        db = _make_db()
        with (
            patch("app.services.calc_snapshot.snapshot_root", new=lambda: tmp_path),
            _patched_service_deps(_FACTS_ACTUAL, _IPDT_PARAMS),
        ):
            report = await verify_tuning_robustness(
                db,
                loop_id=_LOOP_ID,
                model_type="IPDT",
                model_params=dict(_IPDT_PARAMS),
                algorithm="IPDT_PI",
                algorithm_params={"lambdaRatio": 2.0},
                source_record_id=_SOURCE_ID,
                model_source="IDENTIFICATION_RECORD",
                risk_confirmed=True,
            )
        # 集合（P-32：重选不确定性来源，≤64）
        assert report["setBasis"]["source"] == "RESELECT_UNCERTAINTY"
        assert report["setBasis"]["memberCount"] == 64
        # 指标（诚实：GM/PM/OP 过、超调不通过——停点登记事实）
        assert report["indicators"]["gainMargin"]["pass"]
        assert report["indicators"]["phaseMargin"]["pass"]
        assert report["indicators"]["opLimits"]["pass"]
        assert not report["indicators"]["overshoot"]["pass"]
        assert not report["overallPass"]
        # DCS 转换（PB/分钟）
        dcs = report["dcsRepresentation"]
        assert dcs["status"] == "KNOWN"
        rec = report["verifiedPid"]
        assert dcs["dcsPid"]["p"] == pytest.approx(100.0 / rec["kp"], rel=1e-6)
        assert dcs["dcsPid"]["i"] == pytest.approx(rec["ti"] / 60.0, rel=1e-6)
        # 扰动响应证据（C20-ID：OP 随噪声/负载实际变化）
        dr = report["disturbanceResponse"]
        assert dr["opVariesWithNoiseOrLoad"] is True
        assert dr["opStdWithNoise"] > 1e-3
        # S1-b 知悉传递（不承诺标注如实带入建议层）
        assert report["lowSnrDisclaimer"]["mode"] == "PARAM_ACCURACY_UNCOMMITTED"
        assert "不承诺参数级精度" in report["lowSnrDisclaimer"]["disclaimer"]
        # 来源链（P-35）
        assert report["sources"]["modelVersionId"] == "pmv-p307-1"
        assert report["sources"]["datasetSnapshotId"] == _SNAPSHOT_ID
        assert report["finalVerification"]["status"] == "PASSED"
        # OP 限幅来源（P-34：ACTUAL 才放行）
        assert report["opLimits"]["provenance"] == "ACTUAL"

    @pytest.mark.asyncio
    async def test_sopdt_full_chain_overall_pass(self, tmp_path) -> None:
        """SOPDT 全链：λ=2.0 建议在 64 成员集合上四项指标全过."""
        _write_manifest(
            tmp_path,
            final_verification=_FV_PASSED,
            reselect={
                "method": "RESELECT_DELAY_STRUCTURE",
                "status": "COMPLETED",
                "K": {"ci95": [1.9, 2.1]},
                "T1": {"ci95": [95.0, 105.0]},
                "T2": {"ci95": [27.0, 33.0]},
                "theta": {"ci95": [9.0, 11.0]},
            },
        )
        db = _make_db(source=_source_record(model_type="SOPDT", model_params=_SOPDT_PARAMS))
        with (
            patch("app.services.calc_snapshot.snapshot_root", new=lambda: tmp_path),
            _patched_service_deps(_FACTS_ACTUAL, _SOPDT_PARAMS),
        ):
            report = await verify_tuning_robustness(
                db,
                loop_id=_LOOP_ID,
                model_type="SOPDT",
                model_params=dict(_SOPDT_PARAMS),
                algorithm="SOPDT_IMC",
                algorithm_params={"lambdaRatio": 2.0},
                source_record_id=_SOURCE_ID,
                model_source="IDENTIFICATION_RECORD",
                risk_confirmed=True,
            )
        assert report["overallPass"] is True, report["indicators"]
        assert report["setBasis"]["memberCount"] == 64
        assert report["verifiedPidSource"] == "SERVER_TUNED"

    @pytest.mark.asyncio
    async def test_fopdt_full_chain_via_legacy_method(self, tmp_path) -> None:
        """FOPDT 全链（五法同样可走验证链；λ=1.5 全过）."""
        _write_manifest(tmp_path, final_verification=_FV_PASSED)
        db = _make_db(source=_source_record(model_type="FOPDT", model_params=_FOPDT_PARAMS))
        with (
            patch("app.services.calc_snapshot.snapshot_root", new=lambda: tmp_path),
            _patched_service_deps(_FACTS_ACTUAL, _FOPDT_PARAMS),
        ):
            report = await verify_tuning_robustness(
                db,
                loop_id=_LOOP_ID,
                model_type="FOPDT",
                model_params=dict(_FOPDT_PARAMS),
                algorithm="IMC",
                algorithm_params={"lambdaRatio": 1.5},
                source_record_id=_SOURCE_ID,
                model_source="IDENTIFICATION_RECORD",
                risk_confirmed=True,
            )
        assert report["overallPass"] is True, report["indicators"]
        # 无不确定性证据 → 名义单点集合（显式 setBasis，不伪补宽度）
        assert report["setBasis"]["source"] == "NOMINAL_ONLY"
        assert report["setBasis"]["memberCount"] == 1

    @pytest.mark.asyncio
    async def test_default_op_limits_rejected(self, tmp_path) -> None:
        """P-34：默认 0~100 假设不得冒充实际限位 → 明确拒绝."""
        _write_manifest(tmp_path, final_verification=_FV_PASSED)
        db = _make_db()
        with (
            patch("app.services.calc_snapshot.snapshot_root", new=lambda: tmp_path),
            _patched_service_deps(_FACTS_DEFAULT_OP, _IPDT_PARAMS),
        ):
            with pytest.raises(BizError) as exc:
                await verify_tuning_robustness(
                    db,
                    loop_id=_LOOP_ID,
                    model_type="IPDT",
                    model_params=dict(_IPDT_PARAMS),
                    algorithm="IPDT_PI",
                    source_record_id=_SOURCE_ID,
                    model_source="IDENTIFICATION_RECORD",
                    risk_confirmed=True,
                )
            assert exc.value.code == "ERR_TUNING_OP_LIMITS_UNVERIFIED"

    @pytest.mark.asyncio
    async def test_unknown_dcs_representation_rejected(self, tmp_path) -> None:
        """O-D6：未知 DCS 结构 → 链路 DCS 转换步骤明确拒绝."""
        _write_manifest(tmp_path, final_verification=_FV_PASSED)
        db = _make_db()
        with (
            patch("app.services.calc_snapshot.snapshot_root", new=lambda: tmp_path),
            _patched_service_deps(_FACTS_UNKNOWN_DCS, _IPDT_PARAMS),
        ):
            with pytest.raises(BizError) as exc:
                await verify_tuning_robustness(
                    db,
                    loop_id=_LOOP_ID,
                    model_type="IPDT",
                    model_params=dict(_IPDT_PARAMS),
                    algorithm="IPDT_PI",
                    source_record_id=_SOURCE_ID,
                    model_source="IDENTIFICATION_RECORD",
                    risk_confirmed=True,
                )
            assert exc.value.code == "ERR_TUNING_DCS_REPRESENTATION_UNKNOWN"

    @pytest.mark.asyncio
    async def test_missing_final_verification_rejected(self, tmp_path) -> None:
        """终验结论缺失（旧记录无终验装配）→ 明确拒绝."""
        _write_manifest(tmp_path, final_verification=None)
        db = _make_db()
        with (
            patch("app.services.calc_snapshot.snapshot_root", new=lambda: tmp_path),
            _patched_service_deps(_FACTS_ACTUAL, _IPDT_PARAMS),
        ):
            with pytest.raises(BizError) as exc:
                await verify_tuning_robustness(
                    db,
                    loop_id=_LOOP_ID,
                    model_type="IPDT",
                    model_params=dict(_IPDT_PARAMS),
                    algorithm="IPDT_PI",
                    source_record_id=_SOURCE_ID,
                    model_source="IDENTIFICATION_RECORD",
                    risk_confirmed=True,
                )
            assert exc.value.code == "ERR_TUNING_FINAL_VERIFICATION_REQUIRED"


# ---------------------------------------------------------------------------
# P-35：建议来源绑定 + 保存链（B2 冻结链兼容）
# ---------------------------------------------------------------------------


class TestSourceBindingAndSave:
    """recommendationSource 随建议携带；保存链绑定来源身份（calc_context 兼容）."""

    @pytest.mark.asyncio
    async def test_tune_result_carries_recommendation_source(self) -> None:
        ctx = _auth_ctx("IPDT", _IPDT_PARAMS, final_verification=_FV_PASSED)
        result = await tune_pid(
            "IPDT", dict(_IPDT_PARAMS), "IPDT_PI", {"lambdaRatio": 2.0}, source_context=ctx
        )
        src = result["recommendationSource"]
        assert src["modelSource"] == "IDENTIFICATION_RECORD"
        assert src["sourceRecordId"] == _SOURCE_ID
        assert src["modelVersionId"] == "pmv-p307-1"
        assert src["datasetSnapshotId"] == _SNAPSHOT_ID
        assert src["finalVerification"] == "PASSED"
        assert src["algorithm"] == "IPDT_PI"
        assert src["algorithmParams"] == {"lambdaRatio": 2.0}
        assert src["algorithmVersion"] == "TUNE_ENGINE_v1.0"
        assert src["simulationEngineVersion"] == "SIM_ENGINE_v2.0"

    @pytest.mark.asyncio
    async def test_ipdt_default_lambda_is_7(self) -> None:
        """λ 终裁落地：algorithmParams 缺省时 IPDT 默认 λ=7.0（服务级 fallback）.

        与注册表 default、tune_ipdt_pi 签名默认三处单源一致。
        """
        ctx = _auth_ctx("IPDT", _IPDT_PARAMS, final_verification=_FV_PASSED)
        result = await tune_pid("IPDT", dict(_IPDT_PARAMS), "IPDT_PI", None, source_context=ctx)
        expected = tune_ipdt_pi(0.1, 10.0, 7.0)
        assert result["recommendedPid"] == {
            "kp": expected.kp,
            "ti": expected.ti,
            "td": expected.td,
        }
        assert "λ = 7.0 × θ" in result["notes"]

    @pytest.mark.asyncio
    async def test_save_ipdt_scheme_binds_source(self) -> None:
        """IPDT 方案保存：来源/模型版本冻结；仿真证据来自真实内核仿真."""
        pid = tune_ipdt_pi(0.1, 10.0, 2.0)
        pid_dict = {"kp": pid.kp, "ti": pid.ti, "td": pid.td}
        sim = simulate_closed_loop(
            "IPDT",
            dict(_IPDT_PARAMS),
            pid,
            pid,
            sim_duration=300.0,
            sim_step=1.0,
            pid_candidates=[("IPDT-PI λ=2.0", pid)],
            op_limits=_WIDE_OP_LIMITS,
        )
        db = _make_db(source=_source_record())
        with (
            patch(
                "app.services.engineering_facts.resolve_loop_engineering_facts",
                new=AsyncMock(return_value=_FACTS_ACTUAL),
            ),
            patch(
                "app.services.tuning._current_pid_snapshot",
                new=AsyncMock(return_value={"standard": None}),
            ),
            patch(
                "app.services.tuning.get_effective_model_params",
                new=AsyncMock(return_value=dict(_IPDT_PARAMS)),
            ),
        ):
            saved = await create_tuning_task(
                db,
                loop_id=_LOOP_ID,
                model_type="IPDT",
                model_params=dict(_IPDT_PARAMS),
                algorithm="IPDT_PI",
                recommended_pid=pid_dict,
                model_source="IDENTIFICATION_RECORD",
                source_record_id=_SOURCE_ID,
                risk_confirmed=True,
                simulation_result=sim,
                status="SIMULATED",
                created_by="tester",
            )
        record = db.add.call_args[0][0]
        assert record.algorithm == "IPDT_PI"
        assert record.model_type == "IPDT"
        assert record.source_record_id == _SOURCE_ID
        assert record.process_model_version_id == "pmv-p307-1"
        assert record.calc_context["modelVersionId"] == "pmv-p307-1"
        assert record.calc_context["frozenModel"]["modelType"] == "IPDT"
        # 仿真证据含结构正确分派（IPDT 真实积分动力学）与 OP 实际限位
        sim_ctx = record.simulation_result["simulationContext"]
        assert sim_ctx["modelDispatch"] == "IPDT"
        assert sim_ctx["opLimits"] == {"lower": -100.0, "upper": 100.0}
        assert saved["simulationStatus"] == "SIMULATED"

    def test_simulation_context_additive_keys(self) -> None:
        """P3-07 加性上下文：measurementNoise/opLimits/saturationSteps 就位."""
        pid = tune_ipdt_pi(0.1, 10.0, 2.0)
        sim = simulate_closed_loop(
            "IPDT", dict(_IPDT_PARAMS), pid, pid, sim_duration=100.0, op_limits=(0.0, 100.0)
        )
        ctx = sim["simulationContext"]
        assert ctx["measurementNoise"] is None
        assert ctx["opLimits"] == {"lower": 0.0, "upper": 100.0}
        assert set(ctx["saturationSteps"]) == {"current", "recommended"}
        # 响应字典仍为纯列表值（API schema dict[str, list[float]] 契约不变）
        assert all(isinstance(v, list) for v in sim["recommendedResponse"].values())


# ---------------------------------------------------------------------------
# 矩阵端点注册表驱动（P3-07）
# ---------------------------------------------------------------------------


class TestRobustnessEndpointWiring:
    """/tuning/tune/robustness 端点接线（薄壳：服务已 mock，验证路由/审计/透传）."""

    def test_endpoint_wires_service_and_audits(self, client) -> None:
        from tests.conftest import TEST_USERS, mock_current_user

        report = {
            "modelType": "IPDT",
            "overallPass": False,
            "setBasis": {"memberCount": 64},
            "indicators": {},
        }
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(
                "app.api.v1.endpoints.tuning._ensure_tuning_fitness",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.api.v1.endpoints.tuning.verify_tuning_robustness",
                new=AsyncMock(return_value=report),
            ) as mock_verify,
        ):
            resp = client.post(
                "/api/v1/tuning/tune/robustness",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopId": _LOOP_ID,
                    "modelType": "IPDT",
                    "modelParams": dict(_IPDT_PARAMS),
                    "algorithm": "IPDT_PI",
                    "algorithmParams": {"lambdaRatio": 2.0},
                    "sourceRecordId": _SOURCE_ID,
                    "modelSource": "IDENTIFICATION_RECORD",
                    "riskConfirmed": True,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "0"
        assert data["data"]["modelType"] == "IPDT"
        assert mock_verify.await_count == 1
        assert mock_verify.await_args.kwargs["algorithm"] == "IPDT_PI"
        assert mock_verify.await_args.kwargs["algorithm_params"] == {"lambdaRatio": 2.0}

    def test_endpoint_rejects_invalid_lambda(self, client) -> None:
        """schema 层 422：lambdaRatio 越界按注册表原子拒绝（P2-01）."""
        from tests.conftest import TEST_USERS, mock_current_user

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/tune/robustness",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopId": _LOOP_ID,
                    "modelType": "IPDT",
                    "modelParams": dict(_IPDT_PARAMS),
                    "algorithm": "IPDT_PI",
                    "algorithmParams": {"lambdaRatio": 7.5},
                },
            )
        assert resp.status_code == 422


class TestMatrixRegistryDriven:
    def test_applicable_methods_per_model(self) -> None:
        fopdt = [m["code"] for m in TUNING_METHODS_INFO if m["applicableModel"] == "FOPDT"]
        assert fopdt == ["IMC", "LAMBDA", "ZN", "COHEN_COON", "SIMC"]
        assert [m["code"] for m in TUNING_METHODS_INFO if m["applicableModel"] == "IPDT"] == [
            "IPDT_PI"
        ]
        assert [m["code"] for m in TUNING_METHODS_INFO if m["applicableModel"] == "SOPDT"] == [
            "SOPDT_IMC"
        ]
