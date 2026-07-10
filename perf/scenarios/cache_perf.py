"""缓存与并发性能测试（PERF-CACHE-001, PERF-CONC-001）.

用例:
    PERF-CACHE-001: Redis 缓存命中率（> 90%）
        通过持续请求工作台聚合 API（带 5 分钟 Redis 缓存），统计 Redis
        INFO stats 中的 keyspace_hits / keyspace_misses，计算命中率。

    PERF-CONC-001: 1200 回路 KPI 计算（1 小时内完成）
        触发 Celery KPI 计算任务，监控任务完成时间，验收 1200 回路在
        1 小时内完成。

环境变量:
    REDIS_HOST / REDIS_PORT / REDIS_DB
    CLPM_PERF_HOST          后端 API host
    CLPM_PERF_USERNAME      登录用户名
    CLPM_PERF_PASSWORD      登录密码
    CELERY_BROKER_URL       Celery broker（用于 PERF-CONC-001）

运行:
    cd perf/scenarios
    python cache_perf.py --case cache-001        # 缓存命中率
    python cache_perf.py --case conc-001         # KPI 计算并发
    python cache_perf.py --case all              # 全部
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

REDIS_CONFIG = {
    "host": os.environ.get("REDIS_HOST", "localhost"),
    "port": int(os.environ.get("REDIS_PORT", "6379")),
    "db": int(os.environ.get("REDIS_DB", "0")),
}

API_HOST = os.environ.get("CLPM_PERF_HOST", "http://localhost:7101")
API_USERNAME = os.environ.get("CLPM_PERF_USERNAME", "admin")
API_PASSWORD = os.environ.get("CLPM_PERF_PASSWORD", "admin123")

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1")

# PERF-CACHE-001: 请求次数与并发
CACHE_TEST_REQUESTS = 500
CACHE_TEST_CONCURRENCY = 20

# PERF-CONC-001: 回路数与超时
KPI_LOOP_COUNT = 1200
KPI_TIMEOUT_SECONDS = 3600  # 1 小时


# ---------------------------------------------------------------------------
# PERF-CACHE-001: Redis 缓存命中率（> 90%）
# ---------------------------------------------------------------------------


def perf_cache_001() -> bool:
    """Redis 缓存命中率测试，验收命中率 > 90%.

    策略:
        1. 记录 Redis INFO stats 的 keyspace_hits / keyspace_misses 基线
        2. 并发请求 /api/v1/dashboard/overview（后端 Redis 缓存 5 分钟）
        3. 相同参数的请求应命中缓存
        4. 再次读取 INFO stats，计算增量命中率
    """
    print("\n[PERF-CACHE-001] Redis 缓存命中率测试...")
    try:
        import redis  # type: ignore
    except ImportError:
        print("  ⚠️  redis 未安装，跳过。请: pip install redis")
        return False
    try:
        import httpx  # type: ignore
    except ImportError:
        print("  ⚠️  httpx 未安装，跳过。请: pip install httpx")
        return False

    r = redis.Redis(**REDIS_CONFIG, decode_responses=True)
    try:
        r.ping()
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ Redis 连接失败: {exc}")
        return False

    # 1. 读取基线
    info_before = r.info("stats")
    hits_before = info_before.get("keyspace_hits", 0)
    misses_before = info_before.get("keyspace_misses", 0)
    print(f"  基线: hits={hits_before}, misses={misses_before}")

    # 2. 登录获取 token
    try:
        login_resp = httpx.post(
            f"{API_HOST}/api/v1/auth/login",
            json={"username": API_USERNAME, "password": API_PASSWORD, "rememberMe": False},
            timeout=10.0,
        )
        login_resp.raise_for_status()
        token = login_resp.json()["data"]["accessToken"]
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 登录失败: {exc}")
        return False

    headers = {"Authorization": f"Bearer {token}"}

    # 3. 并发请求工作台聚合 API（固定参数，确保命中同一缓存 key）
    #    第一批请求触发缓存写入，后续请求应命中缓存
    print(f"  并发请求 {CACHE_TEST_REQUESTS} 次 /dashboard/overview（并发 {CACHE_TEST_CONCURRENCY}）...")

    def make_request(_client: httpx.Client) -> int:
        resp = _client.get(
            f"{API_HOST}/api/v1/dashboard/overview",
            params={"granularity": "day"},
            headers=headers,
            timeout=10.0,
        )
        return resp.status_code

    success_count = 0
    with httpx.Client() as client:
        # 第一次请求：缓存未命中（cold miss）
        make_request(client)
        # 后续请求：应命中缓存
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=CACHE_TEST_CONCURRENCY) as pool:
            futures = [pool.submit(make_request, client) for _ in range(CACHE_TEST_REQUESTS)]
            for f in concurrent.futures.as_completed(futures):
                if f.result() == 200:
                    success_count += 1

    print(f"  请求成功: {success_count}/{CACHE_TEST_REQUESTS + 1}")

    # 4. 读取终态
    info_after = r.info("stats")
    hits_after = info_after.get("keyspace_hits", 0)
    misses_after = info_after.get("keyspace_misses", 0)

    hits_delta = hits_after - hits_before
    misses_delta = misses_after - misses_before
    total_delta = hits_delta + misses_delta

    if total_delta == 0:
        print("  ⚠️  无缓存操作增量，可能后端未启用缓存或缓存 key 不匹配")
        return False

    hit_rate = (hits_delta / total_delta) * 100
    threshold = 90.0
    passed = hit_rate >= threshold

    print(f"  增量: hits={hits_delta}, misses={misses_delta}, total={total_delta}")
    print(f"  缓存命中率: {hit_rate:.2f}%  (阈值 {threshold}%)")
    print(f"  结果: {'✅ PASS' if passed else '❌ FAIL'}")
    return passed


# ---------------------------------------------------------------------------
# PERF-CONC-001: 1200 回路 KPI 计算（1 小时内完成）
# ---------------------------------------------------------------------------


def perf_conc_001() -> bool:
    """1200 回路 KPI 计算并发性能测试，验收 1 小时内完成.

    策略:
        1. 通过 Celery 提交 KPI 计算任务（app.tasks.kpi_calc）
        2. 轮询任务状态直到完成或超时
        3. 验收：1200 回路在 3600 秒内完成

    注意:
        - 需要 Celery worker 运行中：cd backend && celery -A app.tasks.celery_app worker -l info
        - 需要数据库中有 1200 条回路数据（生产规模）
        - 本脚本通过 import 后端 Celery app 提交任务
    """
    print("\n[PERF-CONC-001] 1200 回路 KPI 计算性能测试...")
    print(f"  目标: {KPI_LOOP_COUNT} 回路在 {KPI_TIMEOUT_SECONDS}s 内完成")

    # 尝试导入后端 Celery app
    backend_path = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
    backend_path = os.path.abspath(backend_path)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)

    try:
        # type: ignore
        from app.tasks.celery_app import celery_app  # type: ignore
        from app.tasks.kpi_calc import calculate_all_loops_kpi  # type: ignore
    except ImportError as exc:
        print(f"  ⚠️  无法导入后端 Celery 任务: {exc}")
        print("  请确保在 backend/ 目录下运行，或设置 PYTHONPATH")
        print("  手动测试方式:")
        print("    1. 启动 Celery worker: cd backend && celery -A app.tasks.celery_app worker -l info")
        print("    2. 在 backend/ 目录执行:")
        print("       python -c \"from app.tasks.kpi_calc import calculate_all_loops_kpi; "
              "r = calculate_all_loops_kpi.delay(); print(r.get(timeout=3600))\"")
        return False

    # 提交任务
    print(f"  提交 KPI 计算任务（{KPI_LOOP_COUNT} 回路）...")
    t0 = time.perf_counter()
    try:
        result = calculate_all_loops_kpi.delay()
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 任务提交失败: {exc}")
        return False

    # 轮询任务状态
    print(f"  任务 ID: {result.id}")
    print(f"  轮询任务状态（超时 {KPI_TIMEOUT_SECONDS}s）...")

    poll_interval = 10  # 10 秒轮询一次
    elapsed = 0.0
    while elapsed < KPI_TIMEOUT_SECONDS:
        try:
            ready = result.ready()
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  状态查询失败: {exc}")
            time.sleep(poll_interval)
            elapsed += poll_interval
            continue

        if ready:
            elapsed = time.perf_counter() - t0
            try:
                task_result = result.get(timeout=5)
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ 任务执行失败: {exc}")
                return False

            print(f"  任务完成，耗时: {elapsed:.1f}s")
            print(f"  任务返回: {task_result}")

            # 验收：1200 回路在 1 小时内完成
            passed = elapsed <= KPI_TIMEOUT_SECONDS
            # 单回路平均耗时
            per_loop = elapsed / KPI_LOOP_COUNT if KPI_LOOP_COUNT else 0
            print(f"  单回路平均耗时: {per_loop * 1000:.1f} ms")
            print(f"  结果: {'✅ PASS' if passed else '❌ FAIL'}")
            return passed

        time.sleep(poll_interval)
        elapsed = time.perf_counter() - t0
        if int(elapsed) % 60 == 0:
            print(f"  进度: 已等待 {int(elapsed)}s / {KPI_TIMEOUT_SECONDS}s")

    print(f"  ❌ 任务超时（{KPI_TIMEOUT_SECONDS}s）")
    return False


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="CLPM 缓存与并发性能测试")
    parser.add_argument(
        "--case",
        choices=["cache-001", "conc-001", "all"],
        default="all",
        help="选择运行的用例（默认 all）",
    )
    args = parser.parse_args()

    results: dict[str, bool] = {}

    if args.case in ("cache-001", "all"):
        results["PERF-CACHE-001"] = perf_cache_001()

    if args.case in ("conc-001", "all"):
        results["PERF-CONC-001"] = perf_conc_001()

    print(f"\n{'=' * 70}")
    print("缓存与并发性能测试汇总")
    print(f"{'=' * 70}")
    for case_id, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {case_id}: {status}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
