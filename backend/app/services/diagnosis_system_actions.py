"""诊断 SYSTEM 处置建议生成（A3：落库即时生成，断链根治）。

原实现位于 ``api/v1/endpoints/diagnosis_v2._generate_system_actions``，
仅在 GET /diagnosis/runs/{id}/actions 首次拉取时懒生成，导致处置工作台
建议列表为空。本模块将生成逻辑提取为可复用位置：

- ``generate_system_actions(db, run)``：核心生成逻辑（内置幂等守卫：
  run 已有建议记录则跳过），返回新增条数
- ``generate_system_actions_best_effort(db, run)``：落库点 commit 后调用，
  try/except 包裹，生成失败仅记日志 + rollback，不阻塞诊断主链路

懒生成兜底路径（GET actions 为空时）保留在端点内，两条路径共用幂等守卫。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.diagnosis_run import DiagnosisRun
from app.models.loop_action_item import LoopActionItem
from app.services.loop_action_templates import STANDARD_ACTION_TEMPLATES

logger = logging.getLogger(__name__)

#: 诊断原因分类标签（供 basis 文案使用；端点层 diagnosis_v2._CATEGORY_LABELS 同源）
CATEGORY_LABELS = {
    "TUNING": "参数问题（PID 整定）",
    "VALVE": "阀门/执行机构问题",
    "INSTRUMENT": "仪表/测量问题",
    "COMMUNICATION": "通信链路问题",
    "PROCESS": "工艺/外扰问题",
    "UTILIZATION": "投用/操作问题",
    "DESIGN": "组态/设计问题",
    "DATA_INSUFFICIENT": "数据不足/无法判定",
}


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def generate_system_actions(db: AsyncSession, run: DiagnosisRun) -> int:
    """按诊断结论/人工复核结论自动生成标准处置建议（§9.4）。

    分类来源：已复核 → review_results（人工复核优先）；
    未复核 → primary_category + secondary_categories（诊断结论）。

    幂等守卫：该 run 已存在任何建议记录（SYSTEM 或 MANUAL）时跳过，
    与懒生成路径"列表非空则不生成"的口径一致。返回新增条数。
    """
    existing = (
        await db.execute(
            select(func.count()).select_from(LoopActionItem).where(LoopActionItem.run_id == run.id)
        )
    ).scalar()
    if existing:
        return 0

    if run.review_status == "REVIEWED" and run.review_results:
        categories = [c for c in run.review_results if c in STANDARD_ACTION_TEMPLATES]
        basis_prefix = "人工复核"
    else:
        categories = (
            [run.primary_category] if run.primary_category in STANDARD_ACTION_TEMPLATES else []
        )
        for j in run.secondary_categories or []:
            cat = j.get("category")
            if cat in STANDARD_ACTION_TEMPLATES and cat not in categories:
                categories.append(cat)
        basis_prefix = "诊断结论"

    now = _utcnow_naive()
    count = 0
    for cat in categories:
        label = CATEGORY_LABELS.get(cat, cat)
        for tpl in STANDARD_ACTION_TEMPLATES[cat]:
            db.add(
                LoopActionItem(
                    run_id=run.id,
                    loop_id=run.loop_id,
                    source="SYSTEM",
                    category=cat,
                    content=f"{tpl['action']}：{tpl['description']}",
                    basis=f"{basis_prefix}：{label}",
                    priority=tpl["priority"],
                    status="PENDING",
                    suggested_by="系统",
                    suggested_at=now,
                )
            )
            count += 1
    await db.flush()
    return count


async def generate_system_actions_best_effort(db: AsyncSession, run: DiagnosisRun) -> int:
    """落库点 commit 后即时生成 SYSTEM 建议（A3）。

    try/except 包裹：生成失败仅记日志并回滚当前事务，不阻塞诊断主链路。
    调用方须已完成 run 行自身的 commit（本函数自行 commit 新增建议）。
    """
    try:
        count = await generate_system_actions(db, run)
        if count:
            await db.commit()
            logger.info("诊断落库即时生成 SYSTEM 建议: run_id=%s count=%d", run.id, count)
        return count
    except Exception as exc:  # noqa: BLE001
        logger.exception("即时生成 SYSTEM 建议失败（不阻塞诊断主链路）: run_id=%s %s", run.id, exc)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
