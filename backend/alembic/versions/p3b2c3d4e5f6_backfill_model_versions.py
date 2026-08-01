"""V62-P3-005 一次性回填：tuning_record.model_params → process_model_version.

revision: p3b2c3d4e5f6
down_revision: p3a1b2c3d4e5

将遗留 ``tuning_record`` 的 ``model_params`` 一次性回填到
``process_model_version`` CANDIDATE，并回填 ``process_model_version_id`` 外键。

回填策略（v6.2 方案 §10）：
- 仅处理 ``model_params IS NOT NULL`` 且 ``process_model_version_id IS NULL`` 的记录；
- 为每条记录创建一个 ``process_model_version`` CANDIDATE（status=CANDIDATE）；
- 携带 model_type / model_params / identify_method / confidence_level 等元数据；
- 回填 ``tuning_record.process_model_version_id`` 外键；
- 幂等：重复执行无副作用（已关联版本的记录跳过）。

回填后，读路径优先从 ``process_model_version`` 读取（P3-005 步骤 3），
新辨识结果不再写 ``tuning_record.model_params``（P3-005 步骤 4）。

downgrade：不自动删除回填的版本记录（数据不可逆），仅清空 FK 引用。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "p3b2c3d4e5f6"
down_revision = "p3a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """一次性回填：为遗留 tuning_record 创建 process_model_version CANDIDATE.

    使用 INSERT ... SELECT FROM ... WHERE NOT EXISTS 保证幂等：
    仅对未关联版本且有 model_params 的记录创建版本。
    """
    op.execute(
        sa.text(
            """
            INSERT INTO process_model_version (
                id, loop_id, version, status,
                data_window_start, data_window_end,
                algorithm_version, identify_method,
                model_type, model_params, theta_source,
                metrics, residual_test,
                confidence_level, confidence_reason,
                created_by, created_at
            )
            SELECT
                uuid_generate_v4(),
                tr.loop_id,
                COALESCE(
                    (SELECT MAX(pmv.version) FROM process_model_version pmv
                     WHERE pmv.loop_id = tr.loop_id),
                    0
                ) + ROW_NUMBER() OVER (PARTITION BY tr.loop_id ORDER BY tr.created_at),
                'CANDIDATE',
                tr.time_window_start,
                tr.time_window_end,
                NULL,
                tr.identify_method,
                tr.model_type,
                tr.model_params,
                CASE
                    WHEN UPPER(COALESCE(tr.confidence_reason, ''))
                        LIKE '%THETA_SOURCE=HEURISTIC_2TS%'
                        THEN 'HEURISTIC_2TS'
                    WHEN UPPER(COALESCE(tr.confidence_reason, ''))
                        LIKE '%THETA_SOURCE=EXPLICIT%'
                        THEN 'EXPLICIT'
                    WHEN UPPER(COALESCE(tr.confidence_reason, ''))
                        LIKE '%THETA_SOURCE=SEARCHED%'
                        THEN 'SEARCHED'
                    ELSE NULL
                END,
                CASE
                    WHEN tr.fitting_score IS NOT NULL OR tr.excitation_score IS NOT NULL THEN
                        jsonb_build_object(
                            'fitting_score', tr.fitting_score,
                            'excitation_score', tr.excitation_score
                        )
                    ELSE NULL
                END,
                CASE
                    WHEN tr.residual_test_passed IS NOT NULL THEN
                        jsonb_build_object('passed', tr.residual_test_passed)
                    ELSE NULL
                END,
                tr.confidence_level,
                tr.confidence_reason,
                tr.created_by,
                NOW() AT TIME ZONE 'UTC'
            FROM tuning_record tr
            WHERE tr.model_params IS NOT NULL
              AND tr.process_model_version_id IS NULL
            """
        )
    )

    # 回填 FK：tuning_record.process_model_version_id → 新创建的版本
    # JSON 类型不支持 = 操作符，用 ::text 转换后比较
    op.execute(
        sa.text(
            """
            UPDATE tuning_record tr
            SET process_model_version_id = (
                SELECT pmv.id
                FROM process_model_version pmv
                WHERE pmv.loop_id = tr.loop_id
                  AND pmv.model_params::text = tr.model_params::text
                  AND pmv.identify_method IS NOT DISTINCT FROM tr.identify_method
                  AND pmv.confidence_level IS NOT DISTINCT FROM tr.confidence_level
                  AND pmv.created_by IS NOT DISTINCT FROM tr.created_by
                ORDER BY pmv.version ASC
                LIMIT 1
            )
            WHERE tr.model_params IS NOT NULL
              AND tr.process_model_version_id IS NULL
            """
        )
    )


def downgrade() -> None:
    """清空回填的 FK 引用（不删除版本记录，数据不可逆）.

    回填创建的 process_model_version CANDIDATE 记录保留，仅解除关联。
    如需完全回滚，需手工清理 status=CANDIDATE 且无 tuning_record 引用的版本。
    """
    op.execute(
        sa.text(
            """
            UPDATE tuning_record
            SET process_model_version_id = NULL
            WHERE process_model_version_id IS NOT NULL
            """
        )
    )
