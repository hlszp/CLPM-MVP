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

    def test_lock_ttl_covers_this_task_hard_timeout(self) -> None:
        """TTL ≥ 本任务自身的硬超时：否则计算中途锁过期 → 并发重复全量轮次。

        2026-09-24 修正：原断言用全局 celery_app.task_time_limit(1800)，
        但 calculate_hourly_kpi 已显式 time_limit=14400（0921 放宽），
        用全局值校验会放过"锁提前 4 小时过期"的真实缺陷。
        """
        task = kc.calculate_hourly_kpi
        hard = getattr(task, "time_limit", None) or celery_app.conf.task_time_limit
        assert hard, "任务硬超时未配置，本不变量无法成立"
        ttl = kc._HOURLY_CALC_LOCK_TTL_SECONDS
        assert ttl >= hard, (
            f"锁 TTL {ttl}s 小于本任务硬超时 {hard}s：全量轮次耗时可达数小时，"
            "锁会在计算中途过期，后续触发可并发启动第二个全量轮次并互相覆盖结果"
        )

    def test_lock_ttl_residual_after_kill_is_bounded(self) -> None:
        """超时被杀后锁残留必须有界（不超过硬超时 + 10 分钟）。"""
        task = kc.calculate_hourly_kpi
        hard = getattr(task, "time_limit", None) or celery_app.conf.task_time_limit
        residual = kc._HOURLY_CALC_LOCK_TTL_SECONDS - hard
        assert 0 <= residual <= 600, (
            f"锁在硬超时被杀后残留 {residual}s，超出 10 分钟缓冲："
            "重投副本会被锁挡住，该小时快照静默缺失（G30 原始问题）"
        )

    def test_lock_miss_raises_instead_of_returning_success_shape(self) -> None:
        """拿不到锁的路径必须是 raise，不能返回 skipped 形状的 dict。

        2026-09-24：抛出类型改为专用 HourlyWindowBusy（RuntimeError 子类），
        便于外层区分抢锁失败（必须原样上抛）与跟踪器故障（可回退）。
        """
        src = inspect.getsource(kc._do_hourly_with_tracking)
        assert "raise HourlyWindowBusy(" in src, (
            "锁未获取路径未抛错——返回 dict 会让 Celery 记 SUCCESS，静默丢失一小时快照"
        )
        assert "skipped" not in src, "锁未获取路径仍在返回成功形状的 dict（G30 回归）"

    def test_busy_error_is_not_swallowed_by_fallback(self) -> None:
        """抢锁失败异常不得被跟踪失败回退分支吞掉。

        否则失败触发会在同一窗口无锁重跑全量批次，与持锁者 UPSERT 互相覆盖。
        """
        src = inspect.getsource(kc.calculate_hourly_kpi)
        assert "except HourlyWindowBusy:" in src, (
            "calculate_hourly_kpi 未对 HourlyWindowBusy 原样上抛：抢锁失败会被降级为无锁重算"
        )
