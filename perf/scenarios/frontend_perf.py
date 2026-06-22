"""前端性能测试（PERF-FE-001, PERF-FE-002）.

用例:
    PERF-FE-001: 首屏加载时间（< 3 秒，Lighthouse）
        使用 Playwright 模拟浏览器访问前端首页，测量首屏加载时间。
        也可使用 Lighthouse CLI 进行标准化测量。

    PERF-FE-002: 工作台 ECharts 渲染（6 图表 < 2 秒）
        登录后访问工作台页面，测量 6 个 ECharts 图表的渲染完成时间。

环境变量:
    CLPM_PERF_FRONTEND_URL   前端 URL（默认 http://localhost:5666）
    CLPM_PERF_API_HOST       后端 API host（用于登录）
    CLPM_PERF_USERNAME       登录用户名
    CLPM_PERF_PASSWORD       登录密码

前置条件:
    1. 前端已启动: cd frontend && pnpm dev（端口 5666）
    2. 后端已启动: cd backend && uv run uvicorn app.main:app --port 8001
    3. 安装 Playwright 浏览器: playwright install chromium

运行:
    cd perf/scenarios
    python frontend_perf.py --case fe-001        # 首屏加载
    python frontend_perf.py --case fe-002        # ECharts 渲染
    python frontend_perf.py --case all           # 全部
"""

from __future__ import annotations

import argparse
import os
import sys
import time

FRONTEND_URL = os.environ.get("CLPM_PERF_FRONTEND_URL", "http://localhost:5666")
API_HOST = os.environ.get("CLPM_PERF_API_HOST", "http://localhost:8001")
USERNAME = os.environ.get("CLPM_PERF_USERNAME", "admin")
PASSWORD = os.environ.get("CLPM_PERF_PASSWORD", "admin123")

# 验收阈值
FE_001_THRESHOLD_MS = 3000  # 首屏 < 3 秒
FE_002_THRESHOLD_MS = 2000  # 6 图表渲染 < 2 秒


# ---------------------------------------------------------------------------
# PERF-FE-001: 首屏加载时间（< 3 秒）
# ---------------------------------------------------------------------------


