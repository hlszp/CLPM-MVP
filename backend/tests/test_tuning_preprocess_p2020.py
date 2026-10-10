"""P2-020（2026-10-04）预处理增强测试：自动选段 / 去趋势 / 低通滤波.

覆盖三层：
1. 三个预处理函数的单元行为（选段正确性 / 斜率归零 / 高频能量抑制）；
2. identify_from_history 端到端：多段窗口自动选段 + reason 透明化标注；
3. 既有行为回归保护：mode=None（无 MODE 信息）不切分、辨识照常成功。
"""

from __future__ import annotations

import math

import numpy as np

from app.services.tuning_identification.pipeline import (
    _auto_select_segment,
    _detrend_signals,
    _lowpass_signals,
    identify_from_history,
)

# ---------------------------------------------------------------------------
# 仿真辅助（同 test_tuning_identification 口径的自包含简版）
# ---------------------------------------------------------------------------


def _simulate_fopdt(
    K: float, tau: float, theta: float, u: np.ndarray, noise_std: float = 0.0, seed: int = 42
) -> np.ndarray:
    """开环 FOPDT 仿真：G(s) = K·e^(-θs)/(τs+1)."""
    rng = np.random.default_rng(seed)
    n = len(u)
    a = math.exp(-1.0 / tau)
    b = K * (1 - a)
    d = max(0, round(theta))
    y = np.zeros(n)
    for k in range(d, n):
        y[k] = a * y[k - 1] + b * u[k - d]
    if noise_std > 0:
        y += rng.normal(0, noise_std, n)
    return y


