"""P1 单元测试：点事件校验/哈希/子表命名/布局兜底（无需外部依赖）."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.data_source import history_layout as layout
from app.services.data_source import point_history_repository as repo


def _ev(**kw) -> repo.PointEvent:
    base = {
        "point_id": "11111111-2222-3333-4444-555555555555",
        "ts": datetime(2026, 9, 6, tzinfo=UTC),
        "value": 1.0,
        "quality_class": repo.QC_GOOD,
        "quality_raw": 1,
        "quality_schema": repo.QSCHEMA_AAS,
    }
    base.update(kw)
    return repo.PointEvent(**base)


class TestPointEvent:
    def test_rejects_naive_ts(self):
        with pytest.raises(ValueError, match="aware"):
            _ev(ts=datetime(2026, 9, 6))

    def test_rejects_bad_quality_class(self):
        with pytest.raises(ValueError, match="quality_class"):
            _ev(quality_class=7)

    def test_rejects_bad_source_kind(self):
        with pytest.raises(ValueError, match="source_kind"):
            _ev(source_kind=99)

    def test_rejects_bad_quality_schema(self):
        with pytest.raises(ValueError, match="quality_schema"):
            _ev(quality_schema=5)

    def test_nonfinite_value_becomes_none(self):
        ev = _ev(value=float("nan"))
        assert ev.value is None
        ev2 = _ev(value=float("inf"))
        assert ev2.value is None

    def test_payload_hash_semantic_dedup(self):
        a = _ev(source_kind=repo.SOURCE_KIND_COV)
        b = _ev(source_kind=repo.SOURCE_KIND_HISTORY)
        assert a.payload_hash() == b.payload_hash()  # 同事实不同来源 → 同 hash
        c = _ev(value=2.0)
        assert a.payload_hash() != c.payload_hash()
        d = _ev(quality_class=repo.QC_BAD, quality_raw=0)
        assert a.payload_hash() != d.payload_hash()
        # 等价 UUID 表示（大小写/连字符）→ 同 hash
        e = _ev(point_id="11111111-2222-3333-4444-555555555555".upper())
        assert a.payload_hash() == e.payload_hash()


class TestSubtableName:
    def test_dashed_and_hex_uuid_same_table(self):
        dashed = "11111111-2222-3333-4444-555555555555"
        assert repo.point_subtable(dashed) == "p_" + dashed.replace("-", "")
        assert repo.point_subtable(dashed.upper()) == repo.point_subtable(dashed)

    def test_rejects_non_uuid(self):
        for bad in ("LIC-101.PV", "p_1234", "'; DROP TABLE st_point_data_v1; --", ""):
            with pytest.raises(ValueError, match="非法点身份"):
                repo.point_subtable(bad)


class TestWritebackRouting:
    def test_mode_matrix(self):
        assert layout.writeback_enabled_for("legacy") == (True, False)
        assert layout.writeback_enabled_for("shadow") == (True, True)
        assert layout.writeback_enabled_for("point") == (False, True)
        with pytest.raises(ValueError, match="非法布局"):
            layout.writeback_enabled_for("both")

    def test_storage_mode_rows_fallback(self):
        from app.models.sys_config import SysConfig

        # sys_config 无行 → settings 兜底 → legacy
        assert layout.get_storage_mode_from_rows({}) == "legacy"
        # 脏值回退
        bad = SysConfig(key=layout.SYS_KEY_STORAGE_MODE, value="yolo")
        assert layout.get_storage_mode_from_rows({layout.SYS_KEY_STORAGE_MODE: bad}) == "legacy"
        # 合法值
        good = SysConfig(key=layout.SYS_KEY_STORAGE_MODE, value="shadow")
        assert layout.get_storage_mode_from_rows({layout.SYS_KEY_STORAGE_MODE: good}) == "shadow"


class TestFormatTsUtc:
    def test_naive_treated_as_utc(self):
        dt = datetime(2026, 9, 6, 0, 0, 0, 123000)
        assert repo.format_ts_utc(dt) == "2026-09-06T00:00:00.123Z"

    def test_aware_converted(self):
        from datetime import timezone

        plus8 = timezone(timedelta(hours=8))
        dt = datetime(2026, 9, 6, 8, 0, 0, 123000, tzinfo=plus8)
        assert repo.format_ts_utc(dt) == "2026-09-06T00:00:00.123Z"
