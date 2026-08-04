"""指标计算器测试共享 fixtures.

提供 MetricDataBundle / DataBlock 构造辅助函数，覆盖常见测试场景：
- 正常数据（pv/sp/op/mode 全有效）
- 边界数据（空/单点/恒定值）
- 极端数据（全无效/振荡/饱和）
- 配置数据（CONFIG tagGroup）
- 7 场景测试数据集（kpi_scenarios session fixture）

设计依据：算法说明 §3.4-3.7
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.contracts.data_types import (
    DataBlock,
    DataLineage,
    MetricDataBundle,
    QualitySummary,
)

# ---------------------------------------------------------------------------
# 7 场景测试数据集（session 级，跨模块共享）
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "kpi_test_data.json"

#: 7 个场景名称（对齐项目记忆硬约束 "7 scenarios"）
SCENARIO_NAMES = (
    "fast_response",
    "slow_response",
    "oscillation",
    "op_saturation",
    "normal",
    "manual_mode",
    "pure_ar2",
)


@pytest.fixture(scope="session")
def kpi_scenarios() -> dict[str, dict[str, Any]]:
    """加载 kpi_test_data.json 中全部 7 个场景数据。

    Returns:
        {scenario_name: scenario_dict} 字典；
        每个 scenario_dict 含 data/description/expected/control_type/pv_range 等字段。
    """
    if not FIXTURE_PATH.exists():
        pytest.skip(f"测试数据文件不存在：{FIXTURE_PATH}")
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    # 校验 7 个场景齐全
    missing = [n for n in SCENARIO_NAMES if n not in data]
    assert not missing, f"测试数据缺失场景：{missing}"
    return data


# ---------------------------------------------------------------------------
# 数据构造辅助
# ---------------------------------------------------------------------------


def make_timestamps(n: int, interval_s: float = 1.0) -> list[datetime]:
    """生成 n 个等间隔时间戳（默认 1 秒间隔）."""
    start = datetime(2024, 1, 1, 0, 0, 0)
    return [start + timedelta(seconds=interval_s * i) for i in range(n)]


def make_data_block(
    signals: dict[str, list[Any]],
    validity: dict[str, list[bool]] | None = None,
    *,
    n: int | None = None,
    tag_group: str = "BASE",
    sampling_freq: str = "1s",
    quality_summary: QualitySummary | None = None,
    consecutive_segments: list[tuple[int, int]] | None = None,
    outlier_reasons: dict[str, list[list[str]]] | None = None,
    data_block_id: str = "db_test_BASE_1s",
    loop_id: str = "L001",
    loop_confidence_level: str = "E",
) -> DataBlock:
    """构造测试用 DataBlock.

    Args:
        signals: 信号字典，如 ``{"pv": [50, 51, ...], "sp": [50, ...]}``
        validity: 有效性字典，key 为 ``{tag}_valid``；None 时全部有效
        n: 点数；None 时从第一个信号推断
        tag_group: tagGroup 标签
        sampling_freq: 采样频率标签
        quality_summary: 质量摘要；None 时自动构造 valid_rate=1.0
        consecutive_segments: 连续有效段
        outlier_reasons: 每点异常原因码字典，key 为 tag，value 为 list[list[str]]；
            None 时空字典（Phase 1 instrument_fault_rate 测试用）
        data_block_id: 数据块 ID
        loop_id: 回路 ID
        loop_confidence_level: 回路级可信度等级（v6.2 P2-2，默认 "E"）
    """
    if n is None:
        n = len(next(iter(signals.values()))) if signals else 0
    if validity is None:
        validity = {f"{k}_valid": [True] * len(v) for k, v in signals.items()}
    if quality_summary is None:
        quality_summary = QualitySummary(
            total_count=n,
            valid_count=n,
            bad_count=0,
            valid_rate=1.0,
            bad_rate=0.0,
        )
    timestamps = make_timestamps(n)
    return DataBlock(
        data_block_id=data_block_id,
        loop_id=loop_id,
        tag_group=tag_group,
        sampling_freq=sampling_freq,
        timestamps=timestamps,
        signals=signals,
        validity=validity,
        outlier_reasons=outlier_reasons or {},
        quality_summary=quality_summary,
        consecutive_segments=consecutive_segments or [(0, n - 1)] if n > 0 else [],
        point_count=n,
        loop_confidence_level=loop_confidence_level,
    )


def make_bundle(
    signals: dict[str, list[Any]],
    validity: dict[str, list[bool]] | None = None,
    *,
    mask_expression: str = "",
    metric_code: str = "accuracy_rate",
    tag_group: str = "BASE",
    sampling_freq: str = "1s",
    quality_summary: QualitySummary | None = None,
    outlier_reasons: dict[str, list[list[str]]] | None = None,
    n: int | None = None,
    loop_confidence_level: str = "E",
) -> MetricDataBundle:
    """构造测试用 MetricDataBundle.

    自动应用 mask_expression 生成 masked_indices；
    空表达式表示全部点有效。
    """
    block = make_data_block(
        signals,
        validity,
        n=n,
        tag_group=tag_group,
        sampling_freq=sampling_freq,
        quality_summary=quality_summary,
        outlier_reasons=outlier_reasons,
        loop_confidence_level=loop_confidence_level,
    )
    # 简化 mask：空表达式 → 全部索引；否则取所有 valid 为 True 的索引交集
    if not mask_expression or not mask_expression.strip():
        masked_indices = list(range(block.point_count))
    else:
        # 解析简单 && 表达式
        tags = [t.strip() for t in mask_expression.split("&&")]
        masked_indices = []
        for i in range(block.point_count):
            if all(
                block.validity.get(t, [False])[i] if i < len(block.validity.get(t, [])) else False
                for t in tags
            ):
                masked_indices.append(i)
    lineage = DataLineage(
        sampling_freq=sampling_freq,
        tag_group=tag_group,
        valid_rate=1.0,
    )
    return MetricDataBundle(
        metric_code=metric_code,
        data_block=block,
        mask_expression=mask_expression,
        masked_indices=masked_indices,
        lineage=lineage,
    )


# ---------------------------------------------------------------------------
# 常用 fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def normal_pv_sp_bundle() -> MetricDataBundle:
    """正常 PV-SP 数据（PV 略微偏离 SP，偏差恒定）."""
    n = 100
    pv = [50.0 + 0.5] * n  # PV 恒定偏离 SP 0.5
    sp = [50.0] * n
    return make_bundle({"pv": pv, "sp": sp}, metric_code="accuracy_rate")


@pytest.fixture
def zero_error_bundle() -> MetricDataBundle:
    """PV=SP 零偏差数据（准确率应为 100）."""
    n = 100
    val = [50.0] * n
    return make_bundle({"pv": list(val), "sp": list(val)}, metric_code="accuracy_rate")


@pytest.fixture
def large_error_bundle() -> MetricDataBundle:
    """大偏差数据（偏差超过 e_max）."""
    n = 100
    pv = [90.0] * n
    sp = [10.0] * n  # 偏差 80，远超 e_max=5
    return make_bundle({"pv": pv, "sp": sp}, metric_code="accuracy_rate")


@pytest.fixture
def empty_bundle() -> MetricDataBundle:
    """空数据包（0 个点）."""
    return make_bundle({}, metric_code="accuracy_rate")


@pytest.fixture
def oscillation_bundle() -> MetricDataBundle:
    """振荡数据（PV 在 SP 上下周期性波动）."""
    import math

    n = 200
    sp = [50.0] * n
    pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
    return make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")


@pytest.fixture
def auto_mode_bundle() -> MetricDataBundle:
    """自控模式数据（mode=1 Auto，OP 在中间区域）."""
    n = 100
    mode = [1] * n  # 全自动
    op = [50.0] * n  # OP 未饱和
    return make_bundle({"mode": mode, "op": op}, metric_code="auto_mode_rate")


@pytest.fixture
def saturation_bundle() -> MetricDataBundle:
    """饱和数据（OP 处于高限位，mode=Auto）."""
    n = 100
    mode = [1] * n
    op = [99.5] * n  # OP 接近高限（饱和）
    return make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")


@pytest.fixture
def config_bundle() -> MetricDataBundle:
    """CONFIG 数据包（含配置参数）."""
    return make_bundle(
        {"ideal_settling_time": [45.0], "control_type": ["FC"], "e_max": [5.0]},
        tag_group="CONFIG",
        metric_code="ideal_settling_time",
        n=1,
    )
