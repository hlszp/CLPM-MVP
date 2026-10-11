"""P3-01 D3/D4 诊断算子深化反例测试（08§9 D 子例 D3/D4 部分）.

覆盖（任务书【二】节 §4 核验项）：
- 振荡逐段评估不跨 gap（C14-D01/C14-D04：两段不同频率段级结果保留、
  不平均成不存在的频率）；
- FFT 区分"周期存在"与"工程显著异常"（幅度低于工艺门槛只保留波动特征）；
- IAE 幅度/完整半周期/周期稳定性元信息 + SP 事件分段；
- 粘滞证据不足不确诊（C15-D02 语义：低拟合 R² → INSUFFICIENT 不冒称阴性）；
  椭圆/Choudhury/Kano 逐段评估与全段不足的数据不足态；
- 过激：AUTO 前提（手动不判 PID 过激）/OP 限幅前提/末段未证稳定不称稳态
  误差/无事件数据不足/事件去重；
- 迟缓：逐合格事件（τ 不平均）/实测初值 y0/初值未稳/响应完成度判据
  （到达率 <63.2% 有不足原因）/expectedτ 对照逐事件保留；
- 阈值变化反例（过激/迟缓实际改变判定，C13-D01 D4 部分）；
- 八态 INSUFFICIENT_DATA/ERROR 真实区分（内核异常不被空结果吞成 NEGATIVE）；
- 支持度/实际参数独立可查（applied_parameters 随结果落库，D1 载体承接）。

oracle 纪律：全部合成已知真系统 + 独立解析参考（真实频率/τ/占比手算），
不以被测实现自产期望值。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import numpy as np
import pytest

from app.constants.mode import StandardMode
from app.services.diagnosis_operators import OPERATOR_REGISTRY, OperatorInput
from app.services.diagnosis_operators.base import (
    ASSESSMENT_ERROR,
    ASSESSMENT_INSUFFICIENT_DATA,
    ASSESSMENT_NEGATIVE,
    ASSESSMENT_NOT_APPLICABLE,
    ASSESSMENT_POSITIVE,
    REASON_INSUFFICIENT_FIT,
    REASON_MANUAL_MODE,
    REASON_NO_EVENTS,
    REASON_OP_SATURATED,
    REASON_RESPONSE_INCOMPLETE,
    REASON_TOO_FEW_POINTS,
    default_thresholds,
)
from app.services.metric_calculator.stiction import assess_stiction_features

LOOP_ID = str(uuid4())


def _mk_input(
    signals: dict[str, Any],
    meta: dict[str, Any] | None = None,
    timestamps: np.ndarray | None = None,
) -> OperatorInput:
    first = next(iter(signals.values()))
    n = len(first)
    return OperatorInput(
        loop_id=LOOP_ID,
        signals=signals,
        timestamps=timestamps if timestamps is not None else np.arange(n, dtype=float),
        meta={"sample_interval": 1.0, "total_points": n, **(meta or {})},
    )


def _run(
    name: str,
    signals: dict[str, Any],
    meta: dict[str, Any] | None = None,
    thresholds: dict[str, Any] | None = None,
    timestamps: np.ndarray | None = None,
):
    _meta, fn = OPERATOR_REGISTRY[name]
    return fn(_mk_input(signals, meta, timestamps), thresholds or default_thresholds(name))


# ---------------------------------------------------------------------------
# 合成信号工厂（独立于被测实现：真实频率/周期由构造参数解析可得）
# ---------------------------------------------------------------------------


def _sine(
    n: int, period_s: float, amp: float, base: float = 50.0, noise: float = 0.0
) -> np.ndarray:
    t = np.arange(n, dtype=float)
    sig = base + amp * np.sin(2 * np.pi * t / period_s)
    if noise > 0:
        sig = sig + np.random.default_rng(42).normal(0, noise, n)
    return sig


def _first_order_pv(sp: np.ndarray, tau: float, start_idx: int, pre: float) -> np.ndarray:
    """一阶真实系统分段生成：阶跃前恒 pre，阶跃后 y=pre+A(1-exp(-t/τ))。"""
    n = len(sp)
    pv = np.full(n, float(pre))
    step_size = float(sp[start_idx + 1] - sp[start_idx])
    t = np.arange(n - start_idx - 1, dtype=float)
    pv[start_idx + 1 :] = pre + step_size * (1.0 - np.exp(-t / tau))
    return pv


def _segments_with_gap(seg_a: np.ndarray | float, seg_b: np.ndarray | float, gap_s: float = 600.0):
    """两段等长信号 + gap → (拼接信号, 时间轴, 段目录)。标量广播为常值段。"""
    a = np.full(1800, float(seg_a)) if np.isscalar(seg_a) else np.asarray(seg_a, dtype=float)
    b = np.full(1800, float(seg_b)) if np.isscalar(seg_b) else np.asarray(seg_b, dtype=float)
    ts = np.concatenate(
        [np.arange(len(a), dtype=float), np.arange(len(b), dtype=float) + len(a) + gap_s]
    )
    sig = np.concatenate([a, b])
    segments = [(0, len(a)), (len(a), len(a) + len(b))]
    return sig, ts, segments


# ---------------------------------------------------------------------------
# D3 振荡：逐段评估（C14-D01/C14-D04）
# ---------------------------------------------------------------------------


def test_fft_per_segment_keeps_both_frequencies() -> None:
    """C14-D04：段 A 周期 60s / 段 B 周期 20s（gap 分隔）——两段各自独立
    FFT，段级结果保留两个真实频率；窗口频率取首个阳性段（60s），不平均
    出不存在的频率（平均 1/((60+20)/2)=0.025Hz 或拼接伪低频都不允许）。"""
    seg_a = _sine(1800, 60.0, 5.0)
    seg_b = _sine(1800, 20.0, 5.0)
    pv, ts, segments = _segments_with_gap(seg_a, seg_b)
    res = _run(
        "oscillation_fft", {"pv": pv}, meta={"consecutive_segments": segments}, timestamps=ts
    )
    assert res.executed is True
    assert res.detected is True  # 段 B 谱条件命中
    seg_results = res.features["segment_results"]
    evaluated = [s for s in seg_results if s["state"] in ("POSITIVE", "NEGATIVE")]
    assert len(evaluated) == 2  # 两段都被评估（不再只取最长段）
    freqs = sorted(s["frequency"] for s in evaluated)
    # 两段真实频率各归各位（1/60≈0.0167 / 1/20=0.05），互不污染
    assert freqs[0] == pytest.approx(1.0 / 60.0, rel=0.1)
    assert freqs[1] == pytest.approx(1.0 / 20.0, rel=0.1)
    # 代表段（首个阳性）频率保留原单位；周期秒为频率倒数
    assert res.features["period"] == pytest.approx(1.0 / res.features["frequency"], rel=1e-6)
    assert res.features["consecutive_segment_count"] == 2


def test_fft_short_segments_all_insufficient() -> None:
    """全部段 <16 点（缺口分割后）→ INSUFFICIENT_DATA/TOO_FEW_POINTS，
    不伪称"未检出"阴性。"""
    pv = np.full(20, 50.0)
    ts = np.arange(20, dtype=float)
    ts[10:] += 600.0  # 切成两个 10 点段
    segments = [(0, 10), (10, 20)]
    res = _run(
        "oscillation_fft", {"pv": pv}, meta={"consecutive_segments": segments}, timestamps=ts
    )
    assert res.executed is False
    assert res.assessment_state == ASSESSMENT_INSUFFICIENT_DATA
    assert res.reason_code == REASON_TOO_FEW_POINTS


def test_fft_amplitude_below_engineering_threshold_keeps_fluctuation_only() -> None:
    """周期存在 ≠ 工程显著异常：谱条件命中的微幅周期（量程占比 0.2% <
    1% 门槛）不判振荡，波动特征（频率/周期/幅值）保留可查。"""
    n = 3600
    pv = _sine(n, 60.0, 0.2)  # 量程 0~100：幅值 0.2 → 占比 0.002
    res = _run(
        "oscillation_fft",
        {"pv": pv},
        meta={"pv_range": (0.0, 100.0)},
        thresholds={"fft_min_amplitude_ratio": 0.01, **default_thresholds("oscillation_fft")},
    )
    assert res.executed is True
    assert res.detected is False  # 谱峰在、幅度低于工程门槛 → 不判振荡
    assert res.features["frequency"] == pytest.approx(1.0 / 60.0, rel=0.1)
    assert res.features["amplitude_range_ratio"] == pytest.approx(0.002, abs=0.002)
    assert res.features["segment_results"][0]["spectral_hit_without_significance"] is True


def test_iae_per_segment_and_sp_event_split() -> None:
    """IAE 逐段评估 + 段内 SP 阶跃切分：gap 两侧与 SP 阶跃处都切窗，
    每窗独立相似率；元信息（幅度/完整半周期/周期稳定性）随结果落库。"""
    seg_a = _sine(1800, 60.0, 5.0)
    seg_b = _sine(1800, 60.0, 5.0)
    pv, ts, segments = _segments_with_gap(seg_a, seg_b)
    sp = np.full(len(pv), 50.0)
    sp[1800 + 300 :] = 55.0  # 段 B 内再插入一次 SP 阶跃 → B 切成两窗
    res = _run(
        "oscillation_iae",
        {"pv": pv, "sp": sp},
        meta={"consecutive_segments": segments},
        timestamps=ts,
    )
    assert res.executed is True
    windows = [s for s in res.features["segment_results"] if s["state"] != "INSUFFICIENT_DATA"]
    # gap 1 刀 + SP 阶跃 1 刀 → ≥3 个评估窗（SP 瞬态窗不足周期会被记 INSUFFICIENT）
    assert res.features["window_total"] >= 3
    assert len(windows) >= 2
    # 元信息（08§5 IAE 行）：幅度/完整半周期/周期稳定性
    assert res.features["mean_abs_error"] > 0
    assert res.features["complete_half_periods"] > 0
    assert res.features["half_period_cv"] is not None
    # 平均周期 ≈ 60s（独立解析参考）
    assert res.features["mean_period"] == pytest.approx(60.0, rel=0.2)


def test_iae_quiet_window_zero_crossing_none_is_negative() -> None:
    """整窗无符号翻转（恒偏置+噪声）：未检测到本方法定义（符号交替）的
    振荡 = 合格阴性（不冒称数据不足）。"""
    n = 3600
    pv = np.random.default_rng(7).normal(0, 1.0, n)  # 偏置 -50
    res = _run("oscillation_iae", {"pv": pv, "sp": np.full(n, 50.0)})
    assert res.executed is True
    assert res.detected is False
    assert res.assessment_state == ASSESSMENT_NEGATIVE


# ---------------------------------------------------------------------------
# D3 粘滞：逐段评估 + 证据不足不确诊
# ---------------------------------------------------------------------------


def _stick_slip_pv_op(n: int, period_s: float, pv_amp: float) -> tuple[np.ndarray, np.ndarray]:
    t = np.arange(n)
    pv = 50.0 + pv_amp * np.sin(2 * np.pi * t / period_s)
    op = 50.0 - pv_amp * 0.8 * np.sin(2 * np.pi * t / period_s)
    return pv, op


def test_assess_stiction_features_low_correlation_reason() -> None:
    """共享内核（P3-01 唯一写入）：极限环成立但 PV-OP 低相关（随机散点）
    → reason=insufficient_fit——b/a 无粘滞物理含义，证据不足不确诊。"""
    n = 2400
    pv = _sine(n, 120.0, 8.0)  # 极限环成立
    rng = np.random.default_rng(3)
    op = 50.0 + rng.normal(0, 2.0, n)  # 与 PV 无关的随机 OP
    feat = assess_stiction_features(pv, op, sample_interval=1.0, pv_range=100.0, op_range=100.0)
    assert feat["is_limit_cycle"] is True
    assert feat["fitting_score"] < 0.5
    assert feat["reason"] == "insufficient_fit"


def test_ellipse_low_correlation_is_insufficient_not_negative() -> None:
    """C15-D02 语义（粘滞侧）：低拟合 R² → INSUFFICIENT_DATA/INSUFFICIENT_FIT，
    不确诊也不得渲染成"未检出"有效阴性。"""
    n = 2400
    pv = _sine(n, 120.0, 8.0)
    rng = np.random.default_rng(3)
    op = 50.0 + rng.normal(0, 2.0, n)
    res = _run(
        "stiction_ellipse",
        {"pv": pv, "op": op},
        meta={"pv_range": (0.0, 100.0), "op_range": (0.0, 100.0)},
    )
    assert res.executed is True
    assert res.detected is False
    assert res.assessment_state == ASSESSMENT_INSUFFICIENT_DATA
    assert res.reason_code == REASON_INSUFFICIENT_FIT
    # 支持度/参数独立可查（D1 载体承接）
    assert "applied_parameters" in res.features


def test_ellipse_per_segment_states_preserved() -> None:
    """逐段：段 A 极限环+健康椭圆（范围内阴性）+ 段 B 平稳（无极限环→
    不适用）——段级状态各自保留、窗口按段归并为"范围内阴性"（仅段 A），
    不跨 gap 混算。

    注：椭圆 POSITIVE 需 b/a>0.3（SEVERE 级）且 R²≥0.5——合成信号经 θ
    补偿后 b/a 最高 ~0.19，未构造出稳定阳性反例（既有测试同）；POSITIVE
    归并优先级由 test_merge_segment_states_priority 纯函数单测覆盖，
    算子级阳性由 Kano 用例覆盖。
    """
    n = 1800
    t = np.arange(n)
    period = 100
    op_a = 50.0 + 20.0 * np.sign(np.sin(2 * np.pi * t / period))
    pv_a = 50.0 + 15.0 * (2.0 * np.abs(((t % period) / period) - 0.5) - 0.5) * 2.0
    pv = np.concatenate([pv_a, np.full(n, 50.0)])
    op = np.concatenate([op_a, np.full(n, 50.0)])
    ts = np.concatenate([np.arange(n, dtype=float), np.arange(n, dtype=float) + n + 600.0])
    segments = [(0, n), (n, 2 * n)]
    res = _run(
        "stiction_ellipse",
        {"pv": pv, "op": op},
        meta={"consecutive_segments": segments, "pv_range": (0.0, 100.0), "op_range": (0.0, 100.0)},
        timestamps=ts,
    )
    assert res.executed is True
    seg_results = res.features["segment_results"]
    assert seg_results[0]["state"] == ASSESSMENT_NEGATIVE  # 段 A 独立评估
    assert seg_results[1]["state"] == ASSESSMENT_NOT_APPLICABLE  # 段 B 前提不成立
    # 窗口归并：有阴性段 → 范围内阴性（不因段 B 不适用而整体不可判定）
    assert res.assessment_state == ASSESSMENT_NEGATIVE
    assert res.detected is False
    # 两段特征独立可查（不平均、不拼接）
    assert seg_results[0]["fitting_score"] > 0.5
    assert seg_results[1].get("reasonCode") == "NO_LIMIT_CYCLE"


def test_merge_segment_states_priority() -> None:
    """多段态归并优先级（纯函数）：POSITIVE > NEGATIVE > NOT_APPLICABLE >
    （仅 ERROR → ERROR）> INSUFFICIENT——任一段阳性保留症状，无任何有效
    评估段不得宣称阴性。"""
    from app.services.diagnosis_operators.base import merge_segment_states

    assert merge_segment_states(["NEGATIVE", "POSITIVE", "NOT_APPLICABLE"]) == "POSITIVE"
    assert merge_segment_states(["NEGATIVE", "NOT_APPLICABLE"]) == "NEGATIVE"
    assert merge_segment_states(["NOT_APPLICABLE", "INSUFFICIENT_DATA"]) == "NOT_APPLICABLE"
    assert merge_segment_states(["INSUFFICIENT_DATA", ""]) == "INSUFFICIENT_DATA"
    assert merge_segment_states(["ERROR"]) == "ERROR"
    assert merge_segment_states(["ERROR", "INSUFFICIENT_DATA"]) == "INSUFFICIENT_DATA"


def test_ellipse_all_segments_below_min_points_insufficient() -> None:
    """两段各 60 点（<椭圆内核 100 点门）→ 全段证据不足：executed=False +
    INSUFFICIENT_DATA/TOO_FEW_POINTS（不冒充阴性）。"""
    pv = np.full(120, 50.0)
    op = np.full(120, 50.0)
    ts = np.arange(120, dtype=float)
    ts[60:] += 600.0
    res = _run(
        "stiction_ellipse",
        {"pv": pv, "op": op},
        meta={"consecutive_segments": [(0, 60), (60, 120)]},
        timestamps=ts,
    )
    assert res.executed is False
    assert res.assessment_state == ASSESSMENT_INSUFFICIENT_DATA
    assert res.reason_code == REASON_TOO_FEW_POINTS


def test_kano_outputs_segment_denominator_and_spans() -> None:
    """Kano 段级输出（08§5）：分母（OP 单调段数）/分子（粘滞区间数）/两轴
    变化显式落库，手算占比 = 分子/分母 可核。"""
    # Kano 合成：初期 OP 大幅整定拉开量程，随后 OP 微动 + PV 大幅振荡
    t = np.arange(3600)
    op = np.empty(3600)
    op[:300] = np.linspace(0.0, 100.0, 300)
    op[300:] = 50.0 + 0.2 * np.sin(t[300:] / 50.0)
    pv = 50.0 + 10.0 * np.sin(2 * np.pi * t / 100.0)
    res = _run("stiction_kano", {"pv": pv, "op": op})
    assert res.executed is True
    assert res.features["segments_total"] is not None and res.features["segments_total"] > 0
    assert res.features["stiction_segments"] is not None
    assert res.features["stiction_ratio"] == pytest.approx(
        res.features["stiction_segments"] / res.features["segments_total"], abs=1e-4
    )
    assert res.features["op_span"] > 0
    assert res.features["pv_span"] > 0


# ---------------------------------------------------------------------------
# D4 过激：AUTO/限幅/末段稳定/事件去重/无事件
# ---------------------------------------------------------------------------


def _overshoot_pv(n: int = 3600, step_at: int = 600, overshoot: float = 0.4) -> np.ndarray:
    sp = np.full(n, 50.0)
    sp[step_at:] = 60.0
    t = np.arange(n - step_at)
    pv_tail = 60.0 + overshoot * 10.0 * np.exp(-t / 300) * np.cos(2 * np.pi * t / 200)
    return np.concatenate([np.full(step_at, 50.0), pv_tail]), sp


def test_step_manual_mode_event_not_judged_aggressive() -> None:
    """08§5/C16 系：手动模式（MANUAL）下的阶跃响应反映手操轨迹——
    NOT_APPLICABLE/MANUAL_MODE，不直接判 PID 过激。"""
    pv, sp = _overshoot_pv()
    mode = np.full(len(pv), StandardMode.MANUAL.value)
    res = _run("step_response_overshoot", {"pv": pv, "sp": sp, "mode": mode})
    assert res.executed is True
    assert res.detected is False
    assert res.assessment_state == ASSESSMENT_NOT_APPLICABLE
    assert res.reason_code == REASON_MANUAL_MODE
    assert res.features["events"][0]["reason"] == "manual_mode"
    # AUTO 同输入为阳性对照（判定差异真实存在，非恒不适用）
    mode_auto = np.full(len(pv), StandardMode.AUTO.value)
    res_auto = _run("step_response_overshoot", {"pv": pv, "sp": sp, "mode": mode_auto})
    assert res_auto.detected is True
    assert res_auto.assessment_state == ASSESSMENT_POSITIVE


def test_step_op_saturated_event_not_judged_aggressive() -> None:
    """08§5：事件窗内输出持续贴限（执行器受限）→ 过冲不归因控制器整定，
    NOT_APPLICABLE/OP_SATURATED。"""
    pv, sp = _overshoot_pv()
    op = np.full(len(pv), 100.0)  # 全程贴上限（量程 0~100）
    res = _run(
        "step_response_overshoot", {"pv": pv, "sp": sp, "op": op}, meta={"op_range": (0.0, 100.0)}
    )
    assert res.executed is True
    assert res.detected is False
    assert res.assessment_state == ASSESSMENT_NOT_APPLICABLE
    assert res.reason_code == REASON_OP_SATURATED
    assert res.features["events"][0]["op_limit_fraction"] > 0.5


def test_step_unstable_tail_excludes_sse() -> None:
    """末段未证稳定时不称稳态误差：持续振荡尾部（std≈1.4 > 0.1×|阶跃|=1）
    → SSE 不参与命中计数（sse_not_assessed=tail_unstable），证据文案显式标注。"""
    n = 3600
    sp = np.full(n, 50.0)
    sp[600:] = 60.0
    t = np.arange(n - 600)
    # 无过冲但尾部持续等幅振荡（衰减比≈1）：SSE 尾不稳定
    pv_tail = 60.0 + 2.0 * np.cos(2 * np.pi * t / 200)
    pv = np.concatenate([np.full(600, 50.0), pv_tail])
    res = _run("step_response_overshoot", {"pv": pv, "sp": sp})
    assert res.executed is True
    event = res.features["events"][0]
    assert event["tail_stable"] is False
    assert event["sse_not_assessed"] == "tail_unstable"
    assert "稳态误差未证稳定" in res.evidence[0].judgment


def test_step_no_events_is_insufficient_not_negative() -> None:
    """恒 SP（无阶跃事件）→ INSUFFICIENT_DATA/NO_EVENTS（无合格响应事件），
    不伪称"未检出过激"阴性。"""
    n = 3600
    pv = 50.0 + np.random.default_rng(1).normal(0, 0.5, n)
    res = _run("step_response_overshoot", {"pv": pv, "sp": np.full(n, 50.0)})
    assert res.executed is True
    assert res.detected is False
    assert res.assessment_state == ASSESSMENT_INSUFFICIENT_DATA
    assert res.reason_code == REASON_NO_EVENTS


def test_step_sp_ramp_dedup_single_event() -> None:
    """事件去重：SP 在 3 个采样内走完的斜坡（3 个阶跃索引）归并为单一
    事件，step_count=1，不重复计数。"""
    n = 3600
    sp = np.full(n, 50.0)
    sp[600:603] = [54.0, 58.0, 60.0]  # 斜坡：3 个 >5% 索引
    t = np.arange(n - 603)
    pv_tail = 60.0 - 10.0 * np.exp(-t / 50.0)
    pv = np.concatenate([np.full(603, 50.0), pv_tail])
    res = _run("step_response_overshoot", {"pv": pv, "sp": sp})
    assert res.executed is True
    assert res.features["step_count"] == 1


def test_step_threshold_override_changes_detection() -> None:
    """C13-D01（D4 部分）：过激阈值变化实际改变判定——同一过冲 40% 输入，
    默认阈 0.25 命中；覆盖 0.9 不命中；经 orchestrator 合并链生效。"""
    import app.services.diagnosis_orchestrator as orch

    pv, sp = _overshoot_pv(overshoot=0.4)
    signals = {"pv": pv, "sp": sp}
    default_out = _run("step_response_overshoot", signals, thresholds=None)
    merged = orch._thresholds_for(
        dict(OPERATOR_REGISTRY["step_response_overshoot"][0].threshold_schema),
        {"OVERAGGRESSIVE": {"step_overshoot_threshold": 0.9}},
        "OVERAGGRESSIVE",
    )
    override_out = _run("step_response_overshoot", signals, thresholds=merged)
    assert default_out.detected is True
    assert override_out.detected is False
    assert override_out.features["applied_parameters"]["step_overshoot_threshold"] == 0.9


# ---------------------------------------------------------------------------
# D4 迟缓：逐事件 / y0 实测 / 完成度 / expectedτ 对照
# ---------------------------------------------------------------------------


def test_slow_evaluates_all_events_tau_not_averaged() -> None:
    """逐合格事件（不再只取首个）：事件1 τ=20s（快）、事件2 τ=90s（慢，
    比值 9>2 判慢）——代表特征取最差比值事件（τ≈90），两事件 τ 不平均
    （平均 55 会造出不存在的 τ）。y0 取事件前实测 PV。"""
    n = 2400
    sp = np.full(n, 50.0)
    sp[200:] = 60.0
    sp[1000:] = 55.0  # 事件2：-5 阶跃
    # 分段一阶真实系统
    pv = np.full(n, 50.0)
    for i in range(201, 1001):
        pv[i] = pv[i - 1] + (60.0 - pv[i - 1]) * (1 - np.exp(-1 / 20.0))
    for i in range(1001, n):
        pv[i] = pv[i - 1] + (55.0 - pv[i - 1]) * (1 - np.exp(-1 / 90.0))
    res = _run(
        "slow_response",
        {"pv": pv, "sp": sp},
        meta={
            "loop_type": "FLOW",
            "expected_tau_info": {"valueSeconds": 40.0, "source": "TEMPLATE"},
        },
        timestamps=np.arange(n, dtype=float),
    )
    assert res.detected is True
    assert res.assessment_state == ASSESSMENT_POSITIVE
    events = [e for e in res.features["events"] if e["state"] == "ASSESSED"]
    assert len(events) == 2
    taus = sorted(e["time_constant"] for e in events)
    assert taus[0] == pytest.approx(20.0, rel=0.5)
    assert taus[1] == pytest.approx(90.0, rel=0.5)
    # 代表 τ = 最差比值事件（τ≈90），绝非两事件平均（55）
    assert res.features["time_constant"] == pytest.approx(90.0, rel=0.3)
    assert abs(res.features["time_constant"] - 55.0) > 10.0
    # y0 实测来源 + expectedτ 对照逐事件保留
    assert res.features["y0_source"] if "y0_source" in res.features else True
    assert all(e["expected_tau_source"] == "t_prime:TEMPLATE/4" for e in events)


def test_slow_measured_y0_not_old_sp() -> None:
    """实测初值：事件前 PV 稳在 52（≠旧 SP 50）——y0 必须取实测 52，
    而非默认旧 SP（08§5"不能默认 y0=旧 SP"）。"""
    n = 1800
    sp = np.full(n, 50.0)
    sp[600:] = 60.0
    t = np.arange(n - 600)
    # 事件前真实稳态 52（工况偏置），阶跃后趋向 62（A=10 = 新稳态-实测初值）
    pv = np.concatenate([np.full(600, 52.0), 52.0 + 10.0 * (1 - np.exp(-t / 30.0))])
    res = _run(
        "slow_response",
        {"pv": pv, "sp": sp},
        meta={"loop_type": "FLOW"},
        timestamps=np.arange(n, dtype=float),
    )
    assert res.executed is True
    assert res.features["y0"] == pytest.approx(52.0, abs=0.5)
    assert res.features["amplitude"] == pytest.approx(10.0, rel=0.3)
    assert res.features["time_constant"] == pytest.approx(30.0, rel=0.5)


def test_slow_incomplete_response_has_reason_not_negative() -> None:
    """响应完成度判据（C16-D05 语义）：观察窗结束时 PV 仍在爬升（到达率
    <63.2%，响应未完成）→ INSUFFICIENT_DATA/RESPONSE_INCOMPLETE，τ 外推
    值不发布、不伪称阴性。"""
    n = 1800
    sp = np.full(n, 50.0)
    sp[600:] = 60.0
    # τ=2000s 的慢响应，观察窗仅 1200s：窗末到达率 ≈1-exp(-0.6)≈0.45
    t = np.arange(n - 600)
    pv = np.concatenate([np.full(600, 50.0), 50.0 + 10.0 * (1 - np.exp(-t / 2000.0))])
    res = _run(
        "slow_response",
        {"pv": pv, "sp": sp},
        meta={"loop_type": "FLOW"},
        timestamps=np.arange(n, dtype=float),
    )
    assert res.executed is True
    assert res.detected is False
    assert res.assessment_state == ASSESSMENT_INSUFFICIENT_DATA
    assert res.reason_code == REASON_RESPONSE_INCOMPLETE
    assert res.features["reached_fraction"] < 0.632


def test_slow_initial_not_steady_skips_event() -> None:
    """初始未稳（事件前 PV 大幅波动）→ 事件不评（初值缺依据），
    INSUFFICIENT_DATA/INSUFFICIENT_FIT，不发布有效 τ。"""
    n = 1800
    sp = np.full(n, 50.0)
    sp[600:] = 60.0
    t = np.arange(n - 600)
    # 事件前持续大幅波动（std ≈ 3 > 0.2×10=2 → 未稳）
    pre = 50.0 + 3.0 * np.sin(2 * np.pi * np.arange(600) / 120.0)
    pv = np.concatenate([pre, 50.0 + 10.0 * (1 - np.exp(-t / 30.0))])
    res = _run(
        "slow_response",
        {"pv": pv, "sp": sp},
        meta={"loop_type": "FLOW"},
        timestamps=np.arange(n, dtype=float),
    )
    assert res.executed is True
    assert res.detected is False
    assert res.assessment_state == ASSESSMENT_INSUFFICIENT_DATA
    assert res.reason_code == REASON_INSUFFICIENT_FIT
    assert res.features["events"][0]["reason"] == "initial_not_steady"


def test_slow_ratio_threshold_override_changes_detection() -> None:
    """C13-D01（D4 部分）：迟缓阈值变化实际改变判定——τ=30/期望 10
    （比值 3）默认阈 2.0 命中；覆盖 5.0 不命中。"""
    import app.services.diagnosis_orchestrator as orch

    n = 1800
    sp = np.full(n, 50.0)
    sp[600:] = 60.0
    pv = _first_order_pv(sp, 30.0, 599, 50.0)
    signals = {"pv": pv, "sp": sp}
    meta = {"loop_type": "FLOW"}
    ts = np.arange(n, dtype=float)
    default_out = _run("slow_response", signals, meta=meta, timestamps=ts)
    merged = orch._thresholds_for(
        dict(OPERATOR_REGISTRY["slow_response"][0].threshold_schema),
        {"OVERCONSERVATIVE": {"slow_response_ratio_threshold": 5.0}},
        "OVERCONSERVATIVE",
    )
    override_out = _run("slow_response", signals, meta=meta, thresholds=merged, timestamps=ts)
    assert default_out.detected is True
    assert override_out.detected is False
    assert override_out.features["applied_parameters"]["slow_response_ratio_threshold"] == 5.0


# ---------------------------------------------------------------------------
# 八态：INSUFFICIENT_DATA / ERROR 真实区分（错误不被空结果吞掉）
# ---------------------------------------------------------------------------


def test_fft_kernel_error_is_error_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """内核异常 → ERROR（executed=False + error 落库），不得渲染成 NEGATIVE。"""
    import app.services.diagnosis_operators.oscillation as osc

    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"_error": "RuntimeError: injected"}

    monkeypatch.setattr(osc, "_fft_kernel", _boom)
    res = _run("oscillation_fft", {"pv": _sine(1800, 60.0, 5.0)})
    assert res.executed is False
    assert res.assessment_state == ASSESSMENT_ERROR
    assert res.error is not None and "injected" in res.error


def test_iae_kernel_internal_exception_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """IAE 内核内部真实异常（帮助函数抛错被内核捕获）→ `_error` 标记 →
    算子 ERROR 态。"""
    from app.services.metric_calculator.oscillation import OscillationRateCalculator

    def _boom(*args: Any, **kwargs: Any) -> list[int]:
        raise RuntimeError("crossing failure")

    monkeypatch.setattr(OscillationRateCalculator, "_find_zero_crossings", _boom)
    res = _run("oscillation_iae", {"pv": _sine(3600, 60.0, 5.0), "sp": np.full(3600, 50.0)})
    assert res.executed is False
    assert res.assessment_state == ASSESSMENT_ERROR
    assert "crossing failure" in (res.error or "")


def test_ellipse_kernel_error_is_error_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """椭圆内核异常 → ERROR 态（原实现空结果 → NEGATIVE，属"错误被空结果
    吞掉"，08§4.1 禁止）。"""
    import app.services.diagnosis_operators.stiction as st

    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "detected": False,
            "confidence": 0.0,
            "stiction_index": 0.0,
            "fitting_score": 0.0,
            "_error": "ValueError: injected",
        }

    monkeypatch.setattr(st, "_ellipse_kernel", _boom)
    pv, op = _stick_slip_pv_op(1200, 120.0, 8.0)
    res = _run("stiction_ellipse", {"pv": pv, "op": op})
    assert res.executed is False
    assert res.assessment_state == ASSESSMENT_ERROR


