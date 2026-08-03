"""8 步预处理 Pipeline.

将原始时序数据（来自 TDengine）预处理为标准化 DataBlock，包含：
质量码识别 → 有效性标记 → 量程归一化 → 异常值检测 → 缺失率统计 →
连续性检查 → Metric Mask → QualitySummary。

核心原则（KEEP_ALL_WITH_VALIDITY, 算法说明 §3.4.1）：
    - 不删除任何数据点
    - 通过 valid 标记区分有效/无效
    - 不同指标通过 Metric Validity Mask 决定哪些点参与计算

设计依据：算法说明 §3.4.2, PRD §5.5, FDS §5.3.1.2
"""

from __future__ import annotations

import logging
from typing import Any

from app.contracts.data_types import (
    DataBlock,
    LoopPreprocessConfig,
    OutlierReason,
    QualityStatus,
    RawTimeSeries,
    TagGroup,
)
from app.services.preprocessing.outlier_detection import OutlierDetector
from app.services.preprocessing.quality_code import (
    is_nan_or_inf,
    map_quality_code,
)
from app.services.preprocessing.quality_summary import (
    compute_consecutive_segments,
    compute_quality_summary,
)
from app.services.preprocessing.thresholds import get_threshold
from app.services.preprocessing.validity_mask import apply_mask

logger = logging.getLogger(__name__)

# 需要量程归一化的信号（连续模拟量）
_NORMALIZABLE_SIGNALS: frozenset[str] = frozenset({"pv", "sp", "op"})

# 跳过冻结值检测的信号（稳态时变化小或常态为常量，冻结检测易误报）
# SP: 操作员设定的设定值，长时间不变是正常的
# MODE: 控制模式（AUTO/MANUAL），离散值
# PID_P/PID_I/PID_D: PID 整定参数，工程师设定后保持不变
# OP: 稳态时 OP 变化幅度小（std < frozen_std_pct×range），FROZEN 检测会误报；
#     阀门粘滞由 stiction_index 指标单独检测，OP 饱和由 saturation_rate 单独检测
_SKIP_FROZEN_SIGNALS: frozenset[str] = frozenset({"sp", "op", "mode", "pid_p", "pid_i", "pid_d"})

PREPROCESS_VERSION = "pre_v1"


