"""回路工程事实解析与资格定义（P2-04，07 号文 §3.3；P3-03 B1 扩展）.

职责（**先保存/解析工程事实，转换与算法消费归 P3-03**）：
- ``resolve_loop_engineering_facts()``：控制作用方向（DIRECT/REVERSE/UNKNOWN
  显式三态）、实际 OP 上下限（既有来源优先级：回路字段 > OP Tag 量程 >
  默认 0/100，来源随值返回）、PID 表示与时间单位（DCS 型号 →
  dcs_pid_structure；**未知态显式 UNKNOWN，绝不默认标准 kp/ti/td**）、
  工程模板引用（不可变版本 revisionRef）。
- ``get_loop_qualifications()``：**历史片段辨识资格与 PID 应用资格分别
  定义**（07 号文 §3.3 红线；P3-03 B1-4 R3 裁决载体）——历史片段辨识资格
  是数据前提（PV/OP 绑定），不消费 L0/L1 整定门禁；PID 应用资格即现行
  TUNING_FITNESS L0/L1 阻断语义的只读映射（门禁本身零改动）；两资格的
  组合语义由 ``QUALIFICATION_MAPPING`` 映射表显式声明。
- ``resolve_assessment_supply_facts()``（P3-03 B1-5）：A 容限 / T′（理想
  稳态时间）/ 实际 OP 限位对评估侧（P3-08a/09a）的供数——每项带
  值/来源/单位与解释版本，缺事实显式 UNKNOWN，不默认冒充。

红线：
- 未知 DCS 型号（未绑定型号 / 型号未配置 PID 结构 / 结构显式
  representation_status=UNKNOWN）→ ``pidRepresentation.status="UNKNOWN"``
  且不携带任何可被误读为"标准 kp/ti/td"的换算语义。
- 不做单位转换（P3-03 tuning_units）；不动 L0/L1 门禁语义与人工实施边界。
"""

from __future__ import annotations

import copy
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
    """实际 OP 上下限（来源优先级：回路字段 > OP Tag 量程 > 默认 0/100）."""
    lower = getattr(loop, "op_output_lower_limit", None)
    upper = getattr(loop, "op_output_upper_limit", None)
    if lower is not None and upper is not None:
        return {
            "lower": float(lower),
            "upper": float(upper),
            "source": "LOOP_FIELD",
            "note": "回路工程属性 op_output_lower/upper_limit",
        }
    if op_tag and op_tag.get("rangeMin") is not None and op_tag.get("rangeMax") is not None:
        return {
            "lower": float(op_tag["rangeMin"]),
            "upper": float(op_tag["rangeMax"]),
            "source": "TAG_RANGE",
            "note": f"OP 位号量程（{op_tag.get('tagName')}）",
        }
    return {
        "lower": _DEFAULT_OP_LOWER,
        "upper": _DEFAULT_OP_UPPER,
        "source": "DEFAULT_0_100",
        "note": "回路字段与 OP 位号量程均缺失，回落默认 0~100（百分比输出假设）",
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
        # P3-03 B1-2：OP tag 量程（归一化换算基准，与 planner Step③ 同源；
        # None=量程未登记，消费方按 DEFAULT_0_100 显式假设处理，不冒充已核实）
        "opTagRange": {
            "lower": op_tag.get("rangeMin") if op_tag else None,
            "upper": op_tag.get("rangeMax") if op_tag else None,
            "unit": op_tag.get("unit") if op_tag else None,
            "tagName": op_tag.get("tagName") if op_tag else None,
        },
        "pidRepresentation": await _resolve_pid_representation(db, loop),
        "engineeringTemplate": template_ref,
    }


#: ---------------------------------------------------------------------------
#: P3-03 B1-4（R3 已裁决）：历史分析资格 × PID 应用资格 → 允许能力映射表
#: ---------------------------------------------------------------------------

#: 资格组合映射表版本（R3 裁决"分立字段+映射表，门禁不动"的表载体）
QUALIFICATION_MAPPING_VERSION = "R3_QUALIFICATION_MAPPING_V1"

