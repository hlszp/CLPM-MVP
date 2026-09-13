"""批量失败熔断（整改 G29）。

背景
----
asyncio.gather(return_exceptions=True) 把异常收集为返回值，
_summarize_batch_results 将其计入 failed，但批量入口仍**正常返回**，跟踪包装
随即无条件 task_tracker.update_status(SUCCESS, progress=1.0)。于是 DB 断连、
TDengine 全站不可达、表结构漂移这类系统性故障在"自动任务"页表现为**成功**：
autoretry_for 永不触发、死信不产生、监控侧也无信号（celery_task_total 本就无埋点）。

本文件守护熔断判定本身；_do_calculate 端到端路径（真实失败注入）未覆盖，
已在整改方案登记为残余风险。
"""

from __future__ import annotations

import pytest

from app.tasks.kpi_calc import _BATCH_FAILURE_ABORT_RATIO, _assert_batch_health


class TestBatchFailureBreaker:
    """失败占比达到阈值必须抛错，使任务进入可观测的失败终态。"""

    def test_all_failed_raises(self) -> None:
        """961 回路全失败（DB/TDengine 不可达的典型形态）必须抛错。"""
        with pytest.raises(RuntimeError, match="系统性故障"):
            _assert_batch_health({"success": 0, "inconclusive": 0, "failed": 961}, 961)

    def test_at_threshold_raises(self) -> None:
        """恰好达到阈值（一半失败）即熔断。"""
        half = 5
        with pytest.raises(RuntimeError):
            _assert_batch_health({"success": half, "failed": half}, half * 2)

    def test_minority_failure_passes(self) -> None:
        """少数回路失败属正常（数据缺失/单点异常），不得熔断。"""
        _assert_batch_health({"success": 9, "inconclusive": 0, "failed": 1}, 10)

    def test_empty_batch_passes(self) -> None:
        """空批次（total=0）不得因除零或误判而抛错。"""
        _assert_batch_health({"success": 0, "failed": 0}, 0)

    def test_threshold_constant_is_sane(self) -> None:
        """阈值须落在 (0,1] 的合理区间，防止被改成 0（恒熔断）或 >1（永不熔断）。"""
        assert 0 < _BATCH_FAILURE_ABORT_RATIO <= 1
