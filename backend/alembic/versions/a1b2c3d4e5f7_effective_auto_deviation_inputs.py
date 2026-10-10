"""effective_auto_rate data requirement: add pv/sp deviation inputs

Revision ID: a1b2c3d4e5f7
Revises: d0724685907c
Create Date: 2026-10-10

CAL-03（系统改造优化-2026-10-10 / P1-01）：有效自控率 PV/SP 缺失语义显式化。

根因（台账 CAL-03）：
    effective_auto_rate 原契约 MODE_HF / ["mode","op"] 不含 PV/SP，
    EffectiveAutoRateCalculator 的偏差检查（|E| < e_max）在 pv/sp 缺失时
    is_deviation_ok=True 自动成立——"控制有效"判定静默偏宽：任何自控且
    未饱和的时段（无论实际偏差多大）都计入有效自控时长。

修正（与 app/services/metric_calculator/effective_auto.py 同批）：
    tags: ["mode","op"] → ["mode","op","pv","sp"]

    MODE_HF 在 KPI 管线中由 BASE 派生复用（data_planner._derive_from_base），
    BASE 本就携带 pv/sp（BASE 契约需要），扩展 tags 不增加回源查询，仅
    派生时多带两列。mask_expression 保持 mode_valid && op_valid 不变：
    pv/sp 值缺失/非法的采样点留在分母、不计入分子（缺输入不当有效），
    由计算器逐点显式判定（deviation_unknown_points 计数入 details）；
    不把 pv_valid/sp_valid 并入 mask，避免坏点静默缩小分母抬高 R。

    计算器侧同步：bundle 缺 pv/sp → INCONCLUSIVE(deviation_inputs_missing)，
    不再静默当有效（见 effective_auto.py CAL-03 注释）。

设计依据：GB/T 44693.2-2024 附录 B.2；算法说明 §4.2
关联种子：db/postgresql/02_seed_data.sql（已同步修正）
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f7"
down_revision = "d0724685907c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """effective_auto_rate 契约 tags 扩为 ["mode","op","pv","sp"]（mask 不变）."""
    op.execute(
        """
        UPDATE clpm_metric_data_requirement
        SET tags = '["mode","op","pv","sp"]'::jsonb
        WHERE metric_code = 'effective_auto_rate'
        """
    )


def downgrade() -> None:
    """还原契约 tags（偏差检查将因缺输入被计算器判 INCONCLUSIVE）."""
    op.execute(
        """
        UPDATE clpm_metric_data_requirement
        SET tags = '["mode","op"]'::jsonb
        WHERE metric_code = 'effective_auto_rate'
        """
    )
