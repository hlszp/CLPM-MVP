"""附录 B.5 稳定率 公式级验证（任务 G2）.

公式事实来源：算法说明 §4.3（对齐 GB/T 44693.2-2024 附录 B.5）：
    S = 1/e^(σ/(0.05·U)) × (1 - Osc) × 100%
    σ = sqrt((1/(n-1)) Σ(E_i - Ē)²)

σ 的 ddof 口径决策（本套件按此口径手算）：
    设计文档 §4.3.2 数学模型行内公式写作 1/n（有偏），但 §4.3.4 计算步骤 7、
    v2.1 变更记录④及 FDS v5.1 均明确"分母 n-1（无偏估计）"；实现
    stability.py 使用 np.std(ddof=1)。裁决：以 v2.1 修正条文（n-1）为准，
    §4.3.2 行内公式为文档未同步更新的遗留表述（报告中记录）。
"""

from __future__ import annotations

import math

import pytest

from app.services.metric_calculator.stability import StabilityRateCalculator

from .g2_helpers import make_bundle, make_metric_result


def _calc(pv, sp, osc: float | None = None, extra_signals: dict | None = None):
    signals = {"pv": pv, "sp": sp}
    if extra_signals:
        signals.update(extra_signals)
    bundle = make_bundle(signals, metric_code="stability_rate")
    calc = StabilityRateCalculator()
    if osc is not None:
        calc.with_dependencies({"oscillation_rate": make_metric_result("oscillation_rate", osc)})
    return calc.calculate(bundle)


class TestB5StabilityRate:
    """附录 B.5 稳定率：已知 σ 序列验证指数公式值."""

    def test_known_sigma_exponential_value(self):
        """附录 B.5：E=[+2,-2,+2,-2]，σ(ddof=1)=sqrt(16/3)，U=100 → S≈63.01.

        Ē=0，Σ(E-Ē)²=16，n=4：
        σ = sqrt(16/3) = 2.3094010768（无偏，n-1）
        σ/(0.05·U) = 2.3094010768/5 = 0.4618802154
        S = 100·e^(-0.4618802154) = 63.0108... → 63.01

        ddof 口径锚点：若按有偏（n）σ=2.0 → S=100·e^(-0.4)=67.03，
        本用例显式断言结果 ≠ 67.03，固化 n-1 口径决策。
        """
        result = _calc([52.0, 48.0, 52.0, 48.0], [50.0] * 4)

        expected = round(100.0 * math.exp(-math.sqrt(16.0 / 3.0) / 5.0), 2)
        assert expected == 63.01  # 手算核实锚点（禁止实现输出反推）
        biased = round(100.0 * math.exp(-2.0 / 5.0), 2)  # ddof=0 口径
        assert biased == 67.03
        assert result.value == expected
        assert result.value != biased  # 固化 ddof=1 口径
        assert result.details["std_error"] == pytest.approx(math.sqrt(16.0 / 3.0), abs=1e-4)

    def test_zero_sigma_full_score(self):
        """附录 B.5：σ=0（PV 恒等于 SP）→ S = 100（指数公式零点）."""
        result = _calc([50.0] * 10, [50.0] * 10)
        assert result.value == 100.0

    def test_oscillation_correction_factor(self):
        """附录 B.5：振荡修正 (1-Osc)：σ 同上 + Osc=40% → S = 63.0108×0.6 ≈ 37.81."""
        result = _calc([52.0, 48.0, 52.0, 48.0], [50.0] * 4, osc=40.0)

        expected = round(100.0 * math.exp(-math.sqrt(16.0 / 3.0) / 5.0) * 0.6, 2)
        assert expected == 37.81  # 手算核实锚点
        assert result.value == expected
        assert result.details["osc_factor"] == pytest.approx(0.6, abs=1e-4)

    def test_full_oscillation_scores_zero(self):
        """附录 B.5：Osc=100% → (1-Osc)=0 → S = 0（§4.3.4 步骤 10）."""
        result = _calc([52.0, 48.0, 52.0, 48.0], [50.0] * 4, osc=100.0)
        assert result.value == 0.0
        assert result.details["reason"] == "osc_too_high"

    def test_pv_range_applied(self):
        """附录 B.5：量程 U 进入 0.05·U 归一化基准.

        同上 σ 序列，U=200（CONFIG pv_range=200，标量注入形式）：
        σ/(0.05·U) = 2.3094010768/10 = 0.2309401077
        S = 100·e^(-0.2309401077) = 79.3757... → 79.38
        """
        signals = {"pv": [52.0, 48.0, 52.0, 48.0], "sp": [50.0] * 4}
        bundle = make_bundle(signals, metric_code="stability_rate")
        # stability._read_pv_range 直接 float(signals["pv_range"])，接受标量形式
        bundle.data_block.signals["pv_range"] = 200.0
        calc = StabilityRateCalculator()
        result = calc.calculate(bundle)

        expected = round(100.0 * math.exp(-math.sqrt(16.0 / 3.0) / 10.0), 2)
        assert expected == 79.38  # 手算核实锚点
        assert result.value == expected
        assert result.details["pv_range"] == pytest.approx(200.0)

    def test_pv_range_list_form_unwrapped(self):
        """附录 B.5：CONFIG 列表形式 pv_range=[200.0] 应被解包（契约：signals 值统一为列表）.

        偏差 D-2 已修复：stability._read_pv_range 改用 _read_config_scalar 解包，
        列表形式与标量注入一致，S≈79.38。
        """
        result = _calc(
            [52.0, 48.0, 52.0, 48.0],
            [50.0] * 4,
            extra_signals={"pv_range": [200.0]},
        )

        expected = round(100.0 * math.exp(-math.sqrt(16.0 / 3.0) / 10.0), 2)
        assert result.value == expected
        assert result.details["pv_range"] == pytest.approx(200.0)

    def test_insufficient_data_inconclusive(self):
        """附录 B.5：n<2 → INCONCLUSIVE（§4.3.4 步骤 2）."""
        result = _calc([52.0], [50.0])
        assert result.value is None
        assert result.confidence_level == "E"
