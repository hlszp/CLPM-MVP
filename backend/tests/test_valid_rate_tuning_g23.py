"""G23 续：整定链 valid_rate 与 KPI/诊断链同口径（折入时间覆盖率）。

沿革：可信度统一方案 Phase 1（2026-08-04）把三链 valid_rate 统一到
compute_loop_valid_rate；R14-2（2026-09-06）为"有效点比例 != 时间覆盖率"
给 Pipeline 增加 time_coverage 因子，**只落在 KPI 链一处**，把刚统一的口径
重新劈开（详见收口报告 §20/§29）。§29 补了诊断链，本文件覆盖**整定链**。

修法要点（易踩的坑）：expected_interval_s 必须是**契约**采样间隔，
故用 control_type 反查阈值，**不能**用块的 pvop_block.sampling_freq ——
后者是实测采样率，用它算覆盖率会把稀疏数据洗白成 coverage=100%，
等于没修（compute_time_coverage 文档明确警告这一点）。

测试局限（如实标注）
--------------------
_fetch_preprocessed_signals 是 async 且依赖 DB + DataPlanner，本层无法做
集成级行为断言。故本文件对**共享因子**做行为断言，并对整定链**取间隔的来源**
做来源断言；整定链的端到端数值一致性登记为残余风险，留待可注入 DataPlanner
的集成测试。
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

from app.contracts.data_types import ControlType
from app.services.preprocessing.quality_summary import compute_time_coverage
from app.services.preprocessing.thresholds import get_threshold


def _sparse_timestamps(interval_s: float = 30.0, n: int = 120) -> list[datetime]:
    base = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    return [base + timedelta(seconds=interval_s * i) for i in range(n)]


class TestSharedCoverageFactor:
    """共享因子本身的行为（三链共用）。"""

    def test_sparse_scenario_coverage_is_far_below_one(self) -> None:
        """契约 1s、实测 30s、120 点 → 覆盖率约 0.03，而非 1.0。"""
        contract = float(get_threshold(ControlType.FLOW).base_sampling_freq)
        assert contract == 1.0
        cov = compute_time_coverage(_sparse_timestamps(30.0), expected_interval_s=contract)
        assert 0.01 < cov < 0.1

    def test_using_actual_interval_would_whitewash(self) -> None:
        """反证：用**实测**间隔算覆盖率会退化为 ~1.0 —— 这正是不得用块级
        sampling_freq 的原因，也是本修复的关键陷阱。"""
        cov_actual = compute_time_coverage(_sparse_timestamps(30.0), expected_interval_s=30.0)
        cov_contract = compute_time_coverage(
            _sparse_timestamps(30.0),
            expected_interval_s=float(get_threshold(ControlType.FLOW).base_sampling_freq),
        )
        assert cov_actual > 0.9, "实测间隔下覆盖率被洗白为 ~1.0"
        assert cov_contract < 0.1


class TestTuningChainUsesContractInterval:
    """整定链必须以契约间隔（而非块级实测采样率）计算覆盖率。"""

    def test_tuning_valid_rate_folds_coverage_with_contract_interval(self) -> None:
        from app.services.tuning import _fetch_preprocessed_signals

        src = inspect.getsource(_fetch_preprocessed_signals)
        assert "compute_time_coverage" in src
        assert "raw_loop_valid_rate" in src
        assert "time_coverage" in src
        assert "get_threshold(control_type).base_sampling_freq" in src
        assert "expected_interval_s=float(pvop_block.sampling_freq" not in src.replace(" ", "")