def _prbs(n: int, seed: int = 42, amp: float = 5.0) -> np.ndarray:
    """二值伪随机激励（OP 归一化 0~100 域，中心 50）."""
    rng = np.random.default_rng(seed)
    u = np.full(n, amp)
    switch_idx = sorted(rng.choice(n, size=max(1, n // 10), replace=False))
    sign = 1.0
    prev = 0
    for idx in switch_idx:
        u[prev:idx] = sign * amp
        sign *= -1
        prev = idx
    u[prev:] = sign * amp
    return u + 50.0


# ---------------------------------------------------------------------------
# 单元：自动选段
# ---------------------------------------------------------------------------


class TestAutoSelectSegment:
    def test_selects_auto_segment_over_manual(self) -> None:
        """前段 AUTO 好激励 + 后段 MANUAL → 选 AUTO 段（切出前半）."""
        n = 1200
        u = _prbs(n, seed=1)
        y = _simulate_fopdt(2.0, 30.0, 5.0, u - 50.0, noise_std=0.05) + 40.0
        mode = [1] * (n // 2) + [0] * (n - n // 2)  # 前 AUTO 后 MANUAL

        su, sy, _ssp, note = _auto_select_segment(u, y, None, mode)

        assert len(su) == n // 2
        assert note is not None and "AUTO" in note
        assert "手动段降级" not in note

    def test_falls_back_to_manual_segment(self) -> None:
        """全 MANUAL（G24：手动段可辨识，降级使用而非拒绝）."""
        n = 800
        u = _prbs(n, seed=2)
        y = _simulate_fopdt(2.0, 30.0, 5.0, u - 50.0, noise_std=0.05) + 40.0
        mode = [0] * n

        su, _sy, _ssp, note = _auto_select_segment(u, y, None, mode)

        assert len(su) > 0
        assert note is not None and "手动段降级" in note

    def test_no_mode_no_split(self) -> None:
        """mode=None：不切分（假设全 AUTO），整窗返回且无标注."""
        n = 600
        u = _prbs(n, seed=3)
        y = _simulate_fopdt(1.5, 20.0, 3.0, u - 50.0)

        su, _sy, _ssp, note = _auto_select_segment(u, y, None, None)

        assert len(su) == n
        assert note is None

    def test_single_auto_window_no_note(self) -> None:
        """mode 全 AUTO（单段整窗）：无选段事实，note=None."""
        n = 600
        u = _prbs(n, seed=4)
        y = _simulate_fopdt(1.5, 20.0, 3.0, u - 50.0)
        mode = [1] * n

        su, _sy, _ssp, note = _auto_select_segment(u, y, None, mode)

        assert len(su) == n
        assert note is None


# ---------------------------------------------------------------------------
# 单元：去趋势
# ---------------------------------------------------------------------------


class TestDetrendSignals:
    def test_linear_trend_removed(self) -> None:
        """工作点漂移（OP 同向缓变佐证）→ 去趋势执行：残差斜率≈0."""
        n = 1000
        t = np.arange(n, dtype=float)
        base = 50.0 + 2.0 * np.sin(t / 50.0)
        trend = 0.05  # 每采样 0.05 → 全窗漂移 50（0~100 归一化域 50%）
        y = base + trend * t
        u = base * 0.5 + 0.01 * t  # OP 同向缓变（全窗 10%）——工作点漂移佐证

        u2, y2, _sp2, note = _detrend_signals(u.copy(), y, None)

        # 残差斜率 ≈ 0
        slope_after = float(np.polyfit(t, y2, 1)[0])
        assert abs(slope_after) < 1e-6
        assert float(np.polyfit(t, u2, 1)[0]) == 0.0 or abs(float(np.polyfit(t, u2, 1)[0])) < 1e-6
        assert note is not None and "detrend" in note
        # 无趋势分量保留（正弦仍在；相关系数略降源自线性投影的端部效应）
        assert abs(float(np.corrcoef(base, y2)[0, 1])) > 0.95

    def test_flat_signal_no_note(self) -> None:
        """平稳信号漂移 < 5% 量程：不执行去趋势（干净数据零影响）."""
        n = 800
        t = np.arange(n, dtype=float)
        base = 50.0 + 2.0 * np.sin(t / 50.0)

        _u2, y2, _sp2, note = _detrend_signals(base.copy(), base.copy(), None)

        assert note is None

    def test_pv_ramp_with_flat_op_protected(self) -> None:
        """OP 平稳 + PV 持续斜坡 = 积分特性（IPDT 保护）→ 不去趋势."""
        n = 1000
        # 平衡方波 OP（全窗线性拟合斜率严格为 0，排除随机相位的残余斜率）
        u = np.tile([45.0, 55.0], n // 2)
        y = np.linspace(0, 40, n)  # PV 持续斜坡（全窗漂移 40%）

        _u2, y2, _sp2, note = _detrend_signals(u, y.copy(), None)

        assert note is None
        assert np.array_equal(y2, y)  # 原样保留


# ---------------------------------------------------------------------------
# 单元：低通滤波
# ---------------------------------------------------------------------------


class TestLowpassSignals:
    def test_high_frequency_noise_suppressed(self) -> None:
        """慢过程 + 高频噪声 → 滤波生效，高频能量显著下降且 note 记录."""
        n = 2000
        rng = np.random.default_rng(7)
        t = np.arange(n, dtype=float)
        slow = 50.0 + 3.0 * np.sin(t / 200.0)  # 慢动态（周期 200 采样）
        u = slow * 0.5 + rng.normal(0, 1.0, n)  # OP 含高频噪声
        y = slow + rng.normal(0, 1.0, n)

        fu, fy, _fsp, note = _lowpass_signals(u, y, None, ts=1.0)

        assert note is not None and "lowpass" in note
        # 噪声残留显著下降（直接度量：滤波后与干净慢信号的差；
        # 总 std 不适用——信号能量占主导时其本就不会大降）
        assert float(np.std(fy - slow)) < 0.5 * float(np.std(y - slow))
        # 慢分量保留
        assert abs(float(np.corrcoef(slow, fy)[0, 1])) > 0.98
        assert fu is not None

    def test_broadband_signal_skipped(self) -> None:
        """宽带信号（白噪声）：自适应截止达上限 → 跳过滤滤（note=None）."""
        n = 2000
        rng = np.random.default_rng(8)
        y = rng.normal(0, 1.0, n)  # 纯白噪声，能量均匀铺到 Nyquist
        u = rng.normal(0, 1.0, n)

        _fu, _fy, _fsp, note = _lowpass_signals(u, y, None, ts=1.0)

        assert note is None


# ---------------------------------------------------------------------------
# 端到端：identify_from_history
# ---------------------------------------------------------------------------


class TestIdentifyWithPreprocess:
    def test_multi_segment_window_selects_best(self) -> None:
        """窗口 = 好激励 AUTO 段 + 平稳死段（OP 恒定）→ 自动选段后辨识成功."""
        n_good, n_dead = 1200, 800
        u_good = _prbs(n_good, seed=11)
        y_good = _simulate_fopdt(2.0, 30.0, 5.0, u_good - 50.0, noise_std=0.05)
        # 死段：OP 恒定（无激励）+ 缓慢漂移 PV（趋势污染源）
        u_dead = np.full(n_dead, 50.0)
        y_dead = np.full(n_dead, 0.0) + np.linspace(0, 8, n_dead)

        u = np.concatenate([u_good, u_dead]).tolist()
        y = np.concatenate([y_good, y_dead]).tolist()
        # 死段标 MANUAL → 切分边界明确（否则死段也是 AUTO，靠激励评分淘汰）
        mode = [1] * n_good + [0] * n_dead

        result = identify_from_history(op=u, pv=y, ts=1.0, mode=mode)

        assert result.success, result.reason
        assert "auto-segment" in result.reason
        # 选中的是好激励段：K 应接近真值 2.0（死段混入时 K 被稀释）
        assert result.best_model is not None
        k_hat = result.best_model.params.K
        assert abs(k_hat - 2.0) / 2.0 < 0.25

    def test_no_mode_backward_compatible(self) -> None:
        """回归保护：不传 mode（既有调用方）→ 不切分、辨识照常成功."""
        n = 1200
        u = _prbs(n, seed=12)
        y = _simulate_fopdt(1.5, 25.0, 4.0, u - 50.0, noise_std=0.05)

        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0)

        assert result.success, result.reason
        assert "auto-segment" not in result.reason

    def test_trend_and_noise_data_identifies(self) -> None:
        """趋势 + 噪声数据（去趋势默认关闭状态，2026-10-10 用户裁决）：
        仍成功辨识；PV 独立线性趋势保留导致 K 系统性偏高是已记录的代价
        （误去趋势摧毁慢过程阶跃响应的代价更高，见 _DETREND_ENABLED 注释）。"""
        n = 2000
        rng = np.random.default_rng(13)
        t = np.arange(n, dtype=float)
        u_clean = _prbs(n, seed=13)
        y_clean = _simulate_fopdt(1.2, 40.0, 6.0, u_clean - 50.0)
        drift = 0.01 * t  # 全窗漂移 20（0~100 域 20%），OP 同向缓变（工作点漂移）
        u = (u_clean + 0.004 * t + rng.normal(0, 0.8, n)).tolist()
        y = (y_clean + 30.0 + drift + rng.normal(0, 0.8, n)).tolist()

        result = identify_from_history(op=u, pv=y, ts=1.0)

        assert result.success, result.reason
        assert result.best_model is not None
        assert abs(result.best_model.params.K - 1.2) / 1.2 < 0.60


# ---------------------------------------------------------------------------
# v1.2：IPDT 默认候选 + 验证段近稳态防护
# ---------------------------------------------------------------------------


class TestV12IpdtDefaultCandidate:
    def test_ipdt_in_default_candidates(self) -> None:
        """不传 candidate_models 时 IPDT 进入默认候选（液位等积分过程）。"""
        from app.services.tuning_identification.types import ModelType

        n = 1500
        u = _prbs(n, seed=31)
        # 积分过程：PV 持续积分爬升（IPDT 仿真简版）
        y = np.cumsum(u - 50.0) * 0.02
        y = y + np.random.default_rng(31).normal(0, 0.05, n)

        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0)
        types = {c.params.model_type for c in result.candidates}
        assert ModelType.IPDT in types

    def test_self_regulating_still_picks_fopdt(self) -> None:
        """自衡过程默认候选含 IPDT 后 Occam 仍选 FOPDT（不误判结构）。"""
        n = 1500
        u = _prbs(n, seed=32)
        y = _simulate_fopdt(1.5, 25.0, 4.0, u - 50.0, noise_std=0.05)

        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0)
        assert result.success, result.reason
        assert result.best_model is not None
        assert result.best_model.params.model_type.value == "FOPDT"


class TestV12ValStationaryGuard:
    def test_front_dynamic_tail_stationary(self) -> None:
        """前部动态+尾部稳态（41LIC12422 形态）：VAL_STATIONARY 标注+C 封顶，
        fittingScore 回退训练口径（不再假性归零）。"""
        n_dyn, n_flat = 1000, 1000
        u_dyn = _prbs(n_dyn, seed=41)
        y_dyn = _simulate_fopdt(2.0, 20.0, 3.0, u_dyn - 50.0, noise_std=0.05)
        # 稳态尾：OP 微幅波动 + PV 恒定（ptp < 1.5% 量程）
        rng = np.random.default_rng(42)
        u_flat = 50.0 + rng.normal(0, 0.3, n_flat)
        y_flat = 40.0 + rng.normal(0, 0.05, n_flat)

        u = np.concatenate([u_dyn, u_flat]).tolist()
        y = np.concatenate([y_dyn, y_flat]).tolist()

        result = identify_from_history(op=u, pv=y, ts=1.0)
        assert result.success, result.reason
        best = result.best_model
        assert best is not None
        # 60/20/20 分割的 val 段落在稳态区 → 触发防护
        assert "VAL_STATIONARY" in (best.evidence.reason_codes if best.evidence else [])
        # C 封顶（验证不可用需人工复核）
        assert best.confidence.value == "C"
        # fittingScore 不再假性归零（训练口径）
        assert best.fitting_score > 50

    def test_full_dynamic_no_guard(self) -> None:
        """全程激励数据不触发稳态防护（回归保护）。"""
        n = 2000
        u = _prbs(n, seed=43)
        y = _simulate_fopdt(1.5, 20.0, 3.0, u - 50.0, noise_std=0.05)

        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0)
        assert result.success
        best = result.best_model
        assert best is not None
        assert "VAL_STATIONARY" not in (best.evidence.reason_codes if best.evidence else [])


# ---------------------------------------------------------------------------
# v1.3：去趋势阶跃保护 + 动态感知子窗（auto-window）
# ---------------------------------------------------------------------------


class TestV13DetrendStepGuard:
    def test_sp_step_ramp_protected(self) -> None:
        """SP 阶跃后的慢爬升是响应不是漂移：不去趋势（v1.3 第四条件）."""
        n = 1200
        t = np.arange(n, dtype=float)
        sp = np.zeros(n)
        sp[300:] = 8.0  # 单次大阶跃（8% 量程）
        u = 50.0 + 0.01 * t  # OP 缓变（漂移形态，过前三条件）
        y = np.cumsum(sp) * 0.004 + 40.0  # 阶跃后持续爬升（线性形状）

        _u2, y2, _sp2, note = _detrend_signals(u, y.copy(), sp)

        assert note is None
        assert np.array_equal(y2, y)  # 原样保留（爬升信号未被去除）

    def test_pure_drift_still_detrended(self) -> None:
        """无阶跃事件的真实漂移（回归保护）：仍执行去趋势."""
        n = 1000
        t = np.arange(n, dtype=float)
        base = 50.0 + 2.0 * np.sin(t / 50.0)
        u = base * 0.5 + 0.01 * t
        y = base + 0.05 * t

        _u2, _y2, _sp2, note = _detrend_signals(u, y, None)

        assert note is not None and "detrend" in note


class TestV13AutoWindow:
    def test_front_dynamic_long_dead_tail(self) -> None:
        """大时段=前部动态+长死水（41LIC 形态）：auto-window 切出动态段.

        验证位（60%~80%）不再落在死水上，VAL_STATIONARY 不触发。
        """
        n_dyn, n_dead = 3600, 7200  # 1h 动态 + 2h 死水（用户给 3h 大时段）
        u_dyn = _prbs(n_dyn, seed=51)
        y_dyn = _simulate_fopdt(2.0, 60.0, 10.0, u_dyn - 50.0, noise_std=0.05)
        rng = np.random.default_rng(52)
        u_dead = 50.0 + rng.normal(0, 0.3, n_dead)
        # 死水段接在动态段末值上（连续过程；不连续会人为制造假阶跃）
        y_dead = float(y_dyn[-1]) + rng.normal(0, 0.05, n_dead)  # ptp < 1.5%

        u = np.concatenate([u_dyn, u_dead]).tolist()
        y = np.concatenate([y_dyn, y_dead]).tolist()

        result = identify_from_history(op=u, pv=y, ts=1.0)
        assert result.success, result.reason
        assert "auto-window" in (result.reason or "")
        best = result.best_model
        assert best is not None
        # 验证位有效 → 不再触发稳态防护
        assert "VAL_STATIONARY" not in (best.evidence.reason_codes if best.evidence else [])
        # 动态段辨识 K 恢复
        assert abs(best.params.K - 2.0) / 2.0 < 0.30

    def test_full_dynamic_window_not_split(self) -> None:
        """整窗动态（验证位正常）：auto-window 不介入（回归保护）."""
        n = 6000
        u = _prbs(n, seed=53)
        y = _simulate_fopdt(1.5, 40.0, 5.0, u - 50.0, noise_std=0.05)

        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0)
        assert result.success
        assert "auto-window" not in (result.reason or "")


