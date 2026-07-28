"""指标计算器抽象基类（Phase 3 任务 3.2）.

提供所有 12 个指标计算器共享的通用辅助方法：
- 从 MetricDataBundle 提取掩码后的信号值
- 计算指标级有效数据率 valid_rate
- 构建数据血缘 DataLineage
- 构造 MetricResult（含可信度等级）
- INCONCLUSIVE 兜底处理

设计依据：算法说明 §3.6, §3.7.1, §3.7.2；数据流程图 §7.5
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from app.contracts.data_types import (
    ConfidenceLevel,
    DataLineage,
    MetricDataBundle,
    MetricResult,
)
from app.contracts.metric_calculator import MetricCalculator
from app.services.confidence_evaluator import ALGORITHM_VERSION, ConfidenceEvaluator

logger = logging.getLogger(__name__)


class MetricCalculatorBase(MetricCalculator):
    """指标计算器抽象基类，提供通用辅助方法.

    所有 12 个指标计算器继承本类，只需实现 ``metric_code`` 属性和
    ``calculate`` 方法，复用本类的信号提取/可信度判定/结果构造能力。

    设计依据：算法说明 §3.6, §3.7
    """

    # 子类可覆盖：算法版本号（默认与系统对齐）
    algorithm_version: str = ALGORITHM_VERSION

    # ------------------------------------------------------------------
    # 信号提取辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _get_signal_values(bundle: MetricDataBundle, tag_name: str) -> list[Any]:
        """获取 DataBlock 中指定 tag 的全部信号值.

        Args:
            bundle: 指标数据包
            tag_name: 信号名，如 ``"pv"`` / ``"sp"`` / ``"op"`` / ``"mode"``

        Returns:
            信号值列表（未掩码）

        Raises:
            KeyError: 信号不存在
        """
        return list(bundle.data_block.signals.get(tag_name, []))

    @staticmethod
    def _get_masked_values(bundle: MetricDataBundle, tag_name: str) -> list[Any]:
        """获取掩码后（masked_indices）的信号值.

        Args:
            bundle: 指标数据包
            tag_name: 信号名

        Returns:
            masked_indices 对应位置的信号值列表
        """
        signals = bundle.data_block.signals.get(tag_name, [])
        return [signals[i] for i in bundle.masked_indices if i < len(signals)]

    @staticmethod
    def _get_masked_pair(bundle: MetricDataBundle, tag_a: str, tag_b: str) -> list[tuple[Any, Any]]:
        """获取掩码后的两个信号配对值（如 PV-SP）.

        Args:
            bundle: 指标数据包
            tag_a: 第一个信号名
            tag_b: 第二个信号名

        Returns:
            [(value_a, value_b), ...] 配对列表
        """
        sig_a = bundle.data_block.signals.get(tag_a, [])
        sig_b = bundle.data_block.signals.get(tag_b, [])
        pairs: list[tuple[Any, Any]] = []
        for i in bundle.masked_indices:
            if i < len(sig_a) and i < len(sig_b):
                pairs.append((sig_a[i], sig_b[i]))
        return pairs

    @staticmethod
    def _get_timestamps(bundle: MetricDataBundle) -> list[Any]:
        """获取 DataBlock 的时间戳列表."""
        return list(bundle.data_block.timestamps)

    @staticmethod
    def _get_masked_timestamps(bundle: MetricDataBundle) -> list[Any]:
        """获取掩码后的时间戳列表."""
        ts = bundle.data_block.timestamps
        return [ts[i] for i in bundle.masked_indices if i < len(ts)]

    # ------------------------------------------------------------------
    # 可信度与血缘辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _get_valid_rate(bundle: MetricDataBundle) -> float:
        """计算指标级有效数据率 valid_rate.

        valid_rate = len(masked_indices) / point_count

        Args:
            bundle: 指标数据包

        Returns:
            有效数据率 0~1；point_count=0 时返回 0
        """
        n = bundle.data_block.point_count
        if n <= 0:
            return 0.0
        return len(bundle.masked_indices) / n

    def _build_lineage(self, bundle: MetricDataBundle, valid_rate: float) -> DataLineage:
        """构建数据血缘（委托 ConfidenceEvaluator）."""
        return ConfidenceEvaluator.build_lineage(bundle, valid_rate, self.algorithm_version)

    def _make_result(
        self,
        bundle: MetricDataBundle,
        value: float,
        details: dict[str, Any] | None = None,
        valid_rate: float | None = None,
        precision: int = 2,
    ) -> MetricResult:
        """构造正常 MetricResult（含可信度判定）.

        Args:
            bundle: 指标数据包
            value: 指标值（已 round 到 precision 位小数）
            details: 指标详细信息
            valid_rate: 有效数据率，None 时自动计算
            precision: value 保留小数位数（默认 2；量级远小于 0.01 的指标
                如 output_trip_index 应传更大精度避免被抹零）

        Returns:
            MetricResult，含 metric_code/value/confidence_level/lineage/details
        """
        vr = valid_rate if valid_rate is not None else self._get_valid_rate(bundle)
        confidence = ConfidenceEvaluator.evaluate(vr)
        lineage = self._build_lineage(bundle, vr)

        logger.debug(
            "[%s] value=%.2f, valid_rate=%.4f, confidence=%s",
            self.metric_code,
            value,
            vr,
            confidence.value,
        )

        return MetricResult(
            metric_code=self.metric_code,
            value=round(float(value), precision),
            confidence_level=confidence.value,
            lineage=lineage,
            details=details or {},
        )

    def _make_inconclusive(
        self,
        bundle: MetricDataBundle,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> MetricResult:
        """构造 INCONCLUSIVE MetricResult（E 级可信度，value=None）.

        Args:
            bundle: 指标数据包
            reason: 不可计算原因（如 "data_insufficient" / "signal_missing"）
            details: 额外详细信息

        Returns:
            MetricResult，value=None，confidence_level="E"
        """
        vr = self._get_valid_rate(bundle)
        lineage = self._build_lineage(bundle, vr)
        detail = {"reason": reason}
        if details:
            detail.update(details)

        logger.warning(
            "[%s] INCONCLUSIVE: reason=%s, valid_rate=%.4f",
            self.metric_code,
            reason,
            vr,
        )

        return MetricResult(
            metric_code=self.metric_code,
            value=None,
            confidence_level=ConfidenceLevel.E.value,
            lineage=lineage,
            details=detail,
        )

    # ------------------------------------------------------------------
    # 时长计算辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _segment_duration(timestamps: list, i: int) -> float:
        """计算第 i 到 i+1 个时间戳的时长（秒）.

        Args:
            timestamps: 时间戳列表
            i: 起始索引

        Returns:
            时长（秒）；无法解析时回退为 1.0
        """
        dt = timestamps[i + 1] - timestamps[i]
        if isinstance(dt, timedelta):
            return dt.total_seconds()
        try:
            return float(dt)
        except (TypeError, ValueError):
            return 1.0

    @staticmethod
    def _total_duration_seconds(timestamps: list) -> float:
        """计算总时长（秒）= timestamps[-1] - timestamps[0].

        Args:
            timestamps: 时间戳列表

        Returns:
            总时长（秒）；点数 < 2 时返回 0
        """
        if len(timestamps) < 2:
            return 0.0
        dt = timestamps[-1] - timestamps[0]
        if isinstance(dt, timedelta):
            return dt.total_seconds()
        try:
            return float(dt)
        except (TypeError, ValueError):
            return float(len(timestamps) - 1)

    @staticmethod
    def _point_durations(timestamps: list) -> list[float]:
        """计算每个采样点代表的时长（秒）.

        采用零阶保持模型：第 i 个点的时长为 timestamps[i+1]-timestamps[i]，
        最后一个点沿用前一段时长。这样 n 个点的总时长 = n × 平均间隔，
        与工业 1Hz 数据的物理含义一致（每个采样点代表 1 秒运行状态）。

        Args:
            timestamps: 时间戳列表

        Returns:
            每个点的时长列表（长度与 timestamps 相同）；点数 < 2 时返回空
        """
        n = len(timestamps)
        if n < 2:
            return []
        durations: list[float] = []
        for i in range(n - 1):
            durations.append(MetricCalculatorBase._segment_duration(timestamps, i))
        # 最后一个点沿用前一段时长
        durations.append(durations[-1])
        return durations

    @staticmethod
    def _read_config_scalar(signals: dict, key: str, default: Any = None) -> Any:
        """从信号字典读取标量配置值（兼容列表存储）.

        DataBlock.signals 的值统一为列表类型；CONFIG tagGroup 的配置参数
        存为单元素列表（如 ``["FC"]`` 或 ``[45.0]``）。本方法提取首元素，
        使计算器能透明读取标量配置。

        Args:
            signals: 信号字典
            key: 配置键名
            default: 未找到时的默认值

        Returns:
            标量值；列表取首元素，缺失返回 default
        """
        val = signals.get(key)
        if val is None:
            return default
        if isinstance(val, (list, tuple)):
            if len(val) == 0:
                return default
            return val[0]
        return val

    # ------------------------------------------------------------------
    # 数值辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
        """将值限制在 [low, high] 区间."""
        return max(low, min(high, value))

    @staticmethod
    def _round2(value: float) -> float:
        """四舍五入到 2 位小数."""
        return round(float(value), 2)


__all__ = ["MetricCalculatorBase"]
