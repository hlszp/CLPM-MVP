#!/usr/bin/env python3
"""验证可信度统一 Phase 2/3：阈值多进程同步 + INCONCLUSIVE 告警（D5）.

本脚本用**真实 Redis（db 0，与运行中的后端共用）**端到端验证：
  1. 阈值多进程同步：父进程 broadcast_thresholds → 子进程订阅线程经 Redis
     pub/sub 收到 → 本地缓存更新
  2. 旧版本号消息去重：父进程发布过期版本消息 → 子进程跳过（版本号不回退）
  3. 濒临 INCONCLUSIVE 告警（D5）：valid_rate ∈ [D, D+0.10) 时触发 WARN

两种运行模式：
- 父进程（默认）：编排验证流程，捕获基线阈值、广播、验证子进程同步、
  发布旧版本消息、测试告警、恢复基线。
- 子进程（``worker``）：启动真实订阅守护线程，接收 Redis pub/sub 广播，
  周期性上报本地阈值状态，最后运行告警自检。

用法::

    cd backend && uv run python scripts/verify_confidence_phase23.py

设计要点：
- 走真实 Redis（localhost:7103/db0），与运行中的后端共享频道
  ``confidence:thresholds:updated``。后端的订阅线程也会收到广播——
  因此脚本结束前会广播"基线阈值"把后端在内存中的缓存恢复到原始值。
- 子进程是独立 OS 进程，pid 不同，便于在日志中核对多进程消息传递。
- 详细日志带 ``[confidence-sync pid=X]`` 前缀，已在
  ``app/services/confidence_evaluator.py`` 的核心分支预埋。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

BACKEND_URL = os.environ.get("CLPM_BACKEND_URL", "http://127.0.0.1:7101")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "7103"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))

#: 验证用的测试阈值（与算法默认不同，便于确认"已更新"）
TEST_THRESHOLDS: dict[str, float] = {"A": 0.92, "B": 0.72, "C": 0.52, "D": 0.12}

ADMIN_USER = os.environ.get("CLPM_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("CLPM_ADMIN_PASS", "admin123")

#: 子进程订阅就绪后的接收窗口（秒）
CHILD_WINDOW_SECONDS = 8
#: 子进程启动后等待订阅线程连上 Redis 的时长（秒）
CHILD_WARMUP_SECONDS = 2

logger = logging.getLogger("verify_confidence_phase23")


# ---------------------------------------------------------------------------
# 子进程模式：模拟 Celery worker
# ---------------------------------------------------------------------------


class _RecordTracker(logging.Handler):
    """记录日志消息，供子进程判断"是否收到旧版本消息被跳过"等事件."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())


