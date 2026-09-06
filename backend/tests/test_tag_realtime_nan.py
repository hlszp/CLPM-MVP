"""测试 _build_tag_dict 对工业组态 NaN/非数字字符串的容错（R06 数据链路整改）.

AAS/工业组态软件常推送 "-1.#QNAN0" / "nan" / "inf" 等 NaN 字面量，
直接 float() 会抛 ValueError，导致 GET /api/v1/tags 整页 500。

R06 契约（S0 §3）：
- 解析走共享模块 ``app/core/numeric.py``（parse_finite_float）；
- 新值无效时 **不得把 DB 旧值与新 quality/collectTime 拼接成"最新有效读数"**：
  currentValue=null、quality=最新质量、stale=true（旧客户端可忽略）；
- 数值有效性与 quality 相互独立；
- 合法科学计数法照常解析。
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


def test_qnan_string_value_null_stale_true_quality_kept():
    """新值无效：currentValue=null + stale=true，不以 DB 旧值伪装有效读数."""
    tag = _make_tag("41FIC20021_PIDA.PV", db_value=3.14)
    cache = {"41FIC20021_PIDA.PV": {"value": "-1.#QNAN0", "quality": 0, "collectTime": "now"}}
    result = _build_tag_dict(tag, loop_info=None, realtime_cache=cache)
    assert result["currentValue"] is None
    assert result["stale"] is True
    # 质量按本条消息更新（数值有效性与质量独立）
    assert result["quality"] == "BAD"
    assert result["lastSyncAt"] == "now"


def test_lowercase_nan_string_value_null_not_db():
    tag = _make_tag("tag_x", db_value=12.5)
    cache = {"tag_x": {"value": "nan", "quality": 0, "collectTime": "now"}}
    result = _build_tag_dict(tag, loop_info=None, realtime_cache=cache)
    assert result["currentValue"] is None
    assert result["stale"] is True
    assert result["quality"] == "BAD"


def test_inf_string_value_null_not_db():
    tag = _make_tag("tag_y", db_value=2.71)
    cache = {"tag_y": {"value": "inf", "quality": 1, "collectTime": "now"}}
    result = _build_tag_dict(tag, loop_info=None, realtime_cache=cache)
    assert result["currentValue"] is None
    assert result["stale"] is True
    # 无效数值不吞质量更新：quality=1 → GOOD（值与质量独立表达）
    assert result["quality"] == "GOOD"


def test_overflow_scientific_string_value_null():
    """1e999 溢出为 inf → 无效 → null + stale."""
    tag = _make_tag("tag_o", db_value=1.0)
    cache = {"tag_o": {"value": "1e999", "quality": 1, "collectTime": "now"}}
    result = _build_tag_dict(tag, loop_info=None, realtime_cache=cache)
    assert result["currentValue"] is None
    assert result["stale"] is True


def test_empty_string_value_null():
    """空串=本次无值 → null + stale（不折算为 0，不回退 DB）."""
    tag = _make_tag("tag_e", db_value=7.0)
    cache = {"tag_e": {"value": "", "quality": 0, "collectTime": "now"}}
    result = _build_tag_dict(tag, loop_info=None, realtime_cache=cache)
    assert result["currentValue"] is None
    assert result["stale"] is True


def test_valid_scientific_notation_parsed():
    """合法科学计数法照常解析."""
    tag = _make_tag("tag_s", db_value=1.0)
    cache = {"tag_s": {"value": "1.5E3", "quality": 1, "collectTime": "now"}}
    result = _build_tag_dict(tag, loop_info=None, realtime_cache=cache)
    assert result["currentValue"] == 1500.0
    assert result["stale"] is False


def test_valid_numeric_string_uses_realtime_value():
    tag = _make_tag("tag_z", db_value=1.0)
    cache = {"tag_z": {"value": "42.5", "quality": 1, "collectTime": "now"}}
    result = _build_tag_dict(tag, loop_info=None, realtime_cache=cache)
    assert result["currentValue"] == 42.5
    assert result["stale"] is False


def test_no_realtime_cache_keeps_db_value_not_stale():
    """无实时缓存：沿用 DB 值，不标旧（未收到无效新值）."""
    tag = _make_tag("tag_n", db_value=9.9)
    result = _build_tag_dict(tag, loop_info=None, realtime_cache={})
    assert result["currentValue"] == 9.9
    assert result["stale"] is False
