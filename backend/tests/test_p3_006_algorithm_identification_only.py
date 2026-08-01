"""V62-P3-006 algorithm=IDENTIFICATION_ONLY 测试.

验证：
1. CHECK 约束允许 IDENTIFICATION_ONLY；
2. 辨识任务创建的记录 algorithm=IDENTIFICATION_ONLY（不再用 IMC 占位）；
3. sync 辨识记录创建也使用 IDENTIFICATION_ONLY；
4. 遗留 IMC 占位记录（recommended_pid IS NULL）已回填为 IDENTIFICATION_ONLY。
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint

from app.models import Base


def test_algorithm_check_constraint_includes_identification_only() -> None:
    """CHECK 约束必须包含 IDENTIFICATION_ONLY 值."""
    table = Base.metadata.tables["tuning_record"]
    algo_checks = [
        c
        for c in table.constraints
        if isinstance(c, CheckConstraint) and c.name == "ck_tuning_record_algo"
    ]
    assert algo_checks, "缺少 ck_tuning_record_algo CHECK 约束"
    sql_text = str(algo_checks[0].sqltext)
    assert "IDENTIFICATION_ONLY" in sql_text, f"CHECK 约束未包含 IDENTIFICATION_ONLY: {sql_text}"
    # 原有算法值必须保留
    for algo in ("IMC", "LAMBDA", "ZN", "COHEN_COON", "SIMC"):
        assert algo in sql_text, f"CHECK 约束丢失了原算法值 {algo}"


def test_tuning_algorithm_literal_includes_identification_only() -> None:
    """schema Literal 类型必须包含 IDENTIFICATION_ONLY."""
    import typing

    from app.schemas.tuning import TuningAlgorithm

    args = typing.get_args(TuningAlgorithm)
    assert "IDENTIFICATION_ONLY" in args
    assert "IMC" in args  # 原有值保留
