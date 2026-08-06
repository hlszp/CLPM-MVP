"""智能预警规则引擎 DSL 解析与校验测试.

覆盖：
- validate_dsl 各字段校验（ruleType/scope/condition/duration/cooldown/
  severity/confidencePolicy/timeWindow/actions/priority/dedupKey）
- 4 类规则条件校验（THRESHOLD/DRIFT/COMPOSITE/CONFIDENCE）
- render_dedup_key 模板渲染
- ValidationError 错误聚合
"""

from __future__ import annotations

import pytest

from app.services.alert_rule_engine.dsl import (
    DEDUP_KEY_VARS,
    MAX_DSL_LENGTH,
    MAX_DURATION_SECONDS,
    MAX_NESTING_DEPTH,
    MIN_WINDOW_SECONDS,
    ValidationError,
    render_dedup_key,
    validate_dsl,
)

# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------


def _base_threshold_dsl() -> dict:
    """构造合法的 THRESHOLD 规则 DSL。"""
    return {
        "ruleType": "THRESHOLD",
        "scope": {
            "loopSelector": {"type": "ALL"},
        },
        "condition": {
            "metric": "PV",
            "operator": ">",
            "value": 100,
        },
        "durationSeconds": 0,
        "cooldownSeconds": 1800,
        "severity": "WARN",
        "actions": [{"type": "CREATE_EVENT"}],
        "priority": 100,
    }


def _base_drift_dsl() -> dict:
    """构造合法的 DRIFT 规则 DSL。"""
    return {
        "ruleType": "DRIFT",
        "scope": {"loopSelector": {"type": "LOOP", "value": "loop-001"}},
        "condition": {
            "metric": "PV",
            "statistic": "MEAN",
            "windowSeconds": 1800,
            "baseline": {"type": "HISTORICAL"},
            "deviationThreshold": 5.0,
            "deviationType": "ABSOLUTE",
        },
        "severity": "ERROR",
        "actions": [{"type": "CREATE_EVENT"}],
    }


def _base_confidence_dsl() -> dict:
    """构造合法的 CONFIDENCE 规则 DSL。"""
    return {
        "ruleType": "CONFIDENCE",
        "scope": {"loopSelector": {"type": "ALL"}},
        "condition": {"maxLevel": "C"},
        "severity": "WARN",
        "actions": [{"type": "CREATE_EVENT"}],
    }


def _base_composite_dsl() -> dict:
    """构造合法的 COMPOSITE 规则 DSL（AND 逻辑）。"""
    return {
        "ruleType": "COMPOSITE",
        "scope": {"loopSelector": {"type": "ALL"}},
        "condition": {
            "logic": "AND",
            "operands": [
                {
                    "type": "THRESHOLD",
                    "metric": "PV",
                    "operator": ">",
                    "value": 100,
                },
                {"type": "CONFIDENCE", "maxLevel": "B"},
            ],
        },
        "severity": "CRITICAL",
        "actions": [{"type": "CREATE_EVENT"}],
    }


# ===========================================================================
# 基础合法性测试
# ===========================================================================


class TestValidateDslBasic:
    """validate_dsl 基础合法性。"""

    def test_valid_threshold_dsl_returns_dict(self) -> None:
        dsl = _base_threshold_dsl()
        result = validate_dsl(dsl)
        assert result is dsl

    def test_valid_drift_dsl(self) -> None:
        result = validate_dsl(_base_drift_dsl())
        assert result["ruleType"] == "DRIFT"

    def test_valid_confidence_dsl(self) -> None:
        result = validate_dsl(_base_confidence_dsl())
        assert result["ruleType"] == "CONFIDENCE"

    def test_valid_composite_dsl(self) -> None:
        result = validate_dsl(_base_composite_dsl())
        assert result["ruleType"] == "COMPOSITE"

    def test_non_dict_raises(self) -> None:
        with pytest.raises(ValidationError):
            validate_dsl("not a dict")  # type: ignore[arg-type]

    def test_dsl_exceeds_max_length_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["description"] = "x" * (MAX_DSL_LENGTH + 100)
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        # 应有 dsl 字段超长错误
        fields = [e["field"] for e in exc_info.value.errors]
        assert "dsl" in fields


