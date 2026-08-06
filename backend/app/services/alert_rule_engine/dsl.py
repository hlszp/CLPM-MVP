"""DSL 解析与校验（方案 §3 + 附录 A）。

4 类规则（THRESHOLD/DRIFT/COMPOSITE/CONFIDENCE）+ 通用字段
（durationSeconds/cooldownSeconds/severity/confidencePolicy/timeWindow/actions/
priority/dedupKey）。校验规则见附录 A。

使用：``validate_dsl(dsl_dict)`` → 校验通过返回 normalized DSL，失败抛 ValidationError。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 枚举常量（对齐方案 §3）
# ---------------------------------------------------------------------------

RULE_TYPES = frozenset({"THRESHOLD", "DRIFT", "COMPOSITE", "CONFIDENCE"})
LOOP_SELECTOR_TYPES = frozenset({"ALL", "LOOP", "PLANT", "CONTROL_TYPE"})
METRICS = frozenset({"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"})
OPERATORS = frozenset({">", ">=", "<", "<=", "==", "!=", "IN", "NOT_IN", "RATE_OF_CHANGE"})
SEVERITIES = frozenset({"INFO", "WARN", "ERROR", "CRITICAL"})
CONFIDENCE_LEVELS = frozenset({"A", "B", "C", "D", "E"})
CONFIDENCE_ACTIONS = frozenset({"SUPPRESS", "DOWNGRADE"})
ACTION_TYPES = frozenset({"CREATE_EVENT", "CREATE_TRACKER", "NOTIFY"})
STATISTICS = frozenset({"MEAN", "STDDEV", "P95", "P99", "MIN", "MAX"})
DEVIATION_TYPES = frozenset({"ABSOLUTE", "RELATIVE", "SIGMA"})
BASELINE_TYPES = frozenset({"STATIC", "HISTORICAL", "RULE_BASED"})
COMPOSITE_LOGIC = frozenset({"AND", "OR", "NOT", "SEQUENCE"})
COMPOSITE_OPERAND_TYPES = frozenset({"THRESHOLD", "DRIFT", "CONFIDENCE", "COMPOSITE"})
DEDUP_KEY_VARS = frozenset({"loop_id", "rule_id", "tag_code", "severity"})

MAX_DSL_LENGTH = 4000
MAX_NESTING_DEPTH = 3
MIN_WINDOW_SECONDS = 300
MAX_WINDOW_SECONDS = 86400
MAX_DURATION_SECONDS = 86400


@dataclass
class ValidationError(Exception):
    """DSL 校验失败。携带字段级错误列表。"""

    errors: list[dict[str, str]] = field(default_factory=list)

    def __init__(self, errors: list[dict[str, str]] | None = None, message: str = "") -> None:
        self.errors = errors or []
        if not message:
            message = "; ".join(f"{e['field']}: {e['message']}" for e in self.errors)
        super().__init__(message)


# ---------------------------------------------------------------------------
# 校验入口
# ---------------------------------------------------------------------------


def validate_dsl(dsl: dict[str, Any]) -> dict[str, Any]:
    """校验 DSL 结构（方案附录 A）。

    Args:
        dsl: 规则 DSL 字典

    Returns:
        通过校验的 DSL 字典（原样返回）

    Raises:
        ValidationError: 校验失败，``errors`` 含字段级错误
    """
    errors: list[dict[str, str]] = []

    if not isinstance(dsl, dict):
        raise ValidationError([{"field": "dsl", "message": "DSL 必须为 JSON 对象"}])

    # DSL 序列化长度
    try:
        dsl_str = json.dumps(dsl, ensure_ascii=False)
        if len(dsl_str) > MAX_DSL_LENGTH:
            errors.append(
                {
                    "field": "dsl",
                    "message": f"DSL 序列化后长度 {len(dsl_str)} 超过 {MAX_DSL_LENGTH}",
                }
            )
    except (TypeError, ValueError):
        errors.append({"field": "dsl", "message": "DSL 无法序列化为 JSON"})
        raise ValidationError(errors) from None

    # ruleType
    rule_type = dsl.get("ruleType")
    if rule_type not in RULE_TYPES:
        errors.append(
            {
                "field": "ruleType",
                "message": f"ruleType 必须为 {RULE_TYPES} 之一，当前: {rule_type}",
            }
        )

    # scope.loopSelector
    scope = dsl.get("scope")
    if not isinstance(scope, dict):
        errors.append({"field": "scope", "message": "scope 必须为对象"})
    else:
        selector = scope.get("loopSelector")
        if not isinstance(selector, dict):
            errors.append({"field": "scope.loopSelector", "message": "loopSelector 必须为对象"})
        else:
            sel_type = selector.get("type")
            if sel_type not in LOOP_SELECTOR_TYPES:
                errors.append(
                    {
                        "field": "scope.loopSelector.type",
                        "message": f"type 必须为 {LOOP_SELECTOR_TYPES} 之一",
                    }
                )
            elif sel_type != "ALL" and not selector.get("value"):
                errors.append(
                    {
                        "field": "scope.loopSelector.value",
                        "message": f"type={sel_type} 时 value 不可为空",
                    }
                )

    # condition（类型特定校验）
    condition = dsl.get("condition")
    if not isinstance(condition, dict):
        errors.append({"field": "condition", "message": "condition 必须为对象"})
    elif rule_type in RULE_TYPES:
        _validate_condition(condition, rule_type, errors)

    # durationSeconds
    duration = dsl.get("durationSeconds", 0)
    if not isinstance(duration, int) or duration < 0 or duration > MAX_DURATION_SECONDS:
        errors.append(
            {
                "field": "durationSeconds",
                "message": f"必须为 0-{MAX_DURATION_SECONDS} 整数",
            }
        )
    elif duration > 0 and duration < 120:
        errors.append(
            {
                "field": "durationSeconds",
                "message": ">0 时须 ≥120s（2× 周期求值间隔 60s）",
            }
        )

    # cooldownSeconds
    cooldown = dsl.get("cooldownSeconds", 1800)
    if not isinstance(cooldown, int) or cooldown < 0 or cooldown > MAX_DURATION_SECONDS:
        errors.append(
            {
                "field": "cooldownSeconds",
                "message": f"必须为 0-{MAX_DURATION_SECONDS} 整数",
            }
        )

    # severity
    severity = dsl.get("severity")
    if severity not in SEVERITIES:
        errors.append(
            {
                "field": "severity",
                "message": f"必须为 {SEVERITIES} 之一，当前: {severity}",
            }
        )

    # confidencePolicy
    conf_policy = dsl.get("confidencePolicy")
    if conf_policy is not None:
        if not isinstance(conf_policy, dict):
            errors.append({"field": "confidencePolicy", "message": "必须为对象"})
        else:
            max_level = conf_policy.get("maxLevel")
            if max_level not in CONFIDENCE_LEVELS:
                errors.append(
                    {
                        "field": "confidencePolicy.maxLevel",
                        "message": f"必须为 {CONFIDENCE_LEVELS} 之一",
                    }
                )
            action = conf_policy.get("action")
            if action not in CONFIDENCE_ACTIONS:
                errors.append(
                    {
                        "field": "confidencePolicy.action",
                        "message": f"必须为 {CONFIDENCE_ACTIONS} 之一",
                    }
                )

    # timeWindow
    time_window = dsl.get("timeWindow")
    if time_window is not None:
        if not isinstance(time_window, dict):
            errors.append({"field": "timeWindow", "message": "必须为对象"})
        elif time_window.get("enabled", False):
            cron = time_window.get("cron")
            if not isinstance(cron, str) or not cron.strip():
                errors.append(
                    {"field": "timeWindow.cron", "message": "enabled=true 时 cron 不可为空"}
                )

    # actions
    actions = dsl.get("actions")
    if not isinstance(actions, list) or len(actions) < 1:
        errors.append({"field": "actions", "message": "至少 1 个动作"})
    else:
        has_create_event = False
        for i, act in enumerate(actions):
            if not isinstance(act, dict):
                errors.append({"field": f"actions[{i}]", "message": "必须为对象"})
                continue
            act_type = act.get("type")
            if act_type not in ACTION_TYPES:
                errors.append(
                    {
                        "field": f"actions[{i}].type",
                        "message": f"必须为 {ACTION_TYPES} 之一",
                    }
                )
            if act_type == "CREATE_EVENT":
                has_create_event = True
        if not has_create_event:
            errors.append({"field": "actions", "message": "必须包含 CREATE_EVENT"})

    # priority
    priority = dsl.get("priority", 100)
    if not isinstance(priority, int) or priority < 1:
        errors.append({"field": "priority", "message": "必须为 ≥1 整数"})

    # dedupKey
    dedup_key = dsl.get("dedupKey")
    if dedup_key is not None:
        if not isinstance(dedup_key, str):
            errors.append({"field": "dedupKey", "message": "必须为字符串"})
        else:
            # 检查变量白名单
            import re

            vars_in_key = set(re.findall(r"\$\{(\w+)\}", dedup_key))
            invalid_vars = vars_in_key - DEDUP_KEY_VARS
            if invalid_vars:
                errors.append(
                    {
                        "field": "dedupKey",
                        "message": f"变量 {invalid_vars} 不在白名单 {DEDUP_KEY_VARS}",
                    }
                )

    if errors:
        raise ValidationError(errors)

    return dsl


# ---------------------------------------------------------------------------
# 类型特定条件校验
# ---------------------------------------------------------------------------


def _validate_condition(
    condition: dict[str, Any], rule_type: str, errors: list[dict[str, str]]
) -> None:
    """按规则类型校验 condition 子结构。"""
    if rule_type == "THRESHOLD":
        _validate_threshold_condition(condition, errors)
    elif rule_type == "DRIFT":
        _validate_drift_condition(condition, errors)
    elif rule_type == "COMPOSITE":
        _validate_composite_condition(condition, errors, depth=0)
    elif rule_type == "CONFIDENCE":
        max_level = condition.get("maxLevel")
        if max_level not in CONFIDENCE_LEVELS:
            errors.append(
                {
                    "field": "condition.maxLevel",
                    "message": f"CONFIDENCE 规则 maxLevel 必须为 {CONFIDENCE_LEVELS} 之一",
                }
            )


def _validate_threshold_condition(condition: dict[str, Any], errors: list[dict[str, str]]) -> None:
    """阈值规则条件校验。"""
    metric = condition.get("metric")
    if metric not in METRICS:
        errors.append({"field": "condition.metric", "message": f"必须为 {METRICS} 之一"})

    operator = condition.get("operator")
    if operator not in OPERATORS:
        errors.append({"field": "condition.operator", "message": f"必须为 {OPERATORS} 之一"})

    # value 必填（IN/NOT_IN 时为列表，其他为数值或百分比字符串）
    value = condition.get("value")
    if value is None:
        errors.append({"field": "condition.value", "message": "value 不可为空"})

    # orCondition 可选，结构与主条件一致
    or_cond = condition.get("orCondition")
    if or_cond is not None and isinstance(or_cond, dict):
        _validate_threshold_condition(or_cond, errors)


def _validate_drift_condition(condition: dict[str, Any], errors: list[dict[str, str]]) -> None:
    """统计漂移规则条件校验。"""
    metric = condition.get("metric")
    if metric not in METRICS:
        errors.append({"field": "condition.metric", "message": f"必须为 {METRICS} 之一"})

    statistic = condition.get("statistic")
    if statistic not in STATISTICS:
        errors.append({"field": "condition.statistic", "message": f"必须为 {STATISTICS} 之一"})

    window = condition.get("windowSeconds", 1800)
    if not isinstance(window, int) or window < MIN_WINDOW_SECONDS or window > MAX_WINDOW_SECONDS:
        errors.append(
            {
                "field": "condition.windowSeconds",
                "message": f"必须为 {MIN_WINDOW_SECONDS}-{MAX_WINDOW_SECONDS} 整数",
            }
        )

    baseline = condition.get("baseline")
    if not isinstance(baseline, dict):
        errors.append({"field": "condition.baseline", "message": "必须为对象"})
    else:
        bl_type = baseline.get("type")
        if bl_type not in BASELINE_TYPES:
            errors.append(
                {
                    "field": "condition.baseline.type",
                    "message": f"必须为 {BASELINE_TYPES} 之一",
                }
            )

    dev_threshold = condition.get("deviationThreshold")
    if not isinstance(dev_threshold, int | float) or dev_threshold <= 0:
        errors.append({"field": "condition.deviationThreshold", "message": "必须为 >0 数值"})

    dev_type = condition.get("deviationType", "ABSOLUTE")
    if dev_type not in DEVIATION_TYPES:
        errors.append(
            {
                "field": "condition.deviationType",
                "message": f"必须为 {DEVIATION_TYPES} 之一",
            }
        )


def _validate_composite_condition(
    condition: dict[str, Any], errors: list[dict[str, str]], depth: int
) -> None:
    """组合条件规则校验（递归，最多 3 层嵌套）。"""
    if depth > MAX_NESTING_DEPTH:
        errors.append(
            {
                "field": "condition",
                "message": f"COMPOSITE 嵌套深度超过 {MAX_NESTING_DEPTH}",
            }
        )
        return

    logic = condition.get("logic")
    if logic not in COMPOSITE_LOGIC:
        errors.append({"field": "condition.logic", "message": f"必须为 {COMPOSITE_LOGIC} 之一"})
        return

    if logic == "SEQUENCE":
        # SEQUENCE 结构：first + then + withinSeconds
        first = condition.get("first")
        then = condition.get("then")
        within = condition.get("withinSeconds")
        if not isinstance(first, dict):
            errors.append({"field": "condition.first", "message": "SEQUENCE first 必须为对象"})
        else:
            _validate_composite_operand(first, errors, depth + 1)
        if not isinstance(then, dict):
            errors.append({"field": "condition.then", "message": "SEQUENCE then 必须为对象"})
        else:
            _validate_composite_operand(then, errors, depth + 1)
        if not isinstance(within, int) or within <= 0:
            errors.append({"field": "condition.withinSeconds", "message": "必须为 >0 整数"})
    else:
        # AND/OR/NOT 结构：operands 数组
        operands = condition.get("operands")
        if not isinstance(operands, list) or len(operands) < 1:
            errors.append(
                {"field": "condition.operands", "message": "AND/OR/NOT 至少 1 个 operand"}
            )
            return
        if logic == "NOT" and len(operands) != 1:
            errors.append({"field": "condition.operands", "message": "NOT 仅允许 1 个 operand"})
        for operand in operands:
            if not isinstance(operand, dict):
                errors.append({"field": "condition.operands[]", "message": "必须为对象"})
                continue
            _validate_composite_operand(operand, errors, depth + 1)


def _validate_composite_operand(
    operand: dict[str, Any], errors: list[dict[str, str]], depth: int
) -> None:
    """校验 COMPOSITE 的单个 operand（可能是 THRESHOLD/DRIFT/CONFIDENCE/嵌套 COMPOSITE）。"""
    op_type = operand.get("type")
    if op_type not in COMPOSITE_OPERAND_TYPES:
        errors.append(
            {
                "field": "condition.operands[].type",
                "message": f"必须为 {COMPOSITE_OPERAND_TYPES} 之一",
            }
        )
        return

    if op_type == "THRESHOLD":
        _validate_threshold_condition(operand, errors)
    elif op_type == "DRIFT":
        _validate_drift_condition(operand, errors)
    elif op_type == "CONFIDENCE":
        max_level = operand.get("maxLevel")
        if max_level not in CONFIDENCE_LEVELS:
            errors.append(
                {
                    "field": "condition.operands[].maxLevel",
                    "message": f"CONFIDENCE maxLevel 必须为 {CONFIDENCE_LEVELS} 之一",
                }
            )
    elif op_type == "COMPOSITE":
        _validate_composite_condition(operand, errors, depth)


# ---------------------------------------------------------------------------
# dedupKey 渲染
# ---------------------------------------------------------------------------


def render_dedup_key(template: str, loop_id: str, rule_id: str, **kwargs: str) -> str:
    """渲染 dedupKey 模板。

    Args:
        template: 模板字符串，如 ``"${loop_id}+${rule_id}"``
        loop_id: 回路 ID
        rule_id: 规则 ID
        **kwargs: 额外变量（tag_code/severity 等）

    Returns:
        渲染后的去重键
    """
    variables = {"loop_id": loop_id, "rule_id": rule_id, **kwargs}
    result = template
    for var_name, var_value in variables.items():
        result = result.replace(f"${{{var_name}}}", var_value)
    return result
