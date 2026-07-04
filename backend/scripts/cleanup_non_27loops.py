#!/usr/bin/env python3
"""清理非 27 测试回路的数据，仅保留 3 单元 27 回路 189 tag。

清理范围：
    - PostgreSQL: plant_node / loop_ledger / loop_tag_mapping / tag_registry
    - TDengine: 非 27 回路的子表

安全措施：
    - 清理前打印待删除记录数
    - 事务包裹，异常回滚
    - --dry-run 预览模式

用法::

    cd backend && uv run python scripts/cleanup_non_27loops.py --dry-run
    cd backend && uv run python scripts/cleanup_non_27loops.py
"""

from __future__ import annotations

import argparse
import asyncio
import re

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.db import AsyncSessionLocal

# 3 个目标单元 ID（与 simulate_unit_loops.py 一致）
TARGET_UNIT_IDS: list[str] = [
    "3353a2b2-2d4f-4907-9964-fb2aac837352",  # 脱甲烷精馏单元
    "07f43143-4f47-4f31-869c-bcdae8ecd865",  # 醛化反应单元
    "ad6a0993-0e83-4645-87f8-edecd2c85356",  # 急冷分离单元
]

_TD_REST_PORT = settings.TDENGINE_PORT + 11
TD_REST_DB_URL = f"http://{settings.TDENGINE_HOST}:{_TD_REST_PORT}/rest/sql/{settings.TDENGINE_DB}"


def subtable_name(tag_name: str) -> str:
    """回路位号 → TDengine 子表名（P3 #54：复用 app.core.tdengine.make_subtable_name）."""
    from app.core.tdengine import make_subtable_name

    return make_subtable_name(tag_name)


async def get_counts(db) -> dict[str, int]:
    """统计当前数据和待保留数据量."""
    stats = {}

    # 工厂节点
    r = await db.execute(text("SELECT count(*) FROM plant_node"))
    stats["plant_node_total"] = r.scalar()
    r = await db.execute(
        text("SELECT count(*) FROM plant_node WHERE id = ANY(:ids)"),
        {"ids": TARGET_UNIT_IDS},
    )
    stats["plant_node_keep"] = r.scalar()

    # 回路
    r = await db.execute(text("SELECT count(*) FROM loop_ledger"))
    stats["loop_total"] = r.scalar()
    r = await db.execute(
        text("SELECT count(*) FROM loop_ledger WHERE unit_id = ANY(:ids)"),
        {"ids": TARGET_UNIT_IDS},
    )
    stats["loop_keep"] = r.scalar()

    # tag 映射
    r = await db.execute(text("SELECT count(*) FROM loop_tag_mapping"))
    stats["mapping_total"] = r.scalar()

    # tag 注册表
    r = await db.execute(text("SELECT count(*) FROM tag_registry"))
    stats["tag_total"] = r.scalar()

    # KPI 快照
    r = await db.execute(text("SELECT count(*) FROM kpi_snapshot_hourly"))
    stats["kpi_snapshot_total"] = r.scalar()

    # 诊断结果
    r = await db.execute(text("SELECT count(*) FROM diagnosis_result"))
    stats["diagnosis_total"] = r.scalar()

    return stats


async def get_keep_tag_ids(db) -> list[str]:
    """获取 27 回路关联的 189 tag ID."""
    r = await db.execute(
        text("""
            SELECT DISTINCT tag_id FROM loop_tag_mapping
            WHERE loop_id IN (
                SELECT id FROM loop_ledger WHERE unit_id = ANY(:unit_ids)
            )
        """),
        {"unit_ids": TARGET_UNIT_IDS},
    )
    return [row[0] for row in r.fetchall()]


async def get_delete_loop_tag_names(db) -> list[str]:
    """获取待删除回路的 tag_name（用于删 TDengine 子表）."""
    r = await db.execute(
        text("""
            SELECT tag_name FROM loop_ledger
            WHERE unit_id != ALL(:unit_ids)
        """),
        {"unit_ids": TARGET_UNIT_IDS},
    )
    return [row[0] for row in r.fetchall()]


