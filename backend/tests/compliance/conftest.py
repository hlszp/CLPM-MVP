"""compliance 套件共享构造辅助（任务 G4）.

与 tests/test_metric_calculator/conftest.py 的 make_bundle 同构，
额外支持自定义时间戳序列（缺口/乱序/重复退化模式）与
outlier_reasons 注入，供数值健壮性矩阵与异常检测边界套件复用。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.contracts.data_types import (
    DataBlock,
    DataLineage,
    MetricDataBundle,
    MetricResult,
    QualitySummary,
)


def make_ts(n: int, interval_s: float = 1.0) -> list[datetime]:
    """生成 n 个等间隔时间戳（默认 1 秒间隔）."""
    start = datetime(2024, 1, 1, 0, 0, 0)
    return [start + timedelta(seconds=interval_s * i) for i in range(n)]


def build_bundle(
    signals: dict[str, list[Any]],
    *,
    timestamps: list[datetime] | None = None,
    n: int | None = None,
    metric_code: str = "accuracy_rate",
    tag_group: str = "BASE",
    sampling_freq: str = "1s",
    outlier_reasons: dict[str, list[list[str]]] | None = None,
    quality_summary: QualitySummary | None = None,
) -> MetricDataBundle:
    """构造测试用 MetricDataBundle（masked_indices 默认为全部点）.

    Args:
        signals: 信号字典（pv/sp/op/mode/配置标量）
        timestamps: 自定义时间戳；None 时按 n 生成 1s 等间隔序列
        n: 点数；None 时从 timestamps 或首个信号推断
        metric_code: 指标代码
        tag_group: tagGroup 标签
        sampling_freq: 采样频率标签（仪表故障复合判据反查阈值用）
        outlier_reasons: 每点异常原因码字典
        quality_summary: 质量摘要；None 时构造 valid_rate=1.0
    """
    if timestamps is not None:
        n = len(timestamps)
    elif n is None:
        n = len(next(iter(signals.values()))) if signals else 0
    if timestamps is None:
        timestamps = make_ts(n)
    validity = {f"{k}_valid": [True] * len(v) for k, v in signals.items()}
    if quality_summary is None:
        quality_summary = QualitySummary(
            total_count=n,
            valid_count=n,
            bad_count=0,
            valid_rate=1.0,
            bad_rate=0.0,
        )
    block = DataBlock(
        data_block_id="db_compliance_BASE_1s",
        loop_id="L001",
        tag_group=tag_group,
        sampling_freq=sampling_freq,
        timestamps=timestamps,
        signals=signals,
        validity=validity,
        outlier_reasons=outlier_reasons or {},
        quality_summary=quality_summary,
        consecutive_segments=[(0, n - 1)] if n > 0 else [],
        point_count=n,
    )
    lineage = DataLineage(sampling_freq=sampling_freq, tag_group=tag_group, valid_rate=1.0)
    return MetricDataBundle(
        metric_code=metric_code,
        data_block=block,
        mask_expression="",
        masked_indices=list(range(n)),
        lineage=lineage,
    )


def make_dep_result(
    code: str, value: float | None, details: dict[str, Any] | None = None
) -> MetricResult:
    """构造依赖注入用 MetricResult（如 fast_rate 依赖 settling_time）."""
    return MetricResult(
        metric_code=code,
        value=value,
        confidence_level="A",
        lineage=DataLineage(),
        details=details or {},
    )
