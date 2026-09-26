#!/usr/bin/env python3
"""实时数据链路一键诊断（只读，2026-09-25）。

症状：实时页面有值 / 无值，但趋势图没有任何数据（TDengine 落库为空）。

原理：实时采集要落库，必须**同时**通过 5 道闸门，任何一道不过都表现为
"数据没落库、趋势空白"：

  闸门1  datasource.signalr_enabled          订阅总开关
  闸门2  datasource.signalr_hub_url           Hub 地址（sys_config 是真相源）
  闸门3  Redis Leader 锁                      多实例下只有 Leader 真正订阅
  闸门4  history.storage_mode = point|shadow **决定 PointHistoryWriter 是否启动**
         （legacy 时 writer 根本不创建，实时页有值但一条都不写 TDengine）
  闸门5  tag_registry.tag_name → id           无点身份的事件被丢弃
         + TDengine 可达、st_point_data_v1 存在

用法（生产容器内）：
    docker compose -f deploy/docker/docker-compose.prod.yml exec backend \
        python scripts/diag_realtime_pipeline.py

也可在宿主机用后端虚拟环境跑（需能连 PG/Redis/TDengine）：
    cd backend && uv run python scripts/diag_realtime_pipeline.py
"""

from __future__ import annotations

import asyncio
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

results: list[tuple[str, str, str]] = []


def record(gate: str, status: str, detail: str) -> None:
    results.append((gate, status, detail))
    print(f"[{status}] {gate}: {detail}")