async def get_keep_loop_tag_names(db) -> list[str]:
    """获取保留回路的 tag_name."""
    r = await db.execute(
        text("SELECT tag_name FROM loop_ledger WHERE unit_id = ANY(:ids)"),
        {"ids": TARGET_UNIT_IDS},
    )
    return [row[0] for row in r.fetchall()]


async def cleanup_postgres(db, dry_run: bool = False) -> dict[str, int]:
    """清理 PostgreSQL 非 27 回路数据."""
    deleted: dict[str, int] = {}
    keep_tag_ids = await get_keep_tag_ids(db)

    # 1. 删除非 27 回路的 KPI 快照（CASCADE 不覆盖此表，需手动删）
    r = await db.execute(
        text("""
            DELETE FROM kpi_snapshot_hourly
            WHERE loop_id NOT IN (
                SELECT id FROM loop_ledger WHERE unit_id = ANY(:ids)
            )
        """),
        {"ids": TARGET_UNIT_IDS},
    )
    deleted["kpi_snapshot_hourly"] = r.rowcount
    if dry_run:
        await db.rollback()
        return deleted

    # 2. 删除非 27 回路的诊断结果（CASCADE 会自动删，但显式删更安全）
    r = await db.execute(
        text("""
            DELETE FROM diagnosis_result
            WHERE loop_id NOT IN (
                SELECT id FROM loop_ledger WHERE unit_id = ANY(:ids)
            )
        """),
        {"ids": TARGET_UNIT_IDS},
    )
    deleted["diagnosis_result"] = r.rowcount

    # 3. 删除非 27 回路的 loop_ledger（CASCADE 会级联删 loop_tag_mapping）
    # 必须先于 plant_node 删除，因为 loop_ledger.unit_id 引用 plant_node.id
    r = await db.execute(
        text("DELETE FROM loop_ledger WHERE unit_id != ALL(:ids)"),
        {"ids": TARGET_UNIT_IDS},
    )
    deleted["loop_ledger"] = r.rowcount

    # 4. 删除非 3 单元的工厂节点（循环删除叶子节点，从深到浅）
    # plant_node 有自引用外键（parent_id），需先删子节点再删父节点
    total_plant_deleted = 0
    for _ in range(20):  # 最多 20 层深度
        r = await db.execute(
            text("""
                DELETE FROM plant_node
                WHERE id != ALL(:ids)
                AND id NOT IN (
                    SELECT DISTINCT parent_id FROM plant_node
                    WHERE parent_id IS NOT NULL
                )
            """),
            {"ids": TARGET_UNIT_IDS},
        )
        if r.rowcount == 0:
            break
        total_plant_deleted += r.rowcount
    deleted["plant_node"] = total_plant_deleted

    # 5. 删除非 189 tag 的 tag_registry
    if keep_tag_ids:
        r = await db.execute(
            text("DELETE FROM tag_registry WHERE id != ALL(:ids)"),
            {"ids": keep_tag_ids},
        )
        deleted["tag_registry"] = r.rowcount

    await db.commit()
    return deleted


