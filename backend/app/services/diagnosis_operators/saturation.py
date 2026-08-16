"""饱和族元算子：OP 限位饱和率分析（对齐 GB/T 44693.2-2024 附录 F.3）。

内核等价复制自 app/tasks/diagnosis_engine.py：
- _saturation_kernel ← _analyze_saturation（L2471-2570）+ _is_auto_mode（L2445-2466）
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.constants.mode import AUTO_MODES, MODE_LABELS_EN
from app.services.diagnosis_operators.base import (
    EvidenceItem,
    OperatorInput,
    OperatorMeta,
    OperatorResult,
    operator,
)

logger = logging.getLogger(__name__)

_AUTO_MODE_LABELS: frozenset[str] = frozenset(MODE_LABELS_EN[m] for m in AUTO_MODES)


def _is_auto_mode(mode_val: Any) -> bool:
    """判定 MODE 值是否为自控模式（等价复制自引擎 L2445-2466）。"""
    if isinstance(mode_val, bool):
        return False
    if isinstance(mode_val, (int, np.integer)):
        return int(mode_val) in AUTO_MODES
    if isinstance(mode_val, (float, np.floating)):
        return float(mode_val).is_integer() and int(mode_val) in AUTO_MODES
    mode_str = str(mode_val).strip().upper()
    if mode_str in _AUTO_MODE_LABELS:
        return True
    try:
        num = float(mode_str)
    except (TypeError, ValueError):
        return False
    return num.is_integer() and int(num) in AUTO_MODES


def _saturation_kernel(
    op_values: np.ndarray,
    mode_values: np.ndarray | None = None,
    threshold: dict | None = None,
    total_points: int | None = None,
) -> dict[str, Any]:
    """OP 饱和率分析（等价复制自引擎 L2469-2568）。

    Sa = 自控模式饱和点数 / 总点数（分母含手动模式，对齐 GB/T F.3）。
    """
    if threshold is None:
        threshold = {}
    op_high_limit = float(threshold.get("op_high_limit", 100.0))
    op_low_limit = float(threshold.get("op_low_limit", 0.0))
    saturation_epsilon = float(threshold.get("saturation_epsilon", 2.0))

    # 分母：评估时段总点数（AllTime，含手动模式）
    if total_points is not None and total_points > 0:
        denom = int(total_points)
    else:
        denom = len(op_values)

    if denom == 0:
        return {
            "detected": False,
            "confidence": 0.0,
            "saturation_rate": 0.0,
            "high_count": 0,
            "low_count": 0,
        }

    try:
        high_threshold = op_high_limit - saturation_epsilon
        low_threshold = op_low_limit + saturation_epsilon

        # 分子：仅自控模式下的饱和点数
        if mode_values is not None and len(mode_values) > 0:
            min_len = min(len(op_values), len(mode_values))
            auto_mask = np.array(
                [_is_auto_mode(mode_values[i]) for i in range(min_len)],
                dtype=bool,
            )
            op_auto = np.asarray(op_values[:min_len], dtype=float)[auto_mask]
            high_count = int(np.sum(op_auto >= high_threshold))
            low_count = int(np.sum(op_auto <= low_threshold))
        else:
            op_arr = np.asarray(op_values, dtype=float)
            high_count = int(np.sum(op_arr >= high_threshold))
            low_count = int(np.sum(op_arr <= low_threshold))

        saturation_rate = (high_count + low_count) / denom

        detected = saturation_rate > 0.2
        confidence = min(1.0, saturation_rate * 3) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "saturation_rate": saturation_rate,
            "high_count": high_count,
            "low_count": low_count,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("OP 饱和率分析失败: %s", exc)
        return {
            "detected": False,
            "confidence": 0.0,
            "saturation_rate": 0.0,
            "high_count": 0,
            "low_count": 0,
        }


@operator(
    OperatorMeta(
        name="output_saturation",
        display_name="OP 输出饱和检测",
        family="saturation",
        diag_code="OUTPUT_SATURATION",
        description="自控模式下 OP 贴限位点数占总点数比例（GB/T F.3 口径），>20% 判饱和",
        required_signals=("op", "mode"),
        min_sample_rate=0.0,
        outputs_schema={
            "saturation_rate": "饱和率",
            "high_count": "贴高限点数",
            "low_count": "贴低限点数",
        },
        threshold_schema={
            "op_high_limit": 100.0,
            "op_low_limit": 0.0,
            "saturation_epsilon": 2.0,
        },
        symptom_tags=("OUTPUT_SATURATION",),
        fast_group=True,
    )
)
def detect_saturation(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    op = input.signals.get("op")
    if op is None or len(op) < 16:
        return OperatorResult("output_saturation", executed=False, skip_reason="op 数据不足")
    mode = input.signals.get("mode")
    total = int(input.meta.get("total_points", len(op)))
    res = _saturation_kernel(op, mode, threshold, total)
    return OperatorResult(
        "output_saturation",
        executed=True,
        detected=bool(res["detected"]),
        confidence=float(res["confidence"]),
        features={
            "saturation_rate": res["saturation_rate"],
            "high_count": res["high_count"],
            "low_count": res["low_count"],
        },
        evidence=[
            EvidenceItem(
                "saturation_rate",
                round(float(res["saturation_rate"]), 4),
                0.2,
                "自控模式饱和率" + ("超阈" if res["detected"] else "未超阈"),
            ),
        ],
    )
