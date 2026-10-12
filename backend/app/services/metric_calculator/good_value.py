"""好值率计算器（算法说明 §4.1）.

公式：η_good = T_good / T_total × 100%

其中：
    T_good：PV 质量码为 Good 且数值在有效量程范围内的累计时长
    T_total：评估时段总时长

设计依据：算法说明 §4.1；GB/T 44693.2-2024 附录 F.6

定位：辅助诊断指标，不参与综合评分加权。
好值率 < 20% 时标记 INCONCLUSIVE（影响整体可信度）。

P3-10a 新 profile（CANDIDATE_V3_RC1，10§3.4 Good 行 / C04-EA3）：
    - ``good_value_rate`` 明确为**质量码 Good 的时间比例**（来源质量）；
    - 新增 ``details.usable_rate`` 表示**数值可用比例**（``pv_valid`` 口径：
      越限/坏质量等数值资格判定的交）——QC 全 Good 但部分点越限时，
      Good=100% 与 usable<100% 同时成立，两者分开不互冒充；
    - **缺质量码 → Good 不可判定**（value=None，assessmentState=
      UNASSESSABLE），不用 pv_valid 回退冒充 QC Good（legacy 保留回退）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 好值率 INCONCLUSIVE 阈值（%）
INCONCLUSIVE_THRESHOLD = 20.0


class GoodValueRateCalculator(MetricCalculatorBase):
    """好值率计算器（算法说明 §4.1）.

    基于 PV 质量码统计，衡量评估时段内数据有效性的时长占比。
    好值率影响指标可信度：好值率低 → valid_rate 低 → 可信度降级。
    """

    @property
    def metric_code(self) -> str:
        return "good_value_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算好值率.

        Args:
            bundle: 指标数据包（mask 通常为空，全量数据参与）

        Returns:
            MetricResult：value 为好值率 0~100；
            好值率 < 20% 时 INCONCLUSIVE（value=None, confidence=E）；
            新 profile 下缺质量码 → UNASSESSABLE（value=None，回路级可信度
            保留），details.usable_rate 另报数值可用比例
        """
        block = bundle.data_block
        n = block.point_count

        logger.debug("[好值率] 输入: point_count=%d", n)

        if n == 0:
            return self._make_inconclusive(bundle, "empty_data_block")

        if self.new_semantics:
            return self._calculate_new_profile(bundle, block, n)

        # 优先使用预处理阶段计算的 quality_summary.good_value_rate
        if block.quality_summary.good_value_rate is not None:
            rate = block.quality_summary.good_value_rate * 100.0
            logger.debug("[好值率] 使用 quality_summary: rate=%.2f%%", rate)
        else:
            # 回退：基于 pv_valid 标记计算（valid 已含质量码判定）
            pv_valid = block.validity.get("pv_valid", [])
            good_count = sum(1 for v in pv_valid if v)
            rate = (good_count / n) * 100.0 if n > 0 else 0.0
            logger.debug("[好值率] 回退 pv_valid 计算: rate=%.2f%%", rate)

        rate = self._clamp(rate)

        # 好值率 < 20% → INCONCLUSIVE
        if rate < INCONCLUSIVE_THRESHOLD:
            return self._make_inconclusive(
                bundle,
                "good_value_rate_below_threshold",
                {"good_value_rate": round(rate, 2), "threshold": INCONCLUSIVE_THRESHOLD},
            )

        return self._make_result(
            bundle,
            rate,
            {
                "good_value_rate": round(rate, 2),
                "sample_count": n,
                "source": "quality_summary"
                if block.quality_summary.good_value_rate is not None
                else "pv_valid",
            },
        )

    # ------------------------------------------------------------------
    # P3-10a 新 profile：QC Good 与 usable 分开（C04-EA3）
    # ------------------------------------------------------------------

    def _calculate_new_profile(self, bundle: MetricDataBundle, block: Any, n: int) -> MetricResult:
        """新 profile 好值率：QC Good 主路径唯一，缺质量码不可判定.

        - ``value`` = 质量码 Good 比例（来源质量口径）；
        - ``details.usable_rate`` = pv_valid 数值可用比例（优先摘要
          ``usable_rate``，缺省本地计算）——两者分开，不互相冒充；
        - 缺质量码（quality_summary.good_value_rate is None 且无
          quality_codes）：Good **不可判定**（UNASSESSABLE，非 0），
          不用 pv_valid 回退冒充 QC Good（legacy 的回退冒充即 EA-04）。
        """
        # 数值可用比例（pv_valid 口径；与 quality_summary 增量字段一致）
        usable_rate = block.quality_summary.usable_rate
        usable_source = "quality_summary"
        if usable_rate is None:
            pv_valid = block.validity.get("pv_valid", [])
            if pv_valid:
                usable_rate = sum(1 for v in pv_valid[:n] if v) / n * 100.0
                usable_source = "pv_valid_local"
            else:
                usable_rate = None
                usable_source = "unavailable"
        else:
            usable_rate = usable_rate * 100.0
        usable_details: dict[str, Any] = {
            "usable_rate": round(usable_rate, 2) if usable_rate is not None else None,
            "usable_rate_basis": "pv_valid",
            "usable_rate_source": usable_source,
        }

        qc_rate = block.quality_summary.good_value_rate
        if qc_rate is None:
            # 缺质量码：QC Good 不可判定（C04-EA3 后半）——不用 pv_valid 冒充
            return self._make_unassessable(
                bundle,
                "no_quality_codes",
                {
                    **usable_details,
                    "sample_count": n,
                    "good_value_rate": None,
                    "note": (
                        "quality codes unavailable; Good undecidable, "
                        "usable_rate reported separately"
                    ),
                },
            )

        rate = self._clamp(qc_rate * 100.0)

        # <20% INCONCLUSIVE 约定沿用（产品口径，不在 EA-04 裁决范围）
        if rate < INCONCLUSIVE_THRESHOLD:
            return self._make_inconclusive(
                bundle,
                "good_value_rate_below_threshold",
                {
                    "good_value_rate": round(rate, 2),
                    "threshold": INCONCLUSIVE_THRESHOLD,
                    **usable_details,
                    "assessmentState": "PARTIAL",
                },
            )

        return self._make_result(
            bundle,
            rate,
            {
                "good_value_rate": round(rate, 2),
                "sample_count": n,
                "source": "quality_summary",
                "assessmentState": "COMPLETE",
                **usable_details,
            },
        )


__all__ = ["GoodValueRateCalculator"]
