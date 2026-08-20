"""V62-P3-003 process_model_version 表结构与约束回归测试.

验证模型生命周期最小聚合的：
1. 表/列/索引在 ORM 元数据中正确声明；
2. 部分唯一索引 ``uk_process_model_version_current`` 存在（P3-004 并发一致性基础）；
3. ``(loop_id, version)`` 唯一索引存在（版本号单调不重复）；
4. CHECK 约束覆盖 status / model_type / theta_source / identify_method / confidence；
5. ``tuning_record.process_model_version_id`` 外键已声明（P3-006 引用基础）；
6. ORM 表集合含 38 张表（37 + process_model_version）。

本测试不连接数据库，仅断言 ``Base.metadata``，与 ``test_schema_convergence.py``
同口径；并发一致性的运行时验证（双写 CURRENT 报唯一约束冲突）由 P3-004 落地。
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint

from app.models import Base


def test_process_model_version_table_registered() -> None:
    """process_model_version 必须在 ORM 元数据中注册。"""
    assert "process_model_version" in Base.metadata.tables
    table = Base.metadata.tables["process_model_version"]
    # 核心字段存在
    expected_columns = {
        "id",
        "loop_id",
        "version",
        "status",
        "data_window_start",
        "data_window_end",
        "data_hash",
        "condition_summary",
        "algorithm_version",
        "identify_method",
        "model_type",
        "model_params",
        "theta_source",
        "sampling_period",
        "metrics",
        "residual_test",
        "uncertainty",
        "physical_feasibility",
        "confidence_level",
        "confidence_reason",
        "published_by",
        "published_at",
        "supersedes_version_id",
        "retired_reason",
        "retired_at",
        "retired_by",
        "created_by",
        "created_at",
    }
    assert expected_columns.issubset(set(table.columns.keys())), (
        f"缺少列: {expected_columns - set(table.columns.keys())}"
    )


def test_orm_has_38_tables() -> None:
    """ORM 表集合数量守恒断言。

    38（基线 37 + process_model_version）+ loop_integrity_snapshot（数据完整性巡检
    快照，2026-08-05 数据质量增强）= 39 + tuning_knowledge_entry（P3-01 整定
    知识库，2026-08-05）= 40 + 智能预警规则引擎 5 表（alert_rule /
    alert_rule_subscription / alert_event / alert_rule_audit_log /
    alert_suppression，2026-08-07）= 45 + diagnosis_run（MVP v2 诊断模块，
    2026-08-16）= 46 + loop_action_item（§9.4 回路处置建议，2026-08-18）= 47
    + handling_order（处置模块 v2.0 双实体工单，2026-08-20）= 48。
    """
    assert len(Base.metadata.tables) == 48


def test_partial_unique_index_current_exists() -> None:
    """P3-004 基础：uk_process_model_version_current 部分唯一索引必须声明.

    该索引仅对 status=CURRENT 的行生效，保证同一回路至多一个 CURRENT。
    """
    table = Base.metadata.tables["process_model_version"]
    matches = [i for i in table.indexes if i.name == "uk_process_model_version_current"]
    assert matches, "缺少部分唯一索引 uk_process_model_version_current"
    idx = matches[0]
    assert idx.unique is True
    assert [c.name for c in idx.columns] == ["loop_id"]
    # 部分索引必须有 postgresql_where 谓词
    dialect_opts = idx.dialect_options.get("postgresql", {})
    assert dialect_opts.get("where") is not None, "必须为部分唯一索引（含 WHERE 谓词）"


def test_loop_version_unique_index_exists() -> None:
    """(loop_id, version) 唯一索引必须存在，保证版本号单回路单调不重复。"""
    table = Base.metadata.tables["process_model_version"]
    matches = [i for i in table.indexes if i.name == "uk_process_model_version_loop_version"]
    assert matches, "缺少唯一索引 uk_process_model_version_loop_version"
    idx = matches[0]
    assert idx.unique is True
    assert [c.name for c in idx.columns] == ["loop_id", "version"]


def test_check_constraints_declared() -> None:
    """五项 CHECK 约束必须命名声明。"""
    table = Base.metadata.tables["process_model_version"]
    check_names = {c.name for c in table.constraints if isinstance(c, CheckConstraint) and c.name}
    expected = {
        "ck_process_model_version_status",
        "ck_process_model_version_model_type",
        "ck_process_model_version_theta_source",
        "ck_process_model_version_identify_method",
        "ck_process_model_version_confidence",
    }
    assert expected.issubset(check_names), f"缺少 CHECK 约束: {expected - check_names}"


def test_supersedes_self_reference_fk() -> None:
    """supersedes_version_id 自引用外键必须 ON DELETE SET NULL。"""
    table = Base.metadata.tables["process_model_version"]
    # 至少有一个指向自身的外键约束（supersedes_version_id → process_model_version.id）
    self_ref = [fk for fk in table.foreign_keys if fk.column.table.name == "process_model_version"]
    assert self_ref, "supersedes_version_id 必须自引用 process_model_version"
    # 自引用外键应为 SET NULL（不级联删除，保留历史链）
    assert all(fk.ondelete == "SET NULL" for fk in self_ref)


def test_tuning_record_references_model_version() -> None:
    """P3-006 基础：tuning_record.process_model_version_id 外键必须声明。"""
    table = Base.metadata.tables["tuning_record"]
    assert "process_model_version_id" in table.columns
    col = table.columns["process_model_version_id"]
    assert col.nullable is True, "process_model_version_id 必须可空（兼容旧 record）"
    fks = [fk for fk in table.foreign_keys if fk.parent.name == "process_model_version_id"]
    assert fks, "process_model_version_id 必须有外键约束"
    assert fks[0].column.table.name == "process_model_version"
    assert fks[0].ondelete == "SET NULL"


def test_no_extra_model_tables_added() -> None:
    """§7.3 约束：只新增 process_model_version 一个聚合，不得新增 process_model 主表或附表。"""
    forbidden = {
        "process_model",
        "process_model_approval",
        "process_model_condition",
        "process_model_error",
    }
    actual = set(Base.metadata.tables.keys())
    assert not (forbidden & actual), (
        f"违反 §7.3 最小聚合原则，出现了禁止的附表: {forbidden & actual}"
    )
