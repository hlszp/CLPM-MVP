"""可信度统一 Phase 1 结构性断言（方案 §11 Phase 1 门禁）.

验证三链路 valid_rate 口径一致性：
- KPI 链路：_compute_loop_valid_rate_from_bundles → DataQualityAssessor.compute_loop_valid_rate
- 诊断链路：_apply_outlier_preprocessing → DataQualityAssessor.assess().loop_valid_rate
- 整定链路：_fetch_preprocessed_signals → DataQualityAssessor.compute_loop_valid_rate

以及 P1-5 字段错配修复：kpi_snapshot_hourly.valid_rate 与 confidence_level 同口径。

设计依据：可信度统一改进方案 §11 Phase 1 门禁、§13.2 HF 组语义退化评估
"""

from __future__ import annotations

import inspect

from app.services.preprocessing.data_quality_assessor import (
    CORE_TAGS,
    DataQualityAssessor,
)

# ===========================================================================
# 1. 三链路均通过 DataQualityAssessor 计算 valid_rate（结构性断言）
# ===========================================================================


class TestThreeLinkValidRateUnification:
    """断言 KPI/诊断/整定三链路均通过共享内核计算 valid_rate。"""

    def test_kpi_uses_compute_loop_valid_rate(self) -> None:
        """KPI 链路通过 _compute_loop_valid_rate_from_bundles 调用共享内核."""
        from app.tasks.kpi_calc import _compute_loop_valid_rate_from_bundles

        source = inspect.getsource(_compute_loop_valid_rate_from_bundles)
        assert "DataQualityAssessor.compute_loop_valid_rate" in source

    def test_diagnosis_uses_assessor(self) -> None:
        """诊断链路 _apply_outlier_preprocessing 调用 DataQualityAssessor.assess."""
        from app.tasks.diagnosis_engine import _apply_outlier_preprocessing

        source = inspect.getsource(_apply_outlier_preprocessing)
        assert "DataQualityAssessor" in source
        assert "loop_valid_rate" in source

    def test_tuning_uses_compute_loop_valid_rate(self) -> None:
        """整定链路 _fetch_preprocessed_signals 调用共享内核计算回路级 valid_rate."""
        from app.services.tuning import _fetch_preprocessed_signals

        source = inspect.getsource(_fetch_preprocessed_signals)
        assert "DataQualityAssessor" in source
        assert "compute_loop_valid_rate" in source

    def test_core_tags_are_pv_sp_op_mode(self) -> None:
        """核心 tag 集合为 pv/sp/op/mode（决策 D1）."""
        assert CORE_TAGS == ("pv", "sp", "op", "mode")


# ===========================================================================
# 2. P1-5 字段错配修复断言
# ===========================================================================


class TestFieldMismatchFix:
    """断言 kpi_snapshot_hourly.valid_rate 与 confidence_level 同为回路级口径。"""

    def test_extract_lineage_info_accepts_loop_valid_rate(self) -> None:
        """_extract_lineage_info 接受 loop_valid_rate 参数（P1-5）."""
        from app.tasks.kpi_calc import _extract_lineage_info

        sig = inspect.signature(_extract_lineage_info)
        assert "loop_valid_rate" in sig.parameters
        assert sig.parameters["loop_valid_rate"].default is None

    def test_calculate_loop_kpi_passes_loop_valid_rate(self) -> None:
        """_calculate_loop_kpi 计算 loop_valid_rate 并传入 _extract_lineage_info."""
        from app.tasks.kpi_calc import _calculate_loop_kpi

        source = inspect.getsource(_calculate_loop_kpi)
        assert "_compute_loop_valid_rate_from_bundles" in source
        assert "loop_valid_rate=" in source


# ===========================================================================
# 3. 回路级 valid_rate 口径一致性（compute_loop_valid_rate 行为断言）
# ===========================================================================


class TestLoopValidRateConsistency:
    """验证 compute_loop_valid_rate 在不同 tag 组合下口径一致。"""

    def test_full_core_tags_intersection(self) -> None:
        """4 核心 tag 全在场 → 交集 / point_count."""
        validity = {
            "pv_valid": [True, True, False, True],
            "sp_valid": [True, False, True, True],
            "op_valid": [True, True, True, False],
            "mode_valid": [True, True, True, True],
        }
        # 交集：[T,F,F,F] → 1/4
        vr = DataQualityAssessor.compute_loop_valid_rate(validity, 4)
        assert vr == 0.25

    def test_partial_core_tags_skip_missing(self) -> None:
        """缺失 tag 跳过（HF 组语义退化缓解：不因 tagGroup 缺 tag 而误判）."""
        validity = {
            "pv_valid": [True, True, False, True],
            "sp_valid": [True, True, True, True],
            # op_valid / mode_valid 缺失 → 跳过
        }
        # 交集：pv∧sp = [T,T,F,T] → 3/4
        vr = DataQualityAssessor.compute_loop_valid_rate(validity, 4)
        assert vr == 0.75

    def test_zero_point_count_returns_zero(self) -> None:
        """point_count=0 → 0.0."""
        vr = DataQualityAssessor.compute_loop_valid_rate({}, 0)
        assert vr == 0.0

    def test_no_core_tags_returns_zero(self) -> None:
        """无任何核心 tag → 0.0."""
        validity = {"pid_p_valid": [True, True]}
        vr = DataQualityAssessor.compute_loop_valid_rate(validity, 2)
        assert vr == 0.0
