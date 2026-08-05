"""粘滞系数计算器单元测试（算法说明 §4.8，Phase 6 G1 增强）.

测试用例覆盖：
- 极限环门控：平稳/噪声/恒定回路 → value=0 (NONE, reason=no_limit_cycle)
- θ 补偿：FOPDT 纯滞后（θ=30s）无粘滞 → 补偿后 St≈0 不误报 SEVERE
- θ 补偿：粘滑（死区跳变）注入 S=20 + θ=30s → 补偿后 St≈8.2（手算）
- 纯相位滞后椭圆（正交/倾斜正弦对）→ 补偿后塌陷为直线，St≈0
- 圆团散点（不可通约频率）→ INCONCLUSIVE(low_correlation)
- 数据不足（< 100 点）
- 振荡下的紧密线性跟踪 → St≈0（无粘滞）

数值期望值均手算核实（推导见各用例注释），禁止用实现输出反推。

设计依据：算法说明 §4.8；GB/T 44693.2-2024 附录 F.2
"""

from __future__ import annotations

import math
import random

from app.services.metric_calculator.stiction import StictionIndexCalculator

from .conftest import make_bundle


def _sin_series(n: int, amp: float, period: float, phase_deg: float = 0.0, offset: float = 50.0):
    """生成 amp·sin(2πt/period + phase) + offset 序列."""
    return [
        offset + amp * math.sin(2 * math.pi * i / period + math.radians(phase_deg))
        for i in range(n)
    ]


