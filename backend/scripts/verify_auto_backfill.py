#!/usr/bin/env python3
"""验证 --auto 模式的缺失快照检测逻辑。

构造三组模拟数据：
  Hour A（2小时前）: 27 条快照（完整）→ 不应被标记
  Hour B（3小时前）: 15 条快照（不完整）→ 应标记为"不完整"
  Hour C（4小时前）: 0 条快照（缺失）→ 应标记为"缺失"

然后调用 detect_missing_snapshots 验证检测结果，最后清理测试数据。
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, func, select

from app.core.db import AsyncSessionLocal
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly

# 降低 SQL 日志噪声
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

# 测试时间窗口：2/3/4 小时前（UTC 整点）
NOW = datetime.now(UTC)
HOUR_A = (NOW - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
HOUR_B = (NOW - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
HOUR_C = (NOW - timedelta(hours=4)).replace(minute=0, second=0, microsecond=0)
# 标记位：用于清理时识别测试数据
TEST_MARKER = "__verify_auto_backfill__"


async def clear_test_data(db):
    """清除本脚本产生的测试数据（按 algorithm_version 标记识别）"""
    result = await db.execute(
        delete(KpiSnapshotHourly).where(KpiSnapshotHourly.algorithm_version == TEST_MARKER)
    )
    await db.commit()
    if result.rowcount > 0:
        print(f"[清理] 删除 {result.rowcount} 条测试快照")


async def get_active_loops(db) -> list[LoopLedger]:
    """查询所有活跃回路"""
    result = await db.execute(
        select(LoopLedger)
        .where(
            LoopLedger.is_active.is_(True),
            LoopLedger.status == "READY",
        )
        .order_by(LoopLedger.tag_name)
    )
    return list(result.scalars().all())


async def insert_mock_snapshots(db, loops: list[LoopLedger]):
    """插入三组模拟快照"""
    ts_a = HOUR_A.replace(tzinfo=None)
    ts_b = HOUR_B.replace(tzinfo=None)
    HOUR_C.replace(tzinfo=None)

    # ── Hour A: 27 条完整快照 ──
    for lp in loops:
        snap = KpiSnapshotHourly(
            id=str(uuid4()),
            loop_id=str(lp.id),
            ts_start=ts_a,
            ts_end=ts_a + timedelta(hours=1),
            status="SUCCESS",
            score=Decimal("85.00"),
            good_value_rate=Decimal("95.00"),
            auto_mode_rate=Decimal("100.00"),
            effective_auto_rate=Decimal("100.00"),
            steady_rate=Decimal("80.00"),
            accuracy_rate=Decimal("85.00"),
            fast_rate=Decimal("75.00"),
            oscillation_rate=Decimal("5.00"),
            saturation_rate=Decimal("3.00"),
            algorithm_version=TEST_MARKER,
            sampling_freq="1s",
            quality_policy="KEEP_ALL_WITH_VALIDITY",
            valid_rate=Decimal("0.9500"),
            confidence_level="A",
            data_lineage={"algorithm_version": TEST_MARKER},
        )
        db.add(snap)
    print(f"[构造] Hour A ({HOUR_A.isoformat()}): {len(loops)} 条完整快照（不应被标记）")

    # ── Hour B: 15 条不完整快照 ──
    for lp in loops[:15]:
        snap = KpiSnapshotHourly(
            id=str(uuid4()),
            loop_id=str(lp.id),
            ts_start=ts_b,
            ts_end=ts_b + timedelta(hours=1),
            status="SUCCESS",
            score=Decimal("70.00"),
            good_value_rate=Decimal("90.00"),
            auto_mode_rate=Decimal("80.00"),
            effective_auto_rate=Decimal("80.00"),
            steady_rate=Decimal("65.00"),
            accuracy_rate=Decimal("70.00"),
            fast_rate=Decimal("60.00"),
            oscillation_rate=Decimal("10.00"),
            saturation_rate=Decimal("5.00"),
            algorithm_version=TEST_MARKER,
            sampling_freq="1s",
            quality_policy="KEEP_ALL_WITH_VALIDITY",
            valid_rate=Decimal("0.9000"),
            confidence_level="B",
            data_lineage={"algorithm_version": TEST_MARKER},
        )
        db.add(snap)
    print(f"[构造] Hour B ({HOUR_B.isoformat()}): 15 条不完整快照（应标记不完整）")

    # ── Hour C: 0 条（不插入任何数据）──
    print(f"[构造] Hour C ({HOUR_C.isoformat()}): 0 条（应标记缺失）")

    await db.commit()


async def verify_detection(loops: list[LoopLedger]) -> bool:
    """调用 detect_missing_snapshots 验证检测结果"""
    # 延迟导入被测函数
    import sys

    sys.path.insert(0, "/Users/zhangping/DEV/CLPM/backend/scripts")
    from backfill_kpi import detect_missing_snapshots

    print("\n" + "=" * 60)
    print("[验证] 调用 detect_missing_snapshots(lookback_hours=6)")
    print("=" * 60)

    missing = await detect_missing_snapshots(lookback_hours=6)

    expected_loop_count = len(loops)

    # ── 验证点 1: Hour A 不在缺失列表中 ──
    if HOUR_A not in missing:
        print(f"\n  [✓] 验证点1 通过: Hour A ({HOUR_A.isoformat()}) 完整快照未被标记")
    else:
        print("\n  [✗] 验证点1 失败: Hour A 不应出现在缺失列表中")
        return False

    # ── 验证点 2: Hour B 在缺失列表中 ──
    if HOUR_B in missing:
        print(f"  [✓] 验证点2 通过: Hour B ({HOUR_B.isoformat()}) 不完整快照被正确标记")
    else:
        print("  [✗] 验证点2 失败: Hour B 应出现在缺失列表中")
        return False

    # ── 验证点 3: Hour C 在缺失列表中 ──
    if HOUR_C in missing:
        print(f"  [✓] 验证点3 通过: Hour C ({HOUR_C.isoformat()}) 缺失快照被正确标记")
    else:
        print("  [✗] 验证点3 失败: Hour C 应出现在缺失列表中")
        return False

    # ── 验证点 4: 缺失列表包含其他空窗口 ──
    # 当前小时不应在列表中（未到计算时间）
    current_hour = NOW.replace(minute=0, second=0, microsecond=0)
    if current_hour not in missing:
        print(f"  [✓] 验证点4 通过: 当前小时 ({current_hour.isoformat()}) 未被标记")
    else:
        print("  [⚠] 验证点4 注意: 当前小时在缺失列表中（可能因为本小时尚未计算）")

    # ── 验证点 5: 统计验证 ──
    # 直接查询确认 Hour A 有 27 条，Hour B 有 15 条
    async with AsyncSessionLocal() as db:
        cnt_a = await db.scalar(
            select(func.count())
            .select_from(KpiSnapshotHourly)
            .where(
                KpiSnapshotHourly.ts_start == HOUR_A.replace(tzinfo=None),
                KpiSnapshotHourly.algorithm_version == TEST_MARKER,
            )
        )
        cnt_b = await db.scalar(
            select(func.count())
            .select_from(KpiSnapshotHourly)
            .where(
                KpiSnapshotHourly.ts_start == HOUR_B.replace(tzinfo=None),
                KpiSnapshotHourly.algorithm_version == TEST_MARKER,
            )
        )

    if cnt_a == expected_loop_count:
        print(f"  [✓] 验证点5 通过: Hour A 快照数={cnt_a}（期望 {expected_loop_count}）")
    else:
        print(f"  [✗] 验证点5 失败: Hour A 快照数={cnt_a}（期望 {expected_loop_count}）")
        return False

    if cnt_b == 15:
        print(
            f"  [✓] 验证点6 通过: Hour B 快照数={cnt_b}"
            f"（期望 15，< {expected_loop_count} → 标记不完整）"
        )
    else:
        print(f"  [✗] 验证点6 失败: Hour B 快照数={cnt_b}（期望 15）")
        return False

    print("\n" + "=" * 60)
    print("[验证] 所有验证点通过！--auto 检测逻辑符合预期")
    print("=" * 60)
    return True


async def main():
    print("=" * 60)
    print("--auto 模式检测逻辑验证")
    print(f"Hour A (完整): {HOUR_A.isoformat()}")
    print(f"Hour B (不完整): {HOUR_B.isoformat()}")
    print(f"Hour C (缺失): {HOUR_C.isoformat()}")
    print("=" * 60)

    async with AsyncSessionLocal() as db:
        # 1. 清理旧测试数据
        await clear_test_data(db)

        # 2. 获取活跃回路
        loops = await get_active_loops(db)
        print(f"[准备] 活跃回路数: {len(loops)}")

        # 3. 插入模拟数据
        await insert_mock_snapshots(db, loops)

    # 4. 运行检测
    success = await verify_detection(loops)

    # 5. 清理测试数据
    async with AsyncSessionLocal() as db:
        await clear_test_data(db)

    print("\n[清理] 测试数据已清除")
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
