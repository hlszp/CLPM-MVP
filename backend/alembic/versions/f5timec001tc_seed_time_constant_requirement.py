"""seed time_constant metric data requirement (F5)

整改 F5：为时间常数计算器（L1 DISPLAY_ONLY）插入指标数据需求契约。

- tag_group=PVOP_HF / tags=["pv","op"] / mask=pv_valid && op_valid
  （与 stiction_index/valve_linearity 同组，OP→PV 相关分析需对齐序列）
- sampling_strategy=FIXED_1S（高频组，质心滞后估计的时间分辨率）
- aggregation_policy=LAST（DISPLAY_ONLY，不参与节点聚合）
- 老快照 time_constant 保持 NULL，仅新计算窗口起写入

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §4 时间常数（L1）；
         app/services/metric_calculator/time_constant.py

Revision ID: f5timec001tc
Revises: a1e2f3g4h5i6
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5timec001tc"
down_revision: str | Sequence[str] | None = "a1e2f3g4h5i6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """插入 time_constant 指标数据需求契约。"""
    op.execute(
        """
        INSERT INTO clpm_metric_data_requirement
            (metric_code, metric_name, tag_group, tags, sampling_strategy,
             quality_policy, mask_expression, aggregation_policy, depends_on) VALUES
        ('time_constant', '时间常数', 'PVOP_HF', '["pv","op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && op_valid', 'LAST', NULL)
        ON CONFLICT (metric_code) DO NOTHING;
        """
    )


def downgrade() -> None:
    """删除 time_constant 指标数据需求契约。"""
    op.execute(
        """
        DELETE FROM clpm_metric_data_requirement WHERE metric_code = 'time_constant';
        """
    )
