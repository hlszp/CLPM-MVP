"""O2 data_version 批级预载（H2a）等价测试 + O1 H2 余段探针（默认 off）测试.

依据：docs/设计文档/系统改造优化-2026-10-10/执行状态/G5-D01-04-实证-20261015/
D01-归因报告.md §三（O1/O2 规格）与 §H2 余段构成。

等价红线（O2）：预载与逐回路读须同源同值同键——两路径共用模块级
``resolve_data_version``（单一事实源），本文件以"相同输入 → 相同缓存键集"
作直接断言。
"""

from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.contracts.data_types import (
    ControlType,
    LoopPreprocessConfig,
    TagGroup,
    TimeWindow,
)
from app.services.cache.l1_datablock import L1DataBlockCache
from app.services.cache.l2_bundle import L2BundleCache
from app.services.data_planner import DataPlanner
from app.services.metric_data_bundle import MetricDataBundleAssembler

from .conftest import (
    FakeCacheRedis,
    build_data_block,
    build_requirement,
)

_VERSION = "mf-abc123def:tqv1"


def _make_db(requirements: list) -> AsyncMock:
    """构造 mock db，execute 返回 requirements 列表（同 test_data_planner）."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = requirements
    db.execute = AsyncMock(return_value=result)
    return db


def _make_config_loader(config_version: str = "cfg_1000"):
    async def loader(loop_id: str, ctrl: ControlType) -> LoopPreprocessConfig:
        return LoopPreprocessConfig(
            loop_id=loop_id,
            control_type=ctrl,
            range_min=0.0,
            range_max=100.0,
            config_version=config_version,
            op_range_min=0.0,
            op_range_max=100.0,
        )

    return loader


def _make_planner(redis: FakeCacheRedis, db: AsyncMock) -> DataPlanner:
    async def query_fn(loop_id, tag_roles, start, end, interval_s):
        from .conftest import build_raw_timeseries

        return build_raw_timeseries(n=50, interval_s=float(interval_s), tags=tag_roles)

    return DataPlanner(
        cache=L1DataBlockCache(redis),
        tdengine_query_fn=query_fn,
        assembler=MetricDataBundleAssembler(),
        db=db,
        config_loader=_make_config_loader(),
        bundle_cache=L2BundleCache(redis),
    )


async def _request(planner: DataPlanner) -> None:
    await planner.request_bundles(
        loop_id="TC101",
        metrics=["accuracy_rate"],
        time_window=TimeWindow(
            start=datetime(2024, 1, 1, 10, 0, 0), end=datetime(2024, 1, 1, 11, 0, 0)
        ),
        control_type=ControlType.TEMPERATURE,
    )


def _requirements_db() -> AsyncMock:
    return _make_db(
        [build_requirement("accuracy_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid")]
    )


@pytest.fixture(autouse=True)
def _trace_off(monkeypatch):
    """隔离探针开关（默认 off；个别用例显式 setenv）."""
    monkeypatch.delenv("CLPM_G5_TRACE", raising=False)


# ---------------------------------------------------------------------------
# O2：预载消费 + 回落口径 + 同键等价
# ---------------------------------------------------------------------------


class TestDataVersionPreload:
    async def test_preload_consumed_skips_per_loop_resolution(self, monkeypatch) -> None:
        """预载注入后 request_bundles 不再逐回路解析，且版本落入缓存键."""
        calls = {"n": 0}

        async def counting_resolve() -> str:
            calls["n"] += 1
            return _VERSION

        monkeypatch.setattr("app.services.data_planner.resolve_data_version", counting_resolve)

        redis = FakeCacheRedis()
        planner = _make_planner(redis, _requirements_db())
        planner._preloaded_data_version = _VERSION

        await _request(planner)

        assert calls["n"] == 0, "预载路径不得再触发逐回路解析"
        assert planner._data_version == _VERSION
        # 非 legacy 版本分量进 L1 与 L2 键（AD02 口径）
        assert any(_VERSION in k for k in redis.keys), redis.keys
        assert len(redis.keys) == 2  # 1 × L1 + 1 × L2

    async def test_fallback_resolves_once_per_request(self, monkeypatch) -> None:
        """未注入时回落逐回路解析（口径与既有行为一致），值同源."""
        calls = {"n": 0}

        async def counting_resolve() -> str:
            calls["n"] += 1
            return _VERSION

        monkeypatch.setattr("app.services.data_planner.resolve_data_version", counting_resolve)

        redis = FakeCacheRedis()
        planner = _make_planner(redis, _requirements_db())

        await _request(planner)

        assert calls["n"] == 1
        assert planner._data_version == _VERSION
        assert any(_VERSION in k for k in redis.keys)

    async def test_preload_and_fallback_produce_identical_keys(self, monkeypatch) -> None:
        """等价红线：同源同值 → 相同输入产出完全一致的缓存键集."""
        monkeypatch.setattr(
            "app.services.data_planner.resolve_data_version", _async_const(_VERSION)
        )

        redis_pre, redis_fb = FakeCacheRedis(), FakeCacheRedis()
        pre = _make_planner(redis_pre, _requirements_db())
        pre._preloaded_data_version = _VERSION
        fb = _make_planner(redis_fb, _requirements_db())

        await _request(pre)
        await _request(fb)

        assert set(redis_pre.keys) == set(redis_fb.keys)

    async def test_legacy_default_no_preload_unchanged(self) -> None:
        """不注入且不补丁：真实回落路径（无 PG → legacy/point 兜底）可正常出键."""
        redis = FakeCacheRedis()
        planner = _make_planner(redis, _requirements_db())
        await _request(planner)
        assert len(redis.keys) == 2


def _async_const(value: str):
    async def fn() -> str:
        return value

    return fn


# ---------------------------------------------------------------------------
# O1：H2 余段探针——默认 off 零输出；显式 on 输出全部细拆 stage
# ---------------------------------------------------------------------------


def _g5_records(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "G5_TRACE" in r.getMessage()]


class TestH2Probe:
    async def test_probe_off_zero_output_full_path(self, caplog) -> None:
        """默认 off：request_bundles 全路径 + L1/L2 写回均零 G5_TRACE 输出."""
        redis = FakeCacheRedis()
        planner = _make_planner(redis, _requirements_db())

        with caplog.at_level(logging.INFO, logger="app.core.g5_trace"):
            await _request(planner)
            cache = L1DataBlockCache(FakeCacheRedis())
            await cache.set_many([build_data_block()])
            l2 = L2BundleCache(FakeCacheRedis())
            await l2.set("l2-key", [])

        assert _g5_records(caplog) == []
        assert len(redis.keys) == 2  # 行为不变：L1/L2 正常写

    async def test_probe_on_emits_all_h2_stages(self, monkeypatch, caplog) -> None:
        """显式 on：四组细拆 stage（config/version/requirements、cache-read、
        set_many encode/await、assemble+L2 encode/await）全部可见."""
        monkeypatch.setenv("CLPM_G5_TRACE", "1")
        redis = FakeCacheRedis()
        planner = _make_planner(redis, _requirements_db())

        with caplog.at_level(logging.INFO, logger="app.core.g5_trace"):
            await _request(planner)

        text = "\n".join(_g5_records(caplog))
        for stage in (
            "data_planner.h2.config",
            "data_planner.h2.version",
            "data_planner.h2.requirements",
            "data_planner.h2.l2_read",
            "data_planner.h2.l1_read",
            "data_planner.h2.assemble",
            "l1.set_many.encode",
            "l1.set_many.redis_await",
            "l2.set.encode",
            "l2.set.redis_await",
        ):
            assert stage in text, f"探针未覆盖 stage: {stage}"

    async def test_probe_on_preloaded_version_span_absent(self, monkeypatch, caplog) -> None:
        """预载路径无版本解析段（O2 生效后 h2.version 探针自然消失）."""
        monkeypatch.setenv("CLPM_G5_TRACE", "1")
        redis = FakeCacheRedis()
        planner = _make_planner(redis, _requirements_db())
        planner._preloaded_data_version = _VERSION

        with caplog.at_level(logging.INFO, logger="app.core.g5_trace"):
            await _request(planner)

        text = "\n".join(_g5_records(caplog))
        assert "data_planner.h2.version" not in text
        assert "data_planner.h2.config" in text
