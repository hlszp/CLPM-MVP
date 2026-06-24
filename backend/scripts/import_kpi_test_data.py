#!/usr/bin/env python3
"""KPI 测试数据导入脚本 — 将 generate_kpi_test_data.py 生成的数据导入数据库。

功能：
1. 在 PostgreSQL 创建 7 个测试回路 + Tag 注册 + 映射
2. 在 TDengine 创建子表并写入时序数据
3. 触发 KPI 计算并输出结果

用法::

    cd backend && uv run python scripts/import_kpi_test_data.py
    cd backend && uv run python scripts/import_kpi_test_data.py --clean  # 清理旧数据
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.db import AsyncSessionLocal

# ============================================================================
# 常量
# ============================================================================

FIXTURE_PATH = Path(__file__).parent.parent / "tests" / "fixtures" / "kpi_test_data.json"

TD_REST_BASE = f"http://{settings.TDENGINE_HOST}:{settings.TDENGINE_PORT + 11}/rest/sql"
TD_REST_DB_URL = f"{TD_REST_BASE}/{settings.TDENGINE_DB}"

BATCH_SIZE = 500  # TDengine 批量写入行数
MAX_CONCURRENT = 3  # 并发写入数

# 测试回路配置（固定 UUID 便于清理）
TEST_LOOPS = [
    {
        "id": "a0000000-0000-0000-0000-000000000001",
        "tag_name": "KPI-FR-001",
        "description": "KPI测试-快速响应回路",
        "scenario": "fast_response",
        "unit_id": "00000000-0000-0000-0000-000000000111",
        "score_weight": 1.0,
    },
    {
        "id": "a0000000-0000-0000-0000-000000000002",
        "tag_name": "KPI-SR-002",
        "description": "KPI测试-慢速响应回路",
        "scenario": "slow_response",
        "unit_id": "00000000-0000-0000-0000-000000000112",
        "score_weight": 1.0,
    },
    {
        "id": "a0000000-0000-0000-0000-000000000003",
        "tag_name": "KPI-OSC-003",
        "description": "KPI测试-振荡回路",
        "scenario": "oscillation",
        "unit_id": "00000000-0000-0000-0000-000000000113",
        "score_weight": 1.0,
    },
    {
        "id": "a0000000-0000-0000-0000-000000000004",
        "tag_name": "KPI-SAT-004",
        "description": "KPI测试-OP饱和回路",
        "scenario": "op_saturation",
        "unit_id": "00000000-0000-0000-0000-000000000114",
        "score_weight": 1.0,
    },
    {
        "id": "a0000000-0000-0000-0000-000000000005",
        "tag_name": "KPI-NOR-005",
        "description": "KPI测试-正常回路",
        "scenario": "normal",
        "unit_id": "00000000-0000-0000-0000-000000000111",
        "score_weight": 1.0,
    },
    {
        "id": "a0000000-0000-0000-0000-000000000006",
        "tag_name": "KPI-MAN-006",
        "description": "KPI测试-手动模式回路",
        "scenario": "manual_mode",
        "unit_id": "00000000-0000-0000-0000-000000000112",
        "score_weight": 1.0,
    },
    {
        "id": "a0000000-0000-0000-0000-000000000007",
        "tag_name": "KPI-AR2-007",
        "description": "KPI测试-纯AR2信号回路",
        "scenario": "pure_ar2",
        "unit_id": "00000000-0000-0000-0000-000000000113",
        "score_weight": 1.0,
    },
]

TAG_ROLES = ["PV", "SP", "OP", "MODE"]


# ============================================================================
# TDengine 操作
# ============================================================================

async def td_execute(client: httpx.AsyncClient, sql: str, use_db: bool = True) -> dict | None:
    """执行 TDengine SQL。"""
    url = TD_REST_DB_URL if use_db else TD_REST_BASE
    try:
        resp = await client.post(url, content=sql.encode("utf-8"), headers={"Content-Type": "text/plain"})
        result = resp.json()
        if result.get("code") == 0:
            return result
        desc = result.get("desc", "未知错误")
        if "already exists" not in desc.lower():
            print(f"  ⚠ TDengine SQL 错误: {desc[:200]}")
        return None
    except Exception as exc:
        print(f"  ⚠ TDengine 请求异常: {exc}")
        return None


def subtable_name(tag_name: str) -> str:
    """子表命名: d_loop_<位号小写连字符转下划线>。"""
    import re
    name = "d_loop_" + tag_name.lower().replace("-", "_").replace(".", "_")
    return re.sub(r"_+", "_", name)


async def setup_tdengine(client: httpx.AsyncClient, clean: bool = False) -> None:
    """创建 TDengine 数据库、超级表、子表。"""
    # 创建数据库（幂等）
    await td_execute(
        client,
        "CREATE DATABASE IF NOT EXISTS clpm_ts KEEP 365 DURATION 10 PRECISION 'ms'",
        use_db=False,
    )

    # 创建超级表（幂等）
    await td_execute(client, """
        CREATE STABLE IF NOT EXISTS st_loop_data (
            ts          TIMESTAMP,
            pv          FLOAT,
            sp          FLOAT,
            op          FLOAT,
            mode        TINYINT,
            pid_p       FLOAT,
            pid_i       FLOAT,
            pid_d       FLOAT,
            pv_quality  TINYINT
        ) TAGS (
            loop_id     BINARY(36),
            unit_id     BINARY(36)
        )
    """)

    # 创建子表
    for cfg in TEST_LOOPS:
        sub = subtable_name(cfg["tag_name"])
        if clean:
            await td_execute(client, f"DROP TABLE IF EXISTS {sub}")
        await td_execute(client, (
            f"CREATE TABLE IF NOT EXISTS {sub} "
            f"USING st_loop_data TAGS ('{cfg['id']}', '{cfg['unit_id']}')"
        ))

    print(f"  ✓ TDengine 子表创建完成（{len(TEST_LOOPS)} 张）")


async def write_scenario_data(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    loop_cfg: dict,
    scenario_data: list[dict],
    base_time: datetime,
) -> int:
    """写入单个场景的时序数据到 TDengine。"""
    sub = subtable_name(loop_cfg["tag_name"])
    total_written = 0

    for i in range(0, len(scenario_data), BATCH_SIZE):
        batch = scenario_data[i : i + BATCH_SIZE]
        parts = [f"INSERT INTO {sub} VALUES"]
        for pt in batch:
            # ts: float 秒 → 毫秒时间戳
            ts_ms = int(pt["ts"] * 1000)
            ts_dt = base_time + timedelta(milliseconds=ts_ms)
            ts_str = ts_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            pv = pt["pv"]
            sp = pt["sp"]
            op = pt["op"]
            mode = int(pt["mode"])
            pv_q = int(pt.get("pv_quality", 1))
            parts.append(
                f"('{ts_str}', {pv}, {sp}, {op}, {mode}, NULL, NULL, NULL, {pv_q})"
            )
        sql = " ".join(parts)

        async with semaphore:
            result = await td_execute(client, sql)
            if result is not None:
                total_written += len(batch)

    return total_written


# ============================================================================
# PostgreSQL 操作
# ============================================================================

async def setup_postgresql(clean: bool = False) -> None:
    """在 PostgreSQL 创建测试回路 + Tag + 映射。"""
    async with AsyncSessionLocal() as session:
        if clean:
            # 清理旧测试数据
            test_ids = [cfg["id"] for cfg in TEST_LOOPS]
            await session.execute(
                text("DELETE FROM kpi_snapshot_hourly WHERE loop_id = ANY(:ids)"),
                {"ids": test_ids},
            )
            await session.execute(
                text("DELETE FROM loop_tag_mapping WHERE loop_id = ANY(:ids)"),
                {"ids": test_ids},
            )
            await session.execute(
                text("DELETE FROM tag_registry WHERE tag_name LIKE 'KPI-%'"),
            )
            await session.execute(
                text("DELETE FROM loop_ledger WHERE id = ANY(:ids)"),
                {"ids": test_ids},
            )
            print("  ✓ 旧测试数据已清理")

        # 创建回路
        for cfg in TEST_LOOPS:
            await session.execute(text("""
                INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, status, last_aas_sync_at, created_at, updated_at, created_by)
                VALUES (:id, :tag_name, :desc, :unit_id, :weight, TRUE, 'READY', NOW(), NOW(), NOW(), 'admin')
                ON CONFLICT (id) DO UPDATE SET
                    tag_name = EXCLUDED.tag_name,
                    description = EXCLUDED.description,
                    unit_id = EXCLUDED.unit_id,
                    score_weight = EXCLUDED.score_weight,
                    status = 'READY',
                    is_active = TRUE,
                    updated_at = NOW()
            """), {
                "id": cfg["id"],
                "tag_name": cfg["tag_name"],
                "desc": cfg["description"],
                "unit_id": cfg["unit_id"],
                "weight": cfg["score_weight"],
            })

        # 创建 Tag 注册 + 映射
        for cfg in TEST_LOOPS:
            loop_id = cfg["id"]
            for role in TAG_ROLES:
                tag_id = str(uuid.uuid4())
                tag_name = f"{cfg['tag_name']}.{role}"
                tag_desc = f"{cfg['description']} {role}"

                await session.execute(text("""
                    INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked)
                    VALUES (:id, :tag_name, :desc, :type, 0.0, 'GOOD', NOW(), TRUE)
                    ON CONFLICT (tag_name) DO UPDATE SET
                        tag_description = EXCLUDED.tag_description,
                        tag_type = EXCLUDED.tag_type,
                        is_linked = TRUE
                """), {
                    "id": tag_id,
                    "tag_name": tag_name,
                    "desc": tag_desc,
                    "type": role,
                })

                # 创建映射
                mapping_id = str(uuid.uuid4())
                await session.execute(text("""
                    INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at)
                    VALUES (:id, :loop_id, :tag_id, :role, TRUE, NOW())
                    ON CONFLICT (loop_id, tag_role) DO UPDATE SET
                        tag_id = EXCLUDED.tag_id
                """), {
                    "id": mapping_id,
                    "loop_id": loop_id,
                    "tag_id": tag_id,
                    "role": role,
                })

        await session.commit()
    print(f"  ✓ PostgreSQL 数据创建完成：{len(TEST_LOOPS)} 回路 / {len(TEST_LOOPS) * len(TAG_ROLES)} Tag / {len(TEST_LOOPS) * len(TAG_ROLES)} 映射")


# ============================================================================
# KPI 计算触发
# ============================================================================

async def trigger_kpi_calculation(ts_start: datetime, ts_end: datetime) -> None:
    """触发所有测试回路的 KPI 计算。"""
    from app.core.tdengine import query_trend_data
    from app.models.loop import LoopLedger, LoopTagMapping
    from app.models.metric import MetricConfig
    from app.models.tag import TagRegistry
    from app.tasks.kpi_calc import _calculate_loop_kpi
    from sqlalchemy import select

    print(f"\n{'='*60}")
    print("触发 KPI 计算")
    print(f"{'='*60}")
    print(f"时间窗: {ts_start.isoformat()} ~ {ts_end.isoformat()}")

    async with AsyncSessionLocal() as db:
        # 加载指标配置（键转小写，与 kpi_calc.py 内部查找一致）
        metric_result = await db.execute(select(MetricConfig))
        metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

        # 逐个回路计算
        for cfg in TEST_LOOPS:
            loop_result = await db.execute(
                select(LoopLedger).where(LoopLedger.id == cfg["id"])
            )
            loop = loop_result.scalar_one_or_none()
            if loop is None:
                print(f"  ✗ 回路 {cfg['tag_name']} 不存在")
                continue

            try:
                snap = await _calculate_loop_kpi(
                    db=db,
                    loop=loop,
                    metric_configs=metric_configs,
                    ts_start=ts_start,
                    ts_end=ts_end,
                    query_trend_fn=query_trend_data,
                )
                await db.commit()

                if snap is None:
                    print(f"  ✗ {cfg['tag_name']:15s} → 计算失败（返回 None）")
                elif snap.get("status") == "INCONCLUSIVE":
                    print(f"  ○ {cfg['tag_name']:15s} → {snap['status']}（数据不足）")
                else:
                    score = snap.get("score", 0)
                    print(
                        f"  ✓ {cfg['tag_name']:15s} → score={score:6.2f}  "
                        f"A={snap.get('accuracy_rate', 0):6.2f}  "
                        f"F={snap.get('fast_response_rate', 0):6.2f}  "
                        f"S={snap.get('steady_rate', 0):6.2f}  "
                        f"R={snap.get('effective_auto_rate', 0):6.2f}  "
                        f"osc={snap.get('oscillation_rate', 0):6.2f}  "
                        f"sat={snap.get('saturation_rate', 0):6.2f}"
                    )
            except Exception as exc:
                await db.rollback()
                print(f"  ✗ {cfg['tag_name']:15s} → 计算异常: {exc}")


# ============================================================================
# 主函数
# ============================================================================

async def main(clean: bool = False) -> None:
    """主入口。"""
    if not FIXTURE_PATH.exists():
        print(f"错误: 测试数据文件不存在: {FIXTURE_PATH}")
        print("请先运行: uv run python scripts/generate_kpi_test_data.py")
        return

    # 加载测试数据
    with FIXTURE_PATH.open() as f:
        all_data = json.load(f)

    print(f"已加载测试数据: {len(all_data)} 个场景")

    # 时间窗：数据从 2 小时前开始，持续 2 小时
    # 使用 offset-naive datetime（数据库列为 TIMESTAMP WITHOUT TIME ZONE）
    now = datetime.utcnow()
    base_time = now - timedelta(hours=2)
    ts_start = base_time
    ts_end = now

    # 1. PostgreSQL 设置
    print(f"\n{'='*60}")
    print("1. PostgreSQL 数据创建")
    print(f"{'='*60}")
    await setup_postgresql(clean=clean)

    # 2. TDengine 设置 + 数据写入
    print(f"\n{'='*60}")
    print("2. TDengine 数据写入")
    print(f"{'='*60}")
    async with httpx.AsyncClient(
        auth=(settings.TDENGINE_USER, settings.TDENGINE_PASSWORD),
        timeout=httpx.Timeout(30.0, connect=10.0),
    ) as client:
        await setup_tdengine(client, clean=clean)

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        total_rows = 0
        for cfg in TEST_LOOPS:
            scenario = cfg["scenario"]
            if scenario not in all_data:
                print(f"  ⚠ 场景 {scenario} 不在测试数据中，跳过")
                continue

            scenario_data = all_data[scenario]["data"]
            rows = await write_scenario_data(client, semaphore, cfg, scenario_data, base_time)
            total_rows += rows
            print(f"  ✓ {cfg['tag_name']:15s} ({scenario:15s}) → {rows} 行")

    print(f"\n  总计写入: {total_rows} 行")

    # 3. 触发 KPI 计算
    await trigger_kpi_calculation(ts_start, ts_end)

    print(f"\n{'='*60}")
    print("导入完成！可在前端查看 KPI 计算结果")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KPI 测试数据导入")
    parser.add_argument("--clean", action="store_true", help="清理旧测试数据")
    args = parser.parse_args()
    asyncio.run(main(clean=args.clean))
