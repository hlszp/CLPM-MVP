"""G33 CI 断言：禁止裸 raise HTTPException（接口契约与权限，S5）。

背景与沿革
----------
计划中 S5 的验收标准原文包含「无裸 HTTPException（CI 断言）」。
实跑核实（第 19 轮）：

- 全仓 raise HTTPException 计数 = 0，raise BizError = 437
  —— 即**整改本身早已完成**；
- 但 tests/ 下 grep 该模式**无任何命中**
  —— 即**该验收标准要求的 CI 断言从未建立**。

于是形成「状态达成但**无守卫**」的局面：下一位开发者可以随手重新引入裸
HTTPException（绕过统一的错误码与响应体契约），而**没有任何检查会发现**。

这与本次治理反复出现的模式同源：**陈述与实际可能背离，而没有守卫时无人发现**
（对照验收记录 §9 第 3 条「守卫要验数值/状态，不能只验调用」、
§11.3「文档与代码不符无守卫」）。

实现说明
--------
用 **AST** 而非文本 grep：文本匹配会把注释、docstring 里出现的该模式
误判为违规（本文件自身就包含该字符串）。AST 只识别真正的 ast.Raise 节点。
"""

from __future__ import annotations

import ast
from pathlib import Path

from _pytest.tmpdir import TempPathFactory  # noqa: F401  (类型提示用，运行时由 pytest 提供)

_APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _find_bare_http_exception(source: str, filename: str = "<src>") -> list[str]:
    """AST 扫描：找出 raise HTTPException(...) 的位置（返回 file:line 列表）。"""
    violations: list[str] = []
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc
        if isinstance(exc, ast.Call):
            func = exc.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "HTTPException":
                violations.append(f"{filename}:{node.lineno}")
    return violations


def _scan_app() -> list[str]:
    violations: list[str] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        violations.extend(_find_bare_http_exception(path.read_text(encoding="utf-8"), str(path)))
    return violations


class TestNoBareHttpException:
    """S5 验收标准：接口层不得绕过统一错误契约。"""

    def test_app_has_no_bare_http_exception(self) -> None:
        """app/ 下不得出现裸 raise HTTPException（应使用 BizError 统一契约）。

        实跑基线（第 19 轮）：0 处。本断言的作用是**防止回归**，
        而非修复现状——现状已达标但此前无守卫。
        """
        violations = _scan_app()
        assert violations == [], (
            "发现裸 raise HTTPException（应改用 BizError 以保持统一错误码与响应体）:\n"
            + "\n".join(violations)
        )

    def test_app_uses_bizerror(self) -> None:
        """反向确认统一契约确实被使用（避免扫不到是因为路径写错）。"""
        total = 0
        for path in sorted(_APP_ROOT.rglob("*.py")):
            total += path.read_text(encoding="utf-8").count("raise BizError")
        assert total > 100, f"BizError 使用量异常偏低（{total}），检查扫描根路径：{_APP_ROOT}"


class TestGuardItselfCanFire:
    """守卫必须能被证明会触发——不可达的守卫比没有守卫更糟。"""

    def test_guard_detects_planted_raise(self, tmp_path: Path) -> None:
        planted = tmp_path / "planted.py"
        planted.write_text(
            "from fastapi import HTTPException\n\n"
            "def f():\n"
            "    raise HTTPException(status_code=400, detail='x')\n",
            encoding="utf-8",
        )
        found = _find_bare_http_exception(planted.read_text(encoding="utf-8"), str(planted))
        assert len(found) == 1, f"守卫未能识别植入的违规：{found}"

    def test_guard_ignores_mentions_in_comments_and_docstrings(self, tmp_path: Path) -> None:
        """注释/docstring 中的同名文本不得误报（这正是选用 AST 的原因）。"""
        benign = tmp_path / "benign.py"
        benign.write_text(
            '"""文档：不要写 raise HTTPException(...)。"""\n'
            "# 注释：raise HTTPException(status_code=400)\n"
            "VALUE = 'raise HTTPException'\n",
            encoding="utf-8",
        )
        assert _find_bare_http_exception(benign.read_text(encoding="utf-8"), str(benign)) == []

    def test_guard_also_catches_attribute_form(self, tmp_path: Path) -> None:
        """raise fastapi.HTTPException(...) 这类属性形式同样应被识别。"""
        planted = tmp_path / "attr_form.py"
        planted.write_text(
            "import fastapi\n\ndef f():\n    raise fastapi.HTTPException(status_code=500)\n",
            encoding="utf-8",
        )
        found = _find_bare_http_exception(planted.read_text(encoding="utf-8"), str(planted))
        assert len(found) == 1, f"属性形式未被识别：{found}"

    def test_no_violation_reported_when_clean(self, tmp_path: Path) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("def f():\n    raise ValueError('ok')\n", encoding="utf-8")
        assert _find_bare_http_exception(clean.read_text(encoding="utf-8"), str(clean)) == []