#: 能力常量
CAPABILITY_PASSIVE_HISTORY_IDENTIFICATION = "PASSIVE_HISTORY_IDENTIFICATION"
CAPABILITY_PID_APPLICATION = "PID_APPLICATION"

#: 历史分析资格状态
HISTORY_STATE_ELIGIBLE = "ELIGIBLE"
HISTORY_STATE_INELIGIBLE = "INELIGIBLE"
#: PID 应用资格状态（PASS=门禁放行；BLOCKED_L0_L1=L0/L1 阻断；UNKNOWN=无档位数据）
PID_APP_STATE_PASS = "PASS"
PID_APP_STATE_BLOCKED = "BLOCKED_L0_L1"
PID_APP_STATE_UNKNOWN = "UNKNOWN"

#: 映射表：两资格独立判定的全部组合 → 允许能力（保守：PID 应用状态未知
#: 时按不放行处理——门禁语义零改动，未知不冒充放行）
QUALIFICATION_MAPPING: list[dict[str, Any]] = [
    {
        "historyIdentification": HISTORY_STATE_ELIGIBLE,
        "pidApplication": PID_APP_STATE_PASS,
        "capabilities": [
            CAPABILITY_PASSIVE_HISTORY_IDENTIFICATION,
            CAPABILITY_PID_APPLICATION,
        ],
        "note": "历史片段可被动辨识；PID 参数应用经现行门禁放行",
    },
    {
        "historyIdentification": HISTORY_STATE_ELIGIBLE,
        "pidApplication": PID_APP_STATE_BLOCKED,
        "capabilities": [CAPABILITY_PASSIVE_HISTORY_IDENTIFICATION],
        "note": (
            "历史片段仍可被动辨识（不消费 L0/L1 门禁）；"
            "PID 参数应用被 L0/L1 阻断（ERR_TUNING_FITNESS_INSUFFICIENT）"
        ),
    },
    {
        "historyIdentification": HISTORY_STATE_ELIGIBLE,
        "pidApplication": PID_APP_STATE_UNKNOWN,
        "capabilities": [CAPABILITY_PASSIVE_HISTORY_IDENTIFICATION],
        "note": "历史片段可被动辨识；PID 应用档位未知（无可整定快照），按不放行处理",
    },
    {
        "historyIdentification": HISTORY_STATE_INELIGIBLE,
        "pidApplication": PID_APP_STATE_PASS,
        "capabilities": [CAPABILITY_PID_APPLICATION],
        "note": "PV/OP 绑定缺失仅阻断历史辨识；PID 应用资格独立判定",
    },
    {
        "historyIdentification": HISTORY_STATE_INELIGIBLE,
        "pidApplication": PID_APP_STATE_BLOCKED,
        "capabilities": [],
        "note": "两资格均不成立：无 PV/OP 绑定，且 PID 应用被 L0/L1 阻断",
    },
    {
        "historyIdentification": HISTORY_STATE_INELIGIBLE,
        "pidApplication": PID_APP_STATE_UNKNOWN,
        "capabilities": [],
        "note": "无 PV/OP 绑定；PID 应用档位未知，按不放行处理",
    },
]


def get_qualification_mapping() -> list[dict[str, Any]]:
    """R3 映射表只读访问（返回深拷贝，防调用方原地改写冻结语义）."""
    return copy.deepcopy(QUALIFICATION_MAPPING)


def _resolve_pid_application_state(tune_level: str | None) -> str:
    """PID 应用资格状态（只读映射现行 L0/L1 门禁语义，不改门禁）."""
    if tune_level is None:
        return PID_APP_STATE_UNKNOWN
    if str(tune_level).upper() in {"L0", "L1"}:
        return PID_APP_STATE_BLOCKED
    return PID_APP_STATE_PASS


