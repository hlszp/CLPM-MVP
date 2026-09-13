"""G26：诊断链"数据不足"不得被渲染成"未检出"（S3 算法契约与量纲）。

事实来源与门禁口径：
- 设计文档 docs/MVP设计/07-诊断模块设计方案.md §7.2 级 0：
  「数据门禁不过（有效点数不足/可信度 E 级/断点 >30%）」——三条条件，
  与 gate.py 实现逐条一致（原判"只拒 E 级""无 MODE 门"两项**不成立**）；
- 编排层门禁 gate.MIN_DATA_POINTS = 32；各诊断算子自身门槛为 8/16/32
  （sensor.py 注明"编排层数据门禁已先行校验"）；
- 但椭圆法内核 assess_stiction_features（metric_calculator/stiction.py）
  要求 **MIN_POINTS = 100**。

缺陷（修复前实测）：32~99 点窗口能过编排层门禁，算子自身门槛（8/16/32）
也放行，于是调用内核后拿到 reason="insufficient_data"，而
_ellipse_kernel **丢弃该字段**，算子一律上报 detected=False / confidence=0.0 /
skip_reason=None —— 即把「数据不足」渲染成「未检出（无粘滞）」，
与同文件既有的"前提不成立"处置范式（no_limit_cycle 走证据分支）不一致。

修复：内核传播 reason，两个算子对 insufficient_data 返回
executed=False + skip_reason，与 no_limit_cycle（合法"未检出"）明确区分。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.diagnosis_operators.base import OperatorInput
from app.services.diagnosis_operators.gate import MIN_DATA_POINTS, evaluate_gate
from app.services.diagnosis_operators.stiction import (
    _choudhury_kernel,
    _ellipse_kernel,
    detect_choudhury,
    detect_ellipse,
)
from app.services.metric_calculator.stiction import MIN_POINTS as ELLIPSE_MIN_POINTS

#: 半周期 10 点（>= 极限环门的 8 点下限），周期 20 点
_OMEGA = 2.0 * math.pi / 20.0


def _signals(n: int) -> tuple[np.ndarray, np.ndarray]:
    t = np.arange(n)
    pv = np.sin(_OMEGA * t)
    op = pv + 0.4 * np.sign(np.cos(_OMEGA * t))
    return pv, op


def _input(n: int) -> OperatorInput:
    pv, op = _signals(n)
    return OperatorInput(
        loop_id="loop-g26",
        signals={"pv": pv, "op": op},
        timestamps=np.arange(n, dtype=float),
        meta={"sample_interval": 1.0},
    )


class TestSeamIsReal:
    """先把"缝"本身固化为断言，避免后人误以为门槛是一致的。"""

    def test_gate_threshold_is_below_ellipse_requirement(self) -> None:
        assert MIN_DATA_POINTS == 32
        assert ELLIPSE_MIN_POINTS == 100
        assert MIN_DATA_POINTS < ELLIPSE_MIN_POINTS

    def test_point_window_passes_gate_but_below_ellipse_requirement(self) -> None:
        """50 点窗口：过编排层门禁，但低于椭圆法 100 点要求。"""
        gate = evaluate_gate(
            point_count=50,
            expected_points=60,
            valid_rate=1.0,
            confidence_level="A",
        )
        assert gate.passed is True
        assert gate.point_count < ELLIPSE_MIN_POINTS

    def test_kernel_reason_is_insufficient_data(self) -> None:
        pv, op = _signals(50)
        assert _ellipse_kernel(pv, op, 1.0).get("reason") == "insufficient_data"


class TestInsufficientDataIsReportedAsSkip:
    """32~99 点：必须 executed=False + skip_reason，而非 detected=False。"""

    @pytest.mark.parametrize("n", [32, 50, 80, 99])
    def test_ellipse_skips_instead_of_reporting_not_detected(self, n: int) -> None:
        res = detect_ellipse(_input(n), {})
        assert res.executed is False, f"n={n} 应跳过而非执行"
        assert res.skip_reason is not None and "100" in res.skip_reason

    @pytest.mark.parametrize("n", [80, 96, 99])
    def test_choudhury_skips_instead_of_reporting_not_detected(self, n: int) -> None:
        """n<100 但过极限环门（半周期 >=8 点）：内核 reason 为 insufficient_data。"""
        pv, op = _signals(n)
        assert _choudhury_kernel(pv, op, {}).get("reason") == "insufficient_data"
        res = detect_choudhury(_input(n), {})
        assert res.executed is False
        assert res.skip_reason is not None and "100" in res.skip_reason


class TestSufficientDataStillExecutes:
    """回归护栏：点数达标后必须恢复执行，且两种 reason 不得混淆。"""

    @pytest.mark.parametrize("n", [100, 200])
    def test_ellipse_executes(self, n: int) -> None:
        res = detect_ellipse(_input(n), {})
        assert res.executed is True
        assert res.skip_reason is None

    def test_choudhury_executes(self) -> None:
        res = detect_choudhury(_input(200), {})
        assert res.executed is True
        assert res.skip_reason is None

    def test_no_limit_cycle_is_not_a_skip(self) -> None:
        """平稳（非极限环）是**合法结论**"无粘滞"，仍应 executed=True。

        这条锁住修复的边界：只把 insufficient_data 归为跳过，
        不得把 no_limit_cycle 也一并跳过。
        """
        n = 200
        flat = np.full(n, 50.0)
        inp = OperatorInput(
            loop_id="loop-g26",
            signals={"pv": flat, "op": flat},
            timestamps=np.arange(n, dtype=float),
            meta={"sample_interval": 1.0},
        )
        assert _ellipse_kernel(flat, flat, 1.0).get("reason") == "no_limit_cycle"

        res = detect_ellipse(inp, {})
        assert res.executed is True
        assert res.detected is False
        assert res.skip_reason is None