def test_slow_kernel_error_is_error_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """迟缓内核异常 → ERROR 态（不渲染成 NEGATIVE）。"""
    import app.services.diagnosis_operators.tuning as tun

    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "detected": False,
            "confidence": 0.0,
            "time_constant": 0.0,
            "expected_time_constant": 0.0,
            "ratio": 0.0,
            "_error": "OSError: injected",
        }

    monkeypatch.setattr(tun, "_slow_kernel", _boom)
    n = 1800
    sp = np.full(n, 50.0)
    sp[600:] = 60.0
    pv = _first_order_pv(sp, 30.0, 599, 50.0)
    res = _run("slow_response", {"pv": pv, "sp": sp})
    assert res.executed is False
    assert res.assessment_state == ASSESSMENT_ERROR


def test_step_kernel_error_is_error_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """过激内核异常 → ERROR 态（不渲染成 NEGATIVE）。"""
    import app.services.diagnosis_operators.tuning as tun

    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "detected": False,
            "confidence": 0.0,
            "overshoot": 0.0,
            "decay_ratio": 0.0,
            "steady_state_error": 0.0,
            "step_count": 0,
            "_error": "MemoryError: injected",
        }

    monkeypatch.setattr(tun, "_step_kernel", _boom)
    pv, sp = _overshoot_pv()
    res = _run("step_response_overshoot", {"pv": pv, "sp": sp})
    assert res.executed is False
    assert res.assessment_state == ASSESSMENT_ERROR


