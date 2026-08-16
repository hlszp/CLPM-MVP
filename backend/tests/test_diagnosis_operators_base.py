"""诊断元算子契约与注册表单测。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §5
"""

from __future__ import annotations

import pytest

from app.services.diagnosis_operators import base as op_base
from app.services.diagnosis_operators.base import (
    OperatorInput,
    OperatorMeta,
    OperatorResult,
    operator,
)


def _make_meta(name: str = "test_op") -> OperatorMeta:
    return OperatorMeta(
        name=name,
        display_name="测试算子",
        family="test",
        diag_code="TEST",
        description="用于单测的算子",
        required_signals=("pv",),
        min_sample_rate=0.0,
        outputs_schema={"feature_a": "特征A"},
        threshold_schema={"th_a": 1.0},
        symptom_tags=("TEST_SYMPTOM",),
    )


@pytest.fixture()
def _cleanup_registry():
    """单测用临时算子，退出时清理注册表，避免污染其他用例。"""

    added: list[str] = []
    yield added
    for name in added:
        op_base.OPERATOR_REGISTRY.pop(name, None)


def test_operator_decorator_registers(_cleanup_registry: list[str]) -> None:
    meta = _make_meta("op_register_ok")

    @operator(meta)
    def detect(inp: OperatorInput, th: dict) -> OperatorResult:  # noqa: ARG001
        return OperatorResult("op_register_ok", executed=True)

    _cleanup_registry.append("op_register_ok")
    entry = op_base.get_operator("op_register_ok")
    assert entry is not None
    assert entry[0].name == "op_register_ok"
    assert entry[1] is detect


def test_duplicate_registration_raises(_cleanup_registry: list[str]) -> None:
    meta = _make_meta("op_dup")

    @operator(meta)
    def detect_a(inp: OperatorInput, th: dict) -> OperatorResult:  # noqa: ARG001
        return OperatorResult("op_dup", executed=True)

    _cleanup_registry.append("op_dup")

    with pytest.raises(ValueError, match="duplicate operator"):

        @operator(meta)
        def detect_b(inp: OperatorInput, th: dict) -> OperatorResult:  # noqa: ARG001
            return OperatorResult("op_dup", executed=True)


def test_list_operators_serializes_meta(_cleanup_registry: list[str]) -> None:
    @operator(_make_meta("op_listed"))
    def detect(inp: OperatorInput, th: dict) -> OperatorResult:  # noqa: ARG001
        return OperatorResult("op_listed", executed=True)

    _cleanup_registry.append("op_listed")
    items = {item["name"]: item for item in op_base.list_operators()}
    assert "op_listed" in items
    item = items["op_listed"]
    assert item["displayName"] == "测试算子"
    assert item["family"] == "test"
    assert item["diagCode"] == "TEST"
    assert item["requiredSignals"] == ["pv"]
    assert item["thresholdSchema"] == {"th_a": 1.0}
    assert item["symptomTags"] == ["TEST_SYMPTOM"]
    assert item["fastGroup"] is False
    assert item["enabledByDefault"] is True


def test_default_thresholds_returns_copy(_cleanup_registry: list[str]) -> None:
    @operator(_make_meta("op_thresholds"))
    def detect(inp: OperatorInput, th: dict) -> OperatorResult:  # noqa: ARG001
        return OperatorResult("op_thresholds", executed=True)

    _cleanup_registry.append("op_thresholds")
    th = op_base.default_thresholds("op_thresholds")
    assert th == {"th_a": 1.0}
    th["th_a"] = 999.0
    assert op_base.default_thresholds("op_thresholds") == {"th_a": 1.0}


def test_default_thresholds_unknown_operator() -> None:
    assert op_base.default_thresholds("no_such_op") == {}
    assert op_base.get_operator("no_such_op") is None
