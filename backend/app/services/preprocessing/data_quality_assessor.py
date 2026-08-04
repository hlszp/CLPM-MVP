"""共享数据质量评估内核（DataQualityAssessor）.

从 PreprocessingPipeline 的质量评估步骤（① 质量码识别 → ④ 异常值检测 →
② 有效性标记 → ⑥ 连续性 → ⑧ QualitySummary）中抽取的纯质量评估模块。
不归一化、不删点（KEEP_ALL_WITH_VALIDITY），保证 KPI/诊断/整定三条链路
使用同一套 validity 与 valid_rate 口径。

设计依据：
- 算法说明 §3.4.2 步骤①②④⑥⑧, §3.4.3 异常值检测, §3.7.2 可信度判定
- 可信度统一改进方案 §4（confidence-unification-plan-2026-08-04.md）

v6.2 变更（可信度统一 Phase 1）：
    新增本模块作为三链路共享的数据质量评估入口。诊断链路原先自写的
    ``_apply_outlier_preprocessing`` 改为调用本模块；KPI Pipeline 内部
    复用 ``compute_loop_valid_rate`` 计算回路级 valid_rate。

核心概念：
    - **回路级 valid_rate（loop_valid_rate）**：核心 tag（pv/sp/op/mode）
      同时有效的点占比，作为可信度判定的唯一输入（决策 D1）。
    - 与 ``compute_quality_summary`` 的块级 valid_rate（全 tag 交集，审计用）
      区别：loop_valid_rate 仅取核心评估 tag，避免 PID 参数等非评估信号拉低。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.contracts.data_types import (
    LoopPreprocessConfig,
    OutlierReason,
    QualityStatus,
    QualitySummary,
    RawTimeSeries,
)
from app.services.preprocessing.outlier_detection import OutlierDetector
from app.services.preprocessing.quality_code import map_quality_code
from app.services.preprocessing.quality_summary import (
    compute_consecutive_segments,
    compute_quality_summary,
)
from app.services.preprocessing.thresholds import get_threshold

logger = logging.getLogger(__name__)

#: 参与回路级 valid_rate 的核心评估 tag（决策 D1：pv/sp/op/mode 架构模式）
CORE_TAGS: tuple[str, ...] = ("pv", "sp", "op", "mode")

#: 默认跳过冻结值检测的信号（与 PreprocessingPipeline 一致：稳态时变化小
#: 或常态为常量的信号，冻结检测易误报；真冻结由 instrument_fault_rate 复合判据识别）
DEFAULT_SKIP_FROZEN_SIGNALS: frozenset[str] = frozenset(
    {"sp", "op", "mode", "pid_p", "pid_i", "pid_d"}
)

#: 量程归一化信号（OP 量程与 PV 不同，原始值检测时需按各自量程）
_OP_TAG = "op"


@dataclass
class QualityAssessment:
    """数据质量评估结果（共享内核输出）.

    Attributes:
        validity: 各 tag 的有效标记，key 为 ``{tag}_valid``，值为逐点 bool 列表
        outlier_reasons: 各 tag 的异常原因码，key 为 tag 名，值为逐点原因码列表
        loop_valid_rate: 回路级 valid_rate（核心 tag 交集 / point_count），
            作为可信度判定的唯一输入
        quality_summary: 审计用质量摘要（含 missing_rate 等）
        consecutive_segments: 连续有效段 [(start, end), ...]
        point_count: 数据点数
    """

    validity: dict[str, list[bool]] = field(default_factory=dict)
    outlier_reasons: dict[str, list[list[str]]] = field(default_factory=dict)
    loop_valid_rate: float = 0.0
    quality_summary: QualitySummary = field(default_factory=QualitySummary)
    consecutive_segments: list[tuple[int, int]] = field(default_factory=list)
    point_count: int = 0


class DataQualityAssessor:
    """共享数据质量评估内核.

    职责：质量码识别 + 异常值检测（8 类）+ validity 标记 + 回路级 valid_rate 计算。
    不做归一化、不删点。KPI/诊断/整定共享，保证 valid_rate 口径统一。

    使用方式：
        - 诊断/整定：``assessor.assess(raw)`` 拿到完整 QualityAssessment
        - KPI Pipeline：复用 ``compute_loop_valid_rate(validity, n)`` 基于现有
          validity 计算回路级 valid_rate（KPI 保持归一化检测不变，行为零回归）

    设计依据：算法说明 §3.4.2, §3.7.2；可信度统一改进方案 §4
    """

    def __init__(self, config: LoopPreprocessConfig) -> None:
        self.config = config
        self.threshold = get_threshold(config.control_type)
        self.detector = OutlierDetector(self.threshold)

    def assess(
        self,
        raw: RawTimeSeries,
        skip_frozen_signals: frozenset[str] | None = None,
    ) -> QualityAssessment:
        """评估原始时序的数据质量（在原始工程值上检测，不归一化）.

        执行步骤①质量码识别 → ④异常值检测（is_normalized=False）→ ②有效性标记
        → ⑥连续性 → ⑧QualitySummary，并计算回路级 loop_valid_rate。

        Args:
            raw: 原始时序数据（工程值，未归一化）
            skip_frozen_signals: 跳过冻结检测的信号集合，默认
                :data:`DEFAULT_SKIP_FROZEN_SIGNALS`（仅 pv 检测冻结）

        Returns:
            QualityAssessment，含 validity + 回路级 valid_rate + 审计摘要

        设计依据：算法说明 §3.4.2 步骤①②④⑥⑧
        """
        n = len(raw.timestamps)
        if n == 0:
            return QualityAssessment()

        skip_frozen = (
            DEFAULT_SKIP_FROZEN_SIGNALS if skip_frozen_signals is None else skip_frozen_signals
        )

        # Step ① 质量码识别（算法说明 §3.4.2 步骤①）
        quality_status_map = self._identify_quality(raw)

        # Step ④ 异常值检测（算法说明 §3.4.2 步骤④, §3.4.3）
        # 在原始工程值上检测（is_normalized=False），按各 tag 量程判定
        all_outlier_reasons = self._detect_outliers(raw, skip_frozen)

        # Step ② 有效性标记（算法说明 §3.4.2 步骤②）
        validity, outlier_reasons = self._mark_validity(
            n, quality_status_map, all_outlier_reasons, raw.signals
        )

        # 全 tag valid 交集（用于连续性检查）
        all_valid = self._compute_all_valid(validity, n)

        # Step ⑥ 连续性检查（算法说明 §3.4.2 步骤⑥）
        consecutive_segments = compute_consecutive_segments(
            all_valid, self.threshold.min_consecutive_points
        )

        # Step ⑧ QualitySummary（算法说明 §3.4.2 步骤⑧，审计用）
        pv_quality_codes = raw.quality_codes.get("pv_quality")
        quality_summary = compute_quality_summary(
            validity=validity,
            timestamps=raw.timestamps,
            point_count=n,
            quality_codes=pv_quality_codes,
            expected_interval_s=float(self.threshold.base_sampling_freq),
        )

        # 回路级 valid_rate（核心 tag 交集，可信度判定唯一输入）
        loop_valid_rate = self.compute_loop_valid_rate(validity, n)

        logger.debug(
            "DataQualityAssessor.assess: loop=%s, points=%d, "
            "loop_valid_rate=%.4f, block_valid_rate=%.4f, segments=%d",
            self.config.loop_id,
            n,
            loop_valid_rate,
            quality_summary.valid_rate,
            len(consecutive_segments),
        )

        return QualityAssessment(
            validity=validity,
            outlier_reasons=outlier_reasons,
            loop_valid_rate=loop_valid_rate,
            quality_summary=quality_summary,
            consecutive_segments=consecutive_segments,
            point_count=n,
        )

    # ------------------------------------------------------------------
    # 静态方法：回路级 valid_rate 计算（供 KPI Pipeline 复用现有 validity）
    # ------------------------------------------------------------------

    @staticmethod
    def compute_loop_valid_rate(
        validity: dict[str, list[bool]],
        point_count: int,
        core_tags: tuple[str, ...] = CORE_TAGS,
    ) -> float:
        """计算回路级 valid_rate（核心 tag 交集 / point_count）.

        核心 tag（pv/sp/op/mode）同时有效的点占比，作为可信度判定的唯一输入。
        缺失的 tag 跳过（不参与交集），避免 tagGroup 不全时误判。

        Args:
            validity: 有效性标记字典，key 为 ``{tag}_valid``
            point_count: 数据点数
            core_tags: 参与回路级评估的核心 tag，默认 :data:`CORE_TAGS`

        Returns:
            回路级 valid_rate ∈ [0.0, 1.0]；point_count=0 时返回 0.0

        设计依据：可信度统一改进方案 §4.3（决策 D1：pv/sp/op/mode）
        """
        if point_count <= 0:
            return 0.0

        # 收集存在的核心 tag 的 validity 数组
        core_arrays: list[list[bool]] = []
        for tag in core_tags:
            key = f"{tag}_valid"
            arr = validity.get(key)
            if arr is not None:
                core_arrays.append(arr)

        if not core_arrays:
            return 0.0

        # 求核心 tag 交集
        valid_count = 0
        for i in range(point_count):
            if all(i < len(arr) and arr[i] for arr in core_arrays):
                valid_count += 1

        return valid_count / point_count

    # ------------------------------------------------------------------
    # 内部步骤（与 PreprocessingPipeline 的 step1/2/4 等价，原始值检测）
    # ------------------------------------------------------------------

    def _identify_quality(self, raw: RawTimeSeries) -> dict[str, list[QualityStatus]]:
        """步骤①：质量码映射为 Good/Bad/Unknown 三态（算法说明 §3.4.2 步骤①）."""
        result: dict[str, list[QualityStatus]] = {}
        for tag_name, codes in raw.quality_codes.items():
            base_tag = tag_name.replace("_quality", "")
            result[base_tag] = [map_quality_code(c) for c in codes]
        return result

    def _detect_outliers(
        self,
        raw: RawTimeSeries,
        skip_frozen_signals: frozenset[str],
    ) -> dict[str, dict[int, list[OutlierReason]]]:
        """步骤④：8 类异常值检测（在原始工程值上，is_normalized=False）.

        按 tag 量程判定：pv/sp 用 PV 量程，op 用 OP 量程，其余用 PV 量程。
        与 PreprocessingPipeline._step4_detect_outliers（归一化值检测）等价：
        归一化是线性变换，OutlierDetector 的 out_of_range/jump/spike 按量程缩放，
        两种模式检测结果一致。

        设计依据：算法说明 §3.4.2 步骤④, §3.4.3
        """
        result: dict[str, dict[int, list[OutlierReason]]] = {}
        for tag_name, values in raw.signals.items():
            qc_key = f"{tag_name}_quality"
            quality_codes = raw.quality_codes.get(qc_key)

            # 按 tag 选择量程（OP 量程与 PV 不同）
            if tag_name == _OP_TAG:
                r_min, r_max = self.config.op_range_min, self.config.op_range_max
            else:
                r_min, r_max = self.config.range_min, self.config.range_max

            skip_frozen = tag_name in skip_frozen_signals
            reasons = self.detector.detect_all(
                tag_name=tag_name,
                values=values,
                timestamps=raw.timestamps,
                range_min=r_min,
                range_max=r_max,
                quality_codes=quality_codes,
                is_normalized=False,
                skip_frozen=skip_frozen,
            )
            result[tag_name] = reasons
        return result

    @staticmethod
    def _mark_validity(
        n: int,
        quality_status_map: dict[str, list[QualityStatus]],
        all_outlier_reasons: dict[str, dict[int, list[OutlierReason]]],
        raw_signals: dict[str, list[Any]],
    ) -> tuple[dict[str, list[bool]], dict[str, list[list[str]]]]:
        """步骤②：基于质量码 + 异常值检测设置 valid 标记（算法说明 §3.4.2 步骤②）.

        规则：
            - 质量码为 Bad/Unknown → valid=False（QC_BAD）
            - 非 MARK_ONLY 异常原因 → valid=False
            - TS_ANOMALY / HF_NOISE / FROZEN → 仅标记，valid 保持 True
        """
        validity: dict[str, list[bool]] = {}
        outlier_reasons: dict[str, list[list[str]]] = {}

        for tag_name in raw_signals:
            valid_arr = [True] * n
            reasons_arr: list[list[str]] = [[] for _ in range(n)]

            # 质量码判定
            if tag_name in quality_status_map:
                statuses = quality_status_map[tag_name]
                for i, status in enumerate(statuses):
                    if i < n and status != QualityStatus.GOOD:
                        valid_arr[i] = False
                        reasons_arr[i].append(OutlierReason.QC_BAD.value)

            # 异常值检测结果
            tag_outliers = all_outlier_reasons.get(tag_name, {})
            for idx, reasons in tag_outliers.items():
                if idx >= n:
                    continue
                for reason in reasons:
                    reasons_arr[idx].append(reason.value)
                    if OutlierDetector.should_invalidate([reason]):
                        valid_arr[idx] = False

            validity[f"{tag_name}_valid"] = valid_arr
            outlier_reasons[tag_name] = reasons_arr

        return validity, outlier_reasons

    @staticmethod
    def _compute_all_valid(validity: dict[str, list[bool]], n: int) -> list[bool]:
        """计算所有信号 valid 的交集（该时间戳是否全有效）."""
        all_valid = [True] * n
        for arr in validity.values():
            for i in range(min(n, len(arr))):
                all_valid[i] = all_valid[i] and arr[i]
        return all_valid
