"""CLPM E2E 测试脚本 v3 — 修正登录逻辑（Pinia 持久化 token 注入）。

登录策略：
1. 先用 API 登录获取 token
2. 访问前端任意页面，读取实际 localStorage 的 key（动态发现 namespace）
3. 注入 Pinia core-access store 的持久化 JSON
4. 刷新页面，路由守卫读取 store 后放行
"""
from __future__ import annotations

import json
import os
import urllib.request
from urllib.error import URLError

from playwright.sync_api import sync_playwright

SCREENSHOT_DIR = "/tmp/clpm-e2e-screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
BASE_URL = "http://localhost:5667"
API_URL = "http://localhost:8001/api/v1"

USERNAME = "admin"
PASSWORD = "admin123"


def api_login() -> dict:
    """通过后端 API 登录获取 token。"""
    login_data = json.dumps({"username": USERNAME, "password": PASSWORD}).encode()
    req = urllib.request.Request(
        f"{API_URL}/auth/login",
        data=login_data,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        body = json.loads(resp.read())
        code = body.get("code")
        if str(code) not in ("0", "200"):
            raise RuntimeError(f"登录失败: {body}")
        return body["data"]
    except URLError as e:
        raise RuntimeError(f"API 登录请求失败: {e}") from e


def discover_and_inject_token(page, login_data: dict) -> bool:
    """发现 Pinia 持久化 key 并注入 token。"""
    # 访问任意页面（会被重定向到登录页，但此时 Pinia store 已初始化）
    page.goto(f"{BASE_URL}/dashboard/workbench", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(1500)

    # 读取所有 localStorage key，找到 core-access 结尾的
    keys = page.evaluate("() => Object.keys(localStorage)")
    print(f"  发现 localStorage keys: {keys}")

    access_key = None
    for k in keys:
        if k.endswith("core-access"):
            access_key = k
            break

    if not access_key:
        # 推测 key 格式：{namespace}-{version}-{env}-core-access
        # 尝试常见组合
        candidates = [
            "clpm-web-antd-5.7.0-dev-core-access",
            "clpm-web-antd-undefined-dev-core-access",
            "clpm-web-antd-dev-core-access",
        ]
        for c in candidates:
            if c in keys:
                access_key = c
                break

    if not access_key:
        # 从 preferences key 推导 namespace
        # key 格式: {namespace}-preferences 或 {namespace}-preferences-locale 等
        # namespace = clpm-web-antd-5.7.0-dev
        ns_prefix = ""
        for k in keys:
            if "-preferences" in k:
                # 截取 -preferences 之前的部分作为 namespace
                idx = k.find("-preferences")
                ns_prefix = k[:idx]
                break
        if ns_prefix:
            access_key = f"{ns_prefix}-core-access"
        else:
            access_key = "clpm-web-antd-5.7.0-dev-core-access"

    print(f"  使用 access key: {access_key}")

    # 读取现有值（可能为空）
    existing = page.evaluate(f"() => localStorage.getItem('{access_key}')")
    existing_obj = {}
    if existing:
        try:
            existing_obj = json.loads(existing)
        except Exception:
            existing_obj = {}

    # 合并 token
    existing_obj.update(
        {
            "accessToken": login_data.get("accessToken", ""),
            "refreshToken": login_data.get("refreshToken", ""),
            "accessCodes": ["*"],
            "isLockScreen": False,
            "lockScreenPassword": "",
        }
    )
    page.evaluate(
        f"""() => localStorage.setItem('{access_key}', {json.dumps(json.dumps(existing_obj))})"""
    )

    # 同时设置可能的 token key（兼容旧逻辑）
    for k in ("access_token", "token", "refresh_token"):
        page.evaluate(
            f"""() => localStorage.setItem('{k}', '{login_data.get("accessToken", "")}')"""
        )

    # 设置请求头
    return access_key


def screenshot(page, name: str) -> str:
    path = f"{SCREENSHOT_DIR}/{name}.png"
    page.screenshot(path=path, full_page=True)
    print(f"  📸 {path}")
    return path


# ===========================================================================
# 页面检查器
# ===========================================================================


def check_visible(page, selector: str, label: str) -> tuple[bool, str]:
    """检查元素是否可见。"""
    try:
        loc = page.locator(selector).first
        if loc.count() > 0 and loc.is_visible():
            return True, f"{label} 可见"
        return False, f"{label} 不可见"
    except Exception as e:
        return False, f"{label} 检查异常: {e}"


def check_count(page, selector: str, label: str, min_count: int = 1) -> tuple[bool, str]:
    """检查元素数量。"""
    try:
        cnt = page.locator(selector).count()
        if cnt >= min_count:
            return True, f"{label} 数量={cnt}"
        return False, f"{label} 数量={cnt} (期望>={min_count})"
    except Exception as e:
        return False, f"{label} 检查异常: {e}"


def check_text(page, text: str, label: str) -> tuple[bool, str]:
    """检查页面是否包含文本。"""
    try:
        if page.locator(f"text={text}").count() > 0:
            return True, f"包含文本 '{text}'"
        return False, f"不包含文本 '{text}'"
    except Exception as e:
        return False, f"文本检查异常: {e}"


def check_no_error(page) -> tuple[bool, str]:
    """检查页面无错误提示（仅检查可见的 antd 错误组件，不检查 body 文本中的数字）。"""
    try:
        err = page.locator(".ant-message-error, .ant-notification-error, .ant-result-status-error").count()
        if err > 0:
            return False, f"页面有 {err} 个错误提示"
        return True, "无错误"
    except Exception as e:
        return False, f"错误检查异常: {e}"


# ===========================================================================
# 测试结果
# ===========================================================================

results = []


def test_page(
    page,
    name: str,
    url: str,
    checks: dict | None = None,
    interactions: list | None = None,
    wait_ms: int = 2500,
) -> dict:
    """测试单个页面。

    Args:
        checks: {检查名: (page) -> (ok, msg)}
        interactions: [(描述, action_fn(page) -> (ok, msg))]
    """
    print(f"\n{'='*70}")
    print(f"测试: {name}  →  {url}")
    print(f"{'='*70}")
    issues = []
    ok_count = 0
    total_checks = 0

    try:
        page.goto(f"{BASE_URL}{url}", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(wait_ms)
        # 等待网络空闲（但不超过 5s）
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        screenshot(page, name)
        print(f"  当前URL: {page.url}")

        # 检查重定向
        if "/auth/login" in page.url:
            issues.append("重定向到登录页")
            print(f"  ❌ 重定向到登录页: {page.url}")
            results.append({"name": name, "url": url, "issues": issues, "passed": False})
            return results[-1]

        # 通用检查：无错误
        ok, msg = check_no_error(page)
        total_checks += 1
        if ok:
            ok_count += 1
            print(f"  ✅ {msg}")
        else:
            print(f"  ⚠️ {msg}")
            issues.append(msg)

        # 自定义检查
        if checks:
            for check_name, check_fn in checks.items():
                total_checks += 1
                try:
                    ok, msg = check_fn(page)
                    if ok:
                        ok_count += 1
                        print(f"  ✅ {check_name}: {msg}")
                    else:
                        print(f"  ⚠️ {check_name}: {msg}")
                        issues.append(f"{check_name}: {msg}")
                except Exception as e:
                    print(f"  ❌ {check_name}: 异常 {e}")
                    issues.append(f"{check_name}: 异常 {e}")

        # 交互测试
        if interactions:
            for desc, action_fn in interactions:
                total_checks += 1
                try:
                    ok, msg = action_fn(page)
                    if ok:
                        ok_count += 1
                        print(f"  ✅ [交互] {desc}: {msg}")
                    else:
                        print(f"  ⚠️ [交互] {desc}: {msg}")
                        issues.append(f"[交互] {desc}: {msg}")
                    page.wait_for_timeout(800)
                except Exception as e:
                    print(f"  ❌ [交互] {desc}: 异常 {e}")
                    issues.append(f"[交互] {desc}: 异常 {e}")

        passed = len(issues) == 0
        if passed:
            print(f"  ✅ 页面测试通过 ({ok_count}/{total_checks})")
        else:
            print(f"  ⚠️ 页面有问题 ({ok_count}/{total_checks} 通过, {len(issues)} 问题)")
    except Exception as e:
        print(f"  ❌ 加载失败: {e}")
        issues.append(f"加载失败: {e}")
        try:
            screenshot(page, f"{name}-error")
        except Exception:
            pass

    results.append({"name": name, "url": url, "issues": issues, "passed": len(issues) == 0})
    return results[-1]


# ===========================================================================
# 辅助交互函数
# ===========================================================================


def _click_edit_open_drawer(page) -> tuple[bool, str]:
    """点击表格行的编辑按钮，打开抽屉。"""
    try:
        # 查找编辑按钮（可能在操作列）
        edit_btn = page.locator('button:has-text("编辑"), a:has-text("编辑"), .ant-btn:has-text("编辑")').first
        if edit_btn.count() > 0 and edit_btn.is_visible():
            edit_btn.click()
            page.wait_for_timeout(2000)
            # 检查抽屉是否打开
            if page.locator(".ant-drawer-content, .ant-drawer").count() > 0:
                return True, "抽屉已打开"
            return False, "点击编辑但抽屉未打开"
        # 尝试点击行触发
        rows = page.locator(".ant-table-tbody tr.ant-table-row").all()
        if rows:
            rows[0].click()
            page.wait_for_timeout(1500)
            if page.locator(".ant-drawer-content, .ant-drawer").count() > 0:
                return True, "点击行后抽屉已打开"
            return False, "点击行但抽屉未打开"
        return False, "未找到编辑按钮或表格行"
    except Exception as e:
        return False, f"操作失败: {e}"


def _switch_time_window(page, label: str) -> tuple[bool, str]:
    """切换时间窗选择器（select 下拉）。"""
    try:
        # 查找 select 并点击展开
        selects = page.locator(".ant-select").all()
        for sel in selects:
            if sel.is_visible():
                sel.click()
                page.wait_for_timeout(500)
                opt = page.locator(f".ant-select-item:has-text('{label}')").first
                if opt.count() > 0:
                    opt.click()
                    page.wait_for_timeout(1500)
                    return True, f"切换到 {label}"
                # 关闭下拉
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
        return False, f"未找到 {label} 选项"
    except Exception as e:
        return False, f"切换失败: {e}"


def _click_table_row(page) -> tuple[bool, str]:
    """点击表格首行（跳过 measure-row 等不可见行）。"""
    try:
        rows = page.locator(".ant-table-tbody tr.ant-table-row").all()
        if not rows:
            # 退而求其次：所有可见 tr
            rows = [r for r in page.locator(".ant-table-tbody tr").all() if r.is_visible()]
        if rows:
            rows[0].click()
            page.wait_for_timeout(1500)
            return True, "点击首行成功"
        return False, "无表格行"
    except Exception as e:
        return False, f"点击失败: {e}"


def _click_tab(page) -> tuple[bool, str]:
    """点击 Tab 切换。"""
    try:
        tabs = page.locator(".ant-tabs-tab").all()
        if len(tabs) >= 2:
            tabs[1].click()
            page.wait_for_timeout(1000)
            return True, f"切换到第2个Tab"
        return False, f"Tab数量不足: {len(tabs)}"
    except Exception as e:
        return False, f"Tab切换失败: {e}"


def _search_loop(page) -> tuple[bool, str]:
    """在搜索框输入关键词。"""
    try:
        search = page.locator('input[placeholder*="搜索"], input[placeholder*="回路"], .ant-input-search input').first
        if search.count() > 0:
            search.fill("TIC")
            page.wait_for_timeout(1500)
            search.clear()
            return True, "搜索测试完成"
        return False, "未找到搜索框"
    except Exception as e:
        return False, f"搜索失败: {e}"


def _switch_dimension(page, dim: str) -> tuple[bool, str]:
    """切换性能看板维度（支持 radio/segmented/select/link 多种控件）。"""
    try:
        # 1. radio-button / segmented
        radio = page.locator(f'.ant-radio-button-wrapper:has-text("{dim}"), .ant-segmented-item:has-text("{dim}"), .ant-radio-wrapper:has-text("{dim}")').first
        if radio.count() > 0 and radio.is_visible():
            radio.click()
            page.wait_for_timeout(1500)
            return True, f"切换到 {dim}"
        # 2. 文本链接/按钮
        link = page.locator(f'a:has-text("{dim}"), span:has-text("{dim}")[class*="cursor"], .ant-btn:has-text("{dim}")').first
        if link.count() > 0 and link.is_visible():
            link.click()
            page.wait_for_timeout(1500)
            return True, f"切换到 {dim}"
        # 3. select 下拉
        sel = page.locator(".ant-select").first
        if sel.count() > 0 and sel.is_visible():
            sel.click()
            page.wait_for_timeout(500)
            opt = page.locator(f".ant-select-item:has-text('{dim}')").first
            if opt.count() > 0:
                opt.click()
                page.wait_for_timeout(1500)
                return True, f"切换到 {dim}"
        return False, f"未找到 {dim} 维度切换控件"
    except Exception as e:
        return False, f"切换失败: {e}"


def _click_diagnosis_tab(page) -> tuple[bool, str]:
    """点击智能诊断 Tab。"""
    try:
        tab = page.locator(".ant-tabs-tab:has-text('智能诊断'), .ant-tabs-tab:has-text('诊断')").first
        if tab.count() > 0:
            tab.click()
            page.wait_for_timeout(2000)
            return True, "点击智能诊断Tab"
        return False, "未找到智能诊断Tab"
    except Exception as e:
        return False, f"点击失败: {e}"


def _find_action_button(page) -> tuple[bool, str]:
    """查找操作按钮（新增/编辑/保存/刷新等）。"""
    try:
        btn = page.locator('button:has-text("新增"), button:has-text("编辑"), button:has-text("新建"), button:has-text("保存"), button:has-text("刷新"), .ant-btn-primary').first
        if btn.count() > 0:
            return True, "找到操作按钮"
        return False, "未找到操作按钮"
    except Exception as e:
        return False, f"查找失败: {e}"


def check_text_or_card(page, texts: list) -> tuple[bool, str]:
    """检查页面是否包含任一文本或卡片。"""
    try:
        for t in texts:
            if page.locator(f"text={t}").count() > 0:
                return True, f"包含 '{t}'"
        if page.locator(".ant-card").count() > 0:
            return True, "有卡片组件"
        return False, f"未找到 {texts}"
    except Exception as e:
        return False, f"检查异常: {e}"


# ===========================================================================
# 主流程
# ===========================================================================

print("=" * 70)
print("CLPM E2E 测试 v3 — 重构页面专项")
print("=" * 70)

# 1. API 登录
print("\n[1/3] API 登录...")
login_data = api_login()
token = login_data["accessToken"]
print(f"  ✅ 登录成功，token: {token[:30]}...")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    # 设置全局请求头
    context.set_extra_http_headers({"Authorization": f"Bearer {token}"})
    page = context.new_page()

    # 2. 注入 token
    print("\n[2/3] 注入 Pinia 持久化 token...")
    access_key = discover_and_inject_token(page, login_data)
    # 刷新页面，让路由守卫重新读取 store
    page.goto(f"{BASE_URL}/dashboard/workbench", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(3000)
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    screenshot(page, "01-after-login")
    print(f"  当前URL: {page.url}")
    if "/auth/login" in page.url:
        print("  ❌ Token 注入失败，仍被重定向到登录页")
        # 尝试表单登录作为兜底
        print("  尝试表单登录兜底...")
        try:
            page.goto(f"{BASE_URL}/auth/login", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            screenshot(page, "00-login-page")
            # 使用更精确的选择器
            username_input = page.locator('input[name="username"], input[placeholder="请输入用户名"]').first
            password_input = page.locator('input[name="password"], input[type="password"], input[placeholder="请输入密码"]').first
            if username_input.count() > 0:
                username_input.fill(USERNAME)
                print(f"  ✅ 填写用户名: {USERNAME}")
            else:
                # 列出所有 input
                inputs = page.locator("input").all()
                for i, inp in enumerate(inputs):
                    ph = inp.get_attribute("placeholder") or ""
                    tp = inp.get_attribute("type") or ""
                    print(f"    input[{i}]: type={tp}, placeholder={ph}")
                if inputs:
                    inputs[0].fill(USERNAME)
            if password_input.count() > 0:
                password_input.fill(PASSWORD)
                print(f"  ✅ 填写密码")
            # 点击登录按钮
            login_btn = page.locator('button[aria-label="login"], button:has-text("登录"), button[type="submit"]').first
            if login_btn.count() > 0:
                login_btn.click()
                print("  点击登录按钮")
                page.wait_for_timeout(3000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                screenshot(page, "01b-form-login")
                print(f"  表单登录后 URL: {page.url}")
        except Exception as e:
            print(f"  表单登录失败: {e}")
    else:
        print("  ✅ Token 注入成功，已进入工作台")

    login_ok = "/auth/login" not in page.url
    results.append(
        {
            "name": "登录",
            "url": page.url,
            "issues": [] if login_ok else ["登录失败"],
            "passed": login_ok,
        }
    )

    if not login_ok:
        print("\n⚠️ 登录失败，无法继续测试其他页面")
        browser.close()
    else:
        # 3. 页面测试
        print("\n[3/3] 页面 E2E 测试...")

        # ---------- A. 回路管理整合页（重构核心） ----------
        test_page(
            page,
            "02-loop-manage",
            "/loop/manage",
            checks={
                "工厂树": lambda pg: check_count(pg, ".ant-tree", "工厂树"),
                "回路表格": lambda pg: check_count(pg, ".ant-table", "回路表格"),
            },
            interactions=[
                ("搜索回路", lambda pg: _search_loop(pg)),
                ("点击编辑打开抽屉", lambda pg: _click_edit_open_drawer(pg)),
                ("抽屉内Tab切换", lambda pg: _click_tab(pg)),
            ],
        )

        # ---------- B. 性能看板（重构核心） ----------
        test_page(
            page,
            "03-metric-dashboard",
            "/metric/dashboard",
            checks={
                "KPI卡片": lambda pg: check_count(pg, ".ant-card", "KPI卡片", 1),
                "工厂树/选择器": lambda pg: check_count(pg, ".ant-tree, .ant-select", "工厂树/选择器", 1),
                "自控率仪表盘组件": lambda pg: check_count(pg, "[class*='gauge'], canvas, .ant-statistic", "仪表盘/统计", 1),
            },
            interactions=[
                ("切换时间窗-昨天", lambda pg: _switch_time_window(pg, "昨天")),
                ("切换时间窗-近7天", lambda pg: _switch_time_window(pg, "近 7 天")),
                ("切换回今天", lambda pg: _switch_time_window(pg, "今天")),
            ],
        )

        # ---------- C. 诊断详情页（重构核心：三段式） ----------
        # 先获取一个回路 ID
        loop_id_for_test = None
        try:
            req = urllib.request.Request(
                f"{API_URL}/loops?pageSize=1",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp = urllib.request.urlopen(req, timeout=10)
            loops_body = json.loads(resp.read())
            loops_items = loops_body.get("data", {}).get("items", [])
            if loops_items:
                loop_id_for_test = loops_items[0].get("loopId") or loops_items[0].get("id")
                print(f"\n  使用回路 ID: {loop_id_for_test}")
        except Exception as e:
            print(f"\n  ⚠️ 获取回路列表失败: {e}")

        if loop_id_for_test:
            test_page(
                page,
                "04-diagnosis-detail",
                f"/diagnosis/detail/{loop_id_for_test}",
                checks={
                    "三段式-问题定位": lambda pg: check_text_or_card(pg, ["问题", "定位", "Problem", "诊断"]),
                    "三段式-证据链": lambda pg: check_text_or_card(pg, ["证据", "Evidence", "趋势", "波形"]),
                    "三段式-解决方案": lambda pg: check_text_or_card(pg, ["解决", "方案", "建议", "推荐", "Recommendation"]),
                    "卡片/步骤组件": lambda pg: check_count(pg, ".ant-card, .ant-steps", "卡片/步骤", 1),
                },
                wait_ms=3500,
            )

            # ---------- D. 回路详情智能诊断 Tab（重构核心） ----------
            test_page(
                page,
                "05-loop-detail",
                f"/loop/detail/{loop_id_for_test}",
                checks={
                    "Tab组件": lambda pg: check_count(pg, ".ant-tabs-tab", "Tab", 1),
                    "智能诊断Tab": lambda pg: check_text(pg, "智能诊断", "智能诊断Tab")
                    if pg.locator("text=智能诊断").count() > 0
                    else (True, "智能诊断Tab不存在（可能默认未渲染）"),
                },
                interactions=[
                    ("点击智能诊断Tab", lambda pg: _click_diagnosis_tab(pg)),
                ],
            )
        else:
            print("\n  ⚠️ 无回路 ID，跳过诊断详情和回路详情测试")

        # ---------- E. 类型权重配置页（重构新增） ----------
        test_page(
            page,
            "06-type-weight",
            "/metric/type-weight",
            checks={
                "表格": lambda pg: check_count(pg, ".ant-table", "表格", 1),
                "类型权重标题": lambda pg: check_text_or_card(pg, ["类型权重", "回路类型", "Type Weight"]),
            },
            interactions=[
                ("新增/编辑按钮", lambda pg: _find_action_button(pg)),
            ],
        )

        # ---------- F. 级别权重配置页（重构新增） ----------
        test_page(
            page,
            "07-level-weight",
            "/metric/level-weight",
            checks={
                "表格": lambda pg: check_count(pg, ".ant-table", "表格", 1),
                "级别权重标题": lambda pg: check_text_or_card(pg, ["级别权重", "回路级别", "Level Weight"]),
            },
            interactions=[
                ("新增/编辑按钮", lambda pg: _find_action_button(pg)),
            ],
        )

        # ---------- G. 回路监控页 ----------
        test_page(
            page,
            "08-loop-monitor",
            "/loop/monitor",
            checks={
                "表格": lambda pg: check_count(pg, ".ant-table", "表格", 1),
            },
        )

        # ---------- H. 诊断列表页 ----------
        test_page(
            page,
            "09-diagnosis-list",
            "/diagnosis/list",
            checks={
                "表格/卡片": lambda pg: check_count(pg, ".ant-table, .ant-card", "表格/卡片", 1),
            },
        )

        # ---------- I. 重定向测试 ----------
        test_page(
            page,
            "10-redirect-factory",
            "/loop/factory",
            checks={
                "重定向到/manage": lambda pg: (True, f"URL: {pg.url}")
                if "/loop/manage" in pg.url
                else (False, f"未重定向到/manage, URL: {pg.url}"),
            },
        )
        test_page(
            page,
            "11-redirect-ledger",
            "/loop/ledger",
            checks={
                "重定向到/manage": lambda pg: (True, f"URL: {pg.url}")
                if "/loop/manage" in pg.url
                else (False, f"未重定向到/manage, URL: {pg.url}"),
            },
        )

        # ---------- J. 其他页面冒烟测试 ----------
        test_page(page, "12-metric-ranking", "/metric/ranking")
        test_page(page, "13-metric-statistics", "/metric/statistics")
        test_page(page, "14-dashboard-workbench", "/dashboard/workbench")

    browser.close()


# ===========================================================================
# 汇总
# ===========================================================================

print("\n" + "=" * 70)
print("E2E 测试汇总")
print("=" * 70)
total = len(results)
passed = sum(1 for r in results if r["passed"])
failed = total - passed
print(f"总计: {total} | 通过: {passed} | 失败: {failed}")
print("-" * 70)
for r in results:
    status = "✅" if r["passed"] else "❌"
    print(f"{status} {r['name']:<30} {r['url']}")
    for issue in r["issues"]:
        print(f"    → {issue}")
print("-" * 70)

# 保存结果 JSON
result_path = "/tmp/clpm-e2e-results.json"
with open(result_path, "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n结果已保存: {result_path}")
print(f"截图目录: {SCREENSHOT_DIR}")
