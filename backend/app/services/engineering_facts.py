"""回路工程事实解析与资格定义（P2-04，07 号文 §3.3）.

职责（**先保存/解析工程事实，转换与算法消费归 P3-03**）：
- ``resolve_loop_engineering_facts()``：控制作用方向（DIRECT/REVERSE/UNKNOWN
  显式三态）、实际 OP 上下限（既有来源优先级：回路字段 > OP Tag 量程 >
  默认 0/100，来源随值返回）、PID 表示与时间单位（DCS 型号 →
  dcs_pid_structure；**未知态显式 UNKNOWN，绝不默认标准 kp/ti/td**）、
  工程模板引用（不可变版本 revisionRef）。
- ``get_loop_qualifications()``：**历史片段辨识资格与 PID 应用资格分别
  定义**（07 号文 §3.3 红线）——历史片段辨识资格是数据前提（PV/OP 绑定），
  不消费 L0/L1 整定门禁；PID 应用资格即现行 TUNING_FITNESS L0/L1 阻断
  语义的只读映射（门禁本身零改动）。

红线：
- 未知 DCS 型号（未绑定型号 / 型号未配置 PID 结构 / 结构显式
  representation_status=UNKNOWN）→ ``pidRepresentation.status="UNKNOWN"``
  且不携带任何可被误读为"标准 kp/ti/td"的换算语义。
- 不做单位转换（P3-03）；不动 L0/L1 门禁语义与人工实施边界。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.dcs_model import DcsModel
from app.models.dcs_pid_structure import (
    PID_REPRESENTATION_KNOWN,
    PID_REPRESENTATION_UNKNOWN,
    DcsPidStructure,
)
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.tag import TagRegistry
from app.services.engineering_template import resolve_template_reference

logger = logging.getLogger(__name__)

#: 控制作用方向合法值（loop_ledger.control_action_direction）
CONTROL_ACTION_DIRECT = "DIRECT"
CONTROL_ACTION_REVERSE = "REVERSE"
CONTROL_ACTION_UNKNOWN = "UNKNOWN"
VALID_CONTROL_ACTION_DIRECTIONS = (
    CONTROL_ACTION_DIRECT,
    CONTROL_ACTION_REVERSE,
    CONTROL_ACTION_UNKNOWN,
)

#: OP 限幅默认值（来源优先级第三级；与既有 v6.1 设计一致）
_DEFAULT_OP_LOWER, _DEFAULT_OP_UPPER = 0.0, 100.0

#: 工程事实解释版本（O-5 载体：事实内容变化进入规范化快照/hash 的版本位；
# 语义变更时递增并登记变更说明——只加不改）
ENGINEERING_FACTS_INTERPRETATION_VERSION = "1"


def _op_limits_provenance(source: str) -> str:
    """既有 opLimits.source → O-5 定稿 provenance 枚举映射（三分语义）.

    LOOP_FIELD/TAG_RANGE=ACTUAL（已核实有来源）；DEFAULT_0_100=DEFAULT
    （默认假设，不当已核实限位——契约 §2.5）。
    """
    from app.contracts.data_types import EngineeringFactProvenance

    if source == "DEFAULT_0_100":
        return EngineeringFactProvenance.DEFAULT
    return EngineeringFactProvenance.ACTUAL


def _dcs_template_revision(loop: LoopLedger) -> str:
    """DCS 模板 revision（与 tuning._dcs_template_revision 同口径）."""
    dcs_model_id = getattr(loop, "dcs_model_id", None)
    if dcs_model_id:
        return f"dcs_model:{dcs_model_id}"
    return "UNKNOWN_DCS_TEMPLATE"


async def _load_loop_or_404(db: AsyncSession, loop_id: str) -> LoopLedger:
    loop = (
        await db.execute(select(LoopLedger).where(LoopLedger.id == str(loop_id)))
    ).scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message=f"回路不存在: {loop_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return loop


async def _load_tag_facts(db: AsyncSession, loop_id: str) -> dict[str, dict[str, Any] | None]:
    """回路绑定 Tag 的量程/单位（按角色；OP/PV）."""
    rows = (
        await db.execute(
            select(LoopTagMapping.tag_role, TagRegistry)
            .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
            .where(LoopTagMapping.loop_id == str(loop_id))
        )
    ).all()
    out: dict[str, dict[str, Any] | None] = {"OP": None, "PV": None}
    for role, tag in rows:
        if role in out and out[role] is None:
            out[role] = {
                "tagId": str(tag.id),
                "tagName": tag.tag_name,
                "rangeMin": float(tag.range_min) if tag.range_min is not None else None,
                "rangeMax": float(tag.range_max) if tag.range_max is not None else None,
                "unit": tag.unit,
            }
    return out


def _resolve_op_limits(loop: LoopLedger, op_tag: dict[str, Any] | None) -> dict[str, Any]:
    """实际 OP 上下限（来源优先级：回路字段 > OP Tag 量程 > 默认 0/100）.

    P3-08a（O-5 定稿）：条目补 ``provenance``（三分语义：ACTUAL/DEFAULT/
    UNKNOWN——DEFAULT_0_100 只是默认假设，不当已核实限位）、``unit`` 与
    ``interpretationVersion``（契约 §2.5：value+source+unit+
    interpretationVersion 四件套；只加不改，既有读端按缺键忽略）。
    """
    unit = (op_tag or {}).get("unit")
    lower = getattr(loop, "op_output_lower_limit", None)
    upper = getattr(loop, "op_output_upper_limit", None)
    if lower is not None and upper is not None:
        base = {
            "lower": float(lower),
            "upper": float(upper),
            "source": "LOOP_FIELD",
            "note": "回路工程属性 op_output_lower/upper_limit",
        }
    elif op_tag and op_tag.get("rangeMin") is not None and op_tag.get("rangeMax") is not None:
        base = {
            "lower": float(op_tag["rangeMin"]),
            "upper": float(op_tag["rangeMax"]),
            "source": "TAG_RANGE",
            "note": f"OP 位号量程（{op_tag.get('tagName')}）",
        }
    else:
        base = {
            "lower": _DEFAULT_OP_LOWER,
            "upper": _DEFAULT_OP_UPPER,
            "source": "DEFAULT_0_100",
            "note": "回路字段与 OP 位号量程均缺失，回落默认 0~100（百分比输出假设）",
        }
    base.update(
        {
            "provenance": _op_limits_provenance(base["source"]),
            "unit": unit,
            "interpretationVersion": ENGINEERING_FACTS_INTERPRETATION_VERSION,
        }
    )
    return base


def _resolve_reference_values(loop: LoopLedger) -> dict[str, Any]:
    """A 容限/T′ 参考值载体（P3-08a 预留；具体数值由 P3-03 供，09a 消费）.

    - ``aTolerance``（A 容限）：本批显式 UNKNOWN（value=null——数值由
      P3-03 从工艺要求/工程事实供；未知不虚构，契约 §2.6"对应真实工艺
      要求，避免容限随不合适量程变宽"）；
    - ``tPrime``（理想 settling time）：``loop.ideal_settling_time`` 有值
      → origin=MANUAL；无值 → origin=DEFAULT（类型默认回退，显式标注，
      10§3.4"保留手动优先/默认回退且显式标来源"）；VERIFIED_MODEL 待
      P3-06 独立终验通过后由消费方接入，本载体不预填。
    """
    from app.contracts.data_types import EngineeringFactProvenance, ReferenceValueOrigin

    manual_tprime = getattr(loop, "ideal_settling_time", None)
    if manual_tprime is not None:
        t_prime = {
            "value": float(manual_tprime),
            "origin": ReferenceValueOrigin.MANUAL,
            "sourceField": "loop_ledger.ideal_settling_time",
            "note": "手动指定的理想稳态时间（手动优先）",
        }
    else:
        t_prime = {
            "value": None,
            "origin": ReferenceValueOrigin.DEFAULT,
            "sourceField": None,
            "note": "未手动指定，消费方按控制类型默认回退（显式标注来源=默认）",
        }
    t_prime["unit"] = "s"
    t_prime["interpretationVersion"] = ENGINEERING_FACTS_INTERPRETATION_VERSION
    return {
        "aTolerance": {
            "value": None,
            "provenance": EngineeringFactProvenance.UNKNOWN,
            "unit": None,
            "interpretationVersion": ENGINEERING_FACTS_INTERPRETATION_VERSION,
            "note": "P3-08a 预留载体：A 容限数值由 P3-03 联合供数（真实工艺要求），未知不虚构",
        },
        "tPrime": t_prime,
    }


async def _resolve_pid_representation(db: AsyncSession, loop: LoopLedger) -> dict[str, Any]:
    """PID 表示与时间单位（未知态显式 UNKNOWN——禁止默认标准 kp/ti/td）."""
    unknown: dict[str, Any] = {
        "status": PID_REPRESENTATION_UNKNOWN,
        "modelId": None,
        "modelCode": None,
        "pType": None,
        "iUnit": None,
        "dUnit": None,
        "dFilterEnabled": None,
        "dFilterUnit": None,
        "dFilterMultiplier": None,
        "dcsTemplateRevision": _dcs_template_revision(loop),
    }
    dcs_model_id = getattr(loop, "dcs_model_id", None)
    if not dcs_model_id:
        return {**unknown, "reason": "回路未绑定 DCS 型号（未知 DCS 型号，不得默认标准 kp/ti/td）"}
    model = (
        await db.execute(select(DcsModel).where(DcsModel.id == str(dcs_model_id)))
    ).scalar_one_or_none()
    if model is None:
        return {**unknown, "reason": "绑定的 DCS 型号不存在（数据不一致，按未知处理）"}
    structure = (
        await db.execute(
            select(DcsPidStructure).where(DcsPidStructure.dcs_model_id == str(dcs_model_id))
        )
    ).scalar_one_or_none()
    if structure is None:
        return {
            **unknown,
            "modelId": str(model.id),
            "modelCode": model.code,
            "reason": f"DCS 型号 {model.code} 未配置 PID 结构（表示未知）",
        }
    status_value = getattr(structure, "representation_status", PID_REPRESENTATION_KNOWN)
    if status_value == PID_REPRESENTATION_UNKNOWN:
        return {
            **unknown,
            "modelId": str(model.id),
            "modelCode": model.code,
            "reason": (
                f"DCS 型号 {model.code} 的 PID 表示显式标记为 UNKNOWN"
                "（p_type/i_unit/d_unit 无转换语义，转换归 P3-03 前拒绝）"
            ),
        }
    return {
        "status": PID_REPRESENTATION_KNOWN,
        "modelId": str(model.id),
        "modelCode": model.code,
        "pType": structure.p_type,
        "iUnit": structure.i_unit,
        "dUnit": structure.d_unit,
        "dFilterEnabled": bool(structure.d_filter_enabled),
        "dFilterUnit": structure.d_filter_unit,
        "dFilterMultiplier": bool(structure.d_filter_multiplier),
        "dcsTemplateRevision": _dcs_template_revision(loop),
        "reason": None,
    }


async def resolve_loop_engineering_facts(db: AsyncSession, loop_id: str) -> dict[str, Any]:
    """解析回路工程事实全集（只读；辨识包 manifest 1.2 engineeringFacts 同构）.

    Raises:
        BizError: ERR_LOOP_NOT_FOUND(404)
    """
    loop = await _load_loop_or_404(db, loop_id)
    tags = await _load_tag_facts(db, loop_id)
    op_tag = tags.get("OP")
    pv_tag = tags.get("PV")
    template_ref = await resolve_template_reference(db, loop)
    return {
        "loopId": str(loop.id),
        "tagName": loop.tag_name,
        "controlActionDirection": getattr(loop, "control_action_direction", CONTROL_ACTION_UNKNOWN)
        or CONTROL_ACTION_UNKNOWN,
        "opLimits": _resolve_op_limits(loop, op_tag),
        "pvRange": {
            "lower": pv_tag.get("rangeMin") if pv_tag else None,
            "upper": pv_tag.get("rangeMax") if pv_tag else None,
            "unit": pv_tag.get("unit") if pv_tag else None,
            "tagName": pv_tag.get("tagName") if pv_tag else None,
        },
        "pidRepresentation": await _resolve_pid_representation(db, loop),
        "engineeringTemplate": template_ref,
        # P3-08a（O-5 定稿）：A 容限/T′ 参考值载体（provenance/origin 显式
        # 标来源；数值 P3-03 供、消费 09a；只加不改）
        "referenceValues": _resolve_reference_values(loop),
    }


async def get_loop_qualifications(db: AsyncSession, loop_id: str) -> dict[str, Any]:
    """历史片段辨识资格与 PID 应用资格——**分别定义，互不借用**（07 号文 §3.3）.

    - 历史片段辨识资格（historyIdentification）：数据前提（PV/OP 位号绑定）
      + 工程事实已知性提示；**不消费 L0/L1 整定门禁**（当前手动/低自控或
      数据不足的回路，其历史片段仍可被被动辨识利用）；片段级资格细则
      （激励充分性/工况一致性/连续段质量）由 P3-06b 定义，本批不预填。
    - PID 应用资格（pidApplication）：现行整定门禁的只读映射——
      TUNING_FITNESS L0/L1 阻断（ERR_TUNING_FITNESS_INSUFFICIENT）+
      可整定性 tune 维度最新档位。门禁语义、权限与人工实施边界零改动。
    """
    loop = await _load_loop_or_404(db, loop_id)
    tags = await _load_tag_facts(db, loop_id)
    has_pv = tags.get("PV") is not None
    has_op = tags.get("OP") is not None
    pid_repr = await _resolve_pid_representation(db, loop)

    tune_level: str | None = None
    try:
        from app.services.loop_fitness import get_latest_fitness_per_loop

        latest = await get_latest_fitness_per_loop(db, [str(loop.id)])
        entry = latest.get(str(loop.id))
        tune_level = entry.tune_level_effective if entry else None
    except Exception:  # noqa: BLE001
        logger.debug("读取回路最新可整定档失败（资格面按未知报告）", exc_info=True)

    return {
        "loopId": str(loop.id),
        "tagName": loop.tag_name,
        "historyIdentification": {
            "defined": True,
            "eligible": has_pv and has_op,
            "criteria": [
                "回路已绑定 PV 位号（历史过程可提取）",
                "回路已绑定 OP 位号（历史过程可提取）",
            ],
            "engineeringFactsKnown": {
                "controlActionDirection": getattr(
                    loop, "control_action_direction", CONTROL_ACTION_UNKNOWN
                )
                != CONTROL_ACTION_UNKNOWN,
                "pidRepresentation": pid_repr["status"] == PID_REPRESENTATION_KNOWN,
            },
            "note": (
                "与 PID 应用资格相互独立（07 号文 §3.3）：不消费 L0/L1 整定门禁；"
                "片段级资格（激励/工况/连续段）由 P3-06b 定义，此处仅数据前提"
            ),
        },
        "pidApplication": {
            "defined": True,
            "gate": "TUNING_FITNESS_L0_L1",
            "blockingLevels": ["L0", "L1"],
            "errorOnBlocked": "ERR_TUNING_FITNESS_INSUFFICIENT",
            "tuneLevel": tune_level,
            "note": "现行 L0/L1 参数整定门禁、权限与人工实施边界保持不变（只读映射）",
        },
    }


__all__ = [
    "CONTROL_ACTION_DIRECT",
    "CONTROL_ACTION_REVERSE",
    "CONTROL_ACTION_UNKNOWN",
    "ENGINEERING_FACTS_INTERPRETATION_VERSION",
    "VALID_CONTROL_ACTION_DIRECTIONS",
    "get_loop_qualifications",
    "resolve_loop_engineering_facts",
]