def perf_fe_001() -> bool:
    """首屏加载时间测试，验收 < 3 秒.

    使用 Playwright 测量 navigation 开始到 load 事件完成的时间。
    也可使用 Lighthouse CLI 替代:
        npx lighthouse http://localhost:5666 --only-categories=performance \\
            --output=json --output-path=perf-fe-001.json
        # 关注 first-contentful-paint / largest-contentful-paint 指标
    """
    print("\n[PERF-FE-001] 首屏加载时间测试...")
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        print("  ⚠️  playwright 未安装，跳过。请: pip install playwright && playwright install chromium")
        return False

    latencies: list[float] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            # 重复测量 5 次取平均
            for i in range(5):
                t0 = time.perf_counter()
                page.goto(FRONTEND_URL, wait_until="load", timeout=30000)
                # 等待首屏关键元素（登录表单或主内容）
                try:
                    page.wait_for_selector("input, .ant-card, main", timeout=10000)
                except Exception:  # noqa: BLE001
                    pass
                elapsed_ms = (time.perf_counter() - t0) * 1000
                latencies.append(elapsed_ms)
                print(f"  第 {i + 1} 次: {elapsed_ms:.0f} ms")

            browser.close()
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ Playwright 执行失败: {exc}")
        print("  备选方案: 使用 Lighthouse CLI")
        print(f"    npx lighthouse {FRONTEND_URL} --only-categories=performance")
        return False

    if not latencies:
        return False

    avg = sum(latencies) / len(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else latencies[0]
    passed = avg <= FE_001_THRESHOLD_MS

    print(f"  平均: {avg:.0f} ms")
    print(f"  P95:  {p95:.0f} ms")
    print(f"  阈值: {FE_001_THRESHOLD_MS} ms")
    print(f"  结果: {'✅ PASS' if passed else '❌ FAIL'}")
    return passed


# ---------------------------------------------------------------------------
# PERF-FE-002: 工作台 ECharts 渲染（6 图表 < 2 秒）
# ---------------------------------------------------------------------------


def perf_fe_002() -> bool:
    """工作台 ECharts 渲染测试，验收 6 图表 < 2 秒.

    策略:
        1. 通过 API 登录获取 token，写入 localStorage
        2. 访问工作台页面
        3. 等待 6 个 ECharts canvas 渲染完成
        4. 测量从页面导航到所有图表渲染完成的时间
    """
    print("\n[PERF-FE-002] 工作台 ECharts 渲染测试（6 图表）...")
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        print("  ⚠️  playwright 未安装，跳过。请: pip install playwright && playwright install chromium")
        return False
    try:
        import httpx  # type: ignore
    except ImportError:
        print("  ⚠️  httpx 未安装，跳过。请: pip install httpx")
        return False

    # 1. 登录获取 token
    try:
        login_resp = httpx.post(
            f"{API_HOST}/api/v1/auth/login",
            json={"username": USERNAME, "password": PASSWORD, "rememberMe": False},
            timeout=10.0,
        )
        login_resp.raise_for_status()
        token = login_resp.json()["data"]["accessToken"]
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 登录失败: {exc}")
        return False

    latencies: list[float] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            # 注入 token 到 localStorage（前端 store 从 localStorage 读取）
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30000)
            page.evaluate(
                """(token) => {
                    localStorage.setItem('access_token', token);
                    localStorage.setItem('token', token);
                }""",
                token,
            )

            # 重复测量 3 次取平均
            for i in range(3):
                t0 = time.perf_counter()
                # 访问工作台页面
                page.goto(f"{FRONTEND_URL}/dashboard", wait_until="networkidle", timeout=30000)

                # 等待 ECharts canvas 渲染（工作台有 6 个图表）
                # ECharts 渲染后会生成 canvas 元素
                try:
                    page.wait_for_selector("canvas", timeout=15000)
                    # 等待足够数量的 canvas（6 个图表）
                    page.wait_for_function(
                        """() => document.querySelectorAll('canvas').length >= 6""",
                        timeout=15000,
                    )
                except Exception:  # noqa: BLE001
                    # 容错：等待至少 1 个 canvas
                    page.wait_for_selector("canvas", timeout=5000)

                elapsed_ms = (time.perf_counter() - t0) * 1000
                latencies.append(elapsed_ms)
                canvas_count = page.evaluate("document.querySelectorAll('canvas').length")
                print(f"  第 {i + 1} 次: {elapsed_ms:.0f} ms (canvas 数: {canvas_count})")

            browser.close()
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ Playwright 执行失败: {exc}")
        return False

    if not latencies:
        return False

    avg = sum(latencies) / len(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else latencies[0]
    passed = avg <= FE_002_THRESHOLD_MS

    print(f"  平均: {avg:.0f} ms")
    print(f"  P95:  {p95:.0f} ms")
    print(f"  阈值: {FE_002_THRESHOLD_MS} ms")
    print(f"  结果: {'✅ PASS' if passed else '❌ FAIL'}")
    return passed


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="CLPM 前端性能测试")
    parser.add_argument(
        "--case",
        choices=["fe-001", "fe-002", "all"],
        default="all",
        help="选择运行的用例（默认 all）",
    )
    args = parser.parse_args()

    results: dict[str, bool] = {}

    if args.case in ("fe-001", "all"):
        results["PERF-FE-001"] = perf_fe_001()

    if args.case in ("fe-002", "all"):
        results["PERF-FE-002"] = perf_fe_002()

    print(f"\n{'=' * 70}")
    print("前端性能测试汇总")
    print(f"{'=' * 70}")
    for case_id, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {case_id}: {status}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
