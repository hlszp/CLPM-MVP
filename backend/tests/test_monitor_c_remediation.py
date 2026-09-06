"""数据链路整改 S3/C：monitor.py R06 安全解析 + R17 modeMapping 测试.

R06（共享数值契约）:
- Redis 实时缓存值无效（"-1.#QNAN0"/"nan"/"Infinity"/空值）时
  currentValues 对应字段为 None，**不以 DB 旧值伪装最新读数**；
- 质量码/collectTime 仍按本条缓存更新。

R17（MODE 映射下发）:
- 列表项携带 ``modeMapping``（无自定义配置 → 默认 {0:Manual,1:Auto,2:Cascade,3:Auto,4:Auto}）；
- 自定义 loop_mode_mapping 配置时下发自定义映射（键为字符串，JSON 安全）；
- 详情 currentValues 同样携带 modeMapping。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.services.monitor import (
    effective_mode_mapping,
    get_loop_monitor_detail,
    list_loop_monitor,
)


def _scalars(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _count(n: int) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = n
    return result


def _one(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _iterable(rows: list) -> MagicMock:
    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter(rows))
    return result


def _loop() -> MagicMock:
    loop = MagicMock()
    loop.id = "loop-001"
    loop.tag_name = "LIC-101"
    loop.description = "液位"
    loop.unit_id = None
    loop.status = "READY"
    loop.is_active = True
    loop.loop_type = "SLOW"
    return loop


def _tag(tag_id: str, name: str, db_value: object = 50.0) -> MagicMock:
    tag = MagicMock()
    tag.id = tag_id
    tag.tag_name = name
    tag.current_value = db_value
    tag.quality = "GOOD"
    tag.last_sync_at = None
    tag.range_min = None
    tag.range_max = None
    tag.unit = None
    return tag


def _mapping(role: str, tag_id: str) -> MagicMock:
    m = MagicMock()
    m.loop_id = "loop-001"
    m.tag_role = role
    m.tag_id = tag_id
    return m


def _mode_row(mode_value: int, mode_label: str) -> MagicMock:
    row = MagicMock()
    row.loop_id = "loop-001"
    row.mode_value = mode_value
    row.mode_label = mode_label
    return row


_DEFAULT_MAPPING = {"0": "Manual", "1": "Auto", "2": "Cascade", "3": "Auto", "4": "Auto"}

# total>0 且非深链接时 list_loop_monitor 会先计算 aggregate，测试统一 mock 掉
_mock_aggregate_patcher = patch(
    "app.services.monitor._build_loop_monitor_aggregate",
    new_callable=AsyncMock,
    return_value={},
)


def _make_list_db(mode_rows: list | None = None) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _count(1),
            _scalars([_loop()]),
            _scalars([]),  # mappings（空 → 跳过 tags 查询）
            _scalars([]),  # KPI 快照
            _scalars([]),  # 昨日基线
            _iterable(mode_rows or []),  # mode mapping 查询（直接迭代）
            _scalars([]),  # 完整性巡检
        ]
    )
    return db


class TestEffectiveModeMapping:
    """R17：effective_mode_mapping 单元行为."""

    def test_none_returns_default(self) -> None:
        assert effective_mode_mapping(None) == _DEFAULT_MAPPING

    def test_custom_mapping_json_safe_keys(self) -> None:
        assert effective_mode_mapping({5: "Auto", 0: "Manual"}) == {
            "5": "Auto",
            "0": "Manual",
        }


class TestListMonitorR06R17:
    """list_loop_monitor：R06 安全解析 + R17 modeMapping 下发."""

    _patcher = _mock_aggregate_patcher

    @classmethod
    def setup_class(cls) -> None:
        _mock_aggregate_patcher.start()

    @classmethod
    def teardown_class(cls) -> None:
        _mock_aggregate_patcher.stop()

    async def test_invalid_cached_value_null_not_db_fallback(self) -> None:
        """R06：缓存值无效 → None（不回退 DB 旧值），质量仍更新."""
        loop = _loop()
        pv = _tag("tag-pv", "LIC-101.PV", db_value=42.0)
        mode = _tag("tag-mode", "LIC-101.MODE", db_value=1)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _count(1),
                _scalars([loop]),
                _scalars([_mapping("PV", "tag-pv"), _mapping("MODE", "tag-mode")]),
                _scalars([pv, mode]),
                _scalars([]),  # KPI 快照
                _scalars([]),  # 昨日基线
                _iterable([]),  # mode mapping（默认）
                _scalars([]),  # 完整性巡检
            ]
        )
        cached = [
            {
                "tagCode": "LIC-101.PV",
                "value": "-1.#QNAN0",
                "quality": 0,
                "collectTime": "2026-09-06T10:00:00Z",
            },
            {
                "tagCode": "LIC-101.MODE",
                "value": "Infinity",
                "quality": 1,
                "collectTime": "2026-09-06T10:00:00Z",
            },
        ]

        async def _fake_get_cached(tag_names):
            return [c for c in cached if c["tagCode"] in tag_names]

        with patch(
            "app.services.monitor.get_subscriber",
            return_value=MagicMock(get_cached_values=_fake_get_cached),
        ):
            item = (await list_loop_monitor(db))["items"][0]
        # 无效值 → None，而不是 DB 旧值 42.0 / 1
        assert item["currentValues"]["pv"] is None
        assert item["currentValues"]["mode"] is None
        assert item["currentValues"]["modeLabel"] is None
        # 质量按缓存消息更新（值与质量独立）
        assert item["currentValues"]["pvQuality"] == "BAD"

    async def test_mode_mapping_default_delivered(self) -> None:
        """R17：无自定义配置时列表项携带默认映射（JSON 字符串键）."""
        db = _make_list_db(mode_rows=[])
        with patch(
            "app.services.monitor.get_subscriber",
            return_value=MagicMock(get_cached_values=AsyncMock(return_value=[])),
        ):
            item = (await list_loop_monitor(db))["items"][0]
        assert item["modeMapping"] == _DEFAULT_MAPPING

    async def test_mode_mapping_custom_delivered(self) -> None:
        """R17：自定义正数映射（如 2→MANUAL）原样下发（与 REST 标签口径一致）."""
        db = _make_list_db(mode_rows=[_mode_row(2, "MANUAL"), _mode_row(1, "AUTO")])
        with patch(
            "app.services.monitor.get_subscriber",
            return_value=MagicMock(get_cached_values=AsyncMock(return_value=[])),
        ):
            item = (await list_loop_monitor(db))["items"][0]
        assert item["modeMapping"] == {"2": "Manual", "1": "Auto"}


class TestDetailModeMapping:
    """get_loop_monitor_detail：R17 详情 currentValues.modeMapping."""

    async def test_detail_carries_mode_mapping(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _one(_loop()),  # loop 查询
                _scalars([]),  # mappings（_get_loop_tag_values；空 → 跳过 tags 查询）
                _iterable([_mode_row(2, "CAS")]),  # _load_mode_mappings
                _scalars([]),  # KPI 快照
            ]
        )
        with (
            patch(
                "app.services.monitor.get_subscriber",
                return_value=MagicMock(get_cached_values=AsyncMock(return_value=[])),
            ),
            patch("app.services.trend_service.fetch_loop_trend") as fetch_trend,
        ):
            fetch_trend.return_value = {
                "pointCount": 0,
                "timestamps": [],
                "pv": [],
                "sp": [],
                "op": [],
                "mode": [],
                "pvQuality": [],
                "sampleInterval": None,
                "downsampled": False,
            }
            detail = await get_loop_monitor_detail(db, "loop-001")
        assert detail["currentValues"]["modeMapping"] == {"2": "Cascade"}
