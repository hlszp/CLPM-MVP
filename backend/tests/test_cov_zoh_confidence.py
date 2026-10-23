"""全测点 COV：值在 builder 展开，可信度逐槽消费质量和真实覆盖依据。"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.contracts.data_types import ControlType, LoopPreprocessConfig, RawTimeSeries, TagGroup
from app.contracts.series_context import IntervalRun, RoleCoverage, SeriesContext
from app.services.data_source import logical_wide_builder as lwb
from app.services.preprocessing.pipeline import PreprocessingPipeline

T0 = datetime(2026, 10, 23, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    original = socket.socket.connect

    def blocked(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            pytest.fail(f"COV unit test attempted network access: {address}")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


def _pipeline() -> PreprocessingPipeline:
    return PreprocessingPipeline(
        LoopPreprocessConfig(
            loop_id="L-COV", control_type=ControlType.FLOW, range_min=0, range_max=100
        )
    )


async def _build(monkeypatch, *, end=3600, gaps=(), initial=None, op_event_seconds=None):
    async def segments(_db, _loop, roles, start, _end):
        return {
            role.upper(): [
                SimpleNamespace(point_id=role, start=_at(-86400), end=None, initial_check=start)
            ]
            for role in roles
        }

    async def events(point_ids, start, stop, **_kwargs):
        return {
            pid: [
                {"ts": _at(s), "value": 50.0, "quality_class": 1}
                for s in (
                    range(end + 1)
                    if pid == "pv"
                    else (
                        op_event_seconds
                        if op_event_seconds is not None
                        else range(0, end + 1, 1200)
                    )
                )
                if start <= _at(s) <= stop and initial is None
            ]
            for pid in point_ids
        }

    monkeypatch.setattr(lwb, "_resolve_role_segments", segments)
    monkeypatch.setattr(lwb, "_load_gap_windows", AsyncMock(return_value=list(gaps)))
    monkeypatch.setattr(
        lwb, "_held_limits_s", AsyncMock(return_value={"fast": 60.0, "slow": 7200.0})
    )
    monkeypatch.setattr(lwb.repo, "read_events", events)
    monkeypatch.setattr(
        lwb.repo,
        "read_last_states_before",
        AsyncMock(return_value=dict.fromkeys(("pv", "op"), initial) if initial else {}),
    )
    monkeypatch.setattr(lwb.meta, "applicable_anchors_batch", AsyncMock(return_value={}))
    return await lwb.build_logical_wide(None, "L-COV", ["pv", "op"], _at(0), _at(end), 1)


async def test_sparse_op_cov_has_full_coverage_and_confidence(monkeypatch):
    """PV 每秒，OP 每 20 分钟：事件数量不同不等于丢数，完整小时应为 A。"""
    raw = await _build(monkeypatch)
    block = _pipeline().process(raw, TagGroup.BASE, (_at(0), _at(3600)))

    assert raw.signals["op"] == [50.0] * 3601
    assert raw.series_context.known_slots("op") == 3601
    assert raw.series_context.unknown_reasons == {}
    assert block.loop_valid_rate == 1.0
    assert block.loop_confidence_level == "A"


async def test_window_initial_state_without_new_observation_remains_valid(monkeypatch):
    """窗前 40 秒的合法初值已由 builder 填到每槽，不需要窗内再发生变化。"""
    initial = {"ts": _at(-40), "value": 50.0, "quality_class": 1}
    raw = await _build(monkeypatch, end=9, initial=initial)
    block = _pipeline().process(raw, TagGroup.BASE, (_at(0), _at(9)))

    assert raw.series_context.role_coverage["op"].observed == []
    assert raw.series_context.known_slots("op") == 10
    assert block.loop_valid_rate == 1.0
    assert block.loop_confidence_level == "A"


async def test_registered_gap_is_not_validated_by_cov_hold(monkeypatch):
    """有真实 gap 登记，即使原值和质量存在也不得获得可信度。"""
    raw = await _build(monkeypatch, end=9, gaps=[(_at(0), _at(10))])
    block = _pipeline().process(raw, TagGroup.BASE, (_at(0), _at(9)))

    assert raw.signals["op"] == [50.0] * 10
    assert raw.series_context.unknown_slots("op") == 10
    assert block.loop_valid_rate == 0
    assert block.loop_confidence_level == "E"


async def test_gap_end_does_not_restore_pre_gap_op_state(monkeypatch):
    """gap 结束后 OP 未再次上报，旧值虽保留也不能重新成为已知状态。"""
    raw = await _build(monkeypatch, end=9, gaps=[(_at(2), _at(4))])
    cov = raw.series_context.role_coverage["op"]

    assert raw.signals["op"] == [50.0] * 10
    assert raw.quality_codes["pv_quality"] == [1] * 10
    assert raw.series_context.known_slots("op") == 2
    assert raw.series_context.unknown_slots("op") == 8
    assert cov.unknown == [IntervalRun(_at(2), _at(10), reason="gap")]


async def test_pre_window_gap_invalidates_old_initial_and_extends_gap_lookup(monkeypatch):
    """窗前已有 gap，必须追溯到初值来源时刻，不能只查评估窗口内缺口。"""
    initial = {"ts": _at(-40), "value": 50.0, "quality_class": 1}
    raw = await _build(monkeypatch, end=9, gaps=[(_at(-30), _at(-20))], initial=initial)

    queried_start = lwb._load_gap_windows.await_args.args[1]
    assert queried_start <= initial["ts"]
    assert raw.signals["op"] == [50.0] * 10
    assert raw.quality_codes["pv_quality"] == [1] * 10
    assert raw.series_context.known_slots("op") == 0
    assert raw.series_context.unknown_slots("op") == 10
    block = _pipeline().process(raw, TagGroup.BASE, (_at(0), _at(9)))
    assert block.loop_valid_rate == 0
    assert block.loop_confidence_level == "E"


async def test_own_point_event_after_gap_restores_known_state(monkeypatch):
    """gap 在 4s 结束，PV 持续上报不能恢复 OP；OP 在 6s 新上报后才恢复。"""
    raw = await _build(monkeypatch, end=9, gaps=[(_at(2), _at(4))], op_event_seconds=[0, 6])
    cov = raw.series_context.role_coverage["op"]

    assert raw.signals["op"] == [50.0] * 10
    assert raw.series_context.known_slots("op") == 6
    assert raw.series_context.unknown_slots("op") == 4
    assert cov.unknown == [IntervalRun(_at(2), _at(6), reason="gap")]
    assert raw.series_context.known_slots("pv") == 8


async def test_old_initial_without_gap_remains_known(monkeypatch):
    """一天前的初值没有经历登记缺口，仍可作为全窗 COV 保持依据。"""
    initial = {"ts": _at(-86400), "value": 50.0, "quality_class": 1}
    raw = await _build(monkeypatch, end=9, initial=initial)
    block = _pipeline().process(raw, TagGroup.BASE, (_at(0), _at(9)))

    assert raw.series_context.known_slots("op") == 10
    assert raw.series_context.unknown_slots("op") == 0
    assert block.loop_valid_rate == 1.0
    assert block.loop_confidence_level == "A"


@pytest.mark.parametrize("bad_quality", [0, -1])
def test_held_bad_or_unknown_quality_is_not_replaced_by_last_good(bad_quality):
    """逐槽质量优先于前一次 OBSERVED 的布尔值，坏段不能被前向洗白。"""
    coverage = RoleCoverage(
        known=[IntervalRun(_at(0), _at(10))],
        observed=[IntervalRun(_at(0), _at(1))],
    )
    raw = RawTimeSeries(
        timestamps=[_at(i) for i in range(10)],
        signals={"pv": [50.0] * 10, "op": [50.0] * 10},
        quality_codes={"pv_quality": [1] + [bad_quality] * 9},
        series_context=SeriesContext(
            layout="point",
            grid_start=_at(0),
            grid_end=_at(9),
            expected_slots=10,
            role_coverage={"pv": coverage, "op": coverage},
        ),
    )
    block = _pipeline().process(raw, TagGroup.BASE, (_at(0), _at(9)))

    assert block.validity["pv_valid"] == [True] + [False] * 9
    assert block.loop_valid_rate == pytest.approx(0.1)
    assert block.loop_confidence_level == "E"


@pytest.mark.parametrize(
    ("validity", "unknown", "expected"),
    [
        ([False, True, True, True], [], [False, True, True, True]),
        ([True, False, False, False], [], [True, False, False, False]),
        (
            [True, True, True, False, False, False],
            [IntervalRun(_at(2), _at(4), reason="rebind")],
            [True, True, False, False, False, False],
        ),
    ],
)
def test_held_slot_uses_its_own_validity_after_outlier_or_rebind(validity, unknown, expected):
    """局部异常恢复、坏值与改绑后的新值不继承旧 OBSERVED 的有效性。"""
    cov = RoleCoverage(
        known=[IntervalRun(_at(0), _at(len(validity)))],
        observed=[IntervalRun(_at(0), _at(1))],
        unknown=unknown,
    )
    actual = _pipeline()._zoh_validity(validity, [_at(i) for i in range(len(validity))], cov)
    assert actual == expected


def test_legacy_without_coverage_preserves_raw_time_coverage():
    """没有 COV 覆盖证据的 legacy 稀疏数据继续按 R14 计覆盖，不自动补齐。"""
    raw = RawTimeSeries(
        timestamps=[_at(i * 30) for i in range(121)],
        signals={"pv": [50.0] * 121, "op": [50.0] * 121},
        quality_codes={"pv_quality": [1] * 121},
    )
    block = _pipeline().process(raw, TagGroup.BASE, (_at(0), _at(3600)))

    assert block.series_context is None
    assert block.loop_valid_rate == pytest.approx(121 / 3601)
    assert block.loop_confidence_level == "E"


async def test_cov_policy_does_not_reuse_pre1053_l1_or_l2_cache(monkeypatch):
    """旧块即使仍在 TTL 内，新预处理/时间策略也不能在 L2 早返回时复用。"""
    from app.services.cache.l1_datablock import L1DataBlockCache
    from app.services.cache.l2_bundle import L2BundleCache
    from app.services.data_planner import resolve_data_version
    from app.services.data_source import history_layout_router
    from app.services.metric_data_bundle import MetricDataBundleAssembler
    from app.services.preprocessing.pipeline import PREPROCESS_VERSION
    from tests.test_data_planner.conftest import FakeCacheRedis, build_data_block

    monkeypatch.setattr(
        history_layout_router, "current_layout_version", AsyncMock(return_value="point-test")
    )
    current_version = await resolve_data_version()
    # 1053 = tqpv4/pre_v4；1054（全角色质量透传 + 锚点有效期）= tqpv5/pre_v5
    old_version = "point-test:tqpv4:pre_v4"
    assert current_version != old_version
    assert current_version == "point-test:tqpv5:pre_v5"
    redis = FakeCacheRedis()
    l1, l2 = L1DataBlockCache(redis), L2BundleCache(redis)

    def keys(version, pre_version):
        common = {
            "loop_id": "L-COV",
            "time_window_start": _at(0),
            "time_window_end": _at(9),
            "cfg_version": "cfg-fixed",
            "data_version": version,
        }
        return (
            l1.build_key(
                **common,
                tag_group="BASE",
                sampling_freq="1s",
                quality_policy="KEEP_ALL_WITH_VALIDITY",
                pre_version=pre_version,
            ),
            l2.build_key(**common, metrics=["accuracy_rate"], control_type=ControlType.FLOW.value),
        )

    old_l1, old_l2 = keys(old_version, "pre_v4")
    new_l1, new_l2 = keys(current_version, PREPROCESS_VERSION)
    old_block = build_data_block(loop_id="L-COV", n=10)
    old_block.preprocess_version = "pre_v4"
    bundle = MetricDataBundleAssembler().assemble(
        metric_code="accuracy_rate",
        data_block=old_block,
        mask_expression="pv_valid && sp_valid",
        requirement=None,
    )
    await l1.set(old_block, key=old_l1)
    await l2.set(old_l2, [bundle])

    assert await l1.get(old_l1) is not None
    assert await l2.get(old_l2) is not None
    assert await l1.get(new_l1) is None
    assert await l2.get(new_l2) is None
