"""G3 增量纠偏（2026-10-13，17 号文 R01/R02/R03）定向回归测试.

- R01 慢过程仿真证据不足不得总体 PASS：四类反例（峰值窗内正常 PASS /
  峰值窗外独立长窗参考 / 临界边缘 / 正负增益镜像）+ 长窗一致性；
- R02 零宽/极窄区间有界终止：四类（全零宽 / 极窄 / 混合固定自由 /
  常规宽区间不回归）+ 确定性 + 服务链（真实 manifest 文件路径）+
  端到端 TestClient（禁 lifespan，CLPM_TEST_MODE=1）；
- R03 适用性限制贯通消费链：普通建议（/tune）→ 矩阵（/tune/matrix）→
  保存（create_tuning_task）→ 重新读取（get_tuning_task_detail）四链全通，
  低 SNR 带 LOW_SNR_PARAM_UNCOMMITTED + "不承诺参数级精度"，未校准
  区间带 calibrationStatus + "仅证明被测集合成员"；S1-b 放行政策保持
  （risk 因素如实陈述、不加风险分、不构成新拒绝门禁）。

oracle 独立性纪律：R01 长窗参考使用测试内独立实现的位置式 PI +
方法-of-steps 显式时滞仿真器（与产品增量式内核不同实现），禁止用被测
实现自产期望值（与 test_p3_07 `_independent_positional_pi_sim` 同族）。
"""

from __future__ import annotations

import contextlib
import json
import signal
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tuning import (
    TuningModelAuthorization,
    create_tuning_task,
    get_tuning_task_detail,
    tune_pid,
    verify_tuning_robustness,
)
from app.services.tuning_algorithms import (
    ROBUST_SET_FILL_ATTEMPT_BUDGET,
    ROBUSTNESS_OVERSHOOT_MAX_PCT,
    build_trustworthy_set,
    tune_ipdt_pi,
    verify_robustness_on_set,
)

# ---------------------------------------------------------------------------
# 公共夹具（与 test_p3_07_robust_tuning 同款程式）
# ---------------------------------------------------------------------------

_LOOP_ID = "00000000-0000-0000-0000-0000000g3rm"
_SOURCE_ID = "00000000-0000-0000-0000-00000g3src"
_SNAPSHOT_ID = "snap-g3rem-0001"

_IPDT_PARAMS = {"K": 0.1, "theta": 10.0}
_FOPDT_PARAMS = {"K": 1.0, "tau": 10.0, "theta": 2.0}

_FV_PASSED = {"status": "PASSED", "channels": {"finalTest": "PASSED"}}

_WIDE_OP_LIMITS = (-100.0, 100.0)

