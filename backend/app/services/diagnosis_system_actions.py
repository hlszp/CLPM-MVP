"""诊断 SYSTEM 处置建议生成（A3：落库即时生成，断链根治）。

原实现位于 ``api/v1/endpoints/diagnosis_v2._generate_system_actions``，
仅在 GET /diagnosis/runs/{id}/actions 首次拉取时懒生成，导致处置工作台
建议列表为空。本模块将生成逻辑提取为可复用位置：

- ``generate_system_actions(db, run)``：核心生成逻辑（内置幂等守卫：
  run 已有建议记录则跳过），返回新增条数
- ``generate_system_actions_best_effort(db, run)``：落库点 commit 后调用，
  try/except 包裹，生成失败仅记日志 + rollback，不阻塞诊断主链路

懒生成兜底路径（GET actions 为空时）保留在端点内，两条路径共用幂等守卫。

D5（08§4.2/§4.5 + 11§5 R11，2026-10-12）：
- 失败/取消/未评估 run 不发布任何 SYSTEM 建议（取消审计行只留痕不发布
  ——08§4.2 行 7/8"不发布该占位为正式结论或建议"）；
- 科学结论未决（INCONCLUSIVE）不推"确定整定建议"（TUNING 模板）——
  自动分类不把症状未决默认派往 PID 整定；投用（UTILIZATION）等独立
  维度事实建议不受影响；
- 建议与证据链绑定：basis 引用算子与支持度（规则支持度，不冒称概率），
  run_id 经 LoopActionItem.run_id 列绑定（建议-证据-run 三方可溯）；
- legacy run（无 assessment_meta 的历史记录）行为不变（reader-first，
  08§10.2：D6 读端未合入前不破坏旧读者）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.diagnosis_run import DiagnosisRun
from app.models.loop_action_item import LoopActionItem
from app.services.diagnosis_assessment import (
    JUDGEMENT_INCONCLUSIVE,
    JUDGEMENT_NOT_ASSESSED,
)
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

#: basis 列宽（loop_action_item.basis String(500)）
_BASIS_MAX_LEN = 500


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _category_evidence(run: DiagnosisRun, category: str) -> dict | None:
    """从 run.recommendations 取该分类的证据链（算子/支持度）。

    D5 分类层为每条候选建议写入 evidence（category/operators/support）；
    legacy run 的 recommendations 无 evidence 键 → 返回 None（旧文案保持）。
    """
    for rec in run.recommendations or []:
        ev = (rec or {}).get("evidence") or {}
        if isinstance(ev, dict) and ev.get("category") == category:
            return ev
    return None


def _evidence_basis(basis_prefix: str, label: str, evidence: dict | None) -> str:
    """basis 文案：诊断结论 + 算子/支持度引用（证据链可溯）。"""
    if not evidence:
        return f"{basis_prefix}：{label}"
    operators = evidence.get("operators") or []
    support = evidence.get("support")
    parts = [f"{basis_prefix}：{label}"]
    if operators:
        parts.append(f"证据算子 {'、'.join(str(o) for o in operators)}")
    elif evidence.get("source") == "kpi_context":
        auto_rate = evidence.get("autoRate")
        if auto_rate is not None:
            parts.append(f"KPI 统计 自动投用率 {float(auto_rate):.0%}")
    if isinstance(support, int | float):
        parts.append(f"支持度 {float(support):.2f}（规则支持度，未校准）")
    if evidence.get("independentEvidenceCount") is not None:
        parts.append(f"独立证据 {evidence['independentEvidenceCount']} 组")
    return "（".join([parts[0], "；".join(parts[1:])]) + "）" if len(parts) > 1 else parts[0]


async def generate_system_actions(db: AsyncSession, run: DiagnosisRun) -> int:
    """按诊断结论/人工复核结论自动生成标准处置建议（§9.4）。

    分类来源：已复核 → review_results（人工复核优先）；
    未复核 → primary_category + secondary_categories（诊断结论）。

    D5 守卫（08§4.2 + 11§5 R11）：
    - run.status=FAILED（含取消审计行/零检查占位）→ 不发布任何建议；
    - assessment_meta.judgementStatus=NOT_ASSESSED → 不发布任何建议；
    - judgementStatus=INCONCLUSIVE（未决）→ 不推确定整定建议（滤除
      TUNING 模板）；人工复核确认的分类不受此滤除（人工已决）。
    - legacy run（无 assessment_meta）不触发新守卫，行为与历史一致。

    幂等守卫：该 run 已存在 SYSTEM 建议时跳过（MANUAL 不拦——D3 修复
    2026-10-01：复核路径先删 SYSTEM 再重建，原"存在任何记录即跳过"导致
    有 MANUAL 项时复核后的新结论永远带不出系统建议）。返回新增条数。
    """
    # ---- D5 未决/失败/取消守卫（先于幂等检查：不发布即不查询） ----
    judgement = (run.assessment_meta or {}).get("judgementStatus")
    if run.status == "FAILED":
        return 0
    if judgement == JUDGEMENT_NOT_ASSESSED:
        return 0

    existing = (
        await db.execute(
            select(func.count())
            .select_from(LoopActionItem)
            .where(LoopActionItem.run_id == run.id, LoopActionItem.source == "SYSTEM")
        )
    ).scalar()
    if existing:
        return 0

    if run.review_status == "REVIEWED" and run.review_results:
        categories = [c for c in run.review_results if c in STANDARD_ACTION_TEMPLATES]
        basis_prefix = "人工复核"
        # 人工复核已决：INCONCLUSIVE 滤除不适用（人工结论优先）
    else:
        categories = (
            [run.primary_category] if run.primary_category in STANDARD_ACTION_TEMPLATES else []
        )
        for j in run.secondary_categories or []:
            cat = j.get("category")
            if cat in STANDARD_ACTION_TEMPLATES and cat not in categories:
                categories.append(cat)
        basis_prefix = "诊断结论"
        if judgement == JUDGEMENT_INCONCLUSIVE:
            # 未决不推确定整定建议（11§5 R11）：TUNING 模板（重新整定
            # PID 参数等确定性整定动作）滤除；症状/数据不足的核查指引
            # 已在 run.recommendations（诊断建议 Tab）呈现，不在此重复
            categories = [c for c in categories if c != "TUNING"]

    now = _utcnow_naive()
    count = 0
    for cat in categories:
        label = CATEGORY_LABELS.get(cat, cat)
        evidence = None if basis_prefix == "人工复核" else _category_evidence(run, cat)
        basis = _evidence_basis(basis_prefix, label, evidence)[:_BASIS_MAX_LEN]
        for tpl in STANDARD_ACTION_TEMPLATES[cat]:
            db.add(
                LoopActionItem(
                    run_id=run.id,
                    loop_id=run.loop_id,
                    source="SYSTEM",
                    category=cat,
                    content=f"{tpl['action']}：{tpl['description']}",
                    basis=basis,
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
