"""tuning SIMC/fallback_step CHECK 补齐 + Choudhury NGI 阈值数据修复.

Revision ID: g7a8b9c0d1e2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-28

变更内容（整定 Phase 2.1 合并评审 P1-5/P1-6 + 粘滞阈值数据迁移）：

1. P1-5：ck_tuning_record_algo 允许值补 'SIMC'（PG 需 DROP + ADD CHECK）。
   e5f6a7b8c9d0 新增 12 列与状态枚举时遗漏本约束：ORM
   （app/models/tuning.py）、schemas（TuningAlgorithm）与 tune_simc 均已
   支持 SIMC，DB CHECK 未同步 → 保存 SIMC 整定任务被 DB 拒绝。
   db/postgresql/01_schema.sql §12 同步补齐（含 12 新列与新状态枚举）。

2. P1-6：ck_tuning_record_data_source 允许值补 'fallback_step'。
   AUTO 策略历史辨识失败/数据不足时降级阶跃实验路径，结果以此值标注
   数据来源（app/tasks/tuning.py 兜底逻辑写入）。

3. 数据迁移：diagnosis_config VALVE_STICTION 的 choudhury_ngi_threshold
   0.001 → 1.0。c3d4e5f6a7b8 按旧量纲域口径种子了 0.001，而代码新默认值
   已改为增量域口径 1.0（app/tasks/diagnosis_engine.py _THRESHOLD_SCHEMA，
   NGI>1.0 对应增量 excess kurtosis>6 的重尾跳变）；库中 0.001 会覆盖
   代码默认值，架空误报修复。用 jsonb_set 仅更新目标键，保留
   choudhury_nli_threshold 等 sibling 键。
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g7a8b9c0d1e2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """升级：SIMC/fallback_step 入 CHECK + NGI 阈值更正为 1.0."""
    # 1. ck_tuning_record_algo 补 SIMC
    op.drop_constraint("ck_tuning_record_algo", "tuning_record", type_="check")
    op.create_check_constraint(
        "ck_tuning_record_algo",
        "tuning_record",
        "algorithm IN ('IMC', 'LAMBDA', 'ZN', 'COHEN_COON', 'SIMC')",
    )

    # 2. ck_tuning_record_data_source 补 fallback_step
    op.drop_constraint("ck_tuning_record_data_source", "tuning_record", type_="check")
    op.create_check_constraint(
        "ck_tuning_record_data_source",
        "tuning_record",
        "data_source IS NULL OR data_source IN ('HISTORY', 'STEP_EXPERIMENT', 'fallback_step')",
    )

    # 3. NGI 阈值数据修复：0.001 → 1.0（增量域口径，仅更新目标键）
    op.execute(
        "UPDATE diagnosis_config "
        "SET threshold = jsonb_set(threshold, '{choudhury_ngi_threshold}', '1.0'::jsonb, true) "
        "WHERE diag_code = 'VALVE_STICTION' AND threshold IS NOT NULL"
    )


def downgrade() -> None:
    """降级：恢复旧 CHECK 与旧 NGI 阈值.

    注意：若 tuning_record 已存在 algorithm='SIMC' 或 data_source='fallback_step'
    的行，CHECK 回退将失败，需先清理相关行。
    """
    # 恢复 NGI 阈值旧值（c3d4e5f6a7b8 的种子口径）
    op.execute(
        "UPDATE diagnosis_config "
        "SET threshold = jsonb_set(threshold, '{choudhury_ngi_threshold}', '0.001'::jsonb, true) "
        "WHERE diag_code = 'VALVE_STICTION' AND threshold IS NOT NULL"
    )

    # data_source CHECK 回退为两值
    op.drop_constraint("ck_tuning_record_data_source", "tuning_record", type_="check")
    op.create_check_constraint(
        "ck_tuning_record_data_source",
        "tuning_record",
        "data_source IS NULL OR data_source IN ('HISTORY', 'STEP_EXPERIMENT')",
    )

    # algo CHECK 回退为四值（不含 SIMC）
    op.drop_constraint("ck_tuning_record_algo", "tuning_record", type_="check")
    op.create_check_constraint(
        "ck_tuning_record_algo",
        "tuning_record",
        "algorithm IN ('IMC', 'LAMBDA', 'ZN', 'COHEN_COON')",
    )
