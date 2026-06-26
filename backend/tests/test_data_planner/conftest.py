"""DataPlanner 测试共享 fixtures.

提供：
    - FakeCacheRedis: 支持 pipeline / scan / setex / get / delete 的内存 Redis mock
    - build_requirement: 构造 mock ClpmMetricDataRequirement
    - build_raw_timeseries: 构造 RawTimeSeries
    - build_data_block: 构造已预处理的 DataBlock
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.contracts.data_types import (
    DataBlock,
    LoopPreprocessConfig,
    QualitySummary,
    RawTimeSeries,
    TagGroup,
)
from app.services.preprocessing.pipeline import PREPROCESS_VERSION


# ---------------------------------------------------------------------------
# FakeCacheRedis — 支持 pipeline / scan / setex / get / delete
# ---------------------------------------------------------------------------


class FakeCacheRedis:
    """内存 Redis mock，支持缓存模块所需的全部接口.

    支持：get / setex / delete / pipeline / scan / exists
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._ttls: dict[str, int] = {}
        # 记录 pipeline 调用次数（用于断言批量写入）
        self.pipeline_calls = 0

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value
        self._ttls[key] = ttl

    async def set(self, key: str, value: str, **kwargs: Any) -> None:
        self._store[key] = value
        if "ex" in kwargs:
            self._ttls[key] = kwargs["ex"]

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                self._ttls.pop(k, None)
                deleted += 1
        return deleted

    async def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    async def ttl(self, key: str) -> int:
        return self._ttls.get(key, -1)

    async def scan(
        self, cursor: int = 0, match: str | None = None, count: int = 100
    ) -> tuple[int, list[str]]:
        """简化 SCAN：匹配所有 key，一次返回（cursor 归零）."""
        import fnmatch

        if match is None:
            matched = list(self._store.keys())
        else:
            # glob 匹配
            matched = [k for k in self._store.keys() if fnmatch.fnmatch(k, match)]
        # 分批模拟：第一批返回 count 个，cursor 非零；后续返回剩余
        start = cursor
        end = start + count
        batch = matched[start:end]
        next_cursor = end if end < len(matched) else 0
        return next_cursor, batch

    def pipeline(self) -> "_FakePipeline":
        self.pipeline_calls += 1
        return _FakePipeline(self)

    async def aclose(self) -> None:
        pass

    def reset(self) -> None:
        self._store.clear()
        self._ttls.clear()
        self.pipeline_calls = 0

    @property
    def keys(self) -> list[str]:
        return list(self._store.keys())


class _FakePipeline:
    """模拟 Redis Pipeline（批量积累命令，execute 时统一执行）."""

    def __init__(self, redis: FakeCacheRedis) -> None:
        self._redis = redis
        self._commands: list[tuple[str, tuple, dict]] = []

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._commands.append(("setex", (key, ttl, value), {}))

    def set(self, key: str, value: str, **kwargs: Any) -> None:
        self._commands.append(("set", (key, value), kwargs))

    def delete(self, *keys: str) -> None:
        self._commands.append(("delete", keys, {}))

    async def execute(self) -> list:
        results = []
        for cmd, args, kwargs in self._commands:
            if cmd == "setex":
                key, ttl, value = args
                self._redis._store[key] = value
                self._redis._ttls[key] = ttl
                results.append(True)
            elif cmd == "set":
                key, value = args
                self._redis._store[key] = value
                if "ex" in kwargs:
                    self._redis._ttls[key] = kwargs["ex"]
                results.append(True)
            elif cmd == "delete":
                for k in args:
                    self._redis._store.pop(k, None)
                    self._redis._ttls.pop(k, None)
                results.append(len(args))
        return results


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def build_requirement(
    metric_code: str,
    tag_group: str | TagGroup,
    tags: list[str],
    mask_expression: str | None = None,
    sampling_strategy: str = "BY_CONTROL_TYPE",
    quality_policy: str = "KEEP_ALL_WITH_VALIDITY",
    aggregation_policy: str = "LAST",
) -> MagicMock:
    """构造 mock ClpmMetricDataRequirement."""
    if isinstance(tag_group, TagGroup):
        tag_group = tag_group.value
    req = MagicMock()
    req.metric_code = metric_code
    req.tag_group = tag_group
    req.tags = tags
    req.mask_expression = mask_expression
    req.sampling_strategy = sampling_strategy
    req.quality_policy = quality_policy
    req.aggregation_policy = aggregation_policy
    req.depends_on = None
    req.version = "v1"
    return req


