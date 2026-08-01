"""V62-P3-007 人工实施清单测试.

验证整定结果包含当前值、建议值、单位转换、风险和回退值。
"""

from __future__ import annotations

from app.services.tuning import _assess_tuning_risk, _compute_max_pid_delta


def test_compute_max_pid_delta_no_change() -> None:
    """PID 参数无变化时 delta=0."""
    delta = _compute_max_pid_delta(
        recommended_pid={"kp": 1.0, "ti": 10.0, "td": 0.5},
        current_pid={"kp": 1.0, "ti": 10.0, "td": 0.5},
    )
    assert delta == 0.0


def test_compute_max_pid_delta_large_change() -> None:
    """PID 参数大幅变化时 delta 接近 1.0."""
    delta = _compute_max_pid_delta(
        recommended_pid={"kp": 2.0, "ti": 10.0, "td": 0.5},
        current_pid={"kp": 1.0, "ti": 10.0, "td": 0.5},
    )
    assert delta == 1.0  # kp 从 1.0 → 2.0，相对变化 100%


def test_compute_max_pid_delta_zero_current() -> None:
    """当前值为 0 时用绝对值衡量."""
    delta = _compute_max_pid_delta(
        recommended_pid={"kp": 1.0, "ti": 0.0, "td": 0.0},
        current_pid={"kp": 0.0, "ti": 0.0, "td": 0.0},
    )
    assert delta == 1.0


def test_assess_risk_low() -> None:
    """A 级可信度 + 小幅变化 → LOW."""
    risk = _assess_tuning_risk(
        recommended_pid={"kp": 1.05, "ti": 10.0, "td": 0.5},
        current_pid={"kp": 1.0, "ti": 10.0, "td": 0.5},
        model_params={"K": 1.0, "tau": 30.0, "theta": 3.0},
        confidence_level="A",
    )
    assert risk["riskLevel"] == "LOW"
    assert len(risk["factors"]) > 0
    assert "description" in risk


def test_assess_risk_high_due_to_confidence() -> None:
    """D 级可信度 → HIGH（无论变化幅度）."""
    risk = _assess_tuning_risk(
        recommended_pid={"kp": 1.0, "ti": 10.0, "td": 0.5},
        current_pid={"kp": 1.0, "ti": 10.0, "td": 0.5},
        model_params={"K": 1.0, "tau": 30.0, "theta": 3.0},
        confidence_level="D",
    )
    assert risk["riskLevel"] == "HIGH"
    assert any("D" in f for f in risk["factors"])


def test_assess_risk_high_due_to_large_delta() -> None:
    """PID 变化 > 50% + C 级 → HIGH."""
    risk = _assess_tuning_risk(
        recommended_pid={"kp": 3.0, "ti": 10.0, "td": 0.5},
        current_pid={"kp": 1.0, "ti": 10.0, "td": 0.5},
        model_params={"K": 1.0, "tau": 30.0, "theta": 3.0},
        confidence_level="C",
    )
    assert risk["riskLevel"] == "HIGH"


def test_assess_risk_medium() -> None:
    """B 级 + 中等变化 → MEDIUM."""
    risk = _assess_tuning_risk(
        recommended_pid={"kp": 1.3, "ti": 10.0, "td": 0.5},
        current_pid={"kp": 1.0, "ti": 10.0, "td": 0.5},
        model_params={"K": 1.0, "tau": 30.0, "theta": 3.0},
        confidence_level="B",
    )
    assert risk["riskLevel"] == "MEDIUM"


def test_assess_risk_no_current_pid() -> None:
    """无当前 PID 时仍可评估（基于模型可信度与参数）."""
    risk = _assess_tuning_risk(
        recommended_pid={"kp": 1.0, "ti": 10.0, "td": 0.5},
        current_pid=None,
        model_params={"K": 1.0, "tau": 30.0, "theta": 3.0},
        confidence_level="A",
    )
    assert risk["riskLevel"] == "LOW"


def test_assess_risk_large_dead_time() -> None:
    """θ/τ > 0.5 增加风险."""
    risk = _assess_tuning_risk(
        recommended_pid={"kp": 1.0, "ti": 10.0, "td": 0.5},
        current_pid={"kp": 1.0, "ti": 10.0, "td": 0.5},
        model_params={"K": 1.0, "tau": 10.0, "theta": 8.0},  # θ/τ=0.8
        confidence_level="A",
    )
    assert any("大滞后" in f for f in risk["factors"])
