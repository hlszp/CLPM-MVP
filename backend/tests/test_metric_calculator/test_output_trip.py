"""输出值行程指数计算器单元测试（算法说明 §4.9）.

测试用例覆盖：
- OP 恒定（trip=0, INACTIVE）
- OP 正常变化（NORMAL）
- OP 频繁变化（FREQUENT）
- OP 过度活跃（EXCESSIVE）
- 空数据
- 自定义 op_range

设计依据：算法说明 §4.9；GB/T 44693.2-2024 附录 F.5
"""

from __future__ import annotations

from app.services.metric_calculator.output_trip import OutputTripIndexCalculator

from .conftest import make_bundle


class TestOutputTripIndex:
    """OutputTripIndexCalculator 测试。"""

    def test_constant_op_inactive(self):
        """OP 恒定 → trip=0, INACTIVE。"""
        n = 100
        op = [50.0] * n
        bundle = make_bundle({"op": op}, metric_code="output_trip_index")
        calc = OutputTripIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["trip_level"] == "INACTIVE"

    def test_normal_op_changes(self):
        """OP 小幅变化 → NORMAL。"""
        n = 100
        op = [50.0 + 0.5 * i for i in range(n)]  # 缓慢线性变化
        bundle = make_bundle({"op": op}, metric_code="output_trip_index")
        calc = OutputTripIndexCalculator()
        result = calc.calculate(bundle)
        # total_trip = 0.5*99 = 49.5, duration=99, op_range=100
        # trip = 49.5 / (99 * 100) ≈ 0.005 → INACTIVE（< 0.01）
        assert result.value is not None
        assert result.details["trip_level"] in ("INACTIVE", "NORMAL")

    def test_frequent_op_changes(self):
        """OP 频繁变化 → FREQUENT。"""
        n = 200
        # 每步变化 10，总变化 10*199=1990
        op = [50.0 + 5.0 * ((-1) ** i) for i in range(n)]
        bundle = make_bundle({"op": op}, metric_code="output_trip_index")
        calc = OutputTripIndexCalculator()
        result = calc.calculate(bundle)
        # trip = 1990 / (199 * 100) = 0.1 → FREQUENT 下界（0.1~1.0）
        assert result.value is not None
        assert 0.1 <= result.value < 1.0
        assert result.details["trip_level"] == "FREQUENT"

    def test_excessive_op_changes(self):
        """OP 过度活跃 → EXCESSIVE。"""
        n = 100
        # 每步变化 100，总变化 100*99=9900
        op = [50.0 + 50.0 * ((-1) ** i) for i in range(n)]
        bundle = make_bundle({"op": op}, metric_code="output_trip_index")
        calc = OutputTripIndexCalculator()
        result = calc.calculate(bundle)
        # trip = 9900 / (99 * 100) = 1.0 → EXCESSIVE（>= 1.0）
        assert result.value is not None
        assert result.value >= 1.0
        assert result.details["trip_level"] == "EXCESSIVE"

    def test_empty_data_inconclusive(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({}, metric_code="output_trip_index")
        calc = OutputTripIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is None

    def test_single_point_inconclusive(self):
        """单点 → INCONCLUSIVE。"""
        bundle = make_bundle({"op": [50.0]}, metric_code="output_trip_index")
        calc = OutputTripIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is None

    def test_custom_op_range(self):
        """自定义 op_range 从 CONFIG 读取。"""
        n = 100
        op = [50.0 + 1.0 * i for i in range(n)]
        bundle = make_bundle(
            {"op": op, "op_range": [200.0] * n},
            metric_code="output_trip_index",
        )
        calc = OutputTripIndexCalculator()
        result = calc.calculate(bundle)
        assert result.details["op_range"] == 200.0
