"""质量码映射模块单元测试.

测试 map_quality_code / is_good_quality / is_nan_or_inf 三个函数，
覆盖 TDengine(1=Good, 0=Bad) / OPC DA(192=Good) / OPC UA(2,3=Good) 三种 schema。

设计依据：算法说明 §4.1.2, PRD §5.5.1
"""

from __future__ import annotations

import pytest

from app.contracts.data_types import QualityStatus
from app.services.preprocessing.quality_code import (
    _BAD_CODES,
    _GOOD_CODES,
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
            (1, QualityStatus.GOOD),  # TDengine Good
            (2, QualityStatus.GOOD),  # OPC UA Good
            (3, QualityStatus.GOOD),  # OPC UA Good_Cascaded
            (192, QualityStatus.GOOD),  # OPC DA Good
            (0, QualityStatus.BAD),  # TDengine / OPC UA Bad
            (999, QualityStatus.UNKNOWN),  # 未知码
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


# ---------------------------------------------------------------------------
# 映射逻辑完整性验证
# ---------------------------------------------------------------------------


class TestQualityCodeMappingLogic:
    """质量码映射逻辑完整性测试.

    验证 _GOOD_CODES / _BAD_CODES 集合的定义与 map_quality_code 行为一致，
    确保多 schema 兼容设计正确、集合无交集、类型转换健壮。
    """

    # ===== 集合定义验证 =====

    def test_good_codes_contains_all_expected(self):
        """_GOOD_CODES 必须包含 TDengine(1) + OPC UA(2,3) + OPC DA(192)。"""
        assert _GOOD_CODES == frozenset({1, 2, 3, 192})

    def test_bad_codes_contains_zero(self):
        """_BAD_CODES 必须包含 0（TDengine/OPC UA Bad）。"""
        assert _BAD_CODES == frozenset({0})

    def test_good_and_bad_sets_are_disjoint(self):
        """GOOD 和 BAD 集合不能有交集（否则映射歧义）。"""
        assert _GOOD_CODES.isdisjoint(_BAD_CODES)

    # ===== 多 schema 兼容验证 =====

    @pytest.mark.parametrize(
        "schema,code,expected",
        [
            # TDengine schema: 1=Good, 0=Bad
            ("TDengine", 1, QualityStatus.GOOD),
            ("TDengine", 0, QualityStatus.BAD),
            # OPC DA schema: 192=Good
            ("OPC DA", 192, QualityStatus.GOOD),
            # OPC UA schema: 2=Good, 3=Good_Cascaded, 0=Bad
            ("OPC UA", 2, QualityStatus.GOOD),
            ("OPC UA", 3, QualityStatus.GOOD),
            ("OPC UA", 0, QualityStatus.BAD),
        ],
    )
    def test_multi_schema_mapping(self, schema, code, expected):
        """验证三种 schema 的关键质量码都能正确映射。"""
        assert map_quality_code(code) == expected, f"{schema} schema: code={code} 期望 {expected}"

    # ===== TDengine 为主数据源的特殊处理 =====

    def test_code_1_is_good_for_tdengine(self):
        """1 在 OPC UA 中是 Uncertain，但本项目 TDengine 为主数据源（1=Good），故映射为 Good。"""
        assert map_quality_code(1) == QualityStatus.GOOD
        assert is_good_quality(1) is True

    # ===== 边界值验证 =====

    @pytest.mark.parametrize("code", list(_GOOD_CODES))
    def test_all_good_codes_map_to_good(self, code):
        """_GOOD_CODES 集合中每个码都必须映射为 Good。"""
        assert map_quality_code(code) == QualityStatus.GOOD

    @pytest.mark.parametrize("code", list(_BAD_CODES))
    def test_all_bad_codes_map_to_bad(self, code):
        """_BAD_CODES 集合中每个码都必须映射为 Bad。"""
        assert map_quality_code(code) == QualityStatus.BAD

    @pytest.mark.parametrize("code", [-1, -100, 4, 5, 99, 100, 191, 193, 200, 255, 1000])
    def test_non_good_non_bad_codes_map_to_unknown(self, code):
        """不在 GOOD/BAD 集合中的码都映射为 Unknown。"""
        assert map_quality_code(code) == QualityStatus.UNKNOWN

    # ===== 类型转换健壮性 =====

    @pytest.mark.parametrize("good_code", [1, 2, 3, 192])
    def test_string_form_of_good_codes(self, good_code):
        """Good 码的字符串形式也应映射为 Good。"""
        assert map_quality_code(str(good_code)) == QualityStatus.GOOD

    @pytest.mark.parametrize("good_code", [1, 2, 3, 192])
    def test_float_form_of_good_codes(self, good_code):
        """Good 码的浮点形式也应映射为 Good。"""
        assert map_quality_code(float(good_code)) == QualityStatus.GOOD

    def test_none_is_good(self):
        """None 缺省值容错为 Good（设计约束，避免缺数据时全部判 Bad）。"""
        assert map_quality_code(None) == QualityStatus.GOOD

    def test_invalid_types_return_unknown(self):
        """无法转换为数字的类型返回 Unknown。"""
        for val in ["abc", "", [], {}, object()]:
            assert map_quality_code(val) == QualityStatus.UNKNOWN

    # ===== map_quality_code 与 is_good_quality 一致性 =====

    @pytest.mark.parametrize(
        "raw_code",
        [None, 0, 1, 2, 3, 192, -1, 999, "0", "1", "192", "abc", 1.0, 0.0],
    )
    def test_is_good_quality_consistency(self, raw_code):
        """is_good_quality(x) == (map_quality_code(x) == GOOD) 必须对所有输入成立。"""
        expected = map_quality_code(raw_code) == QualityStatus.GOOD
        assert is_good_quality(raw_code) == expected
