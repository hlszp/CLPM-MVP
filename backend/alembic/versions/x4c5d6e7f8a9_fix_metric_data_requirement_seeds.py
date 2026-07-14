"""fix metric_data_requirement seeds to align with CALCULATOR_REGISTRY

修正 clpm_metric_data_requirement 种子数据，使其与 CALCULATOR_REGISTRY 中的
metric_code 完全对齐。当前种子数据有 5 个 metric_code 不一致，导致 DataPlanner
无法匹配 Calculator 注册表。

修正内容：
1. 删除 5 个旧 metric_code，插入 5 个新 metric_code：
   - fast_response_rate → fast_rate
   - steady_rate → stability_rate
   - stiction_coeff → stiction_index
   - output_travel_index → output_trip_index
   - steady_state_time → settling_time
2. 修正 oscillation_rate 的 tag_group：BASE → PVOP_HF，tags：["pv","sp"] → ["pv","op"]
3. 修正 effective_auto_rate 的 depends_on：["auto_mode_rate","saturation_rate"] → NULL
4. 新增范围查询索引 idx_kpi_snapshot_ts_loop (ts_start, loop_id)

设计依据：评估算法优化改进方案 v1.0 Phase 1
关联代码：app/services/metric_calculator/__init__.py CALCULATOR_REGISTRY

Revision ID: x4c5d6e7f8a9
Revises: v6p1dcs001
Create Date: 2026-07-14
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "x4c5d6e7f8a9"
down_revision = "v6p1dcs001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """修正种子数据 + 新增范围查询索引."""

    # ========================================================
    # 1. 删除 5 个旧 metric_code（与 CALCULATOR_REGISTRY 不一致）
    # ========================================================
    op.execute("""
        DELETE FROM clpm_metric_data_requirement
        WHERE metric_code IN (
            'fast_response_rate',
            'steady_rate',
            'stiction_coeff',
            'output_travel_index',
            'steady_state_time'
        );
    """)

    # ========================================================
    # 2. 插入 5 个新 metric_code（对齐 CALCULATOR_REGISTRY）
    # ========================================================
    op.execute("""
        INSERT INTO clpm_metric_data_requirement
            (metric_code, metric_name, tag_group, tags, sampling_strategy,
             quality_policy, mask_expression, aggregation_policy, depends_on) VALUES
        ('fast_rate', '快速率', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid',
         'LAST', '["settling_time","ideal_settling_time"]'),
        ('stability_rate', '稳定率', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid',
         'LAST', '["oscillation_rate"]'),
        ('stiction_index', '粘滞系数', 'PVOP_HF', '["pv","op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && op_valid', 'LAST', NULL),
        ('output_trip_index', '输出值行程指数', 'OP_HF', '["op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'op_valid', 'LAST', NULL),
        ('settling_time', '稳态时间', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL)
        ON CONFLICT (metric_code) DO NOTHING;
    """)

    # ========================================================
    # 3. 修正 oscillation_rate：tag_group BASE→PVOP_HF，tags ["pv","sp"]→["pv","op"]
    # ========================================================
    op.execute("""
        UPDATE clpm_metric_data_requirement
        SET tag_group = 'PVOP_HF',
            tags = '["pv","op"]'::jsonb,
            mask_expression = 'pv_valid && op_valid'
        WHERE metric_code = 'oscillation_rate';
    """)

    # ========================================================
    # 4. 修正 effective_auto_rate：清除错误的 depends_on
    #    EffectiveAutoRateCalculator 无 depends_on（不依赖其他指标）
    # ========================================================
    op.execute("""
        UPDATE clpm_metric_data_requirement
        SET depends_on = NULL
        WHERE metric_code = 'effective_auto_rate';
    """)

    # ========================================================
    # 5. 新增范围查询索引 idx_kpi_snapshot_ts_loop (ts_start, loop_id)
    #    优化按时间范围 + 回路 ID 的查询性能（如趋势图、回填查询）
    # ========================================================
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_kpi_snapshot_ts_loop
        ON kpi_snapshot_hourly (ts_start, loop_id);
    """)


def downgrade() -> None:
    """回滚：恢复旧种子数据 + 删除索引."""

    # 删除新增的索引
    op.execute("DROP INDEX IF EXISTS idx_kpi_snapshot_ts_loop;")

    # 恢复 effective_auto_rate 的 depends_on
    op.execute("""
        UPDATE clpm_metric_data_requirement
        SET depends_on = '["auto_mode_rate","saturation_rate"]'::jsonb
        WHERE metric_code = 'effective_auto_rate';
    """)

    # 恢复 oscillation_rate
    op.execute("""
        UPDATE clpm_metric_data_requirement
        SET tag_group = 'BASE',
            tags = '["pv","sp"]'::jsonb,
            mask_expression = 'pv_valid && sp_valid'
        WHERE metric_code = 'oscillation_rate';
    """)

    # 删除新增的 5 个 metric_code
    op.execute("""
        DELETE FROM clpm_metric_data_requirement
        WHERE metric_code IN (
            'fast_rate', 'stability_rate', 'stiction_index',
            'output_trip_index', 'settling_time'
        );
    """)

    # 恢复旧的 5 个 metric_code
    op.execute("""
        INSERT INTO clpm_metric_data_requirement
            (metric_code, metric_name, tag_group, tags, sampling_strategy,
             quality_policy, mask_expression, aggregation_policy, depends_on) VALUES
        ('fast_response_rate', '快速率', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid',
         'LAST', '["settling_time","ideal_settling_time"]'),
        ('steady_rate', '稳定率', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid',
         'LAST', '["oscillation_rate"]'),
        ('stiction_coeff', '粘滞系数', 'PVOP_HF', '["pv","op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && op_valid', 'LAST', NULL),
        ('output_travel_index', '输出值行程指数', 'OP_HF', '["op"]',
         'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'op_valid', 'LAST', NULL),
        ('steady_state_time', '稳态时间', 'BASE', '["pv","sp"]',
         'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL)
        ON CONFLICT (metric_code) DO NOTHING;
    """)
