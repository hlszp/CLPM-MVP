"""FastAPI application entry point.

Wires up logging, CORS, global exception handlers and route prefixes:
- ``/health`` (root) for liveness probes
- ``/api/v1/*`` for business endpoints (auth, ...)
- ``/docs`` and ``/redoc`` for OpenAPI documentation

v6.1：lifespan 中自动启动 Celery Beat 调度进程和 Celery Worker 任务执行
进程，确保每小时 KPI 计算等定时任务、手动触发任务（历史数据导入、KPI 回算等）
在项目启动后自动执行，无需手动启动 Beat / Worker。
生产环境由 docker-compose 独立 celery-beat / celery-worker 容器接管，跳过。
"""

from __future__ import annotations

import asyncio
import atexit
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TextIO

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import (
    # MVP 精简：已屏蔽 AAS/OPC UA 同步模块 → 不注册 aas
    # aas,
    # AI 洞察通用服务（4 场景统一入口：诊断/性能/整定/工作台）
    ai_insight,
    # 智能预警规则引擎（PRD v6.2 §4.4.6）
    alert,
    # P0-B: 指标算法参数配置
    algorithm_config,
    algorithms,
    audit_logs,
    auth,
    # v6.1: 数据可信度阈值配置
    confidence_config,
    configs,
    dashboard,
    dataplanner,
    datasource,
    # v6.1: DCS 配置管理（品牌/型号/MODE 定义/映射矩阵）
    dcs,
    # MVP 精简：已屏蔽诊断模块 → 不注册 diagnosis / diagnosis_trigger_config
    # diagnosis,
    # MVP v2 诊断模块（2026-08-16 重设计：元算子+原因分类，仅手动触发）
    diagnosis_v2,
    # 工厂模型 AAS 同步（工厂配置页：独立同步配置区 + 全量同步）
    factory_sync,
    # diagnosis_trigger_config,
    grading_config,
    handling,
    health,
    # P3-04: LLM 配置（AI 洞察门禁依赖；endpoint 本身独立，不依赖诊断模块）
    llm_config,
    # Phase 3: 回路数据管理（历史数据导入）
    loop_data,
    loop_level_weight,
    loop_mode_mapping,
    loop_type_weight,
    loops,
    # 指标定义管理（指标配置-指标定义 Tab：CRUD + 版本化）
    metric_definition,
    # 监控模块：关注队列（整改方案 §8.1）
    monitor,
    node_performance,
    outlier_config,
    performance,
    plant_nodes,
    realtime,
    reports,
    tags,
    tuning,  # 整定模块（09 设计方案恢复为一级模块）
    users,
    weight_config,
    ws_alert,
    ws_realtime,
)
from app.api.v1.endpoints import (
    tasks as eval_tasks,
)
from app.core.config import settings
from app.core.db import dispose_engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.core.metrics import MetricsMiddleware, setup_metrics
from app.core.redis import close_redis
from app.middleware.idempotency import IdempotencyMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware

logger = get_logger(__name__)

# Celery Beat 子进程引用（lifespan 管理）
_celery_beat_process: subprocess.Popen | None = None

# Celery Worker 子进程引用（lifespan 管理）
_celery_worker_process: subprocess.Popen | None = None

# Beat / Worker 日志文件句柄（lifespan 管理，停止时关闭，避免 fd 泄漏）
_celery_beat_log_handle: TextIO | None = None
_celery_worker_log_handle: TextIO | None = None

