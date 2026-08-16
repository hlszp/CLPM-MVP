"""诊断元算子包。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §5
架构：算子 = 无状态纯函数 + 自描述元数据，经 @operator 装饰器注册到 OPERATOR_REGISTRY。
无状态纪律：算子禁止 import DB/Redis/session，纯 numpy 计算；
可调参数全部经 threshold_schema 注入；同输入必同输出。
"""

from app.services.diagnosis_operators.base import (
    OPERATOR_REGISTRY,
    EvidenceItem,
    OperatorInput,
    OperatorMeta,
    OperatorResult,
    default_thresholds,
    get_operator,
    list_operators,
    operator,
)

__all__ = [
    "EvidenceItem",
    "OperatorInput",
    "OperatorMeta",
    "OperatorResult",
    "OPERATOR_REGISTRY",
    "default_thresholds",
    "get_operator",
    "list_operators",
    "operator",
]
