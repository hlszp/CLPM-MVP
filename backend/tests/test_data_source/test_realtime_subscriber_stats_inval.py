"""2026-09-19 FD 泄漏事故回归：MODE 统计缓存失效的值变化门控 + 单飞合并.

事故链（zpdev 实弹）：每条 MODE 消息 spawn 一个并发 scan_iter 失效任务 →
Redis 连接池只增不减 → 池水位棘轮至历史并发峰值 → 5.9 天打满容器
1024 FD（EMFILE）→ 实时订阅 Leader 全面瘫痪 30+ 小时无人发现。

本测试守护两道防线：
1. 仅 MODE **值**变化（或首次出现）才触发失效——同值重复消息（ts 推进）不触发；
2. 失效任务并发度恒为 1——在途任务运行期间的新变更只置脏标志，收尾补跑一轮。
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.services.data_source.realtime_subscriber import RealtimeSubscriber
from tests.test_data_source.test_realtime_subscriber import _FakeRedis

_SUB = "app.services.data_source.realtime_subscriber"


def _make_sub() -> RealtimeSubscriber:
    sub = RealtimeSubscriber()
    sub._tag_role_cache = {"LIC_MODE": ("LIC", "MODE")}
    sub._loop_role_tags = {"LIC": {"MODE": "LIC_MODE"}}
    return sub


async def _feed(sub: RealtimeSubscriber, value: str, ts: str) -> None:
    await sub._cache_value({"tagCode": "LIC_MODE", "value": value, "quality": 1, "collectTime": ts})


async def test_mode_invalidation_skipped_for_same_value_messages():
    """同值 MODE 消息（仅 ts 推进）不得触发统计缓存失效."""
    fake = _FakeRedis()
    sub = _make_sub()

    runs = 0

    async def _fake_inval() -> None:
        nonlocal runs
        runs += 1

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch.object(sub, "_invalidate_loop_stats_cache", new=_fake_inval),
    ):
        await _feed(sub, "1", "2026-09-19T00:00:00")
        for i in range(1, 6):
            await _feed(sub, "1", f"2026-09-19T00:0{i}:00")
        await asyncio.sleep(0)
        assert sub._stats_inval_task is not None, "首次 MODE 应启动单飞任务"
        # 等单飞任务跑完（无新变更 → 一轮即退出）
        await sub._stats_inval_task
        assert runs == 1, "5 条同值重复消息不应补跑失效"
        assert sub._stats_inval_task.done()


async def test_mode_invalidation_single_flight_coalesces_burst():
    """在途失效任务运行期间到达的值变化只置脏，收尾补跑一轮（并发度恒 1）."""
    fake = _FakeRedis()
    sub = _make_sub()

    runs = 0
    release = asyncio.Event()

    async def _fake_inval() -> None:
        nonlocal runs
        runs += 1
        await release.wait()  # 模拟 scan 耗时，卡住首个 worker

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch.object(sub, "_invalidate_loop_stats_cache", new=_fake_inval),
    ):
        await _feed(sub, "1", "2026-09-19T00:00:00")
        await asyncio.sleep(0)
        worker = sub._stats_inval_task
        assert worker is not None and not worker.done()
        assert runs == 1

        # 在途期间 3 次值变化：只置脏，不得再 spawn 新任务
        for i in range(3):
            await _feed(sub, str(i), f"2026-09-19T00:0{i + 1}:00")
            await asyncio.sleep(0)
            assert sub._stats_inval_task is worker, "单飞期间不得创建第二个任务"
        assert sub._stats_inval_pending is True, "在途期间的变更应置脏"

        # 放行 → 补跑一轮（runs=2）→ 无新变更退出
        release.set()
        await worker
        assert runs == 2, "收尾应恰好补跑一轮"
        assert sub._stats_inval_pending is False


async def test_mode_invalidation_triggered_again_after_worker_exit():
    """单飞任务退出后到达的新值变化应能再次启动任务（非一次性闸门）."""
    fake = _FakeRedis()
    sub = _make_sub()

    runs = 0

    async def _fake_inval() -> None:
        nonlocal runs
        runs += 1

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch.object(sub, "_invalidate_loop_stats_cache", new=_fake_inval),
    ):
        await _feed(sub, "1", "2026-09-19T00:00:00")
        await asyncio.sleep(0)
        first = sub._stats_inval_task
        await first  # 首轮完成退出
        assert runs == 1

        # 新的值变化 → 重新启动任务
        await _feed(sub, "0", "2026-09-19T00:01:00")
        await asyncio.sleep(0)
        assert sub._stats_inval_task is not first
        await sub._stats_inval_task
        assert runs == 2
