"""配置 revision 探针任务（P2-02：任务边界固定快照的验证与诊断载体）.

用途：
- **C11 跨进程实验**：以真实 Celery prefork 子进程验证"任务开始固定配置
  快照、任务中不切参"——``probe_config_snapshot`` 在任务边界调用
  ``pin_config_snapshot``（读 PostgreSQL 持久 revision，不依赖 Redis 通知），
  长睡期间外部发布新 revision 后，任务结束仍回报固定快照的 v1 值；新任务
  则拿到 v2 值。
- **运行诊断**：回报执行子进程 PID/hostname（task→PID 取证）与当前持久
  revision，供运维核查 worker 子进程的配置同步状态。

纪律：本任务只读配置（不发布、不写业务表）；结果经 Celery result backend
返回（JSON 可序列化）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket

from app.services import config_publish as cp
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_probe(
    *,
    metric_codes: list[str] | None,
    control_type: str,
    sleep_seconds: int,
    resolve_scope: dict | None,
) -> dict:
    """在新事件循环中执行探针（Celery 同步任务内无事件循环）."""
    from app.core.db import AsyncSessionLocal

    async def _probe() -> dict:
        async with AsyncSessionLocal() as db:
            # 任务边界：读持久 revision 固定快照（Redis 断连不影响——不读 Redis）
            snapshot = await cp.pin_config_snapshot(db, metric_codes=metric_codes)
            if sleep_seconds > 0:
                logger.info(
                    "[config-probe pid=%s] 快照已固定 revision=%s，休眠 %ss"
                    "（期间外部发布不切换本任务参数）",
                    os.getpid(),
                    snapshot["configRevision"],
                    sleep_seconds,
                )
                await asyncio.sleep(sleep_seconds)
                # 休眠后：从 DB 读当前持久 revision（仅对比展示，不改快照）
                db_revision_now = await cp.get_current_revision(db)
            else:
                db_revision_now = snapshot["configRevision"]
            resolved: dict | None = None
            if resolve_scope and metric_codes:
                resolved = cp.resolve_from_snapshot(
                    snapshot,
                    metric_codes[0],
                    control_type,
                    template_key=resolve_scope.get("templateKey"),
                    node_id=resolve_scope.get("nodeId"),
                    loop_id=resolve_scope.get("loopId"),
                    task_overrides=resolve_scope.get("taskOverrides"),
                )
            return {
                "pinnedRevision": snapshot["configRevision"],
                "dbRevisionAfterSleep": db_revision_now,
                "pinnedAt": snapshot["pinnedAt"],
                "params": snapshot["params"],
                "overrides": snapshot["overrides"],
                "resolved": resolved,
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
            }

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_probe())
    finally:
        loop.close()


@celery_app.task(name="app.tasks.config_probe.probe_config_snapshot", bind=True)
def probe_config_snapshot(
    self,
    metric_codes: list[str] | None = None,
    control_type: str = "STABLE",
    sleep_seconds: int = 0,
    resolve_scope: dict | None = None,
) -> dict:
    """任务边界固定配置快照并回报（运行中不切参；回报执行子进程 PID）.

    Args:
        metric_codes: 仅探测指定指标（None = 全部注册指标）
        control_type: 解析用控制类型
        sleep_seconds: 固定快照后的休眠秒数（模拟长任务运行窗口）
        resolve_scope: 可选作用域（templateKey/nodeId/loopId/taskOverrides），
            提供时额外回报五层链解析结果（快照内解析，零 DB 访问）

    Returns:
        ``{"pinnedRevision", "dbRevisionAfterSleep", "pinnedAt", "params",
        "overrides", "resolved", "pid", "hostname", "taskId"}``
    """
    result = _run_probe(
        metric_codes=metric_codes,
        control_type=control_type,
        sleep_seconds=sleep_seconds,
        resolve_scope=resolve_scope,
    )
    result["taskId"] = self.request.id
    return result


__all__ = ["probe_config_snapshot"]