async def main() -> int:
    from sqlalchemy import text

    from app.core.config import settings
    from app.core.db import AsyncSessionLocal

    print("=" * 78)
    print("CLPM 实时数据链路诊断（只读）")
    print("=" * 78)

    # ---------- ① 配置：sys_config 真相源 + settings 运行值 ----------
    cfg: dict[str, Any] = {}
    tag_count = 0
    try:
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT key, value FROM sys_config WHERE key LIKE "
                        "'history.%' OR key LIKE 'datasource.%' OR key LIKE 'gap%'"
                    )
                )
            ).all()
            cfg = dict(rows)
            tag_count = int(
                (await db.execute(text("SELECT count(*) FROM tag_registry"))).scalar() or 0
            )
    except Exception as exc:  # noqa: BLE001
        record("数据库连接", FAIL, f"无法读取 sys_config/tag_registry: {exc}")

    print("--- sys_config（真相源） ---")
    for key in (
        "history.storage_mode",
        "datasource.signalr_enabled",
        "datasource.signalr_hub_url",
        "datasource.realtime_writeback_enabled",
        "datasource.gap_backfill_enabled",
        "datasource.gap_backfill_min_gap_seconds",
    ):
        print(f"    {key} = {cfg.get(key, '(缺失)')!r}")
    print("--- settings（进程启动时预载的快照） ---")
    print(f"    SIGNALR_ENABLED            = {settings.SIGNALR_ENABLED}")
    print(f"    SIGNALR_HUB_URL            = {settings.SIGNALR_HUB_URL or '(空)'}")
    print(f"    REALTIME_WRITEBACK_ENABLED = {settings.REALTIME_WRITEBACK_ENABLED}")
    print(f"    HISTORY_STORAGE_MODE       = {settings.HISTORY_STORAGE_MODE}")

    # 闸门1：订阅开关
    enabled = str(cfg.get("datasource.signalr_enabled", "")).lower() == "true"
    record(
        "闸门1 订阅总开关",
        PASS if enabled else FAIL,
        "signalr_enabled=true"
        if enabled
        else "signalr_enabled 非 true → 订阅器直接退出（日志：实时数据订阅已禁用）",
    )

    # 闸门2：Hub 地址
    hub = cfg.get("datasource.signalr_hub_url") or ""
    record(
        "闸门2 Hub 地址",
        PASS if hub else FAIL,
        f"hub={hub}" if hub else "地址为空 → 日志：SIGNALR_HUB_URL 未配置，跳过实时数据订阅",
    )

    # 闸门4：写入布局（**最容易被忽略**）
    mode = str(cfg.get("history.storage_mode") or settings.HISTORY_STORAGE_MODE)
    writer_on = mode in ("point", "shadow")
    record(
        "闸门4 写入布局 storage_mode",
        PASS if writer_on else FAIL,
        f"storage_mode={mode}"
        + (
            ""
            if writer_on
            else " → PointHistoryWriter 不启动（legacy 零开销），实时页有值但 TDengine 一条不写"
        ),
    )

    # 闸门5a：点身份映射
    record(
        "闸门5a 点身份映射 tag_registry",
        PASS if tag_count > 0 else FAIL,
        f"tag_registry {tag_count} 行"
        + ("" if tag_count else " → 无点身份，事件全部计入 events_dropped_no_point"),
    )

    # ---------- ② Redis：运行态镜像 + Leader 锁 + 实时缓存 ----------
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD or None,
            db=settings.REDIS_DB,
            decode_responses=True,
        )
        mirror = {
            "signalr_enabled": await r.get("datasource:rt:signalr_enabled"),
            "realtime_writeback_enabled": await r.get("datasource:rt:realtime_writeback_enabled"),
        }
        print("--- Redis 运行态镜像键（多进程热生效真相源） ---")
        for k, v in mirror.items():
            print(f"    datasource:rt:{k} = {v!r}")

        leader_keys = []
        async for key in r.scan_iter(match="*realtime*leader*", count=100):
            leader_keys.append(key)
        for key in leader_keys:
            ttl = await r.ttl(key)
            val = await r.get(key)
            print(f"    Leader 锁 {key} = {str(val)[:40]!r} (ttl={ttl}s)")
        record(
            "闸门3 Leader 锁",
            PASS if leader_keys else WARN,
            f"发现 {len(leader_keys)} 个 Leader 相关键"
            + ("" if leader_keys else " → 没有任何实例持有锁：所有进程都在待命，无人订阅"),
        )

        rt_keys = 0
        async for _ in r.scan_iter(match="realtime:*", count=500):
            rt_keys += 1
            if rt_keys >= 5000:
                break
        record(
            "订阅是否真的在收数（Redis 实时缓存）",
            PASS if rt_keys else WARN,
            f"realtime:* 键 {rt_keys} 个"
            + ("" if rt_keys else " → 订阅没收到数据（网络/Hub/token 问题），先解决采集再谈落库"),
        )
        await r.aclose()
    except Exception as exc:  # noqa: BLE001
        record("Redis 连接", FAIL, f"{exc}")

    # ---------- ③ TDengine：表是否建、有没有近期数据 ----------
    try:
        # 复用应用自身的 TDengine 客户端：REST 端口 = TDENGINE_PORT + 11
        # （见 app/core/tdengine.py 的 _TD_REST_PORT），连接已固定 timezone=UTC。
        from app.core.tdengine import execute_sql

        db = settings.TDENGINE_DB
        # 宽表已退役并从库删除，不再探测（否则表不存在会误报 FAIL）
        for stable in ("st_point_data_v1",):
            try:
                rows = await execute_sql(
                    f"SELECT COUNT(*) AS c FROM {db}.{stable}", raise_on_error=True
                )
            except Exception as exc:  # noqa: BLE001
                record(
                    f"TDengine {stable}",
                    FAIL,
                    f"查询失败：{exc}（表不存在→writer 建表失败或从未写入）",
                )
                continue
            count = (rows[0].get("c") if rows else 0) or 0
            last = await execute_sql(
                f"SELECT LAST(ts) AS last_ts FROM {db}.{stable}", raise_on_error=True
            )
            last_ts = last[0].get("last_ts") if last else None
            recent = False
            if last_ts:
                import datetime as dt

                try:
                    ts = dt.datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))
                    recent = (
                        dt.datetime.now(dt.UTC) - ts.replace(tzinfo=ts.tzinfo or dt.UTC)
                    ).total_seconds() < 900
                except ValueError:
                    recent = False
            status = PASS if (count and recent) else (WARN if count else FAIL)
            record(
                f"TDengine {stable}",
                status,
                f"{count} 行，最新 ts={last_ts}"
                + ("" if recent else " → 近 15 分钟无新数据（落库停滞）"),
            )
    except Exception as exc:  # noqa: BLE001
        record("TDengine 连接", FAIL, f"{exc}")

    # ---------- 结论 ----------
    print("=" * 78)
    fails = [g for g, s, _ in results if s == FAIL]
    warns = [g for g, s, _ in results if s == WARN]
    if fails:
        print("结论：存在硬阻断 → " + "；".join(fails))
    elif warns:
        print("结论：无硬阻断，但需关注 → " + "；".join(warns))
    else:
        print("结论：五道闸门全部通过，实时数据应当正在落库。")
    print("把以上完整输出贴回对话即可定位。")
    print("=" * 78)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
