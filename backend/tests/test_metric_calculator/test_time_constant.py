"""时间常数计算器单元测试（整改 F5）.

覆盖：
- 注册：CALCULATOR_REGISTRY / AUXILIARY_METRIC_CODES 含 time_constant
- 数据不足（<100 点）→ INCONCLUSIVE(insufficient_data)
- 激励不足（OP 恒定）→ INCONCLUSIVE(insufficient_excitation)
- 一阶滞后阶跃响应 → 输出正值 τ 估计（质心法，量级正确）
"""

from __future__ import annotations

import math

from app.services.metric_calculator import (
    AUXILIARY_METRIC_CODES,
    get_calculator,
)
from app.services.metric_calculator.time_constant import (
    MIN_POINTS,
    TimeConstantCalculator,
)

from .conftest import make_bundle


def _first_order_step_bundle(n: int = 300, tau: float = 30.0) -> object:
    """构造一阶滞后系统的 PRBS 激励响应 bundle（OP 多方向变化，PV 以 τ=30s 跟踪）。

    用 PRBS（伪随机二进制序列）而非单阶跃：check_excitation 要求 OP 有
    方向变化（单阶跃单调会被判为激励不足，贴合闭环评估窗实际情况）。
    """
    # 固定种子的确定性 PRBS（每 20 点切换一档，幅值 ±5）
    op = [5.0 if (i // 20) % 3 != 1 else -5.0 for i in range(n)]
    pv = [0.0] * n
    for i in range(1, n):
        # 一阶滞后离散递推：pv[i] = pv[i-1] + (op[i] - pv[i-1]) * (1 - exp(-1/τ))
        pv[i] = pv[i - 1] + (op[i] - pv[i - 1]) * (1 - math.exp(-1 / tau))
    return make_bundle(
        {"pv": pv, "op": op},
        metric_code="time_constant",
        tag_group="PVOP_HF",
    )


class TestTimeConstantRegistration:
    def test_registry_contains_time_constant(self) -> None:
        calc = get_calculator("time_constant")
        assert isinstance(calc, TimeConstantCalculator)
        assert calc is not None
        assert calc.metric_code == "time_constant"

    def test_auxiliary_codes_contains_time_constant(self) -> None:
        assert "time_constant" in AUXILIARY_METRIC_CODES


class TestTimeConstantCalculation:
    def test_insufficient_data_returns_inconclusive(self) -> None:
        bundle = make_bundle(
            {"pv": [1.0] * 10, "op": [1.0] * 10},
            metric_code="time_constant",
            tag_group="PVOP_HF",
        )
        result = TimeConstantCalculator().calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "insufficient_data"

    def test_constant_op_returns_insufficient_excitation(self) -> None:
        n = MIN_POINTS + 50
        bundle = make_bundle(
            {"pv": [50.0] * n, "op": [3.0] * n},
            metric_code="time_constant",
            tag_group="PVOP_HF",
        )
        result = TimeConstantCalculator().calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "insufficient_excitation"

    def test_first_order_step_response_estimates_positive_tau(self) -> None:
        bundle = _first_order_step_bundle(n=300, tau=30.0)
        result = TimeConstantCalculator().calculate(bundle)
        # 质心法对一阶系统的 τ 估计量级正确（宽容差：10~90s）
        assert result.value is not None
        assert 10.0 < result.value < 90.0
        assert result.details["sample_count"] == 300
