"""工作台 v2.0 总览 Tab 演示数据 seed 脚本。

填充：
1. plant_node.source_node_id（给现有节点分配整数 ID，供 workbench_window_summary 对齐）
2. workbench_window_summary 三窗口 KPI 预计算行（GLOBAL + FACTORY × 2 + UNIT × 4 × 24h/7d/30d）
3. diagnosis_tag + diagnosis_result（触发 mv_diagnosis_pareto 刷新）
4. diagnosis_run（14 号方案阶段 A1：工作台诊断迁 v2 引擎数据源；独立 00000000-0000-0001- 清理段）
5. handling_order（触发 mv_handling_funnel 刷新）
6. tuning_batch + tuning_record + 前置工单（G-整定 W11/W12/W13：批次/队列/散点）
7. 刷新 3 个 MV（CONCURRENTLY）

对齐设计文档 §0.2 演示数据 W1/W3/W4/W7/W8/W14 口径：
- 综合评分 ~84 · 自控率 ~91% · 好值率 ~96% · 平稳率 ~88% · 准确率 ~93% · 快速率 ~82%
- 装置：催化裂化 82.1 / 乙烯 83.5 / 常减压 89.2 / 加氢精制 90.5
- 异常类型：仪表故障 / 控制策略 / 工艺扰动 / 设备故障 / 标定漂移
- 根因：振荡 / 过饱和 / 响应滞后 / 非线性 / 传感器故障
- 处置漏斗：pending 6 / executing 4 / verifying 3 / closed 11 / breached 1

G-整定演示（原型 BATCHES/PENDING_TUNE/SCATTER 口径）：
- 批次：ZD-2026-0142（COMPLETED，6 回路 71→88）/ 0143（COMPLETED，3 回路 74→82）
  / 0144（PENDING 排队）/ 0145（前置 CL-2026-0819 未闭合 → 动态 BLOCKED）
  / 0141（CANCELLED 回退，66→62 负 Δ 点）
- 待整定队列 6 条：1 条同回路前置工单阻塞（红·高优先）+ 2 条批次阻塞 + 3 条可操作
- 散点 11 点：批次固化快照 10 点（含 1 负 Δ）+ TUNING 工单 kpi 前后 2 点

用法：
    cd backend && uv run python scripts/seed_workbench_demo.py
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.services.diagnosis_v2_compat import CATEGORY_LABELS_V2

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
    "loop_count": 27,  # 对齐 loop_ledger 实际回路数
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


def _make_distribution(loop_count: int, score: float) -> dict[str, Any]:
    """生成 G-评估 trend 块分布数据（level_dist / mode_dist / data_quality / metric_slopes）。

    对齐原型 #tab-eval Row3 右侧甜甜圈 + 数据质量条 + 分项斜率口径；
    按 loop_count 等比缩放，保证子 scope 汇总 ≈ GLOBAL。
    """
    n = max(loop_count, 1)
    # 等级分布：优≥90 / 良 75–90 / 中 60–75 / 差<60 / 不可评（对齐原型 9/16/5/2/2 = 34）
    level_dist = [
        {"label": "优（≥90）", "count": round(n * 0.265), "color": "#2E7D32", "stripe": False},
        {"label": "良（75–90）", "count": round(n * 0.470), "color": "#7CB342", "stripe": False},
        {"label": "中（60–75）", "count": round(n * 0.147), "color": "#F59E0B", "stripe": False},
        {"label": "差（<60）", "count": round(n * 0.059), "color": "#D93025", "stripe": False},
        {"label": "不可评", "count": round(n * 0.059), "color": "#C9D6E8", "stripe": True},
    ]
    # 控制模式分布：自动/串级/远程/手动（对齐原型 29/1/1/3 = 34）
    mode_dist = [
        {"label": "自动", "count": round(n * 0.853), "color": "#2563EB"},
        {"label": "串级", "count": round(n * 0.029), "color": "#7C3AED"},
        {"label": "远程", "count": round(n * 0.029), "color": "#94A3B8"},
        {"label": "手动", "count": round(n * 0.088), "color": "#F59E0B"},
    ]
    # 数据质量（对齐原型 33/1/0/0 = 34）
    data_quality = [
        {"label": "数据完整", "count": max(n - 1, 0), "level": "green"},
        {"label": "采样异常", "count": 1 if n >= 2 else 0, "level": "orange"},
        {"label": "通讯中断", "count": 0, "level": "gray"},
        {"label": "组态未同步", "count": 0, "level": "gray"},
    ]
    # 分项近 24h 变化量（对齐原型 slope-row：恶化居上红、改善居下绿、零轴居中）
    metric_slopes = [
        {"metric": "仪表故障率", "delta": 0.9, "direction": "bad"},
        {"metric": "准确率", "delta": -0.3, "direction": "bad"},
        {"metric": "快速率", "delta": 2.0, "direction": "good"},
        {"metric": "平稳率", "delta": 1.4, "direction": "good"},
        {"metric": "有效自控", "delta": 0.6, "direction": "good"},
        {"metric": "好值率", "delta": 0.5, "direction": "good"},
    ]
    return {
        "level_dist": level_dist,
        "mode_dist": mode_dist,
        "data_quality": data_quality,
        "metric_slopes": metric_slopes,
    }


async def seed_source_node_ids(db) -> None:
    """为现有 plant_node 填充 source_node_id。"""
    for node_id, source_id in SOURCE_ID_MAP.items():
        await db.execute(
            text("UPDATE plant_node SET source_node_id = :sid WHERE id = :nid"),
            {"sid": source_id, "nid": node_id},
        )
    await db.commit()
    print(f"✅ 已更新 {len(SOURCE_ID_MAP)} 个 plant_node source_node_id")


async def _fetch_actual_loop_counts(db) -> dict[int, int]:
    """从 loop_ledger JOIN plant_node 查各层级实际回路数。

    返回 {source_node_id: count} 字典，key=0 表示全厂总计。
    """
    sql = text("""
        WITH loop_tree AS (
            SELECT l.is_active,
                   u.source_node_id AS unit_src,
                   a.source_node_id AS area_src,
                   f.source_node_id AS factory_src
            FROM loop_ledger l
            LEFT JOIN plant_node u ON l.unit_id = u.id
            LEFT JOIN plant_node a ON u.parent_id = a.id
            LEFT JOIN plant_node f ON a.parent_id = f.id
            WHERE l.is_active = true
        )
        SELECT 0 AS scope_id, count(*) AS cnt FROM loop_tree
        UNION ALL
        SELECT factory_src, count(*) FROM loop_tree
            WHERE factory_src IS NOT NULL GROUP BY factory_src
        UNION ALL
        SELECT area_src, count(*) FROM loop_tree
            WHERE area_src IS NOT NULL GROUP BY area_src
        UNION ALL
        SELECT unit_src, count(*) FROM loop_tree
            WHERE unit_src IS NOT NULL GROUP BY unit_src
    """)
    result = await db.execute(sql)
    counts: dict[int, int] = {}
    for row in result:
        counts[row.scope_id or 0] = row.cnt
    return counts


async def seed_workbench_window_summary(db) -> None:
    """填充 workbench_window_summary 三窗口数据。"""
    now = datetime.now(UTC)
    windows = {
        "24h": {"start": now - timedelta(hours=24), "pts": 24, "offset": 0},
        "7d": {"start": now - timedelta(days=7), "pts": 7, "offset": -0.6},
        "30d": {"start": now - timedelta(days=30), "pts": 15, "offset": 1.0},
    }

    # 动态查询 loop_ledger 各层级的实际回路数（对齐真实数据，不用写死值）
    actual_counts = await _fetch_actual_loop_counts(db)
    print(
        f"  实际回路数：GLOBAL={actual_counts.get(0, 0)}, "
        f"FACTORY 100={actual_counts.get(100, 0)}, "
        f"AREA 1000={actual_counts.get(1000, 0)}"
    )

    # 清除旧数据
    await db.execute(text("DELETE FROM workbench_window_summary"))

    rows = []
    for win_key, win_cfg in windows.items():
        # GLOBAL
        for scope_type, scope_id, score_data in [
            ("GLOBAL", 0, GLOBAL_SCORE),
            ("FACTORY", 100, FACTORY_SCORES[100]),
            ("FACTORY", 200, FACTORY_SCORES[200]),
            ("AREA", 1000, AREA_SCORES[1000]),
            ("AREA", 2000, AREA_SCORES[2000]),
            ("AREA", 3000, AREA_SCORES[3000]),
            ("UNIT", 10000, UNIT_SCORES[10000]),
            ("UNIT", 10001, UNIT_SCORES[10001]),
            ("UNIT", 10002, UNIT_SCORES[10002]),
            ("UNIT", 10003, UNIT_SCORES[10003]),
            ("UNIT", 10004, UNIT_SCORES[10004]),
            ("UNIT", 10005, UNIT_SCORES[10005]),
        ]:
            # 优先使用 loop_ledger 实际回路数；无回路节点保留演示评分但 loop_count=0
            loop_count = actual_counts.get(scope_id, score_data.get("loop_count", 0))
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
            distribution = _make_distribution(loop_count, score)

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
                    "distribution": json.dumps(distribution),
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
         distribution, snapshot_at)
        VALUES
        (:scope_type, :scope_id, :window, :window_start, :window_end, :score,
         :status, :loop_count, :good_value_rate, :auto_mode_rate,
         :effective_auto_rate, :steady_rate, :accuracy_rate, :fast_rate,
         :oscillation_rate, :saturation_rate, :instrument_fault_rate,
         CAST(:score_trend AS jsonb), CAST(:flags AS jsonb),
         CAST(:distribution AS jsonb), :snapshot_at)
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


# ---------------------------------------------------------------------------
# 3.5 diagnosis_run 种子（14 号方案阶段 A1：工作台诊断迁 v2 引擎数据源）
# ---------------------------------------------------------------------------

#: 与编排器 diagnosis_orchestrator.MVP_DIAG_VERSION 同值（algorithm_version 口径一致）
_DIAG_ALGO_VERSION = "MVP_DIAG_V2_v1.0"

#: 症状标签 → 算子族（fusion_results.family 值域，取自 OPERATOR_REGISTRY）
_SYMPTOM_FAMILY: dict[str, str] = {
    "OSCILLATION": "oscillation",
    "VALVE_STICTION": "stiction",
    "QUALITY_ABNORMAL": "sensor",
    "LINK_ABNORMAL": "link",
    "OVERAGGRESSIVE": "tuning",
    "OVERCONSERVATIVE": "tuning",
    "EXTERNAL_DISTURBANCE": "disturbance",
    "OUTPUT_SATURATION": "saturation",
}

#: 症状标签 → 族内算子名（operator_results 键域，取自 OPERATOR_REGISTRY）
_SYMPTOM_OPS: dict[str, tuple[str, ...]] = {
    "OSCILLATION": ("oscillation_fft", "oscillation_iae"),
    "VALVE_STICTION": ("stiction_ellipse", "stiction_choudhury"),
    "QUALITY_ABNORMAL": ("sensor_fault",),
    "LINK_ABNORMAL": ("quality_code_rules",),
    "OVERAGGRESSIVE": ("step_response_overshoot",),
    "OVERCONSERVATIVE": ("slow_response",),
    "EXTERNAL_DISTURBANCE": ("disturbance_burst",),
    "OUTPUT_SATURATION": ("output_saturation",),
}

#: 算子特征模板：算子名 → [(特征名, 检出值, 阈值, 判定文案)]
#: （特征键名对齐各算子 outputs_schema；未命中时取低值版本）
_OP_FEATS: dict[str, list[tuple[str, float, float, str]]] = {
    "oscillation_fft": [
        ("peak_energy_ratio", 0.58, 0.35, "主峰能量占比 58% 超阈值 35%"),
        ("snr", 3.2, 2.0, "主峰信噪比 3.2 超阈值 2.0"),
    ],
    "oscillation_iae": [
        ("half_period_regularity", 0.82, 0.7, "半周期相似率 82% 超阈值 70%"),
        ("osc_count", 42.0, 6.0, "完整振荡周期数 42 超门槛 6"),
    ],
    "stiction_ellipse": [
        ("stiction_index", 0.62, 0.4, "椭圆拟合粘滞指数 0.62 超阈值 0.4"),
    ],
    "stiction_choudhury": [
        ("ngi", 0.55, 0.3, "非高斯指数 NGI 0.55 超阈值 0.3"),
        ("nli", 0.61, 0.5, "非线性指数 NLI 0.61 超阈值 0.5"),
    ],
    "step_response_overshoot": [
        ("overshoot", 0.38, 0.2, "阶跃超调 38% 超阈值 20%"),
        ("decay_ratio", 0.32, 0.25, "衰减比 0.32 超阈值 0.25"),
    ],
    "slow_response": [
        ("tau_ratio", 2.4, 2.0, "实际/期望时间常数比 2.4 超阈值 2.0"),
    ],
    "disturbance_burst": [
        ("shift_frequency", 3.2, 2.0, "偏差确认突变 3.2 次/h 超阈值 2 次/h"),
    ],
    "sensor_fault": [
        ("frozen_segment_ratio", 0.083, 0.05, "冻结段占比 8.3% 超阈值 5%"),
    ],
    "quality_code_rules": [
        ("max_consecutive_bad", 1420.0, 600.0, "最长连续 Bad 1420 点超阈值 600"),
        ("bad_rate", 0.312, 0.2, "Bad 质量码占比 31.2% 超阈值 20%"),
    ],
    "output_saturation": [
        ("saturation_rate", 0.86, 0.2, "OP 贴限占比 86% 超阈值 20%"),
    ],
}

#: 12 条种子 run 规格（覆盖矩阵：11 回路 × 窗口终点近 24h/7d/30d × 窗长 24h/7d
#: × 6 类 primary_category × severity 三档 × review 两态 × trigger 两类）
#: hits = 症状标签 → 族内命中算子置信度列表（D-S 融合后即 symptom_tags 置信度）
_DIAG_RUN_SPECS: list[dict[str, Any]] = [
    # 近 24h 检出（D3 流量态口径：总览 alarm_count 数据源）
    {
        "loop_idx": 0,
        "win_end_ago_h": 2,
        "win_h": 24,
        "trigger": "MANUAL",
        "category": "VALVE",
        "severity": "HIGH",
        "primary_conf": 0.963,
        "score_avg": 36.8,
        "hits": {"VALVE_STICTION": [0.88, 0.78], "OSCILLATION": [0.72], "OVERAGGRESSIVE": [0.58]},
        "basis": ["粘滞算子命中：椭圆拟合、Choudhury（融合置信 0.96）", "椭圆拟合粘滞指数 0.62"],
        "rec": "检查阀门执行机构，清洁或更换阀门填料",
        "direction": "检修/更换配件",
        # 阀门污染链（VALVE→TUNING）：过激候选降级待复核
        "pending": [
            {
                "category": "TUNING",
                "confidence": 0.58,
                "basis": ["阶跃响应过激：超调 38%、衰减比 0.32"],
            }
        ],
        "secondary": [],
    },
    {
        "loop_idx": 1,
        "win_end_ago_h": 6,
        "win_h": 24,
        "trigger": "MANUAL",
        "category": "TUNING",
        "severity": "MEDIUM",
        "primary_conf": 0.75,
        "score_avg": 58.2,
        "hits": {"OVERAGGRESSIVE": [0.75], "OSCILLATION": [0.52]},
        "basis": ["阶跃响应过激：超调 38%、衰减比 0.32"],
        "rec": "按证据方向重新整定：过激减小 Kp/增大 Ti，保守增大 Kp/减小 Ti（参考 IMC）",
        "direction": "重新整定参数",
        "pending": [],
        "secondary": [],
    },
    {
        "loop_idx": 4,
        "win_end_ago_h": 12,
        "win_h": 24,
        "trigger": "SCHEDULED",
        "category": "INSTRUMENT",
        "severity": "MEDIUM",
        "primary_conf": 0.85,
        "score_avg": 72.4,
        "hits": {"QUALITY_ABNORMAL": [0.85], "OSCILLATION": [0.51]},
        "basis": ["传感器故障子类型 drift", "冻结段占比 8.3%"],
        "rec": "检查校验变送器/仪表（修复后复诊确认下游结论）",
        "direction": "校验/维护",
        # 仪表污染链（INSTRUMENT→PROCESS）：纯振荡推断候选降级待复核
        "pending": [
            {
                "category": "PROCESS",
                "confidence": 0.5,
                "basis": ["存在振荡且无粘滞/过激证据，疑外部传入或回路耦合"],
            }
        ],
        "secondary": [],
        "review": {
            "results": ["INSTRUMENT"],
            "comment": "现场校验确认变送器零点漂移，已安排校验",
            "by": "admin",
            "after_h": 1,
        },
    },
    {
        "loop_idx": 7,
        "win_end_ago_h": 20,
        "win_h": 24,
        "trigger": "SCHEDULED",
        "category": "PROCESS",
        "severity": "LOW",
        "primary_conf": 0.58,
        "score_avg": 76.5,
        "hits": {"EXTERNAL_DISTURBANCE": [0.58], "OSCILLATION": [0.55]},
        "basis": [
            "偏差确认突变 3.2 次/h 且与 SP 变更无关",
            "存在振荡且无粘滞/过激证据，疑外部传入或回路耦合",
        ],
        "rec": "排查上游工艺扰动与相邻回路耦合，考虑前馈控制/解耦",
        "direction": "工艺分析/前馈/解耦",
        "pending": [],
        "secondary": [],
    },
    {
        "loop_idx": 2,
        "win_end_ago_h": 22,
        "win_h": 24,
        "trigger": "SCHEDULED",
        "category": "COMMUNICATION",
        "severity": "HIGH",
        "primary_conf": 0.9,
        "score_avg": 38.6,
        "hits": {"LINK_ABNORMAL": [0.9]},
        "basis": [
            "质量码模式 CONSECUTIVE_BAD（连续 Bad 断流）",
            "最长连续 Bad 1420 点",
            "Bad 质量码占比 31.2%",
        ],
        "rec": "检查通信链路：OPC 服务器/网络/采集卡（修复断流后复诊确认下游结论）",
        "direction": "检查通信链路",
        "pending": [],
        "secondary": [],
        "review": {
            "results": ["COMMUNICATION"],
            "comment": "确认 OPC 采集卡间歇断流，更换后复诊",
            "by": "admin",
            "after_h": 2,
        },
    },
    # 近 7d 窗口
    {
        "loop_idx": 0,
        "win_end_ago_h": 40,
        "win_h": 24,
        "trigger": "SCHEDULED",
        "category": "VALVE",
        "severity": "MEDIUM",
        "primary_conf": 0.74,
        "score_avg": 55.1,
        "hits": {"VALVE_STICTION": [0.74], "OSCILLATION": [0.52]},
        "basis": ["粘滞算子命中：椭圆拟合（融合置信 0.74）", "椭圆拟合粘滞指数 0.48"],
        "rec": "检查阀门执行机构，清洁或更换阀门填料",
        "direction": "检修/更换配件",
        "pending": [],
        "secondary": [],
    },
    {
        "loop_idx": 5,
        "win_end_ago_h": 72,
        "win_h": 168,
        "trigger": "SCHEDULED",
        "category": "TUNING",
        "severity": "LOW",
        "primary_conf": 0.58,
        "score_avg": 81.3,
        "hits": {"OVERCONSERVATIVE": [0.58]},
        "basis": ["响应迟缓：实际/期望时间常数比 2.4"],
        "rec": "按证据方向重新整定：过激减小 Kp/增大 Ti，保守增大 Kp/减小 Ti（参考 IMC）",
        "direction": "重新整定参数",
        "pending": [],
        # 投用独立维度（不参与污染链）：自动投用率 32% 命中级 6 → 次分类
        "secondary": [
            {
                "category": "UTILIZATION",
                "confidence": 0.68,
                "basis": ["时间窗内自动投用率 32%，长期手动"],
                "rec": "排查长期手动原因，恢复自动投用后再复诊（其余诊断结论在手动模式下意义有限）",
                "direction": "恢复自动投用",
            }
        ],
        "review": {
            "results": ["TUNING"],
            "comment": "确认过保守，已列入待整定批次",
            "by": "admin",
            "after_h": 4,
        },
    },
    {
        "loop_idx": 8,
        "win_end_ago_h": 120,
        "win_h": 24,
        "trigger": "MANUAL",
        "category": "PROCESS",
        "severity": "MEDIUM",
        "primary_conf": 0.72,
        "score_avg": 57.0,
        "hits": {"EXTERNAL_DISTURBANCE": [0.72], "OSCILLATION": [0.58]},
        "basis": [
            "偏差确认突变 3.6 次/h 且与 SP 变更无关",
            "存在振荡且无粘滞/过激证据，疑外部传入或回路耦合",
        ],
        "rec": "排查上游工艺扰动与相邻回路耦合，考虑前馈控制/解耦",
        "direction": "工艺分析/前馈/解耦",
        "pending": [],
        "secondary": [],
    },
    # 近 30d 窗口
    {
        "loop_idx": 3,
        "win_end_ago_h": 240,
        "win_h": 24,
        "trigger": "MANUAL",
        "category": "INSTRUMENT",
        "severity": "LOW",
        "primary_conf": 0.52,
        "score_avg": 83.9,
        "hits": {"QUALITY_ABNORMAL": [0.52]},
        "basis": ["传感器故障子类型 noise", "噪声突增 2.1 倍"],
        "rec": "检查校验变送器/仪表（修复后复诊确认下游结论）",
        "direction": "校验/维护",
        "pending": [],
        "secondary": [],
        "review": {
            "results": ["INSTRUMENT", "PROCESS"],
            "comment": "初判仪表噪声，复核确认叠加进料波动影响",
            "by": "admin",
            "after_h": 3,
        },
    },
    {
        "loop_idx": 6,
        "win_end_ago_h": 384,
        "win_h": 24,
        "trigger": "SCHEDULED",
        "category": "VALVE",
        "severity": "MEDIUM",
        "primary_conf": 0.86,
        "score_avg": 61.7,
        # 级 3：OP 长期贴限（>80%）→ VALVE（阀容量方向）
        "hits": {"OUTPUT_SATURATION": [0.86]},
        "basis": ["OP 86% 时间贴工程限位，疑阀容量不足或积分饱和"],
        "rec": "检查阀门容量/选型，排查积分饱和",
        "direction": "检修/更换配件",
        "pending": [],
        "secondary": [],
    },
    {
        "loop_idx": 9,
        "win_end_ago_h": 528,
        "win_h": 168,
        "trigger": "MANUAL",
        "category": "PROCESS",
        "severity": "MEDIUM",
        "primary_conf": 0.5,
        "score_avg": 45.2,
        # 级 4 纯振荡推断（无粘滞/过激证据）：候选置信固定 0.50
        "hits": {"OSCILLATION": [0.83]},
        "basis": ["存在振荡且无粘滞/过激证据，疑外部传入或回路耦合"],
        "rec": "排查上游工艺扰动与相邻回路耦合，考虑前馈控制/解耦",
        "direction": "工艺分析/前馈/解耦",
        "pending": [],
        "secondary": [],
    },
    # 门禁短路：数据不足（operator/fusion/symptom 全空，severity=NULL）
    {
        "loop_idx": 10,
        "win_end_ago_h": 648,
        "win_h": 24,
        "trigger": "SCHEDULED",
        "category": "DATA_INSUFFICIENT",
        "severity": None,
        "primary_conf": 0.0,
        "score_avg": None,
        "gate_failed": True,
        "hits": {},
        "basis": ["有效数据点 18 不足（门槛 32 点）"],
        "rec": "先通过数据管理→历史数据导入补齐该时间窗数据，再重新发起诊断",
        "direction": "先补齐数据",
        "pending": [],
        "secondary": [],
    },
]


def _ds_fuse(confidences: list[float]) -> float:
    """D-S 族内融合（公式同 diagnosis_operators.fusion.dempster_shafer，保证种子数值自洽）。"""
    if not confidences:
        return 0.0
    if len(confidences) == 1:
        return float(confidences[0])
    prod_c = 1.0
    prod_n = 1.0
    for c in confidences:
        c = max(1e-9, min(1.0 - 1e-9, c))
        prod_c *= c
        prod_n *= 1.0 - c
    return prod_c / (prod_c + prod_n)


def _diag_operator_results(hits: dict[str, list[float]]) -> dict[str, Any]:
    """构造 operator_results（结构同 orchestrator._operator_result_to_dict 输出）。"""
    out: dict[str, Any] = {}
    for tag, ops in _SYMPTOM_OPS.items():
        hit_confs = hits.get(tag) or []
        for i, op_name in enumerate(ops):
            detected = i < len(hit_confs)
            features: dict[str, Any] = {}
            evidence: list[dict[str, Any]] = []
            for fname, val, thr, judg in _OP_FEATS[op_name]:
                if detected:
                    features[fname] = val
                    evidence.append(
                        {"feature": fname, "value": val, "threshold": thr, "judgment": judg}
                    )
                else:
                    features[fname] = round(val * 0.3, 4)
            out[op_name] = {
                "operator": op_name,
                "executed": True,
                "skipReason": None,
                "detected": detected,
                "confidence": round(hit_confs[i], 4) if detected else 0.12,
                "features": features,
                "evidence": evidence,
                "error": None,
            }
    return out


def _diag_fusion_results(hits: dict[str, list[float]]) -> dict[str, Any]:
    """构造 fusion_results（结构同 FamilyFusion.to_dict）。"""
    out: dict[str, Any] = {}
    for tag, family in _SYMPTOM_FAMILY.items():
        confs = hits.get(tag) or []
        ops = _SYMPTOM_OPS[tag]
        contributors = [
            {"operator": ops[i], "confidence": round(c, 4)} for i, c in enumerate(confs)
        ]
        detected = bool(contributors)
        out[tag] = {
            "family": family,
            "symptomTag": tag,
            "detected": detected,
            "confidence": round(_ds_fuse(confs), 4) if detected else 0.0,
            "contributors": contributors,
            "fused": len(confs) >= 2,
        }
    return out


def _diag_symptom_tags(hits: dict[str, list[float]]) -> dict[str, Any]:
    """构造 symptom_tags（{tag: {detected, confidence}}，置信度=族内融合值）。"""
    return {
        tag: {
            "detected": bool(hits.get(tag)),
            "confidence": round(_ds_fuse(hits.get(tag) or []), 4),
        }
        for tag in _SYMPTOM_FAMILY
    }


def _diag_metric_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """构造 metric_summary（结构同 orchestrator._build_metric_summary：0~100 统一口径）。"""
    neg_keys = (
        "badValueRate",
        "saturationRate",
        "oscillationRate",
        "stictionIndex",
        "settlingTime",
        "outputTravelIndex",
    )
    pos_keys = (
        "score",
        "effectiveAutoRate",
        "autoModeRate",
        "goodValueRate",
        "steadyRate",
        "accuracyRate",
        "fastRate",
    )
    score = spec.get("score_avg")
    if score is None:  # 门禁短路：无 KPI 快照
        return {
            "negative": dict.fromkeys(neg_keys),
            "positive": dict.fromkeys(pos_keys),
            "source": dict.fromkeys(neg_keys, "none"),
        }
    hits = spec["hits"]
    return {
        "negative": {
            "badValueRate": 18.5 if hits.get("QUALITY_ABNORMAL") else 3.6,
            "saturationRate": 86.0 if hits.get("OUTPUT_SATURATION") else 6.2,
            "oscillationRate": 41.0 if hits.get("OSCILLATION") else 3.1,
            "stictionIndex": 62.0 if hits.get("VALVE_STICTION") else 12.0,
            "settlingTime": 850.0,
            "outputTravelIndex": 38.0,
        },
        "positive": {
            "score": score,
            "effectiveAutoRate": 84.2,
            "autoModeRate": 88.5,
            "goodValueRate": 96.4,
            "steadyRate": round(score * 0.9, 1),
            "accuracyRate": round(score * 0.95, 1),
            "fastRate": round(score * 0.82, 1),
        },
        "source": dict.fromkeys(neg_keys, "kpi"),
    }


def _diag_charts(spec: dict[str, Any], points: int = 24) -> dict[str, Any]:
    """构造 evidence_charts（结构同 _build_chart_snapshots：trend + PV-OP 散点）。"""
    if spec.get("gate_failed"):
        return {"trend": {"ts": [], "pv": [], "sp": [], "op": []}, "scatter": {"pv": [], "op": []}}
    rng = random.Random(20260827)
    osc = bool(spec["hits"].get("OSCILLATION"))
    ts: list[int] = []
    pv: list[float] = []
    op: list[float] = []
    for i in range(points):
        ts.append(i * 3_600_000)  # 1h 采样（演示快照，非全量波形）
        if osc:
            pv.append(round(52.0 + 3.0 * math.sin(i * 1.05) + rng.uniform(-0.4, 0.4), 2))
            op.append(round(48.0 + 4.5 * math.sin(i * 1.05 + 0.6) + rng.uniform(-0.5, 0.5), 2))
        else:
            pv.append(round(52.0 + rng.uniform(-0.6, 0.6), 2))
            op.append(round(48.0 + rng.uniform(-0.8, 0.8), 2))
    sp = [52.0] * points
    return {"trend": {"ts": ts, "pv": pv, "sp": sp, "op": op}, "scatter": {"pv": pv, "op": op}}


def _diag_gate(spec: dict[str, Any], win_hours: int) -> dict[str, Any]:
    """构造 data_gate（结构同 GateResult.to_dict）。"""
    if spec.get("gate_failed"):
        return {
            "passed": False,
            "pointCount": 18,
            "expectedPoints": win_hours * 3600,
            "validRate": 0.21,
            "confidenceLevel": "E",
            "gapRatio": 0.9979,
            "reason": "有效数据点 18 不足（门槛 32 点）",
        }
    expected = win_hours * 3600
    point = round(expected * 0.984)
    return {
        "passed": True,
        "pointCount": point,
        "expectedPoints": expected,
        "validRate": 0.983,
        "confidenceLevel": "A",
        "gapRatio": 0.016,
        "reason": None,
    }


async def seed_diagnosis_runs(db) -> None:
    """填充 diagnosis_run（幂等：独立前缀 00000000-0000-0001- 清理段，不触碰真实引擎 run）。"""
    await db.execute(text("DELETE FROM diagnosis_run WHERE id::text LIKE '00000000-0000-0001-%'"))
    loops = await db.execute(text("SELECT id FROM loop_ledger ORDER BY created_at LIMIT 12"))
    loop_ids = [r[0] for r in loops]
    if len(loop_ids) < 11:
        print(f"⚠️  loop_ledger 仅 {len(loop_ids)} 条（<11），跳过 diagnosis_run 种子")
        return

    now = datetime.now(UTC).replace(tzinfo=None)
    insert_sql = text("""
        INSERT INTO diagnosis_run
        (id, loop_id, triggered_by, trigger_type, time_window_start, time_window_end,
         operator_group, status, data_gate, operator_results, fusion_results, symptom_tags,
         primary_category, primary_confidence, secondary_categories, pending_review,
         severity, rationale, recommendations, evidence_charts, metric_summary,
         threshold_version, algorithm_version, started_at, finished_at, duration_ms,
         review_status, review_results, review_comment, reviewed_by, reviewed_at,
         created_at, updated_at)
        VALUES
        (:id, :loop_id, :triggered_by, :trigger_type, :win_start, :win_end,
         'full', 'SUCCESS', CAST(:data_gate AS jsonb), CAST(:operator_results AS jsonb),
         CAST(:fusion_results AS jsonb), CAST(:symptom_tags AS jsonb),
         :primary_category, :primary_conf, CAST(:secondary AS jsonb), CAST(:pending AS jsonb),
         :severity, CAST(:rationale AS jsonb), CAST(:recommendations AS jsonb),
         CAST(:charts AS jsonb), CAST(:metric_summary AS jsonb),
         'default', :algo_version, :started_at, :win_end, :duration_ms,
         :review_status, CAST(:review_results AS jsonb), :review_comment, :reviewed_by,
         :reviewed_at, :win_end, :win_end)
    """)

    for i, spec in enumerate(_DIAG_RUN_SPECS):
        win_end = now - timedelta(hours=spec["win_end_ago_h"])
        win_start = win_end - timedelta(hours=spec["win_h"])
        gate_failed = bool(spec.get("gate_failed"))
        hits = spec["hits"]
        category = spec["category"]
        label = CATEGORY_LABELS_V2[category]
        primary_conf = spec["primary_conf"]

        # 分类判定文书（rationale / 次分类 / 待复核，语义对齐 classification.classify）
        rationale = [f"主分类 {label}（置信 {primary_conf:.2f}）：{'；'.join(spec['basis'])}"]
        secondary: list[dict[str, Any]] = []
        for s in spec["secondary"]:
            s_label = CATEGORY_LABELS_V2[s["category"]]
            rationale.append(
                f"次分类 {s_label}（置信 {s['confidence']:.2f}）：{'；'.join(s['basis'])}"
            )
            secondary.append(
                {
                    "category": s["category"],
                    "categoryLabel": s_label,
                    "confidence": s["confidence"],
                    "basis": s["basis"],
                    "status": "secondary",
                    "contaminationNote": None,
                }
            )
        pending: list[dict[str, Any]] = []
        for p in spec["pending"]:
            p_label = CATEGORY_LABELS_V2[p["category"]]
            rationale.append(f"疑似{p_label}——被主因证据污染，转待复核")
            pending.append(
                {
                    "category": p["category"],
                    "categoryLabel": p_label,
                    "confidence": p["confidence"],
                    "basis": p["basis"],
                    "status": "pending_review",
                    "contaminationNote": (
                        f"主因{label}的证据链污染了{p_label}判定，修复主因后复诊确认"
                    ),
                }
            )
        if gate_failed:
            rationale.insert(0, "数据门禁未通过：" + spec["basis"][0])
            recommendations = [
                {
                    "content": spec["rec"],
                    "basis": spec["basis"][0],
                    "direction": spec["direction"],
                    "priority": 1,
                }
            ]
        else:
            recommendations = [
                {
                    "content": spec["rec"],
                    "basis": "；".join(spec["basis"]),
                    "direction": spec["direction"],
                    "priority": 1,
                }
            ]
        for j, s in enumerate(spec["secondary"]):
            recommendations.append(
                {
                    "content": s["rec"],
                    "basis": "；".join(s["basis"]),
                    "direction": s["direction"],
                    "priority": j + 2,
                }
            )

        review = spec.get("review")
        duration_ms = 4200 + i * 350
        params = {
            "id": str(UUID(f"00000000-0000-0001-0000-{6000 + i:012d}")),
            "loop_id": loop_ids[spec["loop_idx"] % len(loop_ids)],
            "triggered_by": "admin" if spec["trigger"] == "MANUAL" else "system",
            "trigger_type": spec["trigger"],
            "win_start": win_start,
            "win_end": win_end,
            "data_gate": json.dumps(_diag_gate(spec, spec["win_h"])),
            "operator_results": json.dumps({} if gate_failed else _diag_operator_results(hits)),
            "fusion_results": json.dumps({} if gate_failed else _diag_fusion_results(hits)),
            "symptom_tags": json.dumps({} if gate_failed else _diag_symptom_tags(hits)),
            "primary_category": category,
            "primary_conf": primary_conf,
            "secondary": json.dumps(secondary),
            "pending": json.dumps(pending),
            "severity": spec["severity"],
            "rationale": json.dumps(rationale),
            "recommendations": json.dumps(recommendations),
            "charts": json.dumps(_diag_charts(spec)),
            "metric_summary": json.dumps(_diag_metric_summary(spec)),
            "algo_version": _DIAG_ALGO_VERSION,
            "started_at": win_end - timedelta(milliseconds=duration_ms),
            "duration_ms": duration_ms,
            "review_status": "REVIEWED" if review else "PENDING",
            "review_results": json.dumps(review["results"]) if review else None,
            "review_comment": review["comment"] if review else None,
            "reviewed_by": review["by"] if review else None,
            "reviewed_at": (win_end + timedelta(hours=review["after_h"])) if review else None,
        }
        await db.execute(insert_sql, params)

    await db.commit()
    print(f"✅ 已插入 {len(_DIAG_RUN_SPECS)} 条 diagnosis_run 种子（前缀 00000000-0000-0001-）")


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


# ---------------------------------------------------------------------------
# 6. G-整定演示数据（W11 批次 / W12 待整定队列 / W13 散点）
# ---------------------------------------------------------------------------


def _scatter_pts(loop_ids: list, pairs: list[tuple[int, float, float]]) -> tuple[str, str]:
    """构造 scatters_before/after JSON 字符串（[{loop_id, score}]）。"""
    before = [{"loop_id": str(loop_ids[i]), "score": b} for i, b, _ in pairs]
    after = [{"loop_id": str(loop_ids[i]), "score": a} for i, _, a in pairs]
    return json.dumps(before), json.dumps(after)


async def seed_tuning_demo(db) -> None:
    """填充整定批次 + 待整定记录 + 前置工单 + 散点数据源（幂等）。"""
    # 幂等清理（本脚本创建的演示数据）
    await db.execute(
        text("""
            DELETE FROM tuning_batch_records WHERE batch_id IN
            (SELECT id FROM tuning_batch WHERE batch_no LIKE 'ZD-2026-%')
        """)
    )
    await db.execute(text("DELETE FROM tuning_batch WHERE batch_no LIKE 'ZD-2026-%'"))
    await db.execute(
        text("DELETE FROM tuning_record WHERE id::text LIKE '00000000-0000-0000-0000-%'")
    )
    await db.execute(text("DELETE FROM handling_order WHERE order_no LIKE 'CL-2026-%'"))

    loops = await db.execute(
        text("SELECT id, tag_name FROM loop_ledger ORDER BY created_at LIMIT 20")
    )
    loop_rows = [(r[0], r[1]) for r in loops]
    if len(loop_rows) < 10:
        print("⚠️  loop_ledger 不足 10 条，跳过 tuning 演示数据")
        return
    loop_ids = [r[0] for r in loop_rows]

    now = datetime.now(UTC).replace(tzinfo=None)

    # --- 前置工单 CL-2026-0819（VALVE · EXECUTING 未闭合 → 阻塞 ZD-2026-0145）---
    prereq_order_id = UUID("00000000-0000-0000-0000-000000003001")
    await db.execute(
        text("""
            INSERT INTO handling_order
            (id, order_no, loop_id, source, title, action_type, status,
             created_at, updated_at, sla_deadline_at, sla_stage,
             scope_type, scope_id, reopen_count, handler)
            VALUES
            (:id, 'CL-2026-0819', :loop_id, 'DIAGNOSIS',
             '振荡治理·更换阀门定位器（先换阀后整定）', 'VALVE', 'EXECUTING',
             :created, :updated, :sla_deadline, 'WARN',
             'GLOBAL', 0, 0, '张工')
        """),
        {
            "id": prereq_order_id,
            "loop_id": loop_ids[12] if len(loop_ids) > 12 else loop_ids[0],
            "created": now - timedelta(hours=20),
            "updated": now,
            "sla_deadline": now + timedelta(hours=3),
        },
    )

    # --- TUNING 类工单 ×2（散点来源 2：kpi_before/after，CLOSED/VERIFYING）---
    tuning_orders = [
        # (seq, loop_idx, before, after, status)
        (1, 10, 67.0, 76.0, "CLOSED"),
        (2, 11, 65.0, 72.0, "VERIFYING"),
    ]
    for seq, loop_idx, b, a, status in tuning_orders:
        if len(loop_ids) <= loop_idx:
            continue
        await db.execute(
            text("""
                INSERT INTO handling_order
                (id, order_no, loop_id, source, title, action_type, status,
                 created_at, updated_at, verified_at, sla_stage,
                 scope_type, scope_id, reopen_count, handler,
                 kpi_before, kpi_after)
                VALUES
                (:id, :order_no, :loop_id, 'DIAGNOSIS',
                 :title, 'TUNING', :status,
                 :created, :updated, :verified, 'NONE',
                 'GLOBAL', 0, 0, '王工',
                 CAST(:kpi_before AS jsonb), CAST(:kpi_after AS jsonb))
            """),
            {
                "id": UUID(f"00000000-0000-0000-0000-0000000031{seq:02d}"),
                "order_no": f"CL-2026-08{30 + seq}",
                "loop_id": loop_ids[loop_idx],
                "title": f"回路整定实施与效果验证 #{seq}",
                "status": status,
                "created": now - timedelta(days=2),
                "updated": now - timedelta(hours=6),
                "verified": now - timedelta(hours=6) if status == "CLOSED" else None,
                "kpi_before": json.dumps({"score": b}),
                "kpi_after": json.dumps({"score": a}),
            },
        )

    # --- 整定批次 ×5（W11，对齐原型 BATCHES）---
    # (batch_no, title, scope, status, prereq_ids, scatter 配对[(loop_idx,b,a)], 记录算法)
    sc_0142, sa_0142 = _scatter_pts(
        loop_ids,
        [
            (0, 69.0, 88.0),
            (1, 72.0, 85.0),
            (2, 68.0, 83.0),
            (3, 71.0, 86.0),
            (4, 74.0, 82.0),
            (5, 66.0, 79.0),
        ],
    )
    sc_0143, sa_0143 = _scatter_pts(loop_ids, [(6, 74.0, 82.0), (7, 72.0, 81.0), (8, 76.0, 83.0)])
    sc_0141, sa_0141 = _scatter_pts(loop_ids, [(9, 66.0, 62.0)])
    batches = [
        {
            "batch_no": "ZD-2026-0142",
            "title": "常减压 PID 批次整定",
            "scope_type": "FACTORY",
            "scope_id": 100,
            "status": "COMPLETED",
            "prereq": [],
            "block_reason": None,
            "sc_before": sc_0142,
            "sc_after": sa_0142,
            "actual_start": now - timedelta(days=1, hours=12),
            "completed": now - timedelta(hours=20),
            "created": now - timedelta(days=2),
        },
        {
            "batch_no": "ZD-2026-0143",
            "title": "乙烯裂解温度组整定",
            "scope_type": "FACTORY",
            "scope_id": 200,
            "status": "COMPLETED",
            "prereq": [],
            "block_reason": None,
            "sc_before": sc_0143,
            "sc_after": sa_0143,
            "actual_start": now - timedelta(days=1, hours=2),
            "completed": now - timedelta(hours=8),
            "created": now - timedelta(days=1, hours=12),
        },
        {
            "batch_no": "ZD-2026-0144",
            "title": "LIC 液位组继电器反馈辨识",
            "scope_type": "FACTORY",
            "scope_id": 100,
            "status": "PENDING",
            "prereq": [],
            "block_reason": None,
            "sc_before": None,
            "sc_after": None,
            "actual_start": None,
            "completed": None,
            "created": now - timedelta(hours=10),
        },
        {
            "batch_no": "ZD-2026-0145",
            "title": "催化反再振荡组整定（先换阀后整定）",
            "scope_type": "FACTORY",
            "scope_id": 100,
            # 库存储 PENDING；prereq CL-2026-0819 EXECUTING → 服务端动态判定 BLOCKED（B-06）
            "status": "PENDING",
            "prereq": [str(prereq_order_id)],
            "block_reason": None,
            "sc_before": None,
            "sc_after": None,
            "actual_start": None,
            "completed": None,
            "created": now - timedelta(hours=6),
        },
        {
            "batch_no": "ZD-2026-0141",
            "title": "FIC 稀释蒸汽流量阶跃辨识（验证失败已回退）",
            "scope_type": "FACTORY",
            "scope_id": 200,
            "status": "CANCELLED",
            "prereq": [],
            "block_reason": None,
            "sc_before": sc_0141,
            "sc_after": sa_0141,
            "actual_start": now - timedelta(days=3),
            "completed": now - timedelta(days=2, hours=12),
            "created": now - timedelta(days=3, hours=6),
        },
    ]
    for bt in batches:
        await db.execute(
            text("""
                INSERT INTO tuning_batch
                (batch_no, title, scope_type, scope_id, status,
                 prereq_order_ids, block_reason, scatters_before, scatters_after,
                 actual_start_at, completed_at, created_at)
                VALUES
                (:batch_no, :title, :scope_type, :scope_id, :status,
                 CAST(:prereq AS jsonb), :block_reason,
                 CAST(:sc_before AS jsonb), CAST(:sc_after AS jsonb),
                 :actual_start, :completed, :created)
            """),
            {
                "batch_no": bt["batch_no"],
                "title": bt["title"],
                "scope_type": bt["scope_type"],
                "scope_id": bt["scope_id"],
                "status": bt["status"],
                "prereq": json.dumps(bt["prereq"]),
                "block_reason": bt["block_reason"],
                "sc_before": bt["sc_before"],
                "sc_after": bt["sc_after"],
                "actual_start": bt["actual_start"],
                "completed": bt["completed"],
                "created": bt["created"],
            },
        )

    # --- 整定记录（W12 队列 + 批次成员；id 前缀 00000000-...-004xxx）---
    # (seq, loop_idx, status, algorithm, created_by, batch_no, sort_order)
    records = [
        # ZD-2026-0142 成员（COMPLETED → VERIFIED）
        (1, 0, "VERIFIED", "IMC", "王工", "ZD-2026-0142", 1),
        (2, 1, "VERIFIED", "IMC", "王工", "ZD-2026-0142", 2),
        (3, 2, "VERIFIED", "IMC", "王工", "ZD-2026-0142", 3),
        (4, 3, "VERIFIED", "LAMBDA", "王工", "ZD-2026-0142", 4),
        (5, 4, "VERIFIED", "IMC", "王工", "ZD-2026-0142", 5),
        (6, 5, "VERIFIED", "SIMC", "王工", "ZD-2026-0142", 6),
        # ZD-2026-0143 成员
        (7, 6, "VERIFIED", "LAMBDA", "王工", "ZD-2026-0143", 1),
        (8, 7, "VERIFIED", "LAMBDA", "王工", "ZD-2026-0143", 2),
        (9, 8, "VERIFIED", "ZN", "王工", "ZD-2026-0143", 3),
        # ZD-2026-0141 成员（回退）
        (10, 9, "ROLLED_BACK", "IDENTIFICATION_ONLY", "赵工", "ZD-2026-0141", 1),
        # ZD-2026-0145 成员（待整定，随批次动态 BLOCKED）
        (11, 13, "DRAFT", "IMC", "张工", "ZD-2026-0145", 1),
        (12, 14, "DRAFT", "LAMBDA", "张工", "ZD-2026-0145", 2),
        # ZD-2026-0144 成员（排队中，不阻塞）
        (13, 18, "DRAFT", "IDENTIFICATION_ONLY", None, "ZD-2026-0144", 1),
        (14, 19, "DRAFT", "IDENTIFICATION_ONLY", None, "ZD-2026-0144", 2),
        # 独立待整定（不入批次；loop12 被 CL-2026-0819 同回路阻塞）
        (15, 12, "DRAFT", "IMC", None, None, 0),
        (16, 15, "DRAFT", "LAMBDA", "刘工", None, 0),
        (17, 16, "PENDING", "SIMC", "陈工", None, 0),
    ]
    for seq, loop_idx, status, algo, created_by, batch_no, sort_order in records:
        if len(loop_ids) <= loop_idx:
            continue
        rid = UUID(f"00000000-0000-0000-0000-000000004{seq:03d}")
        await db.execute(
            text("""
                INSERT INTO tuning_record
                (id, loop_id, model_type, algorithm, status, created_by, created_at,
                 fitting_score)
                VALUES (:id, :loop_id, 'FOPDT', :algo, :status, :created_by, :created,
                        :fitting)
            """),
            {
                "id": rid,
                "loop_id": loop_ids[loop_idx],
                "algo": algo,
                "status": status,
                "created_by": created_by,
                "created": now - timedelta(hours=seq * 2),
                "fitting": 82.5 if status in ("VERIFIED", "ROLLED_BACK") else None,
            },
        )
        if batch_no:
            await db.execute(
                text("""
                    INSERT INTO tuning_batch_records (batch_id, tuning_record_id, sort_order)
                    SELECT b.id, :rid, :ord FROM tuning_batch b WHERE b.batch_no = :bno
                """),
                {"rid": rid, "ord": sort_order, "bno": batch_no},
            )

    await db.commit()
    print(
        f"✅ 已插入 {len(batches)} 个 tuning_batch + {len(records)} 条 tuning_record "
        "+ 前置工单 CL-2026-0819 + 2 条 TUNING kpi 工单"
    )


async def main() -> None:
    print("🌱 开始填充工作台 v2.0 演示数据...")
    async with AsyncSessionLocal() as db:
        await seed_source_node_ids(db)
        await seed_workbench_window_summary(db)
        await seed_diagnosis_tags(db)
        await seed_diagnosis_runs(db)
        await seed_handling_orders(db)
        await seed_tuning_demo(db)
        await refresh_mv(db)
    print("✅ 演示数据填充完成！可刷新总览 Tab 查看。")


if __name__ == "__main__":
    asyncio.run(main())
