"""seed diagnosis_config threshold keys for P2 oscillation/step/slow-response (P2 阈值配置化)

诊断引擎 P2 整改将以下硬编码阈值纳入 _THRESHOLD_SCHEMA 配置化，
本迁移同步种子数据，默认值对齐代码 _THRESHOLD_SCHEMA：

1. OSCILLATION — 追加 FFT 频域路径阈值键
   （fft_osc_index_threshold / fft_min_zero_crossings，
   _detect_oscillation_fft 此前硬编码 0.3 / 5）；
   min_zero_crossings 由 3 调整为 4，与 KPI 侧
   metric_calculator/oscillation.py 的 MIN_ZERO_CROSSINGS 对齐
   （两侧振荡判定口径统一，至少 2 个完整周期）。

2. VALVE_STICTION — 原 threshold 为 NULL，补登 Choudhury NGI/NLI
   判定阈值键（choudhury_ngi_threshold / choudhury_nli_threshold，
   ADS §5.2.2，此前硬编码 0.001 / 0.01）。

3. OVERAGGRESSIVE — 在 Harris 键基础上追加阶跃响应过激判定阈值键
   （step_overshoot_threshold / step_decay_ratio_threshold /
   step_sse_threshold，ADS §5.3.2，此前硬编码 0.25 / 0.4 / 0.05）。

4. OVERCONSERVATIVE — 原 threshold 为 NULL，补登响应迟缓判定阈值键
   （slow_response_ratio_threshold / slow_no_step_bias_ratio /
   slow_expected_tau_seconds；期望时间常数表为真实秒单位，
   修复归一化时间拟合导致的 τ 随窗口长度漂移问题）。

设计依据：诊断引擎 P2 整改 / FDS §5.4 / ADS §5.2-5.4
关联代码：app.tasks.diagnosis_engine._THRESHOLD_SCHEMA

Revision ID: c3d4e5f6a7b8
Revises: e4f5g6h7i8j9
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "e4f5g6h7i8j9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# upgrade：默认值对齐代码 _THRESHOLD_SCHEMA
OSCILLATION_UPGRADE = (
    '{"similarity_threshold": 0.4, "min_zero_crossings": 4, '
    '"fft_osc_index_threshold": 0.3, "fft_min_zero_crossings": 5}'
)
VALVE_STICTION_UPGRADE = '{"choudhury_ngi_threshold": 0.001, "choudhury_nli_threshold": 0.01}'
OVERAGGRESSIVE_UPGRADE = (
    '{"harris_ar_order": 10, "harris_warn": 2.0, '
    '"step_overshoot_threshold": 0.25, "step_decay_ratio_threshold": 0.4, '
    '"step_sse_threshold": 0.05}'
)
OVERCONSERVATIVE_UPGRADE = (
    '{"slow_response_ratio_threshold": 2.0, "slow_no_step_bias_ratio": 0.2, '
    '"slow_expected_tau_seconds": {"FLOW": 10.0, "PRESSURE": 30.0, "LEVEL": 120.0, '
    '"TEMPERATURE": 600.0, "ANALYSIS": 900.0, "OTHER": 60.0}}'
)

# downgrade：恢复 a1c1d2e3f4g5 / v6p1diag002 设置的值
OSCILLATION_DOWNGRADE = '{"similarity_threshold": 0.4, "min_zero_crossings": 3}'
OVERAGGRESSIVE_DOWNGRADE = '{"harris_ar_order": 10, "harris_warn": 2.0}'


def upgrade() -> None:
    """补齐 OSCILLATION / VALVE_STICTION / OVERAGGRESSIVE / OVERCONSERVATIVE 阈值键。"""
    for diag_code, threshold in (
        ("OSCILLATION", OSCILLATION_UPGRADE),
        ("VALVE_STICTION", VALVE_STICTION_UPGRADE),
        ("OVERAGGRESSIVE", OVERAGGRESSIVE_UPGRADE),
        ("OVERCONSERVATIVE", OVERCONSERVATIVE_UPGRADE),
    ):
        op.execute(
            f"UPDATE diagnosis_config SET threshold = '{threshold}'::jsonb "
            f"WHERE diag_code = '{diag_code}'"
        )


def downgrade() -> None:
    """恢复为 P2 之前的值（VALVE_STICTION / OVERCONSERVATIVE 回退 NULL）。"""
    op.execute(
        f"UPDATE diagnosis_config SET threshold = '{OSCILLATION_DOWNGRADE}'::jsonb "
        "WHERE diag_code = 'OSCILLATION'"
    )
    op.execute("UPDATE diagnosis_config SET threshold = NULL WHERE diag_code = 'VALVE_STICTION'")
    op.execute(
        f"UPDATE diagnosis_config SET threshold = '{OVERAGGRESSIVE_DOWNGRADE}'::jsonb "
        "WHERE diag_code = 'OVERAGGRESSIVE'"
    )
    op.execute("UPDATE diagnosis_config SET threshold = NULL WHERE diag_code = 'OVERCONSERVATIVE'")
