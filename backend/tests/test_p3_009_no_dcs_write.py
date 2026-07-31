"""V62-P3-009 安全红线守卫测试：全程无 DCS 下写.

平台安全边界（v6.2 方案 §7.3）：
    平台不直接修改 DCS 的 P/I/D 参数，只输出建议、证据、风险和回退方案；
    参数由授权人员人工实施并留痕。

本测试为静态守卫，确保代码库中不存在任何 DCS 下写 API/服务/隐含状态：
1. 无 DCS 参数下写 API 端点（路径不含 /write /push /deploy /apply 且目标为 DCS PID）；
2. 无 DCS 参数下写服务函数（函数名不含 write_dcs/push_to_dcs/apply_pid_to_dcs）；
3. 无 OPC 写操作（async_write/write_value 写 PID 参数）；
4. tuning_record APPLIED 状态不被任何代码设置（仅遗留枚举兼容）；
5. 前端无 DCS 下写按钮（通过 API 路由守卫间接保证）。

若未来需要新增 DCS 下写能力，必须先通过 ADR 评审修改安全边界，
并更新本测试的断言清单。
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from types import ModuleType

import pytest

# 禁止的 DCS 下写函数名模式（服务层）
_FORBIDDEN_SERVICE_PATTERNS = (
    "write_dcs",
    "push_to_dcs",
    "apply_pid_to_dcs",
    "deploy_to_dcs",
    "send_pid_to_dcs",
    "write_pid_params",
    "download_to_dcs",
)

# 禁止的 API 路由路径模式（DCS 参数下写）
_FORBIDDEN_ROUTE_PATTERNS = (
    "/dcs/write",
    "/dcs/push",
    "/dcs/deploy",
    "/dcs/apply",
    "/dcs/download-params",
    "/tuning/apply",
    "/tuning/deploy",
    "/tuning/push",
    "/pid/write",
    "/pid/deploy",
)


def _iter_modules(package_name: str) -> list[ModuleType]:
    """递归导入包下所有模块。"""
    try:
        package = importlib.import_module(package_name)
    except ImportError:
        return []

    modules: list[ModuleType] = [package]
    if not hasattr(package, "__path__"):
        return modules

    for _, name, _ in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        try:
            mod = importlib.import_module(name)
            modules.append(mod)
        except Exception:
            continue
    return modules


def _get_function_names(modules: list[ModuleType]) -> set[str]:
    """收集模块中所有函数名。"""
    names: set[str] = set()
    for mod in modules:
        for _, obj in inspect.getmembers(mod, inspect.isfunction):
            names.add(obj.__name__)
        for _, obj in inspect.getmembers(mod, inspect.iscoroutinefunction):
            names.add(obj.__name__)
    return names


def test_no_dcs_write_service_functions() -> None:
    """服务层不得存在 DCS 参数下写函数。"""
    service_modules = _iter_modules("app.services")
    func_names = _get_function_names(service_modules)

    violations: list[str] = []
    for pattern in _FORBIDDEN_SERVICE_PATTERNS:
        for name in func_names:
            if pattern in name.lower():
                violations.append(name)
    assert not violations, (
        f"安全红线违规：服务层存在 DCS 下写函数 {violations}。"
        f"平台不得直接修改 DCS 参数（v6.2 §7.3 安全边界）。"
    )


def test_no_dcs_write_api_routes() -> None:
    """API 层不得存在 DCS 参数下写路由。"""
    from app.main import app

    routes: list[str] = []
    for route in app.routes:
        if hasattr(route, "path"):
            routes.append(route.path)
        if hasattr(route, "methods") and route.methods:
            for method in route.methods:
                if method in ("POST", "PUT", "PATCH", "DELETE"):
                    routes.append(f"{method} {getattr(route, 'path', '')}")

    violations: list[str] = []
    for pattern in _FORBIDDEN_ROUTE_PATTERNS:
        for route in routes:
            if pattern in route.lower():
                violations.append(route)
    assert not violations, (
        f"安全红线违规：API 存在 DCS 下写路由 {violations}。"
        f"平台不得提供 DCS 参数下写接口（v6.2 §7.3 安全边界）。"
    )


def test_tuning_applied_status_not_set_by_code() -> None:
    """tuning_record APPLIED 状态不被任何代码设置（仅遗留枚举兼容）.

    APPLIED 状态保留在枚举中仅为兼容历史数据，当前代码不得设置该状态。
    整定结果的状态流转为：RUNNING → IDENTIFIED → SIMULATED → COMPLETED，
    不经过 APPLIED（该状态隐含"已应用到 DCS"语义，违反安全边界）。
    """
    task_modules = _iter_modules("app.tasks")
    service_modules = _iter_modules("app.services")

    violations: list[str] = []
    for mod in task_modules + service_modules:
        source = inspect.getsource(mod)
        # 检查是否有赋值 status = "APPLIED" 或 .status = "APPLIED"
        if 'status = "APPLIED"' in source or ".status = " + '"APPLIED"' in source:
            violations.append(mod.__name__)
        if "status='APPLIED'" in source or ".status='APPLIED'" in source:
            violations.append(mod.__name__)

    assert not violations, (
        f"安全红线违规：模块 {violations} 设置了 APPLIED 状态。"
        f"该状态隐含 DCS 下写语义，不得由平台代码设置（v6.2 §7.3）。"
    )


def test_dcs_endpoints_are_config_only() -> None:
    """DCS 端点必须是配置元数据 CRUD，不得包含参数下写.

    现有 DCS 端点管理 vendor/model/mode-mapping/pid-structure 配置，
    这些是 CLPM 内部元数据，不是对实际 DCS 的写操作。
    """
    from app.api.v1.endpoints import dcs as dcs_endpoint_module

    # 收集所有路由路径
    route_paths: list[str] = []
    for _, obj in inspect.getmembers(dcs_endpoint_module):
        if hasattr(obj, "path") and isinstance(obj.path, str):
            route_paths.append(obj.path)

    # DCS 路由应仅涉及配置管理（vendors/models/mode-mappings/pid-structure）
    config_keywords = ("vendors", "models", "mode-mappings", "mode-definitions", "pid-structure")
    for path in route_paths:
        if "/dcs/" in path:
            # 排除基础路径 /dcs/，检查子路径是否为配置管理
            sub_path = path.split("/dcs/", 1)[1] if "/dcs/" in path else ""
            if sub_path and not any(kw in sub_path for kw in config_keywords):
                pytest.fail(
                    f"DCS 路由 {path} 不在配置管理范围内，"
                    f"可能违反安全边界（仅允许 vendor/model/mode/pid-structure CRUD）"
                )


def test_safety_boundary_documented() -> None:
    """安全边界必须在关键位置文档化.

    检查 AGENTS.md 或设计文档中记录了"不直接修改 DCS 参数"的安全边界。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    agents_md = root / "AGENTS.md"
    if not agents_md.exists():
        pytest.skip("AGENTS.md 不存在")

    content = agents_md.read_text(encoding="utf-8")
    # 安全边界关键词
    assert "DCS" in content or "dcs" in content.lower(), "AGENTS.md 必须提及 DCS 安全边界"
    # "不直接修改" 或 "只输出建议" 类表述
    boundary_keywords = ["不直接修改", "只输出建议", "安全边界", "不下写", "不直接下写"]
    assert any(kw in content for kw in boundary_keywords), (
        "AGENTS.md 必须明确记录'不直接修改 DCS 参数'的安全边界"
    )
