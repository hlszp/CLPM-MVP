"""expand ck_alert_rule_type with METRIC_THRESHOLD

预警规则引擎重构（2026-08-20）主推 METRIC_THRESHOLD 规则类型，但建表时的
check 约束仅含 THRESHOLD/DRIFT/COMPOSITE/CONFIDENCE，导致指标阈值规则与
预制规则（PRESET_*）无法入库。本迁移扩展约束以包含 METRIC_THRESHOLD。

Revision ID: z2b3c4d5e6f7
Revises: 58f565b5a57f
Create Date: 2026-08-24

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "z2b3c4d5e6f7"
down_revision = "58f565b5a57f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_alert_rule_type", "alert_rule", type_="check")
    op.create_check_constraint(
        "ck_alert_rule_type",
        "alert_rule",
        "rule_type IN ('THRESHOLD', 'DRIFT', 'COMPOSITE', 'CONFIDENCE', 'METRIC_THRESHOLD')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_alert_rule_type", "alert_rule", type_="check")
    op.create_check_constraint(
        "ck_alert_rule_type",
        "alert_rule",
        "rule_type IN ('THRESHOLD', 'DRIFT', 'COMPOSITE', 'CONFIDENCE')",
    )
