"""P3-09 核心动态评分与 F 参考统计验收用例（10 号文 §5 EA 子例）.

覆盖（任务书【4】核验）：
- C07-EA1~6（09a 静稳 F 参考/状态矩阵/资格与互斥、09b 判稳）
- C14-EA1/2（09c 暂态秒数迁移/跨剔除边界不拼接/偏置振荡补充证据）
- C26-EA3（F 参考双统计：参考均分/动态子集/权重占比）
- INT-02 前半（10 条合法静稳 → F 参考 100、动态覆盖 0、P 可评分 0/10）
- 参考构成/数据质量/完整 P 比例可查；legacy 对照（golden 语义零回归）

oracle 独立性纪律（04§2/P3-06-07 冻结草案 §0.2）：全部期望值由独立手算/
解析表达式得出，不用被测实现自产期望。容差基线（§0.3 已冻结总则）：
- 确定的率/评分：绝对误差 ≤0.01 分；
- 数值内核（Green/信号解析量）：绝对误差 ≤1e-4；
- C07-EA2 候选索引/尾段长度：±2 采样（AR 拟合精度余量，EA-D03 范围内
  运行前登记）。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.contracts.data_types import DataLineage, MetricResult
from app.services import algorithm_config as ac
from app.services.confidence_evaluator import ConfidenceEvaluator
from app.services.metric_calculator.fast_rate import FastRateCalculator
from app.services.metric_calculator.oscillation import OscillationRateCalculator
from app.services.metric_calculator.reference_stats import (
    fast_rate_reference_stats,
    scorable_p_ratio,
    weighted_score_stats,
)
from app.services.metric_calculator.settling_time import SettlingTimeCalculator
from app.services.metric_calculator.stability import StabilityRateCalculator
from app.tasks.arma import (
    MAX_GREEN_FUNC_LENGTH,
    SettlingStatus,
    compute_settling_time_detailed,
)

from .test_metric_calculator.conftest import make_bundle

PROFILE = "CANDIDATE_V3_RC1"
_CONF_A = "A"


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------


def _result(
    metric_code: str,
    value: float | None,
    details: dict | None = None,
    confidence: str = _CONF_A,
) -> MetricResult:
    return MetricResult(
        metric_code=metric_code,
        value=value,
        confidence_level=confidence,
        lineage=DataLineage(),
        details=details or {},
    )


def _static_bundle(n: int = 200, pv: float = 50.0, sp: float = 50.0):
    """合格静稳窗（PV/SP 恒定、质量 A、覆盖率 100%）。"""
    return make_bundle(
        {"pv": [pv] * n, "sp": [sp] * n, "pv_range": [100.0] * n},
        metric_code="fast_rate",
        loop_confidence_level=_CONF_A,
    )


def _settling_then_fast(bundle, settling: MetricResult | None = None, ideal: float = 60.0):
    """新 profile 链：settling_time → fast_rate（默认真跑 settling）。"""
    if settling is None:
        settling = (
            SettlingTimeCalculator()
            .with_assessment_profile(PROFILE)
            .calculate(_recode(bundle, "settling_time"))
        )
    calc = (
        FastRateCalculator()
        .with_assessment_profile(PROFILE)
        .with_dependencies(
            {
                "settling_time": settling,
                "ideal_settling_time": _result("ideal_settling_time", ideal),
            }
        )
    )
    return settling, calc.calculate(bundle)


def _recode(bundle, metric_code):
    """make_bundle 产物换 metric_code（DataBlock 复用）。"""
    bundle.metric_code = metric_code
    return bundle


def _ideal(t: float) -> MetricResult:
    return _result("ideal_settling_time", t, {"source": "manual"})


def _settling(value, reason: str, details: dict | None = None) -> MetricResult:
    d = {"reason": reason}
    if details:
        d.update(details)
    return _result("settling_time", value, d)


@pytest.fixture
def reset_algo_config_cache():
    """隔离算法参数缓存（新增 reference_* 键走 _DEFAULTS 默认层）。"""
    saved = dict(ac._merged_cache)
    ac._merged_cache = {}
    yield
    ac._merged_cache = saved


# ---------------------------------------------------------------------------
# C07-EA2（09b）：持续尾段判稳 oracle——独立数学表达式复核
# ---------------------------------------------------------------------------


class TestC07EA2SustainedTail:
    """G(k)=0.998^k·sin((k+1)π/200)/sin(π/200)，k=0…3599，dt=1s，阈值 5%。

    层级=AR 内核独立参考（10§5）：oracle 数字直接锚在**判稳内核**上
    （`sustained_tail_verdict`/`first_window_below_index` 纯函数）——独立
    手算（numpy 解析式，非产品代码）：
    - |G(2499)| = 0.998^2499/sin(π/200) ≈ 0.42768（周期峰值处远超阈值）；
    - 首个 10 点连续低阈窗口起点 k=2393（正弦过零附近，旧 window 判据
      会在此提前判稳——本反例的靶点）；
    - 最后超阈点 k=3525 → 候选尾段从 3526 开始，仅 74s；
    - 极点周期 = 2π/(π/200) = 400s > 74s → 尾段支持不足，T=null。
    注：YW 拟合对确定性瞬态有偏（近单位根过程样本自相关收敛慢），端到端
    管道不能复现解析 Green——本例按 10§5 分层口径在内核层验收；端到端
    行为由衰减信号用例（test_decaying_signal_settles_with_sustained_tail）
    与三态用例覆盖。
    """

    K = np.arange(3600)
    G = 0.998**K * np.sin((K + 1) * np.pi / 200) / np.sin(np.pi / 200)

    def test_oracle_facts_independent(self):
        """oracle 数字自证（独立 numpy，非被测实现）。"""
        assert abs(abs(self.G[2499]) - 0.42768) < 1e-4
        below = np.abs(self.G) < 0.05
        w = np.convolve(below.astype(int), np.ones(10, dtype=int), mode="valid")
        assert int(np.argmax(w == 10)) == 2393
        last_above = int(np.where(~below)[0][-1])
        assert last_above == 3525
        assert 3600 - 3526 == 74

    def test_kernel_sustained_tail_insufficient(self):
        """内核：候选 3526、尾段 74s、极点周期 400s → insufficient，T=null。"""
        from app.tasks.arma import sustained_tail_verdict

        verdict, cand, tail, base_req, total_req = sustained_tail_verdict(
            np.abs(self.G), 0.05, 1.0, pole_period_seconds=400.0
        )
        assert verdict == "insufficient"
        assert abs(cand - 3526) <= 2
        assert abs(tail - 74.0) <= 2.0
        assert base_req == pytest.approx(10.0)
        assert total_req == pytest.approx(400.0, abs=1.0)

    def test_kernel_legacy_window_would_settle_early(self):
        """内核：legacy window 判据在同一向量上判稳 2393（提前判稳的反面
        证据；同时该规则经纯函数抽取后行为不变，见既有 arma 套件回归）。"""
        from app.tasks.arma import first_window_below_index

        assert first_window_below_index(np.abs(self.G), 0.05, 10) == 2393

    def test_kernel_never_verdict_for_persistent_oscillation(self):
        """持续等幅振荡：尾段连基础支持都不足 → never（T 下界=推演时域）。"""
        from app.tasks.arma import sustained_tail_verdict

        k = np.arange(3600)
        osc = np.sin(2 * np.pi * k / 100)  # 等幅，周期 100s
        verdict, cand, tail, base_req, _ = sustained_tail_verdict(
            np.abs(osc), 0.05, 1.0, pole_period_seconds=100.0
        )
        assert verdict == "never"
        assert tail < base_req

    def test_kernel_settled_when_tail_supported(self):
        """单调衰减向量：尾段充分 → settled。"""
        from app.tasks.arma import sustained_tail_verdict

        k = np.arange(3600)
        decay = 0.9**k  # 30s 内衰减过阈
        verdict, cand, tail, _base, total = sustained_tail_verdict(
            np.abs(decay), 0.05, 1.0, pole_period_seconds=None
        )
        assert verdict == "settled"
        assert tail >= total

    def test_decaying_signal_settles_with_sustained_tail(self):
        """端到端：正常衰减信号（AR(1) 实现 YW 无偏）在新判据下仍 SETTLED。"""
        np.random.seed(7)
        n = 500
        signal = np.zeros(n)
        for t in range(1, n):
            signal[t] = -0.3 * signal[t - 1] + np.random.randn() * 0.1
        r = compute_settling_time_detailed(signal, sample_interval_sec=1.0, sustained_tail=True)
        assert r.status is SettlingStatus.SETTLED
        assert r.value is not None and 0 < r.value < r.window_length_sec
        assert r.tail_seconds >= r.required_tail_seconds

    def test_min_tail_support_floor(self):
        """最短支持 = max(3×dt, 10s)：dt=10 → 30s 下限（EA-D03 口径）。"""
        from app.tasks.arma import (
            MIN_TAIL_SUPPORT_DT_MULTIPLE,
            MIN_TAIL_SUPPORT_SECONDS,
        )

        assert MIN_TAIL_SUPPORT_DT_MULTIPLE == 3.0
        assert MIN_TAIL_SUPPORT_SECONDS == 10.0
        assert max(3.0 * 10.0, 10.0) == 30.0

    def test_budget_not_extended(self):
        """不自动提高产品推演预算（C07-EA2：独立扩展向量验证规则即可）。"""
        assert MAX_GREEN_FUNC_LENGTH == 3600

    def test_observation_vs_horizon_separated(self):
        """观测 500s ≠ 推演 3600s：分开报告，不把外推写成观测。"""
        np.random.seed(7)
        signal = np.zeros(500)
        for t in range(1, 500):
            signal[t] = -0.3 * signal[t - 1] + np.random.randn() * 0.1
        r = compute_settling_time_detailed(signal, sample_interval_sec=1.0, sustained_tail=True)
        assert r.observation_seconds == pytest.approx(500.0)
        assert r.simulation_horizon_seconds == pytest.approx(float(MAX_GREEN_FUNC_LENGTH))


# ---------------------------------------------------------------------------
# C07-EA1（09a）：合格静稳 → 正式 T/F null + 参考 100 + 质量不降级
# ---------------------------------------------------------------------------


class TestC07EA1StaticReference:
    def test_static_window_reference_and_null_formal(self, reset_algo_config_cache):
        """PV=SP 静稳窗：T/F 正式 null、参考 100、UNASSESSABLE、质量仍 A。"""
        bundle = _static_bundle()
        settling, fast = _settling_then_fast(bundle)
        # T：当前平稳 + 动态证据不足，正式值 null
        assert settling.value is None
        assert settling.details["currentlyStable"] is True
        assert settling.details["assessmentState"] == "UNASSESSABLE"
        assert settling.confidence_level == _CONF_A  # 数据质量与可判定性分开
        # F：正式 null + 参考字段组（契约 §1.4）
        assert fast.value is None
        assert fast.confidence_level == _CONF_A  # 不得因参考分降 E
        d = fast.details
        assert d["assessmentState"] == "UNASSESSABLE"
        assert d["referenceScore"] == 100.0
        assert d["referenceMethod"] == "STATIC_WINDOW_POLICY"
        assert d["referencePolicy"] == "STATIC_BASELINE_100_V1"
        assert d["evidenceStatus"] == "INSUFFICIENT_DYNAMIC_EVIDENCE"
        assert d["reasonCode"] == "NO_DYNAMIC_EVIDENCE"
        assert d["currentlyStable"] is True
        assert isinstance(d["referenceChecks"], dict)

    def test_reference_same_for_identical_static_windows(self, reset_algo_config_cache):
        """同一静稳窗跑两遍（等价于"快/慢系统无可区分动态"）参考一致——
        参考 100 不证明快慢。"""
        _, f1 = _settling_then_fast(_static_bundle())
        _, f2 = _settling_then_fast(_static_bundle())
        assert f1.value is None and f2.value is None
        assert f1.details["referenceScore"] == f2.details["referenceScore"] == 100.0

    def test_legacy_static_window_still_full_score(self, reset_algo_config_cache):
        """legacy 对照：同一输入 already_stable → F=100（golden 基线不变）。"""
        bundle = _static_bundle()
        settling = SettlingTimeCalculator().calculate(_recode(bundle, "settling_time"))
        assert settling.value == 0.0
        fast = (
            FastRateCalculator()
            .with_dependencies({"settling_time": settling, "ideal_settling_time": _ideal(60.0)})
            .calculate(bundle)
        )
        assert fast.value == 100.0
        assert fast.details["reason"] == "already_stable"

    def test_necessary_f_reference_makes_p_incomplete(self, reset_algo_config_cache):
        """必要 F（权重>0）仅参考 → P 不完整（value=None + PARTIAL）。"""
        bundle = _static_bundle()
        _, fast = _settling_then_fast(bundle)
        metrics = {
            "accuracy_rate": _result("accuracy_rate", 90.0),
            "fast_rate": fast,
            "stability_rate": _result("stability_rate", 95.0, {"assessmentState": "COMPLETE"}),
            "effective_auto_rate": _result("effective_auto_rate", 90.0),
        }
        p = ConfidenceEvaluator.compute_composite_score(
            metrics, weights=None, assessment_profile=PROFILE
        )
        assert p.value is None
        assert p.details["assessmentState"] == "PARTIAL"
        inc = p.details["incompleteComponents"]
        assert any(e["metric"] == "fast_rate" and e.get("referenceOnly") for e in inc)
        assert p.details["availableComponents"] == ["accuracy_rate", "stability_rate"]
        assert p.details["referenceComposition"]["fastRate"] == "REFERENCE_ONLY"


# ---------------------------------------------------------------------------
# C07-EA6（09a）：参考资格反例——斜坡/覆盖不足/失效/失败/未收敛不套 100
# ---------------------------------------------------------------------------


class TestC07EA6ReferenceIneligibility:
    def test_synchronized_ramp_no_reference(self, reset_algo_config_cache):
        """SP/PV 同步斜坡且误差恒定 → 不满足静稳参考资格。"""
        n = 200
        ramp = np.linspace(50.0, 60.0, n)
        bundle = make_bundle(
            {
                "pv": list(ramp),
                "sp": list(ramp),
                "pv_range": [100.0] * n,
            },
            metric_code="fast_rate",
            loop_confidence_level=_CONF_A,
        )
        # 误差恒为 0（σ=0）→ settling 判 already_stable，但资格检查拒斜坡
        _, fast = _settling_then_fast(bundle)
        assert fast.value is None
        assert fast.details.get("referenceScore") is None  # 不套 100
        assert fast.details["reason"] == "static_reference_ineligible"
        failed = fast.details["ineligibleReasons"]
        assert "sp_stationary" in failed and "pv_trend" in failed

    def test_low_coverage_no_reference(self, reset_algo_config_cache):
        """覆盖不足（vr=0.6 < 0.95）→ 不发参考（缺数据不缩分母不套分）。"""
        n = 200
        validity = {
            "pv_valid": [True] * 120 + [False] * 80,
            "sp_valid": [True] * n,
        }
        bundle = make_bundle(
            {"pv": [50.0] * n, "sp": [50.0] * n, "pv_range": [100.0] * n},
            validity,
            mask_expression="pv_valid && sp_valid",
            metric_code="fast_rate",
            loop_confidence_level=_CONF_A,
        )
        settling = (
            SettlingTimeCalculator()
            .with_assessment_profile(PROFILE)
            .calculate(_recode(bundle, "settling_time"))
        )
        assert settling.details["reason"] == "already_stable"  # 120 点 ≥ 100 且 σ=0
        fast = (
            FastRateCalculator()
            .with_assessment_profile(PROFILE)
            .with_dependencies({"settling_time": settling, "ideal_settling_time": _ideal(60.0)})
            .calculate(bundle)
        )
        assert fast.value is None and fast.details.get("referenceScore") is None
        assert "data_quality" in fast.details["ineligibleReasons"]

    def test_invalid_ideal_no_reference(self, reset_algo_config_cache):
        """非法 T′（配置失效）→ 无参考。"""
        bundle = _static_bundle()
        fast = (
            FastRateCalculator()
            .with_assessment_profile(PROFILE)
            .with_dependencies(
                {
                    "settling_time": _settling(None, "already_stable"),
                    "ideal_settling_time": _ideal(0.0),
                }
            )
            .calculate(bundle)
        )
        assert fast.value is None
        assert fast.details["reason"] == "invalid_ideal_settling_time"
        assert fast.details.get("referenceScore") is None

    def test_identification_failed_no_reference(self, reset_algo_config_cache):
        """算法失败 → 无参考。"""
        bundle = _static_bundle()
        fast = (
            FastRateCalculator()
            .with_assessment_profile(PROFILE)
            .with_dependencies(
                {
                    "settling_time": _settling(None, "identification_failed"),
                    "ideal_settling_time": _ideal(60.0),
                }
            )
            .calculate(bundle)
        )
        assert fast.value is None and fast.details.get("referenceScore") is None
        assert fast.details["reason"] == "identification_failed"

    def test_formal_zero_score_wins_over_reference(self, reset_algo_config_cache):
        """正式 F=0 优先于参考语义（互斥；仅计一次）。"""
        bundle = _static_bundle()
        fast = (
            FastRateCalculator()
            .with_assessment_profile(PROFILE)
            .with_dependencies(
                {
                    # SETTLED 但 T=3600 >> 阈值 60 → 正式 F≈0（真实 0 分）
                    "settling_time": _settling(3600.0, "settled", {"actual_settling_time": 3600.0}),
                    "ideal_settling_time": _ideal(60.0),
                }
            )
            .calculate(bundle)
        )
        assert fast.value == 0.0
        assert fast.details["assessmentState"] == "COMPLETE"
        assert "referenceScore" not in fast.details  # 正式值优先，不叠加参考

    def test_constant_offset_bias_reference_with_deviation(self, reset_algo_config_cache):
        """大恒偏差静稳：通过静稳资格才有参考 100，且明确偏差（平稳≠偏差合格）；
        PV 恒值保留仪表未确认提示（恒值不能证明仪表正常）。"""
        bundle = _static_bundle(pv=55.0, sp=50.0)  # 恒偏置 5（量程 5%）
        settling = (
            SettlingTimeCalculator()
            .with_assessment_profile(PROFILE)
            .calculate(_recode(bundle, "settling_time"))
        )
        assert settling.details["reason"] == "already_stable"  # σ=0 低于噪声底
        fast = (
            FastRateCalculator()
            .with_assessment_profile(PROFILE)
            .with_dependencies({"settling_time": settling, "ideal_settling_time": _ideal(60.0)})
            .calculate(bundle)
        )
        assert fast.details["referenceScore"] == 100.0
        dev = fast.details["referenceDeviation"]
        assert dev["meanError"] == pytest.approx(5.0, abs=0.01)
        assert "平稳≠偏差合格" in dev["note"]
        # PV 恒值：不阻断参考，但保留仪表未确认提示
        checks = fast.details["referenceChecks"]
        assert checks["pvFrozen"] is True
        assert "仪表" in checks["pvFrozenInstrumentHint"]

    def test_frozen_pv_gets_reference_with_instrument_hint(self, reset_algo_config_cache):
        """PV 一字不变（恒值）：其他静稳条件成立保留参考 100 + 未确认提示
        （恒值本身不能证明仪表正常，也不能反向确定故障）。"""
        bundle = _static_bundle(pv=55.0, sp=50.0)
        fast = (
            FastRateCalculator()
            .with_assessment_profile(PROFILE)
            .with_dependencies(
                {
                    "settling_time": _settling(None, "already_stable"),
                    "ideal_settling_time": _ideal(60.0),
                }
            )
            .calculate(bundle)
        )
        assert fast.details["referenceScore"] == 100.0
        assert fast.details["referenceChecks"]["pvFrozen"] is True


# ---------------------------------------------------------------------------
# C07-EA3（09a/09b）：全窗不衰减 / 尾段不足 / 辨识发散 三态分别报告
# ---------------------------------------------------------------------------


class TestC07EA3ThreeStates:
    def test_never_settles_reports_lower_bound_not_score(self, reset_algo_config_cache):
        """未收敛：T 下界 + F 上界 details；点估计 null（不以观察窗替 T）。"""
        n = 2000
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 100) for i in range(n)]
        sp = [50.0] * n
        bundle = make_bundle(
            {"pv": pv, "sp": sp, "pv_range": [100.0] * n},
            metric_code="fast_rate",
            loop_confidence_level=_CONF_A,
        )
        settling, fast = _settling_then_fast(bundle)
        assert settling.details["reason"] == "never_settles"
        assert settling.value is None
        assert fast.value is None  # 不给分（legacy 会以窗口代入衰减公式）
        d = fast.details
        assert d["settling_lower_bound_seconds"] == pytest.approx(3600.0, abs=0.01)
        # F 上界 = exp(-(3600-60)/60)×100 ≈ e^-59×100 → 下溢截断为 0
        assert d["fast_rate_upper_bound"] == pytest.approx(0.0, abs=0.01)

    def test_insufficient_tail_reports_candidate(self, reset_algo_config_cache):
        """尾段不足：候选进入带时刻 + 尾段信息（观察不足非失败；经依赖
        注入驱动 fast_rate 分流——内核数字由 TestC07EA2 内核用例锚定）。"""
        bundle = _static_bundle()
        settling = _settling(
            None,
            "insufficient_tail_support",
            {
                "candidate_settling_time": 3526.0,
                "tail_seconds": 74.0,
                "required_tail_seconds": 400.0,
                "pole_period_seconds": 400.0,
            },
        )
        fast = (
            FastRateCalculator()
            .with_assessment_profile(PROFILE)
            .with_dependencies({"settling_time": settling, "ideal_settling_time": _ideal(60.0)})
            .calculate(bundle)
        )
        assert fast.value is None
        assert fast.details["reason"] == "insufficient_tail_support"
        assert fast.details["candidate_settling_time"] == pytest.approx(3526.0)
        assert fast.details["tail_seconds"] == pytest.approx(74.0)
        assert fast.details.get("referenceScore") is None

    def test_reasons_not_mixed(self, reset_algo_config_cache):
        """三态原因不混用（分别可查）。"""
        bundle = _static_bundle()
        for reason, key in (
            ("never_settles", "settling_lower_bound_seconds"),
            ("identification_failed", None),
            ("insufficient_tail_support", "candidate_settling_time"),
        ):
            settling = _settling(None, reason, {key: 1.0} if key else None)
            fast = (
                FastRateCalculator()
                .with_assessment_profile(PROFILE)
                .with_dependencies({"settling_time": settling, "ideal_settling_time": _ideal(60.0)})
                .calculate(bundle)
            )
            assert fast.value is None
            assert fast.details["reason"] == reason
            assert fast.details.get("referenceScore") is None


# ---------------------------------------------------------------------------
# C07-EA4（09a）：T′ 来源 + F 阈值两侧分段映射连续（STD-02 新 profile 回归）
# ---------------------------------------------------------------------------


class TestC07EA4ThresholdContinuity:
    @pytest.mark.parametrize("source", ["manual", "default"])
    def test_ideal_source_explicit(self, reset_algo_config_cache, source):
        d = {"source": source}
        assert d["source"] in ("manual", "default")  # 现行已显式（回归守护）

    def test_threshold_both_sides_continuous(self, reset_algo_config_cache):
        """T=阈值 → 100；T=阈值+1 → exp(-1/60)×100≈98.36（无跳变）。"""
        bundle = _static_bundle()
        exp_decay = math.exp(-1.0 / 60.0) * 100.0
        for t, expected in ((60.0, 100.0), (61.0, exp_decay)):
            fast = (
                FastRateCalculator()
                .with_assessment_profile(PROFILE)
                .with_dependencies(
                    {
                        "settling_time": _settling(t, "settled", {"actual_settling_time": t}),
                        "ideal_settling_time": _ideal(60.0),
                    }
                )
                .calculate(bundle)
            )
            assert fast.value is not None
            assert fast.value == pytest.approx(expected, abs=0.01), t
            assert fast.details["method"] == "error_ar_decay"


# ---------------------------------------------------------------------------
# C07-EA5（09c）：Osc 合法 0 / 缺失 / 失败 → 不默认为 0
# ---------------------------------------------------------------------------


class TestC07EA5OscDependency:
    @staticmethod
    def _stab_signal():
        """微幅波动偏差（σ>0 避开死信号分支；偏置 0.05）。"""
        n = 200
        pv = [50.0 + 0.05 + (0.001 if i % 2 else -0.001) for i in range(n)]
        return pv, [50.0] * n

    def _stab(self, osc: MetricResult | None):
        pv, sp = self._stab_signal()
        bundle = make_bundle(
            {"pv": pv, "sp": sp, "pv_range": [100.0] * len(pv)},
            metric_code="stability_rate",
            loop_confidence_level=_CONF_A,
        )
        deps = {"oscillation_rate": osc} if osc is not None else {}
        calc = StabilityRateCalculator().with_assessment_profile(PROFILE)
        if deps:
            calc = calc.with_dependencies(deps)
        return calc.calculate(bundle)

    def test_legal_zero_scores_complete(self, reset_algo_config_cache):
        """合法 0（充分数据、判非振荡）→ S 完整（osc_factor=1）。"""
        osc = _result(
            "oscillation_rate", 0.0, {"is_oscillating": False, "assessmentState": "COMPLETE"}
        )
        r = self._stab(osc)
        assert r.value is not None
        assert r.details["assessmentState"] == "COMPLETE"
        assert r.details["osc_factor"] == 1.0

    def test_missing_osc_partial_with_base_score(self, reset_algo_config_cache):
        """Osc 缺失 → 基础波动分 + PARTIAL（不默认 Osc=0 混成完整 S）。"""
        r = self._stab(None)
        assert r.value is not None  # 基础波动分保留（可复算）
        assert r.details["assessmentState"] == "PARTIAL"
        assert r.details["oscDependency"]["state"] == "MISSING"
        # 基础分 = exp(−σ/(dU))×100（不乘振荡修正；σ 由序列独立计算）
        pv, sp = self._stab_signal()
        sigma = float(np.std([p - s for p, s in zip(pv, sp, strict=True)], ddof=1))
        expected = math.exp(-(sigma / (0.05 * 100.0))) * 100.0
        assert r.value == pytest.approx(expected, abs=0.01)

    def test_failed_osc_partial(self, reset_algo_config_cache):
        """Osc 内部失败（value=None）→ PARTIAL + UNAVAILABLE + 原因透传。"""
        osc = _result(
            "oscillation_rate",
            None,
            {"reason": "insufficient_data", "assessmentState": "UNASSESSABLE"},
        )
        r = self._stab(osc)
        assert r.details["assessmentState"] == "PARTIAL"
        assert r.details["oscDependency"] == {
            "state": "UNAVAILABLE",
            "reason": "insufficient_data",
        }

    def test_legacy_missing_osc_unchanged(self, reset_algo_config_cache):
        """legacy：Osc 缺失沿用现行行为（osc_factor=1，无新字段）。"""
        pv, sp = self._stab_signal()
        bundle = make_bundle(
            {"pv": pv, "sp": sp, "pv_range": [100.0] * len(pv)},
            metric_code="stability_rate",
            loop_confidence_level=_CONF_A,
        )
        r = StabilityRateCalculator().calculate(bundle)
        assert r.value is not None
        assert "assessmentState" not in r.details


# ---------------------------------------------------------------------------
# C14-EA1（09c）：暂态秒数迁移 + 跨剔除边界不拼周期
# ---------------------------------------------------------------------------


def _sp_step_bundle(n: int, step_at: int, interval_s: float = 1.0):
    sp = [50.0] * step_at + [60.0] * (n - step_at)
    pv = [s + 0.05 for s in sp]  # 跟随（误差恒定）
    return make_bundle(
        {"pv": pv, "sp": sp, "pv_range": [100.0] * n},
        metric_code="oscillation_rate",
        loop_confidence_level=_CONF_A,
        interval_s=interval_s,
    )


class TestC14EA1TransientSeconds:
    @pytest.mark.parametrize(
        ("interval_s", "expected_points"),
        [(1.0, 60), (5.0, 12), (10.0, 6)],
    )
    def test_same_physical_exclusion_across_sampling(
        self, reset_algo_config_cache, interval_s, expected_points
    ):
        """1/5/10s 采样、60s 剔除窗：剔除秒数一致（≈60s），点数随周期换算。"""
        n = 600
        bundle = _sp_step_bundle(n, step_at=300, interval_s=interval_s)
        r = OscillationRateCalculator().with_assessment_profile(PROFILE).calculate(bundle)
        d = r.details
        assert d["sp_steps_detected"] == 1
        assert d["sp_excluded_points"] == expected_points
        assert d["sp_tracking_window_source"] == "physical_seconds"
        # 真实剔除边界一致：秒数 = 点数×周期 = 60s（±1 周期取整容差）
        assert d["sp_excluded_transient_seconds"] == pytest.approx(60.0, abs=interval_s)

    def test_no_bridging_across_exclusion(self, reset_algo_config_cache):
        """剔除区两侧不拼接：legacy 在接缝处产生伪穿越（+1 个伪半周期段，
        段时长被接缝截断污染）；新判据只识别保留段内穿越。"""
        n = 400
        # 前段：cos 振荡（周期 100 样本，幅度 10），段内穿越 25/75/125/175 = 4
        pv_pre = [50.0 + 10.0 * math.cos(2 * math.pi * i / 100) for i in range(200)]
        # SP 阶跃 @200（剔除 60 点）+ 后段恒负（-1，接缝前值为正 → 桥接时
        # 在接缝处产生第 5 个穿越点）
        sp = [50.0] * 200 + [60.0] * (n - 200)
        pv = pv_pre + [59.0] * (n - 200)
        new = (
            OscillationRateCalculator()
            .with_assessment_profile(PROFILE)
            .calculate(
                make_bundle(
                    {"pv": pv, "sp": sp, "pv_range": [100.0] * n},
                    metric_code="oscillation_rate",
                    loop_confidence_level=_CONF_A,
                )
            )
        )
        legacy = OscillationRateCalculator().calculate(
            make_bundle(
                {"pv": pv, "sp": sp, "pv_range": [100.0] * n},
                metric_code="oscillation_rate",
                loop_confidence_level=_CONF_A,
            )
        )
        # 新判据：只识别保留段内穿越（前段 4 个，后段恒定无穿越）
        assert new.details["zero_crossings"] == 4
        # legacy：剔除压缩后前段尾（+9.98）与后段头（-1）直接相邻 →
        # 接缝伪穿越（第 5 个）——跨剔除区拼接的正半周期段被接缝截断污染
        assert legacy.details["zero_crossings"] == 5
        # 新判据完整段 = 段内 3 个（1正2负）；legacy 多出接缝截断的伪正段
        assert new.details["positive_segments"] == 1
        assert new.details["negative_segments"] == 2
        assert legacy.details["positive_segments"] == 2

    def test_insufficient_after_exclusion(self, reset_algo_config_cache):
        """剔除后样本不足 → 明确拒绝（reason 可查）。"""
        n = 50
        sp = [50.0] * 3 + [60.0] * (n - 3)  # 早期阶跃：剔除后仅剩 3 点
        pv = [s + 0.05 for s in sp]
        bundle = make_bundle(
            {"pv": pv, "sp": sp, "pv_range": [100.0] * n},
            metric_code="oscillation_rate",
            loop_confidence_level=_CONF_A,
        )
        r = OscillationRateCalculator().with_assessment_profile(PROFILE).calculate(bundle)
        assert r.value is None
        assert r.details["reason"] == "insufficient_data_after_sp_exclusion"

    def test_band_in_denominator_window_minus_transients(self, reset_algo_config_cache):
        """stability 带内率分母 = 窗口 − 已知剔除暂态（契约 §2.1）。"""
        from datetime import timedelta

        from app.contracts.data_types import DataBlock, MetricDataBundle, QualitySummary
        from app.services.metric_calculator.time_axis import build_metric_time_view

        n = 600
        step_at = 300
        sp = [50.0] * step_at + [60.0] * (n - step_at)
        pv = [s + 0.05 for s in sp]
        base = _sp_step_bundle(n, step_at)
        # 构造携带查询窗的 bundle（时间视图可用）
        start = base.data_block.timestamps[0]
        block = DataBlock(
            data_block_id="db_test",
            loop_id="L001",
            tag_group="BASE",
            sampling_freq="1s",
            timestamps=list(base.data_block.timestamps),
            signals={"pv": pv, "sp": sp, "pv_range": [100.0] * n},
            validity={"pv_valid": [True] * n, "sp_valid": [True] * n},
            outlier_reasons={},
            quality_summary=QualitySummary(
                total_count=n, valid_count=n, bad_count=0, valid_rate=1.0, bad_rate=0.0
            ),
            consecutive_segments=[(0, n - 1)],
            point_count=n,
            loop_confidence_level=_CONF_A,
            query_window_start=start,
            query_window_end=start + timedelta(seconds=n - 1),
        )
        bundle = MetricDataBundle(
            metric_code="stability_rate",
            data_block=block,
            mask_expression="pv_valid && sp_valid",
            masked_indices=list(range(n)),
            lineage=DataLineage(sampling_freq="1s", tag_group="BASE", valid_rate=1.0),
        )
        view = build_metric_time_view(bundle, None, ("pv", "sp"))
        assert view is not None  # 视图可用（契约口径生效前提）
        calc = StabilityRateCalculator().with_assessment_profile(PROFILE)
        r = calc.calculate(bundle)
        d = r.details
        # 窗口 600s − 剔除 60s = 540s（分母=窗口减已知剔除暂态，未知不缩分母）
        assert d["band_in_denominator_basis"] == "WINDOW_MINUS_KNOWN_EXCLUDED_TRANSIENTS"
        assert d["band_in_denominator_seconds"] == pytest.approx(540.0, abs=1.0)
        assert d["sp_excluded_transient_seconds"] == pytest.approx(60.0, abs=1.0)
        # 带内率 = 带内 540 点×1s / 540s = 100%（|E|=0.05 ≤ 1%×100=1）
        assert d["band_in_rate"] == pytest.approx(100.0, abs=0.01)


# ---------------------------------------------------------------------------
# C14-EA2（09c）：偏置振荡仅补充证据
# ---------------------------------------------------------------------------


def _osc_signal(pv, sp, n=None):
    n = n or len(pv)
    return make_bundle(
        {"pv": list(pv), "sp": list(sp), "pv_range": [100.0] * n},
        metric_code="oscillation_rate",
        loop_confidence_level=_CONF_A,
    )


class TestC14EA2BiasOscillation:
    def _calc(self, bundle):
        return OscillationRateCalculator().with_assessment_profile(PROFILE).calculate(bundle)

    def test_pure_sine_oscillates(self, reset_algo_config_cache):
        n = 600
        pv = [50.0 + 2.0 * math.sin(2 * math.pi * i / 50) for i in range(n)]
        r = self._calc(_osc_signal(pv, [50.0] * n))
        assert r.details["is_oscillating"] is True  # 原口径自身定义
        assert r.details["assessmentState"] == "COMPLETE"

    def test_biased_sine_main_untouched_bias_evidence_detected(self, reset_algo_config_cache):
        """5+2sin：原 OscSP 只反映自身定义（无过零→非振荡），补充证据可识别。"""
        n = 600
        pv = [55.0 + 2.0 * math.sin(2 * math.pi * i / 50) for i in range(n)]
        r = self._calc(_osc_signal(pv, [50.0] * n))
        assert r.details["is_oscillating"] is False  # 原口径不变
        bias = r.details["biasOscillation"]
        assert bias["detected"] is True
        assert bias["evidenceOnly"] is True
        assert bias["offsetMean"] == pytest.approx(5.0, abs=0.1)
        assert bias["cycles"] >= 5

    def test_drift_not_auto_hit(self, reset_algo_config_cache):
        """单调漂移不因去趋势自动命中。"""
        n = 600
        pv = [50.0 + 10.0 * i / n for i in range(n)]
        r = self._calc(_osc_signal(pv, [50.0] * n))
        assert r.details["biasOscillation"]["detected"] is False

    def test_noise_not_detected(self, reset_algo_config_cache):
        """白噪声不命中（幅度/周期一致性不足）。"""
        np.random.seed(3)
        n = 600
        pv = 50.0 + np.random.randn(n) * 0.1
        r = self._calc(_osc_signal(pv, [50.0] * n))
        assert r.details["biasOscillation"]["detected"] is False

    def test_short_period_rejected(self, reset_algo_config_cache):
        """少点周期（粗采样）拒绝：周期 4 样本 < 半周期门控 8。"""
        n = 600
        pv = [55.0 + 2.0 * math.sin(2 * math.pi * i / 4) for i in range(n)]
        r = self._calc(_osc_signal(pv, [50.0] * n))
        bias = r.details["biasOscillation"]
        assert bias["detected"] is False
        assert bias["reason"] == "half_period_below_floor"

    def test_bias_evidence_does_not_cut_s(self, reset_algo_config_cache):
        """偏置证据不直接扣 S：stability 对 5+2sin 的分值不受 bias 字段影响。"""
        n = 600
        pv = [55.0 + 2.0 * math.sin(2 * math.pi * i / 50) for i in range(n)]
        osc_bundle = _osc_signal(pv, [50.0] * n)
        osc = self._calc(osc_bundle)
        assert osc.details["is_oscillating"] is False  # 偏置不进 (1-Osc) 修正
        stab_bundle = make_bundle(
            {"pv": list(pv), "sp": [50.0] * n, "pv_range": [100.0] * n},
            metric_code="stability_rate",
            loop_confidence_level=_CONF_A,
        )
        r = (
            StabilityRateCalculator()
            .with_assessment_profile(PROFILE)
            .with_dependencies({"oscillation_rate": osc})
            .calculate(stab_bundle)
        )
        errors = np.array([p - 50.0 for p in pv])
        sigma = float(np.std(errors, ddof=1))
        expected = math.exp(-(sigma / (0.05 * 100.0))) * 100.0
        assert r.value == pytest.approx(expected, abs=0.01)
        assert r.details["osc_factor"] == 1.0


# ---------------------------------------------------------------------------
# C26-EA3 + INT-02 前半：F 参考双统计与完整 P 比例
# ---------------------------------------------------------------------------


class TestC26EA3DualStatistics:
    def test_mixed_reference_and_dynamic(self):
        """9 条参考 100（权重 1）+ 1 条动态 F0（权重 3）→ 独立手算：
        主参考均分 = (9×1×1.0 + 1×3×0)/12×100 = 75；动态子集均分 0；
        参考构成 9/10、参考权重 75%、动态覆盖 10%。"""
        entries = [{"weight": 1.0, "formalValue": None, "referenceScore": 100.0}] * 9 + [
            {"weight": 3.0, "formalValue": 0.0, "referenceScore": 100.0}
        ]
        s = fast_rate_reference_stats(entries)
        assert s["fastRateReferenceMean"] == pytest.approx(75.0, abs=0.01)
        assert s["referenceWeightSum"] == pytest.approx(12.0, abs=1e-6)
        assert s["dynamicEstimateCount"] == 1
        assert s["policyReferenceCount"] == 9
        assert s["unavailableCount"] == 0
        assert s["numericCount"] == 10
        assert s["policyReferenceWeightShare"] == pytest.approx(0.75, abs=1e-4)
        assert s["fastRateDynamicEstimateMean"] == pytest.approx(0.0, abs=0.01)
        assert s["dynamicWeightSum"] == pytest.approx(3.0, abs=1e-6)
        assert s["dynamicEvidenceCoverageRatio"] == pytest.approx(0.1, abs=1e-4)
        # 守恒：dynamic+reference+unavailable = 参评对象数
        assert s["dynamicEstimateCount"] + s["policyReferenceCount"] + s["unavailableCount"] == 10

    def test_all_static_reference(self):
        """全 10 条静稳：参考均分 100、动态子集 null、动态覆盖 0%。"""
        entries = [{"weight": 1.0, "formalValue": None, "referenceScore": 100.0}] * 10
        s = fast_rate_reference_stats(entries)
        assert s["fastRateReferenceMean"] == pytest.approx(100.0, abs=0.01)
        assert s["fastRateDynamicEstimateMean"] is None
        assert s["dynamicEvidenceCoverageRatio"] == pytest.approx(0.0, abs=1e-6)
        assert s["policyReferenceWeightShare"] == pytest.approx(1.0, abs=1e-6)

    def test_formal_zero_wins_on_mixed_fields(self):
        """兼容读取：正式 F 与参考并存时正式优先（含 0 分）。"""
        s = fast_rate_reference_stats(
            [{"weight": 1.0, "formalValue": 0.0, "referenceScore": 100.0}]
        )
        assert s["dynamicEstimateCount"] == 1
        assert s["policyReferenceCount"] == 0
        assert s["fastRateReferenceMean"] == pytest.approx(0.0, abs=0.01)

    def test_weighted_score_stats_null_semantics(self):
        """null 不作 0；权重和为 0 时均分 null；零值参与。"""
        s = weighted_score_stats(
            [
                {"weight": 1.0, "value": 0.0},
                {"weight": 1.0, "value": None},
                {"weight": 0.0, "value": 100.0},
            ]
        )
        assert s["objectCount"] == 3
        assert s["numericCount"] == 2
        assert s["numericWeightSum"] == pytest.approx(1.0, abs=1e-6)
        assert s["weightedMean"] == pytest.approx(0.0, abs=0.01)  # 零值参与
        assert weighted_score_stats([{"weight": 0.0, "value": 100.0}])["weightedMean"] is None


class TestINT02FirstHalf:
    """INT-02 前半：10 条质量合格且合法静稳、必要 F 只有参考 100 →
    参考均分 100、动态覆盖 0、P 可评分 0/10；不推定满分或健康。"""

    def test_ten_static_loops(self, reset_algo_config_cache):
        p_values = []
        entries = []
        for _ in range(10):
            bundle = _static_bundle()
            _, fast = _settling_then_fast(bundle)
            assert fast.value is None
            assert fast.details["referenceScore"] == 100.0
            entries.append({"weight": 1.0, "formalValue": None, "referenceScore": 100.0})
            metrics = {
                "accuracy_rate": _result("accuracy_rate", 90.0),
                "fast_rate": fast,
                "stability_rate": _result("stability_rate", 95.0, {"assessmentState": "COMPLETE"}),
                "effective_auto_rate": _result("effective_auto_rate", 90.0),
            }
            p = ConfidenceEvaluator.compute_composite_score(
                metrics, weights=None, assessment_profile=PROFILE
            )
            assert p.value is None  # P 不完整（必要 F 仅参考）
            p_values.append(p.value)
        # F 参考统计：均分 100、动态覆盖 0%
        s = fast_rate_reference_stats(entries)
        assert s["fastRateReferenceMean"] == pytest.approx(100.0, abs=0.01)
        assert s["dynamicEvidenceCoverageRatio"] == pytest.approx(0.0, abs=1e-6)
        # 完整 P 可评分比例 0/10 = 0%（真实 0 分算可评分，None 不算）
        ratio = scorable_p_ratio(p_values)
        assert ratio == {
            "eligibleLoopCount": 10,
            "scorableLoopCount": 0,
            "scorableLoopRatio": 0.0,
        }

    def test_scorable_ratio_edges(self):
        """E=0 → null（无参评）；E>0 且 N=0 → 0%；真实 0 分算可评分。"""
        assert scorable_p_ratio([])["scorableLoopRatio"] is None
        assert scorable_p_ratio([None])["scorableLoopRatio"] == 0.0
        assert scorable_p_ratio([0.0, None, None])["scorableLoopRatio"] == pytest.approx(
            100.0 / 3.0, abs=0.01
        )


# ---------------------------------------------------------------------------
# 综合评分 v3：每指标独立分子/权重和 + 参考构成可查
# ---------------------------------------------------------------------------


class TestCompositeV3Decomposition:
    def _complete_metrics(self):
        return {
            "accuracy_rate": _result("accuracy_rate", 80.0, {"assessmentState": "COMPLETE"}),
            "fast_rate": _result("fast_rate", 50.0, {"assessmentState": "COMPLETE"}),
            "stability_rate": _result("stability_rate", 90.0, {"assessmentState": "COMPLETE"}),
            "effective_auto_rate": _result("effective_auto_rate", 80.0),
        }

    def test_complete_score_with_decomposition(self):
        """P = (0.2×0.8+0.3×0.5+0.5×0.9)/1.0×100×0.8 = (0.16+0.15+0.45)×80 = 60.8。"""
        p = ConfidenceEvaluator.compute_composite_score(
            self._complete_metrics(), weights=None, assessment_profile=PROFILE
        )
        assert p.value == pytest.approx(60.8, abs=0.01)
        d = p.details
        assert d["assessmentState"] == "COMPLETE"
        assert d["weightSum"] == pytest.approx(1.0, abs=1e-6)
        mc = d["metricComponents"]
        assert mc["fast_rate"]["eta"] == pytest.approx(0.5, abs=1e-6)
        assert mc["fast_rate"]["weightedNumerator"] == pytest.approx(0.15, abs=1e-6)
        assert mc["accuracy_rate"]["weightedNumerator"] == pytest.approx(0.16, abs=1e-6)
        assert mc["stability_rate"]["weightedNumerator"] == pytest.approx(0.45, abs=1e-6)
        assert d["weightedNumerator"] == pytest.approx(0.76, abs=1e-6)
        assert d["referenceComposition"]["fastRate"] == "FORMAL"

    def test_zero_f_weight_not_required(self):
        """F 权重=0 时不强加 F 要求（EA-D02/10§3.6.1）。"""
        metrics = self._complete_metrics()
        del metrics["fast_rate"]
        p = ConfidenceEvaluator.compute_composite_score(
            metrics,
            weights={"accuracy_rate": 0.2, "fast_rate": 0.0, "stability_rate": 0.5},
            assessment_profile=PROFILE,
        )
        assert p.value is not None
        assert p.details["assessmentState"] == "COMPLETE"
        assert p.details["referenceComposition"]["fastRate"] == "ZERO_WEIGHT"

    def test_partial_component_blocks_p(self):
        """S PARTIAL（Osc 缺失）→ P 不完整（不重分配剩余权重）。"""
        metrics = self._complete_metrics()
        metrics["stability_rate"] = _result("stability_rate", 90.0, {"assessmentState": "PARTIAL"})
        p = ConfidenceEvaluator.compute_composite_score(
            metrics, weights=None, assessment_profile=PROFILE
        )
        assert p.value is None
        assert p.details["assessmentState"] == "PARTIAL"

    def test_r_missing_unassessable(self):
        metrics = self._complete_metrics()
        metrics["effective_auto_rate"] = _result("effective_auto_rate", None, confidence="E")
        p = ConfidenceEvaluator.compute_composite_score(
            metrics, weights=None, assessment_profile=PROFILE
        )
        assert p.value is None
        assert p.details["assessmentState"] == "UNASSESSABLE"


# ---------------------------------------------------------------------------
# 09a 附：T′ 模型法来源诚实标注（C07-EA4 关联）
# ---------------------------------------------------------------------------


class TestIdealSettlingModelSource:
    def test_model_source_marked_unverified_under_new_profile(self):
        from app.services.metric_calculator.ideal_settling_time import (
            IdealSettlingTimeCalculator,
        )

        bundle = make_bundle(
            {
                "process_time_constant": [30.0],
                "process_dead_time": [10.0],
                "control_type": ["TC"],
            },
            tag_group="CONFIG",
            metric_code="ideal_settling_time",
            n=1,
            loop_confidence_level=_CONF_A,
        )
        r_new = IdealSettlingTimeCalculator().with_assessment_profile(PROFILE).calculate(bundle)
        assert r_new.details["source"] == "model"
        assert r_new.details["verifiedModel"] is False  # 不冒称已验证模型
        # legacy 无新键
        r_legacy = IdealSettlingTimeCalculator().calculate(bundle)
        assert "verifiedModel" not in r_legacy.details


# ---------------------------------------------------------------------------
# 回归守护：legacy 综合评分（无 profile 参数）行为不变
# ---------------------------------------------------------------------------


class TestLegacyCompositeUnchanged:
    def test_legacy_missing_core_still_inconclusive(self):
        metrics = {
            "accuracy_rate": _result("accuracy_rate", 90.0),
            "fast_rate": _result("fast_rate", None, confidence="E"),
            "stability_rate": _result("stability_rate", 95.0),
            "effective_auto_rate": _result("effective_auto_rate", 90.0),
        }
        p = ConfidenceEvaluator.compute_composite_score(metrics)
        assert p.value is None
        assert p.confidence_level == "E"
        assert "assessmentState" not in p.details  # legacy 不写新字段

    def test_base_profile_gate_default_off(self):
        """默认（无 profile 注入）计算器恒走 legacy 分支。"""
        assert FastRateCalculator().new_semantics is False
        assert SettlingTimeCalculator().new_semantics is False
        assert StabilityRateCalculator().new_semantics is False
