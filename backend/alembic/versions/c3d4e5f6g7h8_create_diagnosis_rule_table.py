"""create diagnosis_rule table + seed R01-R06 (C2)

整改计划 C2：专家规则引擎化。

将硬编码的 R01-R06 专家规则迁入 diagnosis_rule 表，运行时用 simpleeval
安全沙箱求值条件表达式，兑现 FDS §5.4.6 的规则可配承诺。

种子规则（按 priority 升序执行）：
- R01: OSCILLATION + VALVE_STICTION(stiction>0.5) → 移除 OSCILLATION
- R02: OSCILLATION + OVERAGGRESSIVE(无 VALVE_STICTION) → 移除 OSCILLATION
- R03: OVERAGGRESSIVE + OVERCONSERVATIVE → 保留置信度更高的
- R04: QUALITY_ABNORMAL(bad_rate>0.5) → 仅保留 QUALITY_ABNORMAL
- R05: 所有置信度<0.5 → 添加 MANUAL_REVIEW
- R06: 按优先级排序标签

Revision ID: c3d4e5f6g7h8
Revises: b2d3e4f5g6h7
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6g7h8"
down_revision: str | None = "b2d3e4f5g6h7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnosis_rule",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column("rule_code", sa.String(20), nullable=False),
        sa.Column("rule_name", sa.String(100), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("condition_expr", sa.Text, nullable=False),
        sa.Column("action_type", sa.String(20), nullable=False),
        sa.Column(
            "action_params",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(50), nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.CheckConstraint(
            "action_type IN ('REMOVE_LABEL', 'ADD_LABEL', 'KEEP_HIGHEST', "
            "'FILTER_ONLY', 'SORT_PRIORITY')",
            name="ck_diag_rule_action_type",
        ),
    )
    op.create_index("uk_diagnosis_rule_code", "diagnosis_rule", ["rule_code"], unique=True)
    op.create_index("idx_diagnosis_rule_priority", "diagnosis_rule", ["priority"])

    # 种子规则 R01-R06
    import json
    from uuid import uuid4

    rules = [
        {
            "rule_code": "R01",
            "rule_name": "粘滞根因优先于振荡",
            "priority": 10,
            "condition_expr": (
                'has("OSCILLATION") and has("VALVE_STICTION") '
                'and confidence("VALVE_STICTION") > 0.5'
            ),
            "action_type": "REMOVE_LABEL",
            "action_params": json.dumps({"label": "OSCILLATION"}),
        },
        {
            "rule_code": "R02",
            "rule_name": "过激整定根因优先于振荡",
            "priority": 20,
            "condition_expr": (
                'has("OSCILLATION") and has("OVERAGGRESSIVE") and not has("VALVE_STICTION")'
            ),
            "action_type": "REMOVE_LABEL",
            "action_params": json.dumps({"label": "OSCILLATION"}),
        },
        {
            "rule_code": "R03",
            "rule_name": "过激与过保守互斥保留高置信度",
            "priority": 30,
            "condition_expr": 'has("OVERAGGRESSIVE") and has("OVERCONSERVATIVE")',
            "action_type": "KEEP_HIGHEST",
            "action_params": json.dumps({"labels": ["OVERAGGRESSIVE", "OVERCONSERVATIVE"]}),
        },
        {
            "rule_code": "R04",
            "rule_name": "质量异常严重时仅保留质量标签",
            "priority": 5,
            "condition_expr": 'has("QUALITY_ABNORMAL") and feature("bad_quality_rate") > 0.5',
            "action_type": "FILTER_ONLY",
            "action_params": json.dumps({"keep": "QUALITY_ABNORMAL"}),
        },
        {
            "rule_code": "R05",
            "rule_name": "所有算法低置信度时添加人工复核",
            "priority": 40,
            "condition_expr": "count() > 0 and max_confidence() < 0.5",
            "action_type": "ADD_LABEL",
            "action_params": json.dumps({"label": "MANUAL_REVIEW", "confidence": 0.5}),
        },
        {
            "rule_code": "R06",
            "rule_name": "按标签优先级排序",
            "priority": 90,
            "condition_expr": "True",
            "action_type": "SORT_PRIORITY",
            "action_params": json.dumps(
                {
                    "priority_map": {
                        "QUALITY_ABNORMAL": 1,
                        "VALVE_STICTION": 2,
                        "OVERAGGRESSIVE": 3,
                        "OVERCONSERVATIVE": 4,
                        "OUTPUT_SATURATION": 5,
                        "OSCILLATION": 6,
                        "EXTERNAL_DISTURBANCE": 7,
                        "MANUAL_REVIEW": 99,
                    }
                }
            ),
        },
    ]

    for r in rules:
        op.execute(
            sa.text(
                "INSERT INTO diagnosis_rule "
                "(id, rule_code, rule_name, priority, condition_expr, "
                "action_type, action_params, is_enabled, version) "
                "VALUES (CAST(:id AS UUID), :rule_code, :rule_name, :priority, "
                ":condition_expr, :action_type, CAST(:action_params AS JSONB), "
                "true, 1)"
            ).bindparams(
                id=str(uuid4()),
                rule_code=r["rule_code"],
                rule_name=r["rule_name"],
                priority=r["priority"],
                condition_expr=r["condition_expr"],
                action_type=r["action_type"],
                action_params=r["action_params"],
            )
        )


def downgrade() -> None:
    op.drop_index("idx_diagnosis_rule_priority", table_name="diagnosis_rule")
    op.drop_index("uk_diagnosis_rule_code", table_name="diagnosis_rule")
    op.drop_table("diagnosis_rule")
