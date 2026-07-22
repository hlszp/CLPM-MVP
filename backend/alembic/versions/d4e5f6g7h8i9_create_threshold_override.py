"""create diagnosis_threshold_override table + seed templates (C3)

整改计划 C3：差异化阈值。

支持"全局默认 → 回路类型模板 → 装置级 → 回路级"四级阈值覆盖。
全局默认在 diagnosis_config.threshold；本表存储各级覆盖。

种子 4 套控制类型模板（loop_type scope）：
- FLOW: 流量回路——快响应，振荡检测更灵敏，冻结窗口更短
- TEMPERATURE: 温度回路——慢响应，振荡检测更宽松，冻结窗口更长
- LEVEL: 液位回路——积分特性，饱和限值更宽
- PRESSURE: 压力回路——快响应，类似流量但饱和限值不同

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-07-23
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6g7h8i9"
down_revision: str | None = "c3d4e5f6g7h8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnosis_threshold_override",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column("diag_code", sa.String(50), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_id", sa.String(100), nullable=False),
        sa.Column(
            "threshold",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(50), nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.CheckConstraint(
            "scope_type IN ('loop_type', 'plant', 'loop')",
            name="ck_diag_threshold_override_scope",
        ),
    )
    op.create_index(
        "uk_diag_threshold_override",
        "diagnosis_threshold_override",
        ["diag_code", "scope_type", "scope_id"],
        unique=True,
    )
    op.create_index(
        "idx_diag_threshold_override_scope",
        "diagnosis_threshold_override",
        ["scope_type", "scope_id"],
    )

    # 种子 4 套控制类型模板（按回路类型 loop_type 预置差异化阈值）
    from uuid import uuid4

    templates = [
        # FLOW 流量回路：快响应，振荡检测更灵敏，冻结窗口更短
        {
            "diag_code": "OSCILLATION",
            "scope_id": "FLOW",
            "threshold": {"similarity_threshold": 0.35, "min_zero_crossings": 4},
        },
        {
            "diag_code": "QUALITY_ABNORMAL",
            "scope_id": "FLOW",
            "threshold": {
                "frozen_window": 180,
                "frozen_eps": 1e-4,
                "frozen_ratio": 0.15,
                "noise_ratio": 3.0,
            },
        },
        # TEMPERATURE 温度回路：慢响应，振荡检测更宽松，冻结窗口更长
        {
            "diag_code": "OSCILLATION",
            "scope_id": "TEMPERATURE",
            "threshold": {"similarity_threshold": 0.5, "min_zero_crossings": 2},
        },
        {
            "diag_code": "QUALITY_ABNORMAL",
            "scope_id": "TEMPERATURE",
            "threshold": {
                "frozen_window": 600,
                "frozen_eps": 5e-4,
                "frozen_ratio": 0.25,
                "noise_ratio": 4.0,
            },
        },
        # LEVEL 液位回路：积分特性，饱和限值更宽
        {
            "diag_code": "OUTPUT_SATURATION",
            "scope_id": "LEVEL",
            "threshold": {
                "op_high_limit": 100.0,
                "op_low_limit": 0.0,
                "saturation_epsilon": 5.0,
            },
        },
        # PRESSURE 压力回路：快响应，振荡检测灵敏度类似流量
        {
            "diag_code": "OSCILLATION",
            "scope_id": "PRESSURE",
            "threshold": {"similarity_threshold": 0.35, "min_zero_crossings": 3},
        },
        {
            "diag_code": "OUTPUT_SATURATION",
            "scope_id": "PRESSURE",
            "threshold": {
                "op_high_limit": 100.0,
                "op_low_limit": 0.0,
                "saturation_epsilon": 1.0,
            },
        },
    ]

    for t in templates:
        op.execute(
            sa.text(
                "INSERT INTO diagnosis_threshold_override "
                "(id, diag_code, scope_type, scope_id, threshold, version) "
                "VALUES (:id, :diag_code, 'loop_type', :scope_id, :threshold, 1)"
            ).bindparams(
                id=str(uuid4()),
                diag_code=t["diag_code"],
                scope_id=t["scope_id"],
                threshold=json.dumps(t["threshold"]),
            )
        )


def downgrade() -> None:
    op.drop_index("idx_diag_threshold_override_scope", table_name="diagnosis_threshold_override")
    op.drop_index("uk_diag_threshold_override", table_name="diagnosis_threshold_override")
    op.drop_table("diagnosis_threshold_override")