class TestV14MultiWindow:
    def test_two_spaced_dynamic_segments(self) -> None:
        """大时段内两个分离的动态段（各含阶跃响应）+ 死水：multi-window
        逐窗辨识择优，reason 汇总各窗结果."""
        # 8h 大时段（用户生产场景：阶跃响应稀疏分布）：
        # 段1 [0:3600] 动态 + 2.5h 死水 + 段2 [12600:16200] 动态 + 尾部死水。
        # 段距 >120min，单一最大窗无法覆盖两段 → 逐窗 multi-window 编排；
        # 整窗 val 位 [17280:23040] 全为死水满足启动条件（段2 结束 16200 < 17280）
        u1 = _prbs(3600, seed=61)
        y1 = _simulate_fopdt(1.5, 40.0, 6.0, u1 - 50.0, noise_std=0.05)
        rng = np.random.default_rng(62)
        ud1 = 50.0 + rng.normal(0, 0.3, 9000)
        yd1 = float(y1[-1]) + rng.normal(0, 0.05, 9000)
        u2 = _prbs(3600, seed=63)
        y2 = _simulate_fopdt(1.5, 40.0, 6.0, u2 - 50.0, noise_std=0.05)
        ud2 = 50.0 + rng.normal(0, 0.3, 12600)
        yd2 = float(y2[-1]) + rng.normal(0, 0.05, 12600)

        u = np.concatenate([u1, ud1, u2, ud2]).tolist()
        y = np.concatenate([y1, yd1, y2, yd2]).tolist()

        result = identify_from_history(op=u, pv=y, ts=1.0)
        assert result.success, result.reason
        # 挖出动态子窗：相近段被大窗整体覆盖（auto-window）或逐窗择优
        # （multi-window）均为正确行为——管线保证辨识用的是动态数据
        assert ("multi-window" in (result.reason or "")) or ("auto-window" in (result.reason or ""))
        best = result.best_model
        assert best is not None
        # 动态段内辨识 K 恢复（两段真值同为 1.5）
        assert abs(best.params.K - 1.5) / 1.5 < 0.30
        # 验证位有效
        assert "VAL_STATIONARY" not in (best.evidence.reason_codes if best.evidence else [])

    def test_single_dynamic_segment_falls_back_to_auto_window(self) -> None:
        """仅一个动态段：挖出动态子窗（auto-window 单窗或两个高评分半窗
        触发 multi-window 均为正确行为）."""
        n_dyn, n_dead = 3600, 7200
        u_dyn = _prbs(n_dyn, seed=64)
        y_dyn = _simulate_fopdt(2.0, 60.0, 10.0, u_dyn - 50.0, noise_std=0.05)
        rng = np.random.default_rng(65)
        u_dead = 50.0 + rng.normal(0, 0.3, n_dead)
        y_dead = float(y_dyn[-1]) + rng.normal(0, 0.05, n_dead)
        u = np.concatenate([u_dyn, u_dead]).tolist()
        y = np.concatenate([y_dyn, y_dead]).tolist()

        result = identify_from_history(op=u, pv=y, ts=1.0)
        assert result.success, result.reason
        assert ("auto-window" in (result.reason or "")) or ("multi-window" in (result.reason or ""))


class TestV14CollectDisjoint:
    def test_collect_disjoint_windows(self) -> None:
        """多窗收集：按评分降序贪心选互不重叠 top-K."""
        from app.services.tuning_identification.pipeline import _collect_disjoint_windows

        wins = [
            (10.0, 0, 3600, 5.0, 10.0),
            (9.0, 300, 3900, 4.5, 9.0),  # 与首窗重叠 → 剔除
            (8.0, 7200, 10800, 4.0, 8.0),
            (7.0, 9000, 12600, 3.5, 7.0),  # 与第三窗重叠 → 剔除
            (6.0, 14400, 18000, 3.0, 6.0),
            (5.0, 0, 100000, 2.5, 5.0),  # 越界窗长不在列表（防误构造），重叠剔除
        ]
        picked = _collect_disjoint_windows(wins, k=3)  # type: ignore[arg-type]
        assert [(w[1], w[2]) for w in picked] == [(0, 3600), (7200, 10800), (14400, 18000)]

        picked2 = _collect_disjoint_windows(wins, k=2)  # type: ignore[arg-type]
        assert len(picked2) == 2
