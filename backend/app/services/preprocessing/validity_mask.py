"""Metric Validity Mask 生成模块.

根据各指标的数据需求契约（clpm_metric_data_requirement.mask_expression），
生成指标级有效性掩码，决定 DataBlock 中哪些点参与该指标的计算。

掩码表达式语法（算法说明 §3.4.2 步骤⑦, §3.6.1）：
    - 变量：``{tag}_valid``（如 ``pv_valid`` / ``sp_valid`` / ``op_valid`` / ``mode_valid``）
    - 运算符：``&&``（AND）、``||``（OR）、``!``（NOT）
    - 特殊值：``consecutive_valid``（连续有效段内的点）
    - 空表达式或 None：表示不筛选（全部点参与，如好值率）

设计依据：算法说明 §3.4.2 步骤⑦, §3.6.1, PRD §5.5.4
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.contracts.data_types import DataBlock

logger = logging.getLogger(__name__)


def apply_mask(
    data_block: DataBlock,
    mask_expression: str | None,
) -> list[int]:
    """应用 Metric Validity Mask，返回有效索引列表.

    将掩码表达式求值为每个时间戳的布尔值，返回 True 的索引列表。
    指标计算器只使用 masked_indices 中的点进行计算。

    Args:
        data_block: 预处理后的数据块
        mask_expression: 掩码表达式，如 ``"pv_valid && sp_valid"``；
            None 或空字符串表示不筛选（返回全部索引）

    Returns:
        有效索引列表（升序）

    设计依据：算法说明 §3.4.2 步骤⑦, PRD §5.5.4
    """
    n = data_block.point_count
    if n == 0:
        return []

    # 空表达式：不筛选（如好值率统计需要全量数据）
    if not mask_expression or not mask_expression.strip():
        return list(range(n))

    # 逐点求值
    mask = _evaluate_mask(data_block, mask_expression)
    indices = [i for i, valid in enumerate(mask) if valid]

    logger.debug(
        "apply_mask: expr=%r, total=%d, masked=%d (%.1f%%)",
        mask_expression,
        n,
        len(indices),
        (len(indices) / n * 100) if n else 0,
    )
    return indices


def _evaluate_mask(data_block: DataBlock, expr: str) -> list[bool]:
    """将掩码表达式求值为布尔数组.

    支持运算符：``&&`` / ``||`` / ``!`` / 括号 ``()``。
    变量从 data_block.validity 和 consecutive_segments 解析。

    Args:
        data_block: 数据块
        expr: 掩码表达式

    Returns:
        布尔数组（长度 = point_count）
    """
    n = data_block.point_count
    expr = expr.strip()

    # 构建变量上下文：每个 {tag}_valid → list[bool]
    context: dict[str, list[bool]] = {}
    for key, vals in data_block.validity.items():
        context[key] = list(vals)

    # consecutive_valid：连续有效段内的点为 True
    consecutive = [False] * n
    for start, end in data_block.consecutive_segments:
        for i in range(start, min(end + 1, n)):
            consecutive[i] = True
    context["consecutive_valid"] = consecutive

    # 编译只缓存不可变指令，不缓存 DataBlock、validity 或求值结果。
    # 每次调用重新消费当前布尔向量，避免修改数据后复用旧 mask。
    try:
        return _evaluate_program(_compile_expr(expr), context, n)
    except Exception:
        # 无效语法对整块均为 False；一条表达式记录一次，避免逐点刷屏。
        logger.warning("Mask eval failed: expr=%r", expr, exc_info=True)
        return [False] * n


# ---------------------------------------------------------------------------
# 简单布尔表达式求值器
# ---------------------------------------------------------------------------


@lru_cache(maxsize=256)
def _compile_expr(expr: str) -> tuple[tuple[str, ...], ...]:
    """Compile the complete grammar into immutable postfix instructions."""
    tokens = _tokenize(expr)
    parser = _BoolParser(tokens)
    parser.parse_or()
    if parser.pos != len(tokens):
        raise ValueError(f"Unexpected token at pos {parser.pos}: {tokens[parser.pos :]}")
    return tuple(parser.instructions)


def _evaluate_program(
    program: tuple[tuple[str, ...], ...], context: dict[str, list[bool]], n: int
) -> list[bool]:
    """Evaluate per-variable vectors, with missing/short arrays padded False."""
    stack: list[list[bool]] = []
    for instruction in program:
        op = instruction[0]
        if op == "var":
            values = context.get(instruction[1], [])
            vector = [bool(value) for value in values[:n]]
            vector.extend([False] * (n - len(vector)))
            stack.append(vector)
        elif op == "not":
            stack.append([not value for value in stack.pop()])
        else:
            right, left = stack.pop(), stack.pop()
            if op == "and":
                stack.append([a and b for a, b in zip(left, right, strict=True)])
            else:
                stack.append([a or b for a, b in zip(left, right, strict=True)])
    return stack[0]


def _eval_expr(expr: str, env: dict[str, bool]) -> bool:
    """递归下降布尔表达式求值.

    支持：``&&`` / ``||`` / ``!`` / ``()`` / 变量名。
    不调用 eval()，安全沙箱。
    """
    return _evaluate_program(_compile_expr(expr), {key: [value] for key, value in env.items()}, 1)[
        0
    ]


def _tokenize(expr: str) -> list[str]:
    """将表达式分词为 token 列表."""
    tokens: list[str] = []
    i = 0
    while i < len(expr):
        if expr[i].isspace():
            i += 1
            continue
        # 两个字符的运算符
        two = expr[i : i + 2]
        if two in ("&&", "||"):
            tokens.append(two)
            i += 2
            continue
        # 单字符
        c = expr[i]
        if c in "()!":
            tokens.append(c)
            i += 1
            continue
        # 变量名
        if c.isalpha() or c == "_":
            j = i
            while j < len(expr) and (expr[j].isalnum() or expr[j] == "_"):
                j += 1
            tokens.append(expr[i:j])
            i = j
            continue
        raise ValueError(f"Unknown char {c!r} at pos {i} in expr {expr!r}")
    return tokens


class _BoolParser:
    """递归下降布尔表达式解析器.

    Grammar:
        or_expr  := and_expr ('||' and_expr)*
        and_expr := not_expr ('&&' not_expr)*
        not_expr := '!' not_expr | primary
        primary  := '(' or_expr ')' | VAR
    """

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.instructions: list[tuple[str, ...]] = []

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self) -> str:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse_or(self) -> None:
        self.parse_and()
        while self.peek() == "||":
            self.consume()
            self.parse_and()
            self.instructions.append(("or",))

    def parse_and(self) -> None:
        self.parse_not()
        while self.peek() == "&&":
            self.consume()
            self.parse_not()
            self.instructions.append(("and",))

    def parse_not(self) -> None:
        if self.peek() == "!":
            self.consume()
            self.parse_not()
            self.instructions.append(("not",))
        else:
            self.parse_primary()

    def parse_primary(self) -> None:
        tok = self.peek()
        if tok == "(":
            self.consume()
            self.parse_or()
            if self.peek() != ")":
                raise ValueError("Expected ')'")
            self.consume()
            return
        if tok is not None and (tok[0].isalpha() or tok[0] == "_"):
            self.consume()
            self.instructions.append(("var", tok))
            return
        raise ValueError(f"Unexpected token: {tok!r} at pos {self.pos}")
