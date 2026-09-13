"""Beat 调度条件化必须作用于运行中的 Scheduler（整改 G28）。

背景
----
Celery 的 Service.start() 先构造 Scheduler（setup_schedule 把
app.conf.beat_schedule 合并进 self.schedule），**之后**才发送 beat_init 信号；
tick() 只读 self.schedule。因此只改 celery_app.conf.beat_schedule 对运行中的
Beat 完全无效——模块热插拔、周期覆盖、pub/sub 热重载三处同时静默失效。

既有测试恰好只断言 conf 字典，所以永远绿灯；本文件断言**真正生效的**
scheduler.schedule，并用"conf 未被改动"反向证明我们改的是活调度表。
"""

from __future__ import annotations

from types import SimpleNamespace

from celery.beat import Scheduler

from app.tasks import beat_registry as br
from app.tasks.celery_app import celery_app


class TestBeatInitMutatesLiveSchedule:
    """beat_init 必须改 scheduler.schedule，而不是 conf 副本。"""

    def test_disabled_module_entries_removed_from_live_schedule(self, monkeypatch) -> None:
        """禁用模块的调度条目必须从运行中的 schedule 中消失。"""

        # 所有可选模块视为禁用；不触发 DB。
        async def _fake_enabled() -> set[str]:
            return set()

        monkeypatch.setattr(br, "_load_enabled_modules", _fake_enabled)

        conf_before = dict(celery_app.conf.beat_schedule or {})
        assert conf_before, "测试前提：beat_schedule 非空"

        # 构造真实 Scheduler —— setup_schedule 在此发生（正是 beat_init 之前）
        sched = Scheduler(app=celery_app, lazy=False)
        sync_calls: list[int] = []
        monkeypatch.setattr(sched, "sync", lambda: sync_calls.append(1))

        br._on_beat_init_apply_modules(sender=SimpleNamespace(scheduler=sched))

        # 1) 运行中的调度表已被条件化（这是唯一真正生效的地方）
        removed = [
            n for names in br._MODULE_BEAT_ENTRIES.values() for n in names if n in conf_before
        ]
        assert removed, "测试前提：conf 中存在模块化调度条目"
        still = [n for n in removed if n in sched.schedule]
        assert not still, f"以下条目仍在运行中的 schedule 里（G28 未修复）：{still}"

        # 2) conf 未被改动 —— 反向证明改的是活调度表而非 conf 副本
        assert dict(celery_app.conf.beat_schedule or {}) == conf_before, (
            "conf.beat_schedule 被改动，说明仍在走 conf 路径（对运行中的 Beat 无效）"
        )

        # 3) 就地修改后必须落盘同步
        assert sync_calls, "修改 live schedule 后未调用 scheduler.sync()"
