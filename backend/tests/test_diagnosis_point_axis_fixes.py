"""D1~D4 回归用例（2026-09-25 点表模式端到端验证发现）。

- D1：点表同轴组装的 object dtype 序列必须能被 FFT / 传感器算子处理（不再抛错）；
- D2：算子内部异常必须写进 OperatorResult.error（落库可见），不能只写日志；
- D3：门禁通过与"全部症状未命中"必须区分（不得把后者报成 DATA_INSUFFICIENT）。

只断言"不再异常 / 分类区分"，不断言症状是否检出（不调阈值、不改判定口径）。
"""

from __future__ import annotations

import numpy as np

from app.services.diagnosis_operators import OperatorInput
from app.services.diagnosis_operators.classification import (
    DATA_INSUFFICIENT,
    NO_SYMPTOM,
    classify,
)
from app.services.diagnosis_operators.gate import GateResult
from app.services.diagnosis_operators.oscillation import detect_fft
from app.services.diagnosis_operators.sensor import detect_sensor_fault


def _input(signals: dict, *, n: int) -> OperatorInput:
    return OperatorInput(
        loop_id="loop-test",
        signals=signals,
        timestamps=np.arange(n, dtype=float),
        meta={"sample_interval": 1.0, "total_points": n, "point_axis": True},
        kpi_context={},
    )


def _object_signals(n: int = 600) -> dict:
    """复刻点表全轴组装：dtype=object 的数值序列（D1 的现场输入形态）。"""
    rng = np.random.default_rng(7)
    base = 50.0 + np.sin(np.arange(n) / 20.0) * 0.5 + rng.normal(0, 0.01, n)
    return {
        "pv": np.array([float(v) for v in base], dtype=object),
        "sp": np.array([50.0] * n, dtype=object),
        "op": np.array([float(v) for v in base], dtype=object),
        "mode": np.array([1] * n, dtype=object),
        "pv_quality": np.ones(n, dtype=int),
    }


class TestD1ObjectDtypeInputs:
    def test_fft_operator_handles_object_dtype(self) -> None:
        """D1：object dtype 的 pv 不再让 FFT 算子抛错（oscillation.py）。"""
        res = detect_fft(_input(_object_signals(), n=600), {})
        # 只断言"不再异常且产出结果"，不断言是否检出症状（不调阈值、不改口径）
        assert res.executed is True
        assert res.error is None
        assert "index" in res.features
        assert 0.0 <= float(res.confidence) <= 1.0

    def test_sensor_operator_handles_object_dtype(self) -> None:
        """D1：object dtype 的 pv/sp 不再让传感器算子抛错（sensor.py）。"""
        res = detect_sensor_fault(_input(_object_signals(), n=600), {})
        assert res.executed is True
        assert res.error is None
        assert "noise_std_ratio" in res.features

    def test_fft_operator_reports_error_for_non_numeric(self) -> None:
        """D2：真正的非数值输入必须把异常写进结果，而不是静默返回空结果。"""
        signals = _object_signals()
        signals["pv"] = np.array([f"bad-{i}" for i in range(600)], dtype=object)
        res = detect_fft(_input(signals, n=600), {})
        assert res.error is not None
        assert "ValueError" in res.error or "TypeError" in res.error

    def test_sensor_operator_reports_error_for_non_numeric(self) -> None:
        signals = _object_signals()
        signals["pv"] = np.array([f"bad-{i}" for i in range(600)], dtype=object)
        res = detect_sensor_fault(_input(signals, n=600), {})
        assert res.error is not None


def _gate(passed: bool) -> GateResult:
    return GateResult(
        passed=passed,
        point_count=7200,
        expected_points=7200,
        valid_rate=1.0 if passed else 0.2,
        confidence_level="A" if passed else "D",
        gap_ratio=0.0 if passed else 0.8,
        reason=None if passed else "有效样本不足",
    )


class TestD3ClassificationFallback:
    def test_no_symptom_when_gate_passed(self) -> None:
        """D3：数据充足（门禁通过）且无症状命中 → NO_SYMPTOM，且不得推荐"先补齐数据"。"""
        result = classify({}, {}, {}, _gate(True))
        assert result.primary is not None
        assert result.primary.category == NO_SYMPTOM
        assert result.primary.category != DATA_INSUFFICIENT
        rec = " ".join(r.content for r in result.recommendations)
        assert "补齐数据" not in rec
        assert "未发现异常症状" in rec
        assert any("数据充足" in line for line in result.rationale)

    def test_data_insufficient_when_gate_failed(self) -> None:
        """D3：门禁未过仍如实报 DATA_INSUFFICIENT 并建议补齐数据。"""
        result = classify({}, {}, {}, _gate(False))
        assert result.primary is not None
        assert result.primary.category == DATA_INSUFFICIENT
        rec = " ".join(r.content for r in result.recommendations)
        assert "补齐" in rec


class TestD3PersistenceMapping:
    def test_no_symptom_is_internal_only(self) -> None:
        """D3：NO_SYMPTOM 为内部哨兵，不得出现在 DB CHECK 允许的 8 类取值里。"""
        import app.services.diagnosis_operators.classification as mod

        assert NO_SYMPTOM == "NO_SYMPTOM"
        assert mod.CATEGORY_LABELS[NO_SYMPTOM] == "未发现异常症状"
        # 与模型 CheckConstraint 的取值域保持区分（落库由 orchestrator 映射为 NULL）
        assert NO_SYMPTOM != DATA_INSUFFICIENT
        assert "保持运行" in mod.CATEGORY_DIRECTIONS[NO_SYMPTOM]
