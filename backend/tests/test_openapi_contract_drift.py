"""OpenAPI 契约漂移检查（V62-P0-034）。

以 ``tests/golden/openapi_baseline.json`` 为基线，断言当前 ``app.openapi()``
不发生 breaking change，允许 non-breaking 新增。

Breaking change（禁止，测试失败）：
- 删除路径 / 删除 HTTP 方法
- 删除响应状态码
- 删除 schema 组件 / 删除字段 / 字段类型变更
- 可选字段变必填（required 列表扩大）
- 路径参数/查询参数的 required 由 false 变 true

Non-breaking（允许，测试通过）：
- 新增路径 / 新增方法 / 新增状态码
- 新增 schema 组件 / 新增可选字段
- description / summary / example 变更（不比对）
- operationId 变更（不比对，函数改名会变）

刷新基线（API 变更后）::

    uv run python scripts/export_openapi.py --output tests/golden/openapi_baseline.json

---

2026-09-13 整改 S1-b（恢复守护）
----------------------------
本文件此前以 ``pytestmark = pytest.mark.skip`` **整文件跳过**，理由为
"MVP 精简：诊断/整定/AAS/tracker API 路径已屏蔽，OpenAPI 基线漂移为预期行为"。
后果：前后端之间唯一的自动化契约来源失效——前端 273 个 API 封装全部手写，
38 个已无调用方却无人察觉。

本次核对确认"漂移"实际只有两条**已登记的下线**，其余全为新增：
- ``/api/v1/algorithms/diagnosis/analyze``：旧诊断引擎唯一活跃写入口，
  2026-08-27 随 14 号文 A1~A4 解除注册退役；
- ``/api/v1/loops/data-import/integrity-check``：随 ``91938a5f`` 下线完整性检查、
  导入收敛为幂等覆盖而移除。
故基线已按当前 schema 重新固化并恢复实跑。今后任何 breaking change 都必须
显式重新固化基线，不允许再以 skip 绕过。

**比对逻辑抽为模块级纯函数**：原实现内联在测试方法中，无法自证"该守护真的
会失败"。配合 ``TestContractDriftDetectorSelfCheck`` 用合成 schema 逐项验证
每个检测器都能报出对应的 breaking change——"从未被证明会失败的守护不是守护"。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

#: OpenAPI 基线路径
_BASELINE_PATH = Path(__file__).parent / "golden" / "openapi_baseline.json"

#: 参与契约比对的方法
_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


# ---------------------------------------------------------------------------
# 纯比对函数（可独立测试，见文件末尾自检用例）
# ---------------------------------------------------------------------------


def _schema_field_type(field: dict[str, Any]) -> str:
    """提取字段的类型签名（type/format/$ref/items），用于检测类型变更。

    递归处理 array 的 items 与 anyOf/oneOf/allOf。
    """
    if "$ref" in field:
        return field["$ref"]
    parts = [field.get("type", ""), field.get("format", "")]
    if field.get("type") == "array" and "items" in field:
        parts.append(f"items={_schema_field_type(field['items'])}")
    for combiner in ("anyOf", "oneOf", "allOf"):
        if combiner in field:
            parts.append(f"{combiner}=[{','.join(_schema_field_type(f) for f in field[combiner])}]")
    return "|".join(p for p in parts if p)


def _schemas(doc: dict[str, Any]) -> dict[str, Any]:
    return doc.get("components", {}).get("schemas", {})


def diff_paths_removed(baseline: dict, current: dict) -> list[str]:
    """基线路径集合 ⊄ 当前路径集合 → 返回被删除的路径。"""
    return sorted(set(baseline.get("paths", {})) - set(current.get("paths", {})))


def diff_methods_removed(baseline: dict, current: dict) -> list[str]:
    """共有路径上被删除的 HTTP 方法。"""
    errors: list[str] = []
    for path, b_ops in baseline.get("paths", {}).items():
        c_ops = current.get("paths", {}).get(path)
        if c_ops is None:
            continue  # 路径删除由 diff_paths_removed 覆盖
        missing = {m for m in b_ops if m.lower() in _METHODS} - {
            m for m in c_ops if m.lower() in _METHODS
        }
        if missing:
            errors.append(f"{path}: {sorted(missing)}")
    return errors


def diff_status_removed(baseline: dict, current: dict) -> list[str]:
    """共有 operation 上被删除的响应状态码。"""
    errors: list[str] = []
    for path, b_ops in baseline.get("paths", {}).items():
        c_ops = current.get("paths", {}).get(path)
        if c_ops is None:
            continue
        for method, b_op in b_ops.items():
            if method.lower() not in _METHODS or method not in c_ops:
                continue
            missing = set(b_op.get("responses", {})) - set(c_ops[method].get("responses", {}))
            if missing:
                errors.append(f"{path} {method.upper()}: {sorted(missing)}")
    return errors


def diff_schemas_removed(baseline: dict, current: dict) -> list[str]:
    """被删除的 schema 组件。"""
    return sorted(set(_schemas(baseline)) - set(_schemas(current)))


def diff_fields_changed(baseline: dict, current: dict) -> list[str]:
    """共有 schema 中被删除的字段与发生类型变更的字段。"""
    errors: list[str] = []
    b_schemas, c_schemas = _schemas(baseline), _schemas(current)
    for name, b_schema in b_schemas.items():
        if name not in c_schemas:
            continue  # schema 删除由 diff_schemas_removed 覆盖
        b_props = b_schema.get("properties", {})
        c_props = c_schemas[name].get("properties", {})
        removed = sorted(set(b_props) - set(c_props))
        if removed:
            errors.append(f"{name}: 字段被删除 {removed}")
        for field in sorted(set(b_props) & set(c_props)):
            b_type = _schema_field_type(b_props[field])
            c_type = _schema_field_type(c_props[field])
            if b_type != c_type:
                errors.append(f"{name}.{field}: 类型变更 '{b_type}' -> '{c_type}'")
    return errors


def diff_required_expanded(baseline: dict, current: dict) -> list[str]:
    """可选字段变必填（当前 required - 基线 required）。"""
    errors: list[str] = []
    b_schemas, c_schemas = _schemas(baseline), _schemas(current)
    for name, b_schema in b_schemas.items():
        if name not in c_schemas:
            continue
        new_required = set(c_schemas[name].get("required", [])) - set(b_schema.get("required", []))
        if new_required:
            errors.append(f"{name}: 新增必填字段 {sorted(new_required)}")
    return errors


def diff_params_tightened(baseline: dict, current: dict) -> list[str]:
    """路径/查询参数 required 由 false 变 true。"""
    errors: list[str] = []
    for path, b_ops in baseline.get("paths", {}).items():
        c_ops = current.get("paths", {}).get(path)
        if c_ops is None:
            continue
        for method, b_op in b_ops.items():
            if method.lower() not in _METHODS or method not in c_ops:
                continue
            b_params = {p["name"]: p for p in b_op.get("parameters", [])}
            c_params = {p["name"]: p for p in c_ops[method].get("parameters", [])}
            for pname, b_param in b_params.items():
                if pname not in c_params:
                    continue
                if not b_param.get("required", False) and c_params[pname].get("required", False):
                    errors.append(f"{path} {method.upper()} param '{pname}': required false->true")
    return errors


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _load_baseline() -> dict[str, Any]:
    """加载 OpenAPI 基线 JSON。"""
    if not _BASELINE_PATH.exists():
        pytest.skip(
            f"OpenAPI 基线不存在：{_BASELINE_PATH}\n"
            "请先执行：uv run python scripts/export_openapi.py "
            "--output tests/golden/openapi_baseline.json"
        )
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def _current_schema() -> dict[str, Any]:
    """获取当前应用的 OpenAPI schema。"""
    from app.main import app

    return app.openapi()


class TestOpenApiContractDrift:
    """OpenAPI 契约漂移检查（V62-P0-034）。"""

    @pytest.fixture(scope="class")
    def baseline(self) -> dict[str, Any]:
        return _load_baseline()

    @pytest.fixture(scope="class")
    def current(self) -> dict[str, Any]:
        return _current_schema()

    def test_baseline_file_exists_and_valid(self, baseline: dict) -> None:
        """基线文件存在且结构完整。

        不断言 info.title / info.version 的具体值：FastAPI 的 title/version 取自
        settings.APP_NAME / APP_VERSION，而 pydantic-settings 的优先级为
        环境变量 > .env > 默认值——开发者本机 backend/.env（已 gitignore）会覆盖
        它们，导出结果随环境变化（实测本机为 CLPM-MVP / 1.0.0，仓库默认为
        CLPM / 7.1.0）。契约比对只关心 paths 与 components.schemas，故此处
        只校验结构；基线固化时已用仓库口径的 APP_NAME/APP_VERSION 覆盖。
        """
        assert isinstance(baseline.get("info"), dict)
        assert baseline["info"].get("title"), "基线 info.title 不应为空"
        assert "paths" in baseline
        assert "components" in baseline

    def test_no_path_removed(self, baseline: dict, current: dict) -> None:
        removed = diff_paths_removed(baseline, current)
        assert not removed, f"以下路径被删除（breaking change）：{removed}"

    def test_no_method_removed_per_path(self, baseline: dict, current: dict) -> None:
        errors = diff_methods_removed(baseline, current)
        assert not errors, f"以下路径的 HTTP 方法被删除（breaking change）：{errors}"

    def test_no_response_status_removed(self, baseline: dict, current: dict) -> None:
        errors = diff_status_removed(baseline, current)
        assert not errors, f"以下响应状态码被删除（breaking change）：{errors}"

    def test_no_schema_removed(self, baseline: dict, current: dict) -> None:
        removed = diff_schemas_removed(baseline, current)
        assert not removed, f"以下 schema 组件被删除（breaking change）：{removed}"

    def test_no_field_removed_or_retyped(self, baseline: dict, current: dict) -> None:
        errors = diff_fields_changed(baseline, current)
        assert not errors, "schema 字段 breaking change：\n" + "\n".join(errors)

    def test_required_not_expanded(self, baseline: dict, current: dict) -> None:
        errors = diff_required_expanded(baseline, current)
        assert not errors, "可选字段变必填（breaking change）：\n" + "\n".join(errors)

    def test_no_param_required_tightened(self, baseline: dict, current: dict) -> None:
        errors = diff_params_tightened(baseline, current)
        assert not errors, "参数 required 收紧（breaking change）：\n" + "\n".join(errors)

    def test_compare_uses_compare_request(self, current: dict) -> None:
        """/api/v1/tuning/compare POST 请求体必须引用 CompareRequest（V62-P0-030）。"""
        compare_op = current["paths"]["/api/v1/tuning/compare"]["post"]
        request_body = compare_op["requestBody"]["content"]["application/json"]["schema"]
        ref = request_body.get("$ref", "")
        assert ref == "#/components/schemas/CompareRequest", (
            f"/compare 请求体应引用 CompareRequest，实际引用：{ref}"
        )


# ---------------------------------------------------------------------------
# 守护自检：证明每个检测器真的会失败
# ---------------------------------------------------------------------------


class TestContractDriftDetectorSelfCheck:
    """用合成 schema 验证检测器有效性。

    整改 S1-b：原比对逻辑内联在测试方法中，无法证明"该守护能失败"。
    本类对每个 breaking change 造一个最小样例，断言检测器报出问题；
    同时对合法变更（新增路径/可选字段）断言不误报。
    """

    @staticmethod
    def _doc(**kwargs: Any) -> dict[str, Any]:
        return {
            "info": {"title": "CLPM"},
            "paths": kwargs.get("paths", {}),
            "components": {"schemas": kwargs.get("schemas", {})},
        }

    def test_detects_removed_path(self) -> None:
        b = self._doc(paths={"/api/v1/a": {"get": {"responses": {"200": {}}}}})
        c = self._doc(paths={})
        assert diff_paths_removed(b, c) == ["/api/v1/a"]

    def test_detects_removed_method(self) -> None:
        b = self._doc(paths={"/api/v1/a": {"get": {"responses": {}}, "post": {"responses": {}}}})
        c = self._doc(paths={"/api/v1/a": {"get": {"responses": {}}}})
        assert diff_methods_removed(b, c) == ["/api/v1/a: ['post']"]

    def test_detects_removed_status_code(self) -> None:
        b = self._doc(paths={"/api/v1/a": {"get": {"responses": {"200": {}, "404": {}}}}})
        c = self._doc(paths={"/api/v1/a": {"get": {"responses": {"200": {}}}}})
        assert diff_status_removed(b, c) == ["/api/v1/a GET: ['404']"]

    def test_detects_removed_schema(self) -> None:
        b = self._doc(paths={}, schemas={"Gone": {"type": "object"}})
        c = self._doc(paths={}, schemas={})
        assert diff_schemas_removed(b, c) == ["Gone"]

    def test_detects_removed_field(self) -> None:
        b = self._doc(schemas={"S": {"properties": {"a": {"type": "string"}}}})
        c = self._doc(schemas={"S": {"properties": {}}})
        assert "S: 字段被删除 ['a']" in diff_fields_changed(b, c)

    def test_detects_retyped_field(self) -> None:
        b = self._doc(schemas={"S": {"properties": {"a": {"type": "string"}}}})
        c = self._doc(schemas={"S": {"properties": {"a": {"type": "integer"}}}})
        assert diff_fields_changed(b, c), "字段类型变更未被检出"

    def test_detects_required_expanded(self) -> None:
        b = self._doc(schemas={"S": {"properties": {"a": {}}, "required": []}})
        c = self._doc(schemas={"S": {"properties": {"a": {}}, "required": ["a"]}})
        assert diff_required_expanded(b, c) == ["S: 新增必填字段 ['a']"]

    def test_detects_param_required_tightened(self) -> None:
        b = self._doc(
            paths={"/api/v1/a": {"get": {"parameters": [{"name": "p", "required": False}]}}}
        )
        c = self._doc(
            paths={"/api/v1/a": {"get": {"parameters": [{"name": "p", "required": True}]}}}
        )
        assert diff_params_tightened(b, c), "参数 required 收紧未被检出"

    def test_additive_changes_do_not_raise(self) -> None:
        """合法变更（新增路径/方法/状态码/可选字段/schema）不得误报。"""
        b = self._doc(
            paths={"/api/v1/a": {"get": {"responses": {"200": {}}}}},
            schemas={"S": {"properties": {"a": {"type": "string"}}}},
        )
        c = self._doc(
            paths={
                "/api/v1/a": {
                    "get": {"responses": {"200": {}, "201": {}}},
                    "post": {"responses": {}},
                },
                "/api/v1/b": {"get": {"responses": {}}},
            },
            schemas={
                "S": {"properties": {"a": {"type": "string"}, "b": {"type": "integer"}}},
                "T": {},
            },
        )
        assert not diff_paths_removed(b, c)
        assert not diff_methods_removed(b, c)
        assert not diff_status_removed(b, c)
        assert not diff_schemas_removed(b, c)
        assert not diff_fields_changed(b, c)
        assert not diff_required_expanded(b, c)
        assert not diff_params_tightened(b, c)
