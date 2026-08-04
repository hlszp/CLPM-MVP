"""fix saturation_rate data requirement: add mode signal

修正 clpm_metric_data_requirement 中 saturation_rate 的数据需求契约，
使其包含 MODE 信号，解决生产环境饱和率全回路 INCONCLUSIVE（空白）问题。

根因（2026-08-04 排查）：
    saturation_rate 原契约 tag_group=OP_HF / tags=["op"] / mask=op_valid，
    不含 MODE 信号。DataPlanner._derive_from_base 仅按 tag_roles 提取信号，
    故派生的 OP_HF DataBlock 无 mode 列。SaturationRateCalculator.calculate
    读取 masked_mode 得到空列表 → bound=min(n,0,len(durations))=0 →
    total_duration=0 → INCONCLUSIVE。

    对比 effective_auto_rate（MODE_HF / ["mode","op"] / mode_valid && op_valid）
    与 auto_mode_rate（MODE_HF / ["mode"] / mode_valid）均含 MODE，可正常计算；
    唯独 saturation_rate 缺 MODE，导致 KPI 计算任务对该指标恒返回 INCONCLUSIVE，
    前端饱和率显示空白。

修正：
    saturation_rate 契约对齐 effective_auto_rate：
        tag_group:        OP_HF  → MODE_HF
        tags:             ["op"] → ["mode","op"]
        mask_expression:  op_valid → mode_valid && op_valid

    saturation 与 effective_auto 共用 MODE_HF bundle（含 mode+op），mask 一致，
    SaturationRateCalculator 可正常读取 mode 判定自控模式。

设计依据：GB/T 44693.2-2024 附录 F.3；算法说明 §4.7 / §3.6.2
关联代码：app/services/metric_calculator/saturation.py
关联种子：db/postgresql/02_seed_data.sql（已同步修正）

Revision ID: y5d6e7f8a9b0
Revises: p3e5f6g7h8i9
Create Date: 2026-08-04
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "y5d6e7f8a9b0"
down_revision = "p3e5f6g7h8i9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """saturation_rate 契约改为 MODE_HF / ["mode","op"] / mode_valid && op_valid."""
    op.execute(
        """
        UPDATE clpm_metric_data_requirement
        SET tag_group = 'MODE_HF',
            tags = '["mode","op"]'::jsonb,
            mask_expression = 'mode_valid && op_valid'
        WHERE metric_code = 'saturation_rate'
        """
    )


def downgrade() -> None:
    """还原 saturation_rate 契约为 OP_HF / ["op"] / op_valid（会重新导致空白）."""
    op.execute(
        """
        UPDATE clpm_metric_data_requirement
        SET tag_group = 'OP_HF',
            tags = '["op"]'::jsonb,
            mask_expression = 'op_valid'
        WHERE metric_code = 'saturation_rate'
        """
    )