class TestStictionIndex:
    """StictionIndexCalculator 测试。"""

    def test_linear_relationship_low_stiction(self):
        """振荡段 PV 紧密线性跟踪 OP（同相）→ b/a≈0（无粘滞）。

        4 秒…改为正弦振荡（周期 50s，8 周期）满足极限环门控；
        pv = 0.9·op + 5 完全线性 → 协方差矩阵秩 1 → λmin=0 → St=0。
        """
        n = 400
        op = _sin_series(n, 40.0, 50.0)
        pv = [0.9 * o + 5.0 for o in op]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert result.value < 10.0
        assert result.details["stiction_level"] == "NONE"

    def test_phase_lag_circle_collapses_after_compensation(self):
        """正交正弦对（纯 T/4 相位滞后）→ θ 补偿后塌陷为直线，St≈0。

        op=40cos、pv=40sin（周期 50s，4 周期）：散点是正圆，但物理上
        这是纯滞后对而非"无相关圆团"。互相关在 θ̂=T/4=12.5 采样点处取峰
        （|r|=1），离散 argmax 取 13；补偿后残余相位 0.5 采样点=3.6°，
        b/a = tan(1.8°) ≈ 0.031 → St≈3.1 < 5（NONE），不再误报 SEVERE。
        """
        n = 200
        op = [50.0 + 40.0 * math.cos(2 * math.pi * i / 50.0) for i in range(n)]
        pv = [50.0 + 40.0 * math.sin(2 * math.pi * i / 50.0) for i in range(n)]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert result.value < 5.0
        assert result.details["stiction_level"] == "NONE"
        assert result.details["theta_compensated"] is True
        assert 12.0 <= result.details["theta_hat_seconds"] <= 14.0

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
        """随机散点（白噪声）→ 无极限环（平均半周期≈2 < 8）→ value=0 (NONE)。"""
        random.seed(42)
        n = 200
        op = [random.uniform(0, 100) for _ in range(n)]
        pv = [random.uniform(0, 100) for _ in range(n)]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["reason"] == "no_limit_cycle"
        assert result.details["stiction_level"] == "NONE"

    def test_phase_lag_ellipse_compensated_to_line(self):
        """倾斜椭圆（0.8cos+0.3sin）→ 纯相位滞后，θ 补偿后 St≈0。

        pv = 32cos+12sin = 34.18·cos(t-20.56°)，即 op=40cos 的纯滞后缩放版
        （旧实现把该相位滞后椭圆 b/a≈0.18 误报为 MODERATE 粘滞）。
        互相关峰值滞后 θ̂：tanφ=12/32 → φ=20.56°，周期 50s（7.2°/点）
        → 连续峰值 2.86 采样点；np.correlate 截断（大滞后重叠项变少）
        使离散 argmax 落在 θ̂=2，残余相位 δ=20.56°-14.4°=6.16°，
        b/a ≈ tan(3.08°) ≈ 0.054 → St≈5.3（对比未补偿 St≈17.9 显著塌陷）。
        """
        n = 200
        op = [50.0 + 40.0 * math.cos(2 * math.pi * i / 50.0) for i in range(n)]
        pv = [
            50.0 + 32.0 * math.cos(2 * math.pi * i / 50.0) + 12.0 * math.sin(2 * math.pi * i / 50.0)
            for i in range(n)
        ]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        # 补偿后 St 从 ~17.9（未补偿相位滞后椭圆）塌陷到个位数
        assert result.value < 10.0
        assert result.details["stiction_level"] in ("NONE", "MILD")
        assert result.details["theta_compensated"] is True
        assert 2.0 <= result.details["theta_hat_seconds"] <= 4.0
        assert result.details["fitting_score"] >= 0.95

    def test_constant_signal(self):
        """恒定信号 → 无零交叉 → value=0 (NONE, no_limit_cycle)。"""
        n = 200
        op = [50.0] * n
        pv = [50.0] * n
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["reason"] == "no_limit_cycle"
        assert result.details["stiction_level"] == "NONE"
        assert result.details["zero_crossings"] == 0

    def test_stationary_loop_no_limit_cycle(self):
        """平稳回路（小幅测量噪声，无振荡）→ value=0 (NONE, no_limit_cycle)。

        平稳回路无粘滞故障：去均值白噪声每 ~2 点一次伪穿越，
        平均半周期≈2 < 8 → 门控拦截，返回 0（无粘滞）而非 INCONCLUSIVE。
        """
        random.seed(7)
        n = 1200
        op = [50.0 + random.uniform(-0.5, 0.5) for _ in range(n)]
        pv = [50.0 + random.uniform(-0.5, 0.5) for _ in range(n)]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["reason"] == "no_limit_cycle"
        assert result.details["stiction_level"] == "NONE"

    def test_fopdt_pure_lag_no_stiction(self):
        """FOPDT 纯滞后（θ=30s, τ=10s）无粘滞 → 补偿后 St≈0，不误报 SEVERE。

        手算：T=120s，ωτ=2π·10/120=0.5236 → 幅值比 1/√(1+(ωτ)²)=0.8859、
        相位滞后 atan(ωτ)=27.63°；总相位 φ=ωθ+27.63°=117.63°。
        互相关峰值滞后 θ̂=117.63/3=39.21 → 离散 argmax=39（残余 0.63°）；
        补偿后两等幅正弦残余相位 δ=0.63°：b/a=tan(0.317°)=0.0055 → St≈0.55。
        未补偿拟合度 r²=cos²(117.63°)=0.2139（<0.5 本会被门控）。
        """
        n = 1200
        period = 120.0
        amp_pv = 40.0 / math.sqrt(1 + (2 * math.pi * 10 / period) ** 2)  # 35.44
        phase = -(90.0 + math.degrees(math.atan(2 * math.pi * 10 / period)))  # -117.63°
        op = _sin_series(n, 40.0, period)
        pv = _sin_series(n, amp_pv, period, phase_deg=phase)
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert result.value < 5.0
        assert result.details["stiction_level"] == "NONE"
        assert result.details["theta_compensated"] is True
        assert 37.0 <= result.details["theta_hat_seconds"] <= 41.0
        assert result.details["fitting_score"] >= 0.99
        assert result.details["fitting_score_uncompensated"] < 0.3

    def test_deadband_stiction_theta_compensation(self):
        """粘滑（死区跳变 S=20）+ θ=30s → 补偿后 St≈8.2（手算），未补偿 r²≈0。

        构造：op = 40·sin(ωt)，pv = 40·sin(ω(t-30)) + 20·sign(sin(ω(t-30)))
        （T=120s，n=1200，10 个整周期）。死区跳变是 Kano 粘滞的 PV 跳变特征。

        手算（整周期矩，x=40sin，y=x+20·sign(x)，E[x²]=800，E|x|=80/π=25.46）：
        - 互相关 c(l) ∝ (800+509.3)·cos(ω(l-30)) → 峰值恰在 θ̂=30（无偏）
        - 峰值相关 r = 1309.3/√(800·2218.6) = 0.983 ≥ 0.3 → 补偿生效
        - 对齐后 cov=800+20·E|x|=1309.3，var_y=800+40·E|x|+400=2218.6
        - r² = 1309.3²/(800·2218.6) = 0.9659
        - λ∓ = (3018.6 ∓ √(3018.6²-4·60616))/2 → λ-=20.2, λ+=2998.4
        - St = √(20.2/2998.4)·100 ≈ 8.2（MILD）
        - 未补偿（δ=90°）：cov=0 → r²≈0（显著偏差，本会被门控拦截）
        """
        n = 1200
        period = 120.0
        theta = 30
        op = _sin_series(n, 40.0, period)
        pv = []
        for i in range(n):
            u = 2 * math.pi * (i - theta) / period
            s = math.sin(u)
            pv.append(50.0 + 40.0 * s + 20.0 * (1.0 if s > 0 else (-1.0 if s < 0 else 0.0)))
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert 6.0 <= result.value <= 11.0
        assert result.details["stiction_level"] == "MILD"
        assert result.details["theta_compensated"] is True
        assert 28.0 <= result.details["theta_hat_seconds"] <= 32.0
        assert result.details["fitting_score"] >= 0.95
        assert result.details["fitting_score_uncompensated"] < 0.2

    def test_incommensurate_scatter_low_correlation(self):
        """圆团散点（不可通约频率正弦对）→ 无滞后结构 → low_correlation。

        pv 周期 120s（振荡门控通过：20 次零交叉、半周期 60、IAE 相似率 1.0），
        op 周期 77s：两信号不可通约，互相关在任何滞后处均不显著
        （|r| < 0.3 → 回退不补偿），瞬时配对 r²≈0 → 圆团无主导方向，
        R²<0.5 门控拦截，不误报 SEVERE。
        """
        n = 1200
        pv = _sin_series(n, 40.0, 120.0)
        op = _sin_series(n, 40.0, 77.0)
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")
        calc = StictionIndexCalculator()
        result = calc.calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "low_correlation"
        assert result.details["stiction_level"] == "NONE"
        assert result.details["theta_compensated"] is False
