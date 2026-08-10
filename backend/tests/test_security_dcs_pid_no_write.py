"""安全边界静态断言：禁止 DCS PID 参数下写端点（MW-G0-04 基线）.

背景
----
监控—回路工作台闭环整改（monitor-workbench-closed-loop）冻结的安全边界：
平台不直接修改 DCS 的 P/I/D 参数，只输出建议、证据、风险和回退方案；
参数由授权人员人工实施并留痕（见 AGENTS.md §安全边界、整改计划 §1.5）。

本测试以 OpenAPI 为真相源，静态扫描所有写操作（POST/PUT/PATCH/DELETE），
断言不出现 DCS PID 参数下写端点。若新增端点命中下列模式，测试失败：

1. 路径同时含 ``dcs`` 与 ``pid``，且动作词命中 ``write|apply|commit|push|deploy|下发|下写|写入``；
2. 路径含 ``tuning``，且动作词命中 ``apply|commit|push|deploy|下发|下写``（整定结果回写 DCS）；
3. 路径含 ``loops/{loop_id}/pid``，且方法为 POST/PUT/PATCH。

只读端点（GET）、辨识/仿真/对比等纯计算端点不触发断言。

刷新基线（如未来产品决策放开安全边界，须显式更新本测试并升级契约）::
    本测试无 golden 文件，直接断言当前 OpenAPI；任何新增写端点都会被捕获。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from fastapi import FastAPI
from fastapi.testclient import TestClient

_WRITE_METHODS = ("post", "put", "patch", "delete")


def _get_app(client: TestClient) -> FastAPI:
    """从 TestClient 提取底层 FastAPI 实例。"""
    return client.app


#: DCS PID 下写路径模式：路径含 dcs 且含 pid，且动作词命中下写语义
_DCS_PID_WRITE_PATH = re.compile(
    r"(?i)(?=.*dcs)(?=.*pid).*(write|apply|commit|push|deploy|下发|下写|写入)"
)

#: 整定结果回写 DCS 模式：tuning 路径 + 下写动作词
_TUNING_DCS_WRITE_PATH = re.compile(r"(?i)tuning.*(apply|commit|push|deploy|下发|下写)")

#: 回路 PID 参数直写模式：loops/{id}/pid 且为写方法
_LOOP_PID_WRITE_PATH = re.compile(r"(?i)/loops/[^/]+/pid(/|$)")


def _iter_write_operations(
    app: FastAPI,
) -> Iterable[tuple[str, str]]:
    """遍历 OpenAPI 所有写操作，返回 (method, path)。"""
    schema = app.openapi()
    paths: dict[str, dict[str, object]] = schema.get("paths", {}) or {}
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method in _WRITE_METHODS:
            if method in methods:
                yield method.upper(), path


class TestNoDcsPidWriteEndpoint:
    """断言 OpenAPI 中不存在 DCS PID 参数下写端点。"""

    def test_no_dcs_pid_write_endpoint(self, client: TestClient) -> None:
        """路径含 dcs+pid 且动作词命中下写语义的端点必须不存在。"""
        app = _get_app(client)
        offenders = [
            f"{method} {path}"
            for method, path in _iter_write_operations(app)
            if _DCS_PID_WRITE_PATH.search(path)
        ]
        assert not offenders, (
            "检测到 DCS PID 参数下写端点，违反安全边界：\n  - "
            + "\n  - ".join(offenders)
            + "\n如确需放开，须显式升级实现契约与安全边界决策。"
        )

    def test_no_tuning_dcs_write_endpoint(self, client: TestClient) -> None:
        """整定结果回写 DCS 的端点必须不存在。"""
        app = _get_app(client)
        offenders = [
            f"{method} {path}"
            for method, path in _iter_write_operations(app)
            if _TUNING_DCS_WRITE_PATH.search(path)
        ]
        assert not offenders, "检测到整定结果回写 DCS 端点，违反安全边界：\n  - " + "\n  - ".join(
            offenders
        )

    def test_no_loop_pid_write_endpoint(self, client: TestClient) -> None:
        """回路 PID 参数直写端点必须不存在。"""
        app = _get_app(client)
        offenders = [
            f"{method} {path}"
            for method, path in _iter_write_operations(app)
            if _LOOP_PID_WRITE_PATH.search(path)
        ]
        assert not offenders, "检测到回路 PID 参数直写端点，违反安全边界：\n  - " + "\n  - ".join(
            offenders
        )

    def test_tuning_endpoints_are_read_or_compute_only(self, client: TestClient) -> None:
        """整定模块写端点只允许辨识/整定/仿真/对比/任务管理等计算类动作。"""
        app = _get_app(client)
        allowed_tuning_write_patterns = re.compile(
            r"(?i)(identify|tune|simulate|compare|tasks|cancel|calculate)"
        )
        offenders: list[str] = []
        for method, path in _iter_write_operations(app):
            if "/tuning" not in path:
                continue
            if not allowed_tuning_write_patterns.search(path):
                offenders.append(f"{method} {path}")
        assert not offenders, "整定模块出现非计算类写端点，须复核是否越界：\n  - " + "\n  - ".join(
            offenders
        )
