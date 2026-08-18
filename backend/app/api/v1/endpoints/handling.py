"""处置模块 API（Phase 1 后端：5 个流转端点）。

设计文档：docs/MVP设计/08-处置模块设计方案.md §4 状态机 / §6 API 定义
端点清单（前缀 /handling）：
- POST /items/{id}/start           开始处置（PENDING/REOPENED → HANDLING）
- POST /items/{id}/submit          提交验证（HANDLING → VERIFYING，按类型校验必填子字段）
- POST /items/{id}/verify          验证结论（VERIFYING → CLOSED/REOPENED，服务端固化 KPI 前后快照）
- POST /items/{id}/ignore          忽略（PENDING → IGNORED，ignore_reason 必填）
- POST /items/{id}/kpi-comparison  KPI 前后对比预览（VERIFYING 阶段，不落库）

状态机后端强校验：所有非法迁移返回 ERR_STATE 400（§4.2 流转表为唯一合法迁移）。
CLOSED / IGNORED 为终态，CLOSED 不允许重开（评审决策 #9）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.diagnosis_run import DiagnosisRun
from app.models.loop_action_item import ACTION_TYPES, LoopActionItem
from app.models.metric import KpiSnapshotHourly
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success

router = APIRouter(prefix="/handling", tags=["handling"])

#: 允许处置流转的角色（复用诊断触发角色口径 _DIAGNOSIS_TRIGGER_ROLES，§7）
_HANDLING_ROLES = ("IC_ENGINEER", "PE_ENGINEER", "ADMIN")

#: 处置类型中文名（§5.1）
_ACTION_TYPE_LABELS = {
    "TUNING": "参数整定",
    "VALVE": "阀门检修",
    "INSTRUMENT": "仪表校验",
    "LINK": "链路修复",
    "PROCESS": "工艺调整",
    "UTILIZATION": "恢复投用",
    "RECONFIG": "组态改造",
    "OTHER": "其他",
}

#: 状态中文名（§4.1）
_STATUS_LABELS = {
    "PENDING": "待处置",
    "HANDLING": "处置中",
    "VERIFYING": "验证中",
    "CLOSED": "已闭环",
    "REOPENED": "重开",
    "IGNORED": "已忽略",
}

#: KPI 验证窗口时长（§4.3：前后各 24h）
_KPI_WINDOW = timedelta(hours=24)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(dt: datetime | None) -> str | None:
    """naive UTC → ISO + Z（前端补 Z 转本地，同诊断模块口径）。"""
    return dt.isoformat() + "Z" if dt else None


def _err_param(message: str) -> BizError:
    return BizError(code="ERR_PARAM", message=message, status_code=400)


def _err_state(item: LoopActionItem, action: str) -> BizError:
    return BizError(
        code="ERR_STATE",
        message=(
            f"当前状态不允许{action}：{_STATUS_LABELS.get(item.status, item.status)}"
            f"（{item.status}）"
        ),
        status_code=400,
    )


# ---------------------------------------------------------------------------
# 请求体（业务必填/枚举校验在端点内手工执行，保证 ERR_PARAM 400 口径，
# 不走 pydantic Field 约束的 422 通道）
# ---------------------------------------------------------------------------


class StartBody(BaseModel):
    """开始处置请求体（§6.2：actionType 必填；handler 缺省=当前登录用户）。"""

    actionType: str | None = None
    handler: str | None = Field(None, max_length=64)
    actionDetail: dict[str, Any] | None = None
    pidBefore: dict[str, Any] | None = None


class SubmitBody(BaseModel):
    """提交验证请求体（§6.2：actionDetail 必填，按类型校验子字段）。"""

    actionDetail: dict[str, Any] | None = None


class VerifyBody(BaseModel):
    """验证结论请求体（§6.2：verifyResult 必填 EFFECTIVE/INEFFECTIVE）。"""

    verifyResult: str | None = None
    verifyNote: str | None = Field(None, max_length=500)
    verifyRunId: str | None = None


class IgnoreBody(BaseModel):
    """忽略请求体（§6.2：ignoreReason 必填）。"""

    ignoreReason: str | None = Field(None, max_length=200)


# ---------------------------------------------------------------------------
# 序列化与公共查询
# ---------------------------------------------------------------------------


def _item_to_dict(row: LoopActionItem) -> dict[str, Any]:
    return {
        "id": row.id,
        "runId": row.run_id,
        "loopId": row.loop_id,
        "source": row.source,
        "category": row.category,
        "content": row.content,
        "basis": row.basis,
        "priority": row.priority,
        "status": row.status,
        "statusLabel": _STATUS_LABELS.get(row.status, row.status),
        "suggestedBy": row.suggested_by,
        "suggestedAt": _iso(row.suggested_at),
        "actionType": row.action_type,
        "actionTypeLabel": (
            _ACTION_TYPE_LABELS.get(row.action_type, row.action_type) if row.action_type else None
        ),
        "actionDetail": row.action_detail,
        "handledBy": row.handled_by,
        "handledAt": _iso(row.handled_at),
        "submittedAt": _iso(row.submitted_at),
        "verifyRunId": row.verify_run_id,
        "verifyResult": row.verify_result,
        "verifyResultLabel": (
            {"EFFECTIVE": "有效", "INEFFECTIVE": "无效"}.get(row.verify_result)
            if row.verify_result
            else None
        ),
        "verifyNote": row.verify_note,
        "verifiedBy": row.verified_by,
        "verifiedAt": _iso(row.verified_at),
        "kpiBefore": row.kpi_before,
        "kpiAfter": row.kpi_after,
        "tuningRecordId": row.tuning_record_id,
        "ignoreReason": row.ignore_reason,
        "updatedAt": _iso(row.updated_at),
    }


async def _get_item_or_404(db: AsyncSession, item_id: str) -> LoopActionItem:
    row = (
        await db.execute(select(LoopActionItem).where(LoopActionItem.id == item_id))
    ).scalar_one_or_none()
    if row is None:
        raise BizError(code="ERR_NOT_FOUND", message=f"处置项不存在: {item_id}", status_code=404)
    return row


def _kpi_summary(snap: KpiSnapshotHourly | None) -> dict[str, Any] | None:
    """KPI 快照摘要（§4.3：score + 六率 + 可信度 + 窗口；无快照侧为 None）。"""

    def _f(v: Any) -> float | None:
        return float(v) if v is not None else None

    if snap is None:
        return None
    return {
        "score": _f(snap.score),
        "goodValueRate": _f(snap.good_value_rate),
        "effectiveAutoRate": _f(snap.effective_auto_rate),
        "steadyRate": _f(snap.steady_rate),
        "accuracyRate": _f(snap.accuracy_rate),
        "fastRate": _f(snap.fast_rate),
        "oscillationRate": _f(snap.oscillation_rate),
        "saturationRate": _f(snap.saturation_rate),
        "confidenceLevel": snap.confidence_level,
        "tsStart": _iso(snap.ts_start),
        "tsEnd": _iso(snap.ts_end),
    }


async def _latest_snapshot_in_window(
    db: AsyncSession, loop_id: str, win_start: datetime, win_end: datetime
) -> KpiSnapshotHourly | None:
    """窗口内最新一条有 score 的 kpi_snapshot_hourly 记录（§4.3 快照来源）。"""
    return (
        await db.execute(
            select(KpiSnapshotHourly)
            .where(
                KpiSnapshotHourly.loop_id == loop_id,
                KpiSnapshotHourly.score.is_not(None),
                KpiSnapshotHourly.ts_start >= win_start,
                KpiSnapshotHourly.ts_start <= win_end,
            )
            .order_by(KpiSnapshotHourly.ts_start.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _pull_kpi_windows(
    db: AsyncSession, item: LoopActionItem
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """拉取 KPI 前后窗口快照摘要（§4.3：前窗以 handled_at 为界，后窗以 submitted_at 为界）。

    - kpi_before：[handled_at − 24h, handled_at]（处置开始前的最后基线）
    - kpi_after：[submitted_at, submitted_at + 24h]（提交验证后线上效果）
    两侧窗口隔离：处置执行期数据不参与对比；窗口不足/无快照侧为 None。
    """
    before = after = None
    if item.handled_at:
        snap = await _latest_snapshot_in_window(
            db, item.loop_id, item.handled_at - _KPI_WINDOW, item.handled_at
        )
        before = _kpi_summary(snap)
    if item.submitted_at:
        snap = await _latest_snapshot_in_window(
            db, item.loop_id, item.submitted_at, item.submitted_at + _KPI_WINDOW
        )
        after = _kpi_summary(snap)
    return before, after


def _validate_action_detail(action_type: str | None, detail: dict[str, Any]) -> None:
    """submit 按类型校验必填子字段（§5.2：TUNING 必填 pidAfter；其余仅要求非空对象）。"""
    if not detail:
        raise _err_param("actionDetail 必填且为非空对象")
    if action_type == "TUNING":
        pid_after = detail.get("pidAfter")
        if not isinstance(pid_after, dict) or not pid_after:
            raise _err_param("TUNING 类型提交验证时 actionDetail.pidAfter 必填（非空对象）")


# ---------------------------------------------------------------------------
# 流转端点（§6.2）
# ---------------------------------------------------------------------------


@router.post("/items/{item_id}/start", response_model=ApiResponse[dict])
async def start_handling(
    item_id: str,
    body: StartBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """开始处置（PENDING/REOPENED → HANDLING）。

    守卫：action_type 必填且为 8 类枚举；handler 手工填写（缺省=当前登录用户）。
    REOPENED 再 start 时清空上一轮验证字段，待新轮回（§4.2 #3）。
    """
    item = await _get_item_or_404(db, item_id)
    if item.status not in ("PENDING", "REOPENED"):
        raise _err_state(item, "开始处置")
    if not body.actionType:
        raise _err_param("actionType 必填")
    if body.actionType not in ACTION_TYPES:
        raise _err_param(f"actionType 非法: {body.actionType}（合法值: {', '.join(ACTION_TYPES)}）")

    detail: dict[str, Any] = dict(body.actionDetail) if body.actionDetail else {}
    if body.pidBefore is not None:
        # TUNING 类型建议开始处置时回填调整前 P/I/D（§6.2）
        detail["pidBefore"] = body.pidBefore

    item.action_type = body.actionType
    item.action_detail = detail or None
    item.handled_by = body.handler or user.username
    item.handled_at = _utcnow_naive()
    if item.status == "REOPENED":
        # 新一轮处置：清空上一轮验证字段
        item.submitted_at = None
        item.verify_run_id = None
        item.verify_result = None
        item.verify_note = None
        item.verified_by = None
        item.verified_at = None
        item.kpi_before = None
        item.kpi_after = None
    item.status = "HANDLING"
    await db.commit()
    return success(_item_to_dict(item))


@router.post("/items/{item_id}/submit", response_model=ApiResponse[dict])
async def submit_handling(
    item_id: str,
    body: SubmitBody,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """提交验证（HANDLING → VERIFYING）。

    守卫：action_detail 必填（TUNING 必填 pidAfter）；与开始处置时已填详情合并
    （保留 pidBefore 等先填字段），提交键覆盖同名键。
    """
    item = await _get_item_or_404(db, item_id)
    if item.status != "HANDLING":
        raise _err_state(item, "提交验证")
    _validate_action_detail(item.action_type, body.actionDetail or {})

    merged: dict[str, Any] = dict(item.action_detail or {})
    merged.update(body.actionDetail or {})
    item.action_detail = merged
    item.submitted_at = _utcnow_naive()
    item.status = "VERIFYING"
    await db.commit()
    return success(_item_to_dict(item))


@router.post("/items/{item_id}/verify", response_model=ApiResponse[dict])
async def verify_handling(
    item_id: str,
    body: VerifyBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """验证结论（VERIFYING → CLOSED/REOPENED）。

    服务端此刻固化 kpi_before/kpi_after（防快照滚动导致对比漂移，§4.3），
    再落 verify 字段；验证人=当前登录用户。
    """
    item = await _get_item_or_404(db, item_id)
    if item.status != "VERIFYING":
        raise _err_state(item, "验证结论")
    if body.verifyResult not in ("EFFECTIVE", "INEFFECTIVE"):
        raise _err_param("verifyResult 必填且为 EFFECTIVE / INEFFECTIVE")
    if body.verifyRunId is not None:
        run = (
            await db.execute(select(DiagnosisRun.id).where(DiagnosisRun.id == body.verifyRunId))
        ).scalar_one_or_none()
        if run is None:
            raise BizError(
                code="ERR_PARAM",
                message=f"复诊诊断记录不存在: {body.verifyRunId}",
                status_code=400,
            )

    kpi_before, kpi_after = await _pull_kpi_windows(db, item)
    item.kpi_before = kpi_before
    item.kpi_after = kpi_after
    item.verify_result = body.verifyResult
    item.verify_note = body.verifyNote
    item.verify_run_id = body.verifyRunId
    item.verified_by = user.username
    item.verified_at = _utcnow_naive()
    item.status = "CLOSED" if body.verifyResult == "EFFECTIVE" else "REOPENED"
    await db.commit()
    return success(_item_to_dict(item))


@router.post("/items/{item_id}/ignore", response_model=ApiResponse[dict])
async def ignore_handling(
    item_id: str,
    body: IgnoreBody,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """忽略（PENDING → IGNORED 终态）。守卫：ignore_reason 必填。"""
    item = await _get_item_or_404(db, item_id)
    if item.status != "PENDING":
        raise _err_state(item, "忽略")
    if not body.ignoreReason or not body.ignoreReason.strip():
        raise _err_param("ignoreReason 必填")
    item.ignore_reason = body.ignoreReason.strip()
    item.status = "IGNORED"
    await db.commit()
    return success(_item_to_dict(item))


@router.post("/items/{item_id}/kpi-comparison", response_model=ApiResponse[dict])
async def kpi_comparison(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """KPI 前后对比预览（VERIFYING 阶段实时拉取，不落库；verify 时才固化）。"""
    item = await _get_item_or_404(db, item_id)
    if item.status != "VERIFYING":
        raise _err_state(item, "KPI 对比预览")
    kpi_before, kpi_after = await _pull_kpi_windows(db, item)
    return success(
        {
            "id": item.id,
            "loopId": item.loop_id,
            "kpiBefore": kpi_before,
            "kpiAfter": kpi_after,
            "window": {
                "beforeStart": _iso(item.handled_at - _KPI_WINDOW) if item.handled_at else None,
                "beforeEnd": _iso(item.handled_at),
                "afterStart": _iso(item.submitted_at),
                "afterEnd": _iso(item.submitted_at + _KPI_WINDOW) if item.submitted_at else None,
            },
        }
    )


__all__ = ["router"]
