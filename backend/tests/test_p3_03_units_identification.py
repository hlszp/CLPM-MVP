"""P3-03 B1 单位基础+辨识原点（2026-10-11 任务书 B1 节）.

覆盖（03 号文 P3-03 卡验收 + 任务书 §4 核验）：
- 辨识原点：AREA/IPDT 只处理真实阶跃后响应；前基线 5/20 分钟不改 θ；
  与独立响应参考一致（合成已知真系统 + 独立解析参考，禁自证 oracle）
- DCS⇄标准 PID 贯通：PB/增益、秒/分钟双向往返；工程事实驱动转换；
  未知 DCS 表示显式拒绝（不默认标准 kp/ti/td）
- K 单位显式换算层（tuning_units）：归一化 0~100 ↔ 工程量纲；
  量程改单位后的等价标准 PID；缺量程显式未知
- R3 资格分立映射表：历史分析资格 × PID 应用资格 全组合覆盖
- B1-5 供数：A 容限 / T′ / 实际 OP 限位（值/来源/单位/解释版本）
- B1-6 modelRole：最佳解释模型与可整定模型字段级区分（SOPDT/IPDT
  可整定限制保留——P3-07 未过前不放开）
- R1 载体：时滞物理秒边界参数（入参校验 + 结构化记录）
"""

from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.engineering_facts import (
    CAPABILITY_PASSIVE_HISTORY_IDENTIFICATION,
    CAPABILITY_PID_APPLICATION,
    HISTORY_STATE_ELIGIBLE,
    HISTORY_STATE_INELIGIBLE,
    PID_APP_STATE_BLOCKED,
    PID_APP_STATE_PASS,
    PID_APP_STATE_UNKNOWN,
    QUALIFICATION_MAPPING,
    QUALIFICATION_MAPPING_VERSION,
    get_loop_qualifications,
    get_qualification_mapping,
    resolve_assessment_supply_facts,
)
from app.services.pid_conversion import (
    DcsPid,
    PidRepresentationUnknownError,
    StandardPid,
    dcs_pid_from_standard_facts,
    standard_pid_from_facts,
)
from app.services.tuning_algorithms import identify_fopdt, identify_ipdt
from app.services.tuning_identification import identify_from_history
from app.services.tuning_identification.types import (
    AlgorithmConfidenceLevel,
    CandidateModel,
    IdentificationResult,
    IdentifyMethod,
    ModelParams,
    ModelType,
)
from app.services.tuning_units import (
    BASIS_ENGINEERING,
    BASIS_NORMALIZED,
    BASIS_TAG_STORED,
    SignalSpan,
    UnknownConversion,
    convert_gain,
    equivalent_standard_pid,
    normalized_span,
    spans_from_engineering_facts,
    value_from_normalized,
    value_to_normalized,
)


