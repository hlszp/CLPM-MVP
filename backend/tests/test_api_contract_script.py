"""API 契约对账脚本（scripts/check_api_contract.py）的轻量回归。

只覆盖纯函数与白名单结构，不导入 FastAPI app（避免 CI 里重复一次应用启动）；
完整的路由对账由 CI 步骤 `uv run python ../scripts/check_api_contract.py` 承担。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_api_contract.py"
ALLOWLIST = REPO_ROOT / "scripts" / "api-contract-allowlist.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_api_contract", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPathNorm:
    """路径归一化：两侧同口径（后端带 /api/v1，前端不带）。"""

    def test_strips_api_prefix(self) -> None:
        mod = _load_module()
        assert mod.norm("/api/v1/loops") == "/loops"
        assert mod.norm("/loops") == "/loops"

    def test_folds_path_params_and_interpolation(self) -> None:
        mod = _load_module()
        assert mod.norm("/api/v1/loops/{loop_id}/tags") == "/loops/*/tags"
        assert mod.norm("/tuning/tasks/${id}/status") == "/tuning/tasks/*/status"

    def test_drops_query_and_trailing_slash(self) -> None:
        mod = _load_module()
        assert mod.norm("/reports/overview?stage=S1") == "/reports/overview"
        assert mod.norm("/api/v1/loops/") == "/loops"

    def test_framework_paths_are_known(self) -> None:
        mod = _load_module()
        assert mod.FRAMEWORK_PATHS, "框架路由集合不得为空（否则 /docs 会被当成死端点）"


class TestAllowlistFile:
    """白名单必须是可解析 JSON 且结构符合脚本预期。"""

    def test_allowlist_shape(self) -> None:
        payload = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
        assert isinstance(payload.get("backendUnused"), list)
        assert isinstance(payload.get("frontendCallsWithoutBackend"), list)
        # 每条登记都要有原因，否则下个周期没人知道为什么留着
        reasons = payload.get("backendUnusedReasons") or {}
        for route in payload["backendUnused"]:
            assert reasons.get(route), f"白名单条目缺少原因：{route}"

    def test_registry_doc_exists_and_mentions_gate(self) -> None:
        doc = REPO_ROOT / "docs" / "过程文档" / "dead-code-registry-2026-09-24.md"
        assert doc.exists(), "死代码登记表缺失（白名单与本表必须同批更新）"
        assert "check_api_contract.py" in doc.read_text(encoding="utf-8")
