#!/usr/bin/env python3
"""装置级性能评估端到端验证脚本。

构建完整的工厂模型层级 + 控制回路 + 回路级 KPI 快照，
然后触发节点级聚合计算，验证加权聚合、递归收集、查询服务。

用法::

    cd backend && uv run python scripts/e2e_node_kpi.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

# 确保能导入 app 模块
sys.path.insert(0, ".")

from sqlalchemy import delete, select

from app.core.db import AsyncSessionLocal
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly
from app.models.node_kpi import KpiNodeSnapshotHourly
from app.models.plant_node import PlantNode
from app.services.node_performance import (
    calculate_and_save_node_snapshot,
    collect_descendant_loop_ids,
    get_node_latest_snapshot,
    get_node_ranking,
    get_node_trend,
    get_nodes_overview,
)

# ============================================================================
# 测试数据标记（用于清理）
# ============================================================================

TEST_TAG = "[E2E_TEST]"
# P3 #55: 与 app/services/confidence_evaluator.py ALGORITHM_VERSION 统一为 v2.0
ALGORITHM_VERSION = "KPI_CALC_v2.0"

# 工厂模型层级定义
# 化工厂(FACTORY)
#   ├── 催化裂化装置(UNIT) ← is_kpi_enabled
#   │     ├── 反应器区(EQUIPMENT)
#   │     └── 分馏塔区(EQUIPMENT)
#   └── 常减压装置(UNIT) ← is_kpi_enabled
#         ├── 常压塔区(EQUIPMENT)
#         └── 减压塔区(EQUIPMENT)

FACTORY_TREE = {
    "name": f"{TEST_TAG}化工厂",
    "type": "FACTORY",
    "is_kpi_enabled": True,
    "children": [
        {
            "name": f"{TEST_TAG}催化裂化装置",
            "type": "UNIT",
            "is_kpi_enabled": True,
            "children": [
                {"name": f"{TEST_TAG}反应器区", "type": "EQUIPMENT", "children": []},
                {"name": f"{TEST_TAG}分馏塔区", "type": "EQUIPMENT", "children": []},
            ],
        },
        {
            "name": f"{TEST_TAG}常减压装置",
            "type": "UNIT",
            "is_kpi_enabled": True,
            "children": [
                {"name": f"{TEST_TAG}常压塔区", "type": "EQUIPMENT", "children": []},
                {"name": f"{TEST_TAG}减压塔区", "type": "EQUIPMENT", "children": []},
            ],
        },
    ],
}

# 回路定义：挂载到 EQUIPMENT 节点，含 score_weight 和 KPI 快照数据
# 每个回路有不同的评分，验证加权聚合效果
LOOPS_DEF = [
    # --- 催化裂化装置/反应器区 ---
    {
        "tag_name": f"{TEST_TAG}_FIC-101",
        "description": "反应器进料流量控制",
        "loop_type": "FLOW",
        "unit_name": f"{TEST_TAG}反应器区",
        "score_weight": Decimal("1.5"),
        "score": Decimal("88.50"),
        "good_value_rate": Decimal("95.00"),
        "auto_mode_rate": Decimal("92.00"),
        "effective_auto_rate": Decimal("88.00"),
        "steady_rate": Decimal("90.00"),
        "accuracy_rate": Decimal("85.00"),
        "fast_rate": Decimal("82.00"),
        "oscillation_rate": Decimal("10.00"),
        "saturation_rate": Decimal("5.00"),
    },
    {
        "tag_name": f"{TEST_TAG}_TIC-102",
        "description": "反应器温度控制",
        "loop_type": "TEMPERATURE",
        "unit_name": f"{TEST_TAG}反应器区",
        "score_weight": Decimal("2.0"),
        "score": Decimal("75.30"),
        "good_value_rate": Decimal("88.00"),
        "auto_mode_rate": Decimal("85.00"),
        "effective_auto_rate": Decimal("78.00"),
        "steady_rate": Decimal("72.00"),
        "accuracy_rate": Decimal("70.00"),
        "fast_rate": Decimal("65.00"),
        "oscillation_rate": Decimal("25.00"),
        "saturation_rate": Decimal("12.00"),
    },
    # --- 催化裂化装置/分馏塔区 ---
    {
        "tag_name": f"{TEST_TAG}_LIC-201",
        "description": "分馏塔液位控制",
        "loop_type": "LEVEL",
        "unit_name": f"{TEST_TAG}分馏塔区",
        "score_weight": Decimal("1.0"),
        "score": Decimal("92.00"),
        "good_value_rate": Decimal("98.00"),
        "auto_mode_rate": Decimal("95.00"),
        "effective_auto_rate": Decimal("93.00"),
        "steady_rate": Decimal("94.00"),
        "accuracy_rate": Decimal("90.00"),
        "fast_rate": Decimal("88.00"),
        "oscillation_rate": Decimal("5.00"),
        "saturation_rate": Decimal("2.00"),
    },
    {
        "tag_name": f"{TEST_TAG}_FIC-202",
        "description": "分馏塔塔顶回流流量控制",
        "loop_type": "FLOW",
        "unit_name": f"{TEST_TAG}分馏塔区",
        "score_weight": Decimal("1.2"),
        "score": Decimal("68.40"),
        "good_value_rate": Decimal("82.00"),
        "auto_mode_rate": Decimal("60.00"),
        "effective_auto_rate": Decimal("55.00"),
        "steady_rate": Decimal("65.00"),
        "accuracy_rate": Decimal("62.00"),
        "fast_rate": Decimal("58.00"),
        "oscillation_rate": Decimal("35.00"),
        "saturation_rate": Decimal("18.00"),
    },
    # --- 常减压装置/常压塔区 ---
    {
        "tag_name": f"{TEST_TAG}_TIC-301",
        "description": "常压塔塔顶温度控制",
        "loop_type": "TEMPERATURE",
        "unit_name": f"{TEST_TAG}常压塔区",
        "score_weight": Decimal("2.0"),
        "score": Decimal("85.70"),
        "good_value_rate": Decimal("93.00"),
        "auto_mode_rate": Decimal("90.00"),
        "effective_auto_rate": Decimal("86.00"),
        "steady_rate": Decimal("87.00"),
        "accuracy_rate": Decimal("83.00"),
        "fast_rate": Decimal("80.00"),
        "oscillation_rate": Decimal("12.00"),
        "saturation_rate": Decimal("6.00"),
    },
    {
        "tag_name": f"{TEST_TAG}_FIC-302",
        "description": "常压塔进料流量控制",
        "loop_type": "FLOW",
        "unit_name": f"{TEST_TAG}常压塔区",
        "score_weight": Decimal("1.0"),
        "score": Decimal("78.20"),
        "good_value_rate": Decimal("90.00"),
        "auto_mode_rate": Decimal("88.00"),
        "effective_auto_rate": Decimal("80.00"),
        "steady_rate": Decimal("76.00"),
        "accuracy_rate": Decimal("75.00"),
        "fast_rate": Decimal("72.00"),
        "oscillation_rate": Decimal("18.00"),
        "saturation_rate": Decimal("8.00"),
    },
    # --- 常减压装置/减压塔区 ---
    {
        "tag_name": f"{TEST_TAG}_TIC-401",
        "description": "减压塔塔顶温度控制",
        "loop_type": "TEMPERATURE",
        "unit_name": f"{TEST_TAG}减压塔区",
        "score_weight": Decimal("1.8"),
        "score": Decimal("62.10"),
        "good_value_rate": Decimal("80.00"),
        "auto_mode_rate": Decimal("55.00"),
        "effective_auto_rate": Decimal("50.00"),
        "steady_rate": Decimal("60.00"),
        "accuracy_rate": Decimal("58.00"),
        "fast_rate": Decimal("52.00"),
        "oscillation_rate": Decimal("40.00"),
        "saturation_rate": Decimal("22.00"),
    },
    {
        "tag_name": f"{TEST_TAG}_PIC-402",
        "description": "减压塔塔顶压力控制",
        "loop_type": "PRESSURE",
        "unit_name": f"{TEST_TAG}减压塔区",
        "score_weight": Decimal("1.5"),
        "score": Decimal("71.80"),
        "good_value_rate": Decimal("86.00"),
        "auto_mode_rate": Decimal("70.00"),
        "effective_auto_rate": Decimal("65.00"),
        "steady_rate": Decimal("68.00"),
        "accuracy_rate": Decimal("66.00"),
        "fast_rate": Decimal("63.00"),
        "oscillation_rate": Decimal("28.00"),
        "saturation_rate": Decimal("15.00"),
    },
]


# ============================================================================
# 数据清理
# ============================================================================


async def cleanup_test_data(db) -> None:
    """清理之前的 E2E 测试数据。"""
    print("\n[1/6] 清理旧测试数据...")

    # 查找所有测试节点
    result = await db.execute(select(PlantNode).where(PlantNode.name.like(f"{TEST_TAG}%")))
    test_nodes = result.scalars().all()
    test_node_ids = [str(n.id) for n in test_nodes]

    if test_node_ids:
        # 删除节点级快照
        await db.execute(
            delete(KpiNodeSnapshotHourly).where(
                KpiNodeSnapshotHourly.plant_node_id.in_(test_node_ids)
            )
        )

        # 查找关联的回路
        loop_result = await db.execute(
            select(LoopLedger).where(LoopLedger.unit_id.in_(test_node_ids))
        )
        test_loops = loop_result.scalars().all()
        test_loop_ids = [str(loop.id) for loop in test_loops]

        if test_loop_ids:
            # 删除回路级快照
            await db.execute(
                delete(KpiSnapshotHourly).where(KpiSnapshotHourly.loop_id.in_(test_loop_ids))
            )
            # 删除回路
            await db.execute(delete(LoopLedger).where(LoopLedger.id.in_(test_loop_ids)))

        # 删除节点（先子后父，通过递归删除）
        # 由于有外键约束，需要先删子节点
        for _ in range(5):  # 最多 5 层
            children_result = await db.execute(
                select(PlantNode)
                .where(PlantNode.name.like(f"{TEST_TAG}%"))
                .where(
                    ~PlantNode.id.in_(
                        select(PlantNode.parent_id).where(PlantNode.parent_id.is_not(None))
                    )
                )
            )
            leaves = children_result.scalars().all()
            if not leaves:
                break
            for leaf in leaves:
                await db.execute(delete(PlantNode).where(PlantNode.id == leaf.id))

        # 最终清理剩余
        await db.execute(delete(PlantNode).where(PlantNode.name.like(f"{TEST_TAG}%")))

        await db.commit()
        print(f"  已清理 {len(test_nodes)} 个节点, {len(test_loops)} 个回路")
    else:
        print("  无旧数据")


# ============================================================================
# 创建工厂模型
# ============================================================================


async def create_plant_tree(db) -> dict[str, str]:
    """递归创建工厂节点树，返回 {节点名: 节点ID} 映射。"""
    print("\n[2/6] 创建工厂模型层级...")
    name_to_id: dict[str, str] = {}

    async def _create_node(node_def: dict, parent_id: str | None) -> str:
        node = PlantNode(
            id=str(uuid4()),
            name=node_def["name"],
            type=node_def["type"],
            parent_id=parent_id,
            is_kpi_enabled=node_def.get("is_kpi_enabled", False),
        )
        db.add(node)
        await db.flush()
        name_to_id[node_def["name"]] = str(node.id)
        print(
            f"  创建节点: {node_def['name']} ({node_def['type']}) "
            f"is_kpi_enabled={node_def.get('is_kpi_enabled', False)}"
        )
        for child in node_def.get("children", []):
            await _create_node(child, str(node.id))
        return str(node.id)

    await _create_node(FACTORY_TREE, None)
    await db.commit()
    print(f"  共创建 {len(name_to_id)} 个节点")
    return name_to_id


# ============================================================================
# 创建控制回路 + 回路级 KPI 快照
# ============================================================================


async def create_loops_and_snapshots(
    db, name_to_id: dict[str, str], ts_start: datetime, ts_end: datetime
) -> dict[str, str]:
    """创建控制回路和对应的回路级 KPI 快照。"""
    print("\n[3/6] 创建控制回路 + 回路级 KPI 快照...")
    tag_to_loop_id: dict[str, str] = {}

    for loop_def in LOOPS_DEF:
        unit_id = name_to_id.get(loop_def["unit_name"])
        if not unit_id:
            print(f"  ⚠ 节点不存在: {loop_def['unit_name']}")
            continue

        loop = LoopLedger(
            id=str(uuid4()),
            tag_name=loop_def["tag_name"],
            description=loop_def["description"],
            unit_id=unit_id,
            score_weight=loop_def["score_weight"],
            is_active=True,
            status="READY",
            loop_type=loop_def["loop_type"],
        )
        db.add(loop)
        await db.flush()
        tag_to_loop_id[loop_def["tag_name"]] = str(loop.id)

        # 创建回路级 KPI 快照
        snap = KpiSnapshotHourly(
            id=str(uuid4()),
            loop_id=str(loop.id),
            ts_start=ts_start,
            ts_end=ts_end,
            score=loop_def["score"],
            good_value_rate=loop_def["good_value_rate"],
            auto_mode_rate=loop_def["auto_mode_rate"],
            effective_auto_rate=loop_def["effective_auto_rate"],
            steady_rate=loop_def["steady_rate"],
            accuracy_rate=loop_def["accuracy_rate"],
            fast_rate=loop_def["fast_rate"],
            oscillation_rate=loop_def["oscillation_rate"],
            saturation_rate=loop_def["saturation_rate"],
            status="SUCCESS",
        )
        db.add(snap)

        print(
            f"  回路: {loop_def['tag_name']:25s} | 节点: {loop_def['unit_name']:20s} "
            f"| 权重: {loop_def['score_weight']} | 评分: {loop_def['score']}"
        )

    await db.commit()
    print(f"  共创建 {len(LOOPS_DEF)} 个回路 + 快照")
    return tag_to_loop_id


# ============================================================================
# 触发节点级 KPI 计算
# ============================================================================


async def trigger_node_calculation(
    db, name_to_id: dict[str, str], ts_start: datetime, ts_end: datetime
) -> None:
    """对所有 is_kpi_enabled 节点触发节点级 KPI 计算。"""
    print("\n[4/6] 触发节点级 KPI 加权聚合计算...")

    # 查询所有 is_kpi_enabled 的节点
    result = await db.execute(select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True)))
    enabled_nodes = result.scalars().all()

    for node in enabled_nodes:
        # 先查看递归收集的回路数
        loop_ids = await collect_descendant_loop_ids(db, str(node.id))
        print(f"\n  节点: {node.name} ({node.type})\n  递归收集到 {len(loop_ids)} 个回路")

        # 执行聚合计算
        snap_data = await calculate_and_save_node_snapshot(db, str(node.id), ts_start, ts_end)

        if snap_data:
            print(
                f"  ✅ 聚合结果:"
                f"\n     综合评分: {snap_data['score']}"
                f"\n     状态定级: {snap_data['status']}"
                f"\n     回路数:   {snap_data['loop_count']}"
                f"\n     投自动占比: {snap_data['auto_loop_ratio']}%"
                f"\n     好值率:   {snap_data['good_value_rate']}"
                f"\n     自控率:   {snap_data['auto_mode_rate']}"
                f"\n     平稳率:   {snap_data['steady_rate']}"
                f"\n     准确度:   {snap_data['accuracy_rate']}"
                f"\n     快速率:   {snap_data['fast_rate']}"
                f"\n     振荡率:   {snap_data['oscillation_rate']}"
                f"\n     饱和率:   {snap_data['saturation_rate']}"
            )
        else:
            print("  ❌ 无数据（回路或快照不存在）")

    await db.commit()


# ============================================================================
# 验证查询服务
# ============================================================================


async def verify_query_services(
    db, name_to_id: dict[str, str], ts_start: datetime, ts_end: datetime
) -> None:
    """验证节点级查询服务：最新快照、趋势、排名、总览。"""
    print("\n[5/6] 验证查询服务...")

    # --- 5.1 最新快照 ---
    print("\n  --- 5.1 节点最新快照 ---")
    for node_name, node_id in name_to_id.items():
        snap = await get_node_latest_snapshot(db, node_id)
        if snap:
            print(
                f"  {node_name:30s} | 评分: {snap['score']:6.2f} "
                f"| 定级: {snap['status']:12s} | 回路数: {snap['loopCount']}"
            )
        else:
            print(f"  {node_name:30s} | 无快照")

    # --- 5.2 历史趋势 ---
    print("\n  --- 5.2 节点历史趋势 ---")
    factory_id = name_to_id[f"{TEST_TAG}化工厂"]
    trend = await get_node_trend(db, factory_id, ts_start - timedelta(hours=1), ts_end)
    print(f"  节点: {trend['plantNodeName']}")
    print(f"  时间点数: {len(trend['timestamps'])}")
    for s in trend["series"]:
        print(f"    {s['metricName']:15s}: {s['values']}")

    # --- 5.3 节点排名 ---
    print("\n  --- 5.3 节点间排名（按评分降序）---")
    ranking = await get_node_ranking(
        db, ts_start - timedelta(hours=1), ts_end, sort_by="score", sort_order="desc"
    )
    for item in ranking:
        print(
            f"  #{item['rank']} {item['plantNodeName']:30s} "
            f"| 评分: {item['score']:6.2f} | 定级: {item['status']:12s} "
            f"| 类型: {item['plantNodeType']}"
        )

    # --- 5.4 全厂总览 ---
    print("\n  --- 5.4 全厂总览 ---")
    overview = await get_nodes_overview(db, ts_start - timedelta(hours=1), ts_end)
    print(f"  启用 KPI 节点总数: {overview['totalNodes']}")
    print(f"  有快照节点数:     {overview['nodesWithSnapshot']}")
    print(f"  状态分布:         {overview['statusDistribution']}")
    print("  节点列表:")
    for node in overview["nodes"]:
        print(
            f"    {node['plantNodeName']:30s} | 评分: {node['score']:6.2f} "
            f"| 定级: {node['status']:12s} | 投自动占比: {node['autoLoopRatio']:5.1f}%"
        )


# ============================================================================
# 验证加权聚合正确性
# ============================================================================


async def verify_weighted_aggregation(
    db, name_to_id: dict[str, str], ts_start: datetime, ts_end: datetime
) -> None:
    """手动验算加权聚合结果是否正确。"""
    print("\n[6/6] 验证加权聚合正确性...")

    # 验证"催化裂化装置"的加权聚合
    unit_name = f"{TEST_TAG}催化裂化装置"
    unit_id = name_to_id[unit_name]

    # 收集该装置下所有回路
    loop_ids = await collect_descendant_loop_ids(db, unit_id)
    print(f"\n  装置: {unit_name}")
    print(f"  递归收集回路数: {len(loop_ids)}")

    # 查询回路级快照
    result = await db.execute(
        select(KpiSnapshotHourly, LoopLedger)
        .join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
        .where(
            KpiSnapshotHourly.loop_id.in_(loop_ids),
            KpiSnapshotHourly.ts_start >= ts_start,
            KpiSnapshotHourly.ts_start <= ts_end,
            KpiSnapshotHourly.status == "SUCCESS",
        )
    )
    rows = result.all()

    print(f"  回路级快照数: {len(rows)}")
    print("  手动验算加权评分:")

    total_weight = Decimal("0")
    weighted_score_sum = Decimal("0")
    auto_loop_count = 0

    for snap, loop in rows:
        weight = loop.score_weight or Decimal("1.0")
        score = snap.score or Decimal("0")
        weighted_score_sum += score * weight
        total_weight += weight
        is_auto = (snap.auto_mode_rate or 0) > 0
        if is_auto:
            auto_loop_count += 1
        print(
            f"    {loop.tag_name:25s} | 评分: {score:5.2f} "
            f"| 权重: {weight:4.1f} | 加权: {score * weight:7.2f} "
            f"| 自动: {'是' if is_auto else '否'}"
        )

    if total_weight > 0:
        expected_score = (weighted_score_sum / total_weight).quantize(Decimal("0.01"))
        expected_auto_ratio = round(auto_loop_count / len(rows) * 100, 2)
        print(f"\n  期望加权评分: {expected_score}")
        print(f"  期望投自动占比: {expected_auto_ratio}%")

        # 对比实际结果
        actual_snap = await get_node_latest_snapshot(db, unit_id)
        if actual_snap:
            print(f"  实际加权评分: {actual_snap['score']}")
            print(f"  实际投自动占比: {actual_snap['autoLoopRatio']}%")

            score_match = abs(Decimal(str(actual_snap["score"])) - expected_score) < Decimal("0.1")
            ratio_match = abs(
                Decimal(str(actual_snap["autoLoopRatio"])) - Decimal(str(expected_auto_ratio))
            ) < Decimal("0.1")

            if score_match and ratio_match:
                print("  ✅ 加权聚合验证通过！")
            else:
                print("  ❌ 加权聚合验证失败！")
                if not score_match:
                    print(f"     评分不匹配: 期望 {expected_score}, 实际 {actual_snap['score']}")
                if not ratio_match:
                    print(
                        f"     占比不匹配: 期望 {expected_auto_ratio}%, "
                        f"实际 {actual_snap['autoLoopRatio']}%"
                    )


# ============================================================================
# 主函数
# ============================================================================


async def main() -> None:
    """端到端验证主流程。"""
    print("=" * 70)
    print("装置级性能评估端到端验证")
    print("=" * 70)

    # 计算时间窗（上一个完整小时，使用 naive datetime 适配数据库 TIMESTAMP WITHOUT TIME ZONE）
    now = datetime.now(UTC).replace(tzinfo=None)
    ts_end = now.replace(minute=0, second=0, microsecond=0)
    ts_start = ts_end - timedelta(hours=1)
    print(f"时间窗: {ts_start.isoformat()} ~ {ts_end.isoformat()}")

    async with AsyncSessionLocal() as db:
        # 1. 清理旧数据
        await cleanup_test_data(db)

        # 2. 创建工厂模型
        name_to_id = await create_plant_tree(db)

        # 3. 创建回路 + 回路级快照
        await create_loops_and_snapshots(db, name_to_id, ts_start, ts_end)

        # 4. 触发节点级 KPI 计算
        await trigger_node_calculation(db, name_to_id, ts_start, ts_end)

        # 5. 验证查询服务
        await verify_query_services(db, name_to_id, ts_start, ts_end)

        # 6. 验证加权聚合正确性
        await verify_weighted_aggregation(db, name_to_id, ts_start, ts_end)

    print("\n" + "=" * 70)
    print("端到端验证完成！")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
