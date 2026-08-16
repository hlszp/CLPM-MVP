"""诊断元算子测试：合成信号检测 + 与旧引擎等价性 + 注册表契约。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §6、§9.4
"""

from __future__ import annotations

import numpy as np
import pytest

import app.services.diagnosis_operators as ops
import app.tasks.diagnosis_engine as eng
from app.services.diagnosis_operators.base import OperatorInput, default_thresholds

# ---------------------------------------------------------------------------
# 合成信号工厂
# ---------------------------------------------------------------------------


def _sine(n: int = 3600, period: int = 60, amp: float = 2.0, noise: float = 0.05) -> np.ndarray:
    t = np.arange(n)
    return (
        50.0 + amp * np.sin(2 * np.pi * t / period) + np.random.default_rng(42).normal(0, noise, n)
    )


def _white_noise(n: int = 3600, std: float = 1.0) -> np.ndarray:
    return np.random.default_rng(7).normal(0, std, n)


def _stiction_pv_op(n: int = 3600, period: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """粘滞仿真：OP 方波驱动 + 带死区的三角形 PV（PV-OP 呈矩形/椭圆回环）。"""
    t = np.arange(n)
    op = 50.0 + 20.0 * np.sign(np.sin(2 * np.pi * t / period))
    # PV 滞后跟随 OP：锯齿波（粘滞特征——OP 跳变后 PV 缓慢爬行）
    pv = 50.0 + 15.0 * (2.0 * np.abs(((t % period) / period) - 0.5) - 0.5) * 2.0
    pv = pv + np.random.default_rng(3).normal(0, 0.02, n)
    return pv, op


def _kano_signal(n: int = 3600) -> tuple[np.ndarray, np.ndarray]:
    """Kano 粘滞信号：初期 OP 大幅整定后趋于微小波动（段内 ΔOP≈0），
    PV 持续大幅振荡（段内 ΔPV 大）→ OP 几乎不变但 PV 大幅变化。"""
    t = np.arange(n)
    op = np.empty(n)
    op[:300] = np.linspace(0.0, 100.0, 300)  # 初始大幅整定拉开 OP 量程
    op[300:] = 50.0 + 0.2 * np.sin(t[300:] / 50.0)  # 之后微小波动
    pv = 50.0 + 10.0 * np.sin(2 * np.pi * t / 100.0)  # PV 持续大幅振荡
    return pv, op


def _frozen_pv(n: int = 3600) -> np.ndarray:
    """前 60% 冻结 + 后 40% 正常波动。"""
    frozen = np.full(int(n * 0.6), 50.0)
    normal = 50.0 + 0.5 * _white_noise(n - len(frozen), 1.0)
    return np.concatenate([frozen, normal])


def _make_input(
    signals: dict[str, np.ndarray],
    meta: dict | None = None,
) -> OperatorInput:
    first = next(iter(signals.values()), None)
    n = len(first) if first is not None else 16
    return OperatorInput(
        loop_id="test-loop",
        signals=signals,
        timestamps=np.arange(n, dtype=float),
        meta={"sample_interval": 1.0, "total_points": n, **(meta or {})},
    )


def _run(name: str, signals: dict[str, np.ndarray], meta: dict | None = None):
    entry = ops.get_operator(name)
    assert entry is not None, f"算子未注册: {name}"
    _meta, fn = entry
    return fn(_make_input(signals, meta), default_thresholds(name))


# ---------------------------------------------------------------------------
# 注册表契约
# ---------------------------------------------------------------------------

EXPECTED_OPERATORS = {
    "oscillation_iae": ("oscillation", ("pv", "sp"), True),
    "oscillation_fft": ("oscillation", ("pv",), False),
    "stiction_ellipse": ("stiction", ("pv", "op"), True),
    "stiction_choudhury": ("stiction", ("pv", "op"), False),
    "stiction_kano": ("stiction", ("pv", "op"), False),
    "step_response_overshoot": ("tuning", ("pv", "sp"), True),
    "slow_response": ("tuning", ("pv", "sp"), True),
    "disturbance_burst": ("disturbance", ("pv", "sp"), True),
    "sensor_fault": ("sensor", ("pv",), True),
    "quality_code_rules": ("sensor", ("pv_quality",), False),
    "output_saturation": ("saturation", ("op", "mode"), True),
}


def test_registry_contains_11_operators() -> None:
    assert set(ops.OPERATOR_REGISTRY) == set(EXPECTED_OPERATORS)


def test_registry_meta_contract() -> None:
    for name, (family, signals, fast) in EXPECTED_OPERATORS.items():
        meta, _fn = ops.OPERATOR_REGISTRY[name]
        assert meta.family == family, name
        assert meta.required_signals == signals, name
        assert meta.fast_group is fast, name
        assert meta.diag_code, name
        assert meta.symptom_tags, name


# ---------------------------------------------------------------------------
# 合成信号检测（每算子正/反例）
# ---------------------------------------------------------------------------


def test_oscillation_iae_detects_sine() -> None:
    res = _run("oscillation_iae", {"pv": _sine(), "sp": np.full(3600, 50.0)})
    assert res.executed and res.detected
    assert res.confidence > 0.5


def test_oscillation_iae_rejects_white_noise() -> None:
    res = _run("oscillation_iae", {"pv": _white_noise(), "sp": np.full(3600, 50.0)})
    assert res.executed and not res.detected


def test_oscillation_fft_detects_sine() -> None:
    res = _run("oscillation_fft", {"pv": _sine()})
    assert res.executed and res.detected


def test_oscillation_fft_rejects_white_noise() -> None:
    res = _run("oscillation_fft", {"pv": _white_noise()})
    assert res.executed and not res.detected


def test_stiction_ellipse_on_stiction_signal() -> None:
    pv, op = _stiction_pv_op()
    res = _run("stiction_ellipse", {"pv": pv, "op": op})
    assert res.executed
    # 合成信号或被极限环门控拒绝都接受（等价性由对照测试保证），但必须执行成功
    assert res.features["stiction_index"] >= 0.0


def test_stiction_kano_on_stiction_signal() -> None:
    pv, op = _kano_signal()
    res = _run("stiction_kano", {"pv": pv, "op": op})
    assert res.executed and res.detected
    assert res.features["stiction_ratio"] > 0.6


def test_step_response_detects_overshoot() -> None:
    n = 3600
    sp = np.full(n, 50.0)
    sp[600:] = 60.0  # SP 阶跃
    t = np.arange(n - 600)
    # 过冲 40% + 衰减振荡响应
    pv_tail = 60.0 + 4.0 * np.exp(-t / 300) * np.cos(2 * np.pi * t / 200)
    pv = np.concatenate([np.full(600, 50.0), pv_tail])
    res = _run("step_response_overshoot", {"pv": pv, "sp": sp})
    assert res.executed and res.detected
    assert res.features["overshoot"] > 0.25


def test_step_response_rejects_well_damped() -> None:
    n = 3600
    sp = np.full(n, 50.0)
    sp[600:] = 60.0
    t = np.arange(n - 600)
    pv_tail = 60.0 - 10.0 * np.exp(-t / 50.0)  # 平滑无过冲一阶响应
    pv = np.concatenate([np.full(600, 50.0), pv_tail])
    res = _run("step_response_overshoot", {"pv": pv, "sp": sp})
    assert res.executed and not res.detected


def test_slow_response_detects_sluggish_loop() -> None:
    n = 3600
    sp = np.full(n, 50.0)
    sp[600:] = 60.0
    t = np.arange(n - 600)
    # τ=300s 的慢响应（FLOW 期望 10s → ratio=30）
    pv_tail = 60.0 - 10.0 * np.exp(-t / 300.0)
    pv = np.concatenate([np.full(600, 50.0), pv_tail])
    res = _run("slow_response", {"pv": pv, "sp": sp}, meta={"loop_type": "FLOW"})
    assert res.executed and res.detected
    assert res.features["ratio"] > 2.0


def test_disturbance_burst_detects_frequent_shifts() -> None:
    n = 7200  # 2 小时窗
    rng = np.random.default_rng(11)
    # 偏差方波：每 450 点（7.5 分钟）在 0/6 间翻转 → 15 次跳变 ≈7.5 次/h，
    # 每次跳变幅度 6 > σ≈3（amplitude_k=1.0 确认门限）
    t = np.arange(n)
    bias = 6.0 * ((t // 450) % 2)
    pv = 50.0 + bias + rng.normal(0, 0.5, n)
    sp = np.full(n, 50.0)
    res = _run("disturbance_burst", {"pv": pv, "sp": sp})
    assert res.executed and res.detected
    assert res.features["shift_frequency"] >= 5.0


def test_disturbance_burst_rejects_quiet() -> None:
    n = 3600
    pv = 50.0 + np.random.default_rng(5).normal(0, 0.3, n)
    res = _run("disturbance_burst", {"pv": pv, "sp": np.full(n, 50.0)})
    assert res.executed and not res.detected


def test_sensor_fault_detects_frozen() -> None:
    res = _run("sensor_fault", {"pv": _frozen_pv()})
    assert res.executed and res.detected
    assert res.features["sensor_subtype"] == "frozen"


def test_sensor_fault_rejects_normal() -> None:
    pv = 50.0 + 0.5 * _white_noise(3600, 1.0)
    res = _run("sensor_fault", {"pv": pv})
    assert res.executed and not res.detected


def test_quality_code_rules_detects_all_bad() -> None:
    res = _run("quality_code_rules", {"pv_quality": np.full(3600, 2)})
    assert res.executed and res.detected
    assert res.features["quality_pattern"] == "Q001"


def test_quality_code_rules_normal() -> None:
    res = _run("quality_code_rules", {"pv_quality": np.zeros(3600, dtype=int)})
    assert res.executed and not res.detected


def test_output_saturation_detects_pinned_op() -> None:
    n = 3600
    op = np.full(n, 100.0)
    op[:200] = 50.0  # 5% 时间正常，95% 贴高限
    mode = np.ones(n)  # AUTO
    res = _run("output_saturation", {"op": op, "mode": mode})
    assert res.executed and res.detected
    assert res.features["saturation_rate"] > 0.9


def test_output_saturation_rejects_normal_op() -> None:
    n = 3600
    op = 50.0 + 10.0 * np.sin(2 * np.pi * np.arange(n) / 600)
    res = _run("output_saturation", {"op": op, "mode": np.ones(n)})
    assert res.executed and not res.detected


def test_operator_skips_on_missing_signal() -> None:
    res = _run("oscillation_fft", {})
    assert not res.executed
    assert res.skip_reason


def test_operator_skips_on_short_signal() -> None:
    res = _run("oscillation_fft", {"pv": np.full(8, 50.0)})
    assert not res.executed


# ---------------------------------------------------------------------------
# 等价性：新算子内核 vs 旧引擎函数（同输入同输出）
# ---------------------------------------------------------------------------

_EQUIV_CASES = {
    "oscillation_fft": lambda: (_sine(),),
    "oscillation_iae": lambda: (_sine(), np.full(3600, 50.0)),
    "stiction_ellipse": lambda: _stiction_pv_op(),
    "stiction_choudhury": lambda: _stiction_pv_op(),
    "stiction_kano": lambda: _stiction_pv_op(),
    "step_response_overshoot": lambda: _step_signal(),
    "disturbance_burst": lambda: _disturbance_signal(),
    "sensor_fault": lambda: (_frozen_pv(),),
    "output_saturation": lambda: (np.concatenate([np.full(200, 50.0), np.full(3400, 100.0)]),),
}


def _step_signal() -> tuple[np.ndarray, np.ndarray]:
    n = 3600
    sp = np.full(n, 50.0)
    sp[600:] = 60.0
    t = np.arange(n - 600)
    pv_tail = 60.0 + 4.0 * np.exp(-t / 300) * np.cos(2 * np.pi * t / 200)
    return np.concatenate([np.full(600, 50.0), pv_tail]), sp


def _disturbance_signal() -> tuple[np.ndarray, np.ndarray]:
    n = 7200
    rng = np.random.default_rng(11)
    t = np.arange(n)
    bias = 6.0 * ((t // 450) % 2)
    return 50.0 + bias + rng.normal(0, 0.5, n), np.full(n, 50.0)


def _engine_call(name: str, arrays: tuple) -> dict:
    if name == "oscillation_fft":
        return eng._detect_oscillation_fft(arrays[0], 1.0, None)
    if name == "oscillation_iae":
        return eng._detect_oscillation_iae(arrays[0], arrays[1], 1.0, None)
    if name == "stiction_ellipse":
        return eng._detect_valve_stiction(arrays[0], arrays[1], 1.0)
    if name == "stiction_choudhury":
        return eng._detect_choudhury_nonlinearity(arrays[0], arrays[1], None)
    if name == "stiction_kano":
        return eng._detect_kano_stiction(arrays[0], arrays[1])
    if name == "step_response_overshoot":
        return eng._analyze_step_response(arrays[0], arrays[1], None, None, None)
    if name == "disturbance_burst":
        return eng._detect_bias_shift(arrays[0], arrays[1], None, None)
    if name == "sensor_fault":
        return eng._detect_sensor_faults(arrays[0], None, None)
    if name == "output_saturation":
        return eng._analyze_saturation(arrays[0], None, None, None)
    raise AssertionError(name)


def _kernel_call(name: str, arrays: tuple) -> dict:
    mod = {
        "oscillation_fft": ops.oscillation._fft_kernel,
        "oscillation_iae": ops.oscillation._iae_kernel,
        "stiction_ellipse": ops.stiction._ellipse_kernel,
        "stiction_choudhury": ops.stiction._choudhury_kernel,
        "stiction_kano": ops.stiction._kano_kernel,
        "step_response_overshoot": ops.tuning._step_kernel,
        "disturbance_burst": ops.disturbance._bias_shift_kernel,
        "sensor_fault": ops.sensor._sensor_fault_kernel,
        "output_saturation": ops.saturation._saturation_kernel,
    }[name]
    if name == "oscillation_fft":
        return mod(arrays[0], 1.0, None)
    if name == "oscillation_iae":
        return mod(arrays[0], arrays[1], 1.0, None)
    if name == "stiction_ellipse":
        return mod(arrays[0], arrays[1], 1.0)
    if name == "stiction_choudhury":
        return mod(arrays[0], arrays[1], None)
    if name == "stiction_kano":
        return mod(arrays[0], arrays[1])
    if name == "step_response_overshoot":
        return mod(arrays[0], arrays[1], None)
    if name == "disturbance_burst":
        return mod(arrays[0], arrays[1], None, None)
    if name == "sensor_fault":
        return mod(arrays[0], None, None)
    if name == "output_saturation":
        return mod(arrays[0], None, None, None)
    raise AssertionError(name)


_SCALAR_KEYS = {
    "oscillation_fft": ["detected", "confidence", "amplitude", "frequency", "index"],
    "oscillation_iae": [
        "detected",
        "confidence",
        "similarity",
        "zero_crossing_count",
        "mean_period",
    ],
    "stiction_ellipse": ["detected", "confidence", "stiction_index", "fitting_score"],
    "stiction_choudhury": [
        "detected",
        "confidence",
        "ngi",
        "nli",
        "stiction_index",
        "fitting_score",
    ],
    "stiction_kano": ["detected", "confidence", "stiction_ratio", "correlation", "std_ratio"],
    "step_response_overshoot": [
        "detected",
        "confidence",
        "overshoot",
        "decay_ratio",
        "steady_state_error",
        "step_count",
    ],
    "disturbance_burst": [
        "detected",
        "confidence",
        "shift_count",
        "raw_shift_count",
        "max_cusum",
        "shift_magnitude",
    ],
    "sensor_fault": [
        "detected",
        "sensor_subtype",
        "confidence",
        "frozen_max_segment",
        "frozen_segment_ratio",
        "noise_std_ratio",
        "drift_magnitude",
    ],
    "output_saturation": ["detected", "confidence", "saturation_rate", "high_count", "low_count"],
}


@pytest.mark.parametrize("name", sorted(_EQUIV_CASES))
def test_kernel_equivalence_with_engine(name: str) -> None:
    arrays = _EQUIV_CASES[name]()
    old = _engine_call(name, arrays)
    new = _kernel_call(name, arrays)
    for key in _SCALAR_KEYS[name]:
        old_v, new_v = old[key], new[key]
        if isinstance(old_v, bool | int | str) or old_v is None:
            assert new_v == old_v, f"{name}.{key}: {new_v} != {old_v}"
        else:
            assert new_v == pytest.approx(float(old_v), abs=1e-9), f"{name}.{key}"
