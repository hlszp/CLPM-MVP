"""Metric Validity Mask 表达式求值单元测试.

测试掩码表达式求值（&&, ||, !, 括号, consecutive_valid），
以及空表达式/None 返回全部索引的行为。

设计依据：算法说明 §3.4.2 步骤⑦, §3.6.1, PRD §5.5.4
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.contracts.data_types import DataBlock
from app.services.preprocessing.validity_mask import apply_mask

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_data_block(
    validity: dict[str, list[bool]],
    consecutive_segments: list[tuple[int, int]] | None = None,
    point_count: int | None = None,
) -> DataBlock:
    """构造测试用 DataBlock（仅填充 mask 求值所需字段）。"""
    n = point_count if point_count is not None else len(next(iter(validity.values())))
    timestamps = [datetime(2024, 1, 1) + timedelta(seconds=i) for i in range(n)]
    signals: dict[str, list] = {k.replace("_valid", ""): [0.0] * n for k in validity}
    return DataBlock(
        data_block_id="db_test_BASE_1s",
        loop_id="L001",
        tag_group="BASE",
        sampling_freq="1s",
        timestamps=timestamps,
        signals=signals,
        validity=validity,
        consecutive_segments=consecutive_segments or [],
        point_count=n,
    )


# ---------------------------------------------------------------------------
# 空表达式 / None
# ---------------------------------------------------------------------------


class TestEmptyMask:
    """空表达式或 None 返回全部索引。"""

    def test_none_expression_returns_all(self):
        """None 表达式返回全部索引（不筛选）。"""
        validity = {"pv_valid": [True, False, True, False]}
        db = _make_data_block(validity)
        indices = apply_mask(db, None)
        assert indices == [0, 1, 2, 3]

    def test_empty_string_returns_all(self):
        """空字符串返回全部索引。"""
        validity = {"pv_valid": [True, False, True, False]}
        db = _make_data_block(validity)
        assert apply_mask(db, "") == [0, 1, 2, 3]

    def test_whitespace_string_returns_all(self):
        """纯空白字符串返回全部索引。"""
        validity = {"pv_valid": [True, False, True, False]}
        db = _make_data_block(validity)
        assert apply_mask(db, "   ") == [0, 1, 2, 3]

    def test_empty_block_returns_empty(self):
        """空数据块返回空列表。"""
        validity = {"pv_valid": []}
        db = _make_data_block(validity, point_count=0)
        assert apply_mask(db, None) == []


# ---------------------------------------------------------------------------
# AND 运算符 (&&)
# ---------------------------------------------------------------------------


class TestAndOperator:
    """&& 运算符测试。"""

    def test_and_both_valid(self):
        """pv_valid && sp_valid 返回两者都有效的索引。"""
        validity = {
            "pv_valid": [True, False, True, False],
            "sp_valid": [True, True, False, False],
        }
        db = _make_data_block(validity)
        indices = apply_mask(db, "pv_valid && sp_valid")
        # 只有 index 0 两者都 True
        assert indices == [0]

    def test_and_all_true(self):
        """所有 valid 都为 True 时返回全部索引。"""
        validity = {
            "pv_valid": [True, True, True],
            "sp_valid": [True, True, True],
        }
        db = _make_data_block(validity)
        assert apply_mask(db, "pv_valid && sp_valid") == [0, 1, 2]

    def test_and_chain_three_variables(self):
        """三个变量链式 AND。"""
        validity = {
            "pv_valid": [True, True, False, True],
            "sp_valid": [True, False, True, True],
            "op_valid": [True, True, True, False],
        }
        db = _make_data_block(validity)
        # index 0: T && T && T = T
        # index 1: T && F && T = F
        # index 2: F && T && T = F
        # index 3: T && T && F = F
        assert apply_mask(db, "pv_valid && sp_valid && op_valid") == [0]


# ---------------------------------------------------------------------------
# OR 运算符 (||)
# ---------------------------------------------------------------------------


class TestOrOperator:
    """|| 运算符测试。"""

    def test_or_either_valid(self):
        """pv_valid || sp_valid 返回任一有效的索引。"""
        validity = {
            "pv_valid": [True, False, True, False],
            "sp_valid": [True, True, False, False],
        }
        db = _make_data_block(validity)
        indices = apply_mask(db, "pv_valid || sp_valid")
        # index 0: T || T = T
        # index 1: F || T = T
        # index 2: T || F = T
        # index 3: F || F = F
        assert indices == [0, 1, 2]

    def test_or_all_false(self):
        """所有 valid 都为 False 时返回空列表。"""
        validity = {
            "pv_valid": [False, False],
            "sp_valid": [False, False],
        }
        db = _make_data_block(validity)
        assert apply_mask(db, "pv_valid || sp_valid") == []


# ---------------------------------------------------------------------------
# NOT 运算符 (!)
# ---------------------------------------------------------------------------


class TestNotOperator:
    """! 运算符测试。"""

    def test_not_single_variable(self):
        """!pv_valid 返回 pv 无效的索引。"""
        validity = {"pv_valid": [True, False, True, False]}
        db = _make_data_block(validity)
        indices = apply_mask(db, "!pv_valid")
        assert indices == [1, 3]

    def test_double_not(self):
        """!!pv_valid 等价于 pv_valid。"""
        validity = {"pv_valid": [True, False, True]}
        db = _make_data_block(validity)
        assert apply_mask(db, "!!pv_valid") == [0, 2]


# ---------------------------------------------------------------------------
# 括号
# ---------------------------------------------------------------------------


class TestParentheses:
    """括号分组测试。"""

    def test_parentheses_grouping(self):
        """括号改变优先级：(pv_valid || sp_valid) && !op_valid。"""
        validity = {
            "pv_valid": [True, False, True, False],
            "sp_valid": [False, True, True, False],
            "op_valid": [False, False, False, True],
        }
        db = _make_data_block(validity)
        # (pv || sp) && !op
        # index 0: (T||F) && !F = T && T = T
        # index 1: (F||T) && !F = T && T = T
        # index 2: (T||T) && !F = T && T = T
        # index 3: (F||F) && !T = F && F = F
        indices = apply_mask(db, "(pv_valid || sp_valid) && !op_valid")
        assert indices == [0, 1, 2]

    def test_nested_parentheses(self):
        """嵌套括号。"""
        validity = {
            "pv_valid": [True, False, True],
            "sp_valid": [True, True, False],
        }
        db = _make_data_block(validity)
        # ((pv_valid)) && sp_valid
        # index 0: T && T = T
        # index 1: F && T = F
        # index 2: T && F = F
        assert apply_mask(db, "((pv_valid) && (sp_valid))") == [0]


# ---------------------------------------------------------------------------
# consecutive_valid
# ---------------------------------------------------------------------------


class TestConsecutiveValid:
    """consecutive_valid 变量测试。"""

    def test_consecutive_valid_mask(self):
        """consecutive_valid 返回连续有效段内的点。"""
        validity = {"pv_valid": [True] * 4}
        db = _make_data_block(validity, consecutive_segments=[(0, 2)])
        # consecutive_valid = [True, True, True, False]
        indices = apply_mask(db, "consecutive_valid")
        assert indices == [0, 1, 2]

    def test_consecutive_valid_multiple_segments(self):
        """多个连续段。"""
        validity = {"pv_valid": [True] * 8}
        db = _make_data_block(validity, consecutive_segments=[(0, 1), (4, 6)])
        # consecutive_valid = [T, T, F, F, T, T, T, F]
        indices = apply_mask(db, "consecutive_valid")
        assert indices == [0, 1, 4, 5, 6]

    def test_consecutive_valid_combined_with_and(self):
        """consecutive_valid && pv_valid 组合。"""
        validity = {"pv_valid": [True, True, False, True]}
        db = _make_data_block(validity, consecutive_segments=[(0, 2)])
        # consecutive = [T, T, T, F]
        # pv_valid   = [T, T, F, T]
        # AND        = [T, T, F, F]
        indices = apply_mask(db, "consecutive_valid && pv_valid")
        assert indices == [0, 1]


# ---------------------------------------------------------------------------
# 复杂表达式
# ---------------------------------------------------------------------------


class TestComplexExpressions:
    """复杂表达式组合测试。"""

    def test_complex_expression(self):
        """复杂表达式：pv_valid && (sp_valid || !op_valid)。"""
        validity = {
            "pv_valid": [True, True, False, True],
            "sp_valid": [False, True, True, False],
            "op_valid": [True, True, True, False],
        }
        db = _make_data_block(validity)
        # pv && (sp || !op)
        # index 0: T && (F || !T) = T && (F || F) = T && F = F
        # index 1: T && (T || !T) = T && (T || F) = T && T = T
        # index 2: F && (T || !T) = F
        # index 3: T && (F || !F) = T && (F || T) = T && T = T
        indices = apply_mask(db, "pv_valid && (sp_valid || !op_valid)")
        assert indices == [1, 3]

    def test_single_variable(self):
        """单变量表达式。"""
        validity = {"pv_valid": [True, False, True]}
        db = _make_data_block(validity)
        assert apply_mask(db, "pv_valid") == [0, 2]


# ---------------------------------------------------------------------------
# 边界情况
# ---------------------------------------------------------------------------


class TestMaskEdgeCases:
    """掩码求值边界情况。"""

    def test_unknown_variable_defaults_false(self):
        """未知变量默认为 False。"""
        validity = {"pv_valid": [True, True, True]}
        db = _make_data_block(validity)
        indices = apply_mask(db, "unknown_valid")
        assert indices == []

    def test_single_point(self):
        """单点数据掩码。"""
        validity = {"pv_valid": [True]}
        db = _make_data_block(validity)
        assert apply_mask(db, "pv_valid") == [0]
        assert apply_mask(db, "!pv_valid") == []

    def test_invalid_expression_returns_empty(self):
        """无效表达式导致求值失败时返回空列表。"""
        validity = {"pv_valid": [True, False]}
        db = _make_data_block(validity)
        # 语法错误：末尾缺少操作数
        indices = apply_mask(db, "pv_valid &&")
        assert indices == []
