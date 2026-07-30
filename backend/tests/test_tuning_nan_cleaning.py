"""P2-019 历史辨识入口坏点清洗测试.

验证 _clean_nan_segments 和 identify_from_history 入口清洗逻辑：
- 小缺口（< 5 连续 NaN）线性插值恢复；
- 大缺口（≥ 5 连续 NaN）取最长连续有效段；
- SP 同步清洗；
- 纯 NaN / 清洗后不足 50 点拒绝；
- 端点 NaN 按大缺口处理（无法插值）。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.tuning_identification.pipeline import _clean_nan_segments


class TestCleanNanSegments:
    """_clean_nan_segments 单元测试."""

    def test_no_nan_returns_unchanged(self):
        """无坏点：原样返回，stats 全 0."""
        u = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        u_c, y_c, sp_c, stats = _clean_nan_segments(u, y, None)
        np.testing.assert_array_equal(u_c, u)
        np.testing.assert_array_equal(y_c, y)
        assert sp_c is None
        assert stats["interpolated_points"] == 0
        assert stats["dropped_points"] == 0
        assert stats["valid_points"] == 5
        assert stats["valid_rate"] == 1.0

    def test_small_gap_linear_interpolation(self):
        """小缺口（3 连续 NaN < 5）线性插值恢复."""
        u = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
        y = np.array([0.0, 10.0, np.nan, np.nan, np.nan, 50.0, 60.0, 70.0, 80.0, 90.0])
        u_c, y_c, _, stats = _clean_nan_segments(u, y, None)
        # 插值后长度不变（小缺口插值不丢弃）
        assert len(y_c) == 10
        assert stats["interpolated_points"] == 3
        assert stats["dropped_points"] == 0
        # 插值值应在 10 和 50 之间线性分布
        assert y_c[2] == pytest.approx(20.0, abs=0.1)
        assert y_c[3] == pytest.approx(30.0, abs=0.1)
        assert y_c[4] == pytest.approx(40.0, abs=0.1)
        # 无残留 NaN
        assert np.all(np.isfinite(y_c))

    def test_large_gap_takes_longest_segment(self):
        """大缺口（10 连续 NaN ≥ 5）取最长连续有效段."""
        u = np.ones(30)
        y = np.arange(30, dtype=float)
        # 中间挖 10 连续 NaN
        y[10:20] = np.nan
        u_c, y_c, _, stats = _clean_nan_segments(u, y, None)
        # 大缺口不插值，取最长段（0-9 或 20-29，各 10 点，取前者）
        assert stats["interpolated_points"] == 0
        assert stats["dropped_points"] == 20  # 丢弃了另一段 10 点 + 10 个 NaN
        assert stats["valid_points"] == 10
        assert stats["n_large_gaps"] == 1

    def test_mixed_gaps(self):
        """混合缺口：小缺口插值 + 大缺口取最长段."""
        u = np.ones(40)
        y = np.arange(40, dtype=float)
        # 小缺口 2 点（可插值）
        y[5:7] = np.nan
        # 大缺口 8 点（取段）
        y[20:28] = np.nan
        u_c, y_c, _, stats = _clean_nan_segments(u, y, None)
        assert stats["interpolated_points"] == 2
        assert stats["n_large_gaps"] == 1
        # 大缺口切分后：段1=0-19（20点含插值）、段2=28-39（12点），取段1
        assert stats["valid_points"] == 20
        assert np.all(np.isfinite(y_c))

    def test_sp_synchronized_cleaning(self):
        """SP 同步清洗：OP/PV 小缺口插值时 SP 也插值."""
        u = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([0.0, 10.0, np.nan, np.nan, 40.0, 50.0])
        sp = np.array([100.0, 100.0, np.nan, np.nan, 200.0, 200.0])
        _, _, sp_c, stats = _clean_nan_segments(u, y, sp)
        assert stats["interpolated_points"] == 2
        assert sp_c is not None
        assert sp_c[2] == pytest.approx(133.33, abs=0.5)
        assert sp_c[3] == pytest.approx(166.67, abs=0.5)

    def test_all_nan_returns_empty(self):
        """全 NaN：返回空数组，valid_points=0."""
        u = np.full(10, np.nan)
        y = np.full(10, np.nan)
        u_c, y_c, _, stats = _clean_nan_segments(u, y, None)
        assert len(u_c) == 0
        assert stats["valid_points"] == 0
        assert stats["valid_rate"] == 0.0

    def test_endpoint_nan_treated_as_large_gap(self):
        """端点 NaN 无法插值，按大缺口处理（取有效段）."""
        u = np.ones(15)
        y = np.arange(15, dtype=float)
        # 开头 3 个 NaN（端点无法插值，即使 < 5 也按大缺口）
        y[0:3] = np.nan
        u_c, y_c, _, stats = _clean_nan_segments(u, y, None)
        # 端点 NaN 不满足 g_start > 0，归入 large_gaps
        assert stats["interpolated_points"] == 0
        assert stats["valid_points"] == 12  # 取 3-14

    def test_op_nan_only_treated_as_bad(self):
        """OP 单独 NaN（PV 正常）也视为坏点."""
        u = np.array([0.0, np.nan, 2.0, 3.0, 4.0, 5.0])
        y = np.array([0.0, 10.0, 20.0, 30.0, 40.0, 50.0])
        u_c, y_c, _, stats = _clean_nan_segments(u, y, None)
        # 1 点 NaN < 5 且在中间，可插值
        assert stats["interpolated_points"] == 1
        assert u_c[1] == pytest.approx(1.0, abs=0.1)


class TestIdentifyFromHistoryNanCleaning:
    """identify_from_history 入口清洗集成测试."""

    def _generate_fopdt(
        self, n: int = 200, nan_indices: list[int] | None = None
    ) -> tuple[list, list]:
        """生成 FOPDT 仿真数据，可选注入 NaN."""
        rng = np.random.default_rng(42)
        K, tau, theta, ts = 2.0, 30.0, 3.0, 1.0
        a = math.exp(-ts / tau)
        b = K * (1 - a)
        d = round(theta / ts)
        u = rng.uniform(40, 60, n)
        y = np.zeros(n)
        for k in range(d, n):
            y[k] = a * y[k - 1] + b * u[k - d]
        y += rng.normal(0, 0.1, n)
        if nan_indices:
            for idx in nan_indices:
                if 0 <= idx < n:
                    y[idx] = np.nan
        return u.tolist(), y.tolist()

    def test_nan_data_cleaned_and_identified(self):
        """含少量 NaN 的数据清洗后能成功辨识."""
        from app.services.tuning_identification import identify_from_history

        u, y = self._generate_fopdt(nan_indices=[50, 51, 52, 100, 101])
        result = identify_from_history(op=u, pv=y, ts=1.0)
        assert result.success, f"清洗后应辨识成功，reason={result.reason}"
        assert result.best_model is not None
        # 清洗统计应记录在证据中
        assert result.best_model.evidence is not None
        assert result.best_model.evidence.cleaning_stats is not None
        assert result.best_model.evidence.cleaning_stats["interpolated_points"] == 5

    def test_all_nan_rejected_with_clear_reason(self):
        """全 NaN 数据被拒绝，reason 说明清洗后不足."""
        from app.services.tuning_identification import identify_from_history

        u = [float("nan")] * 100
        y = [float("nan")] * 100
        result = identify_from_history(op=u, pv=y, ts=1.0)
        assert not result.success
        assert "清洗后有效数据不足" in result.reason or "数据不足" in result.reason

    def test_large_gap_takes_longest_segment_and_identifies(self):
        """大缺口取最长段后仍能辨识."""
        from app.services.tuning_identification import identify_from_history

        # 200 点，中间挖 20 连续 NaN（大缺口），取最长段（90 点）
        nan_idx = list(range(90, 110))
        u, y = self._generate_fopdt(n=200, nan_indices=nan_idx)
        result = identify_from_history(op=u, pv=y, ts=1.0)
        assert result.success, f"大缺口取段后应辨识成功，reason={result.reason}"
        assert result.best_model is not None
        stats = result.best_model.evidence.cleaning_stats
        assert stats is not None
        assert stats["dropped_points"] > 0
        assert stats["n_large_gaps"] == 1

    def test_too_few_after_cleaning_rejected(self):
        """清洗后不足 50 点被拒绝."""
        from app.services.tuning_identification import identify_from_history

        # 55 点，中间挖 30 连续 NaN，取最长段仅 25 点 < 50
        rng = np.random.default_rng(42)
        u = rng.uniform(40, 60, 55).tolist()
        y = list(range(55))
        for i in range(15, 45):
            y[i] = float("nan")
        result = identify_from_history(op=u, pv=y, ts=1.0)
        assert not result.success
        assert "清洗后有效数据不足" in result.reason

    def test_no_nan_unaffected(self):
        """无 NaN 数据不受清洗逻辑影响（stats 为 None）."""
        from app.services.tuning_identification import identify_from_history

        u, y = self._generate_fopdt()
        result = identify_from_history(op=u, pv=y, ts=1.0)
        assert result.success
        assert result.best_model is not None
        # 无坏点时 cleaning_stats 为 None
        assert result.best_model.evidence.cleaning_stats is None

    def test_sp_nan_also_cleaned(self):
        """SP 含 NaN 时同步清洗，不影响 CLIVC 启用."""
        from app.services.tuning_identification import identify_from_history

        u, y = self._generate_fopdt(nan_indices=[50, 51])
        sp = [450.0] * 100 + [455.0] * 100
        sp[50] = float("nan")
        sp[51] = float("nan")
        result = identify_from_history(op=u, pv=y, sp=sp, ts=1.0)
        assert result.success, f"SP NaN 清洗后应辨识成功，reason={result.reason}"
