"""seed diagnosis threshold templates for 6 loop_types (P3-02)

P3-02 诊断阈值模板化与自适应：按回路类型预置差异化阈值模板。

预置 6 种 loop_type（FLOW/TEMPERATURE/PRESSURE/LEVEL/ANALYSIS/SPEED）
× 有差异化需求的 diag_code 的阈值模板，共 19 条记录。
OTHER 类型不预置（无覆盖时自动回退全局默认）。

差异化设计依据（基于 _THRESHOLD_SCHEMA 默认值 × 工业经验系数）：
- FLOW（流量）：响应快、噪声大 → 振荡阈值严、饱和阈值宽、过激阈值严
- TEMPERATURE（温度）：响应慢、惯性大 → 振荡阈值宽、过激阈值宽
- PRESSURE（压力）：响应较快 → 类似 FLOW 但稍宽
- LEVEL（液位）：积分特性 → 振荡阈值严、饱和阈值严
- ANALYSIS（分析）：响应最慢 → 振荡阈值最宽、过激阈值最宽
- SPEED（转速）：响应快、精度高 → 振荡阈值严、质量阈值严、过激阈值严

仅覆盖差异化的阈值键，未覆盖的键在引擎合并时回退全局默认
（_merge_threshold_overrides: base_threshold.update(ov.threshold)）。

幂等性：ON CONFLICT (diag_code, scope_type, scope_id) DO NOTHING，
不覆盖用户已有覆盖，安全可重入。

设计依据：P3-02 / FDS §5.4.1 / _THRESHOLD_SCHEMA
关联代码：app.tasks.diagnosis_engine._THRESHOLD_SCHEMA
           app.tasks.diagnosis_engine._merge_threshold_overrides

Revision ID: p302a1b2c3d4
Revises: f1a2b3c4d5e6
Create Date: 2026-08-06
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p302a1b2c3d4"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# 差异化阈值模板数据
# 格式: {loop_type: {diag_code: {threshold_key: value, ...}}}
# 只列出差异化的键，未列出的键回退全局默认
# ---------------------------------------------------------------------------
THRESHOLD_TEMPLATES: dict[str, dict[str, dict]] = {
    "FLOW": {
        # 流量：响应快、噪声大 → 振荡阈值严（避免噪声伪振荡）、过激阈值严
        "OSCILLATION": {
            "similarity_threshold": 0.35,
            "min_zero_crossings": 5,
            "fft_min_zero_crossings": 6,
        },
        # 流量阀快动 → 饱和判定稍宽
        "OUTPUT_SATURATION": {
            "saturation_epsilon": 3.0,
        },
        # 流量响应快，不应过激
        "OVERAGGRESSIVE": {
            "step_overshoot_threshold": 0.20,
        },
        # 流量应快速响应，τ=10s
        "OVERCONSERVATIVE": {
            "slow_expected_tau_seconds": 10.0,
            "slow_response_ratio_threshold": 1.5,
        },
    },
    "TEMPERATURE": {
        # 温度：响应慢、惯性大 → 振荡阈值宽、过激阈值宽
        "OSCILLATION": {
            "similarity_threshold": 0.45,
            "min_zero_crossings": 3,
            "fft_min_cycles": 1.5,
        },
        # 温度有惯性，允许较大过冲
        "OVERAGGRESSIVE": {
            "step_overshoot_threshold": 0.30,
        },
        # 温度慢响应正常，τ=600s
        "OVERCONSERVATIVE": {
            "slow_expected_tau_seconds": 600.0,
            "slow_response_ratio_threshold": 2.5,
        },
    },
    "PRESSURE": {
        # 压力：响应较快 → 振荡阈值稍严
        "OSCILLATION": {
            "similarity_threshold": 0.40,
            "min_zero_crossings": 4,
        },
        # 压力响应较快，τ=30s
        "OVERCONSERVATIVE": {
            "slow_expected_tau_seconds": 30.0,
            "slow_response_ratio_threshold": 2.0,
        },
    },
    "LEVEL": {
        # 液位：积分特性 → 振荡阈值严（液位振荡危险）
        "OSCILLATION": {
            "similarity_threshold": 0.35,
            "min_zero_crossings": 5,
        },
        # 液位阀饱和危险 → 饱和阈值严
        "OUTPUT_SATURATION": {
            "saturation_epsilon": 1.5,
        },
        # 液位响应较慢，τ=120s
        "OVERCONSERVATIVE": {
            "slow_expected_tau_seconds": 120.0,
            "slow_response_ratio_threshold": 2.0,
        },
    },
    "ANALYSIS": {
        # 分析：响应最慢 → 振荡阈值最宽
        "OSCILLATION": {
            "similarity_threshold": 0.50,
            "min_zero_crossings": 3,
            "fft_min_cycles": 1.0,
        },
        # 分析仪表慢，允许较大过冲
        "OVERAGGRESSIVE": {
            "step_overshoot_threshold": 0.35,
        },
        # 分析仪表响应最慢，τ=900s
        "OVERCONSERVATIVE": {
            "slow_expected_tau_seconds": 900.0,
            "slow_response_ratio_threshold": 3.0,
        },
    },
    "SPEED": {
        # 转速：响应快、精度高 → 振荡阈值严
        "OSCILLATION": {
            "similarity_threshold": 0.35,
            "min_zero_crossings": 5,
            "fft_min_zero_crossings": 6,
        },
        # 转速仪表重要 → 质量异常阈值严
        "QUALITY_ABNORMAL": {
            "q002_bad_rate": 0.05,
        },
        # 转速过激危险 → 过激阈值严
        "OVERAGGRESSIVE": {
            "step_overshoot_threshold": 0.20,
        },
        # 转速应快速响应，τ=5s
        "OVERCONSERVATIVE": {
            "slow_expected_tau_seconds": 5.0,
            "slow_response_ratio_threshold": 1.5,
        },
    },
}

# 所有预置的 loop_type（OTHER 不预置，回退全局默认)
SEEDED_LOOP_TYPES = list(THRESHOLD_TEMPLATES.keys())


def upgrade() -> None:
    """预置 6 种 loop_type 的差异化阈值模板（19 条记录，幂等）。"""
    for loop_type, diag_codes in THRESHOLD_TEMPLATES.items():
        for diag_code, threshold in diag_codes.items():
            op.execute(
                sa.text(
                    "INSERT INTO diagnosis_threshold_override "
                    "(id, diag_code, scope_type, scope_id, threshold, "
                    "version, updated_by, updated_at) "
                    "VALUES (gen_random_uuid(), :diag_code, 'loop_type', :scope_id, "
                    "CAST(:threshold AS jsonb), 1, 'system', NOW()) "
                    "ON CONFLICT (diag_code, scope_type, scope_id) DO NOTHING"
                ).bindparams(
                    sa.bindparam("diag_code", value=diag_code),
                    sa.bindparam("scope_id", value=loop_type),
                    sa.bindparam("threshold", value=json.dumps(threshold)),
                )
            )


def downgrade() -> None:
    """删除预置的 loop_type 阈值模板（仅删除 system 创建的预置数据）。"""
    loop_type_list = ",".join(f"'{lt}'" for lt in SEEDED_LOOP_TYPES)
    op.execute(
        sa.text(
            f"DELETE FROM diagnosis_threshold_override "
            f"WHERE scope_type = 'loop_type' AND scope_id IN ({loop_type_list}) "
            f"AND updated_by = 'system'"
        )
    )
