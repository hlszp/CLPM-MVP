"""Diagnosis recommendation service (SVC-11).

根据诊断标签返回标准化解决方案推荐。8 类标签的标准化建议模板：

- OSCILLATION（振荡）：1.重新整定PID 2.检查阀门粘滞 3.排查外部干扰
- STICTION（黏滞）：1.清洁/更换阀门填料 2.加装阀门定位器 3.检查气动管路
- SATURATION（饱和）：1.检查工艺负荷 2.调整阀门尺寸 3.检查前段工艺
- SLUGGISH（钝化）：1.增加PID增益 2.减少积分时间 3.检查执行机构
- DEVIATION（偏差）：1.重新整定PID 2.检查传感器校验 3.检查设定值合理性
- NOISE（噪声）：1.增加滤波 2.检查传感器信号 3.排查电磁干扰
- DEAD_BAND（死区）：1.调整阀门定位器 2.减少死区设置 3.检查执行机构间隙
- TUNING（整定）：1.使用Lambda整定法 2.使用Cohen-Coon法 3.使用IMC法

每条建议包含：priority(1-3), action, description, target_module(整定/跟踪/none)。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.diagnosis import DiagnosisResult
from app.models.loop import LoopLedger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 8 类诊断标签中文名映射（推荐服务专用，与 diagnosis.py 中的标签互补）
# ---------------------------------------------------------------------------

RECO_LABEL_NAMES: dict[str, str] = {
    "OSCILLATION": "振荡",
    "STICTION": "黏滞",
    "SATURATION": "饱和",
    "SLUGGISH": "钝化",
    "DEVIATION": "偏差",
    "NOISE": "噪声",
    "DEAD_BAND": "死区",
    "TUNING": "整定",
}

# 现有诊断引擎标签 → 推荐标签的映射（兼容现有诊断引擎输出）
_LABEL_ALIAS_MAP: dict[str, str] = {
    "OSCILLATION": "OSCILLATION",
    "VALVE_STICTION": "STICTION",
    "OUTPUT_SATURATION": "SATURATION",
    "OVERCONSERVATIVE": "SLUGGISH",
    "OVERAGGRESSIVE": "TUNING",
    "EXTERNAL_DISTURBANCE": "DEVIATION",
    "QUALITY_ABNORMAL": "NOISE",
    "MANUAL_REVIEW": "DEAD_BAND",
    # 直接同名兼容
    "STICTION": "STICTION",
    "SATURATION": "SATURATION",
    "SLUGGISH": "SLUGGISH",
    "DEVIATION": "DEVIATION",
    "NOISE": "NOISE",
    "DEAD_BAND": "DEAD_BAND",
    "TUNING": "TUNING",
}

# ---------------------------------------------------------------------------
# 标准化建议模板（priority 1=最高, 2=中, 3=低）
# ---------------------------------------------------------------------------

RECOMMENDATION_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "OSCILLATION": [
        {
            "priority": 1,
            "action": "重新整定PID",
            "description": "降低比例增益或增加积分时间，抑制闭环振荡；建议采用 Lambda 整定法重新计算参数。",
            "target_module": "整定",
        },
        {
            "priority": 2,
            "action": "检查阀门粘滞",
            "description": "PV-OP 散点图呈椭圆轨迹时，阀门可能存在粘滞；建议加阀门定位器或清洁填料。",
            "target_module": "跟踪",
        },
        {
            "priority": 3,
            "action": "排查外部干扰",
            "description": "检查上游工艺参数变化、负荷波动或环境干扰，必要时增加前馈控制。",
            "target_module": "none",
        },
    ],
    "STICTION": [
        {
            "priority": 1,
            "action": "清洁/更换阀门填料",
            "description": "阀门填料老化或污染会导致粘滞；停机检修时清洁或更换填料函。",
            "target_module": "跟踪",
        },
        {
            "priority": 2,
            "action": "加装阀门定位器",
            "description": "智能定位器可补偿阀门非线性与粘滞，提高控制精度。",
            "target_module": "none",
        },
        {
            "priority": 3,
            "action": "检查气动管路",
            "description": "气源压力不足或管路泄漏会导致阀门响应迟缓；检查气源压力与管路密封。",
            "target_module": "none",
        },
    ],
    "SATURATION": [
        {
            "priority": 1,
            "action": "检查工艺负荷",
            "description": "阀门长期处于全开/全关位置，说明工艺负荷超出设计范围；核查当前工况。",
            "target_module": "none",
        },
        {
            "priority": 2,
            "action": "调整阀门尺寸",
            "description": "阀门口径偏小会导致频繁饱和；必要时重新选型或更换阀门。",
            "target_module": "none",
        },
        {
            "priority": 3,
            "action": "检查前段工艺",
            "description": "上游供料不足或压力波动会导致下游阀门饱和；排查前段工艺稳定性。",
            "target_module": "none",
        },
    ],
    "SLUGGISH": [
        {
            "priority": 1,
            "action": "增加PID增益",
            "description": "响应过慢通常是比例增益不足；适当增加 Kp 提高响应速度。",
            "target_module": "整定",
        },
        {
            "priority": 2,
            "action": "减少积分时间",
            "description": "积分时间过长会导致稳态误差消除过慢；适当减少 Ti 提高恢复速度。",
            "target_module": "整定",
        },
        {
            "priority": 3,
            "action": "检查执行机构",
            "description": "执行机构磨损或气源不足会导致响应迟缓；检查气动/电动执行机构状态。",
            "target_module": "跟踪",
        },
    ],
    "DEVIATION": [
        {
            "priority": 1,
            "action": "重新整定PID",
            "description": "PV 与 SP 长期偏差通常是 PID 参数不匹配；重新整定 PID 参数。",
            "target_module": "整定",
        },
        {
            "priority": 2,
            "action": "检查传感器校验",
            "description": "传感器漂移或零点偏移会导致测量偏差；定期校验传感器精度。",
            "target_module": "none",
        },
        {
            "priority": 3,
            "action": "检查设定值合理性",
            "description": "SP 设定超出工艺可达成范围会导致持续偏差；核查 SP 设定值合理性。",
            "target_module": "none",
        },
    ],
    "NOISE": [
        {
            "priority": 1,
            "action": "增加滤波",
            "description": "PV 信号噪声过大时，可在 DCS 中增加一阶低通滤波；注意滤波时间不宜过大。",
            "target_module": "none",
        },
        {
            "priority": 2,
            "action": "检查传感器信号",
            "description": "传感器接线松动、屏蔽不良会导致信号噪声；检查接线与屏蔽接地。",
            "target_module": "none",
        },
        {
            "priority": 3,
            "action": "排查电磁干扰",
            "description": "现场强电设备干扰会耦合到信号回路；检查电缆敷设与电磁兼容性。",
            "target_module": "none",
        },
    ],
    "DEAD_BAND": [
        {
            "priority": 1,
            "action": "调整阀门定位器",
            "description": "定位器死区设置过大导致响应死区；重新校准定位器，减小死区参数。",
            "target_module": "跟踪",
        },
        {
            "priority": 2,
            "action": "减少死区设置",
            "description": "DCS 回路输出死区设置过大；在保证稳定性的前提下减小死区参数。",
            "target_module": "none",
        },
        {
            "priority": 3,
            "action": "检查执行机构间隙",
            "description": "执行机构连杆间隙或齿轮磨损会导致机械死区；检查机械连接件。",
            "target_module": "跟踪",
        },
    ],
    "TUNING": [
        {
            "priority": 1,
            "action": "使用Lambda整定法",
            "description": "Lambda 整定法适用于一阶加纯滞后过程，可指定闭环时间常数实现平滑响应。",
            "target_module": "整定",
        },
        {
            "priority": 2,
            "action": "使用Cohen-Coon法",
            "description": "Cohen-Coon 法适用于开环阶跃响应辨识，对纯滞后补偿效果较好。",
            "target_module": "整定",
        },
        {
            "priority": 3,
            "action": "使用IMC法",
            "description": "内部模型控制（IMC）法适用于模型已知的过程，鲁棒性较好。",
            "target_module": "整定",
        },
    ],
}


def _normalize_label(label: str) -> str | None:
    """将任意诊断标签归一化为推荐模板的 8 类标签之一。

    兼容现有诊断引擎输出的标签（VALVE_STICTION 等）和推荐服务专用标签（STICTION 等）。
    """
    if not label:
        return None
    upper = label.upper()
    return _LABEL_ALIAS_MAP.get(upper)


def get_recommendations(
    loop_id: str,
    tag_codes: list[str],
) -> dict[str, Any]:
    """根据诊断标签返回解决方案推荐。

    Args:
        loop_id: 回路 ID
        tag_codes: 诊断标签列表（支持 8 类标准标签及现有诊断引擎标签）

    Returns:
        {
            "loopId": str,
            "recommendations": [
                {
                    "label": str,            # 标签码
                    "labelName": str,        # 中文标签名
                    "priority": int,         # 1-3
                    "action": str,           # 行动项
                    "description": str,      # 详细描述
                    "targetModule": str,     # 整定/跟踪/none
                },
                ...
            ],
            "totalCount": int,
        }
    """
    recommendations: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    for tag in tag_codes:
        normalized = _normalize_label(tag)
        if normalized is None:
            logger.debug("Unknown diagnosis label skipped: %s", tag)
            continue
        templates = RECOMMENDATION_TEMPLATES.get(normalized, [])
        label_name = RECO_LABEL_NAMES.get(normalized, normalized)
        for tpl in templates:
            key = (normalized, tpl["action"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            recommendations.append(
                {
                    "label": normalized,
                    "labelName": label_name,
                    "priority": tpl["priority"],
                    "action": tpl["action"],
                    "description": tpl["description"],
                    "targetModule": tpl["target_module"],
                }
            )

    # 按 priority 升序排序（1 最高优先）
    recommendations.sort(key=lambda x: (x["priority"], x["label"]))

    return {
        "loopId": loop_id,
        "recommendations": recommendations,
        "totalCount": len(recommendations),
    }


async def get_recommendations_for_loop(
    db: AsyncSession,
    loop_id: str,
) -> dict[str, Any]:
    """从数据库读取回路最新诊断标签，并返回推荐方案。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND
    """
    # 校验回路
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    # 取该回路所有诊断标签（去重）
    diag_result = await db.execute(
        select(DiagnosisResult.diag_label)
        .where(DiagnosisResult.loop_id == loop_id)
        .where(DiagnosisResult.diag_label.is_not(None))
        .distinct()
    )
    tag_codes = [row[0] for row in diag_result.all() if row[0]]

    return get_recommendations(loop_id, tag_codes)


__all__ = [
    "RECO_LABEL_NAMES",
    "RECOMMENDATION_TEMPLATES",
    "get_recommendations",
    "get_recommendations_for_loop",
]