async def get_loop_qualifications(db: AsyncSession, loop_id: str) -> dict[str, Any]:
    """历史片段辨识资格与 PID 应用资格——**分别定义，互不借用**（07 号文 §3.3）.

    - 历史片段辨识资格（historyIdentification）：数据前提（PV/OP 位号绑定）
      + 工程事实已知性提示；**不消费 L0/L1 整定门禁**（当前手动/低自控或
      数据不足的回路，其历史片段仍可被被动辨识利用）；片段级资格细则
      （激励充分性/工况一致性/连续段质量）由 P3-06b 定义，本批不预填。
    - PID 应用资格（pidApplication）：现行整定门禁的只读映射——
      TUNING_FITNESS L0/L1 阻断（ERR_TUNING_FITNESS_INSUFFICIENT）+
      可整定性 tune 维度最新档位。门禁语义、权限与人工实施边界零改动。
    - P3-03 B1-4（R3）：``mapping`` 节给出两资格组合的允许能力（映射表
      ``QUALIFICATION_MAPPING`` 的命中行 + 表版本），组合语义显式可查。
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

    history_state = HISTORY_STATE_ELIGIBLE if (has_pv and has_op) else HISTORY_STATE_INELIGIBLE
    pid_app_state = _resolve_pid_application_state(tune_level)
    mapping_row = next(
        (
            row
            for row in QUALIFICATION_MAPPING
            if row["historyIdentification"] == history_state
            and row["pidApplication"] == pid_app_state
        ),
        None,
    )

    return {
        "loopId": str(loop.id),
        "tagName": loop.tag_name,
        "historyIdentification": {
            "defined": True,
            "eligible": has_pv and has_op,
            "state": history_state,
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
            "state": pid_app_state,
            "blockingLevels": ["L0", "L1"],
            "errorOnBlocked": "ERR_TUNING_FITNESS_INSUFFICIENT",
            "tuneLevel": tune_level,
            "note": "现行 L0/L1 参数整定门禁、权限与人工实施边界保持不变（只读映射）",
        },
        # P3-03 B1-4（R3 裁决载体）：组合映射命中行 + 表版本（门禁不动）
        "mapping": {
            "version": QUALIFICATION_MAPPING_VERSION,
            "historyIdentification": history_state,
            "pidApplication": pid_app_state,
            "capabilities": list(mapping_row["capabilities"]) if mapping_row else [],
            "note": mapping_row["note"] if mapping_row else None,
        },
    }


#: ---------------------------------------------------------------------------
#: P3-03 B1-5：A 容限 / T′ / 实际 OP 限位供数（评估侧 P3-08a/09a 消费）
#: ---------------------------------------------------------------------------

#: 供数解释版本（D0 草案 §2.5：事实条目携带 value/source/unit/interpretVersion）
ASSESSMENT_SUPPLY_INTERPRETATION_VERSION = "ASSESSMENT_SUPPLY_V1"

#: 供数来源枚举（与 D0 草案 §2.5 三分口径一致：实际/模板/默认假设/未知；
#: O-5 provenance 枚举名由 P3-08a/09a 联合 P3-03 定稿，此处值域保持对齐）
SUPPLY_SOURCE_TEMPLATE = "TEMPLATE"
SUPPLY_SOURCE_LOOP_FIELD = "LOOP_FIELD"
SUPPLY_SOURCE_LOOP_TYPE_DEFAULT = "LOOP_TYPE_DEFAULT"
SUPPLY_SOURCE_UNKNOWN = "UNKNOWN"

#: 工程模板 profile 中 A 容限（工程允许偏差）的载体键（Owner 级定稿：
#: profile 为自由 JSONB，本批固定 {allowedDeviation, allowedDeviationUnit}
#: 为 A 容限载体键，缺失=工程事实侧无该事实，评估侧回落配置链须另标来源）
_TEMPLATE_ALLOWED_DEVIATION_KEY = "allowedDeviation"
_TEMPLATE_ALLOWED_DEVIATION_UNIT_KEY = "allowedDeviationUnit"
#: 工程模板 profile 中 T′（秒）的载体键
_TEMPLATE_IDEAL_SETTLING_KEY = "idealSettlingTimeSeconds"


#: loop_type（回路业务类型）→ 理想稳态时间默认表的键（复用
#: app.utils.ideal_settling_time.DEFAULT_SETTLING_TIMES，不建第二套默认表；
#: ANALYSIS→COMPOSITION、SPEED→OTHER 为 Owner 级映射决策）
def _loop_type_default_settling_seconds(loop_type: str | None) -> float | None:
    """loop_type → 类型默认 T′（秒）；无法映射返回 None（显式未知）."""
    from app.utils.ideal_settling_time import DEFAULT_SETTLING_TIMES, ControlType

    normalized = (loop_type or "").upper()
    mapping = {
        "TEMPERATURE": ControlType.TEMPERATURE,
        "PRESSURE": ControlType.PRESSURE,
        "LEVEL": ControlType.LEVEL,
        "FLOW": ControlType.FLOW,
        "ANALYSIS": ControlType.COMPOSITION,
        "SPEED": ControlType.OTHER,
        "OTHER": ControlType.OTHER,
    }
    key = mapping.get(normalized)
    if key is None:
        return None
    # 映射目标全部是默认表已覆盖的枚举成员；120.0 仅为防御性兜底
    return float(DEFAULT_SETTLING_TIMES.get(key, 120.0))


def _template_profile(template_ref: dict[str, Any] | None) -> dict[str, Any]:
    profile = (template_ref or {}).get("profile")
    return dict(profile) if isinstance(profile, dict) else {}


async def resolve_assessment_supply_facts(db: AsyncSession, loop_id: str) -> dict[str, Any]:
    """A 容限 / T′ / 实际 OP 限位供数（P3-03 B1-5，评估侧字段预留的数据源）.

    与 P3-08a/09a 的对接面（任务书 A §2.3"具体数值由 P3-03 供，本批只做
    载体"）：本函数是**工程事实侧**唯一供数入口，每项携带
    ``value/source/unit/interpretationVersion``；缺事实显式 ``source=UNKNOWN``，
    不默认冒充（默认 OP=0/100 只说明假设来源，不自动满足真实限位前提）。

    - **A 容限**（accuracy_rate 允许偏差）：工程模板 profile
      ``allowedDeviation``（工程值）+ ``allowedDeviationUnit``；缺失 →
      UNKNOWN（评估侧回落 algorithm_config 配置链时须自行标注来源，
      不得当作已核实工艺容限）。
    - **T′**（理想稳态时间，秒）：回路字段 ``ideal_settling_time`` >
      模板 profile ``idealSettlingTimeSeconds`` > 回路类型默认值（复用
      ``app.utils.ideal_settling_time`` 同一张默认表）。**模型法
      （α·(τ+θ)）不在本供数**——10 号文 §3.4：模型法只消费 P3-06 已通过
      独立终验且工况匹配的模型，不能将粗估 τ 直接注入。
    - **实际 OP 限位**：复用 ``_resolve_op_limits`` 三分（LOOP_FIELD/
      TAG_RANGE/DEFAULT_0_100）。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND(404)
    """
    loop = await _load_loop_or_404(db, loop_id)
    tags = await _load_tag_facts(db, loop_id)
    op_tag = tags.get("OP")
    pv_tag = tags.get("PV")
    template_ref = await resolve_template_reference(db, loop)
    profile = _template_profile(template_ref)
    revision_ref = (template_ref or {}).get("revisionRef")

    # A 容限（工程允许偏差，PV 工程量纲）
    allowed = profile.get(_TEMPLATE_ALLOWED_DEVIATION_KEY)
    if isinstance(allowed, (int, float)) and not isinstance(allowed, bool) and float(allowed) > 0:
        a_tolerance = {
            "value": float(allowed),
            "source": SUPPLY_SOURCE_TEMPLATE,
            "unit": profile.get(_TEMPLATE_ALLOWED_DEVIATION_UNIT_KEY) or (pv_tag or {}).get("unit"),
            "basis": "ENGINEERING",
            "revisionRef": revision_ref,
            "note": "工程模板 profile 允许偏差（PV 工程量纲）；与归一化信号比较前须换算同量纲",
        }
    else:
        a_tolerance = {
            "value": None,
            "source": SUPPLY_SOURCE_UNKNOWN,
            "unit": None,
            "basis": "ENGINEERING",
            "revisionRef": revision_ref,
            "note": (
                "工程事实侧无允许偏差事实（模板未携带）；评估侧回落配置链"
                "（e_max_tolerance_ratio）时须另行标注来源，不得当作已核实工艺容限"
            ),
        }

    # T′（理想稳态时间，秒）
    manual_t = getattr(loop, "ideal_settling_time", None)
    template_t = profile.get(_TEMPLATE_IDEAL_SETTLING_KEY)
    if (
        isinstance(manual_t, (int, float))
        and not isinstance(manual_t, bool)
        and float(manual_t) > 0
    ):
        t_prime = {
            "valueSeconds": float(manual_t),
            "source": SUPPLY_SOURCE_LOOP_FIELD,
            "note": "回路级手动配置（loop_ledger.ideal_settling_time，最高优先级）",
        }
    elif (
        isinstance(template_t, (int, float))
        and not isinstance(template_t, bool)
        and float(template_t) > 0
    ):
        t_prime = {
            "valueSeconds": float(template_t),
            "source": SUPPLY_SOURCE_TEMPLATE,
            "revisionRef": revision_ref,
            "note": "工程模板 profile idealSettlingTimeSeconds",
        }
    elif (
        default_t := _loop_type_default_settling_seconds(getattr(loop, "loop_type", None))
    ) is not None:
        t_prime = {
            "valueSeconds": default_t,
            "source": SUPPLY_SOURCE_LOOP_TYPE_DEFAULT,
            "note": (
                f"回路类型默认值（loop_type={getattr(loop, 'loop_type', None)}，"
                "复用 ideal_settling_time 默认表）"
            ),
        }
    else:
        t_prime = {
            "valueSeconds": None,
            "source": SUPPLY_SOURCE_UNKNOWN,
            "note": "回路无手动配置/模板值，且 loop_type 缺失无法取类型默认（模型法不在此供数）",
        }
    t_prime["modelBasedNote"] = (
        "模型法 T′（α·(τ+θ)）不在本供数：只消费 P3-06 已通过独立终验"
        "且工况匹配的模型（10 号文 §3.4）"
    )

    return {
        "loopId": str(loop.id),
        "tagName": loop.tag_name,
        "interpretationVersion": ASSESSMENT_SUPPLY_INTERPRETATION_VERSION,
        "aTolerance": a_tolerance,
        "idealSettlingTime": t_prime,
        "opLimits": _resolve_op_limits(loop, op_tag),
        "pvRange": {
            "lower": pv_tag.get("rangeMin") if pv_tag else None,
            "upper": pv_tag.get("rangeMax") if pv_tag else None,
            "unit": pv_tag.get("unit") if pv_tag else None,
        },
        "opTagRange": {
            "lower": op_tag.get("rangeMin") if op_tag else None,
            "upper": op_tag.get("rangeMax") if op_tag else None,
            "unit": op_tag.get("unit") if op_tag else None,
        },
        "engineeringTemplateRevision": revision_ref,
    }


__all__ = [
    "ASSESSMENT_SUPPLY_INTERPRETATION_VERSION",
    "CAPABILITY_PASSIVE_HISTORY_IDENTIFICATION",
    "CAPABILITY_PID_APPLICATION",
    "CONTROL_ACTION_DIRECT",
    "CONTROL_ACTION_REVERSE",
    "CONTROL_ACTION_UNKNOWN",
    "PID_APP_STATE_BLOCKED",
    "PID_APP_STATE_PASS",
    "PID_APP_STATE_UNKNOWN",
    "HISTORY_STATE_ELIGIBLE",
    "HISTORY_STATE_INELIGIBLE",
    "QUALIFICATION_MAPPING",
    "QUALIFICATION_MAPPING_VERSION",
    "SUPPLY_SOURCE_LOOP_FIELD",
    "SUPPLY_SOURCE_LOOP_TYPE_DEFAULT",
    "SUPPLY_SOURCE_TEMPLATE",
    "SUPPLY_SOURCE_UNKNOWN",
    "VALID_CONTROL_ACTION_DIRECTIONS",
    "get_loop_qualifications",
    "get_qualification_mapping",
    "resolve_assessment_supply_facts",
    "resolve_loop_engineering_facts",
]
