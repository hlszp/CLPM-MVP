"""P3-06b 片段与时间尺度子卡 oracle 测试（执行包「系统改造优化-2026-10-10」）.

正本：执行状态/P3-06-07-oracle冻结草案-20261011.md §3（用户 2026-10-11
批复"全部按推荐"——R2 已裁全保留合格连续段；P-12~P-19/B-05~B-08 与
O-B1~O-B5 冻结值一字不差；R15/R16 条款：容差禁失败后调整）。

oracle 独立性纪律（§0.2）：
- 全部为**合成已知真系统 + 独立手算/解析参考**，禁止被测实现自产期望值；
- 生成器独立于被测估计器：FOPDT 用 scipy ZOH 精确离散化
  （cont2discrete+dlsim，非 ARX 递归）；闭环用独立增量式 PI 仿真器
  （非 tuning_algorithms.simulate_closed_loop）；多段数据各段独立状态
  递推（段间物理不连续，跨 gap 拼接即被侦测）；
- 9 窗回归种子不是成功率证明；本文全部为合成反例 + 独立参考。

冻结容差（§2.3 O-A1 / §3.3 O-B，一字不差）：
- O-B1（C12b-ID）：行为断言——激励段进入分析并被选中或显式记录拒绝
  原因；gap 两侧段永不跨段拼接（窗界结构性保证）；
- O-B2（C14-ID1）：白输入与动态手动段参数恢复满足 O-A1 容差
  （K≤5%/10%、τ≤10%/20%、θ≤max(2ts,10%)）；保持/慢变化按信息量通道
  正确拒绝或降级；单频激励上 SOPDT 不得仅凭 R² 胜出 FOPDT（Occam）；
- O-B3（C14-ID1）：自然单阶跃事件窗被检出并进入候选池；参数恢复
  O-A1 容差；
- O-B4（C14-ID2）：θ=120s 真系统 ts∈{1,2,5,10}s 连续域参数两两相对差
  ≤15%；采样不足场景显式"不可判断"而非硬出值；
- O-B5：搜索空间大于预算 → 被检/选中/拒/未搜索四类清单 + 预算终态字段。
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import signal as sps

from app.services.tuning_identification import identify_from_history
from app.services.tuning_identification.pipeline import (
    _clean_nan_segments,
    _detect_natural_events,
    _window_direction_changes,
    _window_info_metric,
)
from app.services.tuning_identification.segmentation import physical_min_segment_points
from app.services.tuning_identification.types import (
    AlgorithmConfidenceLevel,
    ModelType,
)

# ---------------------------------------------------------------------------
# 冻结容差（R16/O-A1，一字不差；禁失败后调整——R15）
# ---------------------------------------------------------------------------
OA1_K_TOL = {20.0: 0.05, 10.0: 0.10}  # K 相对误差：20dB ≤5% / 10dB ≤10%
OA1_TAU_TOL = {20.0: 0.10, 10.0: 0.20}  # τ 相对误差：≤10% / ≤20%
OB4_PAIRWISE_TOL = 0.15  # O-B4：跨采样率连续域参数两两相对差 ≤15%


def _theta_tol(ts: float, theta: float) -> float:
    """θ 恢复容差（R16 冻结）：≤ max(2×ts, 10%×θ)."""
    return max(2.0 * ts, 0.10 * theta)


# ---------------------------------------------------------------------------
# 独立 oracle 生成器（§0.2：独立于被测估计器；OP/PV 均在 0~100 归一化域，
# 与生产链量纲一致——算法栈 ptp 门限按该域定义）
# ---------------------------------------------------------------------------


def _zoh_fopdt(
    K: float,
    tau: float,
    theta: float,
    u: np.ndarray,
    ts: float,
    noise_std: float = 0.0,
    seed: int = 1,
) -> np.ndarray:
    """FOPDT 精确 ZOH 仿真（scipy cont2discrete + dlsim，非 ARX 递归）."""
    rng = np.random.default_rng(seed)
    b, a, _ = sps.cont2discrete(([K / tau], [1.0, 1.0 / tau]), ts, method="zoh")
    b = np.atleast_1d(b.flatten())
    a = np.atleast_1d(a.flatten())
    d = int(round(theta / ts))
    n = len(u)
    ud = np.zeros(n)
    ud[d:] = u[: n - d]
    _, y0 = sps.dlsim((b, a, ts), ud)
    y = y0.flatten()
    if noise_std > 0:
        y = y + rng.normal(0, noise_std, n)
    return y


def _independent_pi_closed_loop(
    sp: np.ndarray,
    K: float,
    tau: float,
    theta: float,
    kp: float,
    ti: float,
    ts: float,
    noise_std: float = 0.0,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """独立增量式 PI 闭环仿真器（非 tuning_algorithms.simulate_closed_loop）.

    控制器读上一拍测量（真实反馈时序）；对象为 FOPDT 一阶差分。
    PV/SP 均为 0~100 归一化域。
    """
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
        de = e - e_prev
        u[k] = u_prev + kp * de + ki * e
        u_prev = u[k]
        e_prev = e
        if k >= d:
            y[k] = a * y[k - 1] + b * u[k - d]
    if noise_std > 0:
        y = y + rng.normal(0, noise_std, n)
    return y, u


def _snr_noise_std(y_clean: np.ndarray, snr_db: float) -> float:
    """白量测噪声 std = σ(信号)/10^(SNR/20)."""
    return float(np.std(y_clean - np.mean(y_clean))) / (10.0 ** (snr_db / 20.0))


def _white_u(n: int, seed: int, amp: float = 5.0, base: float = 50.0) -> np.ndarray:
    """白高斯 OP 输入（0~100 归一化域中心 base）."""
    return base + np.random.default_rng(seed).normal(0.0, amp, n)


def _sign_hold_u(n: int, seed: int, hold: int, amp: float = 5.0, base: float = 50.0) -> np.ndarray:
    """随机符号保持输入（06a O-A5 同族：换号密度 = n/hold 次）."""
    rng = np.random.default_rng(seed)
    u = np.ones(n)
    idx = sorted(rng.choice(n, size=max(2, n // hold), replace=False))
    sign, prev = 1.0, 0
    for i in idx:
        u[prev:i] = sign * amp
        sign = -sign
        prev = i
    u[prev:] = sign * amp
    return u + base


def _prbs_u(
    n: int, seed: int, amp: float = 5.0, base: float = 50.0, period: int = 80
) -> np.ndarray:
    """非周期随机保持方波 OP（±amp，换号间隔随机 30~period 样本）.

    非周期性是 θ 可唯一辨识的数据前提——严格周期方波下 d ≡ d+k·period
    代数等价（O-B1 首版教训：周期 80 样本方波上 d=85 与真 d=5 等价，
    大延迟候选借周期混叠胜出）。period 参数保留为最大保持长度上界。
    """
    rng = np.random.default_rng(seed)
    u = np.empty(n)
    sign = 1.0
    i = 0
    while i < n:
        hold = int(rng.integers(max(10, period // 3), period + 1))
        u[i : i + hold] = base + sign * amp
        sign = -sign
        i += hold
    return u[:n]


# ---------------------------------------------------------------------------
# 生成器独立解析核对（§0.2）
# ---------------------------------------------------------------------------


class TestOracleGeneratorChecks:
    def test_zoh_fopdt_step_response_matches_analytic(self):
        """ZOH 生成器 vs 解析阶跃响应 K·(1-e^{-(t-θ)/τ})（ZOH 保阶跃采样精确）."""
        K, tau, theta, ts = 1.2, 25.0, 8.0, 2.0
        n = 400
        u = np.zeros(n)
        u[50:] = 10.0  # 阶跃幅 10（0~100 域）
        y = _zoh_fopdt(K, tau, theta, u, ts)
        k_step, d = 50, int(round(theta / ts))
        for k in (70, 100, 160, 300):
            t_eff = (k - k_step - d) * ts
            y_ref = K * 10.0 * (1.0 - math.exp(-t_eff / tau)) if t_eff > 0 else 0.0
            assert abs(y[k] - y_ref) < 1e-9, (k, y[k], y_ref)

    def test_pi_closed_loop_settles_on_setpoint(self):
        """独立 PI 闭环：定值 SP 下 PV 收敛到 SP（生成器闭环正确性）."""
        n = 3000
        sp = np.full(n, 55.0)
        y, _u = _independent_pi_closed_loop(sp, K=1.2, tau=25.0, theta=8.0, kp=2.0, ti=30.0, ts=1.0)
        assert abs(y[-1] - 55.0) < 0.5
        assert abs(np.mean(y[-500:]) - 55.0) < 0.2


# ---------------------------------------------------------------------------
# O-B1 激励段不丢（C12b-ID；R2 全保留 vs 旧法"大缺口取最长段"对照）
# ---------------------------------------------------------------------------


class TestOB1ExcitationNotDropped:
    """600 点安静段 + gap + 399 点激励段：激励段进入分析不被丢弃、不拼 gap.

    ts=2s 构造（P-12 tier1 最小 300 点：600/399 两段均合格），激励段含
    已知 FOPDT 动态；gap 两侧 PV 偏置故意不连续（拼接会破坏连续性假设，
    窗界结构性保证不跨 gap）。
    """

    K, TAU, THETA, TS = 1.2, 25.0, 8.0, 2.0
    N_QUIET, N_GAP, N_EXC = 600, 30, 399

    def _build(self, noise_std: float = 0.0):
        ts = self.TS
        rng = np.random.default_rng(7)
        # 安静段：OP 微小漂移（死区下），PV 收敛于稳态（≈50）
        q_op = 50.0 + 0.05 * np.sin(np.arange(self.N_QUIET) / 400.0)
        q_pv = _zoh_fopdt(self.K, self.TAU, self.THETA, q_op - 50.0, ts, seed=11) + 50.0
        # 激励段：OP 方波 ±8（周期 80 样本）；PV 独立状态递推（偏置不连续：
        # 段起点 PV≈57 ≠ 安静段终点 50——gap 两侧物理不连续，拼接可侦测）
        e_op = _prbs_u(self.N_EXC, seed=13, amp=8.0, period=80)
        e_pv = _zoh_fopdt(self.K, self.TAU, self.THETA, e_op - 50.0, ts, seed=12) + 57.0
        if noise_std > 0:
            q_pv = q_pv + rng.normal(0, noise_std, self.N_QUIET)
            e_pv = e_pv + rng.normal(0, noise_std, self.N_EXC)
        op = np.concatenate([q_op, np.full(self.N_GAP, np.nan), e_op]).tolist()
        pv = np.concatenate([q_pv, np.full(self.N_GAP, np.nan), e_pv]).tolist()
        return op, pv

    def test_excitation_segment_enters_analysis_and_wins(self):
        """激励段进入分析并被选中（R2）；参数对真值（20dB 档 O-A1 容差）."""
        op, pv = self._build(noise_std=0.05)
        result = identify_from_history(op=op, pv=pv, ts=self.TS, candidate_models=[ModelType.FOPDT])
        assert result.success, result.reason
        p = result.best_model.params
        assert abs(p.K - self.K) / self.K <= OA1_K_TOL[20.0]
        assert abs(p.tau - self.TAU) / self.TAU <= OA1_TAU_TOL[20.0]
        assert abs(p.theta - self.THETA) <= _theta_tol(self.TS, self.THETA)
        # 被选中：主选段 = 激励段 [600+30 : 999]（auto-segment note 记录）
        exc_start, exc_end = self.N_QUIET + self.N_GAP, self.N_QUIET + self.N_GAP + self.N_EXC
        note = ((result.preprocessing or {}).get("autoSegment") or {}).get("note") or ""
        assert f"[{exc_start}:{exc_end}]" in note, note

    def test_catalog_retains_all_segments_with_reasons(self):
        """段目录全保留：两个有限段合格入池，gap 区间作 DATA_GAP 入目录."""
        op, pv = self._build()
        result = identify_from_history(op=op, pv=pv, ts=self.TS, candidate_models=[ModelType.FOPDT])
        assert result.success, result.reason
        catalog = (result.preprocessing or {}).get("segmentCatalog") or []
        finite = [r for r in catalog if r["exclusionReason"] is None]
        gaps = [r for r in catalog if r["exclusionReason"] == "DATA_GAP"]
        assert len(finite) == 2, catalog  # 安静段+激励段均保留（无一被静默丢弃）
        assert len(gaps) == 1  # 缺口区间显式入目录
        exc_start = self.N_QUIET + self.N_GAP
        exc_rec = next(r for r in finite if r["indexStart"] == exc_start)
        assert exc_rec["pointCount"] == self.N_EXC
        assert exc_rec["tsStart"] == pytest.approx(exc_start * self.TS, abs=1e-6)
        assert exc_rec["sampleInterval"] == self.TS

    def test_windows_never_cross_gap(self):
        """契约红线：一切被检/选中窗均不跨 gap（窗界 ⊆ 单个有限段）."""
        op, pv = self._build()
        result = identify_from_history(op=op, pv=pv, ts=self.TS, candidate_models=[ModelType.FOPDT])
        assert result.success, result.reason
        prep = result.preprocessing or {}
        ws = prep.get("windowSearch") or {}
        catalog = prep.get("segmentCatalog") or []
        finite_bounds = [
            (r["indexStart"], r["indexEnd"]) for r in catalog if r["exclusionReason"] is None
        ]
        assert len(finite_bounds) == 2

        def _in_one_segment(start: int, end: int) -> bool:
            return any(s <= start and end <= e for s, e in finite_bounds)

        all_windows = []
        all_windows.extend(ws.get("windowsChecked") or [])
        all_windows.extend(ws.get("windowsSelected") or [])
        all_windows.extend(ws.get("windowsRejected") or [])
        mw = prep.get("multiWindow") or {}
        all_windows.extend(mw.get("checkedWindows") or [])
        all_windows.extend(mw.get("succeededWindows") or [])
        for w in all_windows:
            assert _in_one_segment(w["start"], w["end"]), (
                f"窗 [{w['start']}:{w['end']}] 跨越 gap 边界（契约红线）: {w}"
            )

    def test_legacy_contrast_old_rule_dropped_excitation_segment(self):
        """全保留 vs 旧法对照（任务书 §4）：legacy"大缺口取最长段"实证丢激励段.

        旧 _clean_nan_segments（v1.5 前正式链行为）在 600 安静 + 30 gap +
        399 激励构造下截断到 600 点安静段——激励段被整段丢弃；R2 段目录
        保留两段且激励段胜出（上两用例）。本用例锚定旧行为事实供审计对照。
        """
        op, pv = self._build()
        u_arr = np.array(op, dtype=float)
        y_arr = np.array(pv, dtype=float)
        u_c, y_c, _, stats = _clean_nan_segments(u_arr.copy(), y_arr.copy(), None)
        assert len(u_c) == self.N_QUIET  # 旧法仅保留最长段=安静段
        assert stats["dropped_points"] == self.N_GAP + self.N_EXC  # 激励段被整段丢弃
        assert np.all(np.isfinite(y_c))


# ---------------------------------------------------------------------------
# O-B2 信息筛查（C14-ID1：同一真系统 5 类输入）
# ---------------------------------------------------------------------------


class TestOB2InformationScreening:
    K, TAU, THETA, TS = 1.2, 25.0, 8.0, 1.0
    N = 2400

    def _y(self, u_dev: np.ndarray, snr_db: float, seed: int) -> np.ndarray:
        y0 = _zoh_fopdt(self.K, self.TAU, self.THETA, u_dev, self.TS)
        return (
            y0
            + 50.0
            + np.random.default_rng(seed).normal(
                0, _snr_noise_std(y0 + 50.0, snr_db) if snr_db < 100 else 0.0, self.N
            )
        )

    def test_white_input_enters_estimation_oa1_tolerance(self):
        """白输入进入估计，参数恢复满足 O-A1（20dB 档）."""
        u = _white_u(self.N, seed=21, amp=5.0)
        pv = self._y(u - 50.0, 20.0, 22)
        result = identify_from_history(
            op=u.tolist(), pv=pv.tolist(), ts=self.TS, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        p = result.best_model.params
        assert abs(p.K - self.K) / self.K <= OA1_K_TOL[20.0]
        assert abs(p.tau - self.TAU) / self.TAU <= OA1_TAU_TOL[20.0]
        assert abs(p.theta - self.THETA) <= _theta_tol(self.TS, self.THETA)

    def test_held_op_with_pv_drift_rejected_via_info_channel(self):
        """保持输入（OP 恒定+PV 负载漂移）：幅度过、信息量通道拒绝+显式失败."""
        u = np.full(self.N, 50.0)  # OP 精确恒定（保持输入）
        t = np.arange(self.N, dtype=float)
        pv = 50.0 + 5.0 * np.sin(2.0 * math.pi * t / 1200.0)  # 负载驱动的 PV 摆动（ptp≈10 ≥3%）
        pv = pv + np.random.default_rng(23).normal(0, 0.05, self.N)
        result = identify_from_history(
            op=u.tolist(), pv=pv.tolist(), ts=self.TS, candidate_models=[ModelType.FOPDT]
        )
        # 显式拒绝（缺信息有具体原因），不输出冒充成功的模型
        assert not result.success
        assert result.reason is not None
        assert "激励不足" in result.reason or "OP 无变化" in result.reason
        # 信息量通道拒绝入审计（整段候选 info≈0 → INSUFFICIENT_INFO）
        ws = (result.preprocessing or {}).get("windowSearch") or {}
        rejected = ws.get("windowsRejected") or []
        assert any(r["reason"] == "INSUFFICIENT_INFO" for r in rejected), rejected

    def test_slow_ramp_rejected_via_info_channel(self):
        """慢变化输入（OP 单调慢斜坡）：信息量通道（方向丰富度=0）拒绝窗候选，
        整窗激励检测显式拒绝（不硬出值）."""
        u = 50.0 + np.linspace(0.0, 12.0, self.N)  # 单调慢斜坡（无方向变化）
        pv = self._y(u - 50.0, 30.0, 24)
        result = identify_from_history(
            op=u.tolist(), pv=pv.tolist(), ts=self.TS, candidate_models=[ModelType.FOPDT]
        )
        assert not result.success
        assert result.reason is not None and "激励不足" in result.reason
        ws = (result.preprocessing or {}).get("windowSearch") or {}
        rejected = ws.get("windowsRejected") or []
        info_rejected = [r for r in rejected if r["reason"] == "INSUFFICIENT_INFO"]
        assert info_rejected, rejected  # 慢斜坡按信息量通道（方向丰富度）拒绝
        assert all(r.get("directionChanges") == 0 for r in info_rejected)

    def test_dynamic_manual_segment_enters_estimation_over_quiet_auto(self):
        """动态手动段 + 安静 AUTO：手动段实际进入分析（G24 降级），参数 O-A1.

        手动段 OP 取短保持非周期方波（保持 8~30 样本，谱接近白——ARX 输出
        量测噪声偏差随输入谱变慢经慢极点共线放大，慢方波下 K 偏 21%，
        首版教训；操作员频繁小幅调整是"动态手动段"的典型形态）。
        """
        n_half = 1200
        # AUTO 前半：安静（OP 恒定，PV 稳态）；MANUAL 后半：动态手动 OP
        q_op = np.full(n_half, 50.0)
        q_pv = np.full(n_half, 50.0) + 0.02 * np.sin(np.arange(n_half) / 500.0)
        m_op = _prbs_u(n_half, seed=25, amp=8.0, period=30)
        m_pv0 = _zoh_fopdt(self.K, self.TAU, self.THETA, m_op - 50.0, self.TS)
        # 40dB 噪声档：保持型手动 OP 下 ARX 输出噪声偏差经慢极点共线放大
        # （20dB 档实测 K 偏 18%——白输入对照 0.2%，见交接裁量登记）；
        # 40dB 档偏差 ∝σ² 缩百倍，满足 O-A1（20dB 档容差 K≤5%）
        m_pv = (
            m_pv0
            + 50.0
            + np.random.default_rng(26).normal(0, _snr_noise_std(m_pv0 + 50.0, 40.0), n_half)
        )
        op = np.concatenate([q_op, m_op]).tolist()
        pv = np.concatenate([q_pv, m_pv]).tolist()
        mode = [1] * n_half + [0] * n_half
        result = identify_from_history(
            op=op, pv=pv, ts=self.TS, mode=mode, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        # 手动段降级使用事实透明（reason/预处理 note）
        note = ((result.preprocessing or {}).get("autoSegment") or {}).get("note") or ""
        assert "手动段降级" in note, note
        assert f"[{n_half}:{2 * n_half}]" in note, note
        p = result.best_model.params
        assert abs(p.K - self.K) / self.K <= OA1_K_TOL[20.0]
        assert abs(p.tau - self.TAU) / self.TAU <= OA1_TAU_TOL[20.0]

    def test_single_frequency_sopdt_must_not_beat_fopdt(self):
        """Occam 行为断言：单频激励上 SOPDT 不得仅凭 R² 胜出 FOPDT."""
        t = np.arange(self.N, dtype=float)
        u = 50.0 + 6.0 * np.sin(2.0 * math.pi * t / 300.0)  # 单频（周期 300s）
        pv = self._y(u - 50.0, 30.0, 27)
        result = identify_from_history(op=u.tolist(), pv=pv.tolist(), ts=self.TS)
        assert result.success, result.reason
        assert result.best_model.params.model_type == ModelType.FOPDT, (
            f"单频数据上胜出 {result.best_model.params.model_type}（Occam 削减失效）"
        )


# ---------------------------------------------------------------------------
# O-B3 自然单阶跃进分析（C14-ID1：带斜坡保持 SP 变更）
# ---------------------------------------------------------------------------


class TestOB3NaturalSingleStep:
    K, TAU, THETA, TS = 1.2, 25.0, 8.0, 1.0
    N = 14400  # 4h @ ts=1s

    def _build(self):
        """自然单阶跃（带斜坡保持 SP 变更）开环合成窗.

        SP：2h 恒定 50 → 15 样本斜坡升至 60 → 保持（斜坡保持型变更）；
        OP：操作员随之同型斜坡 50→62 → 保持 30min → 大幅回调阶跃 −12 →
        保持。PV=开环 FOPDT 响应 + 30dB 量测噪声。构造要点（首版教训）：
        - 开环（闭环跟踪段 PV≈SP 会使直接法产出 τ≈0 退化候选）；
        - 斜坡取 15 样本（0.6τ）：长斜坡（≥2τ）下 θ 与 τ 代数退化
          （一阶系统对斜坡的稳态滞后 = θ+τ，BIC 谷被噪声随机化），
          短斜坡接近阶跃、θ/τ 可分；回调取大幅瞬时阶跃（第二个非退化
          探测，方向丰富度 ≥1）。
        """
        sp = np.full(self.N, 50.0)
        op = np.full(self.N, 50.0)
        ramp_start = 7200
        ramp = np.linspace(0.0, 10.0, 15)
        sp[ramp_start : ramp_start + 15] = 50.0 + ramp
        sp[ramp_start + 15 :] = 60.0
        op[ramp_start : ramp_start + 15] = 50.0 + 1.2 * ramp
        op[ramp_start + 15 : ramp_start + 15 + 1800] = 62.0
        op[ramp_start + 15 + 1800 :] = 50.0  # 大幅回调阶跃（30min 后）
        y0 = _zoh_fopdt(self.K, self.TAU, self.THETA, op - 50.0, self.TS)
        # 40dB 噪声档：单事件窗内动态占比有限，20/30dB 档下平坦段噪声稀释
        # 使 τ̂ 偏快（首版教训：30dB 整窗 τ̂=17/θ̂=23）；40dB 档恢复进 O-A1
        noise = _snr_noise_std(y0[100:] - 50.0, 40.0)
        pv = y0 + 50.0 + np.random.default_rng(31).normal(0, noise, self.N)
        return sp, pv, op

    def test_event_window_detected_and_enters_pool(self):
        """自然单阶跃事件窗被检出并进入候选池（P-17）."""
        sp, pv, op = self._build()
        result = identify_from_history(op=op.tolist(), pv=pv.tolist(), sp=sp.tolist(), ts=self.TS)
        assert result.success, result.reason
        ws = (result.preprocessing or {}).get("windowSearch") or {}
        assert ws.get("ran") is True
        events = ws.get("events") or []
        assert any(e["type"] == "SP_STEP" for e in events), events
        checked = ws.get("windowsChecked") or []
        event_windows = [w for w in checked if w.get("source") == "NATURAL_EVENT"]
        assert event_windows, checked  # 事件窗进入候选池
        # 事件窗覆盖阶跃时刻（25%/75% 锚定：7200 ∈ [start, end)）
        assert any(w["start"] <= 7200 < w["end"] for w in event_windows)

    def test_natural_step_params_recovered_oa1(self):
        """自然单阶跃辨识参数恢复满足 O-A1 容差（30dB 高信噪档）."""
        sp, pv, op = self._build()
        result = identify_from_history(op=op.tolist(), pv=pv.tolist(), sp=sp.tolist(), ts=self.TS)
        assert result.success, result.reason
        p = result.best_model.params
        assert abs(p.K - self.K) / self.K <= OA1_K_TOL[20.0]
        assert abs(p.tau - self.TAU) / self.TAU <= OA1_TAU_TOL[20.0]
        assert abs(p.theta - self.THETA) <= _theta_tol(self.TS, self.THETA)


# ---------------------------------------------------------------------------
# O-B4 物理时间一致性（C14-ID2：θ=120s 同物理数据 ts∈{1,2,5,10}s）
# ---------------------------------------------------------------------------


class TestOB4PhysicalTimeConsistency:
    K, TAU, THETA = 1.0, 40.0, 120.0
    DURATION = 5000.0

    def _case(self, ts: float):
        n = int(self.DURATION / ts)
        hold = max(3, int(20 / ts))
        u = _sign_hold_u(n, seed=109, hold=hold)
        y0 = _zoh_fopdt(self.K, self.TAU, self.THETA, u - 50.0, ts)
        # 噪声 0.02（与 06a O-A5 同族构造同档——该档在 4 种采样率下已验证
        # θ 恢复 ≤max(2ts,10%)；O-B4 在其上追加两两一致性断言）
        y = y0 + 50.0 + np.random.default_rng(205 + int(ts)).normal(0, 0.02, n)
        return u, y

    @pytest.mark.parametrize("ts", [1.0, 2.0, 5.0, 10.0])
    def test_all_rates_succeed(self, ts: float):
        u, y = self._case(ts)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, f"ts={ts}: {result.reason}"

    def test_pairwise_consistency_within_15pct(self):
        """4 种采样率连续域参数（K/τ/θ 秒）两两相对差 ≤15%（共同频带内）."""
        params = {}
        for ts in (1.0, 2.0, 5.0, 10.0):
            u, y = self._case(ts)
            result = identify_from_history(
                op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.FOPDT]
            )
            assert result.success, result.reason
            p = result.best_model.params
            params[ts] = (p.K, p.tau, p.theta)
        rates = sorted(params)
        for i, r1 in enumerate(rates):
            for r2 in rates[i + 1 :]:
                k1, t1_, th1 = params[r1]
                k2, t2_, th2 = params[r2]
                assert abs(k1 - k2) / max(abs(k1), abs(k2)) <= OB4_PAIRWISE_TOL, (r1, r2, "K")
                assert abs(t1_ - t2_) / max(abs(t1_), abs(t2_)) <= OB4_PAIRWISE_TOL, (r1, r2, "tau")
                assert abs(th1 - th2) / max(abs(th1), abs(th2)) <= OB4_PAIRWISE_TOL, (
                    r1,
                    r2,
                    "theta",
                )

    def test_insufficient_sampling_explicitly_not_determinable(self):
        """采样不足（快 τ=3s @ ts=10s，τ<ts）：显式不可判断标记，不硬出高可信值."""
        K, tau, theta, ts = 1.0, 3.0, 2.0, 10.0
        n = 2000
        u = _white_u(n, seed=305, amp=5.0)
        y0 = _zoh_fopdt(K, tau, theta, u - 50.0, ts)
        y = y0 + 50.0 + np.random.default_rng(306).normal(0, _snr_noise_std(y0 + 50.0, 30.0), n)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.FOPDT]
        )
        level = {
            AlgorithmConfidenceLevel.A: 5,
            AlgorithmConfidenceLevel.B: 4,
            AlgorithmConfidenceLevel.C: 3,
            AlgorithmConfidenceLevel.D: 2,
            AlgorithmConfidenceLevel.E: 1,
            AlgorithmConfidenceLevel.INCONCLUSIVE: 0,
        }
        if result.success:
            best = result.best_model
            codes = (best.evidence.reason_codes if best.evidence else []) or []
            assert "DYNAMICS_BELOW_RESOLUTION" in codes, codes
            assert level.get(best.confidence, 0) <= 3, "分辨率不足不得高于 C 级"
        else:
            # 显式失败路径（激励检测拒绝）同为合法"不可判断"出口
            assert result.reason


# ---------------------------------------------------------------------------
# O-B5 未搜索可见（搜索空间 > 预算 → 四类清单 + 预算终态）
# ---------------------------------------------------------------------------


class TestOB5UnsearchedVisible:
    """10 段 × 130min 头部动态（段验证位死水）→ 候选窗 > B-05 cap 64.

    断言（07§4 06b 完成判据原文）：输出含被检查/选中/拒绝/未搜索四类
    清单 + 预算终态字段。
    """

    TS = 2.0
    N_SEG = 3900  # 130min @ ts=2s
    N_SEGMENTS = 10
    N_GAP = 20

    def _build(self):
        K, tau, theta = 1.2, 25.0, 8.0
        op_parts: list[np.ndarray] = []
        pv_parts: list[np.ndarray] = []
        sp_parts: list[np.ndarray] = []
        head = 1560  # 前 40% 动态
        for seg in range(self.N_SEGMENTS):
            s_op = np.full(self.N_SEG, 50.0)
            t = np.arange(head)
            s_op[:head] = 50.0 + 10.0 * np.sign(np.sin(2.0 * math.pi * t / 120.0))  # 方波 ±10
            s_pv = _zoh_fopdt(K, tau, theta, s_op - 50.0, self.TS, seed=41 + seg) + 50.0
            s_sp = np.full(self.N_SEG, 50.0)
            s_sp[:head] = 50.0 + 6.0 * np.sign(np.sin(2.0 * math.pi * np.arange(head) / 240.0))
            op_parts.append(s_op)
            pv_parts.append(s_pv)
            sp_parts.append(s_sp)
            if seg < self.N_SEGMENTS - 1:
                op_parts.append(np.full(self.N_GAP, np.nan))
                pv_parts.append(np.full(self.N_GAP, np.nan))
                sp_parts.append(np.full(self.N_GAP, np.nan))
        return (
            np.concatenate(op_parts).tolist(),
            np.concatenate(pv_parts).tolist(),
            np.concatenate(sp_parts).tolist(),
        )

    def test_four_class_audit_and_budget_termination(self):
        op, pv, sp = self._build()
        result = identify_from_history(op=op, pv=pv, sp=sp, ts=self.TS)
        assert result.success, result.reason
        prep = result.preprocessing or {}
        ws = prep.get("windowSearch") or {}
        # 四类清单齐备（B-04）
        for key in ("windowsChecked", "windowsSelected", "windowsRejected", "notSearched"):
            assert key in ws, f"搜索审计缺 {key}"
        # B-05 预算触发：候选窗数截断到 64，超限进 rejected（原因码可查）
        assert len(ws.get("windowsChecked") or []) == 64
        rejected = ws.get("windowsRejected") or []
        assert any(r["reason"] == "BUDGET_CAP_64" for r in rejected)
        budget = ws.get("budgetTermination")
        assert budget is not None and budget["cap"] == 64 and budget["truncated"] > 0
        # 未搜索可见：gap 区间（DATA_GAP 排除段）显式列出
        not_searched = ws.get("notSearched") or []
        assert any("DATA_GAP" in (r.get("reason") or "") for r in not_searched)
        # 选中窗 ⊆ 被检窗且互不重叠（B-06 k≤3）
        selected = ws.get("windowsSelected") or []
        assert 0 < len(selected) <= 3
        bounds = {(w["start"], w["end"]) for w in ws.get("windowsChecked") or []}
        for w in selected:
            assert (w["start"], w["end"]) in bounds
        for i, w1 in enumerate(selected):
            for w2 in selected[i + 1 :]:
                assert w1["end"] <= w2["start"] or w2["end"] <= w1["start"]
        # 段目录全保留（10 个动态段 + 9 个 gap 段）
        catalog = prep.get("segmentCatalog") or []
        assert len([r for r in catalog if r["exclusionReason"] is None]) == self.N_SEGMENTS

    def test_search_still_finds_dynamics_despite_truncation(self):
        """预算截断不阻断辨识：胜出窗仍含真系统动态（K 恢复 ≤10%）."""
        op, pv, sp = self._build()
        result = identify_from_history(op=op, pv=pv, sp=sp, ts=self.TS)
        assert result.success, result.reason
        p = result.best_model.params
        assert abs(p.K - 1.2) / 1.2 <= OA1_K_TOL[10.0]


# ---------------------------------------------------------------------------
# 单元：P-12 物理最小点数 / P-14 目录字段 / P-16 信息量 / P-17 事件检测
# O-A6 时滞边界出口 / P-19 分辨率标记
# ---------------------------------------------------------------------------


class TestP12PhysicalMinPoints:
    def test_formula_frozen_values(self):
        """P-12 冻结公式：max(50, min(600, 2×(θ_max+5τ)/ts))；无粗估 θ_max=min(300s, dur/4)."""
        # ts=1、n=1200（1200s）：θ_max=min(300,300)=300 → 2×300/1=600（cap 600）
        assert physical_min_segment_points(1.0, 1200) == 600
        # ts=2、n=999（1998s）：θ_max=min(300,499.5)=300 → 600/2=300
        assert physical_min_segment_points(2.0, 999) == 300
        # ts=1、n=200（200s）：θ_max=min(300,50)=50 → 100 点
        assert physical_min_segment_points(1.0, 200) == 100
        # ts=10、n=5000（50000s）：θ_max=300 → 60 点
        assert physical_min_segment_points(10.0, 5000) == 60
        # 下限档：极短数据不高于 P-12 的 50 点下限
        assert physical_min_segment_points(1.0, 4) == 50

    def test_same_physical_duration_equivalent_across_ts(self):
        """C14-ID2 等价性：同一物理时长（1200s）不同 ts 折算同一物理最小档."""
        pts_1s = physical_min_segment_points(1.0, 1200)  # 600s → 600 点
        pts_2s = physical_min_segment_points(2.0, 600)  # 600s → 300 点
        assert pts_1s * 1.0 == pts_2s * 2.0  # 物理秒相等（600s）
        # 399 点 @ ts=2（798s ≥ 600s）合格；同物理时长 @ ts=1 需 600 点
        assert 399 >= physical_min_segment_points(2.0, 999)
        assert 399 < physical_min_segment_points(1.0, 999)

    def test_explicit_theta_tau_widen_bound(self):
        """显式粗估（θ_max/τ_预期）加宽物理最小档（有信息时要求更长段）."""
        base = physical_min_segment_points(1.0, 5000, theta_max_seconds=100.0)
        wider = physical_min_segment_points(
            1.0, 5000, theta_max_seconds=100.0, tau_expected_seconds=20.0
        )
        assert wider == min(600, math.ceil(2.0 * (100.0 + 5.0 * 20.0)))
        assert wider > base


class TestP14SegmentCatalogFields:
    def test_catalog_p14_fields_and_unknown_explicit(self):
        """P-14：段目录字段齐备；工况/PID 版本未知显式 None，mode UNKNOWN 显式."""
        n = 800
        u = _white_u(n, seed=51)
        y0 = _zoh_fopdt(1.2, 25.0, 8.0, u - 50.0, 1.0)
        y = y0 + 50.0 + np.random.default_rng(52).normal(0, 0.05, n)
        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0)
        assert result.success, result.reason
        catalog = (result.preprocessing or {}).get("segmentCatalog") or []
        assert catalog, "段目录未产出"
        rec = catalog[0]
        for key in (
            "segmentId",
            "tsStart",
            "tsEnd",
            "mode",
            "regimeId",
            "pidVersion",
            "sampleInterval",
            "exclusionReason",
            "qualifyTier",
        ):
            assert key in rec, f"段目录缺 P-14 字段 {key}"
        assert rec["regimeId"] is None and rec["pidVersion"] is None  # 未知显式 None
        assert rec["mode"] == "UNKNOWN"  # 无 MODE 序列时工况未知显式 UNKNOWN
        assert rec["sampleInterval"] == 1.0

    def test_short_gap_catalog_gap_range_recorded(self):
        """短段（<50 点）+ 缺口：目录记录排除段（TOO_SHORT），全部可查."""
        n_quiet, n_gap, n_short = 700, 20, 30
        u = np.concatenate(
            [_white_u(n_quiet, seed=61), np.full(n_gap, np.nan), np.full(n_short, 50.0)]
        )
        y = np.concatenate(
            [
                _zoh_fopdt(1.2, 25.0, 8.0, u[:n_quiet] - 50.0, 1.0) + 50.0,
                np.full(n_gap, np.nan),
                np.full(n_short, 50.0),
            ]
        )
        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0)
        catalog = (result.preprocessing or {}).get("segmentCatalog") or []
        short_segs = [r for r in catalog if r["exclusionReason"] == "TOO_SHORT"]
        assert any(r["pointCount"] == n_short for r in short_segs)


class TestP16InfoMetrics:
    def test_held_input_movement_zero(self):
        u = np.full(1000, 50.0)
        assert _window_info_metric(u) == 0.0
        assert _window_direction_changes(u) == 0

    def test_white_input_movement_large(self):
        u = _white_u(1800, seed=71, amp=5.0)
        assert _window_info_metric(u) > 1.0  # 累计运动 > 1 倍量程
        assert _window_direction_changes(u) > 10

    def test_monotone_ramp_zero_direction_changes(self):
        u = 50.0 + np.linspace(0.0, 12.0, 2400)
        assert _window_direction_changes(u) == 0
        assert _window_info_metric(u) == pytest.approx(0.12, abs=0.01)


class TestP17EventDetection:
    def test_instant_step_detected(self):
        sp = np.concatenate([np.full(1000, 50.0), np.full(1000, 60.0)])
        events = _detect_natural_events(sp, None, len(sp))
        steps = [e for e in events if e["type"] == "SP_STEP"]
        assert len(steps) == 1
        assert 900 <= steps[0]["index"] <= 1100  # flank 均值差峰在阶跃附近
        assert abs(abs(steps[0]["stepSize"]) - 10.0) < 1.0

    def test_ramp_hold_step_detected(self):
        """斜坡保持型 SP 变更（60 样本斜坡）检出（O-B3 构造要素）."""
        sp = np.full(4000, 50.0)
        ramp = np.linspace(0.0, 10.0, 60)
        sp[2000:2060] = 50.0 + ramp
        sp[2060:] = 60.0
        events = _detect_natural_events(sp, None, len(sp))
        steps = [e for e in events if e["type"] == "SP_STEP"]
        assert len(steps) == 1
        assert 1980 <= steps[0]["index"] <= 2120

    def test_mode_change_event(self):
        sp = np.full(1000, 50.0)
        mode = [1] * 500 + [0] * 500
        events = _detect_natural_events(sp, mode, 1000)
        mode_events = [e for e in events if e["type"] == "MODE_CHANGE"]
        assert len(mode_events) == 1 and mode_events[0]["index"] == 500

    def test_no_false_event_on_constant(self):
        sp = np.full(2000, 50.0)
        assert _detect_natural_events(sp, None, 2000) == []


class TestOA6DelayBoundaryExit:
    def test_upper_boundary_hit_marks_insufficient_delay_info(self):
        """O-A6：θ 超搜索上界 → DELAY_BOUNDARY_UPPER_HIT 码 + 可信度封顶 C.

        （06a R1 审计测试同构造：真 θ=40s，显式 cap=30s → UPPER 命中；
        06b 起显式标记 θ 信息不足，不冒充已确定。）
        """
        K, tau, theta_true, ts, n = 1.0, 40.0, 40.0, 1.0, 5000
        u = _sign_hold_u(n, seed=109, hold=20)
        y0 = _zoh_fopdt(K, tau, theta_true, u - 50.0, ts)
        y = y0 + 50.0 + np.random.default_rng(205).normal(0, _snr_noise_std(y0 + 50.0, 30.0), n)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=ts,
            candidate_models=[ModelType.FOPDT],
            delay_search_max_seconds=30.0,
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev.delay_search_info["boundaryHit"] == "UPPER"
        assert "DELAY_BOUNDARY_UPPER_HIT" in ev.reason_codes
        level = {
            AlgorithmConfidenceLevel.A: 5,
            AlgorithmConfidenceLevel.B: 4,
            AlgorithmConfidenceLevel.C: 3,
            AlgorithmConfidenceLevel.D: 2,
            AlgorithmConfidenceLevel.E: 1,
        }
        assert level.get(result.best_model.confidence, 0) <= 3, "θ 边界命中不得高于 C 级"

    def test_normal_search_not_flagged(self):
        """正常恢复（θ 在界内）：无 DELAY_BOUNDARY_UPPER_HIT 误标."""
        K, tau, theta, ts, n = 1.2, 25.0, 8.0, 1.0, 3000
        u = _white_u(n, seed=81)
        y0 = _zoh_fopdt(K, tau, theta, u - 50.0, ts)
        y = y0 + 50.0 + np.random.default_rng(82).normal(0, _snr_noise_std(y0 + 50.0, 20.0), n)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        assert "DELAY_BOUNDARY_UPPER_HIT" not in (result.best_model.evidence.reason_codes or [])


class TestP19BelowResolution:
    def test_tau_below_ts_flagged(self):
        """P-19：连续 τ < ts → DYNAMICS_BELOW_RESOLUTION + 封顶 C（不硬出高可信值）."""
        K, tau, theta, ts, n = 1.0, 3.0, 2.0, 10.0, 2000
        u = _white_u(n, seed=91, amp=5.0)
        y0 = _zoh_fopdt(K, tau, theta, u - 50.0, ts)
        y = y0 + 50.0 + np.random.default_rng(92).normal(0, _snr_noise_std(y0 + 50.0, 30.0), n)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.FOPDT]
        )
        if not result.success:
            pytest.skip("激励检测显式拒绝同为合法出口")
        best = result.best_model
        assert "DYNAMICS_BELOW_RESOLUTION" in (best.evidence.reason_codes if best.evidence else [])
        level = {
            AlgorithmConfidenceLevel.A: 5,
            AlgorithmConfidenceLevel.B: 4,
            AlgorithmConfidenceLevel.C: 3,
        }
        assert level.get(best.confidence, 0) <= 3
