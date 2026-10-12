"""诊断元算子契约与注册表。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §5.1/§5.2

无状态纪律（架构红线）：
1. 算子禁止 import DB/Redis/session，禁止全局缓存——纯 numpy/scipy 计算；
2. 可调参数全部经 threshold_schema 注入（键名与旧引擎 _THRESHOLD_SCHEMA 一致）；
3. 确定性：同输入必同输出（单测与 AI 编排的前提）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# 算子族常量（同族算子做族内 D-S 融合）
FAMILY_OSCILLATION = "oscillation"
FAMILY_STICTION = "stiction"
FAMILY_SENSOR = "sensor"
FAMILY_LINK = "link"
FAMILY_TUNING = "tuning"
FAMILY_DISTURBANCE = "disturbance"
FAMILY_SATURATION = "saturation"

# ---------------------------------------------------------------------------
# 算子评估八态（08§4.1 / D0 契约 §1.6，版本化增量字段——旧
# executed/skip_reason/detected/confidence/error 字段保留兼容，不改动语义）
# ---------------------------------------------------------------------------

ASSESSMENT_POSITIVE = "POSITIVE"
ASSESSMENT_NEGATIVE = "NEGATIVE"
ASSESSMENT_NOT_APPLICABLE = "NOT_APPLICABLE"
ASSESSMENT_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
ASSESSMENT_ERROR = "ERROR"
ASSESSMENT_NOT_RUN = "NOT_RUN"
ASSESSMENT_CANCELLED = "CANCELLED"
ASSESSMENT_LEGACY_UNVERIFIABLE = "LEGACY_UNVERIFIABLE"

#: 八态合法值域（LEGACY_UNVERIFIABLE 仅用于历史记录读端标注，writer 不产出）
ASSESSMENT_STATES: tuple[str, ...] = (
    ASSESSMENT_POSITIVE,
    ASSESSMENT_NEGATIVE,
    ASSESSMENT_NOT_APPLICABLE,
    ASSESSMENT_INSUFFICIENT_DATA,
    ASSESSMENT_ERROR,
    ASSESSMENT_NOT_RUN,
    ASSESSMENT_CANCELLED,
    ASSESSMENT_LEGACY_UNVERIFIABLE,
)

#: NOT_RUN / INSUFFICIENT_DATA 细分 reasonCode 码表（D0 契约 §6 O-4，
#: P3-01 D1/D2 Owner 定稿——值域未在任何文档冻结，此处登记为准）
REASON_UPSTREAM_BLOCKED = "UPSTREAM_BLOCKED"  # NOT_RUN：上游阻断（保留码，当前链路未触发）
REASON_BUDGET_TERMINATED = "BUDGET_TERMINATED"  # NOT_RUN：预算终止（保留码，当前链路未触发）
REASON_GATE_BLOCKED = "GATE_BLOCKED"  # INSUFFICIENT_DATA：L0 数据门禁阻断（所选检查保存阻断原因）
REASON_MISSING_SIGNALS = "MISSING_SIGNALS"  # INSUFFICIENT_DATA：输入信号缺失
REASON_TOO_FEW_POINTS = "TOO_FEW_POINTS"  # INSUFFICIENT_DATA：点数/连续段不足
REASON_NO_LIMIT_CYCLE = "NO_LIMIT_CYCLE"  # NOT_APPLICABLE：极限环前提不成立
REASON_NO_EVENTS = "NO_EVENTS"  # INSUFFICIENT_DATA：无合格响应事件
REASON_NO_EXPECTED_TAU = "NO_EXPECTED_TAU"  # NOT_APPLICABLE：expected τ 无工程来源，不判慢
REASON_DISABLED_BY_CONFIG = "DISABLED_BY_CONFIG"  # 显式排除：诊断配置停用（不进 selected）
REASON_NOT_SELECTED = "NOT_SELECTED"  # 显式排除：不在细选白名单（不进 selected）
REASON_OUT_OF_PROFILE = "OUT_OF_PROFILE"  # profile 外：fast 组未包含（不进 profileExpected）
# D3/D4 增补码（P3-01 D3/D4 子卡 Owner 定稿，沿用 D0 契约 §6 O-4 码表机制）
REASON_RESPONSE_INCOMPLETE = (
    "RESPONSE_INCOMPLETE"  # INSUFFICIENT_DATA：响应窗被截断/到达率不足，响应未完成
)
REASON_MANUAL_MODE = "MANUAL_MODE"  # NOT_APPLICABLE：事件均在手动模式（AUTO 前提不成立）
REASON_OP_SATURATED = "OP_SATURATED"  # NOT_APPLICABLE：事件窗口输出受限（限幅前提不成立）
REASON_INSUFFICIENT_FIT = (
    "INSUFFICIENT_FIT"  # INSUFFICIENT_DATA：拟合信息不足（低相关/R² 低——证据不足不确诊，08§4.1）
)


@dataclass(frozen=True)
class OperatorMeta:
    """元算子自描述元数据（人读 + AI 读，GET /diagnosis/operators 直接序列化）。"""

    name: str
    display_name: str
    family: str  # 见 FAMILY_* 常量
    diag_code: str  # 旧诊断码（阈值分组键，如 OSCILLATION/VALVE_STICTION）
    description: str
    required_signals: tuple[str, ...]  # 输入契约：pv/sp/op/mode/pv_quality
    min_sample_rate: float  # Hz，0 表示不限
    outputs_schema: dict[str, str]  # 特征名 -> 工程含义
    threshold_schema: dict[str, Any]  # 参数名 -> 默认值
    symptom_tags: tuple[str, ...]  # 命中时产出的症状标签（原 8 类症状体系）
    enabled_by_default: bool = True
    fast_group: bool = False  # 是否属于 fast 预设（每族代表算子，共 7 个）
    confidence_basis: str = ""  # 置信度计算口径说明（界面/文档直接展示）


@dataclass
class EvidenceItem:
    """单条证据：特征名/实测值/阈值/判定结论。"""

    feature: str
    value: Any
    threshold: Any = None
    judgment: str = ""


@dataclass
class OperatorInput:
    """算子输入（编排器组装，算子不得自行取数）。"""

    loop_id: str
    signals: dict[str, np.ndarray]  # pv/sp/op/mode/pv_quality 时间序列
    timestamps: np.ndarray  # 秒序列（相对窗起点）
    meta: dict[str, Any]  # sample_interval/pv_range/control_type 等只读上下文
    kpi_context: dict[str, Any] | None = None  # KPI 快照只读上下文


@dataclass
class OperatorResult:
    """算子输出契约。

    D2（08§4.1）：保留既有 executed/skip_reason/detected/confidence/error
    字段语义不变（兼容读端）；新增版本化 assessment_state（八态）与
    reason_code（细分码，见 REASON_* 码表）——旧字段 executed=False +
    skip_reason 的混合语义（跳过/缺数据/异常）由八态精确分离。
    """

    operator: str
    executed: bool  # False = 输入缺失/数据不足/fast 组未包含 跳过
    skip_reason: str | None = None
    detected: bool = False
    confidence: float = 0.0  # 0~1
    features: dict[str, Any] = field(default_factory=dict)
    evidence: list[EvidenceItem] = field(default_factory=list)
    error: str | None = None  # 执行异常信息（executed=False 且非跳过）
    #: 算子评估八态（POSITIVE/NEGATIVE/NOT_APPLICABLE/INSUFFICIENT_DATA/
    #: ERROR/NOT_RUN/CANCELLED）；None=算子在 selected 集合外（profile 外/
    #: 显式排除，不计入覆盖分母八类计数）
    assessment_state: str | None = None
    #: 细分原因码（REASON_* 码表；ERROR 态为异常摘要）
    reason_code: str | None = None


OperatorFunc = Callable[[OperatorInput, dict[str, Any]], OperatorResult]

OPERATOR_REGISTRY: dict[str, tuple[OperatorMeta, OperatorFunc]] = {}


def operator(meta: OperatorMeta) -> Callable[[OperatorFunc], OperatorFunc]:
    """注册装饰器：把无状态纯函数绑定为元算子。"""

    def _register(fn: OperatorFunc) -> OperatorFunc:
        if meta.name in OPERATOR_REGISTRY:
            msg = f"duplicate operator registration: {meta.name}"
            raise ValueError(msg)
        OPERATOR_REGISTRY[meta.name] = (meta, fn)
        return fn

    return _register


def get_operator(name: str) -> tuple[OperatorMeta, OperatorFunc] | None:
    return OPERATOR_REGISTRY.get(name)


def default_thresholds(name: str) -> dict[str, Any]:
    """返回某算子阈值默认值的副本（防外部篡改注册表）。"""

    entry = OPERATOR_REGISTRY.get(name)
    if entry is None:
        return {}
    return dict(entry[0].threshold_schema)


def list_operators() -> list[dict[str, Any]]:
    """序列化注册表（前端算子选择 + AI 工具目录共用）。"""

    out: list[dict[str, Any]] = []
    for meta, _fn in OPERATOR_REGISTRY.values():
        out.append(
            {
                "name": meta.name,
                "displayName": meta.display_name,
                "family": meta.family,
                "diagCode": meta.diag_code,
                "description": meta.description,
                "requiredSignals": list(meta.required_signals),
                "minSampleRate": meta.min_sample_rate,
                "outputsSchema": dict(meta.outputs_schema),
                "thresholdSchema": dict(meta.threshold_schema),
                "symptomTags": list(meta.symptom_tags),
                "enabledByDefault": meta.enabled_by_default,
                "fastGroup": meta.fast_group,
                "confidenceBasis": meta.confidence_basis,
            }
        )
    return out


# ---------------------------------------------------------------------------
# D3/D4 段目录消费 helper（08§3.2 逐段评估——不跨 gap 拼接）
# ---------------------------------------------------------------------------


def segment_bounds(meta: dict[str, Any], n: int) -> list[tuple[int, int]]:
    """读取 D1 段目录（meta["consecutive_segments"]）并校验有效性。

    - 段目录缺失（直调/旧输入）→ 单段全窗 [(0, n)]（与 D1 前行为一致）；
    - 段越界/倒序时剔除非法段；全部非法 → 单段全窗兜底（不虚构空输入）。
    """
    raw = meta.get("consecutive_segments")
    if not raw:
        return [(0, n)] if n > 0 else []
    bounds: list[tuple[int, int]] = []
    for seg in raw:
        try:
            start, end = int(seg[0]), int(seg[1])  # type: ignore[index]
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= start < end <= n:
            bounds.append((start, end))
    return bounds or ([(0, n)] if n > 0 else [])


def merge_segment_states(states: list[str]) -> str:
    """多段态归并（08§3.2 推荐规则：每段单独特征与状态，窗口不外推）。

    优先级：POSITIVE > NEGATIVE > NOT_APPLICABLE；
    无任何可归并态（全为空/INSUFFICIENT）→ INSUFFICIENT_DATA——
    没有任何一段完成有效评估时，窗口不得宣称范围内阴性。
    """
    unique = {s for s in states if s}
    if ASSESSMENT_POSITIVE in unique:
        return ASSESSMENT_POSITIVE
    if ASSESSMENT_NEGATIVE in unique:
        return ASSESSMENT_NEGATIVE
    if ASSESSMENT_NOT_APPLICABLE in unique:
        return ASSESSMENT_NOT_APPLICABLE
    if ASSESSMENT_ERROR in unique and len(unique) == 1:
        return ASSESSMENT_ERROR
    return ASSESSMENT_INSUFFICIENT_DATA
