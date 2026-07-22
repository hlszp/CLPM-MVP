"""C2 专家规则引擎测试.

测试覆盖：
- 规则求值引擎（simpleeval 命名空间 + 5 种动作执行器）
- 规则 CRUD 服务
- 规则 API 端点
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import diagnosis_rule as rule_module
from app.services.diagnosis_rule import (
    _build_eval_namespace,
    _execute_action,
    apply_rules,
    invalidate_rule_cache,
    list_rules,
    update_rule,
)

# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_rule(
    rule_code: str = "R01",
    rule_name: str = "测试规则",
    priority: int = 10,
    condition_expr: str = "True",
    action_type: str = "REMOVE_LABEL",
    action_params: dict | None = None,
    is_enabled: bool = True,
) -> MagicMock:
    """构造 DiagnosisRule mock。"""
    r = MagicMock()
    r.id = "rule-id-001"
    r.rule_code = rule_code
    r.rule_name = rule_name
    r.priority = priority
    r.condition_expr = condition_expr
    r.action_type = action_type
    r.action_params = action_params or {}
    r.is_enabled = is_enabled
    r.version = 1
    r.updated_by = None
    r.updated_at = None
    return r


def _make_result(label: str, confidence: float = 0.8) -> dict:
    """构造算法结果字典。"""
    return {
        "label": label,
        "confidence": confidence,
        "feature_values": {},
        "evidence": {},
    }


# ===========================================================================
# 命名空间构建测试
# ===========================================================================


class TestBuildEvalNamespace:
    """测试 _build_eval_namespace() 命名空间构建。"""

    def test_has_returns_true_for_present_label(self) -> None:
        results = [_make_result("OSCILLATION"), _make_result("VALVE_STICTION")]
        ns = _build_eval_namespace(results)
        assert ns["has"]("OSCILLATION") is True
        assert ns["has"]("VALVE_STICTION") is True

    def test_has_returns_false_for_absent_label(self) -> None:
        results = [_make_result("OSCILLATION")]
        ns = _build_eval_namespace(results)
        assert ns["has"]("VALVE_STICTION") is False

    def test_confidence_returns_value(self) -> None:
        results = [_make_result("OSCILLATION", 0.75)]
        ns = _build_eval_namespace(results)
        assert ns["confidence"]("OSCILLATION") == 0.75

    def test_confidence_returns_zero_for_absent(self) -> None:
        results = [_make_result("OSCILLATION", 0.75)]
        ns = _build_eval_namespace(results)
        assert ns["confidence"]("VALVE_STICTION") == 0.0

    def test_feature_returns_value(self) -> None:
        results = [
            {
                "label": "QUALITY_ABNORMAL",
                "confidence": 0.8,
                "feature_values": {"bad_quality_rate": 0.6},
                "evidence": {},
            }
        ]
        ns = _build_eval_namespace(results)
        assert ns["feature"]("bad_quality_rate") == 0.6

    def test_feature_returns_zero_for_missing(self) -> None:
        results = [_make_result("OSCILLATION")]
        ns = _build_eval_namespace(results)
        assert ns["feature"]("bad_quality_rate") == 0.0

    def test_count_returns_label_count(self) -> None:
        results = [_make_result("A"), _make_result("B"), _make_result("C")]
        ns = _build_eval_namespace(results)
        assert ns["count"]() == 3

    def test_max_confidence_returns_highest(self) -> None:
        results = [_make_result("A", 0.3), _make_result("B", 0.8)]
        ns = _build_eval_namespace(results)
        assert ns["max_confidence"]() == 0.8

    def test_max_confidence_empty_returns_zero(self) -> None:
        ns = _build_eval_namespace([])
        assert ns["max_confidence"]() == 0.0


# ===========================================================================
# 动作执行器测试
# ===========================================================================


class TestExecuteAction:
    """测试 _execute_action() 动作执行器。"""

    def test_remove_label(self) -> None:
        results = [_make_result("OSCILLATION"), _make_result("VALVE_STICTION")]
        out = _execute_action("REMOVE_LABEL", {"label": "OSCILLATION"}, results)
        assert len(out) == 1
        assert out[0]["label"] == "VALVE_STICTION"

    def test_add_label(self) -> None:
        results = [_make_result("OSCILLATION")]
        out = _execute_action(
            "ADD_LABEL",
            {"label": "MANUAL_REVIEW", "confidence": 0.5},
            results,
        )
        assert len(out) == 2
        assert out[1]["label"] == "MANUAL_REVIEW"
        assert out[1]["confidence"] == 0.5

    def test_keep_highest(self) -> None:
        results = [
            _make_result("OVERAGGRESSIVE", 0.7),
            _make_result("OVERCONSERVATIVE", 0.5),
            _make_result("OSCILLATION", 0.8),
        ]
        out = _execute_action(
            "KEEP_HIGHEST",
            {"labels": ["OVERAGGRESSIVE", "OVERCONSERVATIVE"]},
            results,
        )
        labels = {r["label"] for r in out}
        assert "OVERAGGRESSIVE" in labels
        assert "OVERCONSERVATIVE" not in labels
        assert "OSCILLATION" in labels

    def test_filter_only(self) -> None:
        results = [
            _make_result("OSCILLATION"),
            _make_result("QUALITY_ABNORMAL"),
        ]
        out = _execute_action("FILTER_ONLY", {"keep": "QUALITY_ABNORMAL"}, results)
        assert len(out) == 1
        assert out[0]["label"] == "QUALITY_ABNORMAL"

    def test_sort_priority(self) -> None:
        results = [
            _make_result("OSCILLATION"),
            _make_result("VALVE_STICTION"),
            _make_result("QUALITY_ABNORMAL"),
        ]
        priority_map = {
            "QUALITY_ABNORMAL": 1,
            "VALVE_STICTION": 2,
            "OSCILLATION": 6,
        }
        out = _execute_action("SORT_PRIORITY", {"priority_map": priority_map}, results)
        assert out[0]["label"] == "QUALITY_ABNORMAL"
        assert out[1]["label"] == "VALVE_STICTION"
        assert out[2]["label"] == "OSCILLATION"

    def test_unknown_action_returns_unchanged(self) -> None:
        results = [_make_result("OSCILLATION")]
        out = _execute_action("UNKNOWN", {}, results)
        assert out is results


# ===========================================================================
# apply_rules 集成测试
# ===========================================================================


class TestApplyRules:
    """测试 apply_rules() 规则矩阵应用。"""

    def test_empty_results_returns_empty(self) -> None:
        rules = [_make_rule()]
        assert apply_rules([], rules) == []

    def test_empty_rules_returns_unchanged(self) -> None:
        results = [_make_result("OSCILLATION")]
        assert apply_rules(results, []) is results

    def test_r01_removes_oscillation_when_stiction_present(self) -> None:
        """R01: OSCILLATION + VALVE_STICTION(stiction>0.5) → 移除 OSCILLATION。"""
        results = [
            _make_result("OSCILLATION", 0.7),
            _make_result("VALVE_STICTION", 0.8),
        ]
        rules = [
            _make_rule(
                rule_code="R01",
                condition_expr=(
                    'has("OSCILLATION") and has("VALVE_STICTION") '
                    'and confidence("VALVE_STICTION") > 0.5'
                ),
                action_type="REMOVE_LABEL",
                action_params={"label": "OSCILLATION"},
            )
        ]
        out = apply_rules(results, rules)
        labels = {r["label"] for r in out}
        assert "OSCILLATION" not in labels
        assert "VALVE_STICTION" in labels

    def test_r01_does_not_trigger_when_stiction_low(self) -> None:
        """R01: stiction 置信度 ≤ 0.5 时不触发。"""
        results = [
            _make_result("OSCILLATION", 0.7),
            _make_result("VALVE_STICTION", 0.3),
        ]
        rules = [
            _make_rule(
                rule_code="R01",
                condition_expr=(
                    'has("OSCILLATION") and has("VALVE_STICTION") '
                    'and confidence("VALVE_STICTION") > 0.5'
                ),
                action_type="REMOVE_LABEL",
                action_params={"label": "OSCILLATION"},
            )
        ]
        out = apply_rules(results, rules)
        labels = {r["label"] for r in out}
        assert "OSCILLATION" in labels

    def test_r05_adds_manual_review_when_all_low(self) -> None:
        """R05: 所有置信度 < 0.5 → 添加 MANUAL_REVIEW。"""
        results = [_make_result("OSCILLATION", 0.3)]
        rules = [
            _make_rule(
                rule_code="R05",
                condition_expr="count() > 0 and max_confidence() < 0.5",
                action_type="ADD_LABEL",
                action_params={"label": "MANUAL_REVIEW", "confidence": 0.5},
            )
        ]
        out = apply_rules(results, rules)
        labels = {r["label"] for r in out}
        assert "MANUAL_REVIEW" in labels

    def test_multiple_rules_execute_in_priority_order(self) -> None:
        """多条规则按 priority 升序执行。"""
        results = [
            _make_result("OVERAGGRESSIVE", 0.7),
            _make_result("OVERCONSERVATIVE", 0.5),
        ]
        rules = [
            _make_rule(
                rule_code="R03",
                priority=30,
                condition_expr='has("OVERAGGRESSIVE") and has("OVERCONSERVATIVE")',
                action_type="KEEP_HIGHEST",
                action_params={"labels": ["OVERAGGRESSIVE", "OVERCONSERVATIVE"]},
            ),
            _make_rule(
                rule_code="R06",
                priority=90,
                condition_expr="True",
                action_type="SORT_PRIORITY",
                action_params={
                    "priority_map": {
                        "OVERAGGRESSIVE": 3,
                        "OVERCONSERVATIVE": 4,
                    }
                },
            ),
        ]
        out = apply_rules(results, rules)
        assert len(out) == 1
        assert out[0]["label"] == "OVERAGGRESSIVE"

    def test_invalid_expression_does_not_crash(self) -> None:
        """条件表达式语法错误不应导致崩溃。"""
        results = [_make_result("OSCILLATION")]
        rules = [
            _make_rule(
                condition_expr="invalid syntax ((((",
                action_type="REMOVE_LABEL",
                action_params={"label": "OSCILLATION"},
            )
        ]
        out = apply_rules(results, rules)
        assert len(out) == 1  # 规则未执行，结果不变


# ===========================================================================
# CRUD 服务测试
# ===========================================================================


class TestRuleCRUD:
    """测试规则 CRUD 服务。"""

    @pytest.mark.asyncio
    async def test_list_rules(self) -> None:
        db = AsyncMock()
        rule = _make_rule()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [rule]
        db.execute = AsyncMock(return_value=result_mock)

        rules = await list_rules(db)
        assert len(rules) == 1
        assert rules[0]["ruleCode"] == "R01"

    @pytest.mark.asyncio
    async def test_update_rule_not_found(self) -> None:
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        from app.core.exceptions import BizError

        with pytest.raises(BizError) as exc_info:
            await update_rule(db, "non-existent", "admin", rule_name="新名称")
        assert exc_info.value.code == "ERR_RULE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_update_rule_success(self) -> None:
        db = AsyncMock()
        rule = _make_rule()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = rule
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.commit = AsyncMock()

        # Mock invalidate_rule_cache
        with patch.object(rule_module, "invalidate_rule_cache", new_callable=AsyncMock):
            data = await update_rule(db, "rule-id-001", "admin", rule_name="更新名称")
        assert data["ruleName"] == "更新名称"
        assert data["version"] == 2


# ===========================================================================
# 缓存测试
# ===========================================================================


class TestRuleCache:
    """测试规则缓存。"""

    def test_preload_and_get_from_memory(self) -> None:
        rule_module._memory_cache = None
        rules = [_make_rule()]
        rule_module.preload_rules_to_memory(rules)
        assert rule_module._memory_cache is rules

    @pytest.mark.asyncio
    async def test_invalidate_clears_memory_cache(self) -> None:
        rule_module._memory_cache = [_make_rule()]
        with patch.object(rule_module, "redis_client", AsyncMock()):
            await invalidate_rule_cache()
        assert rule_module._memory_cache is None
