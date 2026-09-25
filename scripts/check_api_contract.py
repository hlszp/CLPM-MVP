#!/usr/bin/env python3
"""前端调用 与 后端路由 契约对账（CI 门禁，2026-09-24 新增）。

背景
----
2026-09-24 的系统性检查发现：
- 前端存在调用但后端不存在的接口（/menu/all）—— 只在切 accessMode=backend 时才
  暴露 404，静态检查缺失时长期潜伏；
- 后端有 20+ 条已注册但前端从不调用的端点（部分被 BFF 取代、部分是死代码），
  没有登记表也没有护栏，清理周期一到就无从下手。

本脚本给出可复现的对账（三类结论）：
- FE 调用了但 BE 无实现      -> ERROR（退出码 1，阻塞 CI）
- BE 已注册但 FE 从不调用    -> WARN（提示登记，不阻塞；--strict 下也失败）
- 白名单里已消失的条目        -> WARN（提示同步登记表）

用法
----
    cd backend && uv run python ../scripts/check_api_contract.py
    cd backend && uv run python ../scripts/check_api_contract.py --strict
    cd backend && uv run python ../scripts/check_api_contract.py --json

白名单：scripts/api-contract-allowlist.json
登记策略：docs/过程文档/dead-code-registry-2026-09-24.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FE_SRC = REPO_ROOT / "frontend" / "apps" / "web-antd" / "src"
ALLOWLIST_PATH = Path(__file__).resolve().parent / "api-contract-allowlist.json"

BT = chr(96)  # 反引号：避免在源码里直接书写，防止外层工具链转义问题

# 泛型可能跨行且内部含 ">"（如 Record<string, X>），故用 [^(]* 吃到左括号前再回溯；
# 否则 requestClient.post< 换行 A, 换行 B, 换行 >('path') 这类调用会被漏报（2026-09-24 实测）。
#: 匹配 requestClient.get/post/put/delete/download 的调用路径
CALL_RE = re.compile(
    r"requestClient\s*\.\s*(?:get|post|put|patch|delete|download|request)\s*(?:<[^(]*?)?\s*\(\s*"
    r"(?P<quote>[" + BT + r"'\"])(?P<path>[^" + BT + r"'\"]+)(?P=quote)",
    re.MULTILINE,
)
#: 直接传路径常量的调用（形如 requestClient.get(BASE)）——单独一条规则，
#: 避免把主正则写得过复杂（主正则只认字符串字面量第一参数）。
IDENT_CALL_RE = re.compile(
    r"requestClient\s*\.\s*(?:get|post|put|patch|delete|download|request)\s*"
    r"(?:<[^(]*?)?\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*[,)]",
)
#: 匹配 const BASE = 路径常量（api 模块内的路径前缀常量）
BASE_RE = re.compile(
    r"const\s+(?P<name>[A-Z_][A-Z0-9_]*)\s*=\s*"
    r"(?P<quote>[" + BT + r"'\"])(?P<val>[^" + BT + r"'\"]+)(?P=quote)"
)
#: 模板串插值无法静态求值，折叠为通配
INTERP_RE = re.compile(r"\$\{[^}]*\}")
#: FastAPI 路径参数 {id} 折叠为通配
PARAM_RE = re.compile(r"\{[^}]+\}")


#: 全局 API 前缀（后端 /api/v1，前端 requestClient baseURL 已含该前缀）
API_PREFIX = "/api/v1"
#: 框架自带路由（探针/文档），不参与对账
FRAMEWORK_PATHS = frozenset(
    {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}
)


def norm(path: str) -> str:
    """归一化路径：剥掉全局前缀、插值/路径参数折叠为 *、去查询串与尾斜杠。

    两侧必须用同一归一化口径：后端路由带 /api/v1 前缀，前端 api 模块写法不带。
    """
    path = path.split("?")[0].strip()
    path = INTERP_RE.sub("*", path)
    if not path.startswith("/"):
        path = "/" + path
    if path.startswith(API_PREFIX):
        path = path[len(API_PREFIX) :] or "/"
    path = PARAM_RE.sub("*", path)
    return path.rstrip("/") or "/"


#: 只对账业务方法（HEAD/OPTIONS 由框架派生）
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def collect_backend_routes() -> set[str]:
    """枚举后端已注册路由（含模块热插拔条件注册结果）。

    实现说明（2026-09-24 踩坑）：本项目的 ``include_router`` 走自研的
    ``_IncludedRouter`` 延迟展开机制 —— ``app.routes`` 只含 8 条框架路由
    （/docs、/metrics、/static 与 _IncludedRouter 占位），业务路由在
    ``app.openapi()`` 生成时才展开。因此**必须**用 OpenAPI schema 枚举，
    否则会得到"0 条后端路由"的假象并使全部前端调用被误判为缺失。
    """
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.main import app  # noqa: PLC0415 - 需要 sys.path 先就绪

    schema = app.openapi()
    routes: set[str] = set()
    for raw_path, item in (schema.get("paths") or {}).items():
        normalized = norm(raw_path)
        if normalized in FRAMEWORK_PATHS:
            continue
        for method in item:
            if method.lower() in HTTP_METHODS:
                routes.add(method.upper() + " " + normalized)
    return routes


def collect_frontend_calls() -> dict[str, set[str]]:
    """扫描前端源码，返回 {归一化路径: {出现文件}}。"""
    calls: dict[str, set[str]] = {}
    if not FE_SRC.exists():  # pragma: no cover - 缺前端目录时跳过
        return calls
    # .ts 与 .vue 都要扫：api 层之外，视图里也存在直接调用（2026-09-24 实测 19 处）
    for path in list(FE_SRC.rglob("*.ts")) + list(FE_SRC.rglob("*.vue")):
        if "__tests__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover
            continue
        bases = {
            m.group("name"): m.group("val")
            for m in BASE_RE.finditer(text)
            if m.group("val").startswith("/")
        }
        for m in CALL_RE.finditer(text):
            raw = m.group("path")
            for name, prefix in bases.items():
                raw = raw.replace("$" + "{" + name + "}", prefix)
            calls.setdefault(norm(raw), set()).add(str(path.relative_to(FE_SRC)))
        # 直接传常量的写法：requestClient.get(BASE) / requestClient.get(BASE, {...})
        for m in IDENT_CALL_RE.finditer(text):
            prefix = bases.get(m.group(1), "")
            if prefix:
                calls.setdefault(norm(prefix), set()).add(str(path.relative_to(FE_SRC)))
    return calls


def load_allowlist() -> dict[str, list[str]]:
    if not ALLOWLIST_PATH.exists():
        return {"backendUnused": [], "frontendDeadFunctions": []}
    return json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="API 契约对账")
    parser.add_argument("--strict", action="store_true", help="WARN 也返回非零退出码")
    parser.add_argument("--json", action="store_true", help="输出 JSON（CI 归档用）")
    args = parser.parse_args()

    routes = collect_backend_routes()
    calls = collect_frontend_calls()
    allow = load_allowlist()

    fe_paths = set(calls)
    be_paths = {r.split(" ", 1)[1] for r in routes}
    allowed_missing = set(allow.get("frontendCallsWithoutBackend", []))
    missing_all = sorted(p for p in fe_paths if p not in be_paths)
    missing_in_backend = [p for p in missing_all if p not in allowed_missing]
    known_missing = [p for p in missing_all if p in allowed_missing]

    allowed_unused = set(allow.get("backendUnused", []))
    unused_allowed_hits: set[str] = set()
    missing_in_frontend: list[str] = []
    for route in sorted(routes):
        method, path = route.split(" ", 1)
        if path in fe_paths:
            continue
        if route in allowed_unused or path in allowed_unused:
            unused_allowed_hits.add(route if route in allowed_unused else path)
            continue
        missing_in_frontend.append(route)

    stale_allowlist = sorted(allowed_unused - unused_allowed_hits)

    if args.json:
        print(
            json.dumps(
                {
                    "backendRoutes": len(routes),
                    "frontendCalls": len(fe_paths),
                    "missingInBackend": missing_in_backend,
                    "unusedBackendEndpoints": missing_in_frontend,
                    "staleAllowlist": stale_allowlist,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("后端已注册路由 %d 条；前端调用路径 %d 条" % (len(routes), len(fe_paths)))
        if missing_in_backend:
            print("[ERROR] 前端调用但后端无实现：%d 条" % len(missing_in_backend))
            for p in missing_in_backend:
                print("    - %s   （出现于 %s）" % (p, ", ".join(sorted(calls[p]))))
        else:
            print("[OK] 前端调用全部有后端实现")
        if known_missing:
            print("[WARN] 已知缺口（白名单内，前端调用但后端未实现）：%d 条" % len(known_missing))
            for p in known_missing:
                print("    - " + p)
        if missing_in_frontend:
            print("[WARN] 后端已注册但前端未调用（未登记）：%d 条" % len(missing_in_frontend))
            for r in missing_in_frontend:
                print("    - " + r)
            print("    处理：确为运维/直链/导入脚本消费的，登记到")
            print("          scripts/api-contract-allowlist.json 并在登记表注明原因。")
        else:
            print("[OK] 无未登记的死端点")
        if stale_allowlist:
            print("[WARN] 白名单中已不存在的条目：%d 条" % len(stale_allowlist))
            for r in stale_allowlist:
                print("    - " + r)

    failed = bool(missing_in_backend) or (
        args.strict and (missing_in_frontend or stale_allowlist)
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
