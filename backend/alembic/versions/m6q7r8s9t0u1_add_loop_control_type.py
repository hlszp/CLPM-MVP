"""add control_type to loop_ledger

P2 #24 B3 + P2 #25 B4: 补全回路控制类型字段。

背景：
- 前端 `LoopQueryParams.controlType` 声明为 'FAST'|'LOGIC'|'SLOW'|'STABLE'
- 前端创建/编辑表单也提供 controlType 字段
- 但 `LoopLedger` 表无 `control_type` 列，前端筛选/创建/编辑均被静默忽略
- `control_type` 与 `loop_type`（业务类型 TEMPERATURE/PRESSURE/...）是不同概念：
  - `loop_type` 描述物理量（温度/压力/液位/流量/...）
  - `control_type` 描述控制特性（稳定/慢速/快速/逻辑），用于评分权重分类
  - 两者均存储在 LoopLedger，但语义独立

设计依据：
- GB/T 44693.2-2024 附表1：4 种控制类型对应不同评分权重（a/f/s）
- LoopTypeWeight 表已使用 STABLE/SLOW/FAST/LOGIC 作为 loop_type 字段值
- 本迁移在 LoopLedger 表新增 control_type 列，与 LoopTypeWeight.loop_type 对齐

Revision ID: m6q7r8s9t0u1
Revises: l5p6q7r8s9t0
Create Date: 2026-07-03 10:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "m6q7r8s9t0u1"
down_revision = "l5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "loop_ledger",
        sa.Column(
            "control_type",
            sa.String(20),
            nullable=True,
            comment="P2 #24: 控制类型 STABLE/SLOW/FAST/LOGIC（用于评分权重分类）",
        ),
    )
    op.execute(
        "COMMENT ON COLUMN loop_ledger.control_type IS "
        "'P2 #24: 控制类型 STABLE/SLOW/FAST/LOGIC（与 loop_type 业务类型独立）'"
    )
    # 不添加 CHECK 约束，保持向后兼容（NULL 表示未配置，使用默认 STABLE）


def downgrade() -> None:
    op.drop_column("loop_ledger", "control_type")
