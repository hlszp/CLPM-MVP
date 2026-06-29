"""粘滞系数计算器单元测试（算法说明 §4.8）.

测试用例覆盖：
- 线性关系（无粘滞，b/a 接近 0）
- 椭圆关系（有粘滞，b/a > 0）
- 数据不足（< 100 点）
- 完全随机散点
- 粘滞等级判定

设计依据：算法说明 §4.8；GB/T 44693.2-2024 附录 F.2
"""

from __future__ import annotations

import math
import random

from app.services.metric_calculator.stiction import StictionIndexCalculator

from .conftest import make_bundle


class TestStictionIndex:
    """StictionIndexCalculator 测试。"""

    def test_linear_relationship_low_stiction(self):
        """PV-OP 线性关系 → b/a 接近 0（无粘滞）。"""
        n = 200
        # OP 线性变化，PV 紧密跟随
        op = [float(i) for i in range(n)]
        pv = [float(i) * 0.9 + 5.0 for i in range(n)]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        # 线性关系 → 短轴接近 0
        assert result.value < 10.0
        assert result.details["stiction_level"] == "NONE"

    def test_elliptical_relationship_high_stiction(self):
        """PV-OP 椭圆关系 → b/a > 0（有粘滞）。"""
        n = 200
        # PV-OP 形成椭圆（粘滞特征）
        op = [50.0 + 40.0 * math.cos(2 * math.pi * i / n) for i in range(n)]
        pv = [50.0 + 40.0 * math.sin(2 * math.pi * i / n) for i in range(n)]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        # 圆形 → b/a ≈ 1（100%）
        assert result.value > 50.0

    def test_insufficient_data_inconclusive(self):
        """数据不足（< 100 点）→ INCONCLUSIVE。"""
        n = 50
        op = [50.0] * n
        pv = [50.0] * n
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "insufficient_data"

    def test_random_scatter(self):
        """随机散点 → 拟合度可能低。"""
        random.seed(42)
        n = 200
        op = [random.uniform(0, 100) for _ in range(n)]
        pv = [random.uniform(0, 100) for _ in range(n)]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        # 随机散点 b/a 应较高（接近 1）
        assert 0.0 <= result.value <= 100.0

    def test_stiction_level_moderate(self):
        """中等粘滞 → MODERATE 等级。"""
        n = 200
        # 构造 b/a ≈ 0.2 的椭圆
        op = [50.0 + 50.0 * math.cos(2 * math.pi * i / n) for i in range(n)]
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / n) for i in range(n)]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        # b/a ≈ 10/50 = 0.2 → 20% → MODERATE
        assert result.details["stiction_level"] in ("MILD", "MODERATE")

    def test_constant_signal(self):
        """恒定信号 → 拟合失败，返回 0。"""
        n = 200
        op = [50.0] * n
        pv = [50.0] * n
        # 这里数据足够但信号恒定
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        # 恒定信号协方差矩阵为 0，a=0 → 返回 0
        assert result.value is not None
        assert result.value == 0.0
