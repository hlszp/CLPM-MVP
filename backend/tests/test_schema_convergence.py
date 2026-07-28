"""模型元数据收敛单测（P2 数据正确性整改 — ORM 与库 schema 漂移收敛）.

不依赖真实数据库，直接断言 ``Base.metadata`` 与收敛迁移
``d4e5f6a7b8c9_converge_schema_drift.py`` 的约定一致：

1. ``sys_audit_log.target_id`` 为 ``String(36)``（以库为准，target_id 可能是
   loop_id / user_id / 报表 id / 任务 id 等非 UUID 业务标识）。
2. 10 个 uk_* 以命名 ``UniqueConstraint`` 声明（与库中约束口径一致，
   而非同名唯一索引）。
3. 库中已有索引 ``idx_kpi_snapshot_ts_loop`` / ``idx_loop_ledger_dcs_model``
   已补入模型元数据（避免 autogen 误 DROP 生产索引）。
4. ``loop_ledger`` / ``plant_node`` / ``sys_user`` 的
   ``created_at`` / ``updated_at`` 在模型中为 NOT NULL
   （迁移 d4e5f6a7b8c9 已将库对齐为 NOT NULL）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import String, UniqueConstraint

from app.models import Base

#: （表名, uk_* 约束名, 期望列）——收敛为命名 UniqueConstraint 的 10 处
_UK_CONSTRAINTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("loop_ledger", "uk_loop_ledger_tag_name", ("tag_name",)),
    ("loop_tag_mapping", "uk_loop_tag_mapping_loop_role", ("loop_id", "tag_role")),
    ("sys_user", "uk_sys_user_username", ("username",)),
    ("sys_user", "uk_sys_user_email", ("email",)),
    ("tag_registry", "uk_tag_registry_tag_name", ("tag_name",)),
    ("loop_mode_mapping", "uk_loop_mode_mapping_loop_mode", ("loop_id", "mode_value")),
    ("algorithm_parameter", "uk_algorithm_param_code_type", ("metric_code", "control_type")),
    ("engine_rule", "uk_engine_rule_code", ("rule_code",)),
    ("metric_config", "uk_metric_config_code", ("metric_code",)),
    ("diagnosis_config", "uk_diagnosis_config_code", ("diag_code",)),
)

#: （表名, 索引名, 期望列顺序）——库中已有、须保留在元数据中的索引
_REQUIRED_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("kpi_snapshot_hourly", "idx_kpi_snapshot_ts_loop", ("ts_start", "loop_id")),
    ("loop_ledger", "idx_loop_ledger_dcs_model", ("dcs_model_id",)),
)

#: （表名, 列名）——模型与库均为 NOT NULL 的时间戳列
_NOT_NULL_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (table, column)
    for table in ("loop_ledger", "plant_node", "sys_user")
    for column in ("created_at", "updated_at")
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "d4e5f6a7b8c9_converge_schema_drift.py"
)


def test_audit_log_target_id_is_string36() -> None:
    """target_id 以库为准为 VARCHAR(36)，模型不再声明 UUID 类型。"""
    column = Base.metadata.tables["sys_audit_log"].c.target_id
    assert isinstance(column.type, String)
    assert column.type.length == 36
    assert column.nullable is True


def test_uk_constraints_are_named_unique_constraints() -> None:
    """10 个 uk_* 必须是命名 UniqueConstraint（与库口径一致）。"""
    for table_name, uk_name, columns in _UK_CONSTRAINTS:
        table = Base.metadata.tables[table_name]
        matches = [
            c for c in table.constraints if isinstance(c, UniqueConstraint) and c.name == uk_name
        ]
        assert matches, f"{table_name} 缺少命名 UniqueConstraint {uk_name}"
        assert tuple(col.name for col in matches[0].columns) == columns
        # 不得残留同名唯一索引（否则 autogen 仍会报 约束↔索引 互换噪声）
        assert not any(idx.name == uk_name for idx in table.indexes), (
            f"{table_name} 仍存在同名唯一索引 {uk_name}"
        )


def test_db_existing_indexes_present_in_metadata() -> None:
    """库中已有索引必须在模型元数据中，防止 autogen 生成 DROP 生产索引。"""
    for table_name, index_name, columns in _REQUIRED_INDEXES:
        table = Base.metadata.tables[table_name]
        matches = [idx for idx in table.indexes if idx.name == index_name]
        assert matches, f"{table_name} 元数据缺少索引 {index_name}"
        assert tuple(col.name for col in matches[0].columns) == columns
        assert matches[0].unique is False


def test_timestamp_columns_not_null_in_metadata() -> None:
    """created_at/updated_at 以模型为准为 NOT NULL（迁移已对齐库）。"""
    for table_name, column_name in _NOT_NULL_COLUMNS:
        column = Base.metadata.tables[table_name].c[column_name]
        assert column.nullable is False, f"{table_name}.{column_name} 应为 NOT NULL"


def test_convergence_migration_revision_chain() -> None:
    """收敛迁移存在、revision 链正确、upgrade/downgrade 均已实现。"""
    assert _MIGRATION_PATH.exists(), f"缺少收敛迁移 {_MIGRATION_PATH.name}"
    spec = importlib.util.spec_from_file_location("converge_schema_drift", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "d4e5f6a7b8c9"
    # down_revision 必须指向当前 head 的前驱（P2 阈值种子迁移）
    assert module.down_revision == "c3d4e5f6a7b8"
    assert callable(module.upgrade)
    assert callable(module.downgrade)
