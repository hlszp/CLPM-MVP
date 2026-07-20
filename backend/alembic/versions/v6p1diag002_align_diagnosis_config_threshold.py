"""align diagnosis_config.threshold with algorithm-read keys (诊断阈值键名对齐)

诊断配置种子数据的 threshold 字段原为 {"min", "max", "alert"} 结构，
与诊断引擎 _get_threshold 实际读取的算法键名完全不匹配，
导致配置页修改阈值对算法判定完全不生效。

本迁移将存量库 threshold 改写为代码真实读取的键名（默认值对齐代码现有默认值）：
- OSCILLATION:       similarity_threshold / min_zero_crossings
                     （_detect_oscillation_iae）
- QUALITY_ABNORMAL:  q001_consecutive_bad / q002_bad_rate / q003_uncertain_rate /
                     q004_bad_duration / q005_min_bad / q005_max_bad
                     （_analyze_quality）
- OUTPUT_SATURATION: op_high_limit / op_low_limit / saturation_epsilon
                     （_analyze_saturation）
- 其余标签算法暂未从配置读取阈值（代码内默认值），threshold 置 NULL，
  避免配置页展示无生效语义的假阈值

设计依据：FDS §5.4.1 / 诊断模块整改计划 Phase A - A5
关联代码：app/tasks/diagnosis_engine.py:_get_threshold

Revision ID: v6p1diag002
Revises: v6p1dcs001
Create Date: 2026-07-20
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "v6p1diag002"
down_revision = "v6p1dcs001"
branch_labels = None
depends_on = None

# 新阈值：键名对齐 _get_threshold 实际读取键，值对齐代码现有默认值
NEW_THRESHOLDS = {
    "OSCILLATION": '{"similarity_threshold": 0.4, "min_zero_crossings": 3}',
    "QUALITY_ABNORMAL": (
        '{"q001_consecutive_bad": 10, "q002_bad_rate": 0.1, "q003_uncertain_rate": 0.2, '
        '"q004_bad_duration": 5, "q005_min_bad": 3, "q005_max_bad": 10}'
    ),
    "OUTPUT_SATURATION": '{"op_high_limit": 100.0, "op_low_limit": 0.0, "saturation_epsilon": 2.0}',
}

# 算法暂未从配置读取阈值的标签：upgrade 置 NULL
NULL_THRESHOLD_CODES = (
    "VALVE_STICTION",
    "OVERAGGRESSIVE",
    "OVERCONSERVATIVE",
    "EXTERNAL_DISTURBANCE",
)

# 旧阈值：种子数据原有 {min, max, alert} 结构（用于 downgrade 恢复）
OLD_THRESHOLDS = {
    "OSCILLATION": '{"min": 0.4, "max": 1.0, "alert": "warning"}',
    "VALVE_STICTION": '{"min": 0.5, "max": 100, "alert": "critical"}',
    "OVERAGGRESSIVE": '{"min": 25, "max": 100, "alert": "warning"}',
    "OVERCONSERVATIVE": '{"min": 5.0, "max": 100, "alert": "warning"}',
    "EXTERNAL_DISTURBANCE": '{"min": 5, "max": 100, "alert": "warning"}',
    "QUALITY_ABNORMAL": '{"min": 20, "max": 100, "alert": "critical"}',
    "OUTPUT_SATURATION": '{"min": 5, "max": 100, "alert": "warning"}',
}


def upgrade() -> None:
    """将 threshold 改写为算法真实读取的键名。"""
    for diag_code, threshold in NEW_THRESHOLDS.items():
        op.execute(
            f"UPDATE diagnosis_config SET threshold = '{threshold}'::jsonb "
            f"WHERE diag_code = '{diag_code}'"
        )
    for diag_code in NULL_THRESHOLD_CODES:
        op.execute(f"UPDATE diagnosis_config SET threshold = NULL WHERE diag_code = '{diag_code}'")


def downgrade() -> None:
    """恢复种子数据原有 {min, max, alert} 结构。"""
    for diag_code, threshold in OLD_THRESHOLDS.items():
        op.execute(
            f"UPDATE diagnosis_config SET threshold = '{threshold}'::jsonb "
            f"WHERE diag_code = '{diag_code}'"
        )
