"""诊断前置数据门禁。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §4.3 / §7.2 级 0
数据门禁＝消费日常监测层（每小时 KPI 计算链路）已有的质量结论，
快速判断该时间窗数据能否支撑诊断；不过关直接输出 DATA_INSUFFICIENT，
不执行算子（正常完成，不算任务失败）。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 最小有效数据点数（与引擎 get_trigger_config().min_data_points 默认值一致）
MIN_DATA_POINTS = 32
#: 最大断点比例（1 - 实际点数/应有点数）
MAX_GAP_RATIO = 0.3


@dataclass
class GateResult:
    passed: bool
    point_count: int
    expected_points: int
    valid_rate: float
    confidence_level: str
    gap_ratio: float
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "pointCount": self.point_count,
            "expectedPoints": self.expected_points,
            "validRate": round(self.valid_rate, 4),
            "confidenceLevel": self.confidence_level,
            "gapRatio": round(self.gap_ratio, 4),
            "reason": self.reason,
        }


def evaluate_gate(
    point_count: int,
    expected_points: int,
    valid_rate: float,
    confidence_level: str,
) -> GateResult:
    """门禁三条件：点数充足 / 可信度非 E 级 / 断点比例 ≤30%。"""

    gap_ratio = 1.0 - (point_count / expected_points) if expected_points > 0 else 1.0
    gap_ratio = max(0.0, min(1.0, gap_ratio))

    result = GateResult(
        passed=True,
        point_count=point_count,
        expected_points=expected_points,
        valid_rate=valid_rate,
        confidence_level=confidence_level,
        gap_ratio=gap_ratio,
    )

    if point_count < MIN_DATA_POINTS:
        result.passed = False
        result.reason = f"有效数据点 {point_count} 不足（门槛 {MIN_DATA_POINTS} 点）"
    elif confidence_level == "E":
        result.passed = False
        result.reason = f"数据可信度 E 级（valid_rate={valid_rate:.2%}），不足以支撑诊断"
    elif gap_ratio > MAX_GAP_RATIO:
        result.passed = False
        result.reason = f"断点比例 {gap_ratio:.0%} 超过 {MAX_GAP_RATIO:.0%} 门槛"

    return result
