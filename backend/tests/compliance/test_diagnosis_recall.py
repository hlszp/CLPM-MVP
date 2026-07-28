"""Phase 6 GB/T 44693.2 符合性验证 — 诊断故障注入基线（任务 G5-②）.

验证对象：app/tasks/diagnosis_engine.py 各检测器（纯函数级，不跑 Celery 编排）。

方法：构造已知故障数据集（dt=1s, N=3600, seed 固定），逐检测器调用，
标签判定镜像 _diagnose_loop 的 OR 逻辑：
- OSCILLATION          = _detect_oscillation_fft OR _detect_oscillation_iae
- VALVE_STICTION       = _detect_valve_stiction OR _detect_choudhury_nonlinearity
                         OR _detect_kano_stiction
- OUTPUT_SATURATION    = _analyze_saturation（mode 全 AUTO=1）
- EXTERNAL_DISTURBANCE = _detect_bias_shift
- QUALITY_ABNORMAL     = _analyze_quality

每类注入 ≥2 例 + 正常对照 2 例；召回率=检出/注入，误报率=误报/正常对照。
基线断言：召回率 ≥80%、误报率 ≤20%；达不到的用例 xfail 并记录实测值
（Phase 6 基线数据，非整改任务）。

Phase 6 基线实测摘要（seed=7, 2026-07-28）：
- 召回率：5 类标签全部 2/2 = 100%（达标）
- 误报率：OUTPUT_SATURATION 0/2、QUALITY_ABNORMAL 0/2（达标）；
  OSCILLATION 2/2、VALVE_STICTION 2/2、EXTERNAL_DISTURBANCE 2/2（不达标，xfail）：
  * NORM1（纯白噪声 PV + 恒定 SP）：IAE 零交叉法 similarity=0.962 误报振荡；
    CUSUM 在白噪声上 shift_count=10（10 次/小时 > 5 阈值）误报外扰；
    Choudhury 对正弦 OP NGI=0.240 > 0.001 且 NLI=1.0 > 0.01 误报粘滞
  * NORM2（慢漂 PV）：FFT 噪声过零致 zero_crossings>5 且 osc_index=0.661 误报振荡；
    CUSUM shift_count=277 误报外扰；Choudhury 同样误报粘滞
  根因：NGI 阈值 0.001 对任何非高斯 OP（正弦 excess kurtosis=-1.5 → NGI≈0.25）
  必然击穿；CUSUM 频率判据对噪声/振荡缺乏区分度；IAE 相似率对白噪声不免疫。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.tasks.diagnosis_engine import (
    _analyze_quality,
    _analyze_saturation,
    _detect_bias_shift,
    _detect_choudhury_nonlinearity,
    _detect_kano_stiction,
    _detect_oscillation_fft,
    _detect_oscillation_iae,
    _detect_valve_stiction,
)

N = 3600
_T = np.arange(N, dtype=float)
_SEED = 7


def _rng() -> np.random.Generator:
    return np.random.default_rng(_SEED)


# ---------------------------------------------------------------------------
# 故障注入数据集构造
# ---------------------------------------------------------------------------


def _osc_case_1():
    """正弦振荡 1：周期 60s，幅值 3，小噪声."""
    rng = _rng()
    pv = 50.0 + 3.0 * np.sin(2 * np.pi * _T / 60.0) + 0.1 * rng.standard_normal(N)
    sp = np.full(N, 50.0)
    op = 50.0 + 2.7 * np.sin(2 * np.pi * _T / 60.0 + 0.5)
    return pv, sp, op


def _osc_case_2():
    """正弦振荡 2：周期 120s，幅值 5."""
    rng = _rng()
    pv = 100.0 + 5.0 * np.sin(2 * np.pi * _T / 120.0) + 0.2 * rng.standard_normal(N)
    sp = np.full(N, 100.0)
    op = 60.0 + 4.0 * np.sin(2 * np.pi * _T / 120.0)
    return pv, sp, op


def _stiction_case(stick_band: float, amp: float, period: float):
    """粘滞卡涩（stick-slip）：OP 指令正弦，阀粘带 S 内不动、越带跳变；
    PV 为一阶惯性响应（K=1, τ=30）。"""
    rng = _rng()
    op_cmd = 50.0 + amp * np.sin(2 * np.pi * _T / period)
    op = np.zeros(N)
    pv = np.zeros(N)
    for i in range(1, N):
        if abs(op_cmd[i] - op[i - 1]) > stick_band:
            op[i] = op_cmd[i] - np.sign(op_cmd[i] - op[i - 1]) * stick_band * 0.5
        else:
            op[i] = op[i - 1]
        pv[i] = pv[i - 1] + (op[i - 1] - pv[i - 1]) / 30.0
    return pv + 0.05 * rng.standard_normal(N), np.full(N, 50.0), op


def _sat_case_high():
    """OP 顶限饱和：前 60% 时间 OP=100（AUTO 模式）."""
    op = np.full(N, 100.0)
    op[2160:] = 50.0 + 5.0 * np.sin(2 * np.pi * _T[2160:] / 300.0)
    rng = _rng()
    return 50.0 + 0.3 * rng.standard_normal(N), np.full(N, 50.0), op


def _sat_case_low():
    """OP 底限饱和：前 50% 时间 OP=0（AUTO 模式）."""
    op = np.zeros(N)
    op[1800:] = 40.0 + 5.0 * np.sin(2 * np.pi * _T[1800:] / 300.0)
    rng = _rng()
    return 50.0 + 0.3 * rng.standard_normal(N), np.full(N, 50.0), op


def _dist_case_1():
    """阶跃外扰 1：方波 ±4，周期 800s → 8 次突变/小时（>5 阈值）."""
    rng = _rng()
    pv = 50.0 + 4.0 * np.sign(np.sin(2 * np.pi * _T / 800.0)) + 0.2 * rng.standard_normal(N)
    sp = np.full(N, 50.0)
    op = 50.0 + 4.0 * np.sign(np.sin(2 * np.pi * _T / 800.0 - 0.3))
    return pv, sp, op


def _dist_case_2():
    """阶跃外扰 2：方波 ±3，周期 1200s → 6 次突变/小时."""
    rng = _rng()
    pv = 50.0 + 3.0 * np.sign(np.sin(2 * np.pi * _T / 1200.0)) + 0.2 * rng.standard_normal(N)
    sp = np.full(N, 50.0)
    op = 50.0 + 3.0 * np.sign(np.sin(2 * np.pi * _T / 1200.0 - 0.3))
    return pv, sp, op


def _quality_case_1() -> list[dict[str, str]]:
    """Bad 质量码 1：连续 20 点 Bad → 期望 Q001."""
    q: list[dict[str, str]] = [{"quality": "GOOD"} for _ in range(N)]
    for i in range(1000, 1020):
        q[i] = {"quality": "BAD"}
    return q


def _quality_case_2() -> list[dict[str, str]]:
    """Bad 质量码 2：每 8 点 1 个 Bad（12.5%，无连续段）→ 期望 Q002."""
    return [{"quality": "BAD" if i % 8 == 0 else "GOOD"} for i in range(N)]


def _normal_case_1():
    """正常对照 1：PV=SP+白噪声，OP 缓变正弦（2<OP<98），质量码全 Good."""
    rng = _rng()
    pv = 50.0 + 0.3 * rng.standard_normal(N)
    sp = np.full(N, 50.0)
    op = 50.0 + 5.0 * np.sin(2 * np.pi * _T / 600.0) + 0.5 * rng.standard_normal(N)
    return pv, sp, op


def _normal_case_2():
    """正常对照 2：PV 慢漂（周期 3000s 幅 0.5）+小噪声，OP 缓变（37≤OP≤53）."""
    rng = _rng()
    pv = 50.0 + 0.5 * np.sin(2 * np.pi * _T / 3000.0) + 0.1 * rng.standard_normal(N)
    sp = np.full(N, 50.0)
    op = 45.0 + 8.0 * np.sin(2 * np.pi * _T / 900.0) + 0.5 * rng.standard_normal(N)
    return pv, sp, op


# ---------------------------------------------------------------------------
# 检测器编排（镜像 _diagnose_loop 的标签 OR 逻辑，mode 全 AUTO=1）
# ---------------------------------------------------------------------------


def _run_detectors(pv, sp, op, quality=None) -> dict[str, bool]:
    mode = np.ones(N)  # StandardMode AUTO=1
    fft = _detect_oscillation_fft(pv, 1.0)
    iae = _detect_oscillation_iae(pv, sp, 1.0)
    stic = _detect_valve_stiction(pv, op)
    chou = _detect_choudhury_nonlinearity(pv, op)
    kano = _detect_kano_stiction(pv, op)
    sat = _analyze_saturation(op, mode)
    dist = _detect_bias_shift(pv, sp, _T)
    out = {
        "OSCILLATION": bool(fft["detected"] or iae["detected"]),
        "VALVE_STICTION": bool(stic["detected"] or chou["detected"] or kano["detected"]),
        "OUTPUT_SATURATION": bool(sat["detected"]),
        "EXTERNAL_DISTURBANCE": bool(dist["detected"]),
    }
    if quality is not None:
        out["QUALITY_ABNORMAL"] = bool(_analyze_quality(quality)["abnormal"])
    return out


# 注入用例登记表：标签 → 数据构造器列表（≥2 例）
_INJECTED = {
    "OSCILLATION": [_osc_case_1, _osc_case_2],
    "VALVE_STICTION": [
        lambda: _stiction_case(2.0, 10.0, 300.0),
        lambda: _stiction_case(4.0, 12.0, 240.0),
    ],
    "OUTPUT_SATURATION": [_sat_case_high, _sat_case_low],
    "EXTERNAL_DISTURBANCE": [_dist_case_1, _dist_case_2],
}
_QUALITY_INJECTED = [_quality_case_1, _quality_case_2]
_NORMAL = [_normal_case_1, _normal_case_2]

# ---------------------------------------------------------------------------
# 召回率测试（注入检出）
# ---------------------------------------------------------------------------


class TestInjectedRecall:
    """各标签注入用例应检出（每类 2 例，召回率=检出/2 ≥ 80% 需 2/2）."""

    @pytest.mark.parametrize("builder", _INJECTED["OSCILLATION"])
    def test_oscillation_injected(self, builder):
        pv, sp, op = builder()
        assert _run_detectors(pv, sp, op)["OSCILLATION"] is True

    @pytest.mark.parametrize("builder", _INJECTED["VALVE_STICTION"])
    def test_stiction_injected(self, builder):
        pv, sp, op = builder()
        assert _run_detectors(pv, sp, op)["VALVE_STICTION"] is True

    @pytest.mark.parametrize("builder", _INJECTED["OUTPUT_SATURATION"])
    def test_saturation_injected(self, builder):
        pv, sp, op = builder()
        assert _run_detectors(pv, sp, op)["OUTPUT_SATURATION"] is True

    @pytest.mark.parametrize("builder", _INJECTED["EXTERNAL_DISTURBANCE"])
    def test_disturbance_injected(self, builder):
        pv, sp, op = builder()
        assert _run_detectors(pv, sp, op)["EXTERNAL_DISTURBANCE"] is True

    @pytest.mark.parametrize("builder", _QUALITY_INJECTED)
    def test_quality_injected(self, builder):
        assert _analyze_quality(builder())["abnormal"] is True

    def test_aggregate_recall_at_least_80pct(self):
        """汇总召回率基线：5 类标签均 ≥80%（实测全部 100%）."""
        recalls: dict[str, float] = {}
        for label, builders in _INJECTED.items():
            hits = sum(_run_detectors(*b())[label] for b in builders)
            recalls[label] = hits / len(builders)
        q_hits = sum(_analyze_quality(b())["abnormal"] for b in _QUALITY_INJECTED)
        recalls["QUALITY_ABNORMAL"] = q_hits / len(_QUALITY_INJECTED)
        for label, recall in recalls.items():
            assert recall >= 0.8, f"{label} 召回率 {recall:.0%} < 80%"


# ---------------------------------------------------------------------------
# 检测器细节验证点（注入物理量恢复）
# ---------------------------------------------------------------------------


class TestDetectorDetails:
    def test_fft_recovers_oscillation_frequency(self):
        """FFT 主频应恢复注入频率 1/60 Hz（频率分辨率 fs/N 内）."""
        pv, sp, _op = _osc_case_1()
        r = _detect_oscillation_fft(pv, 1.0)
        assert r["detected"] is True
        assert abs(r["frequency"] - 1.0 / 60.0) <= 1.0 / N

    def test_saturation_rate_matches_injected(self):
        """饱和率应恢复注入占比 60%（容差 1 个采样点）."""
        _pv, _sp, op = _sat_case_high()
        r = _analyze_saturation(op, np.ones(N))
        assert r["detected"] is True
        assert r["saturation_rate"] == pytest.approx(0.6, abs=1.0 / N)

    def test_quality_pattern_q001_q002(self):
        """质量模式识别：连续 20 Bad → Q001；12.5% 散布 Bad → Q002."""
        assert _analyze_quality(_quality_case_1())["quality_pattern"] == "Q001"
        assert _analyze_quality(_quality_case_2())["quality_pattern"] == "Q002"


# ---------------------------------------------------------------------------
# 误报率测试（正常对照，基线断言 FPR ≤ 20% 即 0/2）
# ---------------------------------------------------------------------------


class TestFalsePositiveBaseline:
    def test_saturation_no_false_positive(self):
        """OUTPUT_SATURATION 误报率 0/2（达标）."""
        fps = sum(_run_detectors(*b())["OUTPUT_SATURATION"] for b in _NORMAL)
        assert fps / len(_NORMAL) <= 0.2

    def test_quality_no_false_positive(self):
        """QUALITY_ABNORMAL 误报率 0/2（达标）."""
        fps = sum(_analyze_quality([{"quality": "GOOD"}] * N)["abnormal"] for _ in _NORMAL)
        assert fps / len(_NORMAL) <= 0.2

    @pytest.mark.xfail(
        reason=(
            "OSCILLATION 误报率 2/2=100%（基线实测）："
            "NORM1 白噪声下 IAE 相似率 0.962 ≥ 0.4 误报；"
            "NORM2 慢漂+噪声下 FFT osc_index=0.661 且噪声过零数>5 误报。"
            "IAE 相似率对白噪声不免疫、FFT 过零计数未抗噪，Phase 6 基线记录。"
        ),
        strict=False,
    )
    def test_oscillation_no_false_positive(self):
        fps = sum(_run_detectors(*b())["OSCILLATION"] for b in _NORMAL)
        assert fps / len(_NORMAL) <= 0.2

    @pytest.mark.xfail(
        reason=(
            "VALVE_STICTION 误报率 2/2=100%（基线实测）："
            "Choudhury 对正常正弦 OP NGI=0.240 > 0.001 且 NLI=1.0 > 0.01 误报；"
            "NGI 阈值 0.001 对任何非高斯 OP（正弦 excess kurtosis=-1.5 → NGI≈0.25）"
            "必然击穿，Phase 6 基线记录。"
        ),
        strict=False,
    )
    def test_stiction_no_false_positive(self):
        fps = sum(_run_detectors(*b())["VALVE_STICTION"] for b in _NORMAL)
        assert fps / len(_NORMAL) <= 0.2

    @pytest.mark.xfail(
        reason=(
            "EXTERNAL_DISTURBANCE 误报率 2/2=100%（基线实测）："
            "NORM1 纯白噪声 CUSUM shift_count=10（10 次/小时 > 5 阈值）误报；"
            "NORM2 慢漂 shift_count=277 误报。CUSUM 频率判据对噪声/慢漂缺乏区分度，"
            "Phase 6 基线记录。"
        ),
        strict=False,
    )
    def test_disturbance_no_false_positive(self):
        fps = sum(_run_detectors(*b())["EXTERNAL_DISTURBANCE"] for b in _NORMAL)
        assert fps / len(_NORMAL) <= 0.2
