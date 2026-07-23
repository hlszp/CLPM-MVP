"""Phase 1 新增指标计算器单元测试（Task 3 + Task 4）.

TDD：本测试文件在计算器实现前编写，import 将失败直至 Task 3/4 完成。
测试全绿即表明计算器实现满足输入/输出契约。

覆盖的 14 个计算器：
- InstrumentFaultRateCalculator（仪表故障率，复用 outlier_reasons）
- PvMean/PvStd/SpMean/SpStd/OpMean/OpStd（PV/SP/OP 统计）
- ErrorMean/ErrorStd（偏差统计）
- ValveLinearity/ValveNonlinearity/ValveOperatingRange（阀门诊断）
- SetpointCrossingCount/OscillationAmplitude（穿越次数+振荡幅值）

边界条件覆盖：
- 空数据 / 不足最小点数 → INCONCLUSIVE (value=None, confidence=E)
- 恒定值 / 零方差 / 完全相关 / 无相关
- None 值过滤（pair 计算器必须过滤含 None 的配对，否则 TypeError）
- 多原因码叠加（一点多故障，fault_point_count 不重复计数）
- 非故障原因码忽略（SPIKE/NaN/QC_BAD/HF_NOISE/TS_ANOMALY 不计入仪表故障率）

契约审查发现（测试已覆盖）：
- P0-1: valve_linearity/nonlinearity skeleton 对 xs/ys 分别过滤 None → 长度不匹配
        正确做法：过滤"任一为 None"的整对
- P0-2: setpoint_crossing_count/oscillation_amplitude skeleton 未过滤 None pair → TypeError
        正确做法：先过滤含 None 的 pair 再计算
- P1: _make_result 统一 round(2)，stat/valve 列精度 3-4 位（Phase 1 可接受，不覆盖）
- P2: instrument_fault_rate 用全量 point_count 做分母，可信度用 mask valid_rate
- P2: setpoint_crossing_count 严格符号变化法（diffs[i-1]*diffs[i] < 0），diff=0 不算穿越
- P2: 最小点数阈值 stat=2，valve/crossing=3
"""

from __future__ import annotations

import math
import random
import statistics as stats

import pytest

from app.contracts.data_types import OutlierReason
from app.services.metric_calculator.instrument_fault import InstrumentFaultRateCalculator
from app.services.metric_calculator.setpoint_crossing import (
    OscillationAmplitudeCalculator,
    SetpointCrossingCountCalculator,
)
from app.services.metric_calculator.statistics import (
    ErrorMeanCalculator,
    ErrorStdCalculator,
    OpMeanCalculator,
    OpStdCalculator,
    PvMeanCalculator,
    PvStdCalculator,
    SpMeanCalculator,
    SpStdCalculator,
)
from app.services.metric_calculator.valve_diagnosis import (
    ValveLinearityCalculator,
    ValveNonlinearityCalculator,
    ValveOperatingRangeCalculator,
)

from .conftest import make_bundle

# 缩写
OR = OutlierReason

# ---------------------------------------------------------------------------
# 参数化：均值/标准差计算器共享同一模式，按 tag 区分
# ---------------------------------------------------------------------------

MEAN_CALCULATORS = [
    pytest.param(PvMeanCalculator, "pv", "pv_mean", id="pv_mean"),
    pytest.param(SpMeanCalculator, "sp", "sp_mean", id="sp_mean"),
    pytest.param(OpMeanCalculator, "op", "op_mean", id="op_mean"),
]

STD_CALCULATORS = [
    pytest.param(PvStdCalculator, "pv", "pv_std", id="pv_std"),
    pytest.param(SpStdCalculator, "sp", "sp_std", id="sp_std"),
    pytest.param(OpStdCalculator, "op", "op_std", id="op_std"),
]


# ===========================================================================
# Task 3: InstrumentFaultRateCalculator
# ===========================================================================


