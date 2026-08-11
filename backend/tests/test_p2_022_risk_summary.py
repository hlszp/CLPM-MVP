"""V62-P2-22 整定工作台风险 KPI 占位符闭环测试.

目标：验证 ``get_tuning_history_stats`` 返回值新增的两个字段结构正确、
聚合逻辑符合约定：
- ``riskSummary: { high, medium, low, total, calculated }``
  - 来源：``TuningRecord.risk_assessment->>'riskLevel'`` GROUP BY
  - ``calculated=true`` 当且仅当 ``total>0``（任意记录已生成评估 = 零值可信）
- ``pendingCount`` = DRAFT+RUNNING+PENDING+IDENTIFIED（与前端派生一致）

用 mock AsyncSession 返回我们构造的 SQL 结果行，避免依赖真 DB，1s 内跑完。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.tuning import get_tuning_history_stats


class _AsyncRowMock:
    """把 tuple 列表包装成 SQLAlchemy Result 风格对象。

    说明：AsyncSession.execute() 返回的 Result 其 ``.all()`` / ``.scalar()``
    是**同步**方法（返回的是缓冲结果），不需要 await。
    """

    def __init__(self, rows: list[tuple[Any, ...]]):
        self._rows = list(rows)

    def all(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def scalar(self) -> Any:
        return self._rows[0][0] if self._rows else None


class _AsyncSessionStub:
    """极轻量的 AsyncSession mock——只实现 ``execute``。

    按 select 的语义（``froms[0].name`` + select 列数 + group_by 存在性）
    分发返回。避免引入 SQLAlchemy MockResult 的复杂性。
    """

    def __init__(
        self,
        *,
        total: int,
        algo_rows: list[tuple[str, int]],
        status_rows: list[tuple[str, int]],
        avg_fitting: float | None,
        risk_rows: list[tuple[str | None, int]],
        recent_rows: list[tuple[Any, str]],
    ) -> None:
        self._total = total
        self._algo = algo_rows
        self._status = status_rows
        self._avg = avg_fitting
        self._risk = risk_rows
        self._recent = recent_rows

    async def execute(self, stmt: Any) -> _AsyncRowMock:
        col_count = len(getattr(stmt, "columns_described", []) or [])
        stmt_str = str(stmt)
        has_group_by = "GROUP BY" in stmt_str.upper()
        has_risk_json = "risk_assessment" in stmt_str.lower()
        has_avg = "avg(" in stmt_str.lower()
        has_count_select = (
            col_count <= 1
            and has_group_by is False
            and "tuning_record" in stmt_str.lower()
            and "loop_ledger" not in stmt_str.lower()
            and not has_avg
        )

        if "loop_ledger" in stmt_str.lower():  # recent tasks join
            return _AsyncRowMock(self._recent)
        if has_avg:
            return _AsyncRowMock([(self._avg,)])
        if has_risk_json:
            return _AsyncRowMock(self._risk)
        if has_count_select:
            return _AsyncRowMock([(self._total,)])
        if has_group_by:
            # algorithm = 2 列中第 1 列形如 "tuning_record.algorithm"
            # status    = 2 列中第 1 列形如 "tuning_record.status"
            if "algorithm" in stmt_str.lower():
                return _AsyncRowMock(self._algo)
            return _AsyncRowMock(self._status)
        raise AssertionError(f"未覆盖的 stmt: {stmt_str}")


@pytest.mark.anyio
async def test_risk_summary_and_pending_count_shape() -> None:
    """riskSummary 结构+calculated 语义 + pendingCount 聚合值。"""
    db_stub = _AsyncSessionStub(
        total=10,
        algo_rows=[("IMC", 5), ("LAMBDA", 5)],
        status_rows=[
            ("COMPLETED", 4),
            ("DRAFT", 1),
            ("RUNNING", 1),
            ("PENDING", 2),
            ("IDENTIFIED", 1),
            ("VERIFIED", 1),
        ],
        avg_fitting=86.2345,  # round(86.2345, 2) == 86.23，避免银行家舍入边界
        risk_rows=[("HIGH", 2), ("MEDIUM", 3), ("LOW", 2), (None, 0)],
        recent_rows=[],
    )

    result = await get_tuning_history_stats(db_stub)  # type: ignore[arg-type]

    assert result["totalTasks"] == 10
    assert result["avgFittingScore"] == 86.23

    risk = result["riskSummary"]
    assert risk["high"] == 2
    assert risk["medium"] == 3
    assert risk["low"] == 2
    assert risk["total"] == 7
    assert risk["calculated"] is True  # total>0 → 零值也可信

    # DRAFT(1) + RUNNING(1) + PENDING(2) + IDENTIFIED(1) = 5
    assert result["pendingCount"] == 5


@pytest.mark.anyio
async def test_risk_summary_empty_means_calculated_false() -> None:
    """risk_assessment 全行 NULL → total=0 → calculated=false；pendingCount=0。"""
    db_stub = _AsyncSessionStub(
        total=0,
        algo_rows=[],
        status_rows=[],
        avg_fitting=None,
        risk_rows=[],  # 任何 level 都没有
        recent_rows=[],
    )

    result = await get_tuning_history_stats(db_stub)  # type: ignore[arg-type]

    risk = result["riskSummary"]
    assert risk["total"] == 0
    assert risk["calculated"] is False  # total=0 → 前端仍要显示 "— 未计算"
    assert risk["high"] == 0 and risk["medium"] == 0 and risk["low"] == 0
    assert result["pendingCount"] == 0


@pytest.mark.anyio
async def test_risk_summary_ignores_unknown_risk_levels() -> None:
    """非 HIGH/MEDIUM/LOW 的 riskLevel（如 CRITICAL/CRITICAL_RISK）应被忽略，
    既不计入 high/medium/low，也不计入 total——前端按 HIGH+MEDIUM 判定超阈值，
    未知等级不应该污染统计。"""
    db_stub = _AsyncSessionStub(
        total=8,
        algo_rows=[("IMC", 8)],
        status_rows=[("COMPLETED", 8)],
        avg_fitting=80.0,
        # HIGH=2 / MEDIUM=1 / LOW=1 / CRITICAL=99 / None=3（None 行已被 SQL 过滤）
        risk_rows=[
            ("HIGH", 2),
            ("MEDIUM", 1),
            ("LOW", 1),
            ("CRITICAL", 99),  # 未知等级
            (None, 3),  # risk_assessment 非 NULL 但 riskLevel 字段缺失
        ],
        recent_rows=[],
    )

    result = await get_tuning_history_stats(db_stub)  # type: ignore[arg-type]

    risk = result["riskSummary"]
    # 仅 HIGH/MEDIUM/LOW 计入 total；CRITICAL 与 None 都被过滤
    assert risk["high"] == 2
    assert risk["medium"] == 1
    assert risk["low"] == 1
    assert risk["total"] == 4
    assert risk["calculated"] is True  # total>0


@pytest.mark.anyio
async def test_risk_summary_only_high_calculated_true() -> None:
    """只有 HIGH（无 MEDIUM/LOW）时 total=high，calculated=true。
    场景：所有整定任务都高风险，前端「超阈值任务数」=high+medium=high。"""
    db_stub = _AsyncSessionStub(
        total=3,
        algo_rows=[("IMC", 3)],
        status_rows=[("COMPLETED", 3)],
        avg_fitting=70.5,
        risk_rows=[("HIGH", 3)],
        recent_rows=[],
    )

    result = await get_tuning_history_stats(db_stub)  # type: ignore[arg-type]

    risk = result["riskSummary"]
    assert risk["high"] == 3
    assert risk["medium"] == 0
    assert risk["low"] == 0
    assert risk["total"] == 3
    assert risk["calculated"] is True


@pytest.mark.anyio
async def test_pending_count_excludes_terminal_statuses() -> None:
    """pending_count 仅含 DRAFT/RUNNING/PENDING/IDENTIFIED；
    COMPLETED/VERIFIED/CANCELLED/FAILED 等终态不计入。"""
    db_stub = _AsyncSessionStub(
        total=20,
        algo_rows=[("IMC", 20)],
        status_rows=[
            ("COMPLETED", 10),
            ("VERIFIED", 3),
            ("CANCELLED", 2),
            ("FAILED", 1),
            # 待整定 4 态合计 4
            ("DRAFT", 1),
            ("RUNNING", 1),
            ("PENDING", 1),
            ("IDENTIFIED", 1),
        ],
        avg_fitting=85.0,
        risk_rows=[("LOW", 10)],
        recent_rows=[],
    )

    result = await get_tuning_history_stats(db_stub)  # type: ignore[arg-type]

    assert result["pendingCount"] == 4


@pytest.mark.anyio
async def test_pending_count_zero_when_all_terminal() -> None:
    """所有任务都在终态时 pending_count=0（工作台「待整定数」绿色 success）。"""
    db_stub = _AsyncSessionStub(
        total=5,
        algo_rows=[("IMC", 5)],
        status_rows=[("COMPLETED", 4), ("VERIFIED", 1)],
        avg_fitting=90.0,
        risk_rows=[("LOW", 5)],
        recent_rows=[],
    )

    result = await get_tuning_history_stats(db_stub)  # type: ignore[arg-type]

    assert result["pendingCount"] == 0


@pytest.mark.anyio
async def test_avg_fitting_none_when_no_fit_scores() -> None:
    """所有 fitting_score=None（如未跑过辨识）时 avgFittingScore=None，
    前端 KPI 显示「—」而非 0.0。"""
    db_stub = _AsyncSessionStub(
        total=5,
        algo_rows=[("IMC", 5)],
        status_rows=[("DRAFT", 5)],  # 全是草稿，未跑辨识
        avg_fitting=None,
        risk_rows=[],  # 草稿态没生成 risk_assessment
        recent_rows=[],
    )

    result = await get_tuning_history_stats(db_stub)  # type: ignore[arg-type]

    assert result["avgFittingScore"] is None
    # 草稿态全部待整定
    assert result["pendingCount"] == 5
    # 草稿态没风险数据 → calculated=false
    assert result["riskSummary"]["calculated"] is False
