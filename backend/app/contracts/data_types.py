"""v4.0 核心数据结构定义.

定义预处理 Pipeline 的输入输出数据结构，包括 DataBlock、MetricDataBundle、
DataLineage、QualitySummary 等。所有指标计算器只消费 MetricDataBundle，
不直接查询数据库。

设计依据：
    - 算法说明 §3.4-3.7（预处理规范 / tagGroup / 契约 / 血缘可信度）
    - 数据流程图 §7.5（接口契约定义）
    - PRD §5.5（质量策略 / 异常值 / Mask）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# 枚举类型
# ---------------------------------------------------------------------------


class ControlType(str, Enum):
    """回路控制类型（算法说明 §3.4.4）。

    不同控制类型的物理特性不同，异常值检测阈值差异显著。
    """

    FLOW = "FC"  # 流量
    PRESSURE = "PC"  # 压力
    TEMPERATURE = "TC"  # 温度
    LEVEL = "LC"  # 液位
    COMPOSITION = "CC"  # 成分


class TagGroup(str, Enum):
    """tagGroup 分组（算法说明 §3.5.1）。

    不同指标对采样率的需求不同，通过 tagGroup 分组按需获取数据。
    """

    BASE = "BASE"  # PV/SP/MODE/PV_QUALITY，按控制类型采样
    OP_HF = "OP_HF"  # OP，固定 1s
    PVOP_HF = "PVOP_HF"  # PV+OP，固定 1s
    MODE_HF = "MODE_HF"  # MODE，固定 1s
    QUALITY_HF = "QUALITY_HF"  # PV_QUALITY，固定 1s
    CONFIG = "CONFIG"  # 配置参数，无时序数据


class QualityStatus(str, Enum):
    """质量码三态映射（算法说明 §3.4.2 步骤①）。"""

    GOOD = "Good"
    BAD = "Bad"
    UNKNOWN = "Unknown"


class OutlierReason(str, Enum):
    """8 类异常值原因码（算法说明 §3.4.3, PRD §5.5.2）。

    每个异常点可叠加多个原因码。其中 TS_ANOMALY 和 HF_NOISE
    仅标记不置 valid=False（算法说明 §3.4.3 备注）。
    """

    OUT_OF_RANGE = "OUT_OF_RANGE"  # 超量程
    FROZEN = "FROZEN"  # 冻结值
    JUMP = "JUMP"  # 跳变
    SPIKE = "SPIKE"  # 尖峰
    NAN = "NaN"  # NaN/Inf/NULL
    TS_ANOMALY = "TS_ANOMALY"  # 时间戳异常（仅标记）
    QC_BAD = "QC_BAD"  # 质量码异常
    HF_NOISE = "HF_NOISE"  # 高频噪声（仅标记）


class ConfidenceLevel(str, Enum):
    """指标可信度五级（算法说明 §3.7.2）。

    基于有效数据率 valid_rate 自动判定。E 级时 score=NULL，标记 INCONCLUSIVE。
    """

    A = "A"  # valid_rate >= 0.95
    B = "B"  # 0.80 <= valid_rate < 0.95
    C = "C"  # 0.60 <= valid_rate < 0.80
    D = "D"  # 0.20 <= valid_rate < 0.60
    E = "E"  # valid_rate < 0.20 → INCONCLUSIVE


# 仅标记但不置 valid=False 的原因码（算法说明 §3.4.3）
MARK_ONLY_REASONS: frozenset[str] = frozenset(
    {OutlierReason.TS_ANOMALY.value, OutlierReason.HF_NOISE.value}
)


# ---------------------------------------------------------------------------
# 输入数据结构
# ---------------------------------------------------------------------------


@dataclass
class TimeWindow:
    """评估时间窗口。

    Attributes:
        start: 起始时间（含）
        end: 结束时间（含）
    """

    start: datetime
    end: datetime


@dataclass
class RawTimeSeries:
    """原始时序数据（来自 TDengine 查询结果）。

    所有信号共享同一时间轴（同一 tagGroup 内天然对齐）。

    Attributes:
        timestamps: 时间戳序列（升序）
        signals: 信号值字典，如 ``{"pv": [...], "sp": [...], "op": [...]}``
        quality_codes: 质量码字典，如 ``{"pv_quality": [1, 1, 0, ...]}``，
            缺省时视为全部 Good
    """

    timestamps: list[datetime]
    signals: dict[str, list[Any]]
    quality_codes: dict[str, list[int]] = field(default_factory=dict)


@dataclass
class LoopPreprocessConfig:
    """回路预处理配置。

    Attributes:
        loop_id: 回路 ID
        control_type: 控制类型（决定阈值表）
        range_min: 量程下限（归一化 + 超量程检测用）
        range_max: 量程上限
        config_version: 配置版本号（缓存失效依据）
    """

    loop_id: str
    control_type: ControlType
    range_min: float
    range_max: float
    config_version: str = "v1"


# ---------------------------------------------------------------------------
# 输出数据结构
# ---------------------------------------------------------------------------


@dataclass
class QualitySummary:
    """数据质量摘要（算法说明 §3.4.2 步骤⑧）。

    Attributes:
        total_count: 总数据点数
        valid_count: 有效点数（valid=True）
        bad_count: 无效点数（valid=False）
        missing_count: 缺失点数（时间戳缺口）
        valid_rate: 有效数据率 0~1（valid_count / total_count）
        bad_rate: 无效率 0~1
        missing_rate: 缺失率 0~1
        good_value_rate: PV 质量码为 Good 的时长占比 0~1（仅 QUALITY_HF 计算）
    """

    total_count: int = 0
    valid_count: int = 0
    bad_count: int = 0
    missing_count: int = 0
    valid_rate: float = 0.0
    bad_rate: float = 0.0
    missing_rate: float = 0.0
    good_value_rate: float | None = None


@dataclass
class DataBlock:
    """预处理后的标准化数据块（按 tagGroup 分组）。

    KEEP_ALL_WITH_VALIDITY 策略：不删除任何数据点，通过 validity 标记区分有效/无效。
    指标计算器通过 Metric Validity Mask 决定哪些点参与计算。

    设计依据：数据流程图 §7.5, 算法说明 §3.4.1

    Attributes:
        data_block_id: 唯一标识，格式 ``db_{loopId}_{tagGroup}_{freq}``
        loop_id: 回路 ID
        tag_group: 所属 tagGroup
        sampling_freq: 实际采样频率，如 ``"1s"`` / ``"5s"``
        timestamps: 时间戳序列
        signals: 信号值字典（归一化后的百分比或原始值）
        validity: 有效性标记字典，key 为 ``{tag}_valid``，value 为 bool 列表
        outlier_reasons: 每个点的异常原因码列表，key 为 tag，value 为 list[list[str]]
        quality_summary: 质量摘要
        consecutive_segments: 连续有效段索引列表 ``[(start_idx, end_idx), ...]``
        config_version: 回路配置版本（缓存失效依据）
        preprocess_version: 预处理版本
        point_count: 数据点数
    """

    data_block_id: str
    loop_id: str
    tag_group: str
    sampling_freq: str
    timestamps: list[datetime]
    signals: dict[str, list[Any]]
    validity: dict[str, list[bool]]
    outlier_reasons: dict[str, list[list[str]]] = field(default_factory=dict)
    quality_summary: QualitySummary = field(default_factory=QualitySummary)
    consecutive_segments: list[tuple[int, int]] = field(default_factory=list)
    config_version: str = "v1"
    preprocess_version: str = "pre_v1"
    point_count: int = 0

    def __post_init__(self) -> None:
        if not self.point_count and self.timestamps:
            self.point_count = len(self.timestamps)


@dataclass
class DataLineage:
    """数据血缘（随指标结果一起存储，支持审计追溯）。

    设计依据：算法说明 §3.7.1, FDS §5.3.10

    Attributes:
        sampling_freq: 实际采样频率
        aggregation_policy: 聚合策略（LAST / MEAN / MAX）
        quality_policy: 质量策略（KEEP_ALL_WITH_VALIDITY / KEEP_ALL）
        tag_group: 数据来源 tagGroup
        data_block_ids: 使用的 DataBlock ID 列表
        valid_rate: 有效数据率 0~1
        data_policy_version: 预处理版本（如 ``pre_v1``）
        algorithm_version: 算法版本（如 ``KPI_CALC_v2.0``）
    """

    sampling_freq: str = ""
    aggregation_policy: str = ""
    quality_policy: str = ""
    tag_group: str = ""
    data_block_ids: list[str] = field(default_factory=list)
    valid_rate: float = 0.0
    data_policy_version: str = "pre_v1"
    algorithm_version: str = "KPI_CALC_v2.0"

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 可存储的字典（写入 kpi_snapshot_hourly.data_lineage）。"""
        return {
            "sampling_freq": self.sampling_freq,
            "aggregation_policy": self.aggregation_policy,
            "quality_policy": self.quality_policy,
            "tag_group": self.tag_group,
            "data_block_ids": self.data_block_ids,
            "valid_rate": self.valid_rate,
            "data_policy_version": self.data_policy_version,
            "algorithm_version": self.algorithm_version,
        }


@dataclass
class MetricDataBundle:
    """指标计算数据包（指标计算器只消费此对象）。

    设计依据：数据流程图 §7.5

    Attributes:
        metric_code: 指标代码，如 ``"accuracy_rate"``
        data_block: 数据块引用
        mask_expression: 有效性掩码表达式，如 ``"pv_valid && sp_valid"``
        masked_indices: 应用 mask 后的有效索引列表
        lineage: 数据血缘
    """

    metric_code: str
    data_block: DataBlock
    mask_expression: str
    masked_indices: list[int]
    lineage: DataLineage


@dataclass
class MetricResult:
    """指标计算结果（含数据血缘和可信度）。

    设计依据：数据流程图 §7.5

    Attributes:
        metric_code: 指标代码
        value: 指标值，``None`` 表示 INCONCLUSIVE
        confidence_level: 可信度等级 A/B/C/D/E
        lineage: 数据血缘
        details: 指标特定的详细信息
    """

    metric_code: str
    value: float | None
    confidence_level: str
    lineage: DataLineage
    details: dict[str, Any] = field(default_factory=dict)
