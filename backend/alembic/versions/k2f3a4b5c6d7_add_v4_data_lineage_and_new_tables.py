"""add v4.0 data lineage fields and 4 new tables

v4.0 数据架构升级：
1. kpi_snapshot_hourly 增加 ideal_settling_time + algorithm_version + 5个数据血缘字段
2. 新增 kpi_snapshot_custom（自定义任务快照）
3. 新增 clpm_metric_data_requirement（指标数据需求契约）
4. 新增 diagnosis_tag（诊断标签表）
5. 新增 unit_kpi_summary（装置级汇总表）
6. 预置 12 条指标数据需求契约种子数据

Revision ID: k2f3a4b5c6d7
Revises: j1e2f3a4b5c6
Create Date: 2026-06-26 10:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "k2f3a4b5c6d7"
down_revision = "j1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================================
    # 1. kpi_snapshot_hourly 扩展字段
    # ========================================================

    # 1a. 缺失的指标字段
    op.add_column(
        "kpi_snapshot_hourly", sa.Column("ideal_settling_time", sa.DECIMAL(8, 2), nullable=True)
    )
    op.add_column(
        "kpi_snapshot_hourly", sa.Column("algorithm_version", sa.VARCHAR(50), nullable=True)
    )

    # 1b. v4.0 数据血缘字段
    op.add_column("kpi_snapshot_hourly", sa.Column("sampling_freq", sa.VARCHAR(10), nullable=True))
    op.add_column("kpi_snapshot_hourly", sa.Column("quality_policy", sa.VARCHAR(30), nullable=True))
    op.add_column("kpi_snapshot_hourly", sa.Column("valid_rate", sa.DECIMAL(5, 4), nullable=True))
    op.add_column("kpi_snapshot_hourly", sa.Column("confidence_level", sa.CHAR(1), nullable=True))
    op.add_column("kpi_snapshot_hourly", sa.Column("data_lineage", JSONB, nullable=True))

    op.execute(
        "COMMENT ON COLUMN kpi_snapshot_hourly.ideal_settling_time IS "
        "'理想稳态时间（秒），由控制类型/模型参数/手动配置决定'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_snapshot_hourly.algorithm_version IS "
        "'算法版本号（如 KPI_CALC_v2.0）'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_snapshot_hourly.sampling_freq IS '数据采样频率（如 1s/5s/10s）'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_snapshot_hourly.quality_policy IS "
        "'质量策略（KEEP_ALL_WITH_VALIDITY / KEEP_ALL）'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_snapshot_hourly.valid_rate IS '有效数据率（0~1），用于可信度判定'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_snapshot_hourly.confidence_level IS "
        "'指标可信度等级（A/B/C/D/E，E=INCONCLUSIVE）'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_snapshot_hourly.data_lineage IS "
        "'数据血缘JSON：tag_group/data_block_ids/aggregation_policy/data_policy_version 等'"
    )

    # 1c. 增加索引（按时间窗口+回路查询是高频操作）
    op.create_index(
        "ix_kpi_snapshot_hourly_loop_ts", "kpi_snapshot_hourly", ["loop_id", "ts_start"]
    )

    # ========================================================
    # 2. kpi_snapshot_custom（自定义任务快照）
    # ========================================================
    op.create_table(
        "kpi_snapshot_custom",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("task_id", UUID, nullable=False),
        sa.Column("loop_id", UUID, nullable=False),
        sa.Column("ts_start", sa.TIMESTAMP, nullable=False),
        sa.Column("ts_end", sa.TIMESTAMP, nullable=False),
        sa.Column("score", sa.DECIMAL(5, 2)),
        sa.Column("accuracy_rate", sa.DECIMAL(5, 2)),
        sa.Column("fast_response_rate", sa.DECIMAL(5, 2)),
        sa.Column("steady_rate", sa.DECIMAL(5, 2)),
        sa.Column("effective_auto_rate", sa.DECIMAL(5, 2)),
        sa.Column("good_value_rate", sa.DECIMAL(5, 2)),
        sa.Column("oscillation_rate", sa.DECIMAL(5, 2)),
        sa.Column("saturation_rate", sa.DECIMAL(5, 2)),
        sa.Column("stiction_coeff", sa.DECIMAL(5, 2)),
        sa.Column("output_travel_index", sa.DECIMAL(8, 2)),
        sa.Column("steady_state_time", sa.DECIMAL(8, 2)),
        sa.Column("ideal_settling_time", sa.DECIMAL(8, 2)),
        sa.Column("auto_mode_rate", sa.DECIMAL(5, 2)),
        sa.Column("algorithm_version", sa.VARCHAR(50)),
        sa.Column("status", sa.VARCHAR(20), nullable=False),
        sa.Column("confidence_level", sa.CHAR(1)),
        sa.Column("valid_rate", sa.DECIMAL(5, 4)),
        sa.Column("data_lineage", JSONB),
        sa.Column("created_at", sa.TIMESTAMP, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("task_id", "loop_id", name="uq_kpi_custom_task_loop"),
        sa.ForeignKeyConstraint(["loop_id"], ["loop_ledger.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')", name="ck_kpi_custom_status"
        ),
        sa.CheckConstraint("ts_end > ts_start", name="ck_kpi_custom_window"),
    )
    op.execute(
        "COMMENT ON TABLE kpi_snapshot_custom IS '自定义评估任务快照（按需触发，不参与装置级聚合）'"
    )
    op.create_index("ix_kpi_snapshot_custom_task", "kpi_snapshot_custom", ["task_id"])
    op.create_index(
        "ix_kpi_snapshot_custom_loop_ts", "kpi_snapshot_custom", ["loop_id", "ts_start"]
    )

    # ========================================================
    # 3. clpm_metric_data_requirement（指标数据需求契约）
    # ========================================================
    op.create_table(
        "clpm_metric_data_requirement",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("metric_code", sa.VARCHAR(50), nullable=False, unique=True),
        sa.Column("metric_name", sa.VARCHAR(100), nullable=False),
        sa.Column("tag_group", sa.VARCHAR(20), nullable=False),
        sa.Column("tags", JSONB, nullable=False),
        sa.Column("sampling_strategy", sa.VARCHAR(30), nullable=False),
        sa.Column("quality_policy", sa.VARCHAR(30), nullable=False),
        sa.Column("mask_expression", sa.VARCHAR(200)),
        sa.Column("aggregation_policy", sa.VARCHAR(20)),
        sa.Column("depends_on", JSONB),
        sa.Column("version", sa.VARCHAR(20), server_default=sa.text("'v1'")),
        sa.Column("updated_at", sa.TIMESTAMP, server_default=sa.text("NOW()")),
    )
    op.execute(
        "COMMENT ON TABLE clpm_metric_data_requirement IS "
        "'指标数据需求契约：定义每个指标的数据获取和预处理需求'"
    )

    # 3a. 预置 12 条种子数据
    op.execute("""
        INSERT INTO clpm_metric_data_requirement
            (metric_code, metric_name, tag_group, tags, sampling_strategy,
             quality_policy, mask_expression, aggregation_policy, depends_on) VALUES
        ('accuracy_rate', '准确率', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
        ('fast_response_rate', '快速率', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid',
         'LAST', '["settling_time","ideal_settling_time"]'),
        ('steady_rate', '稳定率', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid',
         'LAST', '["oscillation_rate"]'),
        ('effective_auto_rate', '有效自控率', 'MODE_HF', '["mode","op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'mode_valid && op_valid',
         'LAST', '["auto_mode_rate","saturation_rate"]'),
        ('good_value_rate', '好值率', 'QUALITY_HF', '["pv_quality"]',
         'FIXED_1S', 'KEEP_ALL', NULL, NULL, NULL),
        ('oscillation_rate', '振荡率', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
        ('saturation_rate', '饱和率', 'OP_HF', '["op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'op_valid', 'LAST', NULL),
        ('stiction_coeff', '粘滞系数', 'PVOP_HF', '["pv","op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && op_valid', 'LAST', NULL),
        ('output_travel_index', '输出值行程指数', 'OP_HF', '["op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'op_valid && consecutive_valid', 'LAST', NULL),
        ('auto_mode_rate', '自控率', 'MODE_HF', '["mode"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'mode_valid', 'LAST', NULL),
        ('steady_state_time', '稳态时间', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
        ('ideal_settling_time', '理想稳态时间', 'CONFIG', '[]',
         'NONE', 'NONE', NULL, NULL, NULL)
        ON CONFLICT (metric_code) DO NOTHING;
    """)

    # ========================================================
    # 4. diagnosis_tag（诊断标签表）
    # ========================================================
    op.create_table(
        "diagnosis_tag",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("loop_id", UUID, nullable=False),
        sa.Column("tag_code", sa.VARCHAR(50), nullable=False),
        sa.Column("tag_name", sa.VARCHAR(100)),
        sa.Column("severity", sa.VARCHAR(20), nullable=False),
        sa.Column("source_metric", sa.VARCHAR(50)),
        sa.Column("trigger_condition", JSONB),
        sa.Column("trigger_value", sa.DECIMAL(10, 4)),
        sa.Column("triggered_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("NOW()")),
        sa.Column("resolved_at", sa.TIMESTAMP),
        sa.Column("resolved_by", UUID),
        sa.Column("resolution_note", sa.TEXT),
        sa.Column("status", sa.VARCHAR(20), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.ForeignKeyConstraint(["loop_id"], ["loop_ledger.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')", name="ck_diag_tag_severity"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'RESOLVED', 'SUPPRESSED')", name="ck_diag_tag_status"
        ),
    )
    op.execute(
        "COMMENT ON TABLE diagnosis_tag IS "
        "'诊断标签表：用于故障定位和告警（振荡/阀门粘滞/输出饱和/PV质量异常等）'"
    )
    op.create_index("ix_diagnosis_tag_loop_status", "diagnosis_tag", ["loop_id", "status"])
    op.create_index("ix_diagnosis_tag_severity", "diagnosis_tag", ["severity", "triggered_at"])

    # ========================================================
    # 5. unit_kpi_summary（装置级汇总表）
    # ========================================================
    op.create_table(
        "unit_kpi_summary",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("node_id", UUID, nullable=False),
        sa.Column("snapshot_time", sa.TIMESTAMP, nullable=False),
        sa.Column("avg_score", sa.DECIMAL(5, 2)),
        sa.Column("auto_mode_rate", sa.DECIMAL(5, 2)),
        sa.Column("effective_auto_rate", sa.DECIMAL(5, 2)),
        sa.Column("steady_rate", sa.DECIMAL(5, 2)),
        sa.Column("accuracy_rate", sa.DECIMAL(5, 2)),
        sa.Column("fast_response_rate", sa.DECIMAL(5, 2)),
        sa.Column("good_value_rate", sa.DECIMAL(5, 2)),
        sa.Column("oscillation_rate", sa.DECIMAL(5, 2)),
        sa.Column("saturation_rate", sa.DECIMAL(5, 2)),
        sa.Column("total_loops", sa.Integer),
        sa.Column("evaluated_loops", sa.Integer),
        sa.Column("inconclusive_loops", sa.Integer),
        sa.Column("algorithm_version", sa.VARCHAR(50)),
        sa.Column("created_at", sa.TIMESTAMP, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("node_id", "snapshot_time", name="uq_unit_kpi_summary_node_time"),
        sa.ForeignKeyConstraint(["node_id"], ["plant_node.id"], ondelete="CASCADE"),
    )
    op.execute(
        "COMMENT ON TABLE unit_kpi_summary IS "
        "'装置级KPI汇总表：仅基于标准任务（kpi_snapshot_hourly）聚合，自定义任务不参与'"
    )
    op.create_index(
        "ix_unit_kpi_summary_node_time", "unit_kpi_summary", ["node_id", "snapshot_time"]
    )

    # ========================================================
    # 6. kpi_snapshot_hourly 约束：confidence_level 取值校验
    # ========================================================
    op.create_check_constraint(
        "ck_kpi_snapshot_confidence",
        "kpi_snapshot_hourly",
        "confidence_level IS NULL OR confidence_level IN ('A', 'B', 'C', 'D', 'E')",
    )


def downgrade() -> None:
    # 6. 删除约束
    op.drop_constraint("ck_kpi_snapshot_confidence", "kpi_snapshot_hourly", type_="check")

    # 5. 删除 unit_kpi_summary
    op.drop_index("ix_unit_kpi_summary_node_time", table_name="unit_kpi_summary")
    op.drop_table("unit_kpi_summary")

    # 4. 删除 diagnosis_tag（含种子数据）
    op.drop_index("ix_diagnosis_tag_severity", table_name="diagnosis_tag")
    op.drop_index("ix_diagnosis_tag_loop_status", table_name="diagnosis_tag")
    op.drop_table("diagnosis_tag")

    # 3. 删除 clpm_metric_data_requirement（含种子数据）
    op.drop_table("clpm_metric_data_requirement")

    # 2. 删除 kpi_snapshot_custom
    op.drop_index("ix_kpi_snapshot_custom_loop_ts", table_name="kpi_snapshot_custom")
    op.drop_index("ix_kpi_snapshot_custom_task", table_name="kpi_snapshot_custom")
    op.drop_table("kpi_snapshot_custom")

    # 1. 删除 kpi_snapshot_hourly 扩展字段
    op.drop_index("ix_kpi_snapshot_hourly_loop_ts", table_name="kpi_snapshot_hourly")
    op.drop_column("kpi_snapshot_hourly", "data_lineage")
    op.drop_column("kpi_snapshot_hourly", "confidence_level")
    op.drop_column("kpi_snapshot_hourly", "valid_rate")
    op.drop_column("kpi_snapshot_hourly", "quality_policy")
    op.drop_column("kpi_snapshot_hourly", "sampling_freq")
    op.drop_column("kpi_snapshot_hourly", "algorithm_version")
    op.drop_column("kpi_snapshot_hourly", "ideal_settling_time")
