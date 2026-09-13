"""G23：valid_rate 单一事实层（时间覆盖率不得只在一条链路上生效）。

事实来源与沿革：
- docs/过程文档/confidence-unification-plan-2026-08-04.md Phase 1 目标：
  "消除诊断链路的自写预处理，**统一 valid_rate 口径**"，状态"全部验收通过"；
- 该方案 §11 Phase 1 的门禁是**结构性**的（inspect.getsource 含某调用即通过），
  见 tests/test_confidence_unification_structural.py；
- R14-2（2026-09-06）：为"有效点比例 ≠ 时间覆盖率"给 **Pipeline** 增加
  time_coverage 因子（loop_valid_rate = 回路级 valid_rate × 时间覆盖率），
  使稀疏数据（120 点 / 30s 间隔 / 跨 1h / 契约 1s）不再获得 A 可信度；
- 但该因子只落在 Pipeline 一处，其余消费点仍取裸 valid_rate，
  **把 Phase 1 刚统一的口径重新劈开**——结构性守卫看不见数值分歧。

data_types.py 的契约写明：loop_valid_rate 由 Pipeline 用
ConfidenceEvaluator.evaluate(loop_valid_rate) 一次算出，供"所有指标读取"。
故 kpi_calc 应**消费**该字段，而不是从 validity/point_count 重算裸值。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.contracts.data_types import DataBlock, MetricDataBundle, TagGroup
from app.services.confidence_evaluator import ConfidenceEvaluator
from app.tasks.kpi_calc import _compute_loop_valid_rate_from_bundles

#: R14-2 场景：120 个 Good 点 / 30s 间隔 / 跨 1 小时 / 契约采样 1s
#: → 有效点比例 1.0，但时间覆盖率 ≈ 120/3601 ≈ 3.33%
_SPARSE_VALID_RATE = 1.0
_SPARSE_COVERAGE = 120.0 / 3601.0
_SPARSE_EFFECTIVE = _SPARSE_VALID_RATE * _SPARSE_COVERAGE  # ≈ 0.0333


def _block(*, loop_valid_rate: float) -> DataBlock:
    ts = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
    n = 120
    return DataBlock(
        data_block_id="db_loop1_BASE_1s",
        loop_id="loop-1",
        tag_group=TagGroup.BASE.value,
        sampling_freq="1s",
        timestamps=[ts] * n,
        signals={"pv": [50.0] * n, "sp": [50.0] * n, "mode": [1] * n},
        validity={
            "pv_valid": [True] * n,
            "sp_valid": [True] * n,
            "mode_valid": [True] * n,
        },
        point_count=n,
        loop_valid_rate=loop_valid_rate,
    )


def _bundle(*, loop_valid_rate: float) -> MetricDataBundle:
    return MetricDataBundle(
        metric_code="accuracy_rate",
        data_block=_block(loop_valid_rate=loop_valid_rate),
        mask_expression="pv_valid && sp_valid",
        masked_indices=list(range(120)),
        lineage=None,
    )


class TestSingleSourceOfTruth:
    """kpi_calc 必须消费 Pipeline 算出的 loop_valid_rate，而非重算裸值。"""

    def test_helper_consumes_block_effective_valid_rate(self) -> None:
        """稀疏场景：必须返回 Pipeline 的**有效**口径（含时间覆盖），而非裸值 1.0。

        修复前：返回 1.0（从 validity/point_count 重算，漏掉时间覆盖因子）。
        """
        vr = _compute_loop_valid_rate_from_bundles([_bundle(loop_valid_rate=_SPARSE_EFFECTIVE)])
        assert vr == _SPARSE_EFFECTIVE
        assert vr != _SPARSE_VALID_RATE, "不得返回未折入时间覆盖的裸 valid_rate"

    def test_persisted_valid_rate_agrees_with_confidence_level(self) -> None:
        """P1-5 不变式：落库 valid_rate 经 evaluate 必须等于落库 confidence_level。

        kpi_snapshot_hourly 的 confidence_level 取自 Pipeline 的
        loop_confidence_level（含覆盖），valid_rate 取自本 helper。
        修复前二者不同口径 → 同一行出现 valid_rate=1.0000 与 'E' 并存的矛盾。
        """
        effective = _compute_loop_valid_rate_from_bundles(
            [_bundle(loop_valid_rate=_SPARSE_EFFECTIVE)]
        )
        assert effective is not None

        pipeline_level = ConfidenceEvaluator.evaluate(_SPARSE_EFFECTIVE).value
        persisted_level = ConfidenceEvaluator.evaluate(effective).value

        assert pipeline_level == "E", "R14-2 场景应为 E 级（稀疏 COV 不得得 A）"
        assert persisted_level == pipeline_level

    def test_legacy_block_without_loop_valid_rate_falls_back(self) -> None:
        """未设 loop_valid_rate（默认 0.0）的 legacy/手搓块仍走重算回退。"""
        ts = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
        block = DataBlock(
            data_block_id="db_loop1_BASE_5s",
            loop_id="loop-1",
            tag_group=TagGroup.BASE.value,
            sampling_freq="5s",
            timestamps=[ts] * 4,
            signals={"pv": [50, 50, 60, 50], "sp": [50] * 4, "mode": [1] * 4},
            validity={
                "pv_valid": [True, True, False, True],
                "sp_valid": [True, True, True, False],
                "mode_valid": [True, True, True, True],
            },
            point_count=4,
        )
        bundle = MetricDataBundle(
            metric_code="accuracy_rate",
            data_block=block,
            mask_expression="pv_valid && sp_valid",
            masked_indices=[0, 1],
            lineage=None,
        )
        # 核心 tag 交集 pv∧sp∧mode = [T,T,F,F] → 2/4 = 0.5
        assert _compute_loop_valid_rate_from_bundles([bundle]) == 0.5

    def test_no_base_block_returns_none(self) -> None:
        ts = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
        block = DataBlock(
            data_block_id="db_loop1_HF_1s",
            loop_id="loop-1",
            tag_group="PVOP_HF",
            sampling_freq="1s",
            timestamps=[ts],
            signals={"pv": [50.0]},
            validity={"pv_valid": [True]},
            point_count=1,
        )
        bundle = MetricDataBundle(
            metric_code="stiction_index",
            data_block=block,
            mask_expression="",
            masked_indices=[0],
            lineage=None,
        )
        assert _compute_loop_valid_rate_from_bundles([bundle]) is None
