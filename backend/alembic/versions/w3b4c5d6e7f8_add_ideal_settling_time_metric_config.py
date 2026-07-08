"""add IDEAL_SETTLING_TIME to metric_config (补齐第 12 个评估指标)

补齐 metric_config 表中缺失的 IDEAL_SETTLING_TIME（理想稳态时间）配置。
该指标是 12 项 KPI 评估指标中的第 12 项（辅助诊断类），
为快速率（fast_rate）计算提供理想稳态时间基准。

当前数据库已有 11 个指标配置（accuracy_rate/fast_rate/steady_rate/
effective_auto_rate/good_value_rate/auto_mode_rate/oscillation_rate/
saturation_rate/stiction_index/settling_time/output_trip_index），
缺少 ideal_settling_time，导致指标定义页面只显示 11 项。

设计依据：DDS v4.1 §2.6 / 算法说明 §4.5 / GB/T 44693.2-2024 附录 B.4
关联代码：app/tasks/kpi_calc.py:_DB_TO_CALCULATOR_METRIC_CODE
          app/services/metric_calculator/ideal_settling_time.py

Revision ID: w3b4c5d6e7f8
Revises: v6p1diag001
Create Date: 2026-07-08
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "w3b4c5d6e7f8"
down_revision = "v6p1diag001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """补齐 IDEAL_SETTLING_TIME 指标配置。"""
    op.execute(
        """
        INSERT INTO metric_config
            (id, metric_code, metric_name, formula, weight,
             threshold, control_type, is_enabled, updated_by, updated_at, version)
        VALUES (
            uuid_generate_v4(),
            'IDEAL_SETTLING_TIME',
            '理想稳态时间',
            'alpha * (tau + theta) 或按控制类型默认值',
            0.00,
            '{"max": 600, "min": 0, "alert": "warning"}'::jsonb,
            'FAST',
            TRUE,
            'admin',
            NOW(),
            1
        )
        ON CONFLICT (metric_code) DO UPDATE
        SET metric_name = EXCLUDED.metric_name,
            formula = EXCLUDED.formula,
            weight = EXCLUDED.weight,
            threshold = EXCLUDED.threshold,
            control_type = EXCLUDED.control_type,
            is_enabled = TRUE,
            updated_at = NOW()
        """
    )


def downgrade() -> None:
    """回滚：删除 IDEAL_SETTLING_TIME 配置。"""
    op.execute(
        "DELETE FROM metric_config WHERE metric_code = 'IDEAL_SETTLING_TIME'"
    )
