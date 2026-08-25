"""工作台 v2.0 总览 Tab 演示数据 seed 脚本。

填充：
1. plant_node.source_node_id（给现有节点分配整数 ID，供 workbench_window_summary 对齐）
2. workbench_window_summary 三窗口 KPI 预计算行（GLOBAL + FACTORY × 2 + UNIT × 4 × 24h/7d/30d）
3. diagnosis_tag + diagnosis_result（触发 mv_diagnosis_pareto 刷新）
4. handling_order（触发 mv_handling_funnel 刷新）
5. 刷新 3 个 MV（CONCURRENTLY）

对齐设计文档 §0.2 演示数据 W1/W3/W4/W7/W8/W14 口径：
- 综合评分 ~84 · 自控率 ~91% · 好值率 ~96% · 平稳率 ~88% · 准确率 ~93% · 快速率 ~82%
- 装置：催化裂化 82.1 / 乙烯 83.5 / 常减压 89.2 / 加氢精制 90.5
- 异常类型：仪表故障 / 控制策略 / 工艺扰动 / 设备故障 / 标定漂移
- 根因：振荡 / 过饱和 / 响应滞后 / 非线性 / 传感器故障
- 处置漏斗：pending 6 / executing 4 / verifying 3 / closed 11 / breached 1

用法：
    cd backend && uv run python scripts/seed_workbench_demo.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.core.db import AsyncSessionLocal

# ---------------------------------------------------------------------------
# 1. plant_node source_node_id 映射（给现有节点分配稳定整数 ID）
# ---------------------------------------------------------------------------

# 按现有 plant_node 结构，为每个节点分配 source_node_id
# FACTORY → 100/200，AREA → 1000/2000，UNIT → 10000+
SOURCE_ID_MAP: dict[str, int] = {
    # FACTORY
    "35af8328-e72d-414f-bfc9-b0c0caad71ee": 100,  # EO 工厂
    "2181a13b-45eb-4306-9d19-c143f1b3ed11": 200,  # 致联工厂
    # AREA
    "ff26e255-dcb9-45b4-8b79-a0d1c8c51fae": 1000,  # EO 装置
    "59762a31-54a5-4bd1-9a5e-5c06066e13d9": 2000,  # EU装置
    "23588704-79e2-4c87-8b9e-672f24a14939": 3000,  # MTO装置
    # UNIT
    "8e4e3c0e-7cb3-49ad-b0d9-2b9b46dadb76": 10000,  # 精馏单元
    "81a6adfd-18de-44f5-b6ea-09d6933479c7": 10001,  # 反应单元
    "ad6a0993-0e83-4645-87f8-edecd2c85356": 10002,  # 急冷分离单元
    "e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b": 10003,  # 精馏塔单元
    "3353a2b2-2d4f-4907-9964-fb2aac837352": 10004,  # 脱甲烷精馏单元
    "07f43143-4f47-4f31-869c-bcdae8ecd865": 10005,  # 醛化反应单元
}

# 装置评分数据（对齐原型截图）
FACTORY_SCORES: dict[int, dict[str, Any]] = {
    100: {  # EO 工厂 → 催化裂化（装置风险 #1）
        "name": "催化裂化",
        "score": 82.1,
        "status": "FAIR",
        "good_value_rate": 0.945,
        "auto_mode_rate": 0.887,
        "effective_auto_rate": 0.852,
        "steady_rate": 0.835,
        "accuracy_rate": 0.912,
        "fast_rate": 0.798,
        "delta": -2.6,
    },
    200: {  # 致联工厂 → 乙烯（装置风险 #2）
        "name": "乙烯",
        "score": 83.5,
        "status": "FAIR",
        "good_value_rate": 0.952,
        "auto_mode_rate": 0.895,
        "effective_auto_rate": 0.861,
        "steady_rate": 0.850,
        "accuracy_rate": 0.925,
        "fast_rate": 0.805,
        "delta": -1.2,
    },
}

# 装置评分数据（按 area source_node_id）
AREA_SCORES: dict[int, dict[str, Any]] = {
    1000: {  # EO 装置（隶属 EO 工厂）
        "score": 83.8,
        "status": "FAIR",
        "loop_count": 12,
        "good_value_rate": 0.955,
        "auto_mode_rate": 0.890,
        "effective_auto_rate": 0.861,
        "steady_rate": 0.845,
        "accuracy_rate": 0.918,
        "fast_rate": 0.808,
        "delta": -1.8,
    },
    2000: {  # EU 装置（隶属 EO 工厂）
        "score": 81.5,
        "status": "FAIR",
        "loop_count": 5,
        "good_value_rate": 0.928,
        "auto_mode_rate": 0.872,
        "effective_auto_rate": 0.838,
        "steady_rate": 0.820,
        "accuracy_rate": 0.905,
        "fast_rate": 0.780,
        "delta": -3.1,
    },
    3000: {  # MTO 装置（隶属 EO 工厂）
        "score": 85.2,
        "status": "GOOD",
        "loop_count": 8,
        "good_value_rate": 0.961,
        "auto_mode_rate": 0.905,
        "effective_auto_rate": 0.872,
        "steady_rate": 0.858,
        "accuracy_rate": 0.925,
        "fast_rate": 0.832,
        "delta": +0.5,
    },
}

# 单元评分数据（按 unit_id）
UNIT_SCORES: dict[int, dict[str, Any]] = {
    10000: {
        "score": 88.5,
        "status": "GOOD",
        "metrics": {
            "good_value_rate": 0.97,
            "auto_mode_rate": 0.92,
            "steady_rate": 0.90,
            "accuracy_rate": 0.94,
            "fast_rate": 0.85,
        },
    },
    10001: {
        "score": 86.2,
        "status": "GOOD",
        "metrics": {
            "good_value_rate": 0.95,
            "auto_mode_rate": 0.88,
            "steady_rate": 0.86,
            "accuracy_rate": 0.91,
            "fast_rate": 0.83,
        },
    },
    10002: {
        "score": 84.8,
        "status": "FAIR",
        "metrics": {
            "good_value_rate": 0.93,
            "auto_mode_rate": 0.85,
            "steady_rate": 0.82,
            "accuracy_rate": 0.89,
            "fast_rate": 0.80,
        },
    },
    10003: {
        "score": 89.0,
        "status": "GOOD",
        "metrics": {
            "good_value_rate": 0.96,
            "auto_mode_rate": 0.93,
            "steady_rate": 0.91,
            "accuracy_rate": 0.95,
            "fast_rate": 0.86,
        },
    },
    10004: {
        "score": 82.5,
        "status": "FAIR",
        "metrics": {
            "good_value_rate": 0.91,
            "auto_mode_rate": 0.82,
            "steady_rate": 0.80,
            "accuracy_rate": 0.87,
            "fast_rate": 0.78,
        },
    },
    10005: {
        "score": 87.3,
        "status": "GOOD",
        "metrics": {
            "good_value_rate": 0.95,
            "auto_mode_rate": 0.90,
            "steady_rate": 0.88,
            "accuracy_rate": 0.92,
            "fast_rate": 0.84,
        },
    },
}

# GLOBAL 汇总
GLOBAL_SCORE: dict[str, Any] = {
    "score": 84.2,
    "status": "FAIR",
    "loop_count": 34,
    "good_value_rate": 0.968,
    "auto_mode_rate": 0.912,
    "effective_auto_rate": 0.895,
    "steady_rate": 0.882,
    "accuracy_rate": 0.931,
    "fast_rate": 0.823,
}


def _make_score_trend(base: float, points: int = 24, variance: float = 0.8) -> list[dict[str, Any]]:
    """生成模拟 sparkline（围绕 base 值的平稳趋势，末值=base）。

    设计：起点 = base + 小偏移（±2 内），末点 = base；
    中间随机游走方差 0.8，保证环比 delta 在 ±2 内（对齐原型 -2.6 量级）。
    """
    import random

    random.seed(42)  # 确定性
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    # 起点偏移：±1.5 ~ ±2.5，模拟环比变化
    start_offset = random.choice([-1, 1]) * random.uniform(1.5, 2.5)
    start_v = max(60, min(95, base + start_offset))
    end_v = base
    pts: list[dict[str, Any]] = []
    for i in range(points):
        t = now - timedelta(hours=points - i)
        # 线性插值从 start_v 到 end_v + 小噪声
        frac = i / max(points - 1, 1)
        v = start_v + (end_v - start_v) * frac
        v += random.uniform(-variance, variance)
        v = max(60, min(95, v))
        pts.append({"t": t.isoformat(), "v": round(v, 1)})
    # 确保末值精确等于 base
    pts[-1]["v"] = round(base, 1)
    return pts


def _make_flags(count: int = 2) -> list[dict[str, Any]]:
    """生成模拟 flags。"""
    flag_kinds = ["dip", "spike", "deterioration", "jump"]
    severities = ["WARN", "ERROR", "CRITICAL"]
    now = datetime.now(UTC)
    flags: list[dict[str, Any]] = []
    for i in range(count):
        flags.append(
            {
                "kind": flag_kinds[i % len(flag_kinds)],
                "severity": severities[i % len(severities)],
                "t": (now - timedelta(hours=i * 4)).isoformat(),
                "desc": f"24h 内标注点 #{i + 1}",
            }
        )
    return flags


async def seed_source_node_ids(db) -> None:
    """为现有 plant_node 填充 source_node_id。"""
    for node_id, source_id in SOURCE_ID_MAP.items():
        await db.execute(
            text("UPDATE plant_node SET source_node_id = :sid WHERE id = :nid"),
            {"sid": source_id, "nid": node_id},
        )
    await db.commit()
    print(f"✅ 已更新 {len(SOURCE_ID_MAP)} 个 plant_node source_node_id")


async def seed_workbench_window_summary(db) -> None:
    """填充 workbench_window_summary 三窗口数据。"""
    now = datetime.now(UTC)
    windows = {
        "24h": {"start": now - timedelta(hours=24), "pts": 24, "offset": 0},
        "7d": {"start": now - timedelta(days=7), "pts": 7, "offset": -0.6},
        "30d": {"start": now - timedelta(days=30), "pts": 15, "offset": 1.0},
    }

    # 清除旧数据
    await db.execute(text("DELETE FROM workbench_window_summary"))

    rows = []
    for win_key, win_cfg in windows.items():
        # GLOBAL
        for scope_type, scope_id, score_data, loop_count in [
            ("GLOBAL", 0, GLOBAL_SCORE, GLOBAL_SCORE["loop_count"]),
            ("FACTORY", 100, FACTORY_SCORES[100], 18),
            ("FACTORY", 200, FACTORY_SCORES[200], 16),
            ("AREA", 1000, AREA_SCORES[1000], AREA_SCORES[1000]["loop_count"]),
            ("AREA", 2000, AREA_SCORES[2000], AREA_SCORES[2000]["loop_count"]),
            ("AREA", 3000, AREA_SCORES[3000], AREA_SCORES[3000]["loop_count"]),
            ("UNIT", 10000, UNIT_SCORES[10000], 8),
            ("UNIT", 10001, UNIT_SCORES[10001], 6),
            ("UNIT", 10002, UNIT_SCORES[10002], 5),
            ("UNIT", 10003, UNIT_SCORES[10003], 7),
            ("UNIT", 10004, UNIT_SCORES[10004], 4),
            ("UNIT", 10005, UNIT_SCORES[10005], 4),
        ]:
            win_start = win_cfg["start"]
            win_end = now
            # 窗口级评分偏移（7d 略低、30d 略高，使切换窗口可见变化）
            score = round(score_data["score"] + win_cfg["offset"], 1)
            metrics = score_data.get("metrics", score_data)
            metrics_block = {
                "good_value_rate": score_data.get("good_value_rate", 0.95),
                "auto_mode_rate": score_data.get("auto_mode_rate", 0.90),
                "effective_auto_rate": score_data.get("effective_auto_rate", 0.87),
                "steady_rate": score_data.get(
                    "steady_rate",
                    metrics.get("steady_rate", 0.85) if isinstance(metrics, dict) else 0.85,
                ),
                "accuracy_rate": score_data.get(
                    "accuracy_rate",
                    metrics.get("accuracy_rate", 0.92) if isinstance(metrics, dict) else 0.92,
                ),
                "fast_rate": score_data.get(
                    "fast_rate",
                    metrics.get("fast_rate", 0.82) if isinstance(metrics, dict) else 0.82,
                ),
            }
            score_trend = _make_score_trend(score, win_cfg["pts"])
            flags = _make_flags(1 if win_key == "24h" else 0)

            rows.append(
                {
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "window": win_key,
                    "window_start": win_start,
                    "window_end": win_end,
                    "score": score,
                    "status": score_data.get("status", "FAIR"),
                    "loop_count": loop_count,
                    **metrics_block,
                    "oscillation_rate": 0.03,
                    "saturation_rate": 0.02,
                    "instrument_fault_rate": 0.01,
                    "score_trend": json.dumps(score_trend),
                    "flags": json.dumps(flags),
                    "snapshot_at": now,
                }
            )

    # 批量插入
    batch_sql = text("""
        INSERT INTO workbench_window_summary
        (scope_type, scope_id, "window", window_start, window_end, score, status,
         loop_count, good_value_rate, auto_mode_rate, effective_auto_rate,
         steady_rate, accuracy_rate, fast_rate, oscillation_rate,
         saturation_rate, instrument_fault_rate, score_trend, flags,
         snapshot_at)
        VALUES
        (:scope_type, :scope_id, :window, :window_start, :window_end, :score,
         :status, :loop_count, :good_value_rate, :auto_mode_rate,
         :effective_auto_rate, :steady_rate, :accuracy_rate, :fast_rate,
         :oscillation_rate, :saturation_rate, :instrument_fault_rate,
         CAST(:score_trend AS jsonb), CAST(:flags AS jsonb), :snapshot_at)
    """)
    for row in rows:
        await db.execute(batch_sql, row)
    await db.commit()
    print(f"✅ 已插入 {len(rows)} 行 workbench_window_summary")


async def seed_diagnosis_tags(db) -> list[UUID]:
    """填充 diagnosis_tag + diagnosis_result（触发 mv_diagnosis_pareto）。"""
    # 清理旧数据（幂等）
    await db.execute(
        text("DELETE FROM diagnosis_result WHERE id::text LIKE '00000000-0000-0000-0000-%'")
    )
    await db.execute(
        text("DELETE FROM diagnosis_tag WHERE id::text LIKE '00000000-0000-0000-0000-%'")
    )
    # 查现有 loop_ledger id
    loops = await db.execute(text("SELECT id FROM loop_ledger LIMIT 10"))
    loop_ids = [r[0] for r in loops]
    if not loop_ids:
        print("⚠️  无 loop_ledger，跳过 diagnosis_tag")
        return []

    now = datetime.now(UTC).replace(tzinfo=None)
    tags_data = [
        # (loop_idx, tag_code, tag_name, severity, category, disposition)
        (0, "OSC", "回路振荡", "CRITICAL", "仪表故障", "UNADDRESSED"),
        (0, "OSC", "回路振荡", "WARN", "仪表故障", "UNADDRESSED"),
        (1, "SAT", "阀位饱和", "ERROR", "控制策略", "UNADDRESSED"),
        (2, "LAG", "响应滞后", "WARN", "工艺扰动", "UNADDRESSED"),
        (2, "LAG", "响应滞后", "INFO", "工艺扰动", "ACK_REVIEWED"),
        (3, "NONLIN", "非线性", "ERROR", "标定漂移", "UNADDRESSED"),
        (4, "SENSOR", "传感器故障", "CRITICAL", "设备故障", "UNADDRESSED"),
        (5, "OSC", "回路振荡", "ERROR", "仪表故障", "CONVERTED"),
        (6, "DETUNE", "整定偏离", "WARN", "控制策略", "UNADDRESSED"),
        (7, "OSC", "回路振荡", "WARN", "仪表故障", "UNADDRESSED"),
        (8, "LAG", "响应滞后", "ERROR", "工艺扰动", "UNADDRESSED"),
        (9, "SAT", "阀位饱和", "WARN", "控制策略", "ACK_REVIEWED"),
    ]

    tag_ids: list[UUID] = []
    for idx, (loop_idx, code, name, severity, category, disposition) in enumerate(tags_data):
        loop_id = loop_ids[loop_idx % len(loop_ids)]
        tag_id = UUID(f"00000000-0000-0000-0000-{1000 + idx:012d}")
        tag_ids.append(tag_id)

        # 插入 diagnosis_tag
        await db.execute(
            text("""
                INSERT INTO diagnosis_tag
                (id, loop_id, tag_code, tag_name, severity, source_metric,
                 triggered_at, status, disposition_state, sla_deadline_at, sla_stage)
                VALUES
                (:id, :loop_id, :tag_code, :tag_name, :severity, 'workbench_precalc',
                 :triggered_at, :status, :disposition, :sla_deadline, :sla_stage)
            """),
            {
                "id": tag_id,
                "loop_id": loop_id,
                "tag_code": code,
                "tag_name": name,
                "severity": severity,
                "triggered_at": now - timedelta(hours=1 + len(tag_ids) // 2),
                "status": "ACTIVE" if disposition in ("UNADDRESSED", "CONVERTED") else "RESOLVED",
                "disposition": disposition,
                "sla_deadline": now + timedelta(hours=24),
                "sla_stage": "WARN" if disposition == "UNADDRESSED" else "NONE",
            },
        )

        # 插入 diagnosis_result（提供 recommended_category 供 MV 聚合）
        await db.execute(
            text("""
                INSERT INTO diagnosis_result
                (loop_id, diag_label, confidence, recommended_category,
                 diagnosed_at, evidence_summary, algorithm_version)
                VALUES (:loop_id, :label, 0.85, :category, :diagnosed_at, :summary, 'v2.0-demo')
            """),
            {
                "loop_id": loop_id,
                "label": code,
                "category": category,
                "diagnosed_at": now,
                "summary": f"{name}：{category}方向，严重度 {severity}",
            },
        )

    await db.commit()
    print(f"✅ 已插入 {len(tags_data)} 条 diagnosis_tag + {len(tags_data)} 条 diagnosis_result")
    return tag_ids


async def seed_handling_orders(db) -> None:
    """填充 handling_order（触发 mv_handling_funnel）。"""
    # 清理旧数据（幂等）
    await db.execute(
        text("DELETE FROM handling_order WHERE id::text LIKE '00000000-0000-0000-0000-%'")
    )
    loops = await db.execute(text("SELECT id FROM loop_ledger LIMIT 10"))
    loop_ids = [r[0] for r in loops]
    if not loop_ids:
        print("⚠️  无 loop_ledger，跳过 handling_order")
        return

    now = datetime.now(UTC).replace(tzinfo=None)
    orders = [
        # (loop_idx, status, sla_stage, scope_type, scope_id)
        # GLOBAL 范围（4 条，驱动 mv_handling_funnel GLOBAL 行）
        (0, "PENDING", "WARN", "GLOBAL", 0),
        (1, "EXECUTING", None, "GLOBAL", 0),
        (2, "VERIFYING", None, "GLOBAL", 0),
        (3, "CLOSED", None, "GLOBAL", 0),
        (4, "REOPENED", None, "GLOBAL", 0),
        # FACTORY 范围
        (0, "PENDING", "WARN", "FACTORY", 100),
        (1, "PENDING", "BREACH", "FACTORY", 100),
        (2, "PENDING", None, "FACTORY", 200),
        (3, "EXECUTING", "WARN", "FACTORY", 100),
        (4, "EXECUTING", None, "FACTORY", 200),
        (5, "EXECUTING", None, "UNIT", 10001),
        (6, "VERIFYING", None, "FACTORY", 100),
        (7, "VERIFYING", None, "FACTORY", 200),
        (8, "CLOSED", None, "FACTORY", 100),
        (9, "CLOSED", None, "FACTORY", 200),
        (0, "CLOSED", None, "FACTORY", 100),
        (1, "CLOSED", None, "UNIT", 10002),
        (2, "CLOSED", None, "FACTORY", 100),
        (3, "CLOSED", None, "FACTORY", 200),
        (4, "CLOSED", None, "UNIT", 10003),
        (5, "CLOSED", None, "FACTORY", 200),
        (6, "REOPENED", None, "FACTORY", 100),
        (7, "CLOSED", None, "FACTORY", 100),
        (8, "CLOSED", None, "FACTORY", 200),
        (9, "CLOSED", None, "UNIT", 10004),
    ]

    for i, (loop_idx, status, sla_stage, scope_type, scope_id) in enumerate(orders):
        loop_id = loop_ids[loop_idx % len(loop_ids)]
        created = now - timedelta(hours=i * 2)
        verified = created + timedelta(hours=1.5) if status == "CLOSED" else None
        action_type = "TUNING" if i % 3 == 0 else "PROCESS"
        source = "DIAGNOSIS" if i % 2 == 0 else "MANUAL"

        await db.execute(
            text("""
                INSERT INTO handling_order
                (id, order_no, loop_id, source, title, action_type, status,
                 created_at, updated_at, verified_at, sla_deadline_at, sla_stage,
                 scope_type, scope_id, reopen_count)
                VALUES
                (:id, :order_no, :loop_id, :source, :title, :action_type,
                 :status, :created, :updated, :verified, :sla_deadline, :sla_stage,
                 :scope_type, :scope_id, 0)
            """),
            {
                "id": UUID(f"00000000-0000-0000-0000-{2000 + i:012d}"),
                "order_no": f"HO-{2026000 + i:05d}",
                "loop_id": loop_id,
                "source": source,
                "title": f"回路 {i + 1} 处置：{status}",
                "action_type": action_type,
                "status": status,
                "created": created,
                "updated": now,
                "verified": verified,
                "sla_deadline": (
                    now + timedelta(hours=24)
                    if status in ("PENDING", "EXECUTING", "VERIFYING")
                    else None
                ),
                "sla_stage": sla_stage or "NONE",
                "scope_type": scope_type,
                "scope_id": scope_id,
            },
        )

    await db.commit()
    print(f"✅ 已插入 {len(orders)} 条 handling_order")


async def refresh_mv(db) -> None:
    """刷新 3 个 MV。"""
    for mv in ("mv_diagnosis_pareto", "mv_handling_funnel", "mv_staff_workload"):
        try:
            await db.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}"))
            print(f"✅ 已刷新 {mv}")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  刷新 {mv} 失败: {exc}")
    await db.commit()


async def main() -> None:
    print("🌱 开始填充工作台 v2.0 演示数据...")
    async with AsyncSessionLocal() as db:
        await seed_source_node_ids(db)
        await seed_workbench_window_summary(db)
        await seed_diagnosis_tags(db)
        await seed_handling_orders(db)
        await refresh_mv(db)
    print("✅ 演示数据填充完成！可刷新总览 Tab 查看。")


if __name__ == "__main__":
    asyncio.run(main())
