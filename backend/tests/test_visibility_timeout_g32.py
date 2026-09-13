"""broker visibility_timeout 与任务超时的不变量（整改 G32）。

背景：broker_transport_options.visibility_timeout 原为 9000s（2.5h），依据是
"import_history_data 的 time_limit=7200s"。但该任务现为 86400s（24h）——任何
超过 2.5h 的导入，其未 ack 消息都会被 broker 重投给另一个 worker，造成同一
导入并发双跑（写入冲突、重复远端请求、进度互相覆盖）。

本文件守护不变量：visibility_timeout 必须严格大于所有任务的 time_limit。
今后任何人调高某个任务的 time_limit 而未同步 visibility_timeout，此处即失败。
"""

from __future__ import annotations

from app.tasks.celery_app import celery_app


class TestVisibilityTimeoutInvariant:
    """broker 重投窗口必须覆盖最长任务。"""

    def test_visibility_timeout_exceeds_max_task_time_limit(self) -> None:
        """visibility_timeout > max(任务 time_limit)。"""
        vt = (celery_app.conf.broker_transport_options or {}).get("visibility_timeout")
        assert vt, "未配置 broker visibility_timeout（将退回 Redis 默认 1h，风险更大）"

        limits = [getattr(t, "time_limit", None) for t in celery_app.tasks.values()]
        declared = [int(x) for x in limits if x]
        global_limit = celery_app.conf.task_time_limit
        if global_limit:
            declared.append(int(global_limit))
        assert declared, "未能读取到任何任务超时配置"

        worst = max(declared)
        assert int(vt) > worst, (
            f"visibility_timeout={vt}s 未大于最长任务超时 {worst}s："
            "超长任务的未 ack 消息会被 broker 重投，导致同一任务并发双跑"
        )
