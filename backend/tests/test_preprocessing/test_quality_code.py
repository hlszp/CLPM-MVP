"""质量码映射模块单元测试.

测试 map_quality_code / is_good_quality / is_nan_or_inf 三个函数，
覆盖 TDengine(1=Good, 0=Bad) / OPC DA(192=Good) / OPC UA(2,3=Good) 三种 schema。

设计依据：算法说明 §4.1.2, PRD §5.5.1
"""

from __future__ import annotations

import math

import pytest

from app.contracts.data_types import QualityStatus
from app.services.preprocessing.quality_code import (
    is_good_quality,
    is_nan_or_inf,
    map_quality_code,
)


# ---------------------------------------------------------------------------
# map_quality_code
# ---------------------------------------------------------------------------


class TestMapQualityCode:
    """map_quality_code 三态映射测试。"""

    @pytest.mark.parametrize(
        "raw_code,expected",
        [
            (None, QualityStatus.GOOD),
            (1, QualityStatus.GOOD),        # TDengine Good
            (2, QualityStatus.GOOD),        # OPC UA Good
            (3, QualityStatus.GOOD),        # OPC UA Good_Cascaded
            (192, QualityStatus.GOOD),      # OPC DA Good
            (0, QualityStatus.BAD),         # TDengine / OPC UA Bad
            (999, QualityStatus.UNKNOWN),   # 未知码
            (-1, QualityStatus.UNKNOWN),
            (100, QualityStatus.UNKNOWN),
        ],
    )
    def test_quality_code_mapping(self, raw_code, expected):
        """验证关键质量码映射：None→Good, 0→Bad, 1→Good, 192→Good, 999→Unknown。"""
        assert map_quality_code(raw_code) == expected

    def test_none_defaults_to_good(self):
        """None 缺省值视为 Good（容错设计）。"""
        assert map_quality_code(None) == QualityStatus.GOOD

    def test_string_numeric_codes(self):
        """字符串形式的质量码应能正确转换。"""
        assert map_quality_code("1") == QualityStatus.GOOD
        assert map_quality_code("0") == QualityStatus.BAD
        assert map_quality_code("192") == QualityStatus.GOOD

    def test_float_codes(self):
        """浮点形式的质量码应能正确转换。"""
        assert map_quality_code(1.0) == QualityStatus.GOOD
        assert map_quality_code(0.0) == QualityStatus.BAD
        assert map_quality_code(192.0) == QualityStatus.GOOD

    def test_invalid_string_returns_unknown(self):
        """无法解析的字符串返回 Unknown。"""
        assert map_quality_code("abc") == QualityStatus.UNKNOWN
        assert map_quality_code("") == QualityStatus.UNKNOWN

    def test_all_good_codes(self):
        """所有 Good 质量码集合 {1, 2, 3, 192} 都映射为 Good。"""
        for code in [1, 2, 3, 192]:
            assert map_quality_code(code) == QualityStatus.GOOD


# ---------------------------------------------------------------------------
# is_good_quality
# ---------------------------------------------------------------------------


class TestIsGoodQuality:
    """is_good_quality 判断函数测试。"""

    @pytest.mark.parametrize(
        "raw_code,expected",
        [
            (None, True),
            (1, True),
            (2, True),
            (3, True),
            (192, True),
            (0, False),
            (999, False),
            (-1, False),
        ],
    )
    def test_is_good_quality(self, raw_code, expected):
        """验证 is_good_quality 与 map_quality_code 一致性。"""
        assert is_good_quality(raw_code) == expected

    def test_none_is_good(self):
        """None 缺省视为 Good（容错）。"""
        assert is_good_quality(None) is True

    def test_bad_is_not_good(self):
        """0 (Bad) 不是 Good。"""
        assert is_good_quality(0) is False

    def test_unknown_is_not_good(self):
        """999 (Unknown) 不是 Good。"""
        assert is_good_quality(999) is False


# ---------------------------------------------------------------------------
# is_nan_or_inf
# ---------------------------------------------------------------------------


class TestIsNaNOrInf:
    """is_nan_or_inf 判断函数测试。"""

    def test_none_is_nan(self):
        """None 视为 NaN。"""
        assert is_nan_or_inf(None) is True

    def test_nan_is_nan(self):
        """float('nan') 检测为 NaN。"""
        assert is_nan_or_inf(float("nan")) is True

    def test_inf_is_nan(self):
        """float('inf') 检测为 NaN 类。"""
        assert is_nan_or_inf(float("inf")) is True

    def test_neg_inf_is_nan(self):
        """float('-inf') 检测为 NaN 类。"""
        assert is_nan_or_inf(float("-inf")) is True

    def test_normal_float_is_not_nan(self):
        """正常浮点数不是 NaN。"""
        assert is_nan_or_inf(1.0) is False
        assert is_nan_or_inf(0.0) is False
        assert is_nan_or_inf(-100.5) is False

    def test_int_is_not_nan(self):
        """整数不是 NaN。"""
        assert is_nan_or_inf(0) is False
        assert is_nan_or_inf(42) is False

    def test_numeric_string_is_not_nan(self):
        """可转换为 float 的字符串不是 NaN。"""
        assert is_nan_or_inf("1.5") is False
        assert is_nan_or_inf("0") is False

    def test_invalid_string_is_nan(self):
        """无法转换为 float 的字符串视为 NaN。"""
        assert is_nan_or_inf("abc") is True
        assert is_nan_or_inf("") is True

    def test_nan_string_is_nan(self):
        """'nan' 字符串转为 float 后是 NaN。"""
        assert is_nan_or_inf("nan") is True
        assert is_nan_or_inf("inf") is True
