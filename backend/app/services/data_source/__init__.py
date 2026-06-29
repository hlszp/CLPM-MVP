"""数据源抽象层 — 支持多数据源切换（TDengine / 远程 HTTP API）.

通过 ``HistoryDataProvider`` 协议统一数据源接口，由工厂根据
``DATA_SOURCE_TYPE`` 配置返回对应实现：

- ``tdengine``: 直接查 TDengine（默认，生产链路）
- ``remote_api``: 通过外部 HTTP API（HistoryDataAppService）查询

设计原则：DataPlanner 接收的 ``tdengine_query_fn`` 签名不变，
Provider 负责将该签名的调用转发到具体数据源。
"""

from __future__ import annotations

from app.services.data_source.base import HistoryDataProvider
from app.services.data_source.factory import get_provider

__all__ = ["HistoryDataProvider", "get_provider"]
