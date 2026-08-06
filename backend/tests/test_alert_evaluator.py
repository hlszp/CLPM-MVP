"""智能预警规则引擎求值器测试.

覆盖：
- evaluate_rule 完整流程（时效窗口/可信度门禁/条件求值/dedupKey 渲染）
- THRESHOLD 求值（6 种数值比较 + IN/NOT_IN 枚举 + RATE_OF_CHANGE 跳过）
- CONFIDENCE 求值
- COMPOSITE 简化求值（AND/OR/NOT）
- upgrade_severity / _downgrade_severity 严重度升降级
- _confidence_worse_than 可信度比较
- _is_in_time_window 时效窗口（cron 小时部分解析）
- evaluate_loop_rules 批量求值
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.alert_rule_engine import evaluator
from app.services.alert_rule_engine.evaluator import (
    _compare,
    _confidence_worse_than,
    _downgrade_severity,
    _evaluate_composite_simple,
    _evaluate_confidence,
    _evaluate_threshold,
    _is_in_time_window,
    _resolve_value,
    evaluate_loop_rules,
    evaluate_rule,
    upgrade_severity,
)

# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------


def _make_rule(
    rule_id: str = "rule-001",
    rule_code: str = "R001",
    rule_type: str = "THRESHOLD",
    condition: dict | None = None,
    dsl_extras: dict | None = None,
) -> dict:
    """构造规则缓存字典（cache._rule_to_dict 格式）。"""
    dsl: dict = {
        "ruleType": rule_type,
        "severity": "WARN",
        "cooldownSeconds": 1800,
        "dedupKey": "${loop_id}+${rule_id}",
    }
    if condition is not None:
        dsl["condition"] = condition
    if dsl_extras:
        dsl.update(dsl_extras)
    return {
        "id": rule_id,
        "ruleCode": rule_code,
        "ruleName": f"规则-{rule_code}",
        "ruleType": rule_type,
        "dsl": dsl,
        "version": 1,
    }


# ===========================================================================
# _compare 数值比较
# ===========================================================================


class TestCompare:
    """_compare 数值比较。"""

    @pytest.mark.parametrize(
        "operator,actual,threshold,expected",
        [
            (">", 10, 5, True),
            (">", 5, 5, False),
            (">=", 5, 5, True),
            ("<", 3, 5, True),
            ("<", 5, 5, False),
            ("<=", 5, 5, True),
            ("==", 5, 5, True),
            ("==", 5, 5.0, True),
            ("!=", 5, 6, True),
            ("!=", 5, 5, False),
        ],
    )
    def test_compare_operations(
        self, operator: str, actual: float, threshold: float, expected: bool
    ) -> None:
        assert _compare(actual, operator, threshold) is expected

    def test_unknown_operator_returns_false(self) -> None:
        assert _compare(10, "XOR", 5) is False


# ===========================================================================
# _resolve_value 阈值解析
# ===========================================================================


class TestResolveValue:
    """_resolve_value 阈值 value 解析。"""

    def test_int_value(self) -> None:
        assert _resolve_value(100) == 100.0

    def test_float_value(self) -> None:
        assert _resolve_value(3.14) == 3.14

    def test_numeric_string(self) -> None:
        assert _resolve_value("42.5") == 42.5

    def test_percent_string_raises(self) -> None:
        """百分比阈值 Phase 2 才实现。"""
        with pytest.raises(ValueError, match="Phase 2"):
            _resolve_value("80%")

    def test_high_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="Phase 2"):
            _resolve_value("highLimit")

    def test_low_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="Phase 2"):
            _resolve_value("lowLimit")

    def test_unresolvable_raises(self) -> None:
        with pytest.raises(ValueError):
            _resolve_value(["not", "a", "number"])  # type: ignore[arg-type]


# ===========================================================================
# _evaluate_threshold THRESHOLD 求值
# ===========================================================================


class TestEvaluateThreshold:
    """_evaluate_threshold 阈值规则求值。"""

    def test_gt_triggered(self) -> None:
        cond = {"metric": "PV", "operator": ">", "value": 100}
        triggered, val, snap = _evaluate_threshold(cond, {"PV": 150})
        assert triggered is True
        assert val == 150.0
        assert snap["actualValue"] == 150.0

    def test_gt_not_triggered(self) -> None:
        cond = {"metric": "PV", "operator": ">", "value": 100}
        triggered, val, _ = _evaluate_threshold(cond, {"PV": 50})
        assert triggered is False
        assert val == 50.0

    def test_missing_metric_returns_no_data(self) -> None:
        cond = {"metric": "PV", "operator": ">", "value": 100}
        triggered, val, snap = _evaluate_threshold(cond, {})
        assert triggered is False
        assert val is None
        assert snap["reason"] == "no_data"

    def test_none_value_returns_no_data(self) -> None:
        cond = {"metric": "PV", "operator": ">", "value": 100}
        triggered, _, snap = _evaluate_threshold(cond, {"PV": None})
        assert triggered is False
        assert snap["reason"] == "no_data"

    def test_string_value_type_mismatch(self) -> None:
        cond = {"metric": "PV", "operator": ">", "value": 100}
        triggered, _, snap = _evaluate_threshold(cond, {"PV": "not a number"})
        assert triggered is False
        assert snap["reason"] == "type_mismatch"

    def test_in_operator_triggered(self) -> None:
        cond = {"metric": "MODE", "operator": "IN", "value": [1, 2, 3]}
        triggered, _, snap = _evaluate_threshold(cond, {"MODE": 2})
        assert triggered is True
        assert snap["actualValue"] == 2

    def test_in_operator_not_triggered(self) -> None:
        cond = {"metric": "MODE", "operator": "IN", "value": [1, 2, 3]}
        triggered, _, _ = _evaluate_threshold(cond, {"MODE": 5})
        assert triggered is False

    def test_not_in_operator_triggered(self) -> None:
        """NOT_IN：actual 不在集合中时触发。"""
        cond = {"metric": "MODE", "operator": "NOT_IN", "value": [1, 2, 3]}
        triggered, _, _ = _evaluate_threshold(cond, {"MODE": 5})
        assert triggered is True

    def test_not_in_operator_not_triggered(self) -> None:
        cond = {"metric": "MODE", "operator": "NOT_IN", "value": [1, 2, 3]}
        triggered, _, _ = _evaluate_threshold(cond, {"MODE": 1})
        assert triggered is False

    def test_in_operator_with_non_list_value(self) -> None:
        cond = {"metric": "MODE", "operator": "IN", "value": 5}
        triggered, _, snap = _evaluate_threshold(cond, {"MODE": 5})
        assert triggered is False
        assert snap["reason"] == "value_not_list"

    def test_rate_of_change_not_supported(self) -> None:
        """RATE_OF_CHANGE Phase 2 实现。"""
        cond = {"metric": "PV", "operator": "RATE_OF_CHANGE", "value": 10}
        triggered, _, snap = _evaluate_threshold(cond, {"PV": 100})
        assert triggered is False
        assert snap["reason"] == "rate_of_change_not_supported"

    def test_unknown_operator_returns_false(self) -> None:
        cond = {"metric": "PV", "operator": "XOR", "value": 100}
        triggered, _, snap = _evaluate_threshold(cond, {"PV": 100})
        assert triggered is False
        assert snap["reason"] == "unknown_operator"

    def test_string_enum_value_in_set(self) -> None:
        """IN 操作符支持字符串枚举值比较。"""
        cond = {"metric": "MODE", "operator": "IN", "value": ["AUTO", "MANUAL"]}
        triggered, _, _ = _evaluate_threshold(cond, {"MODE": "AUTO"})
        assert triggered is True


# ===========================================================================
# _evaluate_confidence CONFIDENCE 求值
# ===========================================================================


class TestEvaluateConfidence:
    """_evaluate_confidence 可信度联动求值。"""

    def test_triggered_when_worse_than_threshold(self) -> None:
        cond = {"maxLevel": "B"}
        triggered, _, snap = _evaluate_confidence(cond, "D")
        assert triggered is True
        assert snap["maxLevel"] == "B"
        assert snap["actualLevel"] == "D"

    def test_not_triggered_when_equal(self) -> None:
        cond = {"maxLevel": "C"}
        triggered, _, _ = _evaluate_confidence(cond, "C")
        assert triggered is False

    def test_not_triggered_when_better(self) -> None:
        cond = {"maxLevel": "D"}
        triggered, _, _ = _evaluate_confidence(cond, "A")
        assert triggered is False

    def test_no_confidence_level_returns_no_data(self) -> None:
        cond = {"maxLevel": "C"}
        triggered, _, snap = _evaluate_confidence(cond, None)
        assert triggered is False
        assert snap["reason"] == "no_confidence_data"

    def test_no_max_level_returns_no_data(self) -> None:
        triggered, _, snap = _evaluate_confidence({}, "A")
        assert triggered is False
        assert snap["reason"] == "no_confidence_data"


# ===========================================================================
# _confidence_worse_than
# ===========================================================================


class TestConfidenceWorseThan:
    """_confidence_worse_than 可信度等级比较。"""

    @pytest.mark.parametrize(
        "actual,threshold,expected",
        [
            ("A", "A", False),
            ("B", "A", True),
            ("C", "B", True),
            ("D", "C", True),
            ("E", "D", True),
            ("A", "E", False),
            ("E", "A", True),
        ],
    )
    def test_worse_than(self, actual: str, threshold: str, expected: bool) -> None:
        assert _confidence_worse_than(actual, threshold) is expected

    def test_unknown_actual_defaults_to_a(self) -> None:
        """未知等级默认按 A 处理（最优，不触发）。"""
        assert _confidence_worse_than("X", "C") is False

    def test_unknown_threshold_defaults_to_a(self) -> None:
        """未知阈值默认按 A 处理（任何比 A 差的都触发）。"""
        assert _confidence_worse_than("B", "X") is True


# ===========================================================================
# _evaluate_composite_simple
# ===========================================================================


class TestEvaluateCompositeSimple:
    """_evaluate_composite_simple 组合条件求值。"""

    def test_and_all_true(self) -> None:
        cond = {
            "logic": "AND",
            "operands": [
                {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
                {"type": "THRESHOLD", "metric": "SP", "operator": "<", "value": 50},
            ],
        }
        values = {"PV": 150, "SP": 30}
        triggered, _, snap = _evaluate_composite_simple(cond, values, None)
        assert triggered is True
        assert snap["operandResults"] == [True, True]

    def test_and_partial_false(self) -> None:
        cond = {
            "logic": "AND",
            "operands": [
                {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
                {"type": "THRESHOLD", "metric": "SP", "operator": "<", "value": 50},
            ],
        }
        values = {"PV": 150, "SP": 80}
        triggered, _, _ = _evaluate_composite_simple(cond, values, None)
        assert triggered is False

    def test_or_any_true(self) -> None:
        cond = {
            "logic": "OR",
            "operands": [
                {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
                {"type": "THRESHOLD", "metric": "SP", "operator": "<", "value": 50},
            ],
        }
        values = {"PV": 50, "SP": 30}
        triggered, _, _ = _evaluate_composite_simple(cond, values, None)
        assert triggered is True

    def test_or_all_false(self) -> None:
        cond = {
            "logic": "OR",
            "operands": [
                {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
                {"type": "THRESHOLD", "metric": "SP", "operator": "<", "value": 50},
            ],
        }
        values = {"PV": 50, "SP": 80}
        triggered, _, _ = _evaluate_composite_simple(cond, values, None)
        assert triggered is False

    def test_not_inverts(self) -> None:
        cond = {
            "logic": "NOT",
            "operands": [{"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100}],
        }
        triggered, _, _ = _evaluate_composite_simple(cond, {"PV": 50}, None)
        assert triggered is True  # 50 > 100 = False, NOT False = True

    def test_not_inverts_false_when_operand_true(self) -> None:
        cond = {
            "logic": "NOT",
            "operands": [{"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100}],
        }
        triggered, _, _ = _evaluate_composite_simple(cond, {"PV": 150}, None)
        assert triggered is False

    def test_sequence_not_supported(self) -> None:
        cond = {"logic": "SEQUENCE"}
        triggered, _, snap = _evaluate_composite_simple(cond, {}, None)
        assert triggered is False
        assert snap["reason"] == "sequence_not_supported"

    def test_empty_operands(self) -> None:
        cond = {"logic": "AND", "operands": []}
        triggered, _, snap = _evaluate_composite_simple(cond, {}, None)
        assert triggered is False
        assert snap["reason"] == "no_operands"

    def test_with_confidence_operand(self) -> None:
        cond = {
            "logic": "AND",
            "operands": [
                {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
                {"type": "CONFIDENCE", "maxLevel": "B"},
            ],
        }
        triggered, _, _ = _evaluate_composite_simple(cond, {"PV": 150}, "D")
        assert triggered is True

    def test_nested_composite(self) -> None:
        cond = {
            "logic": "AND",
            "operands": [
                {
                    "type": "COMPOSITE",
                    "logic": "OR",
                    "operands": [
                        {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
                        {"type": "THRESHOLD", "metric": "SP", "operator": ">", "value": 100},
                    ],
                },
                {"type": "CONFIDENCE", "maxLevel": "B"},
            ],
        }
        triggered, _, _ = _evaluate_composite_simple(cond, {"PV": 150}, "D")
        assert triggered is True


# ===========================================================================
# _is_in_time_window
# ===========================================================================


class TestIsInTimeWindow:
    """_is_in_time_window 时效窗口检查。

    注意：``_is_in_time_window`` 仅检查 cron 部分，``enabled`` 标记在
    ``evaluate_rule`` 调用层处理（enabled=False 时不进入此函数）。
    """

    def test_empty_cron_defaults_true(self) -> None:
        tw = {"enabled": True, "cron": ""}
        assert _is_in_time_window(tw) is True

    def test_star_hour_always_true(self) -> None:
        tw = {"enabled": True, "cron": "0 * * * *"}
        assert _is_in_time_window(tw) is True

    def test_invalid_cron_format_defaults_true(self) -> None:
        """cron 不足 2 段时默认放行（无法解析）。"""
        tw = {"enabled": True, "cron": "0"}
        assert _is_in_time_window(tw) is True

    def test_range_hour_matches(self) -> None:
        """测试 8-20 小时范围匹配（需 mock 当前时间）。"""
        tw = {"enabled": True, "cron": "0 8-20 * * *"}
        # mock now.hour=10 (UTC) → local_hour=(10+8)%24=18
        mock_now = MagicMock()
        mock_now.hour = 10
        with patch.object(evaluator, "datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            assert _is_in_time_window(tw) is True

    def test_range_hour_not_matches(self) -> None:
        tw = {"enabled": True, "cron": "0 8-20 * * *"}
        # mock now.hour=15 (UTC) → local_hour=(15+8)%24=23 不在 8-20
        mock_now = MagicMock()
        mock_now.hour = 15
        with patch.object(evaluator, "datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            assert _is_in_time_window(tw) is False

    def test_comma_hour_matches(self) -> None:
        tw = {"enabled": True, "cron": "0 8,12,16 * * *"}
        # mock now.hour=8 (UTC) → local_hour=(8+8)%24=16
        mock_now = MagicMock()
        mock_now.hour = 8
        with patch.object(evaluator, "datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            assert _is_in_time_window(tw) is True

    def test_single_hour_matches(self) -> None:
        tw = {"enabled": True, "cron": "0 8 * * *"}
        # mock now.hour=0 (UTC) → local_hour=(0+8)%24=8
        mock_now = MagicMock()
        mock_now.hour = 0
        with patch.object(evaluator, "datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            assert _is_in_time_window(tw) is True


# ===========================================================================
# upgrade_severity / _downgrade_severity
# ===========================================================================


class TestSeverityManagement:
    """严重度升降级。"""

    def test_upgrade_below_threshold_no_change(self) -> None:
        assert upgrade_severity("WARN", trigger_count=2) == "WARN"

    def test_upgrade_at_threshold(self) -> None:
        """触发 3 次后升级一级。"""
        assert upgrade_severity("WARN", trigger_count=3) == "ERROR"

    def test_upgrade_above_threshold(self) -> None:
        assert upgrade_severity("WARN", trigger_count=5) == "ERROR"

    def test_upgrade_critical_stays_critical(self) -> None:
        assert upgrade_severity("CRITICAL", trigger_count=10) == "CRITICAL"

    def test_upgrade_error_to_critical(self) -> None:
        assert upgrade_severity("ERROR", trigger_count=3) == "CRITICAL"

    def test_downgrade_warn_to_info(self) -> None:
        assert _downgrade_severity("WARN") == "INFO"

    def test_downgrade_error_to_warn(self) -> None:
        assert _downgrade_severity("ERROR") == "WARN"

    def test_downgrade_critical_to_error(self) -> None:
        assert _downgrade_severity("CRITICAL") == "ERROR"

    def test_downgrade_info_returns_none(self) -> None:
        """INFO 已是最低，降级为 None（跳过）。"""
        assert _downgrade_severity("INFO") is None


# ===========================================================================
# evaluate_rule 完整流程
# ===========================================================================


class TestEvaluateRule:
    """evaluate_rule 完整求值流程。"""

    @pytest.mark.asyncio
    async def test_threshold_triggered(self) -> None:
        rule = _make_rule(condition={"metric": "PV", "operator": ">", "value": 100})
        result = await evaluate_rule(
            db=AsyncMock(),
            rule=rule,
            loop_id="loop-1",
            current_values={"PV": 150},
        )
        assert result.triggered is True
        assert result.triggered_value == 150.0
        assert result.severity == "WARN"
        assert result.dedup_key == "loop-1+rule-001"

    @pytest.mark.asyncio
    async def test_threshold_not_triggered(self) -> None:
        rule = _make_rule(condition={"metric": "PV", "operator": ">", "value": 100})
        result = await evaluate_rule(
            db=AsyncMock(),
            rule=rule,
            loop_id="loop-1",
            current_values={"PV": 50},
        )
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_confidence_policy_suppress(self) -> None:
        """可信度门禁 SUPPRESS：劣于 maxLevel 时直接跳过。"""
        rule = _make_rule(
            condition={"metric": "PV", "operator": ">", "value": 100},
            dsl_extras={
                "confidencePolicy": {"maxLevel": "B", "action": "SUPPRESS"},
            },
        )
        result = await evaluate_rule(
            db=AsyncMock(),
            rule=rule,
            loop_id="loop-1",
            current_values={"PV": 150},
            confidence_level="D",  # 劣于 B
        )
        assert result.triggered is False
        assert result.confidence_level == "D"

    @pytest.mark.asyncio
    async def test_confidence_policy_downgrade(self) -> None:
        """可信度门禁 DOWNGRADE：劣于 maxLevel 时降级严重度。"""
        rule = _make_rule(
            condition={"metric": "PV", "operator": ">", "value": 100},
            dsl_extras={
                "severity": "ERROR",
                "confidencePolicy": {"maxLevel": "B", "action": "DOWNGRADE"},
            },
        )
        result = await evaluate_rule(
            db=AsyncMock(),
            rule=rule,
            loop_id="loop-1",
            current_values={"PV": 150},
            confidence_level="D",
        )
        assert result.triggered is True
        assert result.severity == "WARN"  # ERROR 降级为 WARN

    @pytest.mark.asyncio
    async def test_confidence_policy_downgrade_to_none_skips(self) -> None:
        """DOWNGRADE 到 INFO 后再降为 None，跳过告警。"""
        rule = _make_rule(
            condition={"metric": "PV", "operator": ">", "value": 100},
            dsl_extras={
                "severity": "INFO",  # INFO 降级为 None
                "confidencePolicy": {"maxLevel": "B", "action": "DOWNGRADE"},
            },
        )
        result = await evaluate_rule(
            db=AsyncMock(),
            rule=rule,
            loop_id="loop-1",
            current_values={"PV": 150},
            confidence_level="D",
        )
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_time_window_filters_out(self) -> None:
        """时效窗口外不触发。"""
        rule = _make_rule(
            condition={"metric": "PV", "operator": ">", "value": 100},
            dsl_extras={
                "timeWindow": {"enabled": True, "cron": "0 8 * * *"},
            },
        )
        mock_now = MagicMock()
        mock_now.hour = 15  # local_hour=23 != 8
        with patch.object(evaluator, "datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            result = await evaluate_rule(
                db=AsyncMock(),
                rule=rule,
                loop_id="loop-1",
                current_values={"PV": 150},
            )
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_drift_rule_phase1_skipped(self) -> None:
        """DRIFT 规则在 Phase 1 跳过（未实现）。"""
        rule = _make_rule(
            rule_type="DRIFT",
            condition={
                "metric": "PV",
                "statistic": "MEAN",
                "windowSeconds": 1800,
                "baseline": {"type": "HISTORICAL"},
                "deviationThreshold": 5.0,
            },
        )
        result = await evaluate_rule(
            db=AsyncMock(),
            rule=rule,
            loop_id="loop-1",
            current_values={"PV": 150},
        )
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_confidence_rule_triggered(self) -> None:
        rule = _make_rule(
            rule_type="CONFIDENCE",
            condition={"maxLevel": "B"},
        )
        result = await evaluate_rule(
            db=AsyncMock(),
            rule=rule,
            loop_id="loop-1",
            confidence_level="D",
        )
        assert result.triggered is True

    @pytest.mark.asyncio
    async def test_composite_rule_and_triggered(self) -> None:
        rule = _make_rule(
            rule_type="COMPOSITE",
            condition={
                "logic": "AND",
                "operands": [
                    {"type": "THRESHOLD", "metric": "PV", "operator": ">", "value": 100},
                    {"type": "CONFIDENCE", "maxLevel": "B"},
                ],
            },
        )
        result = await evaluate_rule(
            db=AsyncMock(),
            rule=rule,
            loop_id="loop-1",
            current_values={"PV": 150},
            confidence_level="D",
        )
        assert result.triggered is True

    @pytest.mark.asyncio
    async def test_unknown_rule_type_returns_not_triggered(self) -> None:
        rule = _make_rule(rule_type="UNKNOWN")
        result = await evaluate_rule(
            db=AsyncMock(),
            rule=rule,
            loop_id="loop-1",
            current_values={"PV": 150},
        )
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_custom_dedup_key_rendered(self) -> None:
        """自定义 dedupKey 中 loop_id/rule_id 会被渲染。

        注：severity 等额外变量需通过 render_dedup_key kwargs 传入，
        evaluate_rule 默认仅传 loop_id + rule_id，其他变量保留原占位符。
        """
        rule = _make_rule(
            condition={"metric": "PV", "operator": ">", "value": 100},
            dsl_extras={"dedupKey": "${loop_id}+${rule_id}+${severity}"},
        )
        result = await evaluate_rule(
            db=AsyncMock(),
            rule=rule,
            loop_id="loop-1",
            current_values={"PV": 150},
        )
        # loop_id/rule_id 被渲染，severity 保留原占位符
        assert result.dedup_key == "loop-1+rule-001+${severity}"


# ===========================================================================
# evaluate_loop_rules 批量求值
# ===========================================================================


class TestEvaluateLoopRules:
    """evaluate_loop_rules 批量求值。"""

    @pytest.mark.asyncio
    async def test_no_rules_returns_empty(self) -> None:
        # get_rules_for_loop 在函数内从 cache 模块导入，需 patch 源模块
        with patch(
            "app.services.alert_rule_engine.cache.get_rules_for_loop",
            new_callable=AsyncMock,
            return_value=[],
        ):
            results = await evaluate_loop_rules(AsyncMock(), "loop-1")
        assert results == []

    @pytest.mark.asyncio
    async def test_returns_only_triggered_results(self) -> None:
        rules = [
            _make_rule(
                rule_id="r1",
                condition={"metric": "PV", "operator": ">", "value": 100},
            ),
            _make_rule(
                rule_id="r2",
                condition={"metric": "PV", "operator": ">", "value": 200},
            ),
        ]
        with (
            patch(
                "app.services.alert_rule_engine.cache.get_rules_for_loop",
                new_callable=AsyncMock,
                return_value=rules,
            ),
            patch.object(evaluator, "_get_current_values", new_callable=AsyncMock) as m_vals,
        ):
            m_vals.return_value = {"PV": 150}  # 只触发 r1
            results = await evaluate_loop_rules(AsyncMock(), "loop-1")
        assert len(results) == 1
        assert results[0].triggered is True

    @pytest.mark.asyncio
    async def test_rule_evaluation_exception_does_not_crash(self) -> None:
        """单条规则求值异常不影响其他规则。"""

        async def boom(*args, **kwargs):
            raise RuntimeError("boom")

        rule = _make_rule(condition={"metric": "PV", "operator": ">", "value": 100})
        with (
            patch(
                "app.services.alert_rule_engine.cache.get_rules_for_loop",
                new_callable=AsyncMock,
                return_value=[rule],
            ),
            patch.object(evaluator, "evaluate_rule", side_effect=boom),
            patch.object(evaluator, "_get_current_values", new_callable=AsyncMock) as m_vals,
        ):
            m_vals.return_value = {"PV": 150}
            results = await evaluate_loop_rules(AsyncMock(), "loop-1")
        assert results == []
