"""seed phase1 metric data requirement contracts

Revision ID: c588a06c1c05
Revises: 33cee6882ec8
Create Date: 2026-07-23 16:25:25.042187

Phase 1（HiaMonitor 借鉴重构，2026-07-23）：为 14 个新增指标在
clpm_metric_data_requirement 表中插入数据需求契约，供 DataPlanner 读取
以合并查询计划、选择采样/质量策略、生成 Metric Validity Mask。

新增契约（14 条，time_constant 计算器延后故契约暂不插入）：
  - instrument_fault_rate：仪表故障率（复用 outlier_reasons，BASE/pv）
  - pv_mean/pv_std、sp_mean/sp_std、op_mean/op_std、error_mean/error_std：
    PV/SP/OP/偏差 统计指标（DISPLAY_ONLY）
  - valve_linearity/valve_nonlinearity/valve_operating_range：阀门诊断指标
  - setpoint_crossing_count：设定值穿越次数
  - oscillation_amplitude：振荡幅值（L2，依赖 oscillation_rate）

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §3-§4
关联代码：app/services/metric_calculator/ （Phase 1 新增计算器将注册到
CALCULATOR_REGISTRY），app/tasks/kpi_calc.py （_DB_TO_CALCULATOR_METRIC_CODE
双向映射、layer1_db_codes / _LAYER2_DEPENDENCIES 三层编排）
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c588a06c1c05"
down_revision: str | Sequence[str] | None = "33cee6882ec8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Phase 1 新增指标代码（time_constant 延后，不含）
_PHASE1_METRIC_CODES: tuple[str, ...] = (
    "instrument_fault_rate",
    "pv_mean",
    "pv_std",
    "sp_mean",
    "sp_std",
    "op_mean",
    "op_std",
    "error_mean",
    "error_std",
    "valve_linearity",
    "valve_nonlinearity",
    "valve_operating_range",
    "setpoint_crossing_count",
    "oscillation_amplitude",
)


def upgrade() -> None:
    """插入 14 条 Phase 1 指标数据需求契约。"""
    op.execute(
        """
        INSERT INTO clpm_metric_data_requirement
            (metric_code, metric_name, tag_group, tags, sampling_strategy,
             quality_policy, mask_expression, aggregation_policy, depends_on) VALUES
        -- 仪表故障率：复用 outlier_reasons，mask 仅影响 valid_rate/可信度
        ('instrument_fault_rate', '仪表故障率', 'BASE', '["pv"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid', 'LAST', NULL),
        -- PV 统计
        ('pv_mean', 'PV均值', 'BASE', '["pv"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid', 'LAST', NULL),
        ('pv_std', 'PV标准差', 'BASE', '["pv"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid', 'LAST', NULL),
        -- SP 统计
        ('sp_mean', '设定值均值', 'BASE', '["sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'sp_valid', 'LAST', NULL),
        ('sp_std', '设定值标准差', 'BASE', '["sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'sp_valid', 'LAST', NULL),
        -- OP 统计
        ('op_mean', '输出均值', 'OP_HF', '["op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'op_valid', 'LAST', NULL),
        ('op_std', '输出标准差', 'OP_HF', '["op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'op_valid', 'LAST', NULL),
        -- 偏差统计（E = PV - SP）
        ('error_mean', '偏差均值', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
        ('error_std', '偏差标准差', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
        -- 阀门诊断（PV-OP 线性相关）
        ('valve_linearity', '阀门线性度', 'PVOP_HF', '["pv","op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && op_valid', 'LAST', NULL),
        ('valve_nonlinearity', '阀门非线性度', 'PVOP_HF', '["pv","op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && op_valid', 'LAST', NULL),
        -- 阀门运行区间（calculator code，DB 列为 valve_op_min/valve_op_max）
        ('valve_operating_range', '阀门运行区间', 'OP_HF', '["op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'op_valid', 'LAST', NULL),
        -- 设定值穿越次数
        ('setpoint_crossing_count', '设定值穿越次数', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
        -- 振荡幅值（L2，依赖 oscillation_rate）
        ('oscillation_amplitude', '振荡幅值', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST',
         '["oscillation_rate"]')
        ON CONFLICT (metric_code) DO NOTHING;
        """
    )


def downgrade() -> None:
    """删除 14 条 Phase 1 指标数据需求契约。"""
    codes_sql = ", ".join(f"'{c}'" for c in _PHASE1_METRIC_CODES)
    op.execute(f"DELETE FROM clpm_metric_data_requirement WHERE metric_code IN ({codes_sql});")
