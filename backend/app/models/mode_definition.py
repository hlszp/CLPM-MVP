"""标准 MODE 定义配置表（ModeDefinition）.

对齐 DDS §3.1 / 算法说明 §4.0.3，**配置驱动**的标准 MODE 定义。

替代 ``app/constants/mode.py`` 中的 ``AUTO_MODES`` 硬编码集合：
- 运行时从本表读取 ``is_auto=True`` 的 ``standard_mode`` 集合
- 表为空时回退到 ``app.constants.mode.AUTO_MODES``（{1, 2, 3, 4}）
- 管理员可通过本表覆盖默认定义（如某项目不计 APC 为自动）

种子数据（5 行，与 StandardMode 枚举对齐）：

| standard_mode | label_zh | label_en | is_auto | color   |
|---|---|---|---|---|
| 0              | 手动     | MANUAL   | FALSE   | #d4380d |
| 1              | 自动     | AUTO     | TRUE    | #52c41a |
| 2              | 串级     | CAS      | TRUE    | #1890ff |
| 3              | 远程     | REMOTE   | TRUE    | #722ed1 |
| 4              | 先控     | APC      | TRUE    | #13c2c2 |
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ModeDefinition(Base, TimestampMixin):
    """标准 MODE 定义（配置驱动，可覆盖常量）.

    ``standard_mode`` 为系统统一枚举值（0-4），``is_auto`` 标识该 MODE
    是否计入自控率。``mode_resolver.get_auto_modes()`` 优先读本表，
    表为空时回退到 ``app.constants.mode.AUTO_MODES``。
    """

    __tablename__ = "mode_definition"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    standard_mode: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        unique=True,
        comment="标准 MODE 值：0=手动/1=自动/2=串级/3=远程/4=先控",
    )
    label_zh: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="中文标签"
    )
    label_en: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="英文标签（与 DDS mode_label 对齐）"
    )
    is_auto: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否计入自控率（AUTO/CAS/REMOTE/APC 为 True）",
    )
    color: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="#999999",
        comment="图表配色（Hex）",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="排序权重"
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "standard_mode IN (0, 1, 2, 3, 4)",
            name="ck_mode_definition_standard_mode",
        ),
        Index("idx_mode_definition_sort", "sort_order"),
    )


__all__ = ["ModeDefinition"]