def run_worker() -> None:
    """子进程入口：启动订阅线程，周期上报状态，最后告警自检."""
    # 日志直接打到 stdout，父进程可见（带 pid 便于核对多进程）
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    tracker = _RecordTracker()
    logging.getLogger("app.services.confidence_evaluator").addHandler(tracker)

    # 延迟导入，确保 logging 先配置好
    import app.services.confidence_evaluator as ce
    from app.services.confidence_evaluator import (
        DEFAULT_CONFIDENCE_THRESHOLDS,
        ConfidenceEvaluator,
        start_threshold_subscriber,
    )

    # 重置本进程缓存为算法默认值、版本号归零（模拟刚启动的 worker）
    ce._threshold_cache = dict(DEFAULT_CONFIDENCE_THRESHOLDS)
    ce._threshold_version = 0

    print(
        f"[child pid={os.getpid()}] 启动订阅守护线程...",
        flush=True,
    )
    start_threshold_subscriber()

    # 等待订阅线程连上 Redis 并完成 subscribe
    time.sleep(CHILD_WARMUP_SECONDS)
    print(f"READY pid={os.getpid()}", flush=True)

    deadline = time.time() + CHILD_WINDOW_SECONDS
    while time.time() < deadline:
        thresholds = ConfidenceEvaluator.get_thresholds()
        version = ConfidenceEvaluator.get_threshold_version()
        stale_skipped = any("跳过旧版本消息" in m for m in tracker.records)
        applied = any("收到阈值更新广播并已应用" in m for m in tracker.records)
        print(
            "STATE "
            + json.dumps(
                {
                    "pid": os.getpid(),
                    "thresholds": thresholds,
                    "version": version,
                    "applied": applied,
                    "stale_skipped": stale_skipped,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        tracker.records.clear()
        time.sleep(0.5)

    # 告警自检：用当前（已被广播同步的）阈值测试 D5 告警区间
    # TEST_THRESHOLDS.D=0.12 → 告警区间 [0.12, 0.22)
    alert_in_zone = _check_alert(0.15)  # 在 [0.12, 0.22) → 应告警
    alert_above = _check_alert(0.30)  # 不在 → 不告警
    print(
        "RESULT "
        + json.dumps(
            {
                "pid": os.getpid(),
                "final_thresholds": ConfidenceEvaluator.get_thresholds(),
                "final_version": ConfidenceEvaluator.get_threshold_version(),
                "alert_in_zone_warned": alert_in_zone,
                "alert_above_warned": alert_above,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    print(f"[child pid={os.getpid()}] 完成，退出", flush=True)


def _check_alert(valid_rate: float) -> bool:
    """在当前进程内调用 evaluate 并捕获是否触发濒临 INCONCLUSIVE 告警."""
    from app.services.confidence_evaluator import ConfidenceEvaluator

    captured: list[bool] = []

    class _Probe(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "濒临 INCONCLUSIVE" in record.getMessage():
                captured.append(True)

    probe = _Probe()
    lg = logging.getLogger("app.services.confidence_evaluator")
    lg.addHandler(probe)
    try:
        ConfidenceEvaluator.evaluate(valid_rate)
    finally:
        lg.removeHandler(probe)
    return bool(captured)


# ---------------------------------------------------------------------------
# 父进程模式：编排验证
# ---------------------------------------------------------------------------


def _login_token() -> str:
    """登录后端获取 accessToken."""
    import httpx

    resp = httpx.post(
        f"{BACKEND_URL}/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload["data"]["accessToken"]


def _get_thresholds_via_api(token: str) -> dict[str, float]:
    """GET /configs/confidence-thresholds → {A: minRate, B: ...}（基线捕获）."""
    import httpx

    resp = httpx.get(
        f"{BACKEND_URL}/api/v1/configs/confidence-thresholds",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return {item["name"]: float(item["minRate"]) for item in data["thresholds"]}


def _publish_stale_message(version: int) -> None:
    """直接用同步 Redis 客户端发布一条旧版本号消息（触发子进程"跳过"分支）."""
    import redis as sync_redis

    client = sync_redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
    message = json.dumps(
        {
            "version": version,
            "thresholds": {"A": 0.10, "B": 0.08, "C": 0.05, "D": 0.01},
            "updated_at": "2026-08-04T00:00:00+00:00",
            "source": "verify-stale-probe",
        },
        ensure_ascii=False,
    )
    n = client.publish("confidence:thresholds:updated", message)
    client.close()
    print(f"[parent] 已发布旧版本消息 version={version}，投递订阅者数={n}")


async def _broadcast(thresholds: dict[str, float], source: str) -> int:
    """调用真实 broadcast_thresholds（Redis INCR + PUBLISH，db 0）."""
    from app.services.confidence_evaluator import broadcast_thresholds

    return await broadcast_thresholds(thresholds, source=source)


def _parent_alert_check() -> dict[str, bool]:
    """父进程内用算法默认阈值测试 D5 告警（默认 D=0.20，告警区间 [0.20, 0.30)）."""
    from app.services.confidence_evaluator import ConfidenceEvaluator

    ConfidenceEvaluator.set_thresholds(None)  # 重置为默认
    return {
        "valid_rate_0.25_warned": _check_alert(0.25),  # 在 [0.20,0.30) → 应告警
        "valid_rate_0.50_warned": _check_alert(0.50),  # 不在 → 不告警
        "valid_rate_0.15_warned": _check_alert(0.15),  # < D → 不告警
    }


async def run_parent() -> int:
    """父进程编排：基线 → 广播 → 验证同步 → 旧版本去重 → 告警 → 恢复."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )

    results: dict[str, Any] = {}
    baseline: dict[str, float] | None = None
    token: str | None = None
    child: subprocess.Popen | None = None

    try:
        # 1. 捕获后端当前基线阈值（用于结束恢复，避免干扰运行中的后端）
        try:
            token = _login_token()
            baseline = _get_thresholds_via_api(token)
            print(f"[parent] 后端基线阈值（GET /configs/confidence-thresholds）: {baseline}")
        except Exception as exc:  # noqa: BLE001
            print(f"[parent] 警告：捕获基线失败，将用算法默认值恢复: {exc}")
            baseline = None

        # 2. 启动子进程（模拟 worker）
        child = subprocess.Popen(
            [
                "uv",
                "run",
                "python",
                "scripts/verify_confidence_phase23.py",
                "worker",
            ],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        print(f"[parent] 子进程已启动 pid={child.pid}")

        # 3. 等待 READY
        ready = False
        state_lines: list[dict] = []
        result_line: dict | None = None

        def _read_line() -> str | None:
            assert child is not None and child.stdout is not None
            line = child.stdout.readline()
            return line if line else None

        while True:
            line = _read_line()
            if line is None:
                break
            line = line.rstrip("\n")
            if line:
                print(f"[child] {line}")
            if line.startswith("READY"):
                ready = True
                break

        if not ready:
            print("[parent] 失败：子进程未就绪")
            return 2

        # 4. 广播测试阈值（真实 Redis pub/sub）
        version = await _broadcast(TEST_THRESHOLDS, source="verify-parent")
        print(f"[parent] 已广播测试阈值: version={version}, thresholds={TEST_THRESHOLDS}")
        results["broadcast_version"] = version

        # 5. 读取子进程 STATE，确认跨进程同步生效
        sync_confirmed = False
        deadline = time.time() + 6
        while time.time() < deadline:
            line = _read_line()
            if line is None:
                break
            line = line.rstrip("\n")
            if line:
                print(f"[child] {line}")
            if line.startswith("STATE "):
                st = json.loads(line[len("STATE ") :])
                state_lines.append(st)
                if st.get("thresholds") == TEST_THRESHOLDS and st.get("version") == version:
                    sync_confirmed = True
                    print("[parent] ✅ 跨进程同步确认：子进程已应用广播阈值")
                    break
        results["sync_confirmed"] = sync_confirmed

        # 6. 发布旧版本号消息，验证去重
        stale_version = version  # 等于当前版本 → 必被跳过
        _publish_stale_message(stale_version)
        stale_confirmed = False
        deadline = time.time() + 5
        while time.time() < deadline:
            line = _read_line()
            if line is None:
                break
            line = line.rstrip("\n")
            if line:
                print(f"[child] {line}")
            if line.startswith("STATE "):
                st = json.loads(line[len("STATE ") :])
                if st.get("stale_skipped"):
                    stale_confirmed = True
                    print(
                        f"[parent] ✅ 旧版本去重确认：子进程跳过 version={stale_version} "
                        f"消息，当前版本仍为 {st.get('version')}"
                    )
                    break
        results["stale_skip_confirmed"] = stale_confirmed

        # 7. 读取子进程 RESULT（告警自检）
        deadline = time.time() + CHILD_WINDOW_SECONDS
        while time.time() < deadline:
            line = _read_line()
            if line is None:
                break
            line = line.rstrip("\n")
            if line:
                print(f"[child] {line}")
            if line.startswith("RESULT "):
                result_line = json.loads(line[len("RESULT ") :])
                break

        if result_line:
            results["child_alert_in_zone_warned"] = result_line.get("alert_in_zone_warned")
            results["child_alert_above_warned"] = result_line.get("alert_above_warned")
            results["child_final_version"] = result_line.get("final_version")

        # 8. 父进程告警自检（默认阈值）
        parent_alert = _parent_alert_check()
        results["parent_alert"] = parent_alert

    finally:
        # 9. 恢复基线阈值（广播给所有订阅者，含运行中的后端）
        restore = baseline if baseline else None
        try:
            if restore:
                await _broadcast(restore, source="verify-restore")
                print(f"[parent] 已广播恢复基线阈值: {restore}")
            else:
                from app.services.confidence_evaluator import (
                    DEFAULT_CONFIDENCE_THRESHOLDS,
                )

                await _broadcast(DEFAULT_CONFIDENCE_THRESHOLDS, source="verify-restore")
                print(f"[parent] 已广播恢复算法默认阈值: {DEFAULT_CONFIDENCE_THRESHOLDS}")
        except Exception as exc:  # noqa: BLE001
            print(f"[parent] 警告：恢复基线广播失败: {exc}")

        if child is not None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

    # 10. 汇总判定
    print("\n" + "=" * 70)
    print("验证结果汇总")
    print("=" * 70)
    print(json.dumps(results, ensure_ascii=False, indent=2))

    ok = (
        results.get("sync_confirmed") is True
        and results.get("stale_skip_confirmed") is True
        and results.get("child_alert_in_zone_warned") is True
        and results.get("child_alert_above_warned") is False
        and results.get("parent_alert", {}).get("valid_rate_0.25_warned") is True
        and results.get("parent_alert", {}).get("valid_rate_0.50_warned") is False
        and results.get("parent_alert", {}).get("valid_rate_0.15_warned") is False
    )
    print("=" * 70)
    print("✅ 全部通过" if ok else "❌ 存在失败项，请检查上方日志")
    print("=" * 70)
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="验证可信度统一 Phase 2/3")
    parser.add_argument(
        "mode",
        nargs="?",
        default="parent",
        choices=["parent", "worker"],
        help="运行模式：parent=编排验证（默认），worker=子进程订阅",
    )
    args = parser.parse_args()
    if args.mode == "worker":
        run_worker()
        return 0
    return asyncio.run(run_parent())


if __name__ == "__main__":
    sys.exit(main())