async def cleanup_tdengine(dry_run: bool = False) -> tuple[int, int]:
    """清理 TDengine 非 27 回路子表."""
    auth = (settings.TDENGINE_USER, settings.TDENGINE_PASSWORD)

    async with httpx.AsyncClient(timeout=30.0, auth=auth) as client:
        # 获取所有子表
        r = await client.post(
            TD_REST_DB_URL,
            content="SHOW TABLES LIKE 'd_loop_%'",
        )
        if r.status_code != 200:
            print(f"[TDengine] 获取子表列表失败: {r.status_code} {r.text[:200]}")
            return 0, 0

        data = r.json()
        if not data.get("data"):
            return 0, 0

        # 解析子表名
        all_tables: list[str] = []
        for row in data["data"]:
            if row and isinstance(row[0], str) and row[0].startswith("d_loop_"):
                all_tables.append(row[0])

        # 获取保留的子表名
        async with AsyncSessionLocal() as db:
            keep_tag_names = await get_keep_loop_tag_names(db)
            keep_tables = {subtable_name(n) for n in keep_tag_names}

        delete_tables = [t for t in all_tables if t not in keep_tables]
        kept = len(all_tables) - len(delete_tables)

        print(f"[TDengine] 子表总数: {len(all_tables)}, 保留: {kept}, 待删: {len(delete_tables)}")

        if dry_run:
            for t in delete_tables[:10]:
                print(f"  [DRY-RUN] 将删除: {t}")
            if len(delete_tables) > 10:
                print(f"  ... 共 {len(delete_tables)} 个")
            return 0, len(delete_tables)

        # 执行删除
        deleted = 0
        for t in delete_tables:
            try:
                r = await client.post(
                    TD_REST_DB_URL,
                    content=f"DROP TABLE IF EXISTS {t}",
                )
                if r.status_code == 200:
                    deleted += 1
                else:
                    print(f"  [WARN] 删除 {t} 失败: {r.status_code}")
            except Exception as exc:
                print(f"  [ERROR] 删除 {t} 异常: {exc}")

        return deleted, len(delete_tables)


async def main(dry_run: bool = False) -> None:
    print("=" * 70)
    print("CLPM 数据清理：仅保留 3 单元 27 回路 189 tag")
    print("=" * 70)

    # 统计清理前状态
    async with AsyncSessionLocal() as db:
        stats = await get_counts(db)
        keep_tag_ids = await get_keep_tag_ids(db)
        delete_tag_names = await get_delete_loop_tag_names(db)

    print("\n[清理前统计]")
    print(f"  工厂节点:     {stats['plant_node_total']} (保留 {stats['plant_node_keep']})")
    print(f"  控制回路:     {stats['loop_total']} (保留 {stats['loop_keep']})")
    print(f"  tag 映射:     {stats['mapping_total']}")
    print(f"  tag 注册表:   {stats['tag_total']} (保留 {len(keep_tag_ids)})")
    print(f"  KPI 快照:     {stats['kpi_snapshot_total']}")
    print(f"  诊断结果:     {stats['diagnosis_total']}")
    print(f"  待删除回路数: {len(delete_tag_names)}")

    if dry_run:
        print("\n[DRY-RUN 模式] 仅预览，不执行删除")
        print("\n待删除回路（前 10 个）:")
        for name in delete_tag_names[:10]:
            print(f"  - {name}")
        if len(delete_tag_names) > 10:
            print(f"  ... 共 {len(delete_tag_names)} 个")
    else:
        print("\n[执行清理]")

    # PostgreSQL 清理
    print("\n--- PostgreSQL 清理 ---")
    async with AsyncSessionLocal() as db:
        deleted = await cleanup_postgres(db, dry_run=dry_run)
        if not dry_run:
            for table, count in deleted.items():
                print(f"  {table}: 删除 {count} 条")

    # TDengine 清理
    print("\n--- TDengine 清理 ---")
    td_deleted, td_total = await cleanup_tdengine(dry_run=dry_run)
    if not dry_run:
        print(f"  子表删除: {td_deleted}/{td_total}")

    # 验证清理后状态
    if not dry_run:
        print("\n[清理后验证]")
        async with AsyncSessionLocal() as db:
            stats = await get_counts(db)
            keep_tag_ids = await get_keep_tag_ids(db)
        print(f"  工厂节点:     {stats['plant_node_total']}")
        print(f"  控制回路:     {stats['loop_total']}")
        print(f"  tag 映射:     {stats['mapping_total']}")
        print(f"  tag 注册表:   {stats['tag_total']}")
        print(f"  KPI 快照:     {stats['kpi_snapshot_total']}")
        print(f"  诊断结果:     {stats['diagnosis_total']}")
        print(f"  保留 tag 数:  {len(keep_tag_ids)}")

    print("\n" + "=" * 70)
    print("清理完成" if not dry_run else "预览完成（未执行删除）")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清理非 27 测试回路数据")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不执行删除")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
