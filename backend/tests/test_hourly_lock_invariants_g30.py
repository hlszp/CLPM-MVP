"""小时评估锁与 Celery 硬超时的不变量（整改 G30）。

背景：原锁 TTL = 7200s，而 celery_app.task_time_limit = 1800s。任务被硬超时
SIGKILL（进程级、不 ack）后 broker 会重投；重投副本到达时锁仍存活（最多剩余
约 5400s）→ 走"已有任务在执行"分支 → 静默 skipped 并被 Celery 记 SUCCESS。
Beat 自动触发路径更连 TaskRecord 都不创建，那一小时的 KPI 快照永久缺失且无人知。

本文件守护两条：
1. 锁 TTL 必须严格小于 Celery 硬超时（否则重投副本永远拿不到锁）；
2. 拿不到锁时必须抛错，而非返回成功形状（返回 dict 会被 Celery 记 SUCCESS，
   与已写入的 TaskRecord FAILED 自相矛盾，监控侧也看不到"有一小时没算"）。
"""

from __future__ import annotations

import inspect

from app.tasks import kpi_calc as kc
from app.tasks.celery_app import celery_app


class TestHourlyLockInvariants:
    """锁 TTL 与失败可见性。"""

    def test_lock_ttl_strictly_below_celery_hard_timeout(self) -> None:
        """TTL < task_time_limit：保证超时被杀后重投副本能取到锁。"""
        hard = celery_app.conf.task_time_limit
        assert hard, "任务硬超时未配置，本不变量无法成立"
        ttl = kc._HOURLY_CALC_LOCK_TTL_SECONDS
        assert ttl < hard, (
            f"锁 TTL {ttl}s 未小于 Celery 硬超时 {hard}s：任务被硬超时杀死后，"
            "锁会在重投副本到达时仍存活，副本静默 skipped 且被记 SUCCESS"
        )

    def test_lock_miss_raises_instead_of_returning_success_shape(self) -> None:
        """拿不到锁的路径必须是 raise，不能返回 skipped 形状的 dict。"""
        src = inspect.getsource(kc._do_hourly_with_tracking)
        assert "raise RuntimeError(" in src, (
            "锁未获取路径未抛错——返回 dict 会让 Celery 记 SUCCESS，静默丢失一小时快照"
        )
        assert "skipped" not in src, "锁未获取路径仍在返回成功形状的 dict（G30 回归）"
