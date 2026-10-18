"""DCS MODE 映射矩阵（DcsModeMapping）.

对齐 DDS §3.1 / 算法说明 §4.0.3，**配置驱动**的 MODE 值映射矩阵。

矩阵表结构（满足用户需求"表头和第一、第二行为本系统默认，后续为各品牌型号"）：

| standard_mode | 本系统默认(dcs_model_id IS NULL) | hollysys-macs | supcon-ecs700 | ... |
|---|---|---|---|---|
| 0 (手动)       | 0                                | 0             | 0             |     |
| 1 (自动)       | 1                                | 1             | 1             |     |
| 2 (串级)       | 2                                | 2             | 2             |     |
| 3 (远程)       | 3                                | 3             | 3             |     |
| 4 (先控)       | 4                                | 4             | 4             |     |

设计要点：
- ``dcs_model_id IS NULL`` 表示本系统默认映射（1:1，种子数据 5 行）
- ``dcs_model_id`` 非 NULL 表示该型号的实际 MODE 值映射
- 回路通过 ``loop_ledger.dcs_model_id`` 关联到型号，再查本表
- ``mode_resolver.resolve_raw_to_standard()`` 优先查型号映射，回退默认

唯一约束（PostgreSQL partial unique index）：
- dcs_model_id IS NOT NULL: (dcs_model_id, standard_mode) 唯一
- dcs_model_id IS NULL: standard_mode 唯一（本系统默认每个标准 MODE 只有一条）
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DcsModeMapping(Base, TimestampMixin):
    """DCS MODE 值映射矩阵项.

    - ``dcs_model_id`` 为 NULL 时表示本系统默认映射（1:1）
    - ``standard_mode`` 为本系统标准 MODE 值（0-4）
    - ``raw_mode_value`` 为该型号 DCS 实际推送的 MODE 值

    ``mode_resolver.resolve_raw_to_standard()`` 通过 raw_mode_value 反查
    standard_mode，用于实时自控率与饼图统计。
    """

    __tablename__ = "dcs_mode_mapping"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    dcs_model_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("dcs_model.id", ondelete="CASCADE"),
        nullable=True,
        comment="关联型号 ID；NULL=本系统默认映射",
    )
    standard_mode: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="本系统标准 MODE 值：0=手动/1=自动/2=串级/3=远程/4=先控",
    )
    raw_mode_value: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="该型号 DCS 实际推送的 MODE 值（整数）",
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        # 型号映射唯一约束：每个型号每个标准 MODE 只能映射一条
        Index(
            "uk_dcs_mode_mapping_model_mode",
            "dcs_model_id",
            "standard_mode",
            unique=True,
            postgresql_where=text("dcs_model_id IS NOT NULL"),
        ),
        # 本系统默认唯一约束：每个标准 MODE 只能有一条默认映射
        Index(
            "uk_dcs_mode_mapping_default",
            "standard_mode",
            unique=True,
            postgresql_where=text("dcs_model_id IS NULL"),
        ),
        # 反向查询索引：通过 raw_mode_value 查 standard_mode
        Index("idx_dcs_mode_mapping_model_raw", "dcs_model_id", "raw_mode_value"),
    )


__all__ = ["DcsModeMapping"]
