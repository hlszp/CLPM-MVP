"""seed diagnosis_config threshold keys for OVERAGGRESSIVE and QUALITY_ABNORMAL (C1)

整改计划 C1：阈值全面入库。

v6p1diag002 已对齐 OSCILLATION / QUALITY_ABNORMAL（Q001-Q005）/
OUTPUT_SATURATION 三组阈值键名，但遗漏了两处：

1. OVERAGGRESSIVE — v6p1diag002 将 threshold 置 NULL，
   但 _assess_model_mismatch 实际读取 harris_ar_order / harris_warn
   两个键（Harris 指数模型失配评估）。

2. QUALITY_ABNORMAL — v6p1diag002 仅登记了 Q001-Q005 质量码规则键，
   未登记 _detect_sensor_faults 读取的 7 个传感器故障检测键
   （frozen_window / frozen_eps / frozen_ratio / noise_ratio /
   noise_segment / drift_k / drift_segments）。

本迁移补齐以上键名，默认值对齐代码 _THRESHOLD_SCHEMA。

设计依据：整改计划 C1 / FDS §5.4
关联代码：app.tasks.diagnosis_engine._THRESHOLD_SCHEMA

Revision ID: a1c1d2e3f4g5
Revises: z1a2b3c4d5e6
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c1d2e3f4g5"
down_revision: str | None = "z1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# OVERAGGRESSIVE: Harris 指数模型失配评估阈值（原为 NULL）
OVERAGGRESSIVE_THRESHOLD = '{"harris_ar_order": 10, "harris_warn": 2.0}'

# QUALITY_ABNORMAL: 在 Q001-Q005 基础上追加传感器故障检测 7 键
QUALITY_ABNORMAL_THRESHOLD = (
    '{"q001_consecutive_bad": 10, "q002_bad_rate": 0.1, "q003_uncertain_rate": 0.2, '
    '"q004_bad_duration": 5, "q005_min_bad": 3, "q005_max_bad": 10, '
    '"frozen_window": 300, "frozen_eps": 0.0001, "frozen_ratio": 0.2, '
    '"noise_ratio": 3.0, "noise_segment": 0.5, "drift_k": 2.0, "drift_segments": 5}'
)

# downgrade: 恢复为 v6p1diag002 设置的值
OVERAGGRESSIVE_DOWNGRADE = "NULL"
QUALITY_ABNORMAL_DOWNGRADE = (
    '{"q001_consecutive_bad": 10, "q002_bad_rate": 0.1, "q003_uncertain_rate": 0.2, '
    '"q004_bad_duration": 5, "q005_min_bad": 3, "q005_max_bad": 10}'
)


def upgrade() -> None:
    """补齐 OVERAGGRESSIVE 和 QUALITY_ABNORMAL 的缺失阈值键。"""
    op.execute(
        "UPDATE diagnosis_config SET threshold = "
        f"'{OVERAGGRESSIVE_THRESHOLD}'::jsonb "
        "WHERE diag_code = 'OVERAGGRESSIVE'"
    )
    op.execute(
        "UPDATE diagnosis_config SET threshold = "
        f"'{QUALITY_ABNORMAL_THRESHOLD}'::jsonb "
        "WHERE diag_code = 'QUALITY_ABNORMAL'"
    )


def downgrade() -> None:
    """恢复为 v6p1diag002 设置的值。"""
    op.execute("UPDATE diagnosis_config SET threshold = NULL WHERE diag_code = 'OVERAGGRESSIVE'")
    op.execute(
        "UPDATE diagnosis_config SET threshold = "
        f"'{QUALITY_ABNORMAL_DOWNGRADE}'::jsonb "
        "WHERE diag_code = 'QUALITY_ABNORMAL'"
    )