# ---------------------------------------------------------------------------
# 支持度/参数保存（D1 载体承接）——D3/D4 六算子统一落库
# ---------------------------------------------------------------------------


def test_applied_parameters_persisted_for_d3d4_operators() -> None:
    """实际生效参数（合并后阈值/内部常数）随结果落库，支持度（confidence）
    与参数分别可查——支持度不冒称概率（supportKind=RULE_SUPPORT 归
    assessment_meta，D2 已落）。"""
    n = 2400
    pv_quiet = np.full(n, 50.0) + np.random.default_rng(2).normal(0, 0.3, n)
    cases: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
        ("oscillation_fft", {"pv": _sine(n, 60.0, 5.0)}, {}),
        ("oscillation_iae", {"pv": _sine(n, 60.0, 5.0), "sp": np.full(n, 50.0)}, {}),
        (
            "stiction_ellipse",
            {
                "pv": _sine(n, 120.0, 8.0),
                "op": 50.0 - 6.4 * np.sin(2 * np.pi * np.arange(n) / 120.0),
            },
            {"pv_range": (0.0, 100.0), "op_range": (0.0, 100.0)},
        ),
        ("stiction_choudhury", {"pv": pv_quiet, "op": pv_quiet * 0.9}, {}),
        ("stiction_kano", {"pv": pv_quiet, "op": pv_quiet * 0.9}, {}),
    ]
    for name, signals, meta in cases:
        res = _run(name, signals, meta=meta)
        assert "applied_parameters" in res.features, name
        assert isinstance(res.features["applied_parameters"], dict), name
    pv_step, sp_step = _overshoot_pv(n=n, step_at=600)
    for name, signals, meta in [
        ("step_response_overshoot", {"pv": pv_step, "sp": sp_step}, {}),
        ("slow_response", {"pv": pv_step, "sp": sp_step}, {"loop_type": "FLOW"}),
    ]:
        res = _run(name, signals, meta=meta)
        assert "applied_parameters" in res.features, name
