"""merge heads x4c5d6e7f8a9 and v6p1diag002

合并两个分支 head：
- x4c5d6e7f8a9: fix metric_data_requirement seeds to align with CALCULATOR_REGISTRY
- v6p1diag002: align diagnosis_config threshold keys with diagnosis engine

合并后形成单一 head，便于后续迁移基于此点继续延伸。
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "v6p1merge002"
down_revision = ("x4c5d6e7f8a9", "v6p1diag002")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 合并迁移，无实际 DDL 操作
    pass


def downgrade() -> None:
    # 合并迁移，无实际 DDL 操作
    pass
