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
import re

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

    # 逐点求值（简单表达式引擎，支持 && / || / ! / 括号 / 变量）
    result = [False] * n
    for i in range(n):
        env = {k: v[i] if i < len(v) else False for k, v in context.items()}
        try:
            result[i] = _eval_expr(expr, env)
        except Exception:
            logger.warning("Mask eval failed at idx=%d: expr=%r", i, expr, exc_info=True)
            result[i] = False
    return result


# ---------------------------------------------------------------------------
# 简单布尔表达式求值器
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\s*(?:(\()|(\))|(&&)|(\"\|\|\")|(\|\|)|(!)|([a-zA-Z_]\w*))")


def _eval_expr(expr: str, env: dict[str, bool]) -> bool:
    """递归下降布尔表达式求值.

    支持：``&&`` / ``||`` / ``!`` / ``()`` / 变量名。
    不调用 eval()，安全沙箱。
    """
    tokens = _tokenize(expr)
    parser = _BoolParser(tokens, env)
    result = parser.parse_or()
    if parser.pos != len(tokens):
        raise ValueError(f"Unexpected token at pos {parser.pos}: {tokens[parser.pos :]}")
    return result


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

    def __init__(self, tokens: list[str], env: dict[str, bool]) -> None:
        self.tokens = tokens
        self.env = env
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self) -> str:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse_or(self) -> bool:
        result = self.parse_and()
        while self.peek() == "||":
            self.consume()
            rhs = self.parse_and()
            result = result or rhs
        return result

    def parse_and(self) -> bool:
        result = self.parse_not()
        while self.peek() == "&&":
            self.consume()
            rhs = self.parse_not()
            result = result and rhs
        return result

    def parse_not(self) -> bool:
        if self.peek() == "!":
            self.consume()
            return not self.parse_not()
        return self.parse_primary()

    def parse_primary(self) -> bool:
        tok = self.peek()
        if tok == "(":
            self.consume()
            result = self.parse_or()
            if self.peek() != ")":
                raise ValueError("Expected ')'")
            self.consume()
            return result
        if tok is not None and (tok[0].isalpha() or tok[0] == "_"):
            self.consume()
            return bool(self.env.get(tok, False))
        raise ValueError(f"Unexpected token: {tok!r} at pos {self.pos}")
