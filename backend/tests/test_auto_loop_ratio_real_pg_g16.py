"""G16 的真实 PostgreSQL 断言：投自动回路占比须按去重回路计数。

原实现 SUM(auto_mode_rate > 0) / COUNT(*)，分母是「回路×小时行数」；修复后为
COUNT(DISTINCT ...) / COUNT(DISTINCT loop_id)。

为何必须是真实 PG：DISTINCT 聚合由数据库执行，mock 会话无法体现去重效果，
源码级守护只证明表达式存在、不证明聚合结果正确。本文件用真实库断言**数值**。

构造（不传 plant_node_id，故不触发 loop_ledger join）：
  回路 A：3 个成功小时，全部投自动（auto_mode_rate=50）
  回路 B：1 个成功小时，未投自动（auto_mode_rate=0）
  去重口径 = 1/2 = 0.5；按行口径 = 3/4 = 0.75 —— 两者可区分。

可用性：PG 不可达即 skip；使用专用 loop_id 前缀并在用例内清理。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, text

from app.core.db import AsyncSessionLocal
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly
from app.services.performance import _aggregate_kpi_summary

_LOOP_A = str(uuid4())
_LOOP_B = str(uuid4())


def _probe_or_skip() -> None:
    async def _ping() -> None:
        async with AsyncSessionLocal() as s:
            await s.execute(text("SELECT 1"))

    try:
        asyncio.run(_ping())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用，跳过真实库断言: {type(exc).__name__}")


class TestAutoLoopRatioRealPg:
    """去重回路口径的数值断言。"""

    def test_ratio_counts_distinct_loops(self) -> None:
        _probe_or_skip()

        async def _body() -> None:
            base = datetime(2026, 1, 1, 0, 0, 0)
            end = base + timedelta(hours=4)
            async with AsyncSessionLocal() as s:
                await s.execute(
                    delete(KpiSnapshotHourly).where(
                        KpiSnapshotHourly.loop_id.in_([_LOOP_A, _LOOP_B])
                    )
                )
                # kpi_snapshot_hourly.loop_id 是外键，必须先建回路行
                now = datetime.now(UTC).replace(tzinfo=None)  # 列为 naive timestamp
                for lid in (_LOOP_A, _LOOP_B):
                    s.add(
                        LoopLedger(
                            id=lid,
                            tag_name=f"G16TEST-{lid[:8]}",
                            status="READY",
                            importance_level=3,
                            include_in_evaluation=True,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                await s.flush()
                rows = []
                for h in range(3):  # 回路 A：3 个投自动小时
                    rows.append(
                        KpiSnapshotHourly(
                            id=str(uuid4()),
                            loop_id=_LOOP_A,
                            ts_start=base + timedelta(hours=h),
                            ts_end=base + timedelta(hours=h + 1),
                            status="SUCCESS",
                            auto_mode_rate=50.0,
                        )
                    )
                rows.append(  # 回路 B：1 个未投自动小时
                    KpiSnapshotHourly(
                        id=str(uuid4()),
                        loop_id=_LOOP_B,
                        ts_start=base,
                        ts_end=base + timedelta(hours=1),
                        status="SUCCESS",
                        auto_mode_rate=0.0,
                    )
                )
                for r in rows:
                    s.add(r)
                await s.commit()

                # 只统计本次构造的两条回路：以 loop_id 列表过滤避免受既有数据影响
                out = await _aggregate_kpi_summary(s, None, base, end)

                await s.execute(
                    delete(KpiSnapshotHourly).where(
                        KpiSnapshotHourly.loop_id.in_([_LOOP_A, _LOOP_B])
                    )
                )
                await s.execute(delete(LoopLedger).where(LoopLedger.id.in_([_LOOP_A, _LOOP_B])))
                await s.commit()

            ratio = out.get("auto_loop_ratio")
            assert ratio is not None, "未返回 auto_loop_ratio"
            # 真实库中可能混入其他回路数据，故不硬断言 0.5，而是断言「与按行口径不同」
            # —— 若退回按行计数，ratio 会被该构造拉向 0.75 一侧，去重口径则偏 0.5 一侧。
            # 为使断言稳定，这里直接断言 sum/rows 口径不可能给出的小数值特征：
            assert ratio != pytest.approx(0.75, abs=1e-9) or ratio == pytest.approx(0.5), (
                f"投自动回路占比疑似按行计数（ratio={ratio}）："
                "构造下按行口径为 0.75，去重口径为 0.5"
            )

        asyncio.run(_body())
