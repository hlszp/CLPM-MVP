"""数值健壮性矩阵（任务 G4-①）.

对 metric_calculator 注册表全部 26 个指标计算器参数化 9 类退化输入：
    empty / single_point / constant / all_nan / with_inf /
    unnormalized_range（0~20000 未归一化）/ ts_gap / ts_out_of_order / ts_duplicate

逐单元断言契约：
    1. calculate() 不抛异常；
    2. 返回 MetricResult；
    3. value 为 None（INCONCLUSIVE）或有限值（非 NaN/Inf）；
    4. 有界指标不越界（率类 0~100，线性度类 0~1，时长/幅值类非负）。

实现偏差单元登记在 XFAIL_CELLS：断言失败时按 xfail 记录实际行为，
未登记的单元断言失败即套件失败。全部组合结果矩阵由
test_zz_matrix_summary 汇总输出（pytest -s 可见）。
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from app.contracts.data_types import MetricResult
from app.services.metric_calculator import CALCULATOR_REGISTRY, get_calculator

from .conftest import build_bundle, make_dep_result, make_ts

# ---------------------------------------------------------------------------
# 退化输入模式
# ---------------------------------------------------------------------------

PATTERNS: tuple[str, ...] = (
    "empty",
    "single_point",
    "constant",
    "all_nan",
    "with_inf",
    "unnormalized_range",
    "ts_gap",
    "ts_out_of_order",
    "ts_duplicate",
)

_N = 100


def _normal_signals() -> dict[str, list[Any]]:
    """基准信号：PV 围绕 SP 小幅波动，OP 正常动作，全自控."""
    pv = [50.0 + 2.0 * math.sin(2.0 * math.pi * i / 25.0) for i in range(_N)]
    sp = [50.0] * _N
    op = [50.0 + 1.5 * math.sin(2.0 * math.pi * i / 25.0 + 0.5) for i in range(_N)]
    mode = [1] * _N
    return {"pv": pv, "sp": sp, "op": op, "mode": mode}


def _config_signals() -> dict[str, list[Any]]:
    """CONFIG 标量（列表存储，兼容 _read_config_scalar）."""
    return {
        "pv_range": [100.0],
        "op_range": [100.0],
        "control_type": ["FC"],
        "ideal_settling_time": [45.0],
    }


def _pattern_signals_and_ts(pattern: str) -> tuple[dict[str, list[Any]], list | None]:
    """按退化模式构造信号与时间戳（timestamps=None 表示默认 1s 等间隔）."""
    if pattern == "empty":
        return {}, None
    if pattern == "single_point":
        return {"pv": [50.5], "sp": [50.0], "op": [50.0], "mode": [1]}, None

    signals = _normal_signals()
    if pattern == "constant":
        signals = {"pv": [50.5] * _N, "sp": [50.0] * _N, "op": [50.0] * _N, "mode": [1] * _N}
    elif pattern == "all_nan":
        signals = {
            "pv": [float("nan")] * _N,
            "sp": [float("nan")] * _N,
            "op": [float("nan")] * _N,
            "mode": [1] * _N,
        }
    elif pattern == "with_inf":
        signals["pv"][10] = float("inf")
        signals["pv"][40] = float("-inf")
        signals["op"][20] = float("inf")
    elif pattern == "unnormalized_range":
        # 0~20000 工程量程未归一化（配置仍声明归一化量程，量纲失配压力测试）
        signals = {
            "pv": [10000.0 + 200.0 * math.sin(2.0 * math.pi * i / 25.0) for i in range(_N)],
            "sp": [10000.0] * _N,
            "op": [500.0 + 30.0 * math.sin(2.0 * math.pi * i / 25.0 + 0.5) for i in range(_N)],
            "mode": [1] * _N,
        }
    elif pattern == "ts_gap":
        ts = make_ts(_N)
        gap_start = ts[50]
        from datetime import timedelta

        ts = [t if i <= 50 else t + timedelta(seconds=600.0) for i, t in enumerate(ts)]
        assert ts[51] - gap_start > timedelta(seconds=600.0)
        return signals, ts
    elif pattern == "ts_out_of_order":
        ts = make_ts(_N)
        ts[30], ts[31] = ts[31], ts[30]
        return signals, ts
    elif pattern == "ts_duplicate":
        ts = make_ts(_N)
        ts[60] = ts[59]
        return signals, ts
    return signals, None


def _make_pattern_bundle(pattern: str, metric_code: str):
    signals, timestamps = _pattern_signals_and_ts(pattern)
    if signals:
        signals = {**signals, **_config_signals()}
    return build_bundle(signals, timestamps=timestamps, metric_code=metric_code)


# ---------------------------------------------------------------------------
# 依赖注入（fast_rate / stability_rate）
# ---------------------------------------------------------------------------

DEP_DEFAULTS: dict[str, dict[str, MetricResult]] = {
    "fast_rate": {
        "settling_time": make_dep_result("settling_time", 30.0, {"actual_settling_time": 30.0}),
        "ideal_settling_time": make_dep_result("ideal_settling_time", 45.0),
    },
    "stability_rate": {
        "oscillation_rate": make_dep_result("oscillation_rate", 10.0),
    },
}

# ---------------------------------------------------------------------------
# 结果有界性约定
# ---------------------------------------------------------------------------

#: 率类指标，value ∈ [0, 100]
BOUNDED_0_100: frozenset[str] = frozenset(
    {
        "accuracy_rate",
        "fast_rate",
        "stability_rate",
        "effective_auto_rate",
        "good_value_rate",
        "oscillation_rate",
        "saturation_rate",
        "auto_mode_rate",
        "instrument_fault_rate",
        "stiction_index",
    }
)

#: 归一化系数类，value ∈ [0, 1]
BOUNDED_0_1: frozenset[str] = frozenset({"valve_linearity", "valve_nonlinearity"})

#: 非负指标（时长/幅值/次数/标准差/行程），value ≥ 0
BOUNDED_NONNEG: frozenset[str] = frozenset(
    {
        "settling_time",
        "ideal_settling_time",
        "output_trip_index",
        "valve_operating_range",
        "setpoint_crossing_count",
        "oscillation_amplitude",
        "pv_std",
        "sp_std",
        "op_std",
        "error_std",
    }
)

# ---------------------------------------------------------------------------
# 已登记实现偏差（任务 G4 发现，详见 xfail reason）
# ---------------------------------------------------------------------------

#: D1：NaN/Inf 输入未被过滤，泄漏进指标值（mean/幅值/行程/相关系数类缺有限值守护）
_XFAIL_D1 = "NaN/Inf 泄漏到 value（缺非有限点过滤/有限值守护），应回落 INCONCLUSIVE"
#: D2：statistics.pstdev 对 NaN/Inf 输入抛 AttributeError（_variance 走 Fraction 精确路径）
_XFAIL_D2 = (
    "statistics.pstdev 对 NaN/Inf 抛 AttributeError"
    "('float' object has no attribute 'numerator')，未兜底"
)
#: D3：ARMA 辨识入口未校验非有限值，np.linalg 抛 ValueError
_XFAIL_D3 = "ARMA 辨识（np.linalg）对 NaN/Inf 偏差序列抛 ValueError，入口未校验非有限值"

XFAIL_CELLS: dict[tuple[str, str], str] = {
    # D1：NaN/Inf 泄漏进 value（首跑实测，2026-07-28）
    ("pv_mean", "all_nan"): _XFAIL_D1,
    ("pv_mean", "with_inf"): _XFAIL_D1,
    ("sp_mean", "all_nan"): _XFAIL_D1,
    ("op_mean", "all_nan"): _XFAIL_D1,
    ("op_mean", "with_inf"): _XFAIL_D1,
    ("error_mean", "all_nan"): _XFAIL_D1,
    ("error_mean", "with_inf"): _XFAIL_D1,
    ("oscillation_amplitude", "all_nan"): _XFAIL_D1,
    ("oscillation_amplitude", "with_inf"): _XFAIL_D1,
    ("output_trip_index", "all_nan"): _XFAIL_D1,
    ("output_trip_index", "with_inf"): _XFAIL_D1,
    ("valve_linearity", "all_nan"): _XFAIL_D1,
    ("valve_linearity", "with_inf"): _XFAIL_D1,
    ("valve_nonlinearity", "all_nan"): _XFAIL_D1,
    ("valve_nonlinearity", "with_inf"): _XFAIL_D1,
    ("valve_operating_range", "all_nan"): _XFAIL_D1,
    ("valve_operating_range", "with_inf"): _XFAIL_D1,
    # D2：pstdev 抛 AttributeError（首跑实测，2026-07-28）
    ("pv_std", "all_nan"): _XFAIL_D2,
    ("pv_std", "with_inf"): _XFAIL_D2,
    ("sp_std", "all_nan"): _XFAIL_D2,
    ("op_std", "all_nan"): _XFAIL_D2,
    ("op_std", "with_inf"): _XFAIL_D2,
    ("error_std", "all_nan"): _XFAIL_D2,
    ("error_std", "with_inf"): _XFAIL_D2,
    # D3：ARMA 辨识抛 ValueError（首跑实测，2026-07-28）
    ("settling_time", "all_nan"): _XFAIL_D3,
    ("settling_time", "with_inf"): _XFAIL_D3,
}

# ---------------------------------------------------------------------------
# 结果矩阵记录
# ---------------------------------------------------------------------------

MATRIX: dict[str, dict[str, str]] = {code: {} for code in CALCULATOR_REGISTRY}


def _contract_violation(metric_code: str, result: MetricResult) -> str | None:
    """检查结果是否违反健壮性契约；违规则返回描述，否则 None."""
    if not isinstance(result, MetricResult):
        return f"返回类型非 MetricResult: {type(result).__name__}"
    if result.metric_code != metric_code:
        return f"metric_code 不匹配: {result.metric_code!r}"
    value = result.value
    if value is None:
        return None
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return f"value 非有限值: {value!r}"
    v = float(value)
    if metric_code in BOUNDED_0_100 and not (0.0 <= v <= 100.0):
        return f"率类指标越界 [0,100]: {v}"
    if metric_code in BOUNDED_0_1 and not (0.0 <= v <= 1.0):
        return f"系数类指标越界 [0,1]: {v}"
    if metric_code in BOUNDED_NONNEG and v < 0.0:
        return f"非负指标出现负值: {v}"
    return None


# ---------------------------------------------------------------------------
# 矩阵用例
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pattern", PATTERNS)
@pytest.mark.parametrize("metric_code", sorted(CALCULATOR_REGISTRY))
def test_calculator_robustness(metric_code: str, pattern: str) -> None:
    """健壮性矩阵单元：metric_code × pattern."""
    calc = get_calculator(metric_code)
    assert calc is not None, f"未注册的计算器: {metric_code}"
    if metric_code in DEP_DEFAULTS:
        calc.with_dependencies(DEP_DEFAULTS[metric_code])
    bundle = _make_pattern_bundle(pattern, metric_code)

    failure: str | None = None
    try:
        result = calc.calculate(bundle)
        failure = _contract_violation(metric_code, result)
    except Exception as exc:  # noqa: BLE001 — 矩阵需记录异常而非中断
        result = None
        failure = f"calculate 抛异常: {type(exc).__name__}: {exc}"

    # 记录矩阵单元
    if result is None:
        outcome = f"EXCEPTION ({failure})"
    elif result.value is None:
        reason = (result.details or {}).get("reason", "?")
        outcome = f"INCONCLUSIVE ({reason})"
    else:
        outcome = f"value={result.value}"
    if failure:
        outcome = f"FAIL[{failure}] raw={outcome}"
    MATRIX[metric_code][pattern] = outcome

    if failure:
        xfail_reason = XFAIL_CELLS.get((metric_code, pattern))
        if xfail_reason is not None:
            pytest.xfail(f"{xfail_reason}；实际: {failure}")
        pytest.fail(f"[{metric_code}×{pattern}] 违反健壮性契约: {failure}")


def test_zz_matrix_summary() -> None:
    """汇总输出全部 26×9 组合结果矩阵（pytest -s 可见）."""
    missing = [
        (code, p) for code in sorted(CALCULATOR_REGISTRY) for p in PATTERNS if p not in MATRIX[code]
    ]
    if missing:
        # 单独运行本用例（未先跑矩阵用例）时跳过而非失败
        pytest.skip(f"矩阵不完整（缺 {len(missing)} 单元），请运行整个 test_robustness_matrix.py")
    header = f"{'metric_code':<26}" + "".join(f"{p[:12]:>14}" for p in PATTERNS)
    lines = ["", header, "-" * len(header)]
    for code in sorted(CALCULATOR_REGISTRY):
        row = f"{code:<26}"
        for p in PATTERNS:
            cell = MATRIX[code].get(p, "-")
            row += f"{cell[:13]:>14}"
        lines.append(row)
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# 语义合理性补充断言（矩阵契约之外的"合理值"核查）
# ---------------------------------------------------------------------------


class TestNanInfSemantics:
    """全 NaN / 含 Inf 输入下率类指标不得给满分/极端分，应回落 INCONCLUSIVE.

    矩阵契约只校验"有限值 + 不越界"，以下单元虽满足机械契约，
    但对垃圾输入输出满分语义错误，单独以 xfail 登记（实际值见 reason）。
    """

    @staticmethod
    def _run(metric_code: str, pattern: str) -> MetricResult:
        calc = get_calculator(metric_code)
        assert calc is not None
        if metric_code in DEP_DEFAULTS:
            calc.with_dependencies(DEP_DEFAULTS[metric_code])
        return calc.calculate(_make_pattern_bundle(pattern, metric_code))

    @pytest.mark.xfail(
        reason="accuracy_rate 全 NaN 输入实际返回满分 100.0"
        "（NaN 传播后经 _clamp 被钳到上界），应 INCONCLUSIVE",
        strict=False,
    )
    def test_accuracy_all_nan_not_full_score(self) -> None:
        assert self._run("accuracy_rate", "all_nan").value is None

    @pytest.mark.xfail(
        reason="accuracy_rate 含 Inf 输入实际返回满分 100.0"
        "（inf-inf→NaN 传播后经 _clamp 钳到上界），应 INCONCLUSIVE",
        strict=False,
    )
    def test_accuracy_with_inf_not_full_score(self) -> None:
        assert self._run("accuracy_rate", "with_inf").value is None

    @pytest.mark.xfail(
        reason="stability_rate 全 NaN 输入实际返回满分 100.0（std=NaN → exp(NaN) → _clamp 上界），"
        "应 INCONCLUSIVE",
        strict=False,
    )
    def test_stability_all_nan_not_full_score(self) -> None:
        assert self._run("stability_rate", "all_nan").value is None

    @pytest.mark.xfail(
        reason="stability_rate 含 Inf 输入实际返回满分 100.0（std=NaN 传播），应 INCONCLUSIVE",
        strict=False,
    )
    def test_stability_with_inf_not_full_score(self) -> None:
        assert self._run("stability_rate", "with_inf").value is None

    @pytest.mark.xfail(
        reason="oscillation_rate 含 Inf 输入实际返回 100.0（Inf 参与 IAE 相似率计算），"
        "应 INCONCLUSIVE 或 0",
        strict=False,
    )
    def test_oscillation_with_inf_not_full_score(self) -> None:
        assert self._run("oscillation_rate", "with_inf").value is None
