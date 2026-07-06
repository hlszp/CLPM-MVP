"""merge heads o8r9s0t1u2v3 and r2b3c4d5e6f7

合并两个分支 head：
- o8r9s0t1u2v3: add AREA node type to plant_node
- r2b3c4d5e6f7: align metric_code in clpm_metric_data_requirement

合并后形成单一 head，便于后续迁移基于此点继续延伸。
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "v6p1merge001"
down_revision = ("o8r9s0t1u2v3", "r2b3c4d5e6f7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 合并迁移，无实际 DDL 操作
    pass


def downgrade() -> None:
    # 合并迁移，无实际 DDL 操作
    pass
