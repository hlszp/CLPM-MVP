#!/usr/bin/env python3
"""清理双 Beat 时代（PR #90 合并前）遗留的重复 STANDARD 任务记录（一次性脚本）。

背景：v6.1 之前手工启动的 Celery Beat 与后端 lifespan 自动启动的 Beat 并存，
每小时定时评估任务被双触发，任务列表遗留同标题（自动评估-YYMMDDHH）的重复
STANDARD 任务记录。PR #90（2026-07-20 合并）已修复双触发，本脚本仅清理历史数据。

分组与删除规则（对齐体检计划 P0 #4）：
    - 按 title 分组（title 内嵌上海时区小时窗 ``自动评估-YYMMDDHH``，
      等价于 title+小时窗）
    - 空 title 的历史任务不参与分组（非双 Beat 产物，避免误删手动重跑记录）
    - 每组保留最早创建者，删除其余处于终态（SUCCESS/FAILED/CANCELLED）的记录
    - RUNNING/PENDING 记录绝不删除

任务记录真相源：Redis（``task:{task_id}`` Hash + ``task:index`` Sorted Set），
PG 无任务记录表。删除路径与 ``DELETE /tasks/{task_id}`` 一致
（task_tracker.delete_task_auxiliary_keys + DEL Hash + ZREM 索引）。

用法::

    cd backend && .venv/bin/python scripts/cleanup_duplicate_standard_tasks.py
    cd backend && .venv/bin/python scripts/cleanup_duplicate_standard_tasks.py --execute

默认 dry-run（仅打印将删除的清单，不写库）；``--execute`` 才真正删除。
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from app.core.redis import close_redis, redis_client
from app.services.task_tracker import TERMINAL_STATUSES, delete_task_auxiliary_keys

_TASK_PREFIX = "task"
_TASK_INDEX_KEY = "task:index"


@dataclass
class TaskRecord:
    """Redis 中的 STANDARD 任务记录（仅保留清理所需字段）。"""

    task_id: str
    title: str
    status: str
    created_at: str


@dataclass
class DupGroup:
    """一组同标题重复任务：keeper 为最早创建者。"""

    title: str
    keeper: TaskRecord
    delete_candidates: list[TaskRecord] = field(default_factory=list)
    skipped_active: list[TaskRecord] = field(default_factory=list)


def _parse_created_at(value: str) -> datetime:
    """解析 created_at（ISO 8601）；解析失败回退到最小时间（排最前）。"""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=None)


async def _load_standard_tasks() -> list[TaskRecord]:
    """从 task:index 索引读取全部 STANDARD 任务记录（跳过索引残留空 Hash）。"""
    task_ids = await redis_client.zrange(_TASK_INDEX_KEY, 0, -1)
    records: list[TaskRecord] = []
    for tid in task_ids:
        data = await redis_client.hgetall(f"{_TASK_PREFIX}:{tid}")
        if not data or data.get("task_type") != "STANDARD":
            continue
        records.append(
            TaskRecord(
                task_id=tid,
                title=data.get("title", ""),
                status=data.get("status", ""),
                created_at=data.get("created_at", ""),
            )
        )
    return records


def find_duplicate_groups(records: list[TaskRecord]) -> list[DupGroup]:
    """按 title 分组找重复组；空 title 不参与分组。

    每组按 created_at 升序排序，最早创建者为 keeper；其余终态记录进入
    待删清单，非终态（RUNNING/PENDING）记录列入跳过清单（绝不删除）。
    """
    by_title: dict[str, list[TaskRecord]] = defaultdict(list)
    for rec in records:
        if rec.title:
            by_title[rec.title].append(rec)

    groups: list[DupGroup] = []
    for title, members in sorted(by_title.items()):
        if len(members) < 2:
            continue
        members.sort(key=lambda r: (_parse_created_at(r.created_at), r.task_id))
        group = DupGroup(title=title, keeper=members[0])
        for rec in members[1:]:
            if rec.status in TERMINAL_STATUSES:
                group.delete_candidates.append(rec)
            else:
                group.skipped_active.append(rec)
        groups.append(group)
    return groups


def _print_plan(groups: list[DupGroup]) -> int:
    """打印分组清单，返回待删除总数。"""
    total_delete = 0
    for group in groups:
        total_delete += len(group.delete_candidates)
        member_count = 1 + len(group.delete_candidates) + len(group.skipped_active)
        print(f"组 {group.title}（{member_count} 条）:")
        keeper = group.keeper
        print(f"  [保留] {keeper.task_id}  {keeper.status:<10s}  {keeper.created_at}")
        for rec in group.delete_candidates:
            print(f"  [删除] {rec.task_id}  {rec.status:<10s}  {rec.created_at}")
        for rec in group.skipped_active:
            print(f"  [跳过-非终态] {rec.task_id}  {rec.status:<10s}  {rec.created_at}")
    return total_delete


async def _delete_task(task_id: str) -> None:
    """删除单条任务记录（与 DELETE /tasks/{task_id} 相同的 Redis 清理路径）。"""
    await delete_task_auxiliary_keys(task_id)
    await redis_client.delete(f"{_TASK_PREFIX}:{task_id}")
    await redis_client.zrem(_TASK_INDEX_KEY, task_id)


async def main(execute: bool) -> None:
    records = await _load_standard_tasks()
    groups = find_duplicate_groups(records)

    mode = "EXECUTE" if execute else "DRY-RUN"
    print(f"[{mode}] STANDARD 任务共 {len(records)} 条，重复组 {len(groups)} 个")
    total_delete = _print_plan(groups)
    total_skipped = sum(len(g.skipped_active) for g in groups)
    print(f"待删除终态重复记录：{total_delete} 条；跳过非终态：{total_skipped} 条")

    if not execute:
        print("dry-run 未做任何修改；确认清单后加 --execute 执行删除。")
        return

    deleted = 0
    for group in groups:
        for rec in group.delete_candidates:
            await _delete_task(rec.task_id)
            deleted += 1
            print(f"已删除 {rec.task_id}（{group.title} {rec.status} {rec.created_at}）")
    print(f"删除完成：{deleted} 条。")


async def _run(execute: bool) -> None:
    try:
        await main(execute)
    finally:
        await close_redis()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="真正执行删除（默认 dry-run 仅打印清单）",
    )
    args = parser.parse_args()
    asyncio.run(_run(execute=args.execute))