class PreprocessingPipeline:
    """8 步预处理 Pipeline 编排器.

    将 RawTimeSeries 预处理为 DataBlock，执行以下 8 步
    （算法说明 §3.4.2）：

    ① 质量码识别 → ② 有效性标记 → ③ 量程归一化 → ④ 异常值识别 →
    ⑤ 缺失率统计 → ⑥ 连续性检查 → ⑦ Metric Mask → ⑧ QualitySummary

    执行顺序说明：步骤②依赖④的异常值检测结果，因此实际执行顺序为
    ①→③→④→②→⑤→⑥→⑧，⑦在 DataPlanner 组装 MetricDataBundle 时调用。

    设计依据：算法说明 §3.4.2, PRD §5.5
    """

    def __init__(self, config: LoopPreprocessConfig) -> None:
        self.config = config
        self.threshold = get_threshold(config.control_type)
        self.detector = OutlierDetector(self.threshold)

    def process(
        self,
        raw: RawTimeSeries,
        tag_group: TagGroup,
    ) -> DataBlock:
        """执行 8 步预处理 Pipeline，生成 DataBlock.

        Args:
            raw: 原始时序数据（来自 TDengine）
            tag_group: 数据所属的 tagGroup

        Returns:
            预处理后的 DataBlock（含 valid 标记 + 异常原因码 + 质量摘要）

        设计依据：算法说明 §3.4.2
        """
        n = len(raw.timestamps)
        loop_id = self.config.loop_id
        freq_label = self.threshold.sampling_freq_label
        data_block_id = f"db_{loop_id}_{tag_group.value}_{freq_label}"

        logger.debug(
            "Pipeline.process: loop=%s, tagGroup=%s, points=%d, controlType=%s",
            loop_id,
            tag_group.value,
            n,
            self.config.control_type.value,
        )

        # 初始化信号和有效性
        signals: dict[str, list[Any]] = {}
        validity: dict[str, list[bool]] = {}
        outlier_reasons: dict[str, list[list[str]]] = {}

        for tag_name, values in raw.signals.items():
            signals[tag_name] = list(values)
            validity[f"{tag_name}_valid"] = [True] * n
            outlier_reasons[tag_name] = [[] for _ in range(n)]

        # Step ① 质量码识别（算法说明 §3.4.2 步骤①）
        quality_status_map = self._step1_identify_quality(raw)

        # Step ③ 量程归一化（算法说明 §3.4.2 步骤③）
        signals = self._step3_normalize(signals)

        # Step ④ 异常值识别（算法说明 §3.4.2 步骤④, §3.4.3）
        all_outlier_reasons = self._step4_detect_outliers(raw, signals, tag_group)

        # Step ② 有效性标记（算法说明 §3.4.2 步骤②）
        # 基于质量码 + 异常值检测结果，设置 valid=True/False
        validity, outlier_reasons = self._step2_mark_validity(
            n, quality_status_map, all_outlier_reasons, raw.signals
        )

        # 计算 all_valid（所有信号 valid 的交集）
        all_valid = self._compute_all_valid(validity, n)

        # Step ⑤ 缺失率统计（算法说明 §3.4.2 步骤⑤）
        # 在 QualitySummary 中计算（步骤⑧）

        # Step ⑥ 连续性检查（算法说明 §3.4.2 步骤⑥）
        consecutive_segments = self._step6_continuity_check(all_valid)

        # Step ⑧ QualitySummary 生成（算法说明 §3.4.2 步骤⑧）
        pv_quality_codes = raw.quality_codes.get("pv_quality")
        quality_summary = compute_quality_summary(
            validity=validity,
            timestamps=raw.timestamps,
            point_count=n,
            quality_codes=pv_quality_codes,
            expected_interval_s=float(self.threshold.base_sampling_freq),
        )

        # 组装 DataBlock
        data_block = DataBlock(
            data_block_id=data_block_id,
            loop_id=loop_id,
            tag_group=tag_group.value,
            sampling_freq=freq_label,
            timestamps=list(raw.timestamps),
            signals=signals,
            validity=validity,
            outlier_reasons=outlier_reasons,
            quality_summary=quality_summary,
            consecutive_segments=consecutive_segments,
            config_version=self.config.config_version,
            preprocess_version=PREPROCESS_VERSION,
            point_count=n,
            # P0-B: 注入响应类别（STABLE/SLOW/FAST/LOGIC），供指标计算器读取算法参数
            control_type=self.config.response_category,
        )

        logger.debug(
            "Pipeline.process done: %s, valid_rate=%.4f, segments=%d",
            data_block_id,
            quality_summary.valid_rate,
            len(consecutive_segments),
        )
        return data_block

    # -------------------------------------------------------------------
    # Step ① 质量码识别
    # -------------------------------------------------------------------

    def _step1_identify_quality(self, raw: RawTimeSeries) -> dict[str, list[QualityStatus]]:
        """步骤①：将原始质量码映射为 Good/Bad/Unknown 三态.

        设计依据：算法说明 §3.4.2 步骤①, §4.1.2
        """
        result: dict[str, list[QualityStatus]] = {}
        for tag_name, codes in raw.quality_codes.items():
            # pv_quality → pv 的质量码
            base_tag = tag_name.replace("_quality", "")
            result[base_tag] = [map_quality_code(c) for c in codes]
        return result

    # -------------------------------------------------------------------
    # Step ② 有效性标记
    # -------------------------------------------------------------------

    def _step2_mark_validity(
        self,
        n: int,
        quality_status_map: dict[str, list[QualityStatus]],
        all_outlier_reasons: dict[str, dict[int, list[OutlierReason]]],
        raw_signals: dict[str, list[Any]],
    ) -> tuple[dict[str, list[bool]], dict[str, list[list[str]]]]:
        """步骤②：基于质量码 + 异常值检测结果设置 valid 标记.

        规则（算法说明 §3.4.1, §3.4.3）：
            - 质量码为 Bad/Unknown → valid=False（QC_BAD）
            - 非 MARK_ONLY 异常原因 → valid=False
            - TS_ANOMALY / HF_NOISE / FROZEN → 仅标记，valid 保持 True
              （FROZEN 仅标记：平稳良好回路 PV 低方差不判无效，
              真仪表卡死由 instrument_fault_rate 复合判据识别）

        Returns:
            (validity_dict, outlier_reasons_dict)
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
                    # 非 MARK_ONLY 原因置 valid=False
                    if OutlierDetector.should_invalidate([reason]):
                        valid_arr[idx] = False

            validity[f"{tag_name}_valid"] = valid_arr
            outlier_reasons[tag_name] = reasons_arr

        return validity, outlier_reasons

    # -------------------------------------------------------------------
    # Step ③ 量程归一化
    # -------------------------------------------------------------------

    def _step3_normalize(self, signals: dict[str, list[Any]]) -> dict[str, list[Any]]:
        """步骤③：PV/SP/OP 按各自量程归一化为百分比（0~100）.

        归一化公式：normalized = (value - range_min) / (range_max - range_min) × 100
        - PV/SP 用 PV tag 量程（``config.range_min/range_max``）
        - OP 用 OP tag 量程（``config.op_range_min/op_range_max``），因为 OP 是百分比
          输出（0-100%），物理量程与 PV 不同，共用 PV 量程会导致归一化越界
        MODE 和 PV_QUALITY 不归一化（离散值/质量码）。

        设计依据：算法说明 §3.4.2 步骤③
        """
        pv_span = self.config.range_max - self.config.range_min
        op_span = self.config.op_range_max - self.config.op_range_min
        if abs(pv_span) < 1e-9:
            logger.warning(
                "PV range span is zero (min=%s, max=%s), skip PV/SP normalization",
                self.config.range_min,
                self.config.range_max,
            )
        if abs(op_span) < 1e-9:
            logger.warning(
                "OP range span is zero (min=%s, max=%s), skip OP normalization",
                self.config.op_range_min,
                self.config.op_range_max,
            )

        normalized: dict[str, list[Any]] = {}
        for tag_name, values in signals.items():
            if tag_name == "op":
                r_min, r_span = self.config.op_range_min, op_span
            elif tag_name in ("pv", "sp"):
                r_min, r_span = self.config.range_min, pv_span
            else:
                normalized[tag_name] = list(values)
                continue
            if abs(r_span) < 1e-9:
                normalized[tag_name] = list(values)
                continue
            norm_vals: list[Any] = []
            for v in values:
                if is_nan_or_inf(v):
                    norm_vals.append(v)  # NaN 保持原样
                else:
                    try:
                        norm_vals.append((float(v) - r_min) / r_span * 100.0)
                    except (ValueError, TypeError):
                        norm_vals.append(v)
            normalized[tag_name] = norm_vals
        return normalized

    # -------------------------------------------------------------------
    # Step ④ 异常值识别
    # -------------------------------------------------------------------

    def _step4_detect_outliers(
        self,
        raw: RawTimeSeries,
        normalized_signals: dict[str, list[Any]],
        tag_group: TagGroup,
    ) -> dict[str, dict[int, list[OutlierReason]]]:
        """步骤④：对每个信号执行 8 类异常值检测.

        在归一化后的数据上检测（阈值转为绝对值）。
        返回每个信号每个异常点的异常原因码列表。

        设计依据：算法说明 §3.4.2 步骤④, §3.4.3-3.4.4
        """
        result: dict[str, dict[int, list[OutlierReason]]] = {}
        for tag_name, values in normalized_signals.items():
            # 质量码仅 PV 有
            qc_key = f"{tag_name}_quality"
            quality_codes = raw.quality_codes.get(qc_key)

            is_norm = tag_name in _NORMALIZABLE_SIGNALS
            skip_frozen = tag_name in _SKIP_FROZEN_SIGNALS
            reasons = self.detector.detect_all(
                tag_name=tag_name,
                values=values,
                timestamps=raw.timestamps,
                range_min=self.config.range_min,
                range_max=self.config.range_max,
                quality_codes=quality_codes,
                is_normalized=is_norm,
                skip_frozen=skip_frozen,
            )
            result[tag_name] = reasons
        return result

    # -------------------------------------------------------------------
    # Step ⑥ 连续性检查
    # -------------------------------------------------------------------

    def _step6_continuity_check(self, all_valid: list[bool]) -> list[tuple[int, int]]:
        """步骤⑥：标记连续有效段，缺口超过阈值时切断.

        连续 valid=True 的段长度不足 min_consecutive_points 时丢弃。

        设计依据：算法说明 §3.4.2 步骤⑥, §3.4.4 连续有效最短段
        """
        return compute_consecutive_segments(all_valid, self.threshold.min_consecutive_points)

    # -------------------------------------------------------------------
    # 辅助方法
    # -------------------------------------------------------------------

    @staticmethod
    def _compute_all_valid(validity: dict[str, list[bool]], n: int) -> list[bool]:
        """计算所有信号 valid 的交集（该时间戳是否全有效）."""
        all_valid = [True] * n
        for arr in validity.values():
            for i in range(min(n, len(arr))):
                all_valid[i] = all_valid[i] and arr[i]
        return all_valid

    def generate_metric_mask(
        self,
        data_block: DataBlock,
        mask_expression: str | None,
    ) -> list[int]:
        """步骤⑦：生成 Metric Validity Mask，返回有效索引列表.

        根据指标数据需求契约的 mask_expression 筛选有效点。
        DataPlanner 在组装 MetricDataBundle 时调用此方法。

        Args:
            data_block: 预处理后的数据块
            mask_expression: 掩码表达式，如 ``"pv_valid && sp_valid"``

        Returns:
            有效索引列表

        设计依据：算法说明 §3.4.2 步骤⑦, PRD §5.5.4
        """
        return apply_mask(data_block, mask_expression)