# ===========================================================================
# ruleType 校验
# ===========================================================================


class TestRuleTypeValidation:
    """ruleType 字段校验。"""

    def test_missing_rule_type_raises(self) -> None:
        dsl = _base_threshold_dsl()
        del dsl["ruleType"]
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "ruleType" for e in exc_info.value.errors)

    def test_invalid_rule_type_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["ruleType"] = "INVALID"
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "ruleType" for e in exc_info.value.errors)

    def test_time_window_is_not_a_rule_type(self) -> None:
        """TIME_WINDOW 已降为通用字段，不再是独立规则类型。"""
        dsl = _base_threshold_dsl()
        dsl["ruleType"] = "TIME_WINDOW"
        with pytest.raises(ValidationError):
            validate_dsl(dsl)


# ===========================================================================
# scope.loopSelector 校验
# ===========================================================================


class TestScopeValidation:
    """scope.loopSelector 字段校验。"""

    def test_missing_scope_raises(self) -> None:
        dsl = _base_threshold_dsl()
        del dsl["scope"]
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "scope" for e in exc_info.value.errors)

    def test_missing_loop_selector_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["scope"] = {}
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "scope.loopSelector" for e in exc_info.value.errors)

    def test_invalid_selector_type_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["scope"]["loopSelector"] = {"type": "INVALID"}
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "scope.loopSelector.type" for e in exc_info.value.errors)

    def test_loop_type_requires_value(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["scope"]["loopSelector"] = {"type": "LOOP"}  # 缺 value
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "scope.loopSelector.value" for e in exc_info.value.errors)

    def test_all_type_does_not_require_value(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["scope"]["loopSelector"] = {"type": "ALL"}
        result = validate_dsl(dsl)
        assert result["ruleType"] == "THRESHOLD"

    def test_plant_type_requires_value(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["scope"]["loopSelector"] = {"type": "PLANT", "value": "plant-01"}
        result = validate_dsl(dsl)
        assert result is dsl


# ===========================================================================
# THRESHOLD condition 校验
# ===========================================================================


class TestThresholdCondition:
    """THRESHOLD 条件校验。"""

    def test_missing_condition_raises(self) -> None:
        dsl = _base_threshold_dsl()
        del dsl["condition"]
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition" for e in exc_info.value.errors)

    def test_invalid_metric_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["condition"]["metric"] = "INVALID_METRIC"
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.metric" for e in exc_info.value.errors)

    def test_invalid_operator_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["condition"]["operator"] = ">>"
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.operator" for e in exc_info.value.errors)

    def test_missing_value_raises(self) -> None:
        dsl = _base_threshold_dsl()
        del dsl["condition"]["value"]
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.value" for e in exc_info.value.errors)

    def test_or_condition_recursively_validated(self) -> None:
        """orCondition 会被递归校验。"""
        dsl = _base_threshold_dsl()
        dsl["condition"]["orCondition"] = {
            "metric": "INVALID",  # 非法 metric
            "operator": ">",
            "value": 50,
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.metric" for e in exc_info.value.errors)

    def test_in_operator_with_list_value_passes(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["condition"]["metric"] = "MODE"
        dsl["condition"]["operator"] = "IN"
        dsl["condition"]["value"] = [1, 2, 3]
        result = validate_dsl(dsl)
        assert result["condition"]["operator"] == "IN"


# ===========================================================================
# DRIFT condition 校验
# ===========================================================================


class TestDriftCondition:
    """DRIFT 条件校验。"""

    def test_invalid_statistic_raises(self) -> None:
        dsl = _base_drift_dsl()
        dsl["condition"]["statistic"] = "MEDIAN"
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.statistic" for e in exc_info.value.errors)

    def test_window_too_small_raises(self) -> None:
        dsl = _base_drift_dsl()
        dsl["condition"]["windowSeconds"] = MIN_WINDOW_SECONDS - 1
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.windowSeconds" for e in exc_info.value.errors)

    def test_window_too_large_raises(self) -> None:
        dsl = _base_drift_dsl()
        dsl["condition"]["windowSeconds"] = 86400 + 1
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.windowSeconds" for e in exc_info.value.errors)

    def test_missing_baseline_raises(self) -> None:
        dsl = _base_drift_dsl()
        del dsl["condition"]["baseline"]
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.baseline" for e in exc_info.value.errors)

    def test_invalid_baseline_type_raises(self) -> None:
        dsl = _base_drift_dsl()
        dsl["condition"]["baseline"]["type"] = "UNKNOWN"
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.baseline.type" for e in exc_info.value.errors)

    def test_deviation_threshold_zero_raises(self) -> None:
        dsl = _base_drift_dsl()
        dsl["condition"]["deviationThreshold"] = 0
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.deviationThreshold" for e in exc_info.value.errors)

    def test_invalid_deviation_type_raises(self) -> None:
        dsl = _base_drift_dsl()
        dsl["condition"]["deviationType"] = "PERCENT"
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.deviationType" for e in exc_info.value.errors)


# ===========================================================================
# CONFIDENCE condition 校验
# ===========================================================================


class TestConfidenceCondition:
    """CONFIDENCE 条件校验。"""

    def test_invalid_max_level_raises(self) -> None:
        dsl = _base_confidence_dsl()
        dsl["condition"]["maxLevel"] = "F"
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.maxLevel" for e in exc_info.value.errors)

    def test_all_valid_confidence_levels_pass(self) -> None:
        for level in ("A", "B", "C", "D", "E"):
            dsl = _base_confidence_dsl()
            dsl["condition"]["maxLevel"] = level
            result = validate_dsl(dsl)
            assert result["condition"]["maxLevel"] == level


# ===========================================================================
# COMPOSITE condition 校验
# ===========================================================================


class TestCompositeCondition:
    """COMPOSITE 条件校验。"""

    def test_invalid_logic_raises(self) -> None:
        dsl = _base_composite_dsl()
        dsl["condition"]["logic"] = "XOR"
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.logic" for e in exc_info.value.errors)

    def test_empty_operands_raises(self) -> None:
        dsl = _base_composite_dsl()
        dsl["condition"]["operands"] = []
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.operands" for e in exc_info.value.errors)

    def test_not_logic_requires_single_operand(self) -> None:
        dsl = _base_composite_dsl()
        dsl["condition"]["logic"] = "NOT"
        dsl["condition"]["operands"] = [
            {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
            {"type": "THRESHOLD", "metric": "SP", "operator": "<", "value": 50},
        ]
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.operands" for e in exc_info.value.errors)

    def test_not_logic_with_single_operand_passes(self) -> None:
        dsl = _base_composite_dsl()
        dsl["condition"]["logic"] = "NOT"
        dsl["condition"]["operands"] = [
            {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
        ]
        result = validate_dsl(dsl)
        assert result["condition"]["logic"] == "NOT"

    def test_sequence_requires_first_then_within(self) -> None:
        dsl = _base_composite_dsl()
        dsl["condition"] = {
            "logic": "SEQUENCE",
            "first": {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
            "then": {"type": "THRESHOLD", "metric": "OP", "operator": "<", "value": 10},
            "withinSeconds": 300,
        }
        result = validate_dsl(dsl)
        assert result["condition"]["logic"] == "SEQUENCE"

    def test_sequence_missing_first_raises(self) -> None:
        dsl = _base_composite_dsl()
        dsl["condition"] = {
            "logic": "SEQUENCE",
            "then": {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
            "withinSeconds": 300,
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.first" for e in exc_info.value.errors)

    def test_sequence_non_positive_within_raises(self) -> None:
        dsl = _base_composite_dsl()
        dsl["condition"] = {
            "logic": "SEQUENCE",
            "first": {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
            "then": {"type": "THRESHOLD", "metric": "OP", "operator": "<", "value": 10},
            "withinSeconds": 0,
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition.withinSeconds" for e in exc_info.value.errors)

    def test_composite_nesting_depth_limit(self) -> None:
        """COMPOSITE 嵌套深度超过 MAX_NESTING_DEPTH 报错。"""

        # 构造深度嵌套的 COMPOSITE：每层把一个 COMPOSITE 作为 operand
        def make_nested_composite(depth: int) -> dict:
            """返回一个嵌套 depth 层的 COMPOSITE condition 字典。"""
            if depth <= 0:
                return {
                    "logic": "AND",
                    "operands": [
                        {
                            "type": "THRESHOLD",
                            "metric": "PV",
                            "operator": ">",
                            "value": 100,
                        }
                    ],
                }
            return {
                "logic": "AND",
                "operands": [
                    {
                        "type": "COMPOSITE",
                        **make_nested_composite(depth - 1),
                    }
                ],
            }

        dsl = _base_composite_dsl()
        dsl["condition"] = make_nested_composite(MAX_NESTING_DEPTH + 2)
        # 嵌套过深会触发错误
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "condition" for e in exc_info.value.errors)


# ===========================================================================
# 通用字段校验：duration/cooldown/severity/confidencePolicy/timeWindow
# ===========================================================================


class TestCommonFields:
    """通用字段校验。"""

    def test_duration_negative_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["durationSeconds"] = -1
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "durationSeconds" for e in exc_info.value.errors)

    def test_duration_too_large_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["durationSeconds"] = MAX_DURATION_SECONDS + 1
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "durationSeconds" for e in exc_info.value.errors)

    def test_duration_between_0_and_120_raises(self) -> None:
        """>0 但 <120s 会报错（需 ≥2×周期 60s）。"""
        dsl = _base_threshold_dsl()
        dsl["durationSeconds"] = 60
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "durationSeconds" for e in exc_info.value.errors)

    def test_duration_zero_passes(self) -> None:
        """0=瞬时触发，合法。"""
        dsl = _base_threshold_dsl()
        dsl["durationSeconds"] = 0
        result = validate_dsl(dsl)
        assert result["durationSeconds"] == 0

    def test_duration_120_passes(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["durationSeconds"] = 120
        result = validate_dsl(dsl)
        assert result["durationSeconds"] == 120

    def test_invalid_severity_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["severity"] = "FATAL"
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "severity" for e in exc_info.value.errors)

    def test_confidence_policy_invalid_max_level_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["confidencePolicy"] = {"maxLevel": "F", "action": "SUPPRESS"}
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "confidencePolicy.maxLevel" for e in exc_info.value.errors)

    def test_confidence_policy_invalid_action_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["confidencePolicy"] = {"maxLevel": "C", "action": "IGNORE"}
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "confidencePolicy.action" for e in exc_info.value.errors)

    def test_time_window_enabled_without_cron_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["timeWindow"] = {"enabled": True, "cron": ""}
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "timeWindow.cron" for e in exc_info.value.errors)

    def test_time_window_disabled_without_cron_passes(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["timeWindow"] = {"enabled": False}
        result = validate_dsl(dsl)
        assert result is dsl

    def test_time_window_no_time_window_passes(self) -> None:
        """timeWindow 是可选字段。"""
        dsl = _base_threshold_dsl()
        assert "timeWindow" not in dsl
        result = validate_dsl(dsl)
        assert result is dsl


# ===========================================================================
# actions / priority / dedupKey 校验
# ===========================================================================


class TestActionsAndMeta:
    """actions / priority / dedupKey 字段校验。"""

    def test_empty_actions_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["actions"] = []
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "actions" for e in exc_info.value.errors)

    def test_actions_missing_create_event_raises(self) -> None:
        """actions 必须包含 CREATE_EVENT。"""
        dsl = _base_threshold_dsl()
        dsl["actions"] = [{"type": "NOTIFY"}]
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "actions" for e in exc_info.value.errors)

    def test_invalid_action_type_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["actions"] = [{"type": "UNKNOWN"}, {"type": "CREATE_EVENT"}]
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "actions[0].type" for e in exc_info.value.errors)

    def test_priority_zero_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["priority"] = 0
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "priority" for e in exc_info.value.errors)

    def test_priority_negative_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["priority"] = -5
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "priority" for e in exc_info.value.errors)

    def test_priority_one_passes(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["priority"] = 1
        result = validate_dsl(dsl)
        assert result["priority"] == 1

    def test_dedup_key_invalid_variable_raises(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["dedupKey"] = "${loop_id}+${unknown_var}"
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        assert any(e["field"] == "dedupKey" for e in exc_info.value.errors)

    def test_dedup_key_valid_variables_passes(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["dedupKey"] = "${loop_id}+${rule_id}+${severity}"
        result = validate_dsl(dsl)
        assert result["dedupKey"] == "${loop_id}+${rule_id}+${severity}"

    def test_dedup_key_no_variables_passes(self) -> None:
        """无变量的静态 dedupKey 也合法。"""
        dsl = _base_threshold_dsl()
        dsl["dedupKey"] = "static-key"
        result = validate_dsl(dsl)
        assert result["dedupKey"] == "static-key"


# ===========================================================================
# ValidationError 错误聚合
# ===========================================================================


class TestErrorAggregation:
    """ValidationError 错误聚合。"""

    def test_multiple_errors_all_collected(self) -> None:
        """多个字段错误应全部收集，不中途返回。"""
        dsl = {
            "ruleType": "INVALID",
            "scope": "not a dict",
            "condition": "not a dict",
            "severity": "INVALID",
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        fields = {e["field"] for e in exc_info.value.errors}
        assert "ruleType" in fields
        assert "scope" in fields
        assert "condition" in fields
        assert "severity" in fields

    def test_validation_error_message_contains_field_info(self) -> None:
        dsl = _base_threshold_dsl()
        dsl["ruleType"] = "INVALID"
        with pytest.raises(ValidationError) as exc_info:
            validate_dsl(dsl)
        message = str(exc_info.value)
        assert "ruleType" in message

    def test_validation_error_default_init(self) -> None:
        """ValidationError 默认初始化无错误。"""
        err = ValidationError()
        assert err.errors == []
        # 空错误列表的 message 也应是空字符串或合理默认
        assert str(err) == ""


# ===========================================================================
# render_dedup_key 测试
# ===========================================================================


class TestRenderDedupKey:
    """render_dedup_key 模板渲染。"""

    def test_render_with_loop_and_rule(self) -> None:
        key = render_dedup_key("${loop_id}+${rule_id}", "loop-1", "rule-1")
        assert key == "loop-1+rule-1"

    def test_render_with_extra_kwargs(self) -> None:
        key = render_dedup_key(
            "${loop_id}+${rule_id}+${tag_code}",
            "loop-1",
            "rule-1",
            tag_code="PV",
        )
        assert key == "loop-1+rule-1+PV"

    def test_render_with_severity(self) -> None:
        key = render_dedup_key("${loop_id}+${severity}", "loop-1", "rule-1", severity="WARN")
        assert key == "loop-1+WARN"

    def test_render_static_template(self) -> None:
        """无变量的模板原样返回。"""
        key = render_dedup_key("static-key", "loop-1", "rule-1")
        assert key == "static-key"

    def test_render_missing_variable_left_as_is(self) -> None:
        """模板中变量未提供值时保留原占位符。"""
        key = render_dedup_key("${loop_id}+${missing}", "loop-1", "rule-1")
        assert key == "loop-1+${missing}"

    def test_dedup_key_vars_whitelist_contains_expected(self) -> None:
        """白名单应包含 loop_id/rule_id/tag_code/severity。"""
        assert "loop_id" in DEDUP_KEY_VARS
        assert "rule_id" in DEDUP_KEY_VARS
        assert "tag_code" in DEDUP_KEY_VARS
        assert "severity" in DEDUP_KEY_VARS