def build_raw_timeseries(
    n: int = 100,
    interval_s: float = 1.0,
    tags: list[str] | None = None,
    bad_quality_indices: set[int] | None = None,
    base_time: datetime | None = None,
) -> RawTimeSeries:
    """构造原始时序数据（用于 TDengine 查询 mock 返回值）."""
    if tags is None:
        tags = ["pv", "sp"]
    if base_time is None:
        base_time = datetime(2024, 1, 1, 10, 0, 0)
    bad = bad_quality_indices or set()
    timestamps = [base_time + timedelta(seconds=i * interval_s) for i in range(n)]
    signals: dict[str, list[Any]] = {}
    quality_codes: dict[str, list[int]] = {}

    for tag in tags:
        # 生成 0~100 范围的平滑数据
        signals[tag] = [50.0 + 10.0 * (i % 20) / 20.0 for i in range(n)]
        if tag == "pv":
            # PV 携带质量码（1=Good, 0=Bad，对齐 quality_code.py 映射）
            quality_codes["pv_quality"] = [0 if i in bad else 1 for i in range(n)]

    return RawTimeSeries(
        timestamps=timestamps,
        signals=signals,
        quality_codes=quality_codes,
    )


def build_data_block(
    loop_id: str = "L001",
    tag_group: TagGroup = TagGroup.BASE,
    n: int = 100,
    sampling_freq: str = "1s",
    valid_rate: float = 1.0,
    config_version: str = "cfg_1000",
) -> DataBlock:
    """构造已预处理的 DataBlock（用于缓存测试）."""
    base_time = datetime(2024, 1, 1, 10, 0, 0)
    timestamps = [base_time + timedelta(seconds=i) for i in range(n)]

    # 根据 valid_rate 决定有效点数
    valid_count = int(n * valid_rate)
    pv_valid = [True] * valid_count + [False] * (n - valid_count)
    sp_valid = [True] * valid_count + [False] * (n - valid_count)

    signals = {
        "pv": [50.0 + i * 0.1 for i in range(n)],
        "sp": [50.0 for _ in range(n)],
    }
    validity = {"pv_valid": pv_valid, "sp_valid": sp_valid}

    quality_summary = QualitySummary(
        total_count=n,
        valid_count=valid_count,
        bad_count=n - valid_count,
        missing_count=0,
        valid_rate=valid_rate,
        bad_rate=1.0 - valid_rate,
        missing_rate=0.0,
        good_value_rate=None,
    )

    return DataBlock(
        data_block_id=f"db_{loop_id}_{tag_group.value}_{sampling_freq}",
        loop_id=loop_id,
        tag_group=tag_group.value,
        sampling_freq=sampling_freq,
        timestamps=timestamps,
        signals=signals,
        validity=validity,
        outlier_reasons={"pv": [[] for _ in range(n)], "sp": [[] for _ in range(n)]},
        quality_summary=quality_summary,
        consecutive_segments=[(0, valid_count - 1)] if valid_count > 0 else [],
        config_version=config_version,
        preprocess_version=PREPROCESS_VERSION,
        point_count=n,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis() -> FakeCacheRedis:
    """提供内存 FakeCacheRedis."""
    return FakeCacheRedis()


@pytest.fixture
def make_requirement():
    """提供 build_requirement 工厂函数."""
    return build_requirement


@pytest.fixture
def make_raw_timeseries():
    """提供 build_raw_timeseries 工厂函数."""
    return build_raw_timeseries


@pytest.fixture
def make_data_block():
    """提供 build_data_block 工厂函数."""
    return build_data_block


@pytest.fixture
def preprocess_config():
    """提供默认 LoopPreprocessConfig."""
    return LoopPreprocessConfig(
        loop_id="L001",
        control_type=__import__("app.contracts.data_types", fromlist=["ControlType"]).ControlType.FLOW,
        range_min=0.0,
        range_max=100.0,
        config_version="cfg_1000",
    )
