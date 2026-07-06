"""align metric_code in clpm_metric_data_requirement with code expectations

修复 clpm_metric_data_requirement 表中 metric_code 与代码层不一致的问题。

根因：早期 seed 数据使用了旧名称（stiction_coeff / steady_state_time /
output_travel_index / fast_response_rate），但 kpi_calc.py 的
_DB_TO_CALCULATOR_METRIC_CODE 字典使用 DDS v4.1 定义的数据库列名
（stiction_index / settling_time / output_trip_index / fast_rate）。

DataPlanner 用代码层名称查询 requirements 表，导致 SQL 返回 0 行，
4 个指标没有 MetricDataBundle，计算器被跳过 → 历史快照中
stiction_index / settling_time / output_trip_index / fast_rate 字段为 NULL。

修复内容：
1. clpm_metric_data_requirement.metric_code 对齐到 DDS v4.1 数据库列名
2. metric_config 表补齐 4 个缺失指标配置（STICTION_INDEX / SETTLING_TIME /
   OUTPUT_TRIP_INDEX / FAST_RATE），确保 _is_metric_enabled() 正常工作
3. 旧 metric_config 表中 FAST_RESPONSE_RATE 重命名为 FAST_RATE

设计依据：DDS v4.1 §2.6 / 实现契约 v1.0 §3.2.1
关联代码：app/tasks/kpi_calc.py:_DB_TO_CALCULATOR_METRIC_CODE
          app/services/data_planner.py:DataPlanner._load_requirements

Revision ID: r2b3c4d5e6f7
Revises: q1a2b3c4d5e6
Create Date: 2026-07-06
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "r2b3c4d5e6f7"
down_revision = "q1a2b3c4d5e6"
branch_labels = None
depends_on = None


# ============ clpm_metric_data_requirement.metric_code 映射 ============
# (old_code, new_code, new_name)
REQUIREMENT_RENAMES = [
    ("stiction_coeff", "stiction_index", "粘滞指数"),
    ("steady_state_time", "settling_time", "稳态时间"),
    ("output_travel_index", "output_trip_index", "输出行程指数"),
    ("fast_response_rate", "fast_rate", "快速率"),
]


# ============ metric_config 补齐 ============
# 4 个之前缺失的指标配置，metric_code 用 DDS v4.1 列名（大写）
# 字段：metric_code, metric_name, formula, weight, threshold, control_type
METRIC_CONFIG_NEW = [
    {
        "metric_code": "STICTION_INDEX",
        "metric_name": "粘滞指数",
        "formula": "cross_correlation_based_stiction_detection",
        "weight": 0.00,
        "threshold": '{"max": 0.5, "min": 0, "alert": "warning"}',
        "control_type": "STABLE",
    },
    {
        "metric_code": "SETTLING_TIME",
        "metric_name": "稳态时间",
        "formula": "arma_green_function_settling_time",
        "weight": 0.00,
        "threshold": '{"max": 60, "min": 0, "alert": "warning"}',
        "control_type": "FAST",
    },
    {
        "metric_code": "OUTPUT_TRIP_INDEX",
        "metric_name": "输出行程指数",
        "formula": "std(op_diff) / range",
        "weight": 0.00,
        "threshold": '{"max": 0.5, "min": 0, "alert": "warning"}',
        "control_type": "STABLE",
    },
    {
        "metric_code": "FAST_RATE",
        "metric_name": "快速率",
        "formula": "ideal_settling_time / actual_settling_time * 100",
        "weight": 20.00,
        "threshold": '{"max": 100, "min": 80, "alert": "warning"}',
        "control_type": "FAST",
    },
]

# metric_config 旧名 → 新名（注意：旧 seed 中可能已有 FAST_RESPONSE_RATE）
METRIC_CONFIG_RENAMES = [
    ("FAST_RESPONSE_RATE", "FAST_RATE", "快速率"),
]


def upgrade() -> None:
    """对齐 metric_code 到 DDS v4.1 列名 + 补齐 metric_config 配置。"""

    # ============ Part 1: clpm_metric_data_requirement ============
    for old_code, new_code, new_name in REQUIREMENT_RENAMES:
        op.execute(
            f"""
            UPDATE clpm_metric_data_requirement
            SET metric_code = '{new_code}',
                metric_name = '{new_name}'
            WHERE metric_code = '{old_code}'
            """
        )

    # ============ Part 2: metric_config 重命名 ============
    for old_code, new_code, new_name in METRIC_CONFIG_RENAMES:
        op.execute(
            f"""
            UPDATE metric_config
            SET metric_code = '{new_code}',
                metric_name = '{new_name}'
            WHERE metric_code = '{old_code}'
            """
        )

    # ============ Part 3: metric_config 补齐 4 个缺失指标 ============
    # 使用固定 UUID 保证幂等（多次执行不冲突）
    for cfg in METRIC_CONFIG_NEW:
        # threshold 使用 jsonb 字面量
        op.execute(
            f"""
            INSERT INTO metric_config
                (id, metric_code, metric_name, formula, weight,
                 threshold, control_type, is_enabled, version)
            VALUES (
                uuid_generate_v4(),
                '{cfg["metric_code"]}',
                '{cfg["metric_name"]}',
                '{cfg["formula"]}',
                {cfg["weight"]},
                '{cfg["threshold"]}'::jsonb,
                '{cfg["control_type"]}',
                TRUE,
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
    """回滚：恢复旧 metric_code 与删除新增 metric_config。

    注意：回滚后 4 个指标会再次无法计算（DataPlanner 查不到 requirements）。
    仅在确认需要回滚时执行。
    """

    # Part 1: 恢复 requirements 表旧 metric_code
    for old_code, new_code, _ in REQUIREMENT_RENAMES:
        op.execute(
            f"""
            UPDATE clpm_metric_data_requirement
            SET metric_code = '{old_code}'
            WHERE metric_code = '{new_code}'
            """
        )

    # Part 2: 恢复 metric_config 旧名
    for old_code, new_code, _ in METRIC_CONFIG_RENAMES:
        op.execute(
            f"""
            UPDATE metric_config
            SET metric_code = '{old_code}'
            WHERE metric_code = '{new_code}'
            """
        )

    # Part 3: 删除新增的 metric_config（保留重命名的）
    new_codes = [c["metric_code"] for c in METRIC_CONFIG_NEW]
    for code in new_codes:
        op.execute(f"DELETE FROM metric_config WHERE metric_code = '{code}'")
