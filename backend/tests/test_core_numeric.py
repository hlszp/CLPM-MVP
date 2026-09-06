"""共享数值解析契约测试（R06，S0）.

覆盖审查报告 §7「工业数值与质量」盲区的字面量集合；前端等价实现由
前端侧测试对齐，本文件只锁定后端语义。
"""

from app.core.numeric import finite_or_none, parse_finite_float, parse_mode_int


class TestParseFiniteFloat:
    def test_valid_literals(self):
        assert parse_finite_float("12.5") == 12.5
        assert parse_finite_float(" 12.5 ") == 12.5  # 允许首尾空白
        assert parse_finite_float("1.5E3") == 1500.0  # 合法科学计数法
        assert parse_finite_float("-2.5e-2") == -0.025
        assert parse_finite_float(3) == 3.0
        assert parse_finite_float(-7.25) == -7.25
        assert parse_finite_float(0) == 0.0
        assert parse_finite_float("0") == 0.0

    def test_industrial_invalid_literals(self):
        # 工业组态软件推送的 NaN/坏值字面量必须全部判无效
        assert parse_finite_float("-1.#QNAN0") is None
        assert parse_finite_float("1.#QNAN0") is None
        assert parse_finite_float("nan") is None
        assert parse_finite_float("NaN") is None
        assert parse_finite_float("-nan") is None
        assert parse_finite_float("Infinity") is None
        assert parse_finite_float("-Infinity") is None
        assert parse_finite_float("inf") is None
        assert parse_finite_float("-inf") is None
        assert parse_finite_float("abc") is None

    def test_overflow_and_empty(self):
        assert parse_finite_float("1e999") is None  # 溢出为 inf → 无效
        assert parse_finite_float("-1e999") is None
        assert parse_finite_float(1e999) is None  # 源码字面量在解析期已是 inf
        assert parse_finite_float(float("inf")) is None
        assert parse_finite_float(float("nan")) is None
        assert parse_finite_float("") is None
        assert parse_finite_float("   ") is None
        assert parse_finite_float(None) is None
        assert parse_finite_float(True) is None  # bool 不作为数值
        assert parse_finite_float(False) is None


class TestParseModeInt:
    def test_valid(self):
        assert parse_mode_int("0") == 0
        assert parse_mode_int("2") == 2
        assert parse_mode_int(2.0) == 2
        assert parse_mode_int("2.7") == 2  # 向零截断
        assert parse_mode_int("-2.7") == -2
        assert parse_mode_int(str(2**31 - 1)) == 2**31 - 1
        assert parse_mode_int(str(-(2**31))) == -(2**31)

    def test_invalid(self):
        assert parse_mode_int("Infinity") is None  # 原实现 int(float(v)) 抛 OverflowError
        assert parse_mode_int("1e999") is None
        assert parse_mode_int("-1.#QNAN0") is None
        assert parse_mode_int("") is None
        assert parse_mode_int(None) is None
        assert parse_mode_int(str(2**31)) is None  # 超 int32 上界
        assert parse_mode_int(str(-(2**31) - 1)) is None
        assert parse_mode_int("abc") is None


class TestFiniteOrNull:
    def test_guard(self):
        assert finite_or_none(1.5) == 1.5
        assert finite_or_none(3) == 3.0
        assert finite_or_none(float("nan")) is None
        assert finite_or_none(float("inf")) is None
        assert finite_or_none("1.5") is None  # 仅守卫数值，不做字符串解析
        assert finite_or_none(True) is None
        assert finite_or_none(None) is None
