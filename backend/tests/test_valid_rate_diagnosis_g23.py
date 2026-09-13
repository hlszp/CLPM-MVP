"""G23 续：诊断链 valid_rate 必须与 KPI 链同口径（折入时间覆盖率）。

沿革
----
- 可信度统一方案（2026-08-04）Phase 1："统一 valid_rate 口径"，其门禁
  （tests/test_confidence_unification_structural.py）是**结构性**断言——只验
  "是否调用了共享内核"，不验数值；
- R14-2（2026-09-06）为"有效点比例 ≠ 时间覆盖率"给 **Pipeline** 增加
  time_coverage 因子（有效可信度 = 回路级 valid_rate × 时间覆盖率），
  使稀疏数据（120 点 / 30s 间隔 / 跨 1h / 契约 1s）不再获得 A 可信度——
  但该因子**只落在 KPI 链一处**；
- 诊断链 assess() 仍取裸值（其注释却自称"可信度判定唯一输入"），
  于是 Phase 1 刚统一的口径被重新劈开，结构性守卫因为"仍调用共享内核"
  而保持全绿（见收口报告 §20）。

后果（修复前）：同一回路同一窗口，KPI 链得 E 级 → INCONCLUSIVE，
诊断链得 A 级 → **通过诊断数据门禁**，算子在约 3% 覆盖的数据上产出"可信"结论。

修复：assess() 折入 compute_time_coverage，expected_interval_s 用**契约**采样
间隔（compute_time_coverage 文档明确：用实测中位间隔会把稀疏数据洗白成 100%）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.contracts.data_types import ControlType, RawTimeSeries
from app.services.confidence_evaluator import ConfidenceEvaluator
from app.services.preprocessing.data_quality_assessor import DataQualityAssessor


class _Config:
    """最小配置（字段名对齐 LoopPreprocessConfig 的消费面）。"""

    def __init__(self, control_type: ControlType = ControlType.FLOW) -> None:
        self.loop_id = "loop-g23"
        self.range_min = 0.0
        self.range_max = 100.0
        self.op_range_min = 0.0
        self.op_range_max = 100.0
        self.control_type = control_type


def _raw(interval_s: float, n: int = 120) -> RawTimeSeries:
    ts = [
        datetime(2026, 6, 22, 8, 0, tzinfo=UTC) + timedelta(seconds=interval_s * i)
        for i in range(n)
    ]
    return RawTimeSeries(
        timestamps=ts,
        signals={
            "pv": [50.0] * n,
            "sp": [50.0] * n,
            "op": [50.0] * n,
            "mode": [1] * n,
        },
        quality_codes={"pv_quality": [1] * n},
    )


class TestDiagnosisChainFoldsTimeCoverage:
    """诊断链的 loop_valid_rate 必须含时间覆盖率因子。"""

    def test_sparse_data_does_not_get_grade_a(self) -> None:
        """R14-2 场景（120 点 / 30s / 跨 1h / 契约 1s）→ 不得为 A 级。

        修复前：有效点比例 1.0 → A 级，通过诊断门禁。
        """
        assessment = DataQualityAssessor(_Config()).assess(_raw(interval_s=30.0))
        eff = assessment.loop_valid_rate
        assert eff < 0.2, f"稀疏数据的有效可信度应远低于 1.0，实测 {eff}"
        assert ConfidenceEvaluator.evaluate(eff).value != "A"

    def test_dense_data_stays_high(self) -> None:
        """契约间隔下采满（1s × 120 点）→ 覆盖率 1.0，不应被误伤。"""
        cfg = _Config()
        interval = float(DataQualityAssessor(cfg).threshold.base_sampling_freq)
        assessment = DataQualityAssessor(cfg).assess(_raw(interval_s=interval))
        assert assessment.loop_valid_rate == pytest.approx(1.0, abs=0.05)
        assert ConfidenceEvaluator.evaluate(assessment.loop_valid_rate).value == "A"

    def test_effective_rate_is_raw_times_coverage(self) -> None:
        """有效值 = 裸值 × 覆盖率，且覆盖率按**契约**间隔计。"""
        cfg = _Config()
        assessor = DataQualityAssessor(cfg)
        raw = _raw(interval_s=30.0)
        assessment = assessor.assess(raw)
        raw_rate = DataQualityAssessor.compute_loop_valid_rate(
            assessment.validity, assessment.point_count
        )
        assert raw_rate == pytest.approx(1.0, abs=1e-9)  # 全部 Good
        assert assessment.loop_valid_rate < raw_rate
        # 契约 1s、30s 间隔、120 点 → 覆盖量级约 0.03~0.04
        assert 0.01 < assessment.loop_valid_rate < 0.1

    def test_structural_guard_alone_cannot_catch_this(self) -> None:
        """记录为什么 Phase 1 的结构性守卫漏掉了它：它只验调用、不验数值。"""
        import inspect

        from app.services.preprocessing.data_quality_assessor import DataQualityAssessor as DQA

        src = inspect.getsource(DQA.assess)
        assert "compute_loop_valid_rate" in src  # 守卫看到的就是这个
        assert "compute_time_coverage" in src  # 修复后新增的才是数值口径
