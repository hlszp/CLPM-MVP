"""Base schema with camelCase alias generation (S2-C1).

所有 API Schema 继承 ``CamelModel`` 后：
- Python 字段名保持 snake_case（符合 Python 规范）
- JSON 序列化/反序列化使用 camelCase 别名（符合前端规范）
- ``populate_by_name=True`` 允许同时接受 snake_case 和 camelCase 输入
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base schema with camelCase alias generation."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
