"""任务 G2（附录 B 公式级验证）专用构造辅助.

与 G4 的 conftest.py 分离存放，避免多 agent 并行写同一文件的冲突。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from app.contracts.data_types import (
    DataBlock,
    DataLineage,
    MetricDataBundle,
    MetricResult,
    QualitySummary,
)
from app.services import algorithm_config as ac

from ..test_metric_calculator.conftest import make_bundle, make_timestamps  # noqa: F401

_TS_BASE = datetime(2024, 1, 1, 0, 0, 0)


def ts_from_offsets(offsets_s: list[float]) -> list[datetime]:
    """按秒偏移序列生成时间戳（支持非均匀间隔）."""
    return [_TS_BASE + timedelta(seconds=s) for s in offsets_s]


def make_ts_bundle(
    signals: dict[str, list[Any]],
    timestamps: list[datetime],
    *,
    metric_code: str,
    tag_group: str = "BASE",
    sampling_freq: str = "1s",
) -> MetricDataBundle:
    """构造自定义时间戳的 MetricDataBundle（全部点有效）.

    Args:
        signals: 信号字典（长度应与 timestamps 一致；CONFIG 标量除外）
        timestamps: 自定义时间戳序列
        metric_code: 指标代码
        tag_group: tagGroup 标签
        sampling_freq: 采样频率标签
    """
    n = len(timestamps)
    validity = {f"{k}_valid": [True] * len(v) for k, v in signals.items()}
    block = DataBlock(
        data_block_id=f"db_gbt_compliance_{metric_code}",
        loop_id="L_GBT",
        tag_group=tag_group,
        sampling_freq=sampling_freq,
        timestamps=timestamps,
        signals=signals,
        validity=validity,
        outlier_reasons={},
        quality_summary=QualitySummary(
            total_count=n,
            valid_count=n,
            bad_count=0,
            valid_rate=1.0,
            bad_rate=0.0,
        ),
        consecutive_segments=[(0, n - 1)] if n > 0 else [],
        point_count=n,
    )
    return MetricDataBundle(
        metric_code=metric_code,
        data_block=block,
        mask_expression="",
        masked_indices=list(range(n)),
        lineage=DataLineage(
            sampling_freq=sampling_freq,
            tag_group=tag_group,
            valid_rate=1.0,
        ),
    )


def make_metric_result(
    metric_code: str,
    value: float | None,
    confidence: str = "A",
    details: dict[str, Any] | None = None,
) -> MetricResult:
    """构造依赖注入 / 综合评分输入用 MetricResult."""
    return MetricResult(
        metric_code=metric_code,
        value=value,
        confidence_level=confidence,
        lineage=DataLineage(),
        details=details or {},
    )


@pytest.fixture
def reset_algo_config_cache():
    """每个测试前后保存/恢复算法参数缓存，避免污染其他测试."""
    saved = dict(ac._merged_cache)
    ac._merged_cache = {}
    yield
    ac._merged_cache = saved
