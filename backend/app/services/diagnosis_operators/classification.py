"""症状→原因分类映射层（纯函数）。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §3.1 / §7.2 / §7.3 / §7.4
输入：症状级融合结果（按 symptom_tag 分组族内融合）+ KPI 只读上下文 + 门禁结论
输出：主分类（唯一）+ 次分类（≤2）+ 待复核（污染链降级）+ 判定依据 + 处置建议 + 严重度

证据污染链（分类粒度声明，MVP 简化）：
    仪表 ──污染──> 阀门 / 工艺外扰 / 参数   （PV 失真 → 下游判定失真）
    阀门 ──污染──> 参数                    （粘滞限幅振荡 → 超调/衰减比失真）
    工艺外扰 ──污染──> 参数                （扰动污染阶跃响应特征）
    投用 = 独立维度（MODE 统计），不参与污染
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.diagnosis_operators.base import OperatorResult
from app.services.diagnosis_operators.fusion import FamilyFusion
from app.services.diagnosis_operators.gate import GateResult

# ---------------------------------------------------------------------------
# 分类常量（7 类，见设计 §3.1）
# ---------------------------------------------------------------------------

TUNING = "TUNING"
VALVE = "VALVE"
INSTRUMENT = "INSTRUMENT"
PROCESS = "PROCESS"
UTILIZATION = "UTILIZATION"
DESIGN = "DESIGN"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"

CATEGORY_LABELS: dict[str, str] = {
    TUNING: "参数问题（PID 整定）",
    VALVE: "阀门/执行机构问题",
    INSTRUMENT: "仪表/测量问题",
    PROCESS: "工艺/外扰问题",
    UTILIZATION: "投用/操作问题",
    DESIGN: "组态/设计问题",
    DATA_INSUFFICIENT: "数据不足/无法判定",
}

CATEGORY_DIRECTIONS: dict[str, str] = {
    TUNING: "重新整定参数",
    VALVE: "检修/更换配件",
    INSTRUMENT: "校验/维护",
    PROCESS: "工艺分析/前馈/解耦",
    UTILIZATION: "恢复自动投用",
    DESIGN: "重新组态/改造",
    DATA_INSUFFICIENT: "先补齐数据",
}

#: 证据污染链：主因 → 其证据所污染的下游分类
CONTAMINATION_MAP: dict[str, tuple[str, ...]] = {
    INSTRUMENT: (VALVE, PROCESS, TUNING),
    VALVE: (TUNING,),
    PROCESS: (TUNING,),
}

#: 次分类纳入置信度门槛
SECONDARY_MIN_CONFIDENCE = 0.6
#: 投用前置建议阈值（自动投用率低于此值时"恢复自动投用"升至第 2 位）
UTILIZATION_PRIORITY_RATE = 0.3
#: 投用问题判定阈值（决策表级 6）
UTILIZATION_DETECT_RATE = 0.5


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------


@dataclass
class CategoryJudgement:
    category: str
    confidence: float
    basis: list[str] = field(default_factory=list)
    status: str = "primary"  # primary | secondary | pending_review
    contamination_note: str | None = None

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "categoryLabel": CATEGORY_LABELS.get(self.category, self.category),
            "confidence": round(self.confidence, 4),
            "basis": self.basis,
            "status": self.status,
            "contaminationNote": self.contamination_note,
        }


@dataclass
class Recommendation:
    content: str
    basis: str
    direction: str
    priority: int  # 1 最高

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "basis": self.basis,
            "direction": self.direction,
            "priority": self.priority,
        }


@dataclass
class ClassificationResult:
    primary: CategoryJudgement | None
    secondary: list[CategoryJudgement] = field(default_factory=list)
    pending_review: list[CategoryJudgement] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    severity: str | None = None  # HIGH | MEDIUM | LOW | None

    def to_dict(self) -> dict:
        return {
            "primary": self.primary.to_dict() if self.primary else None,
            "secondary": [j.to_dict() for j in self.secondary],
            "pendingReview": [j.to_dict() for j in self.pending_review],
            "rationale": self.rationale,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "severity": self.severity,
        }


# ---------------------------------------------------------------------------
# 判定辅助
# ---------------------------------------------------------------------------


def _feature(op_results: dict[str, OperatorResult], name: str, key: str, default: Any = 0.0) -> Any:
    res = op_results.get(name)
    if res is None or not res.executed:
        return default
    return res.features.get(key, default)


def _sensor_basis(op_results: dict[str, OperatorResult]) -> list[str]:
    basis: list[str] = []
    sub = _feature(op_results, "sensor_fault", "sensor_subtype", None)
    if sub:
        basis.append(f"传感器故障子类型 {sub}")
    pattern = _feature(op_results, "quality_code_rules", "quality_pattern", None)
    if pattern and pattern != "NORMAL":
        basis.append(f"质量码模式 {pattern}")
    bad_rate = _feature(op_results, "quality_code_rules", "bad_rate", 0.0)
    if bad_rate:
        basis.append(f"Bad 质量码占比 {bad_rate:.1%}")
    return basis or ["仪表族检测命中"]


def _stiction_basis(fusion: FamilyFusion, op_results: dict[str, OperatorResult]) -> list[str]:
    names = "、".join(c["operator"] for c in fusion.contributors)
    idx = _feature(op_results, "stiction_ellipse", "stiction_index", None)
    basis = [f"粘滞算子命中：{names}（融合置信 {fusion.confidence:.2f}）"]
    if idx is not None:
        basis.append(f"椭圆拟合粘滞指数 {float(idx):.3f}")
    return basis


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def classify(
    fusions: dict[str, FamilyFusion],
    op_results: dict[str, OperatorResult],
    kpi_context: dict[str, Any],
    gate: GateResult,
) -> ClassificationResult:
    """按决策表（优先级自上而下，命中即定主因）执行原因分类。

    fusions 键为症状标签（OSCILLATION/VALVE_STICTION/QUALITY_ABNORMAL/
    OVERAGGRESSIVE/OVERCONSERVATIVE/EXTERNAL_DISTURBANCE/OUTPUT_SATURATION）。
    """

    result = ClassificationResult(primary=None)
    auto_rate = float(kpi_context.get("auto_rate_avg") or 0.0)
    score_avg = kpi_context.get("score_avg")

    osc = fusions.get("OSCILLATION")
    stiction = fusions.get("VALVE_STICTION")
    sensor = fusions.get("QUALITY_ABNORMAL")
    over = fusions.get("OVERAGGRESSIVE")
    under = fusions.get("OVERCONSERVATIVE")
    dist = fusions.get("EXTERNAL_DISTURBANCE")

    # 候选池：命中项按决策表优先级排序（level, category, confidence, basis, rec_content）
    candidates: list[dict[str, Any]] = []

    # 级 0：数据门禁不过 → 数据不足（不执行算子，直接短路）
    if not gate.passed:
        result.primary = CategoryJudgement(
            category=DATA_INSUFFICIENT,
            confidence=0.0,
            basis=[gate.reason or "数据质量不足以支撑诊断"],
        )
        result.rationale.append(f"数据门禁未通过：{gate.reason}")
        result.recommendations = [
            Recommendation(
                content="先通过数据管理→历史数据导入补齐该时间窗数据，再重新发起诊断",
                basis=gate.reason or "数据质量不足",
                direction=CATEGORY_DIRECTIONS[DATA_INSUFFICIENT],
                priority=1,
            )
        ]
        result.severity = None
        return result

    # 级 1：仪表族命中 → INSTRUMENT
    if sensor is not None and sensor.detected:
        candidates.append(
            {
                "category": INSTRUMENT,
                "confidence": sensor.confidence,
                "basis": _sensor_basis(op_results),
                "rec": "检查校验变送器/仪表与通信链路（修复后复诊确认下游结论）",
            }
        )

    # 级 2：粘滞族融合置信 ≥0.7 或 ≥2 算子命中 → VALVE
    if stiction is not None and stiction.detected:
        if stiction.confidence >= 0.7 or len(stiction.contributors) >= 2:
            candidates.append(
                {
                    "category": VALVE,
                    "confidence": stiction.confidence,
                    "basis": _stiction_basis(stiction, op_results),
                    "rec": "检查阀门执行机构，清洁或更换阀门填料",
                }
            )

    # 级 3：OP 长期贴限（>80% 时间）→ VALVE（阀容量方向）
    sat_rate = float(_feature(op_results, "output_saturation", "saturation_rate", 0.0))
    if sat_rate > 0.8:
        candidates.append(
            {
                "category": VALVE,
                "confidence": min(1.0, sat_rate),
                "basis": [f"OP {sat_rate:.0%} 时间贴工程限位，疑阀容量不足或积分饱和"],
                "rec": "检查阀门容量/选型，排查积分饱和",
            }
        )

    # 级 4：外扰命中，或（振荡且无粘滞且无过激）→ PROCESS
    osc_detected = osc is not None and osc.detected
    stiction_detected = stiction is not None and stiction.detected
    over_detected = over is not None and over.detected
    if (dist is not None and dist.detected) or (
        osc_detected and not stiction_detected and not over_detected
    ):
        basis = []
        if dist is not None and dist.detected:
            freq = _feature(op_results, "disturbance_burst", "shift_frequency", 0.0)
            basis.append(f"偏差确认突变 {freq:.1f} 次/h 且与 SP 变更无关")
        if osc_detected:
            basis.append("存在振荡且无粘滞/过激证据，疑外部传入或回路耦合")
        candidates.append(
            {
                "category": PROCESS,
                "confidence": dist.confidence if dist is not None and dist.detected else 0.5,
                "basis": basis,
                "rec": "排查上游工艺扰动与相邻回路耦合，考虑前馈控制/解耦",
            }
        )

    # 级 5：过激/过保守命中 → TUNING
    if over_detected or (under is not None and under.detected):
        basis = []
        if over_detected:
            ov = float(_feature(op_results, "step_response_overshoot", "overshoot", 0.0))
            dr = float(_feature(op_results, "step_response_overshoot", "decay_ratio", 0.0))
            basis.append(f"阶跃响应过激：超调 {ov:.0%}、衰减比 {dr:.2f}")
        if under is not None and under.detected:
            ratio = float(_feature(op_results, "slow_response", "ratio", 0.0))
            basis.append(f"响应迟缓：实际/期望时间常数比 {ratio:.1f}")
        candidates.append(
            {
                "category": TUNING,
                "confidence": max(
                    over.confidence if over is not None else 0.0,
                    under.confidence if under is not None else 0.0,
                ),
                "basis": basis,
                "rec": "按证据方向重新整定：过激减小 Kp/增大 Ti，保守增大 Kp/减小 Ti（参考 IMC）",
            }
        )

    # 级 6：自动投用率 <50% → UTILIZATION（独立维度）
    utilization_hit = 0.0 < auto_rate < UTILIZATION_DETECT_RATE
    if utilization_hit:
        candidates.append(
            {
                "category": UTILIZATION,
                "confidence": min(0.95, 1.0 - auto_rate),
                "basis": [f"时间窗内自动投用率 {auto_rate:.0%}，长期手动"],
                "rec": "排查长期手动原因，恢复自动投用后再复诊（其余诊断结论在手动模式下意义有限）",
            }
        )

    # 级 7：兜底
    if not candidates:
        result.primary = CategoryJudgement(
            category=DATA_INSUFFICIENT,
            confidence=0.0,
            basis=["各算子置信度均低于判定门槛，无法归因"],
        )
        result.rationale.append("全部症状未命中，输出兜底分类")
        result.recommendations = [
            Recommendation(
                content="建议人工分析或更换时间窗后重新发起诊断",
                basis="各算子置信度均低于判定门槛",
                direction=CATEGORY_DIRECTIONS[DATA_INSUFFICIENT],
                priority=1,
            )
        ]
        return result

    # ---- 主分类 = 最高优先级候选（candidates 已按决策表顺序 append） ----
    primary_cand = candidates[0]
    primary_category = primary_cand["category"]
    result.primary = CategoryJudgement(
        category=primary_category,
        confidence=float(primary_cand["confidence"]),
        basis=list(primary_cand["basis"]),
        status="primary",
    )
    primary_label = CATEGORY_LABELS[primary_category]
    primary_conf = float(primary_cand["confidence"])
    result.rationale.append(
        f"主分类 {primary_label}（置信 {primary_conf:.2f}）："
        + "；".join(str(b) for b in primary_cand["basis"])
    )

    # ---- 其余命中：污染链降级 / 次分类 ----
    contaminated = CONTAMINATION_MAP.get(primary_category, ())
    secondary_pool: list[dict[str, Any]] = []
    for cand in candidates[1:]:
        cat = cand["category"]
        if cat in contaminated:
            note = (
                f"主因{CATEGORY_LABELS[primary_category]}的证据链污染了{CATEGORY_LABELS[cat]}判定，"
                f"修复主因后复诊确认"
            )
            result.pending_review.append(
                CategoryJudgement(
                    category=cat,
                    confidence=float(cand["confidence"]),
                    basis=list(cand["basis"]),
                    status="pending_review",
                    contamination_note=note,
                )
            )
            result.rationale.append(f"疑似{CATEGORY_LABELS[cat]}——被主因证据污染，转待复核")
        elif float(cand["confidence"]) >= SECONDARY_MIN_CONFIDENCE:
            secondary_pool.append(cand)
        else:
            result.rationale.append(
                f"{CATEGORY_LABELS[cat]}命中但置信 {float(cand['confidence']):.2f} "
                f"< {SECONDARY_MIN_CONFIDENCE}，不纳入次分类"
            )

    # 同分类去重（如阀门两级同时命中），保留置信最高
    seen: dict[str, dict[str, Any]] = {}
    for cand in secondary_pool:
        if (
            cand["category"] not in seen
            or cand["confidence"] > seen[cand["category"]]["confidence"]
        ):
            seen[cand["category"]] = cand
    result.secondary = [
        CategoryJudgement(
            category=c["category"],
            confidence=float(c["confidence"]),
            basis=list(c["basis"]),
            status="secondary",
        )
        for c in list(seen.values())[:2]
    ]

    # ---- 处置建议排序（设计 §7.4 R1-R5） ----
    result.recommendations.append(
        Recommendation(
            content=str(primary_cand["rec"]),
            basis="；".join(str(b) for b in primary_cand["basis"]),
            direction=CATEGORY_DIRECTIONS[primary_category],
            priority=1,
        )
    )
    next_priority = 2
    if (
        utilization_hit
        and auto_rate < UTILIZATION_PRIORITY_RATE
        and primary_category != UTILIZATION
    ):
        result.recommendations.append(
            Recommendation(
                content="优先恢复自动投用（当前自动率过低，其他处置与复诊在手动模式下意义有限）",
                basis=f"自动投用率 {auto_rate:.0%}",
                direction=CATEGORY_DIRECTIONS[UTILIZATION],
                priority=2,
            )
        )
        next_priority = 3
    for c in seen.values():
        if c["category"] == primary_category:
            continue
        result.recommendations.append(
            Recommendation(
                content=str(c["rec"]),
                basis="；".join(str(b) for b in c["basis"]),
                direction=CATEGORY_DIRECTIONS[c["category"]],
                priority=next_priority,
            )
        )
        next_priority += 1
    # R4：待复核项不生成处置建议，只保留复诊指引（pending_review 的 contamination_note 即指引）

    # ---- 严重度（设计 §7.4） ----
    result.severity = _severity(score_avg, result.primary.confidence, primary_category)

    return result


def _severity(score_avg: float | None, primary_conf: float, category: str) -> str:
    if category == DATA_INSUFFICIENT:
        return "LOW"
    score = float(score_avg) if score_avg is not None else None
    if score is not None and score < 40 and primary_conf >= 0.8:
        return "HIGH"
    if (score is not None and score < 60) or primary_conf >= 0.6:
        return "MEDIUM"
    return "LOW"