_PID_REPR_KNOWN = {
    "status": "KNOWN",
    "modelCode": "TEST-DCS-PB-MIN",
    "pType": "PROPORTION_BAND",
    "iUnit": "MINUTES",
    "dUnit": "MINUTES",
    "dFilterEnabled": False,
    "dcsTemplateRevision": "dcs_model:test",
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


def _source_record(
    *,
    model_type: str = "IPDT",
    model_params: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=_SOURCE_ID,
        loop_id=_LOOP_ID,
        status="IDENTIFIED",
        task_id="celery-g3rem",
        model_type=model_type,
        model_params=dict(model_params or _IPDT_PARAMS),
        process_model_version_id="pmv-g3rem-1",
        confidence_level="A",
        confidence_reason="拟合与残差检验通过",
        identify_method="HISTORICAL_ARX",
        data_source="HISTORY",
        fitting_score=93.5,
        excitation_score=0.72,
        residual_test_passed=True,
        dataset_snapshot_id=_SNAPSHOT_ID,
        time_window_start=datetime(2026, 10, 1, 0, 0, 0),
        time_window_end=datetime(2026, 10, 1, 2, 0, 0),
    )


def _write_manifest(
    tmp_path,
    *,
    final_verification=None,
    reselect=None,
    low_snr=None,
    low_snr_key: str = "preprocess",
) -> None:
    """写辨识输入包 manifest（R03：支持 resultEvidence.lowSnr / preprocess.lowSnr 两落点）."""
    month = tmp_path / "202610"
    month.mkdir(parents=True, exist_ok=True)
    result_evidence: dict = {
        "finalVerification": final_verification,
        "reselectUncertainty": reselect,
    }
    manifest: dict = {"resultEvidence": result_evidence}
    if low_snr_key == "resultEvidence":
        result_evidence["lowSnr"] = low_snr
    else:
        manifest["preprocess"] = {"lowSnr": low_snr}
    (month / f"{_SNAPSHOT_ID}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _make_db(*, source=None, loop=None) -> MagicMock:
    db = MagicMock()
    loop_result = MagicMock()
    loop_result.scalar_one_or_none = MagicMock(
        return_value=loop
        or SimpleNamespace(id=_LOOP_ID, tag_name="LIC-G3RM", is_active=True, dcs_model_id=None)
    )
    db.execute = AsyncMock(return_value=loop_result)
    db.get = AsyncMock(return_value=source if source is not None else _source_record())
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@contextlib.contextmanager
def _patched_service_deps(facts: dict, params: dict):
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


def _auth_ctx(
    model_type: str,
    params: dict,
    *,
    final_verification: dict | None = None,
    identification_manifest: dict | None = None,
) -> TuningModelAuthorization:
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
        model_version_id="pmv-g3rem-1",
        snapshot_id=_SNAPSHOT_ID,
        final_verification=final_verification,
        identification_manifest=identification_manifest,
    )


_LOW_SNR_MANIFEST = {
    "resultEvidence": {
        "lowSnr": {
            "mode": "PARAM_ACCURACY_UNCOMMITTED",
            "snrDb": 14.2,
            "thresholdDb": 20.0,
            "basis": "WELCH_PSD_NOISE_FLOOR_JOINT_AGGREGATE",
        },
        "reselectUncertainty": {
            "method": "RESELECT_DELAY_STRUCTURE",
            "status": "COMPLETED",
            "K": {"ci95": [0.096, 0.104]},
            "theta": {"ci95": [9.5, 10.5]},
        },
    }
}


# ---------------------------------------------------------------------------
# R01：慢过程仿真证据不足不得总体 PASS
# ---------------------------------------------------------------------------


def _independent_pi_sim(K: float, theta: float, Kc: float, Ti: float, T: float, dt: float = 0.5):
    """独立仿真器（非产品代码）：位置式理想 PI + 方法-of-steps 显式时滞.

    返回 (peak, peak_time)。与产品增量式内核不同实现（oracle 独立纪律）。
    """
    n = int(T / dt)
    nd = int(round(theta / dt))
    hist = [0.0] * (nd + 1)
    x = 0.0
    iint = 0.0
    peak = 0.0
    peak_t = 0.0
    for i in range(n):
        e = 1.0 - x
        op = Kc * e + (Kc / Ti) * iint
        hist.append(op)
        if len(hist) > nd + 1:
            hist.pop(0)
        u = hist[0]

        def f(s, uu=u):
            return (K * uu, 1.0 - s[0])

        st = [x, iint]
        k1 = f(st)
        k2 = f([st[j] + 0.5 * dt * k1[j] for j in range(2)])
        k3 = f([st[j] + 0.5 * dt * k2[j] for j in range(2)])
        k4 = f([st[j] + dt * k3[j] for j in range(2)])
        x = st[0] + dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        iint = st[1] + dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        if x > peak:
            peak, peak_t = x, (i + 1) * dt
    return peak, peak_t


class TestR01SlowWindowEvidence:
    """R01 四类反例：窗内正常 / 峰值窗外（独立长窗）/ 临界边缘 / 负增益镜像."""

    @staticmethod
    def _wide_set(K: float, theta: float):
        ci = {"K": [K * 0.95, K * 1.05], "theta": [theta * 0.9, theta * 1.1]}
        return build_trustworthy_set("IPDT", {"K": K, "theta": theta}, ci)

    def test_r01_fast_window_peak_inside_pass(self) -> None:
        """反例类 1（峰值窗内正常 PASS）：θ=10 λ=7 快过程窗内回稳 → PASS 保持."""
        pid = tune_ipdt_pi(0.1, 10.0, 7.0)
        s = self._wide_set(0.1, 10.0)
        r = verify_robustness_on_set("IPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        assert r["overallPass"] is True
        assert r["overallStatus"] == "PASS"
        assert r["simulationEvidence"]["insufficientMembers"] == 0
        assert r["indicators"]["overshoot"]["status"] == "PASS"
        # 独立长窗一致性：窗内结论与 1800s 长窗结论一致（都 < 20%）
        peak600, _ = _independent_pi_sim(0.105, 11.0, pid.kp, pid.ti, 600.0)
        peak1800, _ = _independent_pi_sim(0.105, 11.0, pid.kp, pid.ti, 1800.0)
        assert (peak600 - 1.0) * 100 < ROBUSTNESS_OVERSHOOT_MAX_PCT
        assert (peak1800 - 1.0) * 100 < ROBUSTNESS_OVERSHOOT_MAX_PCT

    def test_r01_slow_window_peak_outside_inconclusive_with_long_window_reference(self) -> None:
        """反例类 2（峰值窗外，17 号文 R01 主反例）：θ=60 λ=6 慢 IPDT.

        修复前（17 号文/G3 λ 验收 F2）：600s 窗内最大超调 ≈19.14% →
        overallPass=True 误报；独立长窗（1800s）真值 ≈20.3% > 20% 冻结
        阈值，峰值 ~730s 在窗外。修复后：INCONCLUSIVE、不得总体 PASS。
        """
        K, theta, lr = 0.1, 60.0, 6.0
        pid = tune_ipdt_pi(K, theta, lr)
        assert pid.kp == pytest.approx(0.044218, abs=1e-6)
        assert pid.ti == pytest.approx(780.0)
        s = self._wide_set(K, theta)
        r = verify_robustness_on_set("IPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        # 修复后：证据不足 → INCONCLUSIVE，不得总体 PASS
        assert r["overallPass"] is False
        assert r["overallStatus"] == "INCONCLUSIVE"
        assert r["indicators"]["overshoot"]["status"] == "INCONCLUSIVE"
        assert r["indicators"]["overshoot"]["pass"] is False
        assert r["simulationEvidence"]["insufficientMembers"] == s["memberCount"]
        assert r["simulationEvidence"]["criterion"] == "TAIL_IN_2PCT_SETTLING_BAND"
        assert "窗外" in r["simulationEvidence"]["note"]
        # 窗内数值锚点（G3 λ 验收 F2 登记口径）
        assert 18.5 < r["indicators"]["overshoot"]["worstPct"] < 19.5
        # 独立长窗参考（四角点，固定名义整定 PID——与验证链同构）：
        # 600s 窗内最坏 < 20%；1800s 长窗最坏 > 20%（峰值在窗外）
        worst600 = worst1800 = 0.0
        peak_time_worst = 0.0
        for k_ in (K * 0.95, K * 1.05):
            for th_ in (theta * 0.9, theta * 1.1):
                p600, _ = _independent_pi_sim(k_, th_, pid.kp, pid.ti, 600.0)
                p1800, pt = _independent_pi_sim(k_, th_, pid.kp, pid.ti, 1800.0)
                worst600 = max(worst600, (p600 - 1.0) * 100)
                if (p1800 - 1.0) * 100 > worst1800:
                    worst1800, peak_time_worst = (p1800 - 1.0) * 100, pt
        assert worst600 < ROBUSTNESS_OVERSHOOT_MAX_PCT  # 窗内会误判通过
        assert worst1800 > ROBUSTNESS_OVERSHOOT_MAX_PCT  # 长窗真值超限
        assert peak_time_worst > 600.0  # 峰值在窗外（≈730s）

    def test_r01_slow_critical_edge_inconclusive(self) -> None:
        """反例类 3a（慢过程临界边缘）：θ=60 λ=7 窗内 15.63%，长窗真值 18.24%.

        真值虽仍 < 20%，但 600s 窗内无法证明（未回稳）→ 诚实 INCONCLUSIVE
        （G3 λ 验收 F2 登记的 θ=60 行，成因=真实峰值落在窗外）。
        """
        K, theta, lr = 0.1, 60.0, 7.0
        pid = tune_ipdt_pi(K, theta, lr)
        s = self._wide_set(K, theta)
        r = verify_robustness_on_set("IPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        assert r["overallStatus"] == "INCONCLUSIVE"
        assert r["overallPass"] is False
        assert r["indicators"]["overshoot"]["worstPct"] < 20.0
        # 独立长窗真值仍 < 20%（本档长窗真值过），但窗内证据不足不可判 PASS
        worst1800 = 0.0
        for k_ in (K * 0.95, K * 1.05):
            for th_ in (theta * 0.9, theta * 1.1):
                p1800, _ = _independent_pi_sim(k_, th_, pid.kp, pid.ti, 1800.0)
                worst1800 = max(worst1800, (p1800 - 1.0) * 100)
        assert worst1800 < ROBUSTNESS_OVERSHOOT_MAX_PCT

    def test_r01_fast_critical_edge_conclusive(self) -> None:
        """反例类 3b（快过程临界边缘 ~20%±ε）：θ=10 λ=6.5 窗内 19.62% 回稳.

        峰值在窗内且已回稳 → 证据充分，临界值仍判 PASS（不因保守判据
        把窗内可证的临界案例误报 INCONCLUSIVE）。
        """
        pid = tune_ipdt_pi(0.1, 10.0, 6.5)
        s = self._wide_set(0.1, 10.0)
        r = verify_robustness_on_set("IPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        assert r["overallStatus"] == "PASS"
        assert r["indicators"]["overshoot"]["status"] == "PASS"
        assert 19.0 < r["indicators"]["overshoot"]["worstPct"] < 20.0

    @pytest.mark.parametrize("K", [0.1, -0.1])
    def test_r01_gain_sign_mirror_inconclusive(self, K: float) -> None:
        """反例类 4（正负增益镜像）：K 符号翻转（Kc 随之变号）慢窗同样 INCONCLUSIVE.

        镜像恒等：闭环指标与正增益逐位一致（G3 λ 验收 F1 口径——正确镜像
        CI 构造下线性系统镜像恒等）；证据判据按 |PV−SP| 判定，符号无关。
        """
        theta, lr = 60.0, 6.0
        pid = tune_ipdt_pi(K, theta, lr)
        ci = {
            "K": [K * 0.95, K * 1.05] if K > 0 else [K * 1.05, K * 0.95],
            "theta": [theta * 0.9, theta * 1.1],
        }
        s = build_trustworthy_set("IPDT", {"K": K, "theta": theta}, ci)
        r = verify_robustness_on_set("IPDT", s["members"], pid, op_limits=_WIDE_OP_LIMITS)
        assert r["overallPass"] is False
        assert r["overallStatus"] == "INCONCLUSIVE"
        assert r["simulationEvidence"]["insufficientMembers"] == 64
        assert r["indicators"]["overshoot"]["status"] == "INCONCLUSIVE"

    def test_r01_fail_case_still_fail_not_masked_by_inconclusive(self) -> None:
        """窗内已超限（快过程 λ=5，≈20.8%）→ 仍判 FAIL，不被 INCONCLUSIVE 掩盖."""
        pid = tune_ipdt_pi(0.1, 10.0, 5.0)
        r = verify_robustness_on_set("IPDT", [dict(_IPDT_PARAMS)], pid, op_limits=_WIDE_OP_LIMITS)
        assert r["overallStatus"] == "FAIL"
        assert r["indicators"]["overshoot"]["status"] == "FAIL"
        assert r["indicators"]["overshoot"]["worstPct"] > ROBUSTNESS_OVERSHOOT_MAX_PCT

    def test_r01_frozen_thresholds_unchanged(self) -> None:
        """R01 不动 R15 冻结阈值与 B-11 预算（数值守卫）."""
        from app.services.tuning_algorithms import (
            ROBUST_SET_MAX_MEMBERS,
            ROBUST_SET_MAX_SIM_DURATION,
            ROBUSTNESS_GAIN_MARGIN_MIN,
            ROBUSTNESS_PHASE_MARGIN_MIN_DEG,
        )

        assert (ROBUSTNESS_GAIN_MARGIN_MIN, ROBUSTNESS_PHASE_MARGIN_MIN_DEG) == (2.0, 30.0)
        assert (ROBUSTNESS_OVERSHOOT_MAX_PCT, ROBUST_SET_MAX_MEMBERS) == (20.0, 64)
        assert ROBUST_SET_MAX_SIM_DURATION == 600.0


# ---------------------------------------------------------------------------
# R02：零宽/极窄区间有界终止
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _alarm_guard(seconds: int = 15):
    """受控反例护栏：若死循环复发（修复回归），以 TimeoutError 失败而非挂死."""

    def _handler(signum, frame):
        raise TimeoutError(f"build_trustworthy_set 未在 {seconds}s 内终止（死循环回归）")

    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _override_app_db(db) -> None:
    """TestClient 用例：把 get_db 依赖覆写为携带来源记录的 mock 会话."""
    from app.core.db import get_db
    from app.main import app

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override


class TestR02DegenerateIntervalBoundedTermination:
    """R02 四类：全零宽 / 极窄（<舍入分辨率）/ 混合固定自由 / 常规宽区间."""

    def test_r02_all_zero_width_bounded(self) -> None:
        """反例类 1（全零宽，17 号文 R02 探针输入）：K=[1,1] tau=[10,10] theta=[2,2].

        修复前：K/tau/theta 均为"自由"维度但均匀采样去重后只有 1 个成员，
        `while len(members) < 64` 无尝试上限 → 死循环。修复后：全部按固定
        维度处理，集合=名义单点，有界终止 + 原因码。
        """
        with _alarm_guard():
            z = build_trustworthy_set(
                "FOPDT",
                {"K": 1.0, "tau": 10.0, "theta": 2.0},
                {"K": [1.0, 1.0], "tau": [10.0, 10.0], "theta": [2.0, 2.0]},
            )
        assert z["memberCount"] == 1
        assert z["members"] == [{"K": 1.0, "tau": 10.0, "theta": 2.0}]
        assert z["freeKeys"] == []
        assert set(z["fixedKeys"]) == {"K", "tau", "theta"}
        cons = z["setConstruction"]
        assert cons["actualMemberCount"] == 1
        assert set(cons["degenerateKeysFixed"]) == {"K", "tau", "theta"}
        assert "DEGENERATE_INTERVALS_FIXED" in cons["reasonCodes"]
        assert "ALL_DIMENSIONS_FIXED" in cons["reasonCodes"]

    def test_r02_sub_resolution_narrow_bounded(self) -> None:
        """反例类 2（极窄，宽度 < 去重分辨率 1e-9）：按固定维度处理."""
        with _alarm_guard():
            z = build_trustworthy_set(
                "FOPDT",
                {"K": 1.0, "tau": 10.0, "theta": 2.0},
                {"K": [1.0, 1.0 + 5e-10], "tau": [10.0, 10.0], "theta": [2.0, 2.0]},
            )
        assert z["memberCount"] == 1
        assert set(z["setConstruction"]["degenerateKeysFixed"]) == {"K", "tau", "theta"}

    def test_r02_mixed_fixed_and_free_dimensions(self) -> None:
        """反例类 3（混合）：零宽 K 固定 + tau/theta 自由 → 64 成员、原因码如实."""
        with _alarm_guard():
            z = build_trustworthy_set(
                "FOPDT",
                _FOPDT_PARAMS,
                {"K": [1.0, 1.0], "tau": [8.0, 12.0], "theta": [1.8, 2.2]},
            )
        assert z["memberCount"] == 64
        assert z["freeKeys"] == ["tau", "theta"]
        assert z["fixedKeys"] == ["K"]
        cons = z["setConstruction"]
        assert cons["degenerateKeysFixed"] == ["K"]
        assert cons["reasonCodes"] == ["DEGENERATE_INTERVALS_FIXED"]
        # 固定维度成员值=名义值（与既有 fixed 语义一致）
        assert {m["K"] for m in z["members"]} == {1.0}

    def test_r02_wide_interval_no_regression(self) -> None:
        """反例类 4（常规宽区间）：行为不回归——64 成员、角点包含、确定性."""
        with _alarm_guard():
            a = build_trustworthy_set(
                "IPDT", {"K": 0.1, "theta": 10.0}, {"K": [0.095, 0.105], "theta": [9.0, 11.0]}
            )
            b = build_trustworthy_set(
                "IPDT", {"K": 0.1, "theta": 10.0}, {"K": [0.095, 0.105], "theta": [9.0, 11.0]}
            )
        assert a["memberCount"] == 64
        assert a["members"] == b["members"]  # 同输入同结果（确定性）
        assert a["setConstruction"]["reasonCodes"] == []
        assert {round(m["K"], 6) for m in a["members"]} >= {0.095, 0.105}  # 角点保留

    def test_r02_narrow_but_valid_width_fills_or_stops_with_reason(self) -> None:
        """上游四位小数舍入产生的极窄（1e-4 宽）有效维度：能填满则填满，
        不能填满时按尝试预算有界终止并携带原因码（不无限重采样）."""
        with _alarm_guard():
            z = build_trustworthy_set(
                "FOPDT",
                _FOPDT_PARAMS,
                {"K": [1.0, 1.0], "tau": [10.0, 10.0], "theta": [2.0, 2.0 + 1e-4]},
            )
        assert z["memberCount"] <= 64
        assert z["setConstruction"]["fillAttempts"] <= ROBUST_SET_FILL_ATTEMPT_BUDGET
        if z["memberCount"] < 64:
            assert "FILL_ATTEMPT_BUDGET_EXHAUSTED" in z["setConstruction"]["reasonCodes"]

    @pytest.mark.asyncio
    async def test_r02_service_chain_degenerate_ci_returns(self, tmp_path) -> None:
        """服务链（真实 manifest 文件路径）：零宽 ci95 → 名义单点集合 + 构造说明.

        17 号文 R02 服务调用锚点（services/tuning.py verify_tuning_robustness
        内 build_trustworthy_set）——退化区间经完整服务链（授权→manifest
        读取→集合→仿真→报告）可返回，不挂死。
        """
        _write_manifest(
            tmp_path,
            final_verification=_FV_PASSED,
            reselect={
                "method": "RESELECT_DELAY_STRUCTURE",
                "status": "COMPLETED",
                "K": {"ci95": [0.1, 0.1]},
                "theta": {"ci95": [10.0, 10.0]},
            },
        )
        db = _make_db()
        with (
            patch("app.services.calc_snapshot.snapshot_root", new=lambda: tmp_path),
            _patched_service_deps(_FACTS_ACTUAL, _IPDT_PARAMS),
            _alarm_guard(),
        ):
            report = await verify_tuning_robustness(
                db,
                loop_id=_LOOP_ID,
                model_type="IPDT",
                model_params=dict(_IPDT_PARAMS),
                algorithm="IPDT_PI",
                algorithm_params={"lambdaRatio": 7.0},
                source_record_id=_SOURCE_ID,
                model_source="IDENTIFICATION_RECORD",
                risk_confirmed=True,
            )
        basis = report["setBasis"]
        assert basis["source"] == "RESELECT_UNCERTAINTY"
        assert basis["memberCount"] == 1  # 退化区间 → 名义单点，如实报告
        cons = basis["setConstruction"]
        assert set(cons["degenerateKeysFixed"]) == {"K", "theta"}
        assert "ALL_DIMENSIONS_FIXED" in cons["reasonCodes"]
        # 单成员（名义点）快过程 θ=10 λ=7：四项指标照常判定（名义点超调
        # 低于 64 宽集合最坏 19.12——集合缩小后最坏=名义单点）
        assert report["overallPass"] is True
        assert 18.0 < report["indicators"]["overshoot"]["worstPct"] < 19.5

    def test_r02_endpoint_returns_via_testclient(self, client, tmp_path) -> None:
        """端到端 TestClient（禁 lifespan）：退化 ci95 请求经真实路由+真实服务返回."""
        from tests.conftest import TEST_USERS, mock_current_user

        _write_manifest(
            tmp_path,
            final_verification=_FV_PASSED,
            reselect={
                "method": "RESELECT_DELAY_STRUCTURE",
                "status": "COMPLETED",
                "K": {"ci95": [0.1, 0.1]},
                "theta": {"ci95": [10.0, 10.0]},
            },
        )
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(
                "app.api.v1.endpoints.tuning._ensure_tuning_fitness",
                new=AsyncMock(return_value=None),
            ),
            patch("app.services.calc_snapshot.snapshot_root", new=lambda: tmp_path),
            _patched_service_deps(_FACTS_ACTUAL, _IPDT_PARAMS),
        ):
            _override_app_db(_make_db())
            resp = client.post(
                "/api/v1/tuning/tune/robustness",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopId": _LOOP_ID,
                    "modelType": "IPDT",
                    "modelParams": dict(_IPDT_PARAMS),
                    "algorithm": "IPDT_PI",
                    "sourceRecordId": _SOURCE_ID,
                    "modelSource": "IDENTIFICATION_RECORD",
                    "riskConfirmed": True,
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["setBasis"]["memberCount"] == 1
        assert data["setBasis"]["setConstruction"]["reasonCodes"]
        assert data["overallPass"] is True


# ---------------------------------------------------------------------------
# R03：适用性限制贯通消费链（普通建议 → 矩阵 → 保存 → 重新读取）
# ---------------------------------------------------------------------------


class TestR03ApplicabilityLimitationsChains:
    """R03：低 SNR / ci95 未校准限制四链全通（加性携带，S1-b 放行政策保持）."""

    @pytest.mark.asyncio
    async def test_r03_tune_pid_carries_limitations_and_risk_reads_low_snr(self) -> None:
        """链 1（普通建议 tune_pid）：低 SNR + ci95 → applicabilityLimitations 在场."""
        ctx = _auth_ctx(
            "IPDT",
            _IPDT_PARAMS,
            final_verification=_FV_PASSED,
            identification_manifest=_LOW_SNR_MANIFEST,
        )
        result = await tune_pid(
            "IPDT", dict(_IPDT_PARAMS), "IPDT_PI", {"lambdaRatio": 2.0}, source_context=ctx
        )
        items = result["applicabilityLimitations"]["items"]
        codes = {i["reasonCode"] for i in items}
        assert codes == {"LOW_SNR_PARAM_UNCOMMITTED", "CI95_UNCALIBRATED"}
        low = next(i for i in items if i["reasonCode"] == "LOW_SNR_PARAM_UNCOMMITTED")
        assert "不承诺参数级精度" in low["statement"]
        assert low["evidence"]["snrDb"] == 14.2
        assert low["evidence"]["basis"] == "WELCH_PSD_NOISE_FLOOR_JOINT_AGGREGATE"
        ci = next(i for i in items if i["reasonCode"] == "CI95_UNCALIBRATED")
        assert ci["calibrationStatus"] == "uncalibrated"
        assert "仅证明被测集合成员" in ci["statement"]
        assert ci["evidence"]["ciSource"] == "RESELECT_UNCERTAINTY"
        # 通用 risk 读取低 SNR 标记（S1-b：因素如实陈述）
        factors_text = "；".join(result["risk"]["factors"])
        assert "LOW_SNR_PARAM_UNCOMMITTED" in factors_text
        assert "不承诺参数级精度" in factors_text

    @pytest.mark.asyncio
    async def test_r03_risk_level_not_inflated_by_low_snr(self) -> None:
        """S1-b 放行政策保持：低 SNR 因素不加分——riskLevel 与无标注来源一致."""
        base_ctx = _auth_ctx("IPDT", _IPDT_PARAMS, final_verification=_FV_PASSED)
        low_ctx = _auth_ctx(
            "IPDT",
            _IPDT_PARAMS,
            final_verification=_FV_PASSED,
            identification_manifest=_LOW_SNR_MANIFEST,
        )
        base = await tune_pid("IPDT", dict(_IPDT_PARAMS), "IPDT_PI", None, source_context=base_ctx)
        low = await tune_pid("IPDT", dict(_IPDT_PARAMS), "IPDT_PI", None, source_context=low_ctx)
        assert base["risk"]["riskLevel"] == low["risk"]["riskLevel"] == "LOW"
        assert len(low["risk"]["factors"]) == len(base["risk"]["factors"]) + 1
        assert "applicabilityLimitations" not in base  # 无已知限制 → 缺键不伪补

    @pytest.mark.asyncio
    async def test_r03_low_snr_result_evidence_key_chain(self) -> None:
        """低 SNR 三键回退链：resultEvidence.lowSnr（G3-APP joint 落点）优先命中."""
        manifest = {
            "resultEvidence": {
                "lowSnr": {
                    "mode": "PARAM_ACCURACY_UNCOMMITTED",
                    "snrDb": -3.15,
                    "thresholdDb": 20.0,
                }
            }
        }
        ctx = _auth_ctx(
            "IPDT",
            _IPDT_PARAMS,
            final_verification=_FV_PASSED,
            identification_manifest=manifest,
        )
        result = await tune_pid("IPDT", dict(_IPDT_PARAMS), "IPDT_PI", None, source_context=ctx)
        (item,) = result["applicabilityLimitations"]["items"]
        assert item["reasonCode"] == "LOW_SNR_PARAM_UNCOMMITTED"
        assert item["evidence"]["snrDb"] == -3.15

    def test_r03_matrix_endpoint_rows_carry_limitations(self, client, tmp_path) -> None:
        """链 2（矩阵 /tune/matrix）：授权来源限制逐行加性携带（经 TestClient）."""
        from tests.conftest import TEST_USERS, mock_current_user

        _write_manifest(
            tmp_path,
            final_verification=_FV_PASSED,
            low_snr={
                "mode": "PARAM_ACCURACY_UNCOMMITTED",
                "snrDb": 14.2,
                "thresholdDb": 20.0,
            },
        )
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(
                "app.api.v1.endpoints.tuning._ensure_tuning_fitness",
                new=AsyncMock(return_value=None),
            ),
            patch("app.services.calc_snapshot.snapshot_root", new=lambda: tmp_path),
            _patched_service_deps(_FACTS_ACTUAL, _IPDT_PARAMS),
        ):
            _override_app_db(_make_db())
            resp = client.post(
                "/api/v1/tuning/tune/matrix",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopId": _LOOP_ID,
                    "modelType": "IPDT",
                    "modelParams": dict(_IPDT_PARAMS),
                    "sourceRecordId": _SOURCE_ID,
                    "modelSource": "IDENTIFICATION_RECORD",
                    "riskConfirmed": True,
                },
            )
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]["rows"]
        assert rows and all(r["ok"] for r in rows), rows
        for row in rows:
            lims = row["result"]["applicabilityLimitations"]["items"]
            assert {i["reasonCode"] for i in lims} == {"LOW_SNR_PARAM_UNCOMMITTED"}
            assert any("不承诺参数级精度" in i["statement"] for i in lims)

    @pytest.mark.asyncio
    async def test_r03_save_and_reload_limitations_persisted(self, tmp_path) -> None:
        """链 3+4（保存→重新读取）：低 SNR 经真实持久化路径不丢失.

        真实 manifest 文件（tmp 快照根）→ create_tuning_task（授权+派生+
        calc_context 冻结）→ get_tuning_task_detail（_record_to_dict 回读）。
        """
        _write_manifest(
            tmp_path,
            final_verification=_FV_PASSED,
            reselect={
                "method": "RESELECT_DELAY_STRUCTURE",
                "status": "COMPLETED",
                "K": {"ci95": [0.096, 0.104]},
                "theta": {"ci95": [9.5, 10.5]},
            },
            low_snr={"mode": "PARAM_ACCURACY_UNCOMMITTED", "snrDb": 14.2, "thresholdDb": 20.0},
        )
        db = _make_db()
        with (
            patch("app.services.calc_snapshot.snapshot_root", new=lambda: tmp_path),
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
                recommended_pid={"kp": 0.29, "ti": 150.0, "td": 0.0},
                model_source="IDENTIFICATION_RECORD",
                source_record_id=_SOURCE_ID,
                risk_confirmed=True,
                simulation_result={"candidateResponses": [{"label": "x"}]},
                status="SIMULATED",
                created_by="tester",
            )
            assert saved["simulationStatus"] == "SIMULATED"
            record = db.add.call_args[0][0]
            frozen = record.calc_context["applicabilityLimitations"]["items"]
            assert {i["reasonCode"] for i in frozen} == {
                "LOW_SNR_PARAM_UNCOMMITTED",
                "CI95_UNCALIBRATED",
            }
            assert "不承诺参数级精度" in frozen[0]["statement"]

            # 重新读取（get_tuning_task_detail → _record_to_dict 消费
            # calc_context 冻结值；execute 结果按 (record, tag) 元组形状）
            detail_result = MagicMock()
            detail_result.first = MagicMock(return_value=(record, "LIC-G3RM"))
            db.execute = AsyncMock(return_value=detail_result)
            detail = await get_tuning_task_detail(db, task_id=str(record.id))
        reloaded = detail["applicabilityLimitations"]["items"]
        assert reloaded == frozen  # 回读不漂移
        codes = {i["reasonCode"] for i in reloaded}
        assert codes == {"LOW_SNR_PARAM_UNCOMMITTED", "CI95_UNCALIBRATED"}

    @pytest.mark.asyncio
    async def test_r03_robust_chain_set_basis_carries_calibration_status(self, tmp_path) -> None:
        """鲁棒链 setBasis：未校准 ci95 → calibrationStatus + 成员边界 + 统一限制."""
        _write_manifest(
            tmp_path,
            final_verification=_FV_PASSED,
            reselect={
                "method": "RESELECT_DELAY_STRUCTURE",
                "status": "COMPLETED",
                "K": {"ci95": [0.096, 0.104]},
                "theta": {"ci95": [9.5, 10.5]},
            },
            low_snr={"mode": "PARAM_ACCURACY_UNCOMMITTED", "snrDb": 14.2, "thresholdDb": 20.0},
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
                algorithm_params={"lambdaRatio": 7.0},
                source_record_id=_SOURCE_ID,
                model_source="IDENTIFICATION_RECORD",
                risk_confirmed=True,
            )
        assert report["setBasis"]["calibrationStatus"] == "uncalibrated"
        assert "仅证明被测集合成员" in report["setBasis"]["membershipScope"]
        assert report["setBasis"]["setConstruction"]["actualMemberCount"] == 64
        lims = report["applicabilityLimitations"]["items"]
        assert {i["reasonCode"] for i in lims} == {
            "LOW_SNR_PARAM_UNCOMMITTED",
            "CI95_UNCALIBRATED",
        }
        # 既有 lowSnrDisclaimer 键兼容保持（S1-b 知悉传递不回退）
        assert report["lowSnrDisclaimer"]["mode"] == "PARAM_ACCURACY_UNCOMMITTED"
        assert "不承诺参数级精度" in report["lowSnrDisclaimer"]["disclaimer"]

    @pytest.mark.asyncio
    async def test_r03_clean_source_no_limitation_keys(self, tmp_path) -> None:
        """干净来源（无低 SNR/无 CI 证据）：不携带限制键（缺键不伪补）."""
        _write_manifest(tmp_path, final_verification=_FV_PASSED)
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
                source_record_id=_SOURCE_ID,
                model_source="IDENTIFICATION_RECORD",
                risk_confirmed=True,
            )
        assert report["applicabilityLimitations"] is None
        assert "calibrationStatus" not in report["setBasis"]