def asyncio_run(coro):
    """同步测试中执行协程（与 test_p2_04 同款）."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 独立解析参考（oracle 纪律：合成已知真系统 + 独立手算公式，禁被测实现自产）
# ---------------------------------------------------------------------------


def _analytic_fopdt_step_with_prebaseline(
    K: float,
    tau: float,
    theta: float,
    mv_step: float,
    post_duration: float,
    prebaseline_seconds: float = 0.0,
    dt: float = 1.0,
    pv0: float = 0.0,
) -> tuple[list[float], list[float]]:
    """独立解析 FOPDT 阶跃响应（含可选前基线负时间）.

    y(t) = pv0 + K·ΔMV·(1 − exp(−(t−θ)/τ))，t ≥ θ（t=0 为 MV 阶跃时刻）；
    前基线段（t<0）平坦于 pv0。解析式独立于被测辨识实现（非其内部仿真）。
    """
    pv: list[float] = []
    ts: list[float] = []
    n_pre = int(round(prebaseline_seconds / dt))
    for i in range(n_pre):
        ts.append(-(n_pre - i) * dt)
        pv.append(pv0)
    n_post = int(round(post_duration / dt))
    for i in range(n_post + 1):
        t = i * dt
        ts.append(t)
        pv.append(pv0 + K * mv_step * (1.0 - math.exp(-(t - theta) / tau)) if t >= theta else pv0)
    return pv, ts


def _analytic_ipdt_step_with_prebaseline(
    K: float,
    theta: float,
    mv_step: float,
    post_duration: float,
    prebaseline_seconds: float = 0.0,
    dt: float = 1.0,
    pv0: float = 0.0,
) -> tuple[list[float], list[float]]:
    """独立解析 IPDT 阶跃响应：y(t) = pv0 + K·ΔMV·(t−θ)，t ≥ θ."""
    pv: list[float] = []
    ts: list[float] = []
    n_pre = int(round(prebaseline_seconds / dt))
    for i in range(n_pre):
        ts.append(-(n_pre - i) * dt)
        pv.append(pv0)
    n_post = int(round(post_duration / dt))
    for i in range(n_post + 1):
        t = i * dt
        ts.append(t)
        pv.append(pv0 + K * mv_step * (t - theta) if t >= theta else pv0)
    return pv, ts


# ---------------------------------------------------------------------------
# B1-3 辨识原点：前基线不改 θ；AREA/IPDT 与独立响应参考一致
# ---------------------------------------------------------------------------


class TestIdentificationOrigin:
    """AREA/IPDT 只处理真实阶跃后响应；θ 从 MV 阶跃时刻起算."""

    K_TRUE, TAU_TRUE, THETA_TRUE = 0.8, 40.0, 10.0
    MV_STEP = 12.0

    def _area_with_prebaseline(self, prebaseline: float) -> dict:
        pv, ts = _analytic_fopdt_step_with_prebaseline(
            self.K_TRUE,
            self.TAU_TRUE,
            self.THETA_TRUE,
            self.MV_STEP,
            post_duration=400.0,
            prebaseline_seconds=prebaseline,
        )
        return identify_fopdt(pv, ts, self.MV_STEP, method="AREA")

    @pytest.mark.parametrize("prebaseline", [0.0, 300.0, 1200.0])
    def test_area_theta_unaffected_by_prebaseline(self, prebaseline: float):
        """前基线 0 / 5 分钟 / 20 分钟：AREA 的 θ/τ/K 保持一致（前基线不改 θ）."""
        result = self._area_with_prebaseline(prebaseline)
        assert result["K"] is not None, result
        # 独立解析参考容差（O-A1 无噪档）：K ≤5%、τ ≤10%、θ ≤ max(2·ts, 10%·θ)
        assert abs(result["K"] - self.K_TRUE) / self.K_TRUE <= 0.05
        assert abs(result["tau"] - self.TAU_TRUE) / self.TAU_TRUE <= 0.10
        theta_tol = max(2 * 1.0, 0.10 * self.THETA_TRUE)
        assert abs(result["theta"] - self.THETA_TRUE) <= theta_tol
        # 20 分钟前基线若被计入 θ，θ 将抬高 1200s —— 断言其未发生
        if prebaseline > 0:
            assert result["theta"] < prebaseline / 2

    def test_area_all_prebaselines_agree(self):
        """三种前基线长度之间结果一致（θ 不随前基线漂移）."""
        r0 = self._area_with_prebaseline(0.0)
        r5 = self._area_with_prebaseline(300.0)
        r20 = self._area_with_prebaseline(1200.0)
        assert abs(r0["theta"] - r5["theta"]) <= 2.0
        assert abs(r0["theta"] - r20["theta"]) <= 2.0
        assert abs(r0["tau"] - r20["tau"]) / self.TAU_TRUE <= 0.10

    def test_area_independent_reference(self):
        """AREA 与独立解析参考一致（已知真系统参数恢复）."""
        result = self._area_with_prebaseline(0.0)
        assert result["K"] is not None
        assert result["fitting_score"] > 95.0

    def test_ipdt_theta_unaffected_by_prebaseline(self):
        """IPDT：前基线 0/5/20 分钟 θ 一致且等于真值."""
        K_true, theta_true, mv = 0.05, 8.0, 15.0
        results = []
        for pre in (0.0, 300.0, 1200.0):
            pv, ts = _analytic_ipdt_step_with_prebaseline(
                K_true, theta_true, mv, post_duration=300.0, prebaseline_seconds=pre
            )
            r = identify_ipdt(pv, ts, mv)
            assert r["K"] is not None
            assert abs(r["K"] - K_true) / K_true <= 0.10
            theta_tol = max(2 * 1.0, 0.10 * theta_true)
            assert abs(r["theta"] - theta_true) <= theta_tol
            results.append(r["theta"])
        assert abs(results[0] - results[2]) <= 2.0

    def test_ipdt_independent_reference(self):
        """IPDT 与独立解析参考一致（积分斜率恢复）."""
        K_true, theta_true, mv = 0.05, 8.0, 15.0
        pv, ts = _analytic_ipdt_step_with_prebaseline(K_true, theta_true, mv, post_duration=300.0)
        r = identify_ipdt(pv, ts, mv)
        assert r["fitting_score"] > 95.0
        assert abs(r["K"] - K_true) / K_true <= 0.10

    def test_all_prebaseline_no_post_step_data_fails(self):
        """全窗口都在阶跃前（t<0，含变化的 PV）→ 显式失败，不硬出参数."""
        pv_ramp, ts_neg = [], []
        for i in range(120):
            ts_neg.append(-(120 - i) * 1.0)
            pv_ramp.append(i * 0.5)  # 窗口内有变化（K≠0），但全部在阶跃前
        result = identify_fopdt(pv_ramp, ts_neg, self.MV_STEP, method="AREA")
        assert result["K"] is None
        assert "阶跃时刻" in (result["reason"] or "")
        r_ipdt = identify_ipdt(pv_ramp, ts_neg, self.MV_STEP)
        assert r_ipdt["K"] is None

    def test_fitting_score_not_inflated_by_prebaseline(self):
        """拟合优度只在阶跃后响应段评价（前基线平坦段不虚增 R²）."""
        r0 = self._area_with_prebaseline(0.0)
        r20 = self._area_with_prebaseline(1200.0)
        # 20 分钟前基线若被计入 R²，分数将被平坦段抬高 —— 断言其不被抬高
        assert r20["fitting_score"] <= r0["fitting_score"] + 1.0


# ---------------------------------------------------------------------------
# B1-1 DCS⇄标准 PID：工程事实驱动转换 + 未知表示显式拒绝
# ---------------------------------------------------------------------------


class TestFactsDrivenPidConversion:
    KNOWN_REPR = {
        "status": "KNOWN",
        "modelId": "model-1",
        "modelCode": "supcon-ecs700",
        "pType": "PROPORTION_BAND",
        "iUnit": "MINUTES",
        "dUnit": "SECONDS",
        "dFilterEnabled": False,
        "dFilterUnit": None,
        "dFilterMultiplier": False,
    }

    def test_known_representation_converts(self):
        """KNOWN 表示：PB=50→Kp=2；2 分钟→120 秒；Td 秒直通."""
        pid = standard_pid_from_facts(DcsPid(p=50.0, i=2.0, d=30.0), self.KNOWN_REPR)
        assert math.isclose(pid.kp, 2.0, rel_tol=1e-12)
        assert math.isclose(pid.ti, 120.0, rel_tol=1e-12)
        assert math.isclose(pid.td, 30.0, rel_tol=1e-12)

    def test_round_trip_via_facts(self):
        """标准→DCS→标准 往返（数值内核容差 1e-6 级）."""
        std = StandardPid(kp=1.7, ti=240.0, td=45.0)
        dcs = dcs_pid_from_standard_facts(std, self.KNOWN_REPR)
        assert math.isclose(dcs.p, 100.0 / 1.7, rel_tol=1e-9)
        assert math.isclose(dcs.i, 4.0, rel_tol=1e-9)
        back = standard_pid_from_facts(dcs, self.KNOWN_REPR)
        assert math.isclose(back.kp, std.kp, rel_tol=1e-9)
        assert math.isclose(back.ti, std.ti, rel_tol=1e-9)
        assert math.isclose(back.td, std.td, rel_tol=1e-9)

    def test_unknown_representation_refused(self):
        """UNKNOWN 表示显式拒绝——不默认标准 kp/ti/td（P2-04 口径承接）."""
        unknown_repr = {
            "status": "UNKNOWN",
            "pType": None,
            "iUnit": None,
            "dUnit": None,
            "reason": "回路未绑定 DCS 型号（未知 DCS 型号，不得默认标准 kp/ti/td）",
        }
        with pytest.raises(PidRepresentationUnknownError, match="不得默认标准"):
            standard_pid_from_facts(DcsPid(p=50.0, i=2.0, d=0.0), unknown_repr)
        with pytest.raises(PidRepresentationUnknownError):
            dcs_pid_from_standard_facts(StandardPid(kp=1.0, ti=60.0), unknown_repr)

    def test_known_but_missing_fields_refused(self):
        """status=KNOWN 但 pType/iUnit/dUnit 缺失 → 按未知拒绝（不猜）."""
        broken = {**self.KNOWN_REPR, "pType": None}
        with pytest.raises(PidRepresentationUnknownError, match="数据不一致"):
            standard_pid_from_facts(DcsPid(p=50.0, i=2.0, d=0.0), broken)

    def test_seconds_seconds_passthrough(self):
        """PROPORTION + SECONDS/SECONDS：恒等直通."""
        repr_ss = {**self.KNOWN_REPR, "pType": "PROPORTION", "iUnit": "SECONDS"}
        pid = standard_pid_from_facts(DcsPid(p=2.5, i=90.0, d=10.0), repr_ss)
        assert math.isclose(pid.kp, 2.5, rel_tol=1e-12)
        assert math.isclose(pid.ti, 90.0, rel_tol=1e-12)


# ---------------------------------------------------------------------------
# B1-2 K 单位显式换算层（tuning_units）
# ---------------------------------------------------------------------------


class TestTuningUnits:
    PV = SignalSpan(lower=0.0, upper=1000.0)  # 工程量程 0~1000
    OP = SignalSpan(lower=0.0, upper=100.0)

    def test_gain_normalized_to_engineering_and_back(self):
        """K 归一化域 ⇄ 工程域往返（1e-9 级，严于数值内核 1e-6 容差）."""
        k_eng = 2.5  # ΔPV(工程)/ΔOP(%)
        k_norm = convert_gain(k_eng, self.PV, self.OP, normalized_span(), normalized_span())
        # 独立手算：K_norm = K_eng × (op_span/pv_span) = 2.5 × 100/1000 = 0.25
        assert math.isclose(k_norm, 0.25, rel_tol=1e-12)
        back = convert_gain(k_norm, normalized_span(), normalized_span(), self.PV, self.OP)
        assert math.isclose(back, k_eng, rel_tol=1e-12)

    def test_ten_x_scale_lesson(self):
        """记忆教训反例：量程 0~1000 的 PV 若不换算，K 差 10 倍."""
        k_eng = 1.2
        k_norm = convert_gain(k_eng, self.PV, self.OP, normalized_span(), normalized_span())
        assert not math.isclose(k_norm, k_eng, rel_tol=1e-6)
        assert math.isclose(k_norm * (self.PV.span / self.OP.span), k_eng, rel_tol=1e-12)

    def test_affine_value_round_trip(self):
        """原值 ⇄ 归一化往返；公式与 planner Step③ 同式."""
        v = 375.0
        n = value_to_normalized(v, self.PV)
        assert math.isclose(n, (375.0 - 0.0) / 1000.0 * 100.0, rel_tol=1e-12)
        assert math.isclose(value_from_normalized(n, self.PV), v, rel_tol=1e-12)

    def test_offset_range_conversion(self):
        """非零起点量程（4~20mA OP）仿射换算正确."""
        op_420 = SignalSpan(lower=4.0, upper=20.0)
        k_stored = 0.6  # ΔPV/ΔOP(mA)
        k_norm = convert_gain(k_stored, self.PV, op_420, normalized_span(), normalized_span())
        assert math.isclose(k_norm, 0.6 * (16.0 / 1000.0), rel_tol=1e-12)

    def test_equivalent_standard_pid_on_range_change(self):
        """量程改单位后的等价标准 PID：Kp 随量程比值缩放，Ti/Td 不变.

        Kp 语义为 ΔOP/ΔPV（各自域内数值）：PV 量程翻倍 → 同物理误差的
        数值翻倍 → 等价 Kp 减半（独立手算：0.9 = 1.8×1000/2000）。
        """
        kp, ti, td = 1.8, 120.0, 30.0
        kp2, ti2, td2 = equivalent_standard_pid(
            kp,
            ti,
            td,
            src_pv=self.PV,
            src_op=self.OP,
            dst_pv=SignalSpan(lower=0.0, upper=2000.0),
            dst_op=self.OP,
        )
        assert math.isclose(kp2, kp / 2.0, rel_tol=1e-12)
        assert ti2 == ti and td2 == td
        # OP 域改单位（0~100% → 0~10 工程指令）：等价 Kp 随 op 量程同比缩放
        kp3, _, _ = equivalent_standard_pid(
            kp,
            ti,
            td,
            src_pv=self.PV,
            src_op=self.OP,
            dst_pv=self.PV,
            dst_op=SignalSpan(lower=0.0, upper=10.0),
        )
        assert math.isclose(kp3, kp / 10.0, rel_tol=1e-12)
        # 双向换回复原（等价性闭环）
        kp_back, ti_back, td_back = equivalent_standard_pid(
            kp2,
            ti2,
            td2,
            src_pv=SignalSpan(lower=0.0, upper=2000.0),
            src_op=self.OP,
            dst_pv=self.PV,
            dst_op=self.OP,
        )
        assert math.isclose(kp_back, kp, rel_tol=1e-12)
        assert ti_back == ti and td_back == td

    def test_unknown_span_refused(self):
        """量程缺失/非法 → 显式 UnknownConversion，不默认 0~100 冒充."""
        bad = SignalSpan(lower=0.0, upper=0.0)
        with pytest.raises(UnknownConversion):
            convert_gain(1.0, bad, self.OP, normalized_span(), normalized_span())
        unknown_src = SignalSpan(lower=0.0, upper=100.0, source="UNKNOWN")
        with pytest.raises(UnknownConversion):
            convert_gain(1.0, unknown_src, self.OP, normalized_span(), normalized_span())
        with pytest.raises(UnknownConversion):
            value_to_normalized(1.0, bad)

    def test_spans_from_engineering_facts(self):
        """工程事实 → 量程：tag 量程优先；缺失回落 0~100 显式 DEFAULT_0_100."""
        facts = {
            "pvRange": {"lower": 0.0, "upper": 1000.0},
            "opTagRange": {"lower": None, "upper": None},
        }
        pv_span, op_span = spans_from_engineering_facts(facts)
        assert pv_span.source == "TAG_RANGE" and pv_span.span == 1000.0
        assert op_span.source == "DEFAULT_0_100" and op_span.span == 100.0
        pv_none, op_none = spans_from_engineering_facts(None)
        assert pv_none.source == "DEFAULT_0_100" and op_none.source == "DEFAULT_0_100"

    def test_basis_constants(self):
        """域标识枚举值稳定（API 字段 unitBasis.basis 契约）."""
        assert BASIS_NORMALIZED == "NORMALIZED_0_100"
        assert BASIS_TAG_STORED == "TAG_STORED"
        assert BASIS_ENGINEERING == "ENGINEERING"


# ---------------------------------------------------------------------------
# B1-4 R3 资格分立映射表
# ---------------------------------------------------------------------------


class TestQualificationMapping:
    def test_mapping_table_full_coverage(self):
        """映射表覆盖两资格全部 3×2 组合，无遗漏无重复."""
        combos = {(r["historyIdentification"], r["pidApplication"]) for r in QUALIFICATION_MAPPING}
        expected = {
            (h, p)
            for h in (HISTORY_STATE_ELIGIBLE, HISTORY_STATE_INELIGIBLE)
            for p in (PID_APP_STATE_PASS, PID_APP_STATE_BLOCKED, PID_APP_STATE_UNKNOWN)
        }
        assert combos == expected
        assert len(QUALIFICATION_MAPPING) == 6

    def test_mapping_semantics_r3(self):
        """R3 语义：历史辨识资格不因 PID 应用被阻断而失去；反之亦然."""
        by_combo = {
            (r["historyIdentification"], r["pidApplication"]): r for r in QUALIFICATION_MAPPING
        }
        caps = by_combo[(HISTORY_STATE_ELIGIBLE, PID_APP_STATE_BLOCKED)]["capabilities"]
        assert CAPABILITY_PASSIVE_HISTORY_IDENTIFICATION in caps
        assert CAPABILITY_PID_APPLICATION not in caps
        caps2 = by_combo[(HISTORY_STATE_INELIGIBLE, PID_APP_STATE_PASS)]["capabilities"]
        assert CAPABILITY_PID_APPLICATION in caps2
        assert CAPABILITY_PASSIVE_HISTORY_IDENTIFICATION not in caps2
        # 未知 PID 应用状态按不放行处理（保守，门禁不动）
        caps3 = by_combo[(HISTORY_STATE_ELIGIBLE, PID_APP_STATE_UNKNOWN)]["capabilities"]
        assert CAPABILITY_PID_APPLICATION not in caps3

    def test_get_qualification_mapping_defensive_copy(self):
        rows = get_qualification_mapping()
        rows[0]["capabilities"].append("TAMPERED")
        assert "TAMPERED" not in QUALIFICATION_MAPPING[0]["capabilities"]

    def _db_and_loop(self, has_op: bool = True):
        loop = MagicMock()
        loop.id = "loop-1"
        loop.tag_name = "TIC-101"
        loop.control_action_direction = "UNKNOWN"
        loop.dcs_model_id = None
        rows = [("PV", _tag_row())] + ([("OP", _tag_row())] if has_op else [])
        db = _db_with([_scalar_result(loop), _all_result(rows), _none_result()])
        return db, loop

    def test_qualifications_mapping_node_blocked(self):
        """L1 阻断 + 历史可辨识 → mapping 命中（ELIGIBLE, BLOCKED）行."""
        db, _ = self._db_and_loop()
        with patch(
            "app.services.loop_fitness.get_latest_fitness_per_loop",
            new=AsyncMock(return_value={"loop-1": SimpleNamespace(tune_level_effective="L1")}),
        ):
            quals = asyncio_run(get_loop_qualifications(db, "loop-1"))
        assert quals["mapping"]["version"] == QUALIFICATION_MAPPING_VERSION
        assert quals["mapping"]["historyIdentification"] == HISTORY_STATE_ELIGIBLE
        assert quals["mapping"]["pidApplication"] == PID_APP_STATE_BLOCKED
        assert CAPABILITY_PASSIVE_HISTORY_IDENTIFICATION in quals["mapping"]["capabilities"]
        assert CAPABILITY_PID_APPLICATION not in quals["mapping"]["capabilities"]

    def test_qualifications_mapping_node_pass(self):
        db, _ = self._db_and_loop()
        with patch(
            "app.services.loop_fitness.get_latest_fitness_per_loop",
            new=AsyncMock(return_value={"loop-1": SimpleNamespace(tune_level_effective="L2")}),
        ):
            quals = asyncio_run(get_loop_qualifications(db, "loop-1"))
        assert quals["mapping"]["pidApplication"] == PID_APP_STATE_PASS
        assert CAPABILITY_PID_APPLICATION in quals["mapping"]["capabilities"]

    def test_qualifications_two_fields_independent(self):
        """缺 OP 绑定：历史辨识 INELIGIBLE，PID 应用资格独立判定（UNKNOWN）."""
        db, _ = self._db_and_loop(has_op=False)
        with patch(
            "app.services.loop_fitness.get_latest_fitness_per_loop",
            new=AsyncMock(return_value={}),
        ):
            quals = asyncio_run(get_loop_qualifications(db, "loop-1"))
        assert quals["historyIdentification"]["eligible"] is False
        assert quals["historyIdentification"]["state"] == HISTORY_STATE_INELIGIBLE
        assert quals["pidApplication"]["tuneLevel"] is None
        assert quals["pidApplication"]["state"] == PID_APP_STATE_UNKNOWN
        assert quals["mapping"]["capabilities"] == []


# ---------------------------------------------------------------------------
# B1-5 A 容限 / T′ / 实际 OP 限位供数
# ---------------------------------------------------------------------------


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _none_result():
    return _scalar_result(None)


def _all_result(items: list):
    result = MagicMock()
    result.all.return_value = items
    return result


def _db_with(side_effect):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=side_effect)
    return db


def _tag_row(range_min=0.0, range_max=100.0, unit="%"):
    tag = MagicMock()
    tag.id = "tag-1"
    tag.tag_name = "TI_101"
    tag.range_min = range_min
    tag.range_max = range_max
    tag.unit = unit
    return tag


def _make_loop_row(**kw):
    loop = MagicMock()
    loop.id = kw.get("loop_id", "loop-1")
    loop.tag_name = kw.get("tag_name", "TIC-101")
    loop.control_action_direction = kw.get("direction", "UNKNOWN")
    loop.dcs_model_id = kw.get("dcs_model_id")
    loop.op_output_lower_limit = kw.get("op_lower")
    loop.op_output_upper_limit = kw.get("op_upper")
    loop.ideal_settling_time = kw.get("ideal_settling_time")
    loop.loop_type = kw.get("loop_type", "OTHER")
    loop.engineering_template_id = None
    loop.engineering_template_version = None
    return loop


class TestAssessmentSupplyFacts:
    def _supply(self, loop, tag_rows=None, extra_results=None):
        side_effect = [
            _scalar_result(loop),
            _all_result(
                tag_rows if tag_rows is not None else [("PV", _tag_row()), ("OP", _tag_row())]
            ),
        ]
        if extra_results:
            side_effect.extend(extra_results)
        db = _db_with(side_effect)
        return asyncio_run(resolve_assessment_supply_facts(db, "loop-1"))

    def _template_rows(self, profile: dict):
        """resolve_template_reference 的两次查询（template 行 + version 行）."""
        template = MagicMock()
        template.id = "tpl-1"
        template.template_key = "FAST_RESP"
        template.current_version = 3
        version = MagicMock()
        version.version = 3
        version.profile = dict(profile)
        return [_scalar_result(template), _scalar_result(version)]

    def test_a_tolerance_from_template_profile(self):
        """回路有工程事实（模板 allowedDeviation）→ A 容限实际可读，来源 TEMPLATE."""
        loop = _make_loop_row()
        loop.engineering_template_id = "tpl-1"
        loop.engineering_template_version = 3
        supply = self._supply(
            loop,
            extra_results=self._template_rows(
                {"allowedDeviation": 4.0, "allowedDeviationUnit": "℃"}
            ),
        )
        assert supply["aTolerance"]["value"] == 4.0
        assert supply["aTolerance"]["source"] == "TEMPLATE"
        assert supply["aTolerance"]["unit"] == "℃"
        assert supply["engineeringTemplateRevision"] == "engineering_template:tpl-1@v3"

    def test_a_tolerance_unknown_when_no_facts(self):
        """缺工程事实 → 显式 UNKNOWN + 指引（不默认冒充工艺容限）."""
        supply = self._supply(_make_loop_row())
        assert supply["aTolerance"]["value"] is None
        assert supply["aTolerance"]["source"] == "UNKNOWN"
        assert "不得当作已核实" in supply["aTolerance"]["note"]

    def test_t_prime_priority_loop_field(self):
        """T′ 优先级：回路字段（LOOP_FIELD）> 类型默认（LOOP_TYPE_DEFAULT）."""
        loop = _make_loop_row(ideal_settling_time=90.0, loop_type="TEMPERATURE")
        supply = self._supply(loop)
        assert supply["idealSettlingTime"]["source"] == "LOOP_FIELD"
        assert supply["idealSettlingTime"]["valueSeconds"] == 90.0
        loop2 = _make_loop_row(ideal_settling_time=None, loop_type="TEMPERATURE")
        supply2 = self._supply(loop2)
        assert supply2["idealSettlingTime"]["source"] == "LOOP_TYPE_DEFAULT"
        assert supply2["idealSettlingTime"]["valueSeconds"] == 180.0  # 复用既有默认表

    def test_t_prime_from_template_profile(self):
        loop = _make_loop_row(ideal_settling_time=None, loop_type="OTHER")
        loop.engineering_template_id = "tpl-1"
        loop.engineering_template_version = 3
        supply = self._supply(
            loop, extra_results=self._template_rows({"idealSettlingTimeSeconds": 45.0})
        )
        assert supply["idealSettlingTime"]["source"] == "TEMPLATE"
        assert supply["idealSettlingTime"]["valueSeconds"] == 45.0

    def test_t_prime_model_based_not_supplied(self):
        """模型法 T′ 不在供数（只消费 P3-06 终验通过模型——显式声明）."""
        supply = self._supply(_make_loop_row())
        assert "modelBasedNote" in supply["idealSettlingTime"]
        assert "P3-06" in supply["idealSettlingTime"]["modelBasedNote"]

    def test_op_limits_three_way(self):
        """实际 OP 限位三分：回路字段 > tag 量程 > 默认假设（来源随值+provenance 三分语义）."""
        loop = _make_loop_row(op_lower=5.0, op_upper=95.0)
        supply = self._supply(loop)
        assert supply["opLimits"] == {
            "lower": 5.0,
            "upper": 95.0,
            "source": "LOOP_FIELD",
            "note": "回路工程属性 op_output_lower/upper_limit",
            "provenance": "ACTUAL",
            "unit": "%",
            "interpretationVersion": "1",
        }
        supply2 = self._supply(
            _make_loop_row(),
            tag_rows=[("PV", _tag_row()), ("OP", _tag_row(range_min=4.0, range_max=20.0))],
        )
        assert supply2["opLimits"]["source"] == "TAG_RANGE"
        assert supply2["opLimits"]["lower"] == 4.0
        assert supply2["opLimits"]["provenance"] == "ACTUAL"
        supply3 = self._supply(
            _make_loop_row(),
            tag_rows=[("PV", _tag_row()), ("OP", _tag_row(range_min=None, range_max=None))],
        )
        assert supply3["opLimits"]["source"] == "DEFAULT_0_100"
        assert supply3["opLimits"]["provenance"] == "DEFAULT"

    def test_supply_interpretation_version_and_ranges(self):
        """供数带解释版本；PV/OP 量程随供数（换算层消费）."""
        supply = self._supply(
            _make_loop_row(),
            tag_rows=[("PV", _tag_row(0.0, 1000.0, "℃")), ("OP", _tag_row(0.0, 100.0))],
        )
        assert supply["interpretationVersion"] == "ASSESSMENT_SUPPLY_V1"
        assert supply["pvRange"]["upper"] == 1000.0
        assert supply["opTagRange"]["upper"] == 100.0


# ---------------------------------------------------------------------------
# B1-6 最佳解释模型 vs 可整定模型（字段级）
# ---------------------------------------------------------------------------


def _candidate(model_type: ModelType, score: float, conf: str = "A") -> CandidateModel:
    params = ModelParams(
        model_type=model_type, K=1.0, tau=30.0 if model_type == ModelType.FOPDT else 0.0, theta=5.0
    )
    return CandidateModel(
        params=params,
        fitting_score=score,
        confidence=AlgorithmConfidenceLevel(conf),
        identify_method=IdentifyMethod.HISTORICAL_ARX,
        residual_test_passed=True,
        excitation_score=80.0,
    )


class TestModelRole:
    def test_best_fopdt_is_tunable(self):
        """胜出 FOPDT → tunable=自身（isBestModel=True）."""
        best = _candidate(ModelType.FOPDT, 95.0)
        result = IdentificationResult(success=True, best_model=best, candidates=[best])
        role = result.to_dict()["modelRole"]
        assert role["bestExplanatory"]["modelType"] == "FOPDT"
        assert role["tunable"]["modelType"] == "FOPDT"
        assert role["tunable"]["isBestModel"] is True
        assert "门禁" in role["note"]

    def test_best_sopdt_falls_back_to_fopdt_candidate(self):
        """胜出 SOPDT 不可整定 → tunable 取候选池最优 FOPDT（限制保留）."""
        best = _candidate(ModelType.SOPDT, 97.0)
        fopdt = _candidate(ModelType.FOPDT, 90.0, conf="B")
        result = IdentificationResult(success=True, best_model=best, candidates=[best, fopdt])
        role = result.to_dict()["modelRole"]
        assert role["bestExplanatory"]["modelType"] == "SOPDT"
        assert role["tunable"]["modelType"] == "FOPDT"
        assert role["tunable"]["isBestModel"] is False
        assert "P3-07" in role["note"]

    def test_no_tunable_candidate(self):
        """无 FOPDT 候选 → tunable=null（只作解释模型，不给降级整定）."""
        best = _candidate(ModelType.IPDT, 93.0)
        result = IdentificationResult(success=True, best_model=best, candidates=[best])
        role = result.to_dict()["modelRole"]
        assert role["tunable"] is None
        assert "解释模型" in role["note"]

    def test_failure_result_has_no_model_role(self):
        result = IdentificationResult(success=False, reason="激励不足")
        assert "modelRole" not in result.to_dict()

    def test_tunable_restriction_frozen(self):
        """可整定结构集 = FOPDT（SOPDT/IPDT 限制保留至 P3-07）."""
        assert IdentificationResult.TUNABLE_MODEL_TYPES == ("FOPDT",)


# ---------------------------------------------------------------------------
# R1 载体：时滞物理秒边界
# ---------------------------------------------------------------------------


class TestDelaySearchPhysicalSecondsCarrier:
    def test_invalid_seconds_rejected(self):
        """非法物理秒（0/负/Inf）显式拒绝，不静默忽略."""
        u = [0.0] * 60
        for bad in (0.0, -5.0, float("inf")):
            result = identify_from_history(op=list(u), pv=list(u), delay_search_max_seconds=bad)
            assert result.success is False
            assert "delay_search_max_seconds" in (result.reason or "")

    @staticmethod
    def _prbs_fopdt_pair(n: int = 1000, seed: int = 123):
        """PRBS 输入驱动的 FOPDT 真系统（独立于被测管线的合成 oracle）."""
        rng = np.random.default_rng(seed)
        u = np.ones(n)
        switch_idx = sorted(rng.choice(n, size=n // 10, replace=False))
        sign = 1.0
        prev = 0
        for idx in switch_idx:
            u[prev:idx] = sign * 5.0
            sign = -sign
            prev = idx
        u[prev:] = sign * 5.0
        a = math.exp(-1.0 / 20.0)
        y = np.zeros(n)
        d = 3
        for k in range(d, n):
            y[k] = a * y[k - 1] + 1.5 * (1 - a) * u[k - d]
        return u, y

    def test_carrier_recorded_in_preprocessing(self):
        """物理秒边界生效并记入结构化预处理参数（delaySearchBound）."""
        u, y = self._prbs_fopdt_pair()
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            candidate_models=[ModelType.FOPDT],
            delay_search_max_seconds=30.0,
        )
        assert result.success is True, result.reason
        assert result.preprocessing is not None
        bound = result.preprocessing.get("delaySearchBound")
        assert bound is not None and bound["applied"] is True
        assert bound["maxSeconds"] == 30.0
        # 物理秒 30s @ ts=1 → 候选上限收紧至 30 样本（自样本域 60）
        assert bound["maxSamples"] <= 30
        # θ 恢复不受收紧影响（真 θ=3s 在搜索窗内）
        assert result.best_model is not None
        assert abs(result.best_model.params.theta - 3.0) <= max(2.0, 0.1 * 3.0)

    def test_default_behavior_unchanged(self):
        """不传参数 → 无 delaySearchBound 记录（现行样本域 60 上限零变更）."""
        u, y = self._prbs_fopdt_pair()
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success is True, result.reason
        assert (result.preprocessing or {}).get("delaySearchBound") is None
