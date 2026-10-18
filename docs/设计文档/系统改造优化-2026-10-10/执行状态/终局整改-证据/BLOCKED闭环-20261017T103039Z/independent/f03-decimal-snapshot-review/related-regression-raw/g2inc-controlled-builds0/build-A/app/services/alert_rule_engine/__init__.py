"""智能预警规则引擎核心服务（方案 §4.1）。

模块组成：
- dsl        DSL 解析与校验（§3 规则类型与表达式）
- evaluator  规则求值（阈值/漂移/组合/可信度/时效）
- suppressor 抑制/去抖/冷却/去重
- dispatcher 动作分发（CREATE_EVENT/CREATE_TRACKER/NOTIFY）
- cache      规则缓存（Redis 单层 30s TTL）
- audit      规则变更审计
"""

from app.services.alert_rule_engine.dispatcher import dispatch
from app.services.alert_rule_engine.dsl import ValidationError, validate_dsl
from app.services.alert_rule_engine.evaluator import (
    EvaluationResult,
    evaluate_loop_rules,
    evaluate_rule,
)
from app.services.alert_rule_engine.suppressor import Suppressor

__all__ = [
    "EvaluationResult",
    "Suppressor",
    "ValidationError",
    "dispatch",
    "evaluate_loop_rules",
    "evaluate_rule",
    "validate_dsl",
]
