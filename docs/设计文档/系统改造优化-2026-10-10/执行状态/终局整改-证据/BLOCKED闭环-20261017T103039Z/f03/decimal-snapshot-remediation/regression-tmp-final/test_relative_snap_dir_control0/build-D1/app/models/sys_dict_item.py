"""SysDictItem model — 通用字典项（可配置枚举）.

设计（2026-08-20，测点类型可配置决策）：
- 单表结构：dict_type 用代码常量（如 MEASURE_TYPE），不建字典类型表
  （类型元信息少，常量即可；未来新枚举类型只需注册常量 + 种子数据）
- 首期注册：MEASURE_TYPE（测点类型，7 项种子，替代 tag service 硬编码枚举）
- 引用校验：删除/禁用字典项时由 service 层校验业务表引用
  （如 MEASURE_TYPE 被 tag_registry.measure_type 引用）

已知引用关系：
- MEASURE_TYPE → tag_registry.measure_type（CHECK 约束已随迁移移除，
  合法性改由字典校验）
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SysDictItem(Base):
    """字典项。"""

    __tablename__ = "sys_dict_item"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # 字典类型编码（代码常量，如 MEASURE_TYPE）
    dict_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # 项编码（落库到业务表的值，如 TEMPERATURE）
    item_code: Mapped[str] = mapped_column(String(50), nullable=False)
    # 项显示名（中文，如 温度）
    item_label: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("dict_type", "item_code", name="uk_sys_dict_item_type_code"),
        {"comment": "通用字典项（可配置枚举）"},
    )