class TestInstrumentFaultRate:
    """仪表故障率计算器测试（复用 DataBlock.outlier_reasons）。"""

    def test_zero_fault(self):
        """全量点无故障原因码 → fault_rate=0%。"""
        n = 10
        bundle = make_bundle(
            {"pv": [50.0] * n},
            outlier_reasons={"pv": [[] for _ in range(n)]},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 0.0
        assert result.details["fault_point_count"] == 0
        assert result.details["sample_count"] == n

    def test_all_frozen(self):
        """全部冻结 → fault_rate=100%，freeze_count=n。"""
        n = 10
        bundle = make_bundle(
            {"pv": [50.0] * n},
            outlier_reasons={"pv": [[OR.FROZEN.value]] * n},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 100.0
        assert result.details["freeze_count"] == n
        assert result.details["fault_point_count"] == n

    def test_all_out_of_range(self):
        """全部超量程 → fault_rate=100%，overrange_count=n。"""
        n = 10
        bundle = make_bundle(
            {"pv": [50.0] * n},
            outlier_reasons={"pv": [[OR.OUT_OF_RANGE.value]] * n},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 100.0
        assert result.details["overrange_count"] == n

    def test_all_jump(self):
        """全部跳变 → fault_rate=100%，mutation_count=n。"""
        n = 10
        bundle = make_bundle(
            {"pv": [50.0] * n},
            outlier_reasons={"pv": [[OR.JUMP.value]] * n},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 100.0
        assert result.details["mutation_count"] == n

    def test_mixed_faults(self):
        """3/10 点分别有超限/跳变/冻结 → fault_rate=30%。"""
        reasons_pv = [
            [OR.OUT_OF_RANGE.value],
            [],
            [OR.JUMP.value],
            [],
            [],
            [OR.FROZEN.value],
            [],
            [],
            [],
            [],
        ]
        bundle = make_bundle(
            {"pv": list(range(10))},
            outlier_reasons={"pv": reasons_pv},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 30.0
        assert result.details["overrange_count"] == 1
        assert result.details["mutation_count"] == 1
        assert result.details["freeze_count"] == 1
        assert result.details["fault_point_count"] == 3

    def test_multiple_reasons_per_point(self):
        """一点叠加 FROZEN+JUMP → 各 count 各+1，fault_point_count 只+1。"""
        reasons_pv = [
            [],
            [],
            [OR.FROZEN.value, OR.JUMP.value],
            [],
            [],
        ]
        bundle = make_bundle(
            {"pv": [50.0] * 5},
            outlier_reasons={"pv": reasons_pv},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 20.0  # 1/5
        assert result.details["freeze_count"] == 1
        assert result.details["mutation_count"] == 1
        assert result.details["fault_point_count"] == 1  # 不重复计数

    def test_non_fault_reasons_ignored(self):
        """SPIKE/NaN/QC_BAD/HF_NOISE/TS_ANOMALY 不计入仪表故障率。

        仅 OUT_OF_RANGE/FROZEN/JUMP 三类为仪表故障（HiaMonitor 超限/冻结/突变）。
        """
        reasons_pv = [
            [OR.SPIKE.value],
            [OR.NAN.value],
            [OR.QC_BAD.value],
            [OR.HF_NOISE.value],
            [OR.TS_ANOMALY.value],
            [],
        ]
        bundle = make_bundle(
            {"pv": [50.0] * 6},
            outlier_reasons={"pv": reasons_pv},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 0.0
        assert result.details["fault_point_count"] == 0

    def test_empty_block_inconclusive(self):
        """空数据块 → INCONCLUSIVE (value=None, confidence=E)。"""
        bundle = make_bundle({"pv": []}, metric_code="instrument_fault_rate")
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value is None
        assert result.confidence_level == "E"

    def test_no_outlier_reasons_key(self):
        """outlier_reasons 无 "pv" 键 → fault_rate=0%（无故障）。"""
        n = 10
        bundle = make_bundle(
            {"pv": [50.0] * n},
            outlier_reasons={},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 0.0
        assert result.details["fault_point_count"] == 0

    def test_short_reasons_padded(self):
        """outlier_reasons["pv"] 长度 < point_count → 自动补齐，不崩溃。"""
        n = 10
        bundle = make_bundle(
            {"pv": [50.0] * n},
            outlier_reasons={"pv": [[OR.FROZEN.value]]},  # 只有 1 个点的原因码
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 10.0  # 1/10
        assert result.details["freeze_count"] == 1

    def test_confidence_reflects_valid_rate(self):
        """故障点 pv_valid=False → 排除出 mask → valid_rate 降低 → 可信度降级。

        instrument_fault_rate 用全量 point_count 算故障率（3/10=30%），
        但可信度用 mask valid_rate（7/10=0.7 → C 级）。
        """
        n = 10
        validity = {"pv_valid": [False, False, False] + [True] * 7}
        bundle = make_bundle(
            {"pv": [50.0] * n},
            validity=validity,
            mask_expression="pv_valid",
            outlier_reasons={"pv": [[OR.FROZEN.value]] * 3 + [[]] * 7},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 30.0  # 3/10 全量点
        assert result.confidence_level == "C"  # valid_rate=0.7


# ===========================================================================
# Task 4.1: 统计指标（均值）— PvMean / SpMean / OpMean
# ===========================================================================


@pytest.mark.parametrize("calc_cls,tag,code", MEAN_CALCULATORS)
class TestMeanCalculators:
    """PV/SP/OP 均值计算器测试（参数化，同一模式）。"""

    def test_normal_mean(self, calc_cls, tag, code):
        """已知值 → 正确均值。"""
        bundle = make_bundle(
            {tag: [10.0, 20.0, 30.0]},
            metric_code=code,
        )
        result = calc_cls().calculate(bundle)
        assert result.value == 20.0
        assert result.details["n"] == 3

    def test_constant_values(self, calc_cls, tag, code):
        """恒定值 → mean=value。"""
        bundle = make_bundle(
            {tag: [50.0, 50.0, 50.0, 50.0]},
            metric_code=code,
        )
        result = calc_cls().calculate(bundle)
        assert result.value == 50.0

    def test_insufficient_points(self, calc_cls, tag, code):
        """仅 1 点（< 2）→ INCONCLUSIVE。"""
        bundle = make_bundle({tag: [50.0]}, metric_code=code)
        result = calc_cls().calculate(bundle)
        assert result.value is None
        assert result.confidence_level == "E"

    def test_empty_data(self, calc_cls, tag, code):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({tag: []}, metric_code=code)
        result = calc_cls().calculate(bundle)
        assert result.value is None

    def test_none_values_filtered(self, calc_cls, tag, code):
        """信号含 None → 过滤后计算（2 点足够）。"""
        bundle = make_bundle(
            {tag: [10.0, None, 30.0]},
            metric_code=code,
        )
        result = calc_cls().calculate(bundle)
        assert result.value == 20.0  # (10+30)/2
        assert result.details["n"] == 2

    def test_missing_tag(self, calc_cls, tag, code):
        """信号字典无该 tag → INCONCLUSIVE。"""
        bundle = make_bundle({"other": [1.0, 2.0]}, metric_code=code)
        result = calc_cls().calculate(bundle)
        assert result.value is None


# ===========================================================================
# Task 4.1: 统计指标（标准差）— PvStd / SpStd / OpStd
# ===========================================================================


@pytest.mark.parametrize("calc_cls,tag,code", STD_CALCULATORS)
class TestStdCalculators:
    """PV/SP/OP 标准差计算器测试（参数化，用总体标准差 pstdev）。"""

    def test_normal_std(self, calc_cls, tag, code):
        """已知值 → 正确总体标准差。"""
        vals = [10.0, 20.0, 30.0]
        bundle = make_bundle({tag: vals}, metric_code=code)
        result = calc_cls().calculate(bundle)
        expected = round(stats.pstdev(vals), 2)
        assert result.value == expected

    def test_constant_values(self, calc_cls, tag, code):
        """恒定值 → std=0。"""
        bundle = make_bundle(
            {tag: [50.0, 50.0, 50.0]},
            metric_code=code,
        )
        result = calc_cls().calculate(bundle)
        assert result.value == 0.0

    def test_insufficient_points(self, calc_cls, tag, code):
        """仅 1 点（< 2）→ INCONCLUSIVE。"""
        bundle = make_bundle({tag: [50.0]}, metric_code=code)
        result = calc_cls().calculate(bundle)
        assert result.value is None

    def test_empty_data(self, calc_cls, tag, code):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({tag: []}, metric_code=code)
        result = calc_cls().calculate(bundle)
        assert result.value is None

    def test_none_values_filtered(self, calc_cls, tag, code):
        """信号含 None → 过滤后计算。"""
        bundle = make_bundle(
            {tag: [10.0, None, 30.0]},
            metric_code=code,
        )
        result = calc_cls().calculate(bundle)
        expected = round(stats.pstdev([10.0, 30.0]), 2)
        assert result.value == expected
        assert result.details["n"] == 2


# ===========================================================================
# Task 4.1: 偏差统计 — ErrorMean / ErrorStd
# ===========================================================================


class TestErrorMean:
    """偏差均值计算器测试（E = PV - SP）。"""

    def test_normal_error_mean(self):
        """pv=[55,55], sp=[50,50] → error_mean=5。"""
        bundle = make_bundle(
            {"pv": [55.0, 55.0], "sp": [50.0, 50.0]},
            metric_code="error_mean",
        )
        result = ErrorMeanCalculator().calculate(bundle)
        assert result.value == 5.0
        assert result.details["n"] == 2

    def test_negative_error(self):
        """PV < SP → 负偏差。"""
        bundle = make_bundle(
            {"pv": [45.0, 45.0], "sp": [50.0, 50.0]},
            metric_code="error_mean",
        )
        result = ErrorMeanCalculator().calculate(bundle)
        assert result.value == -5.0

    def test_insufficient_points(self):
        """仅 1 对 → INCONCLUSIVE。"""
        bundle = make_bundle(
            {"pv": [55.0], "sp": [50.0]},
            metric_code="error_mean",
        )
        result = ErrorMeanCalculator().calculate(bundle)
        assert result.value is None

    def test_empty_data(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({"pv": [], "sp": []}, metric_code="error_mean")
        result = ErrorMeanCalculator().calculate(bundle)
        assert result.value is None

    def test_none_pair_filtered(self):
        """P0：pair 含 None → 过滤整对，不 TypeError。"""
        bundle = make_bundle(
            {"pv": [55.0, None, 55.0], "sp": [50.0, 50.0, 50.0]},
            metric_code="error_mean",
        )
        result = ErrorMeanCalculator().calculate(bundle)
        assert result.value == 5.0  # (5+5)/2，中间 None pair 过滤
        assert result.details["n"] == 2


class TestErrorStd:
    """偏差标准差计算器测试。"""

    def test_normal_error_std(self):
        """errors=[0, 10] → pstdev=5。"""
        bundle = make_bundle(
            {"pv": [50.0, 60.0], "sp": [50.0, 50.0]},
            metric_code="error_std",
        )
        result = ErrorStdCalculator().calculate(bundle)
        assert result.value == 5.0  # pstdev([0, 10]) = 5.0

    def test_constant_error(self):
        """恒定偏差 → std=0。"""
        bundle = make_bundle(
            {"pv": [55.0, 55.0, 55.0], "sp": [50.0, 50.0, 50.0]},
            metric_code="error_std",
        )
        result = ErrorStdCalculator().calculate(bundle)
        assert result.value == 0.0

    def test_insufficient_points(self):
        """仅 1 对 → INCONCLUSIVE。"""
        bundle = make_bundle(
            {"pv": [55.0], "sp": [50.0]},
            metric_code="error_std",
        )
        result = ErrorStdCalculator().calculate(bundle)
        assert result.value is None

    def test_none_pair_filtered(self):
        """P0：pair 含 None → 过滤整对。"""
        bundle = make_bundle(
            {"pv": [50.0, None, 60.0], "sp": [50.0, 50.0, 50.0]},
            metric_code="error_std",
        )
        result = ErrorStdCalculator().calculate(bundle)
        # 过滤后 errors=[0, 10], pstdev=5.0
        assert result.value == 5.0
        assert result.details["n"] == 2


# ===========================================================================
# Task 4.2: 阀门诊断 — ValveLinearity / ValveNonlinearity / ValveOperatingRange
# ===========================================================================


class TestValveLinearity:
    """阀门线性度计算器测试（PV-OP 皮尔逊相关系数绝对值）。"""

    def test_perfect_positive_correlation(self):
        """PV = 2*OP + 1 → r=1，linearity=1。"""
        op = [10.0, 20.0, 30.0, 40.0, 50.0]
        pv = [2.0 * o + 1.0 for o in op]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="valve_linearity")
        result = ValveLinearityCalculator().calculate(bundle)
        assert result.value == 1.0

    def test_perfect_negative_correlation(self):
        """PV = -2*OP + 100 → r=-1，linearity=abs(-1)=1。"""
        op = [10.0, 20.0, 30.0, 40.0, 50.0]
        pv = [-2.0 * o + 100.0 for o in op]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="valve_linearity")
        result = ValveLinearityCalculator().calculate(bundle)
        assert result.value == 1.0

    def test_no_correlation(self):
        """随机 PV/OP → linearity ≈ 0。"""
        random.seed(123)
        n = 100
        op = [random.uniform(0, 100) for _ in range(n)]
        pv = [random.uniform(0, 100) for _ in range(n)]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="valve_linearity")
        result = ValveLinearityCalculator().calculate(bundle)
        assert result.value is not None
        assert result.value < 0.3  # 随机数据相关性低

    def test_insufficient_points(self):
        """仅 2 点（< 3）→ INCONCLUSIVE。"""
        bundle = make_bundle(
            {"pv": [10.0, 20.0], "op": [1.0, 2.0]},
            metric_code="valve_linearity",
        )
        result = ValveLinearityCalculator().calculate(bundle)
        assert result.value is None

    def test_empty_data(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({"pv": [], "op": []}, metric_code="valve_linearity")
        result = ValveLinearityCalculator().calculate(bundle)
        assert result.value is None

    def test_zero_variance(self):
        """PV 恒定（零方差）→ r=None → linearity=0。"""
        bundle = make_bundle(
            {"pv": [50.0, 50.0, 50.0, 50.0, 50.0], "op": [10.0, 20.0, 30.0, 40.0, 50.0]},
            metric_code="valve_linearity",
        )
        result = ValveLinearityCalculator().calculate(bundle)
        assert result.value == 0.0

    def test_none_pair_filtered(self):
        """P0：pair 含 None → 过滤整对，不长度不匹配。

        pv=[50, None, 70, 80, 90], op=[10, 20, None, 40, 50]
        正确：过滤后 [(50,10),(80,40),(90,50)] → 3 对 → 可计算
        错误：xs=[50,70,80,90], ys=[10,20,40,50] → 4 vs 4 但配对错误 → 结果错误
        """
        bundle = make_bundle(
            {"pv": [50.0, None, 70.0, 80.0, 90.0], "op": [10.0, 20.0, None, 40.0, 50.0]},
            metric_code="valve_linearity",
        )
        result = ValveLinearityCalculator().calculate(bundle)
        # 过滤后 3 对，PV=2*OP+30 → r=1.0
        assert result.value is not None
        assert result.details["n"] == 3


class TestValveNonlinearity:
    """阀门非线性度计算器测试（1 - |r|）。"""

    def test_perfect_correlation_zero_nonlinearity(self):
        """r=1 → nonlinearity=0。"""
        op = [10.0, 20.0, 30.0, 40.0, 50.0]
        pv = [2.0 * o + 1.0 for o in op]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="valve_nonlinearity")
        result = ValveNonlinearityCalculator().calculate(bundle)
        assert result.value == 0.0

    def test_no_correlation_full_nonlinearity(self):
        """r≈0 → nonlinearity≈1。"""
        random.seed(456)
        n = 100
        op = [random.uniform(0, 100) for _ in range(n)]
        pv = [random.uniform(0, 100) for _ in range(n)]
        bundle = make_bundle({"pv": pv, "op": op}, metric_code="valve_nonlinearity")
        result = ValveNonlinearityCalculator().calculate(bundle)
        assert result.value is not None
        assert result.value > 0.7  # 随机数据非线性度高

    def test_insufficient_points(self):
        """仅 2 点（< 3）→ INCONCLUSIVE。"""
        bundle = make_bundle(
            {"pv": [10.0, 20.0], "op": [1.0, 2.0]},
            metric_code="valve_nonlinearity",
        )
        result = ValveNonlinearityCalculator().calculate(bundle)
        assert result.value is None

    def test_none_pair_filtered(self):
        """P0：pair 含 None → 过滤整对，不崩溃。"""
        bundle = make_bundle(
            {"pv": [50.0, None, 70.0, 80.0, 90.0], "op": [10.0, 20.0, None, 40.0, 50.0]},
            metric_code="valve_nonlinearity",
        )
        result = ValveNonlinearityCalculator().calculate(bundle)
        assert result.value is not None
        assert result.details["n"] == 3


class TestValveOperatingRange:
    """阀门运行区间计算器测试（OP 的 [min, max]）。"""

    def test_normal_range(self):
        """op=[10,20,30] → min=10, max=30, span=20。"""
        bundle = make_bundle({"op": [10.0, 20.0, 30.0]}, metric_code="valve_operating_range")
        result = ValveOperatingRangeCalculator().calculate(bundle)
        assert result.value == 20.0  # span
        assert result.details["op_min"] == 10.0
        assert result.details["op_max"] == 30.0
        assert result.details["span"] == 20.0

    def test_constant_op(self):
        """恒定 OP → min=max, span=0。"""
        bundle = make_bundle(
            {"op": [50.0, 50.0, 50.0]},
            metric_code="valve_operating_range",
        )
        result = ValveOperatingRangeCalculator().calculate(bundle)
        assert result.value == 0.0
        assert result.details["op_min"] == 50.0
        assert result.details["op_max"] == 50.0

    def test_insufficient_points(self):
        """仅 1 点（< 2）→ INCONCLUSIVE。"""
        bundle = make_bundle({"op": [50.0]}, metric_code="valve_operating_range")
        result = ValveOperatingRangeCalculator().calculate(bundle)
        assert result.value is None

    def test_empty_data(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({"op": []}, metric_code="valve_operating_range")
        result = ValveOperatingRangeCalculator().calculate(bundle)
        assert result.value is None

    def test_none_values_filtered(self):
        """OP 含 None → 过滤后计算。"""
        bundle = make_bundle(
            {"op": [10.0, None, 30.0]},
            metric_code="valve_operating_range",
        )
        result = ValveOperatingRangeCalculator().calculate(bundle)
        assert result.value == 20.0  # 30-10
        assert result.details["op_min"] == 10.0
        assert result.details["op_max"] == 30.0


# ===========================================================================
# Task 4.3: 设定值穿越次数 — SetpointCrossingCount
# ===========================================================================


class TestSetpointCrossingCount:
    """设定值穿越次数计算器测试（PV 穿越 SP 的符号变化次数）。"""

    def test_no_crossing(self):
        """PV 始终高于 SP → 0 次穿越。"""
        bundle = make_bundle(
            {"pv": [60.0, 60.0, 60.0, 60.0], "sp": [50.0, 50.0, 50.0, 50.0]},
            metric_code="setpoint_crossing_count",
        )
        result = SetpointCrossingCountCalculator().calculate(bundle)
        assert result.value == 0.0
        assert result.details["crossing_count"] == 0

    def test_one_crossing(self):
        """PV 从高于 SP 降到低于 SP → 1 次穿越。"""
        bundle = make_bundle(
            {"pv": [60.0, 60.0, 40.0, 40.0], "sp": [50.0, 50.0, 50.0, 50.0]},
            metric_code="setpoint_crossing_count",
        )
        result = SetpointCrossingCountCalculator().calculate(bundle)
        assert result.value == 1.0
        assert result.details["crossing_count"] == 1

    def test_multiple_crossings(self):
        """PV 交替穿越 SP → 5 次穿越。"""
        pv = [60.0, 40.0, 60.0, 40.0, 60.0, 40.0]
        sp = [50.0] * 6
        bundle = make_bundle(
            {"pv": pv, "sp": sp},
            metric_code="setpoint_crossing_count",
        )
        result = SetpointCrossingCountCalculator().calculate(bundle)
        assert result.value == 5.0
        assert result.details["crossing_count"] == 5

    def test_insufficient_points(self):
        """仅 2 点（< 3）→ INCONCLUSIVE。"""
        bundle = make_bundle(
            {"pv": [60.0, 40.0], "sp": [50.0, 50.0]},
            metric_code="setpoint_crossing_count",
        )
        result = SetpointCrossingCountCalculator().calculate(bundle)
        assert result.value is None

    def test_empty_data(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({"pv": [], "sp": []}, metric_code="setpoint_crossing_count")
        result = SetpointCrossingCountCalculator().calculate(bundle)
        assert result.value is None

    def test_exact_zero_diff_no_crossing(self):
        """P2：PV=SP（diff=0）→ 0*0=0 不满足 <0 → 不算穿越。"""
        bundle = make_bundle(
            {"pv": [50.0, 50.0, 50.0, 50.0], "sp": [50.0, 50.0, 50.0, 50.0]},
            metric_code="setpoint_crossing_count",
        )
        result = SetpointCrossingCountCalculator().calculate(bundle)
        assert result.value == 0.0

    def test_none_pair_filtered(self):
        """P0：pair 含 None → 过滤整对，不 TypeError。"""
        bundle = make_bundle(
            {"pv": [60.0, None, 40.0, 40.0], "sp": [50.0, 50.0, 50.0, 50.0]},
            metric_code="setpoint_crossing_count",
        )
        result = SetpointCrossingCountCalculator().calculate(bundle)
        # 过滤后 diffs=[10, -10, 0] → 1 次穿越（10*-10 < 0）
        assert result.value == 1.0
        assert result.details["n"] == 3


# ===========================================================================
# Task 4.3: 振荡幅值 — OscillationAmplitude
# ===========================================================================


class TestOscillationAmplitude:
    """振荡幅值计算器测试（PV 偏离 SP 的平均绝对偏差）。"""

    def test_normal_amplitude(self):
        """pv=[55,45,55], sp=[50,50,50] → amp=5。"""
        bundle = make_bundle(
            {"pv": [55.0, 45.0, 55.0], "sp": [50.0, 50.0, 50.0]},
            metric_code="oscillation_amplitude",
        )
        result = OscillationAmplitudeCalculator().calculate(bundle)
        assert result.value == 5.0  # (5+5+5)/3

    def test_zero_amplitude(self):
        """PV=SP → amp=0。"""
        bundle = make_bundle(
            {"pv": [50.0, 50.0, 50.0], "sp": [50.0, 50.0, 50.0]},
            metric_code="oscillation_amplitude",
        )
        result = OscillationAmplitudeCalculator().calculate(bundle)
        assert result.value == 0.0

    def test_insufficient_points(self):
        """仅 2 点（< 3）→ INCONCLUSIVE。"""
        bundle = make_bundle(
            {"pv": [55.0, 45.0], "sp": [50.0, 50.0]},
            metric_code="oscillation_amplitude",
        )
        result = OscillationAmplitudeCalculator().calculate(bundle)
        assert result.value is None

    def test_empty_data(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle(
            {"pv": [], "sp": []},
            metric_code="oscillation_amplitude",
        )
        result = OscillationAmplitudeCalculator().calculate(bundle)
        assert result.value is None

    def test_none_pair_filtered(self):
        """P0：pair 含 None → 过滤整对，不 TypeError。"""
        bundle = make_bundle(
            {"pv": [55.0, None, 55.0, 55.0], "sp": [50.0, 50.0, 50.0, 50.0]},
            metric_code="oscillation_amplitude",
        )
        result = OscillationAmplitudeCalculator().calculate(bundle)
        # 过滤后 abs_errs=[5, 5, 5], amp=5.0, n=3（满足 MIN_POINTS=3）
        assert result.value == 5.0
        assert result.details["n"] == 3

    def test_sinusoidal_amplitude(self):
        """正弦振荡 → amp ≈ 理论平均绝对偏差。"""
        n = 200
        sp = [50.0] * n
        amplitude_param = 10.0
        pv = [50.0 + amplitude_param * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle(
            {"pv": pv, "sp": sp},
            metric_code="oscillation_amplitude",
        )
        result = OscillationAmplitudeCalculator().calculate(bundle)
        # 正弦波的平均绝对值 = 2*A/pi ≈ 0.6366 * A
        expected = round(2.0 * amplitude_param / math.pi, 2)
        assert result.value is not None
        assert abs(result.value - expected) < 0.5  # 允许离散采样误差
