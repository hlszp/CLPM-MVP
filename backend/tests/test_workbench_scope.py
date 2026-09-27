"""工作台 scope_id 口径与排名空原因回归用例（2026-09-28）。"""

from __future__ import annotations

from app.services.workbench_overview import _plants_empty_reason
from app.services.workbench_scope import FALLBACK_BASE, node_scope_id


class TestNodeScopeId:
    def test_uses_source_node_id_when_present(self) -> None:
        assert node_scope_id(1000, "uuid-1") == 1000

    def test_fallback_is_stable_and_in_reserved_range(self) -> None:
        a = node_scope_id(None, "uuid-1")
        b = node_scope_id(None, "uuid-1")
        assert a == b
        assert a >= FALLBACK_BASE
        assert a != node_scope_id(None, "uuid-2")

    def test_invalid_source_falls_back(self) -> None:
        assert node_scope_id("not-an-int", "uuid-1") == node_scope_id(None, "uuid-1")


class TestPlantsEmptyReason:
    def test_none_when_rows_exist(self) -> None:
        assert _plants_empty_reason({"factories": [object()]}, [{"id": 1}]) is None

    def test_no_org_nodes(self) -> None:
        assert _plants_empty_reason({}, None) == "NO_ORG_NODES"

    def test_no_precalc_rows(self) -> None:
        assert _plants_empty_reason({"factories": [object()]}, None) == "NO_PRECALC_ROWS"
