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
FAMILY_TUNING = "tuning"
FAMILY_DISTURBANCE = "disturbance"
FAMILY_SATURATION = "saturation"


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
    """算子输出契约。"""

    operator: str
    executed: bool  # False = 输入缺失/数据不足/fast 组未包含 跳过
    skip_reason: str | None = None
    detected: bool = False
    confidence: float = 0.0  # 0~1
    features: dict[str, Any] = field(default_factory=dict)
    evidence: list[EvidenceItem] = field(default_factory=list)
    error: str | None = None  # 执行异常信息（executed=False 且非跳过）


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
            }
        )
    return out
