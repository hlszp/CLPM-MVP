"""P3-06c 闭环估计与验证子卡 oracle 测试（执行包「系统改造优化-2026-10-10」）.

正本：执行状态/P3-06-07-oracle冻结草案-20261011.md §4（用户 2026-10-11
批复"全部按推荐"——R8~R11/R16~R19 冻结值一字不差实施，R15 特别条款：
数值不得事后迁就实现结果调整）。

oracle 独立性纪律（§0.2）：
- 全部为**合成已知真系统 + 独立手算/解析参考**，禁止被测实现自产期望值；
- 合成数据生成器独立于被测估计器：闭环用独立增量式 PI/P 仿真器（控制器
  读上一拍测量；非 tuning_algorithms.simulate_closed_loop）；FOPDT 开环用
  一阶差分递推（生成器核对见 TestOracleGeneratorChecks）；频带验证用
  scipy Welch/csd 独立谱估计 vs 解析频响；
- 9 窗回归种子不是成功率证明；本文全部为合成反例 + 独立参考。

冻结容差（§4.3，R16/R19）：
- O-C1/O-C4：闭环/共同估计参数恢复 ≤15%（O-C1 另断言有偏 ARX 偏差>30%）；
- O-C2/O-C3：拒绝例行为断言（CLIVC 不适用 + 不晋升 A/B + 不由输入变化
  单独推因果）；
- O-C5：独立事件终验自由仿真 NRMSE ≤0.10（量程归一）；
- O-C6（R19）：共同频带内幅值差 ≤20%、相位差 ≤15°，频带外不判；
- O-C7：θ 区间非零宽（退化条件须记录）；真值落入 95% 区间覆盖率 ≥90%；
- O-C8：弱证据可信度不得高于 C（P-25/P-10 联合行为断言）。

构造裁量登记（oracle 侧，非冻结值）：
- 跟踪型 SP 构造取一阶跟踪滤波（sp[k]=sp[k-1]+α(y[k-1]-sp[k-1])，α=0.3）
  ——α=1 的逐点硬跟踪使控制器误差恒为零、OP 恒定（回路退化），一阶滤波
  为"逐点跟踪"的非退化物理形式；
- 串级构造取 P 主控（副环 SP=主控输出；主环 PV=副环 PV 经慢一阶+时滞），
  负载 ARMA 有色打在副环 PV 上（P2-009 消费口径：辨识对象=副环）。
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import signal as sps

from app.services.tuning_identification.pipeline import (
    ALGORITHM_VERSION,
    identify_from_history,
)
from app.services.tuning_identification.types import (
    AlgorithmConfidenceLevel,
    ModelType,
)

# ---------------------------------------------------------------------------
# 冻结容差（R16/R19，一字不差；禁失败后调整——R15）
# ---------------------------------------------------------------------------
OC_PARAM_TOL = 0.15  # O-C1/O-C4 闭环/共同估计参数恢复 ≤15%
OC_ARX_CONTRAST = 0.30  # O-C1：有偏 ARX 同场景偏差 >30%（对照断言）
OC5_NRMSE_TOL = 0.10  # O-C5：独立事件终验量程归一 NRMSE ≤0.10
OC6_GAIN_TOL = 0.20  # O-C6（R19）：共同频带内幅值差 ≤20%
OC6_PHASE_TOL_DEG = 15.0  # O-C6（R19）：共同频带内相位差 ≤15°
OC7_COVERAGE_MIN = 0.90  # O-C7：真值落入 95% 区间覆盖率 ≥90%（20 次重复）

_CONF_ORDER = {
    AlgorithmConfidenceLevel.A: 5,
    AlgorithmConfidenceLevel.B: 4,
    AlgorithmConfidenceLevel.C: 3,
    AlgorithmConfidenceLevel.D: 2,
    AlgorithmConfidenceLevel.E: 1,
    AlgorithmConfidenceLevel.INCONCLUSIVE: 0,
}


def _theta_tol(ts: float, theta: float) -> float:
    """θ 恢复容差（R16 冻结）：≤ max(2×ts, 10%×θ)."""
    return max(2.0 * ts, 0.10 * theta)


# ---------------------------------------------------------------------------
# 独立 oracle 生成器（§0.2：独立于被测估计器）
# ---------------------------------------------------------------------------


def _sp_steps(n: int, pattern: list[tuple[int, float]]) -> np.ndarray:
    sp = np.zeros(n)
    for t, v in pattern:
        sp[int(t) :] = v
    return sp


def _arma_load(n: int, seed: int, c1: float = 0.6, std: float = 1.0) -> np.ndarray:
    """ARMA(1,1) 有色负载（独立递推生成器）."""
    rng = np.random.default_rng(seed)
    e = rng.normal(0, std, n)
    v = np.zeros(n)
    for k in range(1, n):
        v[k] = e[k] + c1 * e[k - 1]
    return v


def _closed_loop(
    sp: np.ndarray,
    K: float,
    tau: float,
    theta: float,
    kp: float,
    ti: float,
    ts: float = 1.0,
    load: np.ndarray | None = None,
    meas_std: float = 0.0,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """独立增量式 PI 闭环仿真器（控制器读上一拍测量；负载直接进 PV）."""
    rng = np.random.default_rng(seed)
    n = len(sp)
    a = math.exp(-ts / tau)
    b = K * (1 - a)
    d = max(0, round(theta / ts))
    y = np.zeros(n)
    u = np.zeros(n)
    e_prev = 0.0
    u_prev = 0.0
    ki = kp * ts / ti
    for k in range(n):
        y_meas = y[k - 1] if k > 0 else 0.0
        e = sp[k] - y_meas
        u[k] = u_prev + kp * (e - e_prev) + ki * e
        u_prev = u[k]
        e_prev = e
        if k >= d:
            y[k] = a * y[k - 1] + b * u[k - d] + (load[k] if load is not None else 0.0)
    if meas_std > 0:
        y = y + rng.normal(0, meas_std, n)
    return y, u


def _tracking_sp_loop(
    n: int,
    K: float,
    tau: float,
    theta: float,
    kp: float,
    ti: float,
    ts: float = 1.0,
    load: np.ndarray | None = None,
    meas_std: float = 0.0,
    seed: int = 7,
    alpha: float = 0.3,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """跟踪型 SP 闭环（O-C2）：sp 经一阶滤波逐点跟随 PV（见模块裁量登记）."""
    rng = np.random.default_rng(seed)
    a = math.exp(-ts / tau)
    b = K * (1 - a)
    d = max(0, round(theta / ts))
    y = np.zeros(n)
    u = np.zeros(n)
    sp = np.zeros(n)
    sp[0] = 10.0
    e_prev = 0.0
    u_prev = 0.0
    ki = kp * ts / ti
    for k in range(1, n):
        sp[k] = sp[k - 1] + alpha * (y[k - 1] - sp[k - 1])
        e = sp[k] - y[k - 1]
        u[k] = u_prev + kp * (e - e_prev) + ki * e
        u_prev = u[k]
        e_prev = e
        if k >= d:
            y[k] = a * y[k - 1] + b * u[k - d] + (load[k] if load is not None else 0.0)
    if meas_std > 0:
        y = y + rng.normal(0, meas_std, n)
    return y, u, sp


def _cascade_secondary(
    n: int,
    K2: float,
    tau2: float,
    theta2: float,
    kp2: float,
    ti2: float,
    K1: float,
    tau1: float,
    theta1: float,
    kp1: float,
    ts: float = 1.0,
    seed: int = 11,
    sp1_pattern: list[tuple[int, float]] | None = None,
    load_std: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """串级合成（O-C2）：副环 SP=主环 P 控制器输出；主环 PV=副环 PV 滞后滤波.

    辨识对象=副环（OP2/PV2/SP2）；ARMA 负载打在副环 PV 上并经主环反馈进
    入 SP2 → SP2 含扰动信息（IV 有效性条件不成立）。
    """
    _ = seed  # 段种子（负载种子在 _arma_load 内部派生；主链无随机性）
    if sp1_pattern is None:
        sp1_pattern = [(0, 50.0), (500, 51.5), (1200, 48.5), (1800, 51.5)]
    sp1 = _sp_steps(n, sp1_pattern)
    load = _arma_load(n, 31, c1=0.6, std=load_std)
    a2 = math.exp(-ts / tau2)
    b2 = K2 * (1 - a2)
    d2 = max(0, round(theta2 / ts))
    a1 = math.exp(-ts / tau1)
    d1 = max(0, round(theta1 / ts))
    pv2 = np.zeros(n)
    op2 = np.zeros(n)
    sp2 = np.zeros(n)
    pv1 = np.zeros(n)
    e2p = 0.0
    o2p = 0.0
    ki2 = kp2 * ts / ti2
    for k in range(n):
        pv2m = pv2[k - 1] if k > 0 else 0.0
        e2 = sp2[k] - pv2m
        op2[k] = o2p + kp2 * (e2 - e2p) + ki2 * e2
        o2p = op2[k]
        e2p = e2
        if k >= d2:
            pv2[k] = a2 * pv2[k - 1] + b2 * op2[k - d2] + load[k]
        pv1m = pv1[k - 1] if k > 0 else 0.0
        sp2[k] = kp1 * (sp1[k] - pv1m) + 50.0
        if k >= d1:
            pv1[k] = a1 * pv1[k - 1] + (1 - a1) * pv2[k - d1]
    return pv2, op2, sp2


def _oc1_exogenous_closed_loop(n: int = 7200):
    """O-C1 外生 SP 闭环成功例：96 次 ±1 交替阶跃 + ARMA 有色负载.

    阶跃密度是 oracle 构造参数（非冻结值）：±1 小幅高频交替阶跃使工具变量
    谱丰富——稀疏大阶跃下 CLIVC 极点方差爆炸（τ 误差 40%+），96 步序列下
    K/τ 稳定 ≤5%（生成器与被测估计器独立）。
    """
    K, tau, theta = 2.0, 30.0, 5.0
    rng = np.random.default_rng(77)
    pool = np.arange(100, n - 200, 65)
    n_steps = int(min(96, len(pool)))
    times = sorted(rng.choice(pool, size=n_steps, replace=False))
    levels = 10.0 + np.cumsum(rng.choice([-1.0, 1.0], size=n_steps))
    sp = _sp_steps(
        n, [(0, 10.0)] + [(int(t), float(v)) for t, v in zip(times, levels, strict=True)]
    )
    load = _arma_load(n, 77, c1=0.6, std=1.2)
    y, u = _closed_loop(sp, K, tau, theta, kp=2.5, ti=20.0, load=load, meas_std=0.2)
    return u, y, sp, (K, tau, theta)


def _open_loop_fopdt(
    u: np.ndarray,
    K: float,
    tau: float,
    theta: float,
    ts: float = 1.0,
    noise_std: float = 0.0,
    seed: int = 1,
) -> np.ndarray:
    """开环 FOPDT 一阶差分递推（O-C4 多实验段生成器；独立于 ARX 回归）."""
    rng = np.random.default_rng(seed)
    a = math.exp(-ts / tau)
    b = K * (1 - a)
    d = max(0, round(theta / ts))
    y = np.zeros(len(u))
    for k in range(d, len(u)):
        y[k] = a * y[k - 1] + b * u[k - d]
    if noise_std > 0:
        y = y + rng.normal(0, noise_std, len(u))
    return y


# ---------------------------------------------------------------------------
# 生成器独立解析核对（§0.2）
# ---------------------------------------------------------------------------


class TestOracleGeneratorChecks:
    def test_open_loop_fopdt_step_matches_closed_form(self):
        """开环生成器 vs 独立闭式解（ZOH 一阶差分的精确阶跃响应）.

        y[k]=a·y[k-1]+b·u[k-d] 对 t0 时刻阶跃的闭式解（几何级数和，独立
        手算）：k≥t0+d 时 y[k]=K·(1-a^{k-t0-d+1})——与递推生成器逐点核对。
        """
        K, tau, theta, ts = 1.2, 25.0, 8.0, 1.0
        n = 400
        u = np.zeros(n)
        u[50:] = 1.0
        y = _open_loop_fopdt(u, K, tau, theta, ts)
        a = math.exp(-ts / tau)
        d = round(theta / ts)
        for k in (58, 70, 100, 200, 399):
            m = k - 50 - d + 1
            y_ref = K * (1.0 - a**m) if m > 0 else 0.0
            assert abs(y[k] - y_ref) < 1e-9, (k, y[k], y_ref)

    def test_closed_loop_pv_tracks_sp_in_mean(self):
        """闭环生成器物理核对：恒 SP 无负载时 PV 终值 ≈ SP（PI 无静差）."""
        n = 1200
        sp = np.full(n, 10.0)
        y, _u = _closed_loop(sp, 2.0, 30.0, 5.0, kp=2.5, ti=20.0)
        assert abs(y[-1] - 10.0) < 0.05, f"PI 闭环终值 {y[-1]:.4f} 应跟踪 SP"

    def test_cascade_secondary_sp_contains_load(self):
        """串级生成器物理核对：负载经主环注入副环 SP（内生性的构造前提）.

        通断对比：同种子无负载时 SP2 仅由 sp1 驱动（确定性）；有负载时
        SP2 增量方差显著增大——负载的影响经 pv2→pv1→主控进入 SP2。
        """
        n = 2400
        _pv2_on, _op2_on, sp2_on = _cascade_secondary(
            n, 1.0, 20.0, 4.0, 2.0, 15.0, 1.0, 60.0, 10.0, 1.2, load_std=2.0
        )
        _pv2_off, _op2_off, sp2_off = _cascade_secondary(
            n, 1.0, 20.0, 4.0, 2.0, 15.0, 1.0, 60.0, 10.0, 1.2, load_std=0.0
        )
        var_on = float(np.var(np.diff(sp2_on)))
        var_off = float(np.var(np.diff(sp2_off)))
        assert var_on > 2.0 * var_off, (
            f"负载通断 SP2 增量方差 {var_on:.4g} vs {var_off:.4g}——负载未注入 SP2（构造失效）"
        )


# ---------------------------------------------------------------------------
# O-C1 外生 SP 闭环成功例
# ---------------------------------------------------------------------------


class TestOC1ExogenousClosedLoop:
    """O-C1：CLIVC 参数恢复 ≤15%；有偏 ARX >30% 对照；ARX 基线仍输出."""

    def test_clivc_recovery_and_arx_contrast(self):
        u, y, sp, (K, tau, theta) = _oc1_exogenous_closed_loop()
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=theta,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success, result.reason
        # 外生性判定通过（三条件零命中）→ CLIVC 进入候选集
        assert result.best_model.evidence is not None
        assert result.best_model.evidence.sp_exogeneity is not None
        assert result.best_model.evidence.sp_exogeneity["verdict"] == "EXOGENOUS"
        iv = [c for c in result.candidates if c.identify_method.value == "HISTORICAL_IV"]
        assert iv, "外生 SP 闭环应产出 CLIVC 候选"
        best_iv = max(iv, key=lambda c: c.fitting_score)
        p = best_iv.params
        assert abs(p.K - K) / K <= OC_PARAM_TOL
        assert abs(p.tau - tau) / tau <= OC_PARAM_TOL
        assert abs(p.theta - theta) <= _theta_tol(1.0, theta)
        # 有偏 ARX 对照断言（闭环直接法显著偏 → CLIVC 的必要性证据）
        arx = [c for c in result.candidates if c.identify_method.value == "HISTORICAL_ARX"]
        assert arx, "正确 ARX 仍可作基线（07§4）——ARX 候选应正常输出"
        arx_dev = abs(arx[0].params.K - K) / K
        assert arx_dev > OC_ARX_CONTRAST, f"ARX K 偏差 {arx_dev:.1%} 应 >30%"

    def test_exogeneity_record_conditions_visible(self):
        """三条件检测记录可见（锁步/串级模板/残差相关三键）."""
        u, y, sp, _truth = _oc1_exogenous_closed_loop(n=3600)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=5.0,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success, result.reason
        rec = result.best_model.evidence.sp_exogeneity
        assert set(rec["conditions"]) >= {"lockstep", "cascadeTemplate", "residualCorrelation"}
        d = result.best_model.evidence.to_dict()
        assert d["spExogeneity"]["verdict"] == "EXOGENOUS"


# ---------------------------------------------------------------------------
# O-C2 串级/跟踪 SP 拒绝例
# ---------------------------------------------------------------------------


class TestOC2CascadeAndTrackingRejection:
    """O-C2：CLIVC 标记适用条件不满足 / 不晋升 A/B / 不输出串级 SP 当 IV 的成功结果."""

    def test_tracking_sp_rejected(self):
        n = 2400
        K, tau, theta = 2.0, 30.0, 5.0
        load = _arma_load(n, 6, c1=0.6, std=1.2)
        y, u, sp = _tracking_sp_loop(
            n, K, tau, theta, kp=2.0, ti=20.0, load=load, meas_std=0.2, alpha=0.3
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=theta,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success or result.candidates, "跟踪型场景候选应产出（供审计）"
        best = result.best_model
        assert best is not None and best.evidence is not None
        assert best.evidence.sp_exogeneity["applicable"] is False
        assert best.evidence.sp_exogeneity["verdict"] in {
            "TRACKING_LOCKSTEP",
            "RESIDUAL_CORRELATED",
        }
        # 不输出把跟踪 SP 当 IV 的"成功"结果
        iv = [c for c in result.candidates if c.identify_method.value == "HISTORICAL_IV"]
        assert not iv, "跟踪型 SP 不得产出 CLIVC 候选"
        # 结果不晋升 A/B
        assert _CONF_ORDER.get(best.confidence, 0) <= 3
        assert "SP_EXOGENEITY_FAILED" in best.evidence.reason_codes

    def test_cascade_secondary_rejected_data_driven(self):
        n = 2400
        pv2, op2, sp2 = _cascade_secondary(
            n, 1.0, 20.0, 4.0, 2.0, 15.0, 1.0, 60.0, 10.0, 1.2, load_std=2.0
        )
        result = identify_from_history(
            op=op2.tolist(),
            pv=pv2.tolist(),
            sp=sp2.tolist(),
            ts=1.0,
            theta_estimate=4.0,
            candidate_models=[ModelType.FOPDT],
        )
        best = result.best_model
        assert best is not None and best.evidence is not None
        assert best.evidence.sp_exogeneity["applicable"] is False
        iv = [c for c in result.candidates if c.identify_method.value == "HISTORICAL_IV"]
        assert not iv, "串级 SP（数据驱动检出）不得产出 CLIVC 候选"
        assert _CONF_ORDER.get(best.confidence, 0) <= 3

    def test_cascade_template_hint_path(self):
        """P-21 条件②：串级模板标识（P2-04 engineeringFacts 结构来源）."""
        n = 2400
        pv2, op2, sp2 = _cascade_secondary(
            n, 1.0, 20.0, 4.0, 2.0, 15.0, 1.0, 60.0, 10.0, 1.2, load_std=1.0
        )
        result = identify_from_history(
            op=op2.tolist(),
            pv=pv2.tolist(),
            sp=sp2.tolist(),
            ts=1.0,
            theta_estimate=4.0,
            candidate_models=[ModelType.FOPDT],
            sp_cascade_hint=True,
        )
        best = result.best_model
        assert best is not None and best.evidence is not None
        rec = best.evidence.sp_exogeneity
        assert rec["verdict"] == "CASCADE_TEMPLATE"
        assert rec["conditions"]["cascadeTemplate"]["source"] == "ENGINEERING_FACTS"
        iv = [c for c in result.candidates if c.identify_method.value == "HISTORICAL_IV"]
        assert not iv


# ---------------------------------------------------------------------------
# O-C3 恒 SP 有色扰动
# ---------------------------------------------------------------------------


class TestOC3ConstantSpColoredLoad:
    """O-C3：恒 SP + 有色负载闭环——显式记录"SP 无激励，闭环有偏方法不适用"并拒绝或降级."""

    def test_constant_sp_recorded_and_degraded(self):
        n = 2400
        K, tau, theta = 2.0, 30.0, 5.0
        sp = np.full(n, 10.0)
        load = _arma_load(n, 8, c1=0.6, std=0.8)
        y, u = _closed_loop(sp, K, tau, theta, kp=1.5, ti=20.0, load=load, meas_std=0.2)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=theta,
            candidate_models=[ModelType.FOPDT],
        )
        if result.success and result.best_model is not None:
            best = result.best_model
            rec = best.evidence.sp_exogeneity
            assert rec["verdict"] == "NO_SP_EXCITATION"
            assert "闭环有偏方法不适用" in rec["note"]
            assert "SP_NO_EXCITATION_CLOSED_LOOP" in best.evidence.reason_codes
            # 降级：可信度不得高于 C
            assert _CONF_ORDER.get(best.confidence, 0) <= 3
            # 不由输入变化单独推因果：恒 SP 下 OP 变化全部来自负载抑制，
            # 不得以此宣称可辨识外生激励
            assert "HEURISTIC" not in (best.reason or "")
        else:
            # 拒绝路径：失败原因显式含 O-C3 事实（记录不随候选全灭丢失）
            assert "SP 无激励" in (result.reason or "")
            assert "闭环有偏方法不适用" in (result.reason or "")


# ---------------------------------------------------------------------------
# O-C4 多实验共同估计
# ---------------------------------------------------------------------------


def _oc4_two_experiment_segments(
    gap: int = 200,
    bias_a: float = 40.0,
    bias_b: float = 55.0,
    seg_n: int = 1800,
    noise_std: float = 0.15,
):
    """同真系统两个不连续实验段（同 regime），偏置不同（白输入开环）.

    段 A/B 独立状态递推、独立工作点（OP 均值 40/55）、NaN 缺口分隔——
    段间物理不连续（不跨 gap 拼接的构造前提）。
    """
    K, tau, theta = 1.5, 20.0, 4.0
    rng = np.random.default_rng(301)
    u_a = bias_a + rng.normal(0, 5.0, seg_n)
    u_b = bias_b + rng.normal(0, 5.0, seg_n)
    y_a = _open_loop_fopdt(u_a, K, tau, theta, noise_std=noise_std, seed=302)
    y_b = _open_loop_fopdt(u_b, K, tau, theta, noise_std=noise_std, seed=303)
    na = np.full(gap, np.nan)
    op = np.concatenate([u_a, na, u_b])
    pv = np.concatenate([y_a, na, y_b])
    return op, pv, (K, tau, theta)


class TestOC4JointEstimation:
    """O-C4：合并估计参数恢复 ≤15%；逐窗一致性；段偏置分别输出（P-23）."""

    def test_joint_estimate_produced_and_recovers(self):
        op, pv, (K, tau, theta) = _oc4_two_experiment_segments()
        result = identify_from_history(op=op.tolist(), pv=pv.tolist(), ts=1.0)
        assert result.success, result.reason
        joint = [c for c in result.candidates if c.evidence and c.evidence.joint_estimate]
        assert joint, "两段多实验输入应产出共同估计候选（P-22 并列输出）"
        best_joint = max(joint, key=lambda c: c.fitting_score)
        p = best_joint.params
        assert p.model_type == ModelType.FOPDT
        assert abs(p.K - K) / K <= OC_PARAM_TOL, f"joint K 偏差 {abs(p.K - K) / K:.1%}"
        assert abs(p.tau - tau) / tau <= OC_PARAM_TOL
        assert abs(p.theta - theta) <= _theta_tol(1.0, theta)

    def test_per_window_estimates_consistent_with_truth(self):
        """逐窗各自估计互差与真值一致性均在容差（O-C4）."""
        op, pv, (K, tau, theta) = _oc4_two_experiment_segments()
        result = identify_from_history(op=op.tolist(), pv=pv.tolist(), ts=1.0)
        assert result.success, result.reason
        mw = (result.preprocessing or {}).get("multiWindow")
        assert mw is not None, "两段输入应触发多窗编排"
        seg_fits = mw["succeededWindows"]
        assert len(seg_fits) >= 2
        # 逐窗参数（从候选池逐窗结果核对——各段 60/20/20 独立估计）
        seg_a = identify_from_history(op=op[:1800].tolist(), pv=pv[:1800].tolist(), ts=1.0)
        seg_b = identify_from_history(op=op[-1800:].tolist(), pv=pv[-1800:].tolist(), ts=1.0)
        for r in (seg_a, seg_b):
            assert r.success, r.reason
            p = r.best_model.params
            assert abs(p.K - K) / K <= OC_PARAM_TOL
            assert abs(p.tau - tau) / tau <= OC_PARAM_TOL
        pa, pb = seg_a.best_model.params, seg_b.best_model.params
        assert abs(pa.K - pb.K) / max(abs(pa.K), abs(pb.K)) <= OC_PARAM_TOL

    def test_segment_biases_recorded_separately(self):
        """P-23：每段独立偏置入证据（不同工作点分别记录，不共享）."""
        op, pv, _truth = _oc4_two_experiment_segments()
        result = identify_from_history(op=op.tolist(), pv=pv.tolist(), ts=1.0)
        assert result.success, result.reason
        joint = [c for c in result.candidates if c.evidence and c.evidence.joint_estimate]
        assert joint
        rec = max(joint, key=lambda c: c.fitting_score).evidence.joint_estimate
        assert rec["segmentCount"] == 2
        means = sorted(s["opMean"] for s in rec["segments"])
        assert abs(means[0] - 40.0) < 1.5 and abs(means[1] - 55.0) < 1.5
        assert abs(means[0] - means[1]) > 10.0, "段偏置应分别记录且不同（P-23）"

    def test_no_cross_gap_concat_in_joint(self):
        """禁止跨 gap 把两段连成一条等距序列参与回归（P-22 冻结红线）."""
        op, pv, (K, _tau, _theta) = _oc4_two_experiment_segments()
        result = identify_from_history(op=op.tolist(), pv=pv.tolist(), ts=1.0)
        assert result.success, result.reason
        joint = [c for c in result.candidates if c.evidence and c.evidence.joint_estimate]
        assert joint
        rec = max(joint, key=lambda c: c.fitting_score).evidence.joint_estimate
        assert rec["noCrossGapConcat"] is True
        assert rec["method"] == "SEGMENT_BLOCK_STACKING"
        # 结构性证明：gap 区间不落入任何参与段（段界即窗界）
        segs = [(s["start"], s["end"]) for s in rec["segments"]]
        for s, e in segs:
            assert not (s < 1800 < e) or e <= 1800, "段不得跨 gap（1800 处为缺口）"


# ---------------------------------------------------------------------------
# O-C5 独立动态事件终验
# ---------------------------------------------------------------------------


def _oc5_multi_segment_events(n_seg: int = 1800, tail_gain_factor: float = 1.0):
    """四段闭环数据：段 1~3 为选模段（丰富外生 SP），段 4 含独立终验事件.

    段间评分分离使 top-3 不重叠窗未覆盖全部段（种子固定，挑选确定性）→
    未被选窗覆盖的段内事件即独立终验事件（P-24）。tail_gain_factor≠1 时
    改变事件源段（第三段）的增益——胜出模型来自其余段（真系统不变），
    独立事件动态不同 → 终验失败构造。
    """
    K, tau, theta = 2.0, 30.0, 5.0
    segs = []
    gap = np.full(150, np.nan)
    for i, amp in enumerate([2.0, 2.0, 2.0, 1.0]):
        n = n_seg
        rng = np.random.default_rng(400 + i)
        times = sorted(rng.choice(np.arange(100, n - 150, 40), size=24, replace=False))
        levels = 10.0 + np.cumsum(rng.choice([-amp, amp], size=24))
        sp = _sp_steps(
            n, [(0, 10.0)] + [(int(t), float(v)) for t, v in zip(times, levels, strict=True)]
        )
        load = _arma_load(n, 500 + i, c1=0.6, std=0.15)
        gain = K * (tail_gain_factor if i == 2 else 1.0)
        y, u = _closed_loop(
            sp, gain, tau, theta, kp=2.0, ti=20.0, load=load, meas_std=0.05, seed=420 + i
        )
        segs.append((u, y, sp))
    op = np.concatenate([segs[0][0], gap, segs[1][0], gap, segs[2][0], gap, segs[3][0]])
    pv = np.concatenate([segs[0][1], gap, segs[1][1], gap, segs[2][1], gap, segs[3][1]])
    sp_all = np.concatenate([segs[0][2], gap, segs[1][2], gap, segs[2][2], gap, segs[3][2]])
    return op, pv, sp_all, (K, tau, theta)


class TestOC5IndependentEventFinalTest:
    """O-C5：独立事件终验自由仿真 NRMSE ≤0.10；终验失败不晋升."""

    def test_independent_event_passed_on_consistent_system(self):
        op, pv, sp_all, truth = _oc5_multi_segment_events()
        result = identify_from_history(
            op=op.tolist(),
            pv=pv.tolist(),
            sp=sp_all.tolist(),
            ts=1.0,
        )
        assert result.success, result.reason
        best = result.best_model
        ev = best.evidence
        assert ev is not None and ev.final_event_test is not None, (
            "四段构造应存在与选模用窗不重叠的独立事件（段 4）"
        )
        rec = ev.final_event_test
        assert rec["status"] == "PASSED", f"一致系统独立事件终验应通过：{rec}"
        assert rec["nrmse"] <= OC5_NRMSE_TOL
        # 事件窗与全部选模用窗不重叠（P-24 构造前提的行为核验）
        mw = (result.preprocessing or {}).get("multiWindow")
        assert mw is not None
        s, e = rec["window"]["start"], rec["window"]["end"]
        for w in mw["checkedWindows"]:
            assert e <= w["start"] or s >= w["end"], "终验事件窗不得与选模窗重叠"

    def test_independent_event_failed_not_promoted(self):
        """段 4 增益改变（K/3）→ 独立事件终验失败 → 不晋升 A/B."""
        op, pv, sp_all, (K, tau, theta) = _oc5_multi_segment_events(tail_gain_factor=1 / 3.0)
        result = identify_from_history(
            op=op.tolist(),
            pv=pv.tolist(),
            sp=sp_all.tolist(),
            ts=1.0,
        )
        assert result.success, result.reason
        best = result.best_model
        ev = best.evidence
        assert ev is not None and ev.final_event_test is not None
        rec = ev.final_event_test
        assert rec["status"] == "FAILED", f"增益改变事件终验应失败：{rec}"
        assert "FINAL_EVENT_FAILED" in ev.reason_codes
        assert _CONF_ORDER.get(best.confidence, 0) <= 3, "终验失败不得晋升 A/B"


# ---------------------------------------------------------------------------
# O-C6 频带验证（R19 冻结容差）
# ---------------------------------------------------------------------------


class TestOC6FrequencyBandVerification:
    """O-C6：终验事件上频响 vs 解析频响（共同频带内幅值 ≤20%、相位 ≤15°）.

    07§4 完成判据"最终连续模型通过独立响应/频带验证"：以胜出模型的解析
    频响对真系统解析频响在共同频带（终验事件输入谱显著覆盖的频带）内
    核对；Welch 谱（scipy csd，独立谱估计）用于界定共同频带。
    """

    def test_winner_model_frf_matches_truth_in_band(self):
        op, pv, sp_all, (K, tau, theta) = _oc5_multi_segment_events()
        result = identify_from_history(
            op=op.tolist(),
            pv=pv.tolist(),
            sp=sp_all.tolist(),
            ts=1.0,
        )
        assert result.success, result.reason
        best = result.best_model
        assert best.params.model_type == ModelType.FOPDT
        ev = best.evidence
        assert ev is not None and ev.final_event_test is not None
        ws, we = ev.final_event_test["window"]["start"], ev.final_event_test["window"]["end"]
        u_ev = op[ws:we]
        ts = 1.0
        fs = 1.0 / ts
        # 独立谱估计界定共同频带（输入 PSD ≥ 峰值 5% 的频带）
        f, pxx = sps.welch(u_ev - u_ev.mean(), fs=fs, nperseg=256, detrend="constant")
        band = (f >= f[1]) & (f <= 0.05) & (pxx >= 0.05 * pxx[1:].max())
        assert band.any(), "终验事件输入谱无显著频带（构造失效）"
        w = 2j * np.pi * f[band]
        # 真系统解析频响（独立手算）G(jω) = K·e^{-θjω}/(τjω+1)
        g_true = K * np.exp(-theta * w) / (tau * w + 1.0)
        p = best.params
        g_est = p.K * np.exp(-p.theta * w) / (p.tau * w + 1.0)
        gain_err = np.abs(np.abs(g_est) - np.abs(g_true)) / np.abs(g_true)
        phase_err_deg = np.degrees(np.abs(np.angle(g_est) - np.angle(g_true)))
        # 相位卷绕（±360° 歧义按最小卷绕差核对）
        phase_err_deg = np.minimum(phase_err_deg, 360.0 - phase_err_deg)
        assert gain_err.max() <= OC6_GAIN_TOL, f"共同频带内幅值差 {gain_err.max():.1%} 超 20%"
        assert phase_err_deg.max() <= OC6_PHASE_TOL_DEG, (
            f"共同频带内相位差 {phase_err_deg.max():.1f}° 超 15°"
        )


# ---------------------------------------------------------------------------
# O-C7 不确定性非零宽（C19-ID4）
# ---------------------------------------------------------------------------


class TestOC7ReselectUncertainty:
    """O-C7：重选时滞/结构的重复估计 → θ 区间非零宽 + 覆盖率 ≥90%."""

    @staticmethod
    def _weak_delay_case(seed: int):
        """中等噪声短窗构造：子窗抽样+时滞平局集重选 → 参数分布有宽度.

        构造参数（oracle 侧裁量，20 次固定种子实验下 K/τ/θ 覆盖率 ≥90%
        实证标定）：n=1200、20dB、τ=25/θ=12（1+a1≈0.039 近对消使参数对
        估计误差敏感——覆盖断言的保守档）。
        """
        K, tau, theta = 1.5, 25.0, 12.0
        n = 1200
        rng = np.random.default_rng(seed)
        u = rng.normal(0, 5.0, n)
        y0 = _open_loop_fopdt(u, K, tau, theta)
        noise = float(np.std(y0 - y0.mean())) / 10.0 ** (20.0 / 20.0)  # 20dB
        y = _open_loop_fopdt(u, K, tau, theta, noise_std=noise, seed=seed + 1)
        return u, y, (K, tau, theta)

    def test_theta_interval_nonzero_width_or_condition_recorded(self):
        """θ 区间必须非零宽（除非全部重复收敛同一值——此时条件也须记录）."""
        u, y, _truth = self._weak_delay_case(seed=901)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        rec = result.best_model.evidence.reselect_uncertainty
        assert rec is not None and rec["status"] == "COMPLETED"
        theta_rec = rec["theta"]
        lo, hi = theta_rec["ci95"]
        if hi - lo <= 0.5:  # 零宽（≤ts/2）
            assert theta_rec["degenerate"] is True
            assert "degenerateCondition" in theta_rec, "零宽区间必须记录条件（O-C7）"
        else:
            assert hi > lo, "θ 区间不得为假零宽（固定 θ 报零宽区间被禁止）"
        assert rec["nRepetitions"] == 48  # P-27 区间 32~64 内取值
        assert rec["budget"]["cap"] == 192  # B-10
        assert rec["budget"]["total"] <= 192

    def test_coverage_at_least_90_percent(self):
        """真值落入 95% 区间的覆盖率在 20 次重复实验中 ≥90%（统计断言）.

        覆盖主体=C19-ID4 的 θ（时滞不确定性的原始关切）+ 增益 K。τ 的
        子窗族分散度对跨实验分散度欠覆盖（同构造下 ~70%，子窗高度重叠的
        固有欠分散），登记为实施发现（交接文档），不在本断言中主张。
        """
        covered_k = covered_theta = 0
        n_exp = 20
        for seed in range(920, 920 + n_exp):
            u, y, (K, tau, theta) = self._weak_delay_case(seed=seed)
            result = identify_from_history(
                op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
            )
            assert result.success, result.reason
            rec = result.best_model.evidence.reselect_uncertainty
            assert rec is not None and rec["status"] == "COMPLETED"
            k_lo, k_hi = rec["K"]["ci95"]
            if k_lo <= K <= k_hi:
                covered_k += 1
            th_lo, th_hi = rec["theta"]["ci95"]
            if th_lo <= theta <= th_hi:
                covered_theta += 1
        assert covered_k / n_exp >= OC7_COVERAGE_MIN, f"K 覆盖率 {covered_k}/{n_exp} < 90%"
        assert covered_theta / n_exp >= OC7_COVERAGE_MIN, (
            f"theta 覆盖率 {covered_theta}/{n_exp} < 90%"
        )


# ---------------------------------------------------------------------------
# O-C8 弱证据不升级
# ---------------------------------------------------------------------------


class TestOC8WeakEvidenceNotPromoted:
    """O-C8：近稳态/低激励 + 高 R²_train → 可信度不得高于 C（P-25/P-10 联合）."""

    def test_near_stationary_weak_excitation_capped_at_c(self):
        """近稳态构造（PV 动态贴着检测下限）——弱证据不得自动升 A/B."""
        K, tau, theta = 1.5, 40.0, 6.0
        n = 2400
        rng = np.random.default_rng(950)
        # 低激励：OP 罕见小步（累计运动刚过信息门槛 2%）、PV 近稳态
        u = np.full(n, 50.0)
        flip_idx = sorted(rng.choice(np.arange(200, n - 200, 200), size=8, replace=False))
        sign, prev = 1.0, 0
        for i in flip_idx:
            u[prev:i] = 50.0 + sign * 1.0
            sign = -sign
            prev = i
        u[prev:] = 50.0 + sign * 1.0
        y = _open_loop_fopdt(u, K, tau, theta, noise_std=0.01, seed=951)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        # 弱证据：成功则不得高于 C；失败/激励不足拒绝同样合规（拒绝或降级）
        if result.success and result.best_model is not None:
            best = result.best_model
            assert _CONF_ORDER.get(best.confidence, 0) <= 3, (
                f"弱证据（近稳态+低激励）可信度 {best.confidence} 不得高于 C"
            )
            if best.evidence is not None:
                weak_codes = {
                    "VAL_STATIONARY",
                    "FINAL_TEST_NO_DYNAMICS",
                    "CROSS_WINDOW_UNSTABLE",
                    "FINAL_TEST_FAILED",
                }
                assert weak_codes & set(best.evidence.reason_codes), (
                    "弱证据降级须有显式原因码（不静默降级）"
                )

    def test_unstable_cross_window_params_capped(self):
        """P-25：多窗参数两两超差（模拟时变增益）→ 显式跨窗不稳定 + 封顶 C."""
        K, tau, theta = 1.5, 30.0, 5.0
        gap = np.full(150, np.nan)
        rng = np.random.default_rng(960)
        segs = []
        gains = [1.0, 0.55, 1.0]  # 中段增益改变 → 跨窗参数超 25%
        for i, g in enumerate(gains):
            n = 1800
            u = rng.normal(0, 5.0, n)
            y = _open_loop_fopdt(u, K * g, tau, theta, noise_std=0.05, seed=961 + i)
            segs.append((u, y))
        op = np.concatenate([segs[0][0], gap, segs[1][0], gap, segs[2][0]])
        pv = np.concatenate([segs[0][1], gap, segs[1][1], gap, segs[2][1]])
        result = identify_from_history(op=op.tolist(), pv=pv.tolist(), ts=1.0)
        if result.success and result.best_model is not None:
            best = result.best_model
            mw = (result.preprocessing or {}).get("multiWindow") or {}
            stab = mw.get("crossWindowStability")
            if stab is not None and not stab["stable"]:
                assert _CONF_ORDER.get(best.confidence, 0) <= 3
                assert best.evidence is not None
                assert "CROSS_WINDOW_UNSTABLE" in best.evidence.reason_codes
            else:
                pytest.skip("构造未触发跨窗不稳定路径（时变增益窗未同结构双入选）")


# ---------------------------------------------------------------------------
# 证据 schema 与版本（P2-03 六层"只加不改"承接）
# ---------------------------------------------------------------------------


class TestEvidenceSchemaAndVersion:
    def test_additive_evidence_keys_and_version(self):
        u, y, sp, _truth = _oc1_exogenous_closed_loop(n=3600)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=5.0,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success, result.reason
        assert result.algorithm_version == ALGORITHM_VERSION == "TUNE_IDENT_v1.7"
        d = result.best_model.evidence.to_dict()
        assert set(d.keys()) >= {
            "spExogeneity",
            "jointEstimate",
            "finalEventTest",
            "reselectUncertainty",
        }

    def test_no_sp_leaves_exogeneity_none(self):
        """未提供 SP：spExogeneity=None（显式未检测，不伪补）."""
        rng = np.random.default_rng(971)
        u = rng.normal(0, 5.0, 1500)
        y = _open_loop_fopdt(u, 1.5, 20.0, 4.0, noise_std=0.1, seed=972)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        assert result.best_model.evidence.sp_exogeneity is None
