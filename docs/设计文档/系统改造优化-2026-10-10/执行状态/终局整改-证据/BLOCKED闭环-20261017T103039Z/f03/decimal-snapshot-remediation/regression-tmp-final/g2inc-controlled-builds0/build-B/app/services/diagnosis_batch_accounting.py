"""诊断批任务终结统计（P3-02，08§4.2 对象计数口径）。

职责（纯函数为主，供 Celery 批任务收尾与读端/D6 复用）：
- 按 08§4.2「取消→运行失败→不可判定→可判定」**互斥优先级**对批任务内
  每个回路对象归类，四类终结 + 排队/运行另报 + 缺失，六类守恒
  （INT-03 诊断侧 E 六类：可判定+不可判定+运行失败+取消+排队运行+缺失
   = 对象总数）；
- PARTIAL run 数与 selected 算子覆盖（八态计数）**独立另报**，不并入
  四类终结、不用一个成功数混代；
- 取消对象不再计失败一次（取消优先）；选中算子分母不因未执行缩小
  （覆盖计数来自 D2 落库时任务开始固定的 assessment_meta.coverage，
   本模块只聚合不改写）。

与科学结论分层：本模块只做「对象终结」账，judgementStatus 等科学结论
以 diagnosis_run.assessment_meta 为准（08§4.2 运行状态与科学结论分开）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.diagnosis_run import DiagnosisRun
from app.services.diagnosis_assessment import (
    JUDGEMENT_SYMPTOM_DETECTED,
    JUDGEMENT_VALID_NEGATIVE,
)

# ---- 对象终结类别（08§4.2 互斥优先级：cancelled > failed > 不可判定 > 可判定）----
CLASS_CANCELLED = "cancelled"
CLASS_FAILED = "failed"
CLASS_UNDETERMINABLE = "undeterminable"
CLASS_DETERMINABLE = "determinable"
# ---- 无 run 行对象类别（另报；不与四类终结混计）----
CLASS_PENDING = "pending"  # 排队/运行中（任务未终结）
CLASS_MISSING = "missing"  # 任务已 SUCCESS 终结但对象无 run 行（守恒缺口）

_TERMINAL_FOUR = (CLASS_CANCELLED, CLASS_FAILED, CLASS_UNDETERMINABLE, CLASS_DETERMINABLE)

#: assessment_meta.coverage 中参与聚合的算子八态键（与 D2 三等式右端一致）
_COVERAGE_KEYS = (
    "positive",
    "negative",
    "notApplicable",
    "insufficient",
    "error",
    "cancelled",
    "notRun",
)

#: 任务终态 → 无 run 行对象的兜底归类（对象随任务终结，不计排队/运行）
_TASK_TERMINAL_FALLBACK = {
    "CANCELLED": CLASS_CANCELLED,
    "FAILED": CLASS_FAILED,
    "SUCCESS": CLASS_MISSING,
}


def _meta(run: DiagnosisRun) -> dict[str, Any]:
    meta = run.assessment_meta
    return meta if isinstance(meta, dict) else {}


def classify_run(run: DiagnosisRun) -> str:
    """单 run 对象归类（08§4.2 互斥优先级）。

    - 取消：assessment_meta.cancelled=True（status=FAILED+reasonCode=CANCELLED
      的审计行——取消优先，不再计运行失败一次）；
    - 运行失败：status=FAILED（含 selected 为空 NOT_ASSESSED 的 08§4.2 行 7）；
    - 可判定：judgementStatus ∈ {SYMPTOM_DETECTED, VALID_NEGATIVE}；
    - 不可判定：INCONCLUSIVE，或无 assessment_meta 的历史 run
      （LEGACY_UNVERIFIABLE，不伪称可判定）。
    """
    if _meta(run).get("cancelled") is True:
        return CLASS_CANCELLED
    if run.status == "FAILED":
        return CLASS_FAILED
    judgement = _meta(run).get("judgementStatus")
    if judgement in (JUDGEMENT_SYMPTOM_DETECTED, JUDGEMENT_VALID_NEGATIVE):
        return CLASS_DETERMINABLE
    return CLASS_UNDETERMINABLE


def summarize_batch(
    *,
    task_status: str,
    loop_ids: list[str],
    runs: list[DiagnosisRun],
) -> dict[str, Any]:
    """批任务对象终结统计（纯函数）。

    Args:
        task_status: 任务记录终态（PENDING/RUNNING/SUCCESS/FAILED/CANCELLED）
        loop_ids: 任务对象清单（批任务 loop_ids；空清单时仅按 run 行统计）
        runs: 该 task_id 名下全部 run 行

    Returns:
        六类守恒 + 独立另报（partialRuns/legacyUnverifiable/operatorCoverage）
        的统计字典；invariantObjects=True 表示六类之和=对象总数。
    """
    by_loop: dict[str, DiagnosisRun] = {}
    for run in runs:
        # (task_id, loop_id) 唯一约束下不会有同 loop 多行；防御取最新
        prev = by_loop.get(run.loop_id)
        if prev is None or (run.created_at or "") >= (prev.created_at or ""):
            by_loop[run.loop_id] = run

    counts = dict.fromkeys((*_TERMINAL_FOUR, CLASS_PENDING, CLASS_MISSING), 0)
    legacy = 0
    partial_runs = 0
    coverage_totals: dict[str, int] = dict.fromkeys(_COVERAGE_KEYS, 0)
    coverage_totals["selected"] = 0

    for run in runs:
        if run.status == "PARTIAL":
            partial_runs += 1
        if not _meta(run):
            legacy += 1
        cov = _meta(run).get("coverage") or {}
        coverage_totals["selected"] += int(cov.get("selected") or 0)
        for key in _COVERAGE_KEYS:
            coverage_totals[key] += int(cov.get(key) or 0)
        counts[classify_run(run)] += 1

    # 无 run 行对象：按任务状态兜底归类（排队/运行仅任务未终结时另报）
    fallback = _TASK_TERMINAL_FALLBACK.get(task_status, CLASS_PENDING)
    objects = set(loop_ids) or set()
    counts[fallback] += len(objects - set(by_loop))

    total_objects = max(len(objects), len(by_loop))
    return {
        "taskStatus": task_status,
        "objects": total_objects,
        # 四类互斥终结（08§4.2 优先级：取消→运行失败→不可判定→可判定）
        "terminal": {k: counts[k] for k in _TERMINAL_FOUR},
        # 排队/运行另报 + 缺失（不与终结混计；六类守恒）
        "pending": counts[CLASS_PENDING],
        "missing": counts[CLASS_MISSING],
        "invariantObjects": sum(counts.values()) == total_objects,
        # 独立另报：PARTIAL 不并入四类；算子覆盖分母为任务开始固定值
        "partialRuns": partial_runs,
        "legacyUnverifiableRuns": legacy,
        "operatorCoverage": coverage_totals,
    }


async def load_task_runs(db: AsyncSession, task_id: str) -> list[DiagnosisRun]:
    """读取批任务名下全部 run 行（按任务键查询，含审计行）。"""
    result = await db.execute(select(DiagnosisRun).where(DiagnosisRun.task_id == task_id))
    return list(result.scalars().all())


async def summarize_task(
    db: AsyncSession,
    *,
    task_record: dict[str, str],
) -> dict[str, Any]:
    """从任务记录（Redis hash 字典）+ run 行计算终结统计。

    task_record 需含 status/loop_ids 字段（task_tracker.get_task 返回形态）。
    """
    loop_ids: list[str] = []
    raw_loops = task_record.get("loop_ids") or ""
    if raw_loops:
        try:
            parsed = json.loads(raw_loops)
            loop_ids = [str(x) for x in parsed] if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            loop_ids = []
    runs = await load_task_runs(db, task_record.get("task_id", ""))
    return summarize_batch(
        task_status=task_record.get("status", ""),
        loop_ids=loop_ids,
        runs=runs,
    )


__all__ = [
    "CLASS_CANCELLED",
    "CLASS_DETERMINABLE",
    "CLASS_FAILED",
    "CLASS_MISSING",
    "CLASS_PENDING",
    "CLASS_UNDETERMINABLE",
    "classify_run",
    "load_task_runs",
    "summarize_batch",
    "summarize_task",
]
