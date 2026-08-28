"""sync valve_nonlinearity depends_on contract

clpm_metric_data_requirement.depends_on 为纯文档性契约字段（无代码消费，
编排层依赖关系在 kpi_calc._LAYER2_DEPENDENCIES）。2026-08-27 指标计算去重：
valve_nonlinearity 改为经依赖注入复用 valve_linearity 的皮尔逊 r
（nonlinearity = 1 - |r|，同一份 PV-OP 数据只算一次），契约表同步登记
该依赖，保持文档与实现一致。

Revision ID: f5a6b7c8d9e0
Revises: e3f4a5b6c7d8
Create Date: 2026-08-28
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f5a6b7c8d9e0"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """valve_nonlinearity.depends_on → ["valve_linearity"]。"""
    op.execute(
        """
        UPDATE clpm_metric_data_requirement
        SET depends_on = '["valve_linearity"]'::jsonb
        WHERE metric_code = 'valve_nonlinearity';
        """
    )


def downgrade() -> None:
    """恢复为无依赖。"""
    op.execute(
        """
        UPDATE clpm_metric_data_requirement
        SET depends_on = NULL
        WHERE metric_code = 'valve_nonlinearity';
        """
    )
