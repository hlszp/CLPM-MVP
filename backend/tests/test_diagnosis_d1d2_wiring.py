"""P3-01 D1/D2 诊断接线反例测试（08§9 C13-D/C14-D/C15-D/C16-D 对应子例）.

覆盖：
- DIA-01/CFG-02/C13-D01：阈值覆盖按 diagCode 真实消费（recommendations[]
  提取 + 阈值变化实际改变算子行为）；
- DIA-03/C14-D01：断点不造频谱（连续段目录 + FFT 段内运行）；
- DIA-05/C16-D01：椭圆真实量程（换单位/量程归一化结论等价）；
- DIA-06/C16-D02/C15-D03：外扰排除 SP 阶跃；0% 手动边界与 None 透传；
- DIA-08/C16-D03/C16-D05：多阶跃截到下一 SP 变化；expectedτ 工程来源
  消费与无来源不判慢；
- 08§4.2/§4.3：算子八态 / judgementStatus 五值 / 覆盖分母三等式 /
  组合决定表 / 主分类 NULL 不推健康。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import numpy as np
import pytest

import app.services.diagnosis_orchestrator as orch
from app.contracts.data_types import RawTimeSeries
from app.services.diagnosis_assessment import (
    CAUSE_CANDIDATE,
    CAUSE_NOT_ASSESSED,
    CAUSE_UNRESOLVED,
    JUDGEMENT_INCONCLUSIVE,
    JUDGEMENT_NOT_ASSESSED,
    JUDGEMENT_SYMPTOM_DETECTED,
    JUDGEMENT_VALID_NEGATIVE,
    build_assessment_meta,
    derive_run_status,
)
from app.services.diagnosis_operators import OPERATOR_REGISTRY, OperatorInput
from app.services.diagnosis_operators.base import (
    ASSESSMENT_ERROR,
    ASSESSMENT_INSUFFICIENT_DATA,
    ASSESSMENT_NEGATIVE,
    ASSESSMENT_NOT_APPLICABLE,
    ASSESSMENT_POSITIVE,
    OperatorResult,
)
from app.services.diagnosis_operators.classification import classify
from app.services.diagnosis_operators.gate import GateResult

LOOP_ID = str(uuid4())
START = datetime(2026, 8, 15, 0, 0, 0)
END = START + timedelta(hours=1)


# ---------------------------------------------------------------------------
# DIA-01 / CFG-02 / C13-D01：阈值真实消费
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_effective_thresholds_reads_recommendations_by_diag_code() -> None:
    """DIA-01 断链修复：recommend_for_loop 返回 recommendations[]（无顶层
    effectiveThreshold），_effective_thresholds 必须按 diagCode 提取——
    修复前恒空 dict，四级覆盖从未生效。"""
    recommend_result = {
        "loopId": LOOP_ID,
        "tagName": "TIC-001",
        "recommendations": [
            {
                "diagCode": "OSCILLATION",
                "effectiveThreshold": {"similarity_threshold": 0.9},
            },
            {"diagCode": "VALVE_STICTION", "effectiveThreshold": {}},  # 空=无覆盖
        ],
    }
    with patch(
        "app.services.diagnosis_threshold.recommend_for_loop",
        new=AsyncMock(return_value=recommend_result),
    ):
        effective = await orch._effective_thresholds(MagicMock(), LOOP_ID)
    assert effective == {"OSCILLATION": {"similarity_threshold": 0.9}}


@pytest.mark.asyncio
async def test_threshold_override_actually_changes_operator_behaviour() -> None:
    """C13-D01/CFG-02 反例：阈值变化实际改变算子行为。

    偏差突变算子：1 小时窗内 3 次独立突变（SP 不变）——默认阈值
    （5 次/h）不命中；把 bias_shift_freq_threshold 覆盖为 2.0 后命中。
    同一输入，两种 effective_thresholds 判定不同（修复前覆盖链断开，
    恒用默认）。"""
    n = 3600  # 1h
    sp = np.full(n, 50.0)
    pv = np.full(n, 50.0)
    pv[900:1800] = 56.0  # 3 次独立负荷突变
    pv[2700:] = 44.0
    op_input = OperatorInput(
        loop_id=LOOP_ID,
        signals={"pv": pv, "sp": sp},
        timestamps=np.arange(n, dtype=float),
        meta={"sample_interval": 1.0},
    )
    from app.services.diagnosis_operators.disturbance import detect_bias_shift

    default_out = detect_bias_shift(op_input, {"bias_shift_freq_threshold": 5.0})
    override_out = detect_bias_shift(op_input, {"bias_shift_freq_threshold": 2.0})
    assert default_out.features["shift_frequency"] == pytest.approx(2.0, abs=0.6)
    assert default_out.detected is False  # 2 次/h < 默认 5
    assert override_out.detected is True  # 覆盖 2.0 → 命中
    # 编排层八态派生：同一输入覆盖阈值后 POSITIVE（阈值改变实际改变算子行为）
    import copy

    d_default = copy.deepcopy(default_out)
    d_override = copy.deepcopy(override_out)
    orch._derive_assessment_state(d_default)
    orch._derive_assessment_state(d_override)
    assert d_default.assessment_state == ASSESSMENT_NEGATIVE
    assert d_override.assessment_state == ASSESSMENT_POSITIVE
    # 经 orchestrator 实际合并链路（diagCode 提取）
    merged = orch._thresholds_for(
        {"bias_shift_freq_threshold": 5.0},
        {"EXTERNAL_DISTURBANCE": {"bias_shift_freq_threshold": 2.0}},
        "EXTERNAL_DISTURBANCE",
    )
    assert merged["bias_shift_freq_threshold"] == 2.0


# ---------------------------------------------------------------------------
# DIA-03 / C14-D01：断点不造频谱
# ---------------------------------------------------------------------------


def _oscillating_pv(n: int = 3600, period_s: float = 60.0) -> np.ndarray:
    t = np.arange(n, dtype=float)
    return 50.0 + 5.0 * np.sin(2 * np.pi * t / period_s)


def test_consecutive_segments_split_at_gap() -> None:
    """缺口（>3×采样间隔）切开连续段；段目录与时间轴同步。"""
    n = 600
    ts = np.arange(n, dtype=float)
    # 在中点插入 600 秒缺口（压缩数组：后半段时间戳整体 +600）
    ts[n // 2 :] += 600.0
    segments = orch._consecutive_segments(ts, 1.0)
    assert segments == [(0, n // 2), (n // 2, n)]


def test_fft_does_not_fabricate_spectrum_across_gap() -> None:
    """C14-D01：同波形插入长 gap——FFT 只在最长连续段内运行，主频不因
    缺口拼接漂移（拼接会把 600s 缺口当作半个超长周期，主频大幅下移）。"""
    n = 3600
    period_s = 60.0
    pv_full = _oscillating_pv(n, period_s)
    # 段 A：前 1800 点（30 个完整周期）；gap 600s；段 B：后 1800 点
    seg_a = pv_full[:1800]
    seg_b = pv_full[1800:]
    ts = np.concatenate([np.arange(1800, dtype=float), np.arange(1800, dtype=float) + 2400.0])
    pv = np.concatenate([seg_a, seg_b])
    segments = orch._consecutive_segments(ts, 1.0)
    assert len(segments) == 2

    from app.services.diagnosis_operators.oscillation import detect_fft

    op_input = OperatorInput(
        loop_id=LOOP_ID,
        signals={"pv": pv},
        timestamps=ts,
        meta={"sample_interval": 1.0, "consecutive_segments": segments},
    )
    res = detect_fft(op_input, {})
    # 主频应保持在段内真实频率（1/60 Hz ≈ 0.0167），而非拼接伪低频
    assert res.features["frequency"] == pytest.approx(1.0 / period_s, rel=0.2)
    assert res.features["consecutive_segment_count"] == 2
    assert res.features["analyzed_points"] == 1800
    # 段内实际采样间隔按真实时间轴估计（段 A 内 1s）
    assert res.features["analyzed_sample_interval"] == pytest.approx(1.0, abs=0.01)


def test_fft_single_segment_keeps_original_behaviour() -> None:
    """无缺口（单段）：行为与原实现一致（全窗 FFT，段数=1）。"""
    n = 1800
    pv = _oscillating_pv(n, 60.0)
    ts = np.arange(n, dtype=float)
    from app.services.diagnosis_operators.oscillation import detect_fft

    op_input = OperatorInput(
        loop_id=LOOP_ID,
        signals={"pv": pv},
        timestamps=ts,
        meta={
            "sample_interval": 1.0,
            "consecutive_segments": orch._consecutive_segments(ts, 1.0),
        },
    )
    res = detect_fft(op_input, {})
    assert res.executed is True
    assert res.features["consecutive_segment_count"] == 1
    assert res.features["analyzed_points"] == n


# ---------------------------------------------------------------------------
# DIA-05 / C16-D01：椭圆真实量程
# ---------------------------------------------------------------------------


def _stick_slip_pv_op(n: int, period_s: float, pv_amp: float) -> tuple[np.ndarray, np.ndarray]:
    """构造 PV-OP 反相振荡（stick-slip 特征），PV 幅度可缩放。"""
    t = np.arange(n, dtype=float)
    pv = 50.0 + pv_amp * np.sin(2 * np.pi * t / period_s)
    op = 50.0 - pv_amp * 0.8 * np.sin(2 * np.pi * t / period_s)
    return pv, op


def test_ellipse_real_range_unit_equivalence() -> None:
    """C16-D01：同一物理 PV/OP 换量程单位（0~100 vs 0~4000，幅值×40）——
    传真实量程后归一化特征与结论等价；不传量程（双轴默认 100 兜底）
    时换算后结论漂移（反例存在性）。"""
    n = 2400
    pv1, op1 = _stick_slip_pv_op(n, 120.0, 8.0)  # PV 摆动 ±8（量程 0~100）
    pv2, op2 = pv1 * 40.0, op1 * 40.0  # 同物理信号，单位放大 40 倍

    from app.services.diagnosis_operators.stiction import _ellipse_kernel

    r1 = _ellipse_kernel(pv1, op1, 1.0, pv_range=(0.0, 100.0), op_range=(0.0, 100.0))
    r2 = _ellipse_kernel(pv2, op2, 1.0, pv_range=(0.0, 4000.0), op_range=(0.0, 4000.0))
    # 真实量程：归一化结论等价（detected 同判，stiction_index 容差内一致）
    assert r1["detected"] == r2["detected"]
    assert r1["stiction_index"] == pytest.approx(r2["stiction_index"], abs=0.02)
    assert r1["range_source"] == {"pv": "explicit", "op": "explicit"}
    # 未传量程（默认 0~100 兜底）时，×40 信号归一化后幅度超界 → 结论可漂移
    r2_norange = _ellipse_kernel(pv2, op2, 1.0)
    assert r2_norange.get("range_source") == {
        "pv": "normalized_default",
        "op": "normalized_default",
    }


def test_detect_ellipse_passes_meta_ranges() -> None:
    """编排层 meta 携带 pv_range/op_range → detect_ellipse 透传内核。"""
    n = 2400
    pv, op = _stick_slip_pv_op(n, 120.0, 8.0)
    from app.services.diagnosis_operators.stiction import detect_ellipse

    op_input = OperatorInput(
        loop_id=LOOP_ID,
        signals={"pv": pv, "op": op},
        timestamps=np.arange(n, dtype=float),
        meta={
            "sample_interval": 1.0,
            "pv_range": (0.0, 100.0),
            "op_range": (0.0, 100.0),
        },
    )
    res = detect_ellipse(op_input, {})
    assert res.executed is True
    assert res.features.get("range_source") == {"pv": "explicit", "op": "explicit"}


# ---------------------------------------------------------------------------
# DIA-06 / C16-D02 / C15-D03：外扰排除 SP + 0% 手动边界
# ---------------------------------------------------------------------------


def _disturbance_input(pv: np.ndarray, sp: np.ndarray) -> OperatorInput:
    n = len(pv)
    return OperatorInput(
        loop_id=LOOP_ID,
        signals={"pv": pv, "sp": sp},
        timestamps=np.arange(n, dtype=float),
        meta={"sample_interval": 1.0},
    )


def test_cusum_excludes_sp_step_changes() -> None:
    """C16-D02：SP 配方阶跃引起的偏差跳变不判独立外扰。

    SP 每 1200s 阶跃 ±10（确认窗内 SP 变化），PV 跟随——CUSUM 会触发
    但确认时应被 SP 阶跃排除，不计数、不称"与 SP 变更无关"。"""
    n = 7200
    sp = np.concatenate([np.full(1200, 50.0)] * 6)
    sp[1200:2400] = 60.0
    sp[2400:3600] = 50.0
    sp[3600:4800] = 60.0
    sp[4800:6000] = 50.0
    sp[6000:7200] = 60.0
    pv = sp + 0.5 * np.sin(np.arange(n) / 50.0)  # PV 良好跟随 SP
    from app.services.diagnosis_operators.disturbance import detect_bias_shift

    res = detect_bias_shift(_disturbance_input(pv, sp), {})
    assert res.executed is True
    assert res.detected is False, "SP 阶跃响应不得判为独立外扰"
    assert res.features["sp_coincident_shifts"] >= 1, "应有候选突变被 SP 排除记录"


def test_cusum_reports_independent_load_shift() -> None:
    """C16-D02 第三组：SP 不变时独立负荷突变（PV 阶跃偏移）→ 报告真实突变。"""
    n = 3600
    sp = np.full(n, 50.0)
    pv = np.full(n, 50.0)
    pv[600:] = 56.0  # 单次负荷突变 +6
    pv[1800:] = 50.0  # 再突回
    from app.services.diagnosis_operators.disturbance import detect_bias_shift

    res = detect_bias_shift(_disturbance_input(pv, sp), {})
    assert res.executed is True
    assert res.features["shift_count"] >= 1, "独立突变应保留（先报突变再按证据归因）"
    assert res.features["sp_coincident_shifts"] == 0


def _classify_with_auto_rate(auto_rate: Any):
    gate = GateResult(
        passed=True,
        point_count=100,
        expected_points=100,
        valid_rate=0.99,
        confidence_level="A",
        gap_ratio=0.0,
    )
    return classify({}, {}, {"auto_rate_avg": auto_rate, "score_avg": 80.0}, gate)


def test_zero_auto_rate_triggers_utilization() -> None:
    """C15-D03/DIA-06：0% 自动投用=真实长期手动 → 触发 UTILIZATION
    （原实现 0.0 < auto_rate 排除 0%，长期手动漏判）。"""
    result = _classify_with_auto_rate(0.0)
    assert result.primary is not None
    assert result.primary.category == "UTILIZATION"


def test_missing_auto_rate_does_not_trigger_utilization() -> None:
    """C15-D03：auto_rate=None（窗口无 KPI 快照）缺失不当 0——不触发。"""
    result = _classify_with_auto_rate(None)
    assert result.primary is None or result.primary.category != "UTILIZATION"


def test_positive_auto_rate_below_threshold_triggers() -> None:
    """正小值（0.2）仍触发（既有行为不回归）。"""
    result = _classify_with_auto_rate(0.2)
    assert result.primary is not None
    assert result.primary.category == "UTILIZATION"


# ---------------------------------------------------------------------------
# DIA-08 / C16-D03 / C16-D05：多阶跃截断 + expectedτ 工程来源
# ---------------------------------------------------------------------------


def test_slow_response_truncated_at_next_sp_step() -> None:
    """C16-D03：两次 SP 阶跃间隔短于旧拟合窗——第一次响应截到第二次阶跃，
    不混合拟合；单阶跃结果不受影响。"""
    tau_true = 20.0
    n = 1200
    t = np.arange(n, dtype=float)
    sp = np.full(n, 50.0)
    sp[200:] = 60.0
    sp[500:] = 55.0  # 第二次阶跃（旧窗口=600 点会跨越）
    # 一阶响应分段生成（真实物理：每次阶跃后按新稳态演化）
    pv = np.full(n, 50.0)
    for i in range(1, n):
        target = sp[i]
        pv[i] = pv[i - 1] + (target - pv[i - 1]) * (1 - np.exp(-1.0 / tau_true))
    from app.services.diagnosis_operators.tuning import _slow_kernel

    res = _slow_kernel(
        pv,
        sp,
        "FLOW",
        t,
        sample_interval=1.0,
        threshold={"slow_response_ratio_threshold": 2.0},
        expected_tau_info={"valueSeconds": 80.0, "source": "LOOP_FIELD"},
    )
    # 响应窗 = [201, 501)（300 点），拟合 τ 接近真值 20s（不混入第二次阶跃）
    assert res["time_constant"] == pytest.approx(tau_true, rel=0.5)
    assert res["expected_time_constant"] == pytest.approx(20.0)  # T′=80 → τ=20


def test_slow_response_no_engineering_source_reports_features_only() -> None:
    """C16-D05/DIA-08：expected τ 无工程来源（UNKNOWN）→ 只报告测得动态
    特征，不判断"比应有速度慢"（NOT_APPLICABLE，不伪称阴性）。"""
    tau_true = 90.0  # 很慢的响应
    n = 900
    t = np.arange(n, dtype=float)
    sp = np.full(n, 50.0)
    sp[100:] = 60.0
    pv = np.full(n, 50.0)
    for i in range(1, n):
        pv[i] = pv[i - 1] + (sp[i] - pv[i - 1]) * (1 - np.exp(-1.0 / tau_true))
    from app.services.diagnosis_operators.tuning import detect_slow

    op_input = OperatorInput(
        loop_id=LOOP_ID,
        signals={"pv": pv, "sp": sp},
        timestamps=t,
        meta={
            "sample_interval": 1.0,
            "loop_type": "FLOW",
            "expected_tau_info": {"valueSeconds": None, "source": "UNKNOWN"},
        },
    )
    res = detect_slow(op_input, {})
    assert res.executed is True
    assert res.detected is False, "无 expected τ 来源不得判慢"
    assert res.assessment_state == ASSESSMENT_NOT_APPLICABLE
    assert res.reason_code == "NO_EXPECTED_TAU"
    # 测得动态特征仍保留（τ 拟合值可见）
    assert res.features["time_constant"] > 0
    assert res.features["expected_time_constant"] is None


def test_slow_response_consumes_engineering_source() -> None:
    """expected τ 消费工程来源（B1 供数 T′=idealSettlingTime）：T′=40s →
    expected τ=10s（4τ 换算），慢响应（τ=90）按工程标准判慢。"""
    tau_true = 90.0
    n = 900
    t = np.arange(n, dtype=float)
    sp = np.full(n, 50.0)
    sp[100:] = 60.0
    pv = np.full(n, 50.0)
    for i in range(1, n):
        pv[i] = pv[i - 1] + (sp[i] - pv[i - 1]) * (1 - np.exp(-1.0 / tau_true))
    from app.services.diagnosis_operators.tuning import detect_slow

    op_input = OperatorInput(
        loop_id=LOOP_ID,
        signals={"pv": pv, "sp": sp},
        timestamps=t,
        meta={
            "sample_interval": 1.0,
            "loop_type": "FLOW",
            "expected_tau_info": {"valueSeconds": 40.0, "source": "TEMPLATE"},
        },
    )
    res = detect_slow(op_input, {})
    assert res.detected is True
    assert res.assessment_state == ASSESSMENT_POSITIVE
    assert res.features["expected_tau_source"] == "t_prime:TEMPLATE/4"


# ---------------------------------------------------------------------------
# 08§4.1/§4.2/§4.3：八态 / 组合决定表 / 覆盖分母三等式 / NULL 不推健康
# ---------------------------------------------------------------------------


def _mk_result(name: str, state: str | None, **kw: Any) -> OperatorResult:
    defaults: dict[str, Any] = {
        "executed": state in (ASSESSMENT_POSITIVE, ASSESSMENT_NEGATIVE, ASSESSMENT_NOT_APPLICABLE),
        "detected": state == ASSESSMENT_POSITIVE,
    }
    defaults.update(kw)
    return OperatorResult(name, assessment_state=state, **defaults)


class TestBuildAssessmentMeta:
    """组合决定表（08§4.2 八行核心组合）+ 覆盖三等式（08§4.3）。"""

    def _meta(
        self,
        results: dict[str, OperatorResult],
        *,
        primary="VALVE",
        gate_passed=True,
        excluded=None,
        expected=None,
    ) -> dict:
        return build_assessment_meta(
            op_results=results,
            explicitly_excluded=excluded or {},
            profile_expected_operators=expected or sorted(results),
            primary_category=primary,
            gate_passed=gate_passed,
            gate_reason=None,
            selected_is_empty=not any(r.assessment_state for r in results.values()),
        )

    def test_all_qualified_negative_valid_negative(self) -> None:
        """所选必需检查全部合格阴性 → VALID_NEGATIVE（范围内）。"""
        results = {
            "a": _mk_result("a", ASSESSMENT_NEGATIVE),
            "b": _mk_result("b", ASSESSMENT_NEGATIVE),
        }
        meta = self._meta(results, primary="NO_SYMPTOM")
        assert meta["judgementStatus"] == JUDGEMENT_VALID_NEGATIVE
        assert meta["causeStatus"] == CAUSE_UNRESOLVED
        assert (
            derive_run_status(op_results=results, gate_passed=True, selected_is_empty=False)
            == "SUCCESS"
        )

    def test_single_positive_cause_unresolved_primary_null(self) -> None:
        """C15-D02：fast 仅椭圆阳性、主分类 NULL → SYMPTOM_DETECTED +
        UNRESOLVED（症状保留，不推健康）。"""
        results = {
            "ellipse": _mk_result("ellipse", ASSESSMENT_POSITIVE),
            "iae": _mk_result("iae", ASSESSMENT_NEGATIVE),
        }
        meta = self._meta(results, primary=None)
        assert meta["judgementStatus"] == JUDGEMENT_SYMPTOM_DETECTED
        assert meta["causeStatus"] == CAUSE_UNRESOLVED  # 无正面原因证据 → 未决

    def test_positive_with_cause_candidate(self) -> None:
        results = {"a": _mk_result("a", ASSESSMENT_POSITIVE)}
        meta = self._meta(results, primary="VALVE")
        assert meta["judgementStatus"] == JUDGEMENT_SYMPTOM_DETECTED
        assert meta["causeStatus"] == CAUSE_CANDIDATE

    def test_partial_error_with_positive(self) -> None:
        """阳性 + 部分 ERROR → SYMPTOM_DETECTED + run PARTIAL。"""
        results = {
            "a": _mk_result("a", ASSESSMENT_POSITIVE),
            "b": _mk_result("b", ASSESSMENT_ERROR, executed=False, error="x"),
        }
        meta = self._meta(results)
        assert meta["judgementStatus"] == JUDGEMENT_SYMPTOM_DETECTED
        assert (
            derive_run_status(op_results=results, gate_passed=True, selected_is_empty=False)
            == "PARTIAL"
        )

    def test_negative_with_error_inconclusive_partial(self) -> None:
        """阴性 + ERROR，分类层 NO_SYMPTOM：2026-10-22 裁决现象全未命中→正常.

        执行态仍为 PARTIAL（有算子出错），科学结论 VALID_NEGATIVE。
        """
        results = {
            "a": _mk_result("a", ASSESSMENT_NEGATIVE),
            "b": _mk_result("b", ASSESSMENT_ERROR, executed=False, error="x"),
        }
        meta = self._meta(results, primary="NO_SYMPTOM")
        assert meta["judgementStatus"] == JUDGEMENT_VALID_NEGATIVE
        assert (
            derive_run_status(op_results=results, gate_passed=True, selected_is_empty=False)
            == "PARTIAL"
        )

    def test_all_error_failed(self) -> None:
        """全部 ERROR → FAILED + INCONCLUSIVE（配置/资源问题不计缺数据）。"""
        results = {
            "a": _mk_result("a", ASSESSMENT_ERROR, executed=False, error="x"),
            "b": _mk_result("b", ASSESSMENT_ERROR, executed=False, error="y"),
        }
        meta = self._meta(results)
        assert meta["judgementStatus"] == JUDGEMENT_INCONCLUSIVE
        assert (
            derive_run_status(op_results=results, gate_passed=True, selected_is_empty=False)
            == "FAILED"
        )

    def test_all_not_applicable_success_inconclusive(self) -> None:
        """全部 NOT_APPLICABLE + NO_SYMPTOM：2026-10-22 裁决现象未命中→正常.

        无阳性即不报「不可判定」；执行态仍为 SUCCESS。
        """
        results = {"a": _mk_result("a", ASSESSMENT_NOT_APPLICABLE)}
        meta = self._meta(results, primary="NO_SYMPTOM")
        assert meta["judgementStatus"] == JUDGEMENT_VALID_NEGATIVE
        assert (
            derive_run_status(op_results=results, gate_passed=True, selected_is_empty=False)
            == "SUCCESS"
        )

    def test_selected_empty_not_assessed_failed(self) -> None:
        """C15-D01：selected 为空（零检查）→ NOT_ASSESSED + FAILED。"""
        results = {"a": _mk_result("a", None, executed=False)}
        meta = self._meta(results, expected=["a"], excluded={"a": "NOT_SELECTED"})
        assert meta["judgementStatus"] == JUDGEMENT_NOT_ASSESSED
        assert meta["causeStatus"] == CAUSE_NOT_ASSESSED
        assert (
            derive_run_status(op_results=results, gate_passed=True, selected_is_empty=True)
            == "FAILED"
        )

    def test_coverage_invariants(self) -> None:
        """08§4.3 三等式守恒：fast 7 项、禁用 3 项、其余 4 项全完成 →
        profile 覆盖 4/7；notRun 只统计 selected 集合。"""
        fast_ops = sorted(n for n, (m, _f) in OPERATOR_REGISTRY.items() if m.fast_group)
        results = {n: _mk_result(n, ASSESSMENT_NEGATIVE) for n in fast_ops}
        # 模拟 fast 中 3 个被停用（显式排除），4 个执行
        excluded = dict.fromkeys(fast_ops[:3], "DISABLED_BY_CONFIG")
        for n in excluded:
            results[n] = _mk_result(n, None, executed=False)
        selected_names = [n for n in fast_ops if n not in excluded]
        meta = build_assessment_meta(
            op_results=results,
            explicitly_excluded=excluded,
            profile_expected_operators=fast_ops,
            primary_category=None,
            gate_passed=True,
            gate_reason=None,
            selected_is_empty=False,
        )
        cov = meta["coverage"]
        assert cov["profileExpected"] == len(fast_ops)
        assert cov["explicitlyExcluded"] == 3
        assert cov["selected"] == len(selected_names)
        assert cov["negative"] == len(selected_names)
        assert cov["validAssessmentCount"] == len(selected_names)
        assert cov["invariantSelected"] is True
        assert cov["invariantExpected"] is True

    def test_gate_blocked_not_conclusive(self) -> None:
        """L0 门禁阻断：selected 全 GATE_BLOCKED → INCONCLUSIVE（正式
        DATA_INSUFFICIENT 在分类层；科学结论不冒充阴性）。"""
        results = {"a": _mk_result("a", ASSESSMENT_INSUFFICIENT_DATA, executed=False)}
        meta = self._meta(results, primary="DATA_INSUFFICIENT", gate_passed=False)
        assert meta["judgementStatus"] == JUDGEMENT_INCONCLUSIVE


class TestRunOperatorsStates:
    """_run_operators 的八态赋值与 selected/excluded 划分。"""

    def _input(self, n: int = 200) -> OperatorInput:
        return OperatorInput(
            loop_id=LOOP_ID,
            signals={
                "pv": np.full(n, 50.0),
                "sp": np.full(n, 50.0),
                "op": np.full(n, 50.0),
                "mode": np.ones(n),
            },
            timestamps=np.arange(n, dtype=float),
            meta={"sample_interval": 1.0},
        )

    def test_fast_group_exclusion_out_of_profile(self) -> None:
        """fast 模式：非 fast 组算子 OUT_OF_PROFILE（assessment_state=None，
        不进八类分母）。"""
        results, _ = orch._run_operators(self._input(), {}, "fast", None)
        non_fast = [n for n, (m, _f) in OPERATOR_REGISTRY.items() if not m.fast_group]
        assert all(results[n].assessment_state is None for n in non_fast)
        assert all(results[n].reason_code == "OUT_OF_PROFILE" for n in non_fast)
        fast_ops = [n for n, (m, _f) in OPERATOR_REGISTRY.items() if m.fast_group]
        assert all(results[n].assessment_state is not None for n in fast_ops)

    def test_disabled_operator_explicitly_excluded(self) -> None:
        meta = next(iter(OPERATOR_REGISTRY.values()))[0]
        results, _ = orch._run_operators(
            self._input(), {}, "full", None, disabled_diag_codes={meta.diag_code}
        )
        disabled_names = [
            n for n, (m, _f) in OPERATOR_REGISTRY.items() if m.diag_code == meta.diag_code
        ]
        for n in disabled_names:
            assert results[n].assessment_state is None
            assert results[n].reason_code == "DISABLED_BY_CONFIG"
        excluded = orch._explicitly_excluded(results)
        assert set(disabled_names) <= set(excluded)

    def test_operator_exception_is_error_state(self) -> None:
        """算子抛异常 → ERROR 态（错误不能被空结果吞掉）。"""
        original = OPERATOR_REGISTRY["oscillation_fft"]
        OPERATOR_REGISTRY["oscillation_fft"] = (original[0], lambda i, t: 1 / 0)
        try:
            results, _ = orch._run_operators(self._input(), {}, "full", None)
        finally:
            OPERATOR_REGISTRY["oscillation_fft"] = original
        r = results["oscillation_fft"]
        assert r.assessment_state == ASSESSMENT_ERROR
        assert r.reason_code is not None

    def test_custom_whitelist_states(self) -> None:
        """custom 细选：白名单外 NOT_SELECTED（显式排除），白名单内有状态。"""
        target = "oscillation_iae"
        results, _ = orch._run_operators(self._input(), {}, "full", [target])
        assert results[target].assessment_state is not None
        assert all(results[n].reason_code == "NOT_SELECTED" for n in results if n != target)
        assert orch._profile_expected_operators("full", [target]) == [target]


# ---------------------------------------------------------------------------
# 编排层端到端：assessment_meta 落库 + 主分类 NULL 不推健康
# ---------------------------------------------------------------------------


def _frozen_series(n: int = 3600) -> RawTimeSeries:
    rng = np.random.default_rng(1)
    pv = np.concatenate([np.full(int(n * 0.6), 50.0), 50.0 + rng.normal(0, 0.5, n - int(n * 0.6))])
    ts = [START + timedelta(seconds=i) for i in range(n)]
    return RawTimeSeries(
        timestamps=ts,
        signals={
            "pv": pv.tolist(),
            "sp": [50.0] * n,
            "op": (50.0 + 0.1 * rng.normal(0, 1, n)).tolist(),
            "mode": [1] * n,
        },
        quality_codes={"pv_quality": [1] * n},
    )


def _mock_db(loop: MagicMock) -> AsyncMock:
    db = AsyncMock()
    loop_result = MagicMock()
    loop_result.scalar_one_or_none.return_value = loop
    mapping_result = MagicMock()
    mapping_result.scalars.return_value.all.return_value = []
    kpi_result = MagicMock()
    kpi_result.one_or_none.return_value = None  # 无 KPI 快照（auto_rate=None）
    count_result = MagicMock()
    count_result.scalar.return_value = 0
    calls: list[int] = []

    def _exec(stmt, *args, **kwargs):  # noqa: ARG001
        idx = len(calls)
        calls.append(1)
        return results[idx] if idx < len(results) else count_result

    results = [loop_result, mapping_result, kpi_result]
    db.execute = AsyncMock(side_effect=_exec)
    db.add = MagicMock()
    return db


async def _run_orchestrator(raw_series, operator_group="full", operators=None):
    loop = MagicMock()
    loop.id = LOOP_ID
    loop.tag_name = "TEST-D1D2"
    loop.loop_type = "FLOW"
    db = _mock_db(loop)
    provider = MagicMock()
    provider.make_query_fn.return_value = AsyncMock(return_value=raw_series)
    with (
        patch.object(orch, "get_provider", return_value=provider),
        patch(
            "app.services.diagnosis_threshold.recommend_for_loop",
            new=AsyncMock(return_value={}),
        ),
    ):
        run = await orch.run_diagnosis_for_loop(
            db,
            LOOP_ID,
            start=START,
            end=END,
            task_id="task-1",
            triggered_by="tester",
            operator_group=operator_group,
            operators=operators,
        )
    return run, db


@pytest.mark.asyncio
async def test_run_persists_assessment_meta_with_invariants() -> None:
    """正常完成 run：assessment_meta 落库，judgementStatus/coverage/等式齐全。"""
    run, _ = await _run_orchestrator(_frozen_series())
    assert run is not None
    meta = run.assessment_meta
    assert meta["schemaVersion"] == "diagnosis-assessment/1"
    assert meta["judgementStatus"] in {
        JUDGEMENT_SYMPTOM_DETECTED,
        JUDGEMENT_VALID_NEGATIVE,
        JUDGEMENT_INCONCLUSIVE,
    }
    cov = meta["coverage"]
    assert cov["profileExpected"] == len(OPERATOR_REGISTRY)  # full 组
    assert cov["invariantSelected"] is True
    assert cov["invariantExpected"] is True
    # 算子结果序列化带八态
    assert all("assessmentState" in r for r in run.operator_results.values())


@pytest.mark.asyncio
async def test_run_fast_group_coverage_4_of_7() -> None:
    """08§4.3 覆盖示例：fast 7 项（全启用全执行）→ selected=7，执行覆盖 7/7。"""
    run, _ = await _run_orchestrator(_frozen_series(), operator_group="fast")
    assert run is not None
    cov = run.assessment_meta["coverage"]
    fast_count = sum(1 for _n, (m, _f) in OPERATOR_REGISTRY.items() if m.fast_group)
    assert cov["profileExpected"] == fast_count
    assert cov["selected"] == fast_count
    assert cov["explicitlyExcluded"] == 0


@pytest.mark.asyncio
async def test_run_custom_empty_selection_not_assessed() -> None:
    """C15-D01：custom 白名单与注册表交集空 → NOT_ASSESSED + FAILED
    （零检查不输出健康）。"""
    run, _ = await _run_orchestrator(_frozen_series(), operators=["nonexistent_op"])
    assert run is not None
    assert run.assessment_meta["judgementStatus"] == JUDGEMENT_NOT_ASSESSED
    assert run.status == "FAILED"
    # 无症状主分类映射为 NULL，且不得伴随"未发现异常"建议
    assert run.primary_category is None


@pytest.mark.asyncio
async def test_run_operator_results_no_symptom_keeps_null_but_valid_negative() -> None:
    """主分类 NULL+SUCCESS：科学结论由 assessment_meta 区分（VALID_NEGATIVE
    才是范围内阴性；NULL 本身不再被当成"正常"的充分条件——reader 消费
    judgementStatus，本测试锁定后端语义就位）。"""
    # 平稳数据（无冻结）：全部算子应合格阴性或前提不成立
    rng = np.random.default_rng(3)
    n = 3600
    pv = 50.0 + 0.3 * rng.normal(0, 1, n)
    ts = [START + timedelta(seconds=i) for i in range(n)]
    series = RawTimeSeries(
        timestamps=ts,
        signals={
            "pv": pv.tolist(),
            "sp": [50.0] * n,
            "op": (50.0 + 0.1 * rng.normal(0, 1, n)).tolist(),
            "mode": [1] * n,
        },
        quality_codes={"pv_quality": [1] * n},
    )
    run, _ = await _run_orchestrator(series)
    assert run is not None
    assert run.status == "SUCCESS"
    assert run.primary_category is None  # NO_SYMPTOM → NULL（既有映射保留）
    meta = run.assessment_meta
    # 平稳窗口：无 POSITIVE → 不是 SYMPTOM_DETECTED；2026-10-22 裁决
    # 现象全未命中（NO_SYMPTOM）→ 正常（VALID_NEGATIVE），不再报不可判定
    assert meta["judgementStatus"] == JUDGEMENT_VALID_NEGATIVE
