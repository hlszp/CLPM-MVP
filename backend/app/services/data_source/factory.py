"""数据源工厂 — 根据 DATA_SOURCE_TYPE 配置返回对应 Provider.

支持的类型：
- ``tdengine``: 直接查 TDengine（默认）
- ``remote_api``: 通过外部 HTTP API 查询
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.services.data_source.base import HistoryDataProvider

logger = logging.getLogger(__name__)

_provider_instance: HistoryDataProvider | None = None


def get_provider() -> HistoryDataProvider:
    """获取全局数据源 Provider 单例.

    根据 ``settings.DATA_SOURCE_TYPE`` 返回对应实现：
    - ``tdengine`` → TDengineProvider
    - ``remote_api`` → RemoteApiProvider

    Returns:
        HistoryDataProvider 实例
    """
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    source_type = settings.DATA_SOURCE_TYPE.lower()

    if source_type == "remote_api":
        from app.services.data_source.remote_api_provider import RemoteApiProvider

        _provider_instance = RemoteApiProvider()
        logger.info("数据源: RemoteApiProvider (DATA_SOURCE_TYPE=remote_api)")
    elif source_type == "tdengine":
        from app.services.data_source.tdengine_provider import TDengineProvider

        _provider_instance = TDengineProvider()
        logger.info("数据源: TDengineProvider (DATA_SOURCE_TYPE=tdengine)")
    else:
        raise ValueError(f"不支持的 DATA_SOURCE_TYPE: {source_type!r}，可选: tdengine / remote_api")

    return _provider_instance


async def close_provider() -> None:
    """关闭全局 Provider（应用关闭时调用）."""
    global _provider_instance
    if _provider_instance is not None:
        await _provider_instance.close()
        _provider_instance = None
        logger.info("数据源 Provider 已关闭")
