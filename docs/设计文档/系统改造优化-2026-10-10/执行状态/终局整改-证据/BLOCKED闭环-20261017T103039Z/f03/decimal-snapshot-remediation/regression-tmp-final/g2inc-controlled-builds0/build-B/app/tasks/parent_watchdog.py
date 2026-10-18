"""父进程看门狗：Celery 子进程跟随宿主（uvicorn）退出，防独立进程组孤儿。

问题背景（2026-08-18 实测）：Beat/Worker 由 lifespan 用
start_new_session=True 拉起（独立 session），退出清理靠
lifespan/atexit/signal 三层钩子——但它们都要求宿主"有机会执行用户态
代码"。宿主被 SIGKILL / 崩溃 / OOM 时任何钩子都不会跑，Celery 进程组
被 launchd 收养后长期滞留，继续消费任务并与新实例冲突。

方案：spawn 时宿主注入 CLPM_PARENT_PID 环境变量；Celery 进程内
（beat 主进程 / worker 主进程 / 每个 prefork 子进程）启动 daemon 线程
周期检查宿主是否存活——getppid 与记录值不符（被收养）且 kill(0) 双重
确认宿主进程不存在时，发 SIGTERM 优雅自退出。

- 不依赖信号传递与 PDEATHSIG（Linux 专属），macOS/Linux 通用
- 未注入环境变量（运维手工启动）→ 不装看门狗，保持手工生命周期
- prefork 子进程同样挂看门狗：worker master 被 SIGKILL 时 pool 子进程
  不必然跟随退出，直接监视宿主 PID 统一口径
"""

from __future__ import annotations

import logging
import os
import signal
import threading

logger = logging.getLogger(__name__)

#: 环境变量名：宿主（uvicorn）进程 PID，由 main.py Popen 注入
ENV_PARENT_PID = "CLPM_PARENT_PID"

#: 检查间隔（秒）。宿主死亡到自退出的最迟窗口 = 1 个周期
CHECK_INTERVAL = 3.0


def parent_gone(parent_pid: int) -> bool:
    """判定被监视的父进程是否已死亡：getppid != parent_pid 即死亡。

    纯 getppid 判据（内核维护的父子关系，出生即定）：
    - 父进程存活 → getppid 恒等于 parent_pid，不可能抖动
    - 父进程死亡 → 本进程被 init/launchd 收养，getppid 变为 1 等

    为什么不用 kill(parent_pid, 0) 双重确认（2026-08-18 实测教训）：
    父进程被 SIGKILL 后若无人 wait/reap，会以 <defunct> 僵尸态滞留
    PID 表，kill(0) 对僵尸返回成功 → 看门狗永远误判"父进程存活"，
    防护失效（现场：worker master 僵尸 + pool 孤儿滞留）。
    """
    return os.getppid() != parent_pid


def _spawn_watcher_thread(label: str, parent_pid: int) -> None:
    """启动 daemon 线程监视宿主进程，宿主死亡 → 本进程 SIGTERM 自退出。"""

    def _watch() -> None:
        while True:
            threading.Event().wait(CHECK_INTERVAL)
            try:
                if not parent_gone(parent_pid):
                    continue
                logger.warning(
                    "[%s] 被监视进程 %d 已退出，看门狗触发自退出（防孤儿滞留）",
                    label,
                    parent_pid,
                )
                os.kill(os.getpid(), signal.SIGTERM)
                return
            except Exception:  # noqa: BLE001
                # 看门狗自身异常绝不冒泡终止线程（守护线程死亡=防护失效）
                logger.exception("[%s] 父进程看门狗检查异常（继续）", label)

    t = threading.Thread(target=_watch, name=f"clpm-parent-watchdog-{label}", daemon=True)
    t.start()
    logger.info("[%s] 父进程看门狗已启动（监视 PID=%s）", label, parent_pid)


def install_watching(label: str, watch_pid: int) -> None:
    """安装看门狗监视指定进程（watch_pid 死亡 → 本进程自退出）。

    幂等（fork 安全）：按实际存活的线程名判重，不能用模块级标记位——
    POSIX fork 只复制调用线程，pool 子进程会继承 master 的标记位内存
    却不继承看门狗线程，标记位会让子进程误判"已安装"而裸奔。
    """
    thread_name = f"clpm-parent-watchdog-{label}"
    if thread_name in {t.name for t in threading.enumerate()}:
        return
    if watch_pid <= 1 or watch_pid == os.getpid():
        return
    _spawn_watcher_thread(label, watch_pid)


def install_from_env(label: str) -> None:
    """按环境变量监视宿主（uvicorn）；未注入（手工启动）则跳过。"""
    raw = os.environ.get(ENV_PARENT_PID, "").strip()
    if not raw:
        return
    try:
        parent_pid = int(raw)
    except ValueError:
        logger.warning("[%s] %s 非法（%r），跳过安装看门狗", label, ENV_PARENT_PID, raw)
        return
    install_watching(label, parent_pid)


def install_direct_parent(label: str) -> None:
    """监视直接父进程（prefork pool 子进程专用）。

    pool 子进程不能沿用 CLPM_PARENT_PID（那是 uvicorn PID，且环境变量
    继承自 master）：worker master 被 SIGKILL 时 pool 子进程被收养但
    命令行与 master 相同，pgrep 仍误判"worker 在位"→ 宿主看门狗不补
    拉起，pool 实际无人派活已瘫痪。改为监视直接父进程（master），
    master 死 → pool 3s 内级联自退 → pgrep 归零 → 宿主补拉起新 worker。
    uvicorn 崩溃场景为级联退出（宿主→master→pool，最多两跳 ≈6s）。
    """
    install_watching(label, os.getppid())
