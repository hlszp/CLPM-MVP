"""影子开关、独立报告与有界单队列；不接触正式任务/结果表。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models.sys_config import SysConfig
from app.services.calc_snapshot import _atomic_write, snapshot_root
from app.services.result_ledger import canonical_json
from app.services.ten_minute_blocks import utc

CONFIG_KEY = "kpi.shadow.config"
ACTIVE_KEY = "shadow10:cohort"
LEASE_KEY = "shadow10:execution"
COHORT_TTL = 900  # 排队超过 15 分钟的影子步骤作废；不能阻塞正式任务。
MAX_LOOPS = 24
_FENCED = """
if redis.call('GET', KEYS[1]) ~= ARGV[1]
or redis.call('GET', KEYS[2]) ~= ARGV[2] then return 0 end
if ARGV[3] == 'renew' then
  redis.call('EXPIRE', KEYS[1], ARGV[5])
  redis.call('EXPIRE', KEYS[2], 60)
else
  if ARGV[4] == '' then redis.call('DEL', KEYS[1])
  else redis.call('SET', KEYS[1], ARGV[4], 'EX', ARGV[5]) end
  redis.call('DEL', KEYS[2])
end
return 1
"""
_ABORT_PENDING = """
if redis.call('GET', KEYS[1]) ~= ARGV[1]
or redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""


def parse_config(raw: str | None) -> dict:
    cfg = json.loads(raw) if raw else {"enabled": False, "loopIds": []}
    if not isinstance(cfg, dict) or type(cfg.get("enabled")) is not bool:
        raise ValueError("enabled 必须为布尔值")
    ids = cfg.get("loopIds", [])
    if not isinstance(ids, list) or len(ids) > MAX_LOOPS:
        raise ValueError("影子白名单必须为数组，至多 24 个回路")
    ids = [str(UUID(str(value))) for value in ids]
    if len(set(ids)) != len(ids) or (cfg["enabled"] and not ids):
        raise ValueError("启用必须明确指定非空且不重复的回路白名单")
    return {"enabled": cfg["enabled"], "loopIds": ids}


async def read_config() -> dict:
    from app.core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        raw = await db.execute(select(SysConfig.value).where(SysConfig.key == CONFIG_KEY))
        return parse_config(raw.scalar_one_or_none())


def report_root():
    from app.core.config import settings

    configured = settings.CLPM_KPI_SHADOW_REPORT_DIR
    return Path(configured) if configured else snapshot_root() / "ten-minute-shadow"


def report_path(report_id: str):
    return report_root() / f"{UUID(report_id)}.json"


def save_report(report_id: str, report: dict) -> str:
    path = report_path(report_id)
    _atomic_write(path, lambda f: f.write(canonical_json(report).encode()))
    # 独立影子报告：14 天 / 最多 1000 份，正式不可变输入包不在此目录。
    files = []
    for old in path.parent.glob("*.json"):
        try:
            files.append((old.stat().st_mtime, old))
        except FileNotFoundError:
            continue  # 另一进程可能刚完成保留清理。
    files.sort(reverse=True)
    cutoff = (datetime.now(UTC) - timedelta(days=14)).timestamp()
    for i, (mtime, old) in enumerate(files):
        if old != path and (i >= 1000 or mtime < cutoff):
            old.unlink(missing_ok=True)
    return str(path)


async def fenced_step(redis, old, token, new=None, *, renew=False):
    """租约 token 与 cohort 身份在同一原子操作验证；交接同时解锁。"""
    return bool(
        await redis.eval(
            _FENCED,
            2,
            ACTIVE_KEY,
            LEASE_KEY,
            old,
            token,
            "renew" if renew else "commit",
            canonical_json(new) if new else "",
            COHORT_TTL,
        )
    )


async def abort_pending(redis, raw):
    """仅回收尚无执行者接手的失败投递，不能清除后继租约。"""
    return bool(await redis.eval(_ABORT_PENDING, 2, ACTIVE_KEY, LEASE_KEY, raw))


async def enqueue(redis, *, loop_ids, start, end, kind, source_task_id=None) -> dict:
    """全局最多一条影子 cohort；重复/并发请求跳过，不堆积 961 子任务。"""
    cfg = await read_config()
    if not cfg["enabled"]:
        return {"status": "DISABLED"}
    selected = [str(p) for p in loop_ids if str(p) in cfg["loopIds"]]
    if not selected:
        return {"status": "NOT_SELECTED"}
    if not timedelta(0) < utc(end) - utc(start) <= timedelta(hours=1):
        return {"status": "UNSUPPORTED_WINDOW", "reason": "影子试验仅接受至多 1 小时窗口"}
    owner = str(uuid4())
    state = {
        "id": owner,
        "kind": kind,
        "sourceTaskId": source_task_id,
        "loopIds": list(dict.fromkeys(selected)),
        "cursor": 0,
        "start": utc(start).isoformat(),
        "end": utc(end).isoformat(),
        "createdAt": datetime.now(UTC).isoformat(),
        "status": "QUEUED",
        "reports": [],
    }
    raw = canonical_json(state)
    if not await redis.set(ACTIVE_KEY, raw, nx=True, ex=COHORT_TTL):
        return {"status": "BUSY", "reason": "已有影子试验，未另行排队"}
    try:
        save_report(owner, state)
        await asyncio.to_thread(dispatch, owner, 0)
    except (Exception, asyncio.CancelledError):
        await abort_pending(redis, raw)
        raise
    return {"status": "QUEUED", "id": owner, "selected": len(selected)}


def dispatch(owner, cursor):
    from app.tasks.ten_minute_shadow import shadow_step

    shadow_step.apply_async(args=[owner, cursor], priority=9, expires=COHORT_TTL, retry=False)


async def after_assessment(loops, results, start, end, *, source, source_task_id):
    """正式路径仅提交轻量后台请求，不等待配置查询、租约或报告写盘。"""
    from app.services.ten_minute_submission import submit

    try:
        eligible = [
            str(loop.id)
            for loop, result in zip(loops, results, strict=True)
            if isinstance(result, dict)
        ]
        report = await submit(eligible, start, end, source, source_task_id)
        logging.getLogger(__name__).info("10分钟影子请求 %s", report)
    except Exception:
        logging.getLogger(__name__).exception("10分钟影子派发失败（正式结果已独立完成）")