# 项目级隔离标识：从 main.py 所在路径推导项目根目录 basename（如 CLPM-MVP），
# 作为 pgrep 的唯一匹配标记，避免同机多个 CLPM 项目（如原项目 CLPM 与 MVP 项目
# CLPM-MVP）的 celery 进程互相误杀。
# 边界锚定消除 basename 互为前缀（CLPM 是 CLPM-MVP 前缀）的子串误匹配：
#  - worker 支持 --hostname，命令行含 {tag}@%h，pattern 锚定 "tag@"
#  - beat 不支持 --hostname，靠 --pidfile 路径含 {tag}/，pattern 锚定 "tag/"
# CLPM@/CLPM/ 与 CLPM-MVP@/CLPM-MVP/ 互斥，双向不误匹配。
_PROJECT_TAG = os.path.basename(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_PROJECT_TAG_RE = re.escape(_PROJECT_TAG)
# pgrep 匹配特征：必须同时包含本项目 Celery 应用入口（-A app.tasks.celery_app）
# 和项目唯一标识，避免误匹配本机其他项目的 celery 进程导致误判/误杀
_BEAT_PGREP_PATTERN = rf"celery.*-A app\.tasks\.celery_app.*beat.*{_PROJECT_TAG_RE}/"
_WORKER_PGREP_PATTERN = rf"celery.*-A app\.tasks\.celery_app.*worker.*{_PROJECT_TAG_RE}@"

# 看门狗：worker/beat 进程探活周期（秒）。仅告警不自动拉起，
# 避免与 _start_celery_* 的单例防护冲突（多实例并发拉起）
_CELERY_WATCHDOG_INTERVAL = 60

# 兜底全局清理：发 SIGTERM 后等待的秒数，超时仍存活再 SIGKILL
_CELERY_TERM_TIMEOUT_S = 10
_CELERY_KILL_TIMEOUT_S = 5


def _pgrep_pids(pattern: str) -> list[int]:
    """pgrep -f pattern，返回匹配 PID 列表（空列表=无匹配或 pgrep 不可用）。"""
    try:
        result = subprocess.run(  # noqa: S603
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:  # noqa: BLE001
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def _pid_alive(pid: int) -> bool:
    """检查 PID 是否仍存活（通过 kill(pid, 0)，不含权限/状态语义）。

    注意：已被 SIGKILL、但父进程尚未执行 wait() 回收的 zombie <defunct> 进
    程 kill(pid,0) 仍返回 True（macOS 实测）。调用方在自己发出 SIGKILL
    后，应避免再用本函数作为"是否成功清理"的最终依据。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但当前用户无权限发信号：视为存活
        return True
    except OSError:
        return False
    return True


def _wait_pids_gone(pids: list[int], timeout_s: float) -> list[int]:
    """轮询等待一组 PID 全部退出。返回到 timeout 仍存活的 PID 列表。"""
    deadline = time.monotonic() + timeout_s
    remaining = [p for p in pids if _pid_alive(p)]
    while remaining and time.monotonic() < deadline:
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        remaining = [p for p in remaining if _pid_alive(p)]
    return remaining


def _terminate_pids_fallback(
    pids: list[int],
    label: str,
    *,
    term_timeout_s: float = _CELERY_TERM_TIMEOUT_S,
    kill_timeout_s: float = _CELERY_KILL_TIMEOUT_S,
) -> int:
    """兜底全局清理：对指定 PID 列表执行 SIGTERM → 等待 → SIGKILL 流程。
    用于 shutdown 时 lifespan 没自己 spawn 过进程（被单例防护跳过），
    但系统里仍有同项目 pgrep 匹配的 celery 进程（历史遗留/手工启动）。
    返回最终实际停止的进程数（用于日志）。"""
    pids_alive = [p for p in pids if _pid_alive(p)]
    if not pids_alive:
        return 0
    total = len(pids_alive)
    logger.info(
        "全局清理 %s：检测到 %d 个遗留进程(PID=%s)，发送 SIGTERM 并最多等待 %ds…",
        label,
        total,
        ",".join(str(p) for p in pids_alive),
        term_timeout_s,
    )
    for pid in pids_alive:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    survivors = _wait_pids_gone(pids_alive, term_timeout_s)
    if survivors:
        logger.warning(
            "全局清理 %s：%d/%d 个进程 SIGTERM %ds 后仍存活(PID=%s)，发送 SIGKILL…",
            label,
            len(survivors),
            total,
            term_timeout_s,
            ",".join(str(p) for p in survivors),
        )
        for pid in survivors:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        # 发过 SIGKILL 后不再轮询等待：SIGKILL 是不可屏蔽信号，进
        # 程必然被内核终止；zombie 态的 kill(pid,0) 在 mac 上仍返回
        # True，会造成"仍存活请手工清理"假阳性错误日志。最多 sleep
        # 一小段给内核处理动作窗口即可，不做严格 alive 判据。
        time.sleep(min(1.0, kill_timeout_s))
    stopped = total
    logger.info(
        "全局清理 %s：已对 %d/%d 个遗留进程执行停止（SIGTERM→SIGKILL）", label, stopped, total
    )
    return stopped


def _kill_process_group(process: subprocess.Popen, label: str) -> None:
    """停止 Popen 启动的进程：优先 killpg 整个进程组，保证 prefork 子进程
    （celery worker --concurrency N）和 master 一同退出；如果进程组不可
    用（pid 非进程组组长），退回单独 terminate/kill。

    start_new_session=True 已保证 `process.pid == getsid(process.pid)`，
    即新 spawn 的进程自身就是 session/group leader，所以 killpg 会带走
    master + 所有 prefork。"""
    try:
        pgid = os.getpgid(process.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    if pgid == process.pid:
        # 进程存在且自己是组长（start_new_session 保证）→ 杀进程组
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pgid = None  # 退回单进程路径

    if pgid == process.pid:
        # 进程组信号已发出，等待整个组退出
        deadline = time.monotonic() + _CELERY_TERM_TIMEOUT_S
        while _pid_alive(process.pid) and time.monotonic() < deadline:
            time.sleep(0.25)
        if _pid_alive(process.pid):
            # 仍有存活 → SIGKILL 组
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            deadline = time.monotonic() + _CELERY_KILL_TIMEOUT_S
            while _pid_alive(process.pid) and time.monotonic() < deadline:
                time.sleep(0.25)
        # 最后同步 Popen returncode，避免僵尸
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    else:
        # 兜底：非进程组模式（老代码/异常路径），用 Popen 原生
        process.terminate()
        try:
            process.wait(timeout=_CELERY_TERM_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=_CELERY_KILL_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                pass
    logger.info("%s 已停止（进程组=%s，PID=%s）", label, pgid or "-", process.pid)


def _is_production() -> bool:
    """判断当前是否为生产环境。

    生产环境由 docker-compose 独立 celery-beat 服务接管定时任务调度，
    backend lifespan 不再启动 Beat 子进程，避免重复执行。
    """
    return os.environ.get("ENV", "").lower() == "production"


def _is_test() -> bool:
    """判断当前是否为测试环境（pytest）。

    测试模式下跳过 Celery 子进程启动、DB 配置预载、实时订阅器启动，
    避免 TestClient lifespan fork 子进程和连接真实 PostgreSQL 导致 hang。
    conftest.py 设置 CLPM_TEST_MODE=1 激活此守卫。
    """
    return os.environ.get("CLPM_TEST_MODE", "").strip() in {"1", "true", "yes", "on"}


def _any_beat_process_running() -> bool:
    """pgrep 扫描是否已有 celery beat 进程在运行（Beat 单例兜底检查）.

    pidfile 检查无法覆盖"pidfile 被另一个 beat 进程覆盖/删除"的场景
    （如手工启动的 beat 与 lifespan 自动启动的 beat 共用同一路径）。
    """
    return bool(_pgrep_pids(_BEAT_PGREP_PATTERN))


def _start_celery_beat() -> None:
    """启动 Celery Beat 调度子进程。

    在 FastAPI lifespan 中调用，确保定时任务（如每小时 KPI 计算）
    随后端启动自动运行。Beat 进程独立于 Celery worker，仅负责
    按 schedule 发送任务到队列。

    PersistentScheduler 使用文件锁（celerybeat-schedule），
    即使多个 Beat 进程启动也只有一个能运行。
    """
    global _celery_beat_process, _celery_beat_log_handle, _celery_ever_touched
    # 进入启动路径即标记"本进程管过 celery"，以便后续 atexit 钩子允许兜底
    _celery_ever_touched = True

    # 检查是否已有 Beat 进程在运行（通过 celerybeat.pid 文件）
    # 将 pidfile 和 schedule 文件都放在 logs/ 目录下，避免在项目根目录写文件
    # 触发 uvicorn --reload 的文件监视导致循环重启
    os.makedirs("logs", exist_ok=True)
    pid_file = os.path.join(os.getcwd(), "logs", "celerybeat.pid")
    schedule_file = os.path.join(os.getcwd(), "logs", "celerybeat-schedule")
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)  # 检查进程是否存在
            logger.info("Celery Beat 已在运行 (PID=%s)，跳过启动", old_pid)
            return
        except (ProcessLookupError, ValueError, OSError):
            # 进程不存在，清理遗留的 PID 文件
            try:
                os.remove(pid_file)
            except OSError:
                pass

    # 兜底：pidfile 可能被手工启动的 beat 覆盖/失效（同一 pidfile 路径被
    # 两个 beat 共用时互相覆盖），用 pgrep 扫描确认无其他 celery beat
    # 进程在运行。两个 beat 并存会导致每个定时任务双触发（2026-07-20 实测）。
    if _any_beat_process_running():
        logger.info("检测到已有 Celery Beat 进程在运行（pgrep 兜底），跳过启动")
        return

    try:
        # stderr 合并到 stdout，单句柄减少 fd 占用；句柄存入模块级引用，
        # 由 _stop_celery_beat 关闭，避免 lifespan 重启泄漏 fd
        log_handle = open("logs/celery-beat.log", "a")  # noqa: SIM115
        # 注入宿主 PID：子进程看门狗据此监视宿主，宿主被 SIGKILL/崩溃时
        # 自行 SIGTERM 退出，防独立进程组孤儿滞留（app/tasks/parent_watchdog.py）
        child_env = {**os.environ, "CLPM_PARENT_PID": str(os.getpid())}
        try:
            _celery_beat_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "app.tasks.celery_app",
                    "beat",
                    "-l",
                    "info",
                    "--pidfile",
                    pid_file,
                    "--schedule",
                    schedule_file,
                ],
                cwd=os.getcwd(),
                env=child_env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                # 方案 B：新 session = 独立进程组，便于 shutdown 一次性
                # killpg 整个组（虽然 beat 没有 prefork，但与 worker 保持一致）
                start_new_session=True,
            )
        except Exception:
            log_handle.close()
            raise
        _celery_beat_log_handle = log_handle
        logger.info(
            "Celery Beat 调度进程已启动 (PID=%s)，定时任务将自动执行，日志: logs/celery-beat.log",
            _celery_beat_process.pid,
        )
    except Exception as exc:
        logger.warning("启动 Celery Beat 失败（定时任务将不会自动执行）: %s", exc)


def _stop_celery_beat() -> None:
    """停止 Celery Beat 调度子进程。

    停止策略（A+B 组合）：
    - 若本实例自己 spawn 过 beat（`_celery_beat_process` 非空）：优先
      killpg 整个 session 进程组，退回单进程 terminate/kill。
    - 若本实例未 spawn（被单例防护跳过启动），执行方案 A：对 pgrep 匹配
      的所有 beat 进程做 SIGTERM→等待→SIGKILL 兜底清理，防止"启动跳过→
      停止跳过"导致历史遗留僵尸在正常退出后仍然留存。
    """
    global _celery_beat_process, _celery_beat_log_handle, _celery_ever_touched
    _celery_ever_touched = True
    process = _celery_beat_process
    if process is not None:
        logger.info("停止 Celery Beat 调度进程...")
        _kill_process_group(process, "Celery Beat")
        _celery_beat_process = None

        # 只清理由当前 FastAPI 实例创建的 Beat PID 文件。
        # reload 子进程若只是检测到外部 Beat 并跳过启动，绝不能删除对方
        # 的 PID 文件，否则下一次 reload 会再启动一个重复 Beat。
        pid_file = os.path.join(os.getcwd(), "celerybeat.pid")
        try:
            with open(pid_file) as file:
                pid_from_file = int(file.read().strip())
            if pid_from_file == process.pid:
                os.remove(pid_file)
        except (FileNotFoundError, ProcessLookupError, ValueError, OSError):
            pass
    else:
        # 方案 A 兜底：无论本实例是否 spawn 过 beat，只要 pgrep 到同项目
        # 的遗留 beat，一律清理（可能是手工启动 / 前一次非优雅退出孤儿）
        leftover = _pgrep_pids(_BEAT_PGREP_PATTERN)
        if leftover:
            _terminate_pids_fallback(leftover, "Celery Beat（遗留）")
        else:
            logger.debug("无 lifespan 管理的 Beat 进程，且 pgrep 未发现遗留，跳过")

    # 关闭日志句柄（无论本次是否停止了进程，避免 lifespan 重启泄漏 fd）
    if _celery_beat_log_handle is not None:
        _celery_beat_log_handle.close()
        _celery_beat_log_handle = None


def _any_worker_process_running() -> bool:
    """pgrep 扫描是否已有 celery worker 进程在运行（单例兜底检查）。

    避免 lifespan 自动启动的 worker 与手工启动的 worker 并存，导致任务
    被重复消费（多 worker 竞争同一队列）。
    """
    return bool(_pgrep_pids(_WORKER_PGREP_PATTERN))


def _start_celery_worker() -> None:
    """启动 Celery Worker 子进程。

    在 FastAPI lifespan 中调用，确保手动触发的任务（历史数据导入、
    KPI 回算、自定义评估等）和 Beat 派发的定时任务都有 worker 执行。

    与 Beat 不同，worker 没有 pidfile，使用 pgrep 做单例检查。
    """
    global _celery_worker_process, _celery_worker_log_handle, _celery_ever_touched
    _celery_ever_touched = True

    if _any_worker_process_running():
        logger.info("检测到已有 Celery Worker 进程在运行，跳过启动")
        return

    try:
        os.makedirs("logs", exist_ok=True)
        # 同时消费 default 与 dead_letter 队列（S2-A6 死信由同一 worker 排查，
        # 否则死信无人消费永久堆积）；stderr 合并到 stdout，句柄由
        # _stop_celery_worker 关闭，避免 lifespan 重启泄漏 fd
        log_handle = open("logs/celery-worker.log", "a")  # noqa: SIM115
        # 注入宿主 PID：子进程看门狗据此监视宿主（同 _start_celery_beat）
        child_env = {**os.environ, "CLPM_PARENT_PID": str(os.getpid())}
        try:
            _celery_worker_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "app.tasks.celery_app",
                    "worker",
                    "-l",
                    "info",
                    "--concurrency",
                    "4",
                    "-Q",
                    "default,dead_letter",
                    "--hostname",
                    f"{_PROJECT_TAG}@%h",
                ],
                cwd=os.getcwd(),
                env=child_env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                # 方案 B：启动时新建 session，本 worker master 自动成为进程
                # 组组长，shutdown 时 killpg 一次带走 master + N prefork。
                # 这解决了原来只 terminate master 时 prefork 子进程可能
                # 不跟着退出、被 launchd 收养成孤儿的问题。
                start_new_session=True,
            )
        except Exception:
            log_handle.close()
            raise
        _celery_worker_log_handle = log_handle
        logger.info(
            "Celery Worker 进程已启动 (PID=%s)，任务队列开始消费，日志: logs/celery-worker.log",
            _celery_worker_process.pid,
        )
    except Exception as exc:
        logger.warning("启动 Celery Worker 失败（任务将不会自动执行）: %s", exc)


def _stop_celery_worker() -> None:
    """停止 Celery Worker 进程。

    停止策略（A+B 组合）：
    - 方案 B（本实例 spawn 的 worker）：用 session 进程组 killpg，
      保证 master + `--concurrency N` 个 prefork 子进程一起退出，避免
      只 terminate master 导致 prefork 被 init 收养后成孤儿。
    - 方案 A（本实例未 spawn 的遗留 worker）：启动时检测到 pgrep 匹
      配，走"跳过启动"路径 → 若 shutdown 时仍然存在遗留，则对所有
      匹配 PID 执行 SIGTERM→等待→SIGKILL 兜底清理。这彻底闭合了「前
      一次非优雅退出留下僵尸 → 下一次启动被单例防护跳过 → 下一次正
      常停止又跳过停止」的循环缺口。
    """
    global _celery_worker_process, _celery_worker_log_handle, _celery_ever_touched
    _celery_ever_touched = True
    process = _celery_worker_process
    if process is not None:
        logger.info("停止 Celery Worker 进程...")
        _kill_process_group(process, "Celery Worker")
        _celery_worker_process = None
    else:
        # 方案 A 兜底：即使启动时跳过 spawn（因为 pgrep 到遗留），
        # shutdown 也必须把遗留全带走，避免层层累加
        leftover = _pgrep_pids(_WORKER_PGREP_PATTERN)
        if leftover:
            _terminate_pids_fallback(leftover, "Celery Worker（遗留）")
        else:
            logger.debug("无 lifespan 管理的 Worker 进程，且 pgrep 未发现遗留，跳过")

    # 关闭日志句柄（无论本次是否停止了进程，避免 lifespan 重启泄漏 fd）
    if _celery_worker_log_handle is not None:
        _celery_worker_log_handle.close()
        _celery_worker_log_handle = None


async def _celery_watchdog_check() -> None:
    """单次探活：worker/beat 任一缺失时自动补拉起。

    v6.2 升级（2026-08-18）：从"仅告警"升级为"探活缺失→自动补拉起"。
    动机：uvicorn --reload 热重载存在时序竞态——旧 reload worker 退出时
    清理了自己的 Celery 子进程，而新 worker 启动瞬间 pgrep 仍能扫到
    旧进程（尚未死透）→ 单例防护跳过启动 → Celery 全空停摆，只能重启
    后端恢复。自动补拉起形成自愈闭环。

    安全性：补拉起走 _start_celery_*，其单例防护（pidfile/pgrep）本身
    就是防重复的兜底——若进程确实存在则拉起被跳过，不存在重复拉起风险。

    探活前先 reap 本实例 spawn 的子进程（poll）：子进程被 SIGKILL 后
    若不 reap 会以 <defunct> 僵尸态滞留 PID 表，pgrep 仍匹配其 cmdline
    → 误判"进程在位"→ 永不补拉起（2026-08-18 实测）。
    """
    for proc in (_celery_beat_process, _celery_worker_process):
        if proc is not None:
            try:
                proc.poll()  # 非阻塞 reap：已死则回收僵尸，活着无副作用
            except Exception:  # noqa: BLE001
                pass
    if not await asyncio.to_thread(_any_beat_process_running):
        logger.warning("看门狗：未检测到 Celery Beat，自动补拉起")
        await asyncio.to_thread(_start_celery_beat)
    if not await asyncio.to_thread(_any_worker_process_running):
        logger.warning("看门狗：未检测到 Celery Worker，自动补拉起")
        await asyncio.to_thread(_start_celery_worker)


async def _celery_watchdog_loop(stop_event: asyncio.Event) -> None:
    """周期性探活 worker/beat 进程，直至 stop_event 置位（lifespan 关闭）。"""
    while not stop_event.is_set():
        # 先等待一个周期再检查：启动初期 worker/beat 可能尚在拉起中
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_CELERY_WATCHDOG_INTERVAL)
        except TimeoutError:
            pass
        if stop_event.is_set():
            break
        try:
            await _celery_watchdog_check()
        except Exception:  # noqa: BLE001
            logger.exception("看门狗探活执行失败")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialise resources on startup, clean up on shutdown."""
    setup_logging()
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    logger.info("数据源: 计算=本地 TDengine（性能评估/诊断/整定），远端 API 仅历史数据导入任务调用")

    # 测试模式守卫：跳过 Celery 子进程启动、DB 配置预载、实时订阅器启动，
    # 避免 TestClient lifespan fork 子进程和连接真实 PostgreSQL 导致 hang。
    # conftest.py 通过 CLPM_TEST_MODE=1 激活此守卫，测试使用 mock DB + FakeRedis。
    if _is_test():
        logger.info("测试模式：跳过 Celery/DB预载/订阅器启动")
        yield
        logger.info("测试模式：跳过 Celery/订阅器停止")
        return

    # v6.1：自动启动 Celery Beat 调度进程和 Celery Worker 任务执行进程
    # 生产环境由 docker-compose 独立 celery-beat / celery-worker 容器接管，避免重复启动
    watchdog_stop: asyncio.Event | None = None
    watchdog_task: asyncio.Task[None] | None = None
    if not _is_production():
        _start_celery_beat()
        _start_celery_worker()
        # 看门狗：定期探活 worker/beat 进程，崩溃缺失时 error 级告警
        # （仅告警不自动拉起，避免与单例防护冲突）
        watchdog_stop = asyncio.Event()
        watchdog_task = asyncio.create_task(_celery_watchdog_loop(watchdog_stop))
    else:
        logger.info("生产环境：Celery Beat / Worker 由独立容器接管，跳过 lifespan 启动")

    # 从 sys_config 预载数据源配置到 settings（运行时真相源优先于 .env）
    # 方案 B：.env 仅保留基础设施配置 + 合理默认值，业务 URL/Token/SignalR Hub
    # 等由 sys_config 管理。预载确保 SignalR 订阅器等启动时组件读取到 sys_config
    # 中的配置，而不是 .env 中的空值。预载失败不应阻塞启动，兜底使用 .env 默认值。
    from app.core.db import AsyncSessionLocal
    from app.services.datasource_config import preload_datasource_config

    try:
        async with AsyncSessionLocal() as db:
            await preload_datasource_config(db)
        logger.info(
            "数据源配置已从 sys_config 预载（network_mode=%s, signalr_enabled=%s, "
            "history_api_url=%s, signalr_hub_url=%s）",
            settings.NETWORK_MODE,
            settings.SIGNALR_ENABLED,
            "已配置" if settings.HISTORY_DATA_API_URL else "未配置",
            "已配置" if settings.SIGNALR_HUB_URL else "未配置",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("从 sys_config 预载数据源配置失败（将使用 .env 默认值）: %s", exc)

    # 从 sys_config 预载异常值检测参数/开关到进程内缓存（热路径不查库，
    # 预载失败回落 thresholds.py 算法默认值，不阻塞启动）
    from app.services.preprocessing.outlier_params import preload_outlier_params

    try:
        async with AsyncSessionLocal() as db:
            await preload_outlier_params(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("从 sys_config 预载异常值检测参数失败（将使用算法默认值）: %s", exc)

    # P0-B: 从 algorithm_parameter 表预载指标算法参数到进程内缓存
    # 预载失败回落算法默认值，不阻塞启动
    from app.services.algorithm_config import preload_algorithm_params

    try:
        async with AsyncSessionLocal() as db:
            await preload_algorithm_params(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("预载指标算法参数失败（将使用算法默认值）: %s", exc)

    # 从 sys_config 预载诊断触发条件到进程内缓存（整改计划 C6，
    # 预载失败回落默认值 score_threshold=60/concurrency=5/min_data_points=32，不阻塞启动）
    # MVP 精简：已屏蔽诊断模块 → 跳过诊断触发条件预载
    # from app.services.diagnosis_trigger_config import preload_diagnosis_trigger
    #
    # try:
    #     async with AsyncSessionLocal() as db:
    #         await preload_diagnosis_trigger(db)
    # except Exception as exc:  # noqa: BLE001
    #     logger.warning("从 sys_config 预载诊断触发条件失败（将使用默认值）: %s", exc)

    # 预载诊断专家规则到进程内缓存（整改计划 C2，
    # 预载失败回退到空列表，触发 _diagnose_loop 硬编码规则兜底，不阻塞启动）
    # MVP 精简：已屏蔽诊断模块 → 跳过诊断专家规则预载
    # from app.services.diagnosis_rule import preload_rules
    #
    # try:
    #     async with AsyncSessionLocal() as db:
    #         await preload_rules(db)
    # except Exception as exc:  # noqa: BLE001
    #     logger.warning("预载诊断专家规则失败（将回退到硬编码规则）: %s", exc)

    # 可信度统一 Phase 3（P3-2 / D4）：预载可信度阈值 + 启动 pub/sub 订阅线程
    # 预载失败回落算法默认值，不阻塞启动；订阅线程确保运行时阈值变更实时同步
    from app.services.confidence_evaluator import (
        load_thresholds_from_db,
        start_threshold_subscriber,
    )

    try:
        async with AsyncSessionLocal() as db:
            await load_thresholds_from_db(db)
        start_threshold_subscriber()
    except Exception as exc:  # noqa: BLE001
        logger.warning("预载可信度阈值失败（将使用算法默认值）: %s", exc)

    # 启动实时数据订阅（如已启用）
    from app.services.data_source.realtime_subscriber import start_subscriber

    await start_subscriber()

    yield

    logger.info("Shutting down %s", settings.APP_NAME)

    # 停止看门狗、Celery Worker 和 Beat
    if not _is_production():
        if watchdog_stop is not None and watchdog_task is not None:
            watchdog_stop.set()
            try:
                await asyncio.wait_for(watchdog_task, timeout=5)
            except TimeoutError:
                watchdog_task.cancel()
        _stop_celery_worker()
        _stop_celery_beat()
        # 置位幂等标记：lifespan 已经走过一遍 stop，后续 atexit/signal 不
        # 需再跑，避免无意义的二次 pgrep 空转 + 防止与 SIGTERM handler
        # 双触发重叠。
        global _celery_shutdown_done  # noqa: PLW0603
        _celery_shutdown_done = True

    # 停止实时数据订阅
    from app.services.data_source.realtime_subscriber import stop_subscriber

    await stop_subscriber()

    # 关闭数据源 Provider
    from app.services.data_source.factory import close_provider

    await close_provider()

    await dispose_engine()
    await close_redis()


# ---------------------------------------------------------------------------
# 进程退出兜底（A+B+C 保险）：atexit + SIGTERM/SIGINT 双钩子
#
# 为什么需要这层：uvicorn --reload 模式下，SIGTERM 发给 watcher 时 watcher
# 会向 reload worker 发送自定义的 reload 信号（不是完整 shutdown 语义，
# lifespanshutdown 代码段不一定会跑），导致 Celery 子进程被 launchd 收
# 养成孤儿。atexit + signal handler 双钩子从解释器层面拦截任何退出路径：
#   · 正常 Python 退出（sys.exit / 未捕获异常） → atexit
#   · 热重载 reload worker 退出 → atexit（worker 解释器正常退出）
#   · 被 TERM / INT 信号终止 → signal handler 立即执行（比 atexit 更早、
#     在 uvicorn 默认 handler chain 之前）
#
# 幂等保护：一旦 _shutdown_celery_once() 被调用过一次，后续钩子直接
# return，避免信号→atexit 双触发时同一份清理跑两次。
# ---------------------------------------------------------------------------
_celery_shutdown_done = False


def _should_skip_exit_hooks() -> bool:
    """判断当前 Python 进程是否应当跳过 atexit/signal 级 celery 清理。

    命中以下任一直接跳过（不做任何进程级信号动作）：
    1) 显式环境变量 CLPM_SKIP_EXIT_HOOKS=1（pytest conftest / CI 可设置）
    2) pytest / coverage / hypothesis / sphinx-build / mypy / ruff / alembic
       等离线工具入口：它们只是「import app.main 拿配置」，从未真正启动
       或需要清理 celery，对它们发信号会误伤宿主机正在运行的同项目
       celery 服务实例（实测 pytest 退出 atexit 会真的 SIGKILL 工作进程）。
    3) 进程没有被 lifespan（或等价入口）"实际 touch 过 celery"。

    注意：不能简单按 argv 包含 'uvicorn' 跳过 —— 真实的后端 reload worker
    进程的 argv 就是完整的 uvicorn 命令，它恰恰需要钩子。是否真正需要
    清理交给下游 _shutdown_celery_once() 通过 `_celery_ever_touched`
    再二次判。
    """
    if os.environ.get("CLPM_SKIP_EXIT_HOOKS", "").strip() in {"1", "true", "yes", "on"}:
        return True

    argv_str = " ".join(sys.argv).lower()
    skip_tokens = (
        "pytest",
        "py.test",
        "coverage",
        "hypothesis",
        "sphinx-build",
        "mypy",
        "ruff",
        "alembic",
    )
    for tok in skip_tokens:
        if tok in argv_str:
            return True
    return False


# 本进程是否"实际接触过 celery 生命周期"：只要 _start_celery_* 被调用
# （无论实际是否 spawn）或停止时兜底发现遗留，都标记为 True。用于 atexit
# 钩子在"纯工具导入 app.main"场景下连 pgrep 都不做（不产生任何可观测
# 副作用）。
_celery_ever_touched = False


def _shutdown_celery_once() -> None:
    """Celery 进程幂等清理：所有退出路径（lifespan / atexit / signal）统一入口。"""
    global _celery_shutdown_done
    if _celery_shutdown_done:
        return
    _celery_shutdown_done = True

    # 工具进程守卫（2026-08-18 修复：此前 _should_skip_exit_hooks 是死代码
    # 从未被调用，pytest 退出时 atexit 真的 SIGKILL 了宿主机正在运行的
    # 生产 Celery——conftest 虽设 CLPM_SKIP_EXIT_HOOKS=1 但没人读它）
    if _should_skip_exit_hooks():
        return

    if _is_production():
        # 生产：celery-beat / celery-worker 由独立容器接管，禁止本进程清理
        return
    if not _celery_ever_touched:
        # 本进程从未进入任何 celery 启动/停止路径（通常是工具导入），
        # 完全跳过 pgrep + 信号动作，避免 pytest 退出时对宿主机运行中的
        # celery 产生副作用。
        return

    try:
        # 先 worker 再 beat（停止与启动逆序，worker 先退避免 beat 再派任务）
        _stop_celery_worker()
        _stop_celery_beat()
    except Exception:  # noqa: BLE001
        logger.exception("Celery 退出兜底清理执行异常（忽略，继续让解释器退出）")


def _register_exit_hooks() -> None:
    """注册 atexit + SIGTERM/SIGINT 钩子（模块级调用一次即可，重复调用无副作用）。"""
    if _is_production():
        return

    # atexit：任何 Python 解释器正常退出都会触发
    atexit.register(_shutdown_celery_once)

    # SIGTERM/SIGINT：拦截 shell/uvicorn/IDE 发来的进程终止信号
    # 保存旧 handler，调用完清理再把信号转发给旧 handler（uvicorn 默认 handler
    # 仍能执行 Shutdown error / lifespan shutdown，不与我们的兜底互斥）
    def _sigterm_handler(signum: int, _frame: object) -> None:
        try:
            _shutdown_celery_once()
        finally:
            # 还原为默认并重新发送同一个信号，让 uvicorn / reload watcher
            # 执行自己的 Shutdown flow。如果之前没有注册 handler，就走
            # 默认行为（SIG_DFL → 终止）。
            try:
                prev = signal.getsignal(signum)
                signal.signal(signum, signal.SIG_DFL)
                if prev not in (signal.SIG_DFL, signal.SIG_IGN, None):
                    os.kill(os.getpid(), signum)
                else:
                    os.kill(os.getpid(), signum)
            except (ProcessLookupError, OSError, ValueError):
                # 已在终止流程，忽略
                pass

    try:
        signal.signal(signal.SIGTERM, _sigterm_handler)
    except (ValueError, OSError):
        # 非主线程 / 非前台组 注册不了，退回 atexit 兜底
        pass
    try:
        signal.signal(signal.SIGINT, _sigterm_handler)
    except (ValueError, OSError):
        pass


# 模块加载即注册（FastAPI app factory / celery worker / celery beat 任何
# 一个入口 import app.main 时都会完成注册）。生产环境由 _is_production
# 守卫跳过。
_register_exit_hooks()


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Control Loop Performance Monitoring backend API",
        debug=settings.DEBUG,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "Idempotency-Key"],
    )
    # S2-C5: 敏感端点速率限制
    app.add_middleware(RateLimitMiddleware)
    # S2-C6: 写操作幂等性（在限流之后，缓存命中时跳过限流）
    app.add_middleware(IdempotencyMiddleware)
    # S3-B3: Prometheus 指标采集中间件
    app.add_middleware(MetricsMiddleware)
    # S3-B4: request_id 请求追踪（最外层，最先执行）
    app.add_middleware(RequestIdMiddleware)

    # S3-B3: 挂载 /metrics 端点
    setup_metrics(app)

    register_exception_handlers(app)

    # Health probe at root (no business prefix) for k8s/container probes.
    app.include_router(health.router)

    # Business endpoints under /api/v1.
    from fastapi import APIRouter

    v1_router = APIRouter(prefix="/api/v1")
    v1_router.include_router(auth.router)
    v1_router.include_router(plant_nodes.router)
    v1_router.include_router(loops.router)
    # Phase 3: 回路数据管理（历史数据导入）
    v1_router.include_router(loop_data.router)
    v1_router.include_router(tags.router)
    # MVP 精简：已屏蔽 AAS/OPC UA 同步模块 → 不挂载 aas.router
    # v1_router.include_router(aas.router)
    v1_router.include_router(datasource.router)
    v1_router.include_router(performance.router)
    # S3-METRIC 节点级性能评估（GB/T 44693.2-2024 §6.4 综合评估）
    v1_router.include_router(node_performance.router)
    # S6 工作台门户：BFF 聚合层
    v1_router.include_router(dashboard.router)
    # S4 诊断中心：诊断、波形、Tracker、诊断标签
    # v4.0: tags_router 须在 diagnosis.router 之前注册，避免 GET /{loop_id} 拦截 /diagnosis/tags
    # MVP 精简：已屏蔽旧诊断模块 → 不挂载所有 diagnosis.*_router
    # v1_router.include_router(diagnosis.tags_router)
    # v1_router.include_router(diagnosis.router)
    # v1_router.include_router(diagnosis.timeseries_router)
    v1_router.include_router(tags.timeseries_router)
    # v1_router.include_router(diagnosis.tracker_router)
    # MVP v2 诊断模块（重设计版：/diagnosis/run|runs|operators|export）
    v1_router.include_router(diagnosis_v2.router)
    # 处置模块 Phase 1（/handling/items/* 流转端点，08-处置模块设计方案 §6.2）
    v1_router.include_router(handling.router)
    # v4.0: DataPlanner 内部管理接口（仅 ADMIN）
    v1_router.include_router(dataplanner.router)
    # v4.0: 算法服务接口（IDS §2.7）
    v1_router.include_router(algorithms.router)
    # 智能预警规则引擎（PRD v6.2 §4.4.6）
    v1_router.include_router(alert.router)
    # 监控模块：关注队列（整改方案 §8.1）
    v1_router.include_router(monitor.router)
    # v4.0: 批量配置接口（IDS §2.8/§2.9）
    v1_router.include_router(configs.router)
    # v5.3: 权重模板管理（FDS §5.2.2）+ 定级阈值管理（FDS §5.2.4）
    v1_router.include_router(weight_config.router)
    v1_router.include_router(grading_config.router)
    # 指标定义管理（指标配置-指标定义 Tab：CRUD + 版本化）
    v1_router.include_router(metric_definition.router)
    # 工厂模型 AAS 同步（工厂配置页）
    v1_router.include_router(factory_sync.router)
    # v6.1: 数据可信度阈值管理
    v1_router.include_router(confidence_config.router)
    # P3-04: LLM 配置（AI 洞察门禁读取；endpoint 独立，不依赖诊断模块）
    v1_router.include_router(llm_config.router)
    v1_router.include_router(ai_insight.router)  # AI 洞察通用服务（4 场景统一入口）

    # v6.2: 8 类异常值检测参数与启停开关配置
    v1_router.include_router(outlier_config.router)
    # P0-B: 算法参数配置
    v1_router.include_router(algorithm_config.router)
    # MVP 精简：已屏蔽诊断模块 → 不挂载 diagnosis_trigger_config.router
    # v1_router.include_router(diagnosis_trigger_config.router)
    # v4.0: 评估任务管理（标准/自定义）
    v1_router.include_router(eval_tasks.router)
    # S5 系统管理：用户管理、审计日志、报表配置
    v1_router.include_router(users.router)
    v1_router.include_router(audit_logs.router)
    v1_router.include_router(reports.router)
    # S7 回路整定：模型辨识、PID 整定、闭环仿真（09 设计方案恢复为一级模块）
    v1_router.include_router(tuning.router)
    # 重构方案 v1.2：回路配置 CRUD（投用定义、类型权重、级别权重）
    v1_router.include_router(loop_mode_mapping.router)
    v1_router.include_router(loop_type_weight.router)
    v1_router.include_router(loop_level_weight.router)
    # v6.1：DCS 配置管理（品牌/型号/MODE 定义/映射矩阵）
    v1_router.include_router(dcs.router)
    # 实时数据查询（从 Redis 缓存读取 SignalR 订阅数据）
    v1_router.include_router(realtime.router)
    v1_router.include_router(ws_realtime.router)
    v1_router.include_router(ws_alert.router)  # 预警实时推送
    app.include_router(v1_router)

    return app


app = create_app()
