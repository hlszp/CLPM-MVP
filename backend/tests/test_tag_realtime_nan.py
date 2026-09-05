"""测试 _build_tag_dict 对工业组态 NaN/非数字字符串的容错。

AAS/工业组态软件常推送 "-1.#QNAN0" / "nan" / "inf" 等 NaN 字面量，
直接 float() 会抛 ValueError，导致 GET /api/v1/tags 整页 500。
修复：解析失败回退到 DB 历史值。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.tag import _build_tag_dict


def _make_tag(tag_name: str, db_value: float | None = 12.5) -> MagicMock:
    tag = MagicMock()
    tag.tag_name = tag_name
    tag.current_value = db_value
    tag.id = "tag-id"
    tag.tag_type = "PV"
    tag.tag_description = ""
    tag.range_min = None
    tag.range_max = None
    tag.unit = None
    tag.measure_type = "PRESSURE"
    tag.last_sync_at = None
    tag.tdengine_tag_id = None
    tag.is_linked = False
    tag.measure_type_norm = "PRESSURE"
    tag.unit_norm = None
    tag.tag_type_norm = "PV"
    return tag


def test_nan_qnan_string_falls_back_to_db_value():
    tag = _make_tag("41FIC20021_PIDA.PV", db_value=3.14)
    cache = {"41FIC20021_PIDA.PV": {"value": "-1.#QNAN0", "quality": 1, "collectTime": "now"}}
    result = _build_tag_dict(tag, loop_info=None, realtime_cache=cache)
    # 解析失败 → 回退到 tag.current_value
    assert result["currentValue"] == 3.14


def test_lowercase_nan_string_falls_back_to_db_value():
    tag = _make_tag("tag_x", db_value=None)
    cache = {"tag_x": {"value": "nan", "quality": 0, "collectTime": "now"}}
    result = _build_tag_dict(tag, loop_info=None, realtime_cache=cache)
    # DB 也没有值 → 保持 None（不再 500）
    assert result["currentValue"] is None


def test_inf_string_falls_back_to_db_value():
    tag = _make_tag("tag_y", db_value=2.71)
    cache = {"tag_y": {"value": "inf", "quality": 1, "collectTime": "now"}}
    result = _build_tag_dict(tag, loop_info=None, realtime_cache=cache)
    assert result["currentValue"] == 2.71


def test_valid_numeric_string_uses_realtime_value():
    tag = _make_tag("tag_z", db_value=1.0)
    cache = {"tag_z": {"value": "42.5", "quality": 1, "collectTime": "now"}}
    result = _build_tag_dict(tag, loop_info=None, realtime_cache=cache)
    assert result["currentValue"] == 42.5
