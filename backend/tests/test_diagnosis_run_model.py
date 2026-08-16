"""DiagnosisRun 模型元数据测试。"""

from __future__ import annotations

from app.models import DiagnosisRun
from app.models.base import Base


def test_diagnosis_run_table_metadata() -> None:
    table = DiagnosisRun.__table__
    assert table.name == "diagnosis_run"
    cols = {c.name for c in table.columns}
    expected = {
        "id",
        "task_id",
        "loop_id",
        "triggered_by",
        "time_window_start",
        "time_window_end",
        "operator_group",
        "status",
        "data_gate",
        "operator_results",
        "fusion_results",
        "symptom_tags",
        "primary_category",
        "primary_confidence",
        "secondary_categories",
        "pending_review",
        "severity",
        "rationale",
        "recommendations",
        "evidence_charts",
        "threshold_version",
        "algorithm_version",
        "started_at",
        "finished_at",
        "duration_ms",
        "created_at",
        "updated_at",
    }
    assert expected <= cols
    constraints = {c.name for c in table.constraints if hasattr(c, "name") and c.name}
    assert "ck_diagnosis_run_status" in constraints
    assert "ck_diagnosis_run_category" in constraints
    assert "ck_diagnosis_run_severity" in constraints
    index_names = {ix.name for ix in table.indexes}
    assert "idx_diagnosis_run_loop_created" in index_names
    assert "idx_diagnosis_run_category" in index_names
    # 注册进 Base.metadata（alembic autogenerate 依赖）
    assert "diagnosis_run" in Base.metadata.tables
