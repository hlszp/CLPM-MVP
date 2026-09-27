"""diag_realtime_pipeline.classify_td_error 的错误分类用例（2026-09-27 现场事故沉淀）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diag_realtime_pipeline.py"
_spec = importlib.util.spec_from_file_location("diag_realtime_pipeline", SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_connection_failure_hints_profile_and_rest_port() -> None:
    kind, advice = mod.classify_td_error(
        RuntimeError("TDengine 执行失败: All connection attempts failed")
    )
    assert kind == "连接失败"
    assert "--profile tdengine" in advice
    assert "TDENGINE_PORT + 11" in advice


def test_auth_failure_hints_three_place_consistency() -> None:
    kind, advice = mod.classify_td_error(
        "RST ERROR connect server error, err:[0x357] Authentication failure"
    )
    assert kind == "认证失败"
    assert ".env.prod" in advice
    assert "--env-file" in advice


def test_database_not_exist_points_to_ddl_file() -> None:
    kind, advice = mod.classify_td_error(Exception("Database not exist"))
    assert kind == "库或表不存在"
    assert "02_point_history.sql" in advice


def test_table_not_exist_same_category() -> None:
    kind, _ = mod.classify_td_error("Table does not exist")
    assert kind == "库或表不存在"


def test_unknown_error_keeps_no_advice() -> None:
    kind, advice = mod.classify_td_error("unexpected failure xyz")
    assert kind == "其它错误"
    assert advice == ""
