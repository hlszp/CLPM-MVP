"""验证聚合逻辑：标准任务参与聚合，自定义任务不参与聚合，E级可信度被排除。

构造模拟数据：
- 选择"急冷分离单元"(8 个回路)作为测试节点
- 标准任务快照写入 kpi_snapshot_hourly（6 条 score=80 confidence=A + 1 条 score=90 confidence=B + 1 条 score=70 confidence=E）
- 自定义任务快照写入 kpi_snapshot_custom（3 条 score=50，验证不参与聚合）

预期聚合结果：
- E 级被排除 → 7 条参与聚合
- 全部 level=3, weight=1.0 → weight_sum=7.0
- score = (6*80 + 1*90) / 7 = 570/7 ≈ 81.43
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, select, text

from app.core.db import AsyncSessionLocal
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotCustom, KpiSnapshotHourly
from app.models.node_kpi import KpiNodeSnapshotHourly
from app.services.node_performance import aggregate_node_snapshot

# 配置日志：显示 INFO 级别，便于观察聚合过程
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
# 降低 SQLAlchemy 日志级别
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

# 测试参数
TEST_NODE_ID = "ad6a0993-0e83-4645-87f8-edecd2c85356"  # 急冷分离单元
TEST_TS_START = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
TEST_TS_END = TEST_TS_START + timedelta(hours=1)
CUSTOM_TASK_ID = str(uuid4())


async def clear_test_data(db):
    """清除测试时间窗内的所有快照数据（幂等，可重复运行）"""
    # 清除标准快照
    await db.execute(
        delete(KpiSnapshotHourly).where(
            KpiSnapshotHourly.ts_start == TEST_TS_START.replace(tzinfo=None)
        )
    )
    # 清除自定义快照（按 task_id）
    await db.execute(
        delete(KpiSnapshotCustom).where(KpiSnapshotCustom.task_id == CUSTOM_TASK_ID)
    )
    # 清除节点级快照
    await db.execute(
        delete(KpiNodeSnapshotHourly).where(
            KpiNodeSnapshotHourly.ts_start == TEST_TS_START.replace(tzinfo=None),
            KpiNodeSnapshotHourly.plant_node_id == TEST_NODE_ID,
        )
    )
    await db.commit()
    print(f"[清理] 已清除测试时间窗 {TEST_TS_START.isoformat()} 的旧数据")


async def insert_mock_data(db):
    """插入模拟快照数据"""
    # 查询测试节点下属回路
    result = await db.execute(
        select(LoopLedger).where(
            LoopLedger.unit_id == TEST_NODE_ID,
            LoopLedger.is_active.is_(True),
            LoopLedger.status == "READY",
        ).order_by(LoopLedger.tag_name)
    )
    loops = list(result.scalars().all())
    print(f"[构造] 测试节点 {TEST_NODE_ID} 下属回路数: {len(loops)}")
    for i, lp in enumerate(loops):
        print(f"  [{i}] id={lp.id} tag={lp.tag_name} level={lp.level}")

    if len(loops) < 8:
        print(f"[警告] 预期 8 个回路，实际 {len(loops)} 个，调整测试计划")

    # 分配模拟数据：
    # loops[0..5]: 标准任务 score=80 confidence=A
    # loops[6]:    标准任务 score=90 confidence=B
    # loops[7]:    标准任务 score=70 confidence=E（应被排除）
    # loops[0..2]: 额外自定义任务 score=50（不应参与聚合）
    ts_start_naive = TEST_TS_START.replace(tzinfo=None)
    ts_end_naive = TEST_TS_END.replace(tzinfo=None)

    standard_snapshots = []
    for i, lp in enumerate(loops):
        if i <= 5:
            score = Decimal("80.00")
            confidence = "A"
        elif i == 6:
            score = Decimal("90.00")
            confidence = "B"
        else:
            score = Decimal("70.00")
            confidence = "E"

        snap = KpiSnapshotHourly(
            id=str(uuid4()),
            loop_id=str(lp.id),
            ts_start=ts_start_naive,
            ts_end=ts_end_naive,
            status="SUCCESS",
            score=score,
            good_value_rate=Decimal("95.00"),
            auto_mode_rate=Decimal("100.00"),
            effective_auto_rate=Decimal("100.00"),
            steady_rate=Decimal("85.00"),
            accuracy_rate=Decimal("80.00"),
            fast_rate=Decimal("75.00"),
            oscillation_rate=Decimal("5.00"),
            saturation_rate=Decimal("3.00"),
            algorithm_version="KPI_CALC_v2.0",
            sampling_freq="1s",
            quality_policy="KEEP_ALL_WITH_VALIDITY",
            valid_rate=Decimal("0.9500"),
            confidence_level=confidence,
            data_lineage={"algorithm_version": "KPI_CALC_v2.0"},
        )
        standard_snapshots.append(snap)
        db.add(snap)
        print(f"  [标准] loop={lp.tag_name} score={score} confidence={confidence}")

    # 自定义任务快照（3 条，score=50，不应参与聚合）
    custom_snapshots = []
    for i in range(min(3, len(loops))):
        lp = loops[i]
        snap = KpiSnapshotCustom(
            id=str(uuid4()),
            task_id=CUSTOM_TASK_ID,
            loop_id=str(lp.id),
            ts_start=ts_start_naive,
            ts_end=ts_end_naive,
            status="SUCCESS",
            score=Decimal("50.00"),
            good_value_rate=Decimal("90.00"),
            auto_mode_rate=Decimal("80.00"),
            effective_auto_rate=Decimal("80.00"),
            steady_rate=Decimal("70.00"),
            accuracy_rate=Decimal("50.00"),
            fast_rate=Decimal("60.00"),
            oscillation_rate=Decimal("10.00"),
            saturation_rate=Decimal("5.00"),
            algorithm_version="KPI_CALC_v2.0",
            valid_rate=Decimal("0.9000"),
            confidence_level="A",
            data_lineage={"algorithm_version": "KPI_CALC_v2.0"},
        )
        custom_snapshots.append(snap)
        db.add(snap)
        print(f"  [自定义] loop={lp.tag_name} score=50.00 (不应参与聚合)")

    await db.commit()
    print(f"\n[构造] 完成: {len(standard_snapshots)} 条标准快照 + {len(custom_snapshots)} 条自定义快照")
    return loops


async def verify_aggregation(db, loops):
    """运行聚合并验证结果"""
    print("\n" + "=" * 60)
    print("[验证] 开始运行 aggregate_node_snapshot")
    print("=" * 60)

    result = await aggregate_node_snapshot(
        db=db,
        plant_node_id=TEST_NODE_ID,
        ts_start=TEST_TS_START.replace(tzinfo=None),
        ts_end=TEST_TS_END.replace(tzinfo=None),
    )

    print("\n" + "=" * 60)
    print("[验证] 聚合结果")
    print("=" * 60)

    if result is None:
        print("[失败] 聚合结果为 None！")
        return False

    print(f"  plant_node_id: {result['plant_node_id']}")
    print(f"  score: {result['score']}")
    print(f"  loop_count: {result['loop_count']}")
    print(f"  status: {result['status']}")
    print(f"  accuracy_rate: {result['accuracy_rate']}")
    print(f"  steady_rate: {result['steady_rate']}")

    # ── 验证点 1: 参与聚合的回路数 ──
    expected_count = len(loops) - 1  # 排除 1 条 E 级
    actual_count = result["loop_count"]
    if actual_count == expected_count:
        print(f"\n  [✓] 验证点1 通过: 参与聚合回路数={actual_count}（已排除 E 级）")
    else:
        print(f"\n  [✗] 验证点1 失败: 期望 {expected_count}，实际 {actual_count}")
        return False

    # ── 验证点 2: 综合评分加权平均 ──
    # 6 条 score=80 (weight=1.0) + 1 条 score=90 (weight=1.0) = 570/7 ≈ 81.43
    expected_score = (6 * 80 + 1 * 90) / (6 + 1)
    actual_score = float(result["score"])
    if abs(actual_score - expected_score) < 0.1:
        print(f"  [✓] 验证点2 通过: 综合评分={actual_score:.2f}（期望 {expected_score:.2f}）")
    else:
        print(f"  [✗] 验证点2 失败: 期望 {expected_score:.2f}，实际 {actual_score:.2f}")
        return False

    # ── 验证点 3: 自定义任务未污染聚合 ──
    # 如果自定义任务（score=50）参与了聚合，score 会明显低于 81.43
    if actual_score > 75:
        print(f"  [✓] 验证点3 通过: 自定义任务(score=50)未参与聚合（score={actual_score:.2f} > 75）")
    else:
        print(f"  [✗] 验证点3 失败: 自定义任务可能参与了聚合（score={actual_score:.2f} 偏低）")
        return False

    # ── 验证点 4: E 级被排除 ──
    # 如果 E 级(score=70)参与了聚合，score = (6*80+90+70)/8 = 640/8 = 80.00
    # 排除 E 级后 score = 570/7 ≈ 81.43
    if actual_score > 80.5:
        print(f"  [✓] 验证点4 通过: E 级快照(score=70)已被排除（score={actual_score:.2f} > 80.5）")
    else:
        print(f"  [✗] 验证点4 失败: E 级可能未被排除（score={actual_score:.2f} ≤ 80.5）")
        return False

    # ── 验证点 5: accuracy_rate 加权平均 ──
    # 6 条 accuracy=80 + 1 条 accuracy=80（E级那条也是80，但被排除）
    # 实际上所有标准快照 accuracy_rate=80，所以期望 80.00
    expected_accuracy = 80.0
    actual_accuracy = float(result["accuracy_rate"])
    if abs(actual_accuracy - expected_accuracy) < 0.1:
        print(f"  [✓] 验证点5 通过: accuracy_rate={actual_accuracy:.2f}（期望 {expected_accuracy:.2f}）")
    else:
        print(f"  [✗] 验证点5 失败: 期望 {expected_accuracy:.2f}，实际 {actual_accuracy:.2f}")
        return False

    print("\n" + "=" * 60)
    print("[验证] 所有验证点通过！聚合逻辑符合预期")
    print("=" * 60)
    return True


async def main():
    print("=" * 60)
    print("CLPM 聚合逻辑验证脚本")
    print(f"测试节点: {TEST_NODE_ID} (急冷分离单元)")
    print(f"测试时间窗: {TEST_TS_START.isoformat()} ~ {TEST_TS_END.isoformat()}")
    print(f"自定义任务 ID: {CUSTOM_TASK_ID}")
    print("=" * 60)

    async with AsyncSessionLocal() as db:
        # 1. 清除旧数据
        await clear_test_data(db)

        # 2. 插入模拟数据
        loops = await insert_mock_data(db)

        # 3. 运行聚合并验证
        success = await verify_aggregation(db, loops)

        # 4. 清理测试数据
        await clear_test_data(db)
        print("\n[清理] 测试数据已清除")

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
