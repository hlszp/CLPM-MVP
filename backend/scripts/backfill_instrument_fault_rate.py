"""定向回填 instrument_fault_rate（仪表故障率）.

只重算 instrument_fault_rate 一个指标，不重算其他 11 个 KPI。
复用 DataPlanner 基础设施（TDengine 取数 + 预处理），但仅请求 PV 信号。

流程：
1. 查出 kpi_snapshot_hourly 中 SUCCESS 但 instrument_fault_rate IS NULL 的
   (loop_id, ts_start) 对
2. 逐小时窗口：DataPlanner.request_bundles(["instrument_fault_rate"])
   → InstrumentFaultRateCalculator → UPDATE
3. loop 级更新完成后，逐小时窗口执行 batch_calculate_and_save_node_snapshots
   更新 UnitKpiSummary

用法：
    cd backend && uv run python scripts/backfill_instrument_fault_rate.py
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)
logger = logging.getLogger("backfill_fault_rate")

# 时间范围（TDengine 数据可用范围）
BACKFILL_START = datetime(2026, 7, 12, 16, 0, 0)
BACKFILL_END = datetime(2026, 7, 23, 19, 0, 0)

# 并发控制
CONCURRENCY = 10


async def _get_pending_snapshots(db) -> list[dict]:
    """查询所有需要回填 instrument_fault_rate 的快照."""
    from sqlalchemy import text

    result = await db.execute(
        text("""
            SELECT loop_id, ts_start, ts_end
            FROM kpi_snapshot_hourly
            WHERE status = 'SUCCESS'
              AND instrument_fault_rate IS NULL
              AND ts_start >= :start
              AND ts_start < :end
            ORDER BY ts_start, loop_id
        """),
        {"start": BACKFILL_START, "end": BACKFILL_END},
    )
    rows = result.fetchall()
    return [{"loop_id": str(r[0]), "ts_start": r[1], "ts_end": r[2]} for r in rows]


def _get_unique_windows(snapshots: list[dict]) -> list[datetime]:
    """提取去重的小时窗口列表."""
    seen = set()
    windows = []
    for s in snapshots:
        ts = s["ts_start"]
        if ts not in seen:
            seen.add(ts)
            windows.append(ts)
    windows.sort()
    return windows


async def _backfill_window(
    window_start: datetime,
    snapshots: list[dict],
    loop_configs: dict[str, dict],
) -> dict:
    """回填单个小时窗口内所有回路的 instrument_fault_rate."""
    from sqlalchemy import select, update

    from app.contracts.data_types import TimeWindow
    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger
    from app.models.metric import KpiSnapshotHourly
    from app.services.data_planner import DataPlanner
    from app.services.data_source.factory import get_provider
    from app.services.metric_calculator.instrument_fault import InstrumentFaultRateCalculator
    from app.services.metric_data_bundle import MetricDataBundleAssembler
    from app.tasks.kpi_calc import _loop_type_to_control_type, _make_config_loader

    window_end = window_start + timedelta(hours=1)
    window_snapshots = [s for s in snapshots if s["ts_start"] == window_start]

    if not window_snapshots:
        return {"total": 0, "success": 0, "skipped": 0, "failed": 0}

    total = len(window_snapshots)
    success = 0
    skipped = 0
    failed = 0

    sem = asyncio.Semaphore(CONCURRENCY)
    calculator = InstrumentFaultRateCalculator()

    async def _process_one(snap: dict) -> None:
        nonlocal success, skipped, failed
        async with sem:
            loop_id = snap["loop_id"]
            async with AsyncSessionLocal() as worker_db:
                try:
                    # 查询回路类型
                    loop_result = await worker_db.execute(
                        select(LoopLedger.loop_type).where(LoopLedger.id == loop_id)
                    )
                    loop_row = loop_result.first()
                    if not loop_row:
                        skipped += 1
                        return

                    loop_type = loop_row[0]
                    control_type = _loop_type_to_control_type(loop_type)

                    # 构建 DataPlanner（禁用缓存）
                    query_fn = get_provider().make_query_fn(worker_db)
                    assembler = MetricDataBundleAssembler()
                    data_planner = DataPlanner(
                        cache=None,
                        tdengine_query_fn=query_fn,
                        assembler=assembler,
                        db=worker_db,
                        bundle_cache=None,
                    )

                    # 设置预加载的 OP 限位和配置加载器
                    cfg = loop_configs.get(loop_id)
                    if cfg:
                        data_planner._preloaded_op_limits = {
                            loop_id: (cfg.get("op_lower"), cfg.get("op_upper"))
                        }
                        data_planner._config_loader = _make_config_loader(cfg)

                    # 请求 bundles（仅 instrument_fault_rate，只需 PV 信号）
                    time_window = TimeWindow(start=window_start, end=window_end)
                    try:
                        bundles = await data_planner.request_bundles(
                            loop_id=loop_id,
                            metrics=["instrument_fault_rate"],
                            time_window=time_window,
                            control_type=control_type,
                        )
                    except Exception as exc:
                        logger.warning(
                            "窗口 %s 回路 %s 取数失败: %s",
                            window_start.isoformat(),
                            loop_id,
                            exc,
                        )
                        failed += 1
                        return

                    if not bundles:
                        skipped += 1
                        return

                    # 计算 instrument_fault_rate
                    bundle = bundles[0]
                    result = calculator.calculate(bundle)

                    fault_rate = None
                    if result.value is not None:
                        fault_rate = Decimal(str(result.value)).quantize(Decimal("0.01"))

                    # 更新 kpi_snapshot_hourly.instrument_fault_rate
                    await worker_db.execute(
                        update(KpiSnapshotHourly)
                        .where(
                            KpiSnapshotHourly.loop_id == loop_id,
                            KpiSnapshotHourly.ts_start == window_start,
                        )
                        .values(instrument_fault_rate=fault_rate)
                    )
                    await worker_db.commit()
                    success += 1

                except Exception as exc:
                    await worker_db.rollback()
                    logger.warning(
                        "窗口 %s 回路 %s 计算失败: %s",
                        window_start.isoformat(),
                        loop_id,
                        exc,
                        exc_info=True,
                    )
                    failed += 1

    await asyncio.gather(*[_process_one(s) for s in window_snapshots])

    return {"total": total, "success": success, "skipped": skipped, "failed": failed}


async def _backfill_node_aggregation(windows: list[datetime]) -> int:
    """逐小时窗口执行节点级聚合（更新 UnitKpiSummary）."""
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.models.plant_node import PlantNode
    from app.services.node_performance import batch_calculate_and_save_node_snapshots

    async with AsyncSessionLocal() as db:
        node_result = await db.execute(select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True)))
        nodes = list(node_result.scalars().all())

    if not nodes:
        logger.warning("无 KPI 启用节点，跳过节点聚合")
        return 0

    total_success = 0
    for window_start in windows:
        window_end = window_start + timedelta(hours=1)
        result = await batch_calculate_and_save_node_snapshots(
            nodes=nodes,
            ts_start=window_start,
            ts_end=window_end,
            concurrency=10,
        )
        total_success += result.get("success", 0)
        logger.info(
            "节点聚合 %s: success=%d, skipped=%d, failed=%d",
            window_start.strftime("%Y-%m-%d %H:00"),
            result.get("success", 0),
            result.get("skipped", 0),
            result.get("failed", 0),
        )

    return total_success


async def main():
    from app.core.db import AsyncSessionLocal
    from app.tasks.kpi_calc import _batch_load_loop_configs

    logger.info("=== 仪表故障率定向回填 ===")
    logger.info("时间范围: %s ~ %s", BACKFILL_START, BACKFILL_END)

    # 1. 查询待回填的快照
    async with AsyncSessionLocal() as db:
        snapshots = await _get_pending_snapshots(db)

    if not snapshots:
        logger.info("无需回填的快照，退出")
        return

    windows = _get_unique_windows(snapshots)
    logger.info(
        "待回填: %d 个快照, %d 个小时窗口 (%s ~ %s)",
        len(snapshots),
        len(windows),
        windows[0].isoformat(),
        windows[-1].isoformat(),
    )

    # 2. 批量预加载回路配置
    all_loop_ids = list({s["loop_id"] for s in snapshots})
    async with AsyncSessionLocal() as db:
        loop_configs = await _batch_load_loop_configs(db, all_loop_ids)
    logger.info("预加载 %d 个回路配置", len(loop_configs))

    # 3. 逐小时窗口回填 loop 级 instrument_fault_rate
    total_success = 0
    total_skipped = 0
    total_failed = 0

    for i, window_start in enumerate(windows, 1):
        result = await _backfill_window(window_start, snapshots, loop_configs)
        total_success += result["success"]
        total_skipped += result["skipped"]
        total_failed += result["failed"]

        logger.info(
            "[%d/%d] 窗口 %s: success=%d, skipped=%d, failed=%d (累计 success=%d)",
            i,
            len(windows),
            window_start.strftime("%Y-%m-%d %H:00"),
            result["success"],
            result["skipped"],
            result["failed"],
            total_success,
        )

    logger.info(
        "=== Loop 级回填完成: success=%d, skipped=%d, failed=%d ===",
        total_success,
        total_skipped,
        total_failed,
    )

    # 4. 逐小时窗口执行节点级聚合
    logger.info("=== 开始节点级聚合 ===")
    node_success = await _backfill_node_aggregation(windows)
    logger.info("=== 节点级聚合完成: node_success=%d ===", node_success)

    logger.info("=== 回填全部完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
