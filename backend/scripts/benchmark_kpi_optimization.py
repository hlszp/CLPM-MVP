"""评估算法优化性能基准测试 — Phase 5.

对比 Phase 3/4 优化前后的关键算法性能：

1. ARMA Green 函数：解析解 vs 递推（Phase 3 措施 3）
2. 振荡率相似率：向量化 vs 原始 O(n²)（Phase 3 措施 2）
3. 稳态时间搜索：np.convolve vs 逐点扫描（Phase 3 措施 3）
4. 批量节点聚合 DB 查询数对比（Phase 4）

运行方式::

    cd backend && uv run python scripts/benchmark_kpi_optimization.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC
from pathlib import Path

import numpy as np

# 确保能导入 app 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def benchmark_green_function() -> dict:
    """基准 1：Green 函数解析解 vs 递推（Phase 3 措施 3）."""
    from app.tasks.arma import (
        _green_function_analytic,
        _green_function_recursive,
        fit_ar_model,
    )

    # 生成 AR(2) 信号（模拟慢速响应回路）
    np.random.seed(42)
    n = 3600  # 1 小时 @ 1Hz
    a1, a2 = -1.8, 0.85  # 接近单位根的慢速响应
    signal = np.zeros(n)
    for i in range(2, n):
        signal[i] = -a1 * signal[i - 1] - a2 * signal[i - 2] + np.random.randn() * 0.01

    ar_coeffs = fit_ar_model(signal, order=2)

    # 预热
    _green_function_analytic(ar_coeffs, 100)
    _green_function_recursive(ar_coeffs, 100)

    # 解析解
    iterations = 100
    t0 = time.perf_counter()
    for _ in range(iterations):
        g_analytic = _green_function_analytic(ar_coeffs, 3600)
    t_analytic = (time.perf_counter() - t0) / iterations * 1000

    # 递推
    t0 = time.perf_counter()
    for _ in range(iterations):
        g_recursive = _green_function_recursive(ar_coeffs, 3600)
    t_recursive = (time.perf_counter() - t0) / iterations * 1000

    # 验证结果一致
    if g_analytic is not None:
        max_diff = float(np.max(np.abs(g_analytic - g_recursive)))
    else:
        max_diff = float("inf")

    speedup = t_recursive / t_analytic if t_analytic > 0 else float("inf")
    print(
        f"  [Green 函数] 解析解: {t_analytic:.3f}ms, 递推: {t_recursive:.3f}ms, "
        f"加速: {speedup:.1f}x, 最大误差: {max_diff:.2e}"
    )

    return {
        "analytic_ms": round(t_analytic, 3),
        "recursive_ms": round(t_recursive, 3),
        "speedup": round(speedup, 1),
        "max_diff": max_diff,
    }


def benchmark_similarity_rate() -> dict:
    """基准 2：振荡率相似率向量化 vs 原始 O(n²)（Phase 3 措施 2）."""
    from app.services.metric_calculator.oscillation import OscillationRateCalculator

    calc = OscillationRateCalculator()

    # 生成不同规模的 IAE 段列表
    results = {}
    for n_segments in [10, 50, 100, 500]:
        np.random.seed(n_segments)
        iae_values = [float(abs(np.random.randn()) * 10 + 5) for _ in range(n_segments)]

        # 向量化版本（当前实现）
        iterations = 200 if n_segments <= 100 else 20
        t0 = time.perf_counter()
        for _ in range(iterations):
            s_vectorized = calc._similarity_rate(iae_values)
        t_vectorized = (time.perf_counter() - t0) / iterations * 1000

        # 原始 O(n²) 版本（Phase 3 优化前）
        t0 = time.perf_counter()
        for _ in range(iterations):
            s_original = _similarity_rate_original(iae_values)
        t_original = (time.perf_counter() - t0) / iterations * 1000

        speedup = t_original / t_vectorized if t_vectorized > 0 else float("inf")
        diff = abs(s_vectorized - s_original)
        print(
            f"  [相似率 n={n_segments}] 向量化: {t_vectorized:.3f}ms, "
            f"O(n²): {t_original:.3f}ms, 加速: {speedup:.1f}x, 误差: {diff:.2e}"
        )

        results[f"n_{n_segments}"] = {
            "vectorized_ms": round(t_vectorized, 3),
            "original_ms": round(t_original, 3),
            "speedup": round(speedup, 1),
            "diff": diff,
        }

    return results


def _similarity_rate_original(values: list[float]) -> float:
    """Phase 3 优化前的原始 O(n²) 相似率实现（用于对比基准）."""
    n = len(values)
    if n < 2:
        return 0.0
    arr = np.array(values, dtype=float)
    min_dist = float("inf")
    best_j = 0
    for j in range(n):
        dist = 0.0
        for i in range(n):
            diff = arr[i] - arr[j]
            dist += diff * diff
        if dist < min_dist:
            min_dist = dist
            best_j = j
    ref = arr[best_j]
    total = float(np.sum(arr))
    if total == 0:
        return 0.0
    return float(ref * n / total)


def benchmark_settling_time_search() -> dict:
    """基准 3：稳态时间搜索 np.convolve vs 逐点扫描（Phase 3 措施 3）.

    测试两种场景：
    - 早收敛：信号在第 18 点即收敛（循环版提前退出，占优）
    - 全扫描：信号不收敛（循环版需扫描全部 3600 点，向量化占优）
    """
    from app.tasks.arma import compute_green_function, fit_ar_model

    results = {}

    for label, a1, a2 in [("早收敛", -1.5, 0.7), ("全扫描", -1.95, 0.96)]:
        np.random.seed(42)
        n = 3600
        signal = np.zeros(n)
        for i in range(2, n):
            signal[i] = -a1 * signal[i - 1] - a2 * signal[i - 2] + np.random.randn() * 0.01

        ar_coeffs = fit_ar_model(signal, order=2)
        g = compute_green_function(ar_coeffs, 3600)
        if g[0] != 0:
            g = g / g[0]

        threshold = 0.05
        n_consecutive = 10
        abs_green = np.abs(g)
        below = abs_green < threshold

        # 向量化版本
        iterations = 500
        t0 = time.perf_counter()
        for _ in range(iterations):
            idx_vectorized = -1
            if len(below) >= n_consecutive:
                ones = np.ones(n_consecutive, dtype=int)
                window_sums = np.convolve(below.astype(int), ones, mode="valid")
                valid_starts = np.where(window_sums == n_consecutive)[0]
                if len(valid_starts) > 0:
                    idx_vectorized = int(valid_starts[0])
        t_vectorized = (time.perf_counter() - t0) / iterations * 1000

        # 逐点扫描版本
        t0 = time.perf_counter()
        for _ in range(iterations):
            idx_original = -1
            consecutive = 0
            for k in range(len(below)):
                if below[k]:
                    consecutive += 1
                    if consecutive >= n_consecutive:
                        idx_original = k - n_consecutive + 1
                        break
                else:
                    consecutive = 0
        t_original = (time.perf_counter() - t0) / iterations * 1000

        speedup = t_original / t_vectorized if t_vectorized > 0 else 0.0
        match = idx_vectorized == idx_original
        print(
            f"  [稳态搜索-{label}] 向量化: {t_vectorized:.3f}ms, 逐点: {t_original:.3f}ms, "
            f"加速: {speedup:.1f}x, 结果一致: {match} (idx={idx_vectorized})"
        )

        results[label] = {
            "vectorized_ms": round(t_vectorized, 3),
            "original_ms": round(t_original, 3),
            "speedup": round(speedup, 1),
            "result_match": match,
            "settling_index": idx_vectorized,
        }

    return results


def benchmark_zero_crossings() -> dict:
    """基准 4：零交叉检测向量化 vs 原始循环（Phase 3 措施 2）."""
    from app.services.metric_calculator.oscillation import OscillationRateCalculator

    calc = OscillationRateCalculator()

    np.random.seed(42)
    n = 7200  # 2 小时 @ 1Hz
    t = np.linspace(0, 20 * np.pi, n)
    errors = np.sin(t) + np.random.randn(n) * 0.1

    iterations = 200

    # 向量化版本（当前实现）
    t0 = time.perf_counter()
    for _ in range(iterations):
        crossings_vectorized = calc._find_zero_crossings(errors)
    t_vectorized = (time.perf_counter() - t0) / iterations * 1000

    # 原始循环版本
    t0 = time.perf_counter()
    for _ in range(iterations):
        crossings_original = _find_zero_crossings_original(errors)
    t_original = (time.perf_counter() - t0) / iterations * 1000

    speedup = t_original / t_vectorized if t_vectorized > 0 else float("inf")
    match = crossings_vectorized == crossings_original
    print(
        f"  [零交叉] 向量化: {t_vectorized:.3f}ms, 循环: {t_original:.3f}ms, "
        f"加速: {speedup:.1f}x, 结果一致: {match} (count={len(crossings_vectorized)})"
    )

    return {
        "vectorized_ms": round(t_vectorized, 3),
        "original_ms": round(t_original, 3),
        "speedup": round(speedup, 1),
        "result_match": match,
        "crossing_count": len(crossings_vectorized),
    }


def _find_zero_crossings_original(errors: np.ndarray) -> list[int]:
    """Phase 3 优化前的原始零交叉检测（用于对比基准）."""
    crossings: list[int] = []
    prev_sign = 0
    for i in range(len(errors)):
        if errors[i] > 0:
            curr_sign = 1
        elif errors[i] < 0:
            curr_sign = -1
        else:
            curr_sign = 0
        if prev_sign != 0 and curr_sign != 0 and prev_sign != curr_sign:
            crossings.append(i)
        if curr_sign != 0:
            prev_sign = curr_sign
    return crossings


async def benchmark_end_to_end() -> dict | None:
    """基准 5：端到端单回路 KPI 计算（需要 DB + Redis）."""
    try:
        from sqlalchemy import select

        from app.core.db import AsyncSessionLocal
        from app.models.loop import LoopLedger
        from app.models.metric import MetricConfig
        from app.services.loop_config import get_loop_type_weights_map
        from app.tasks.kpi_calc import (
            _batch_load_loop_configs,
            _build_data_planner,
            _calculate_loop_kpi,
            _get_shared_bundle_cache,
            _make_config_loader,
        )
    except ImportError as e:
        print(f"  [端到端] 跳过：无法导入模块 ({e})")
        return None

    from datetime import datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    ts_end = now.replace(minute=0, second=0, microsecond=0)
    ts_start = ts_end - timedelta(hours=1)

    # 加载回路 + 指标配置 + 类型权重 + 批量预加载回路配置
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(LoopLedger).where(LoopLedger.is_active.is_(True)).limit(1)
            )
            loop = result.scalar_one_or_none()
            if loop is None:
                print("  [端到端] 跳过：无活跃回路")
                return None

            metric_result = await db.execute(select(MetricConfig))
            metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

            type_weights = await get_loop_type_weights_map(db)

            loop_configs = await _batch_load_loop_configs(db, [str(loop.id)])
    except Exception as e:
        print(f"  [端到端] 跳过：DB 不可用 ({e})")
        return None

    loop_name = loop.tag_name
    print(f"  [端到端] 回路: {loop_name}, 时间窗: {ts_start} ~ {ts_end}")

    # 预热（L1/L2 缓存填充）
    try:
        async with AsyncSessionLocal() as db:
            loop_cfg = loop_configs.get(str(loop.id))
            config_loader = _make_config_loader(loop_cfg)
            data_planner = _build_data_planner(db, bundle_cache=_get_shared_bundle_cache())
            data_planner._config_loader = config_loader
            data_planner._preloaded_op_limits = {
                str(lid): (cfg["op_lower"], cfg["op_upper"]) for lid, cfg in loop_configs.items()
            }
            await _calculate_loop_kpi(
                db=db,
                loop=loop,
                metric_configs=metric_configs,
                ts_start=ts_start,
                ts_end=ts_end,
                data_planner=data_planner,
                type_weights=type_weights,
            )
    except Exception as e:
        print(f"  [端到端] 预热失败: {e}")
        return None

    # 正式测量（3 次取平均）
    times = []
    for i in range(3):
        t0 = time.perf_counter()
        try:
            async with AsyncSessionLocal() as db:
                loop_cfg = loop_configs.get(str(loop.id))
                config_loader = _make_config_loader(loop_cfg)
                data_planner = _build_data_planner(db, bundle_cache=_get_shared_bundle_cache())
                data_planner._config_loader = config_loader
                data_planner._preloaded_op_limits = {
                    str(lid): (cfg["op_lower"], cfg["op_upper"])
                    for lid, cfg in loop_configs.items()
                }
                result = await _calculate_loop_kpi(
                    db=db,
                    loop=loop,
                    metric_configs=metric_configs,
                    ts_start=ts_start,
                    ts_end=ts_end,
                    data_planner=data_planner,
                    type_weights=type_weights,
                )
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            status = result.get("status", "UNKNOWN") if isinstance(result, dict) else "UNKNOWN"
            print(f"  [端到端] 第 {i + 1} 次: {elapsed:.3f}s, status={status}")
        except Exception as e:
            print(f"  [端到端] 第 {i + 1} 次失败: {e}")
            return None

    avg_time = sum(times) / len(times)
    print(f"  [端到端] 平均: {avg_time:.3f}s (3 次)")

    return {
        "loop_name": loop_name,
        "avg_time_s": round(avg_time, 3),
        "runs": [round(t, 3) for t in times],
    }


def print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def main() -> None:
    print_header("CLPM 评估算法优化 — Phase 5 性能基准测试")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  NumPy: {np.__version__}")

    results: dict = {}

    # 纯算法基准（无需 DB）
    print_header("基准 1: ARMA Green 函数（解析解 vs 递推）")
    results["green_function"] = benchmark_green_function()

    print_header("基准 2: 振荡率相似率（向量化 vs O(n²)）")
    results["similarity_rate"] = benchmark_similarity_rate()

    print_header("基准 3: 稳态时间搜索（np.convolve vs 逐点）")
    results["settling_time_search"] = benchmark_settling_time_search()

    print_header("基准 4: 零交叉检测（向量化 vs 循环）")
    results["zero_crossings"] = benchmark_zero_crossings()

    # 端到端基准（需要 DB + Redis）
    print_header("基准 5: 端到端单回路 KPI 计算（1h 时间窗）")
    e2e_result = asyncio.run(benchmark_end_to_end())
    if e2e_result:
        results["end_to_end"] = e2e_result

    # 汇总
    print_header("性能基准汇总")
    print(f"\n  Green 函数加速: {results['green_function']['speedup']}x")
    for key, val in results["similarity_rate"].items():
        print(f"  相似率 {key} 加速: {val['speedup']}x")
    for label, val in results["settling_time_search"].items():
        print(f"  稳态搜索-{label} 加速: {val['speedup']}x")
    print(f"  零交叉检测加速: {results['zero_crossings']['speedup']}x")
    if "end_to_end" in results:
        print(f"  端到端单回路 1h: {results['end_to_end']['avg_time_s']}s")

    print(f"\n{'=' * 60}")
    print("  基准测试完成")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
