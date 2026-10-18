"""数据源工厂 — 计算类历史数据查询统一返回本地 TDengine Provider.

架构决策（2026-07-20 用户定调）：**导入走远端、计算全本地**。
- 历史数据有两个数据源：① 远端 AAS 系统的历史数据接口（remote_api），
  **有且仅有**"数据管理 → 历史数据导入"手工任务可调用（`services/data_import.py`
  自带独立 HTTP 客户端，不经本工厂）；② 本地 TDengine，是所有性能评估、
  回路诊断、回路整定等计算任务的唯一历史数据来源。
- 任何计算任务**不得**自动降级或切换到远端 API 取数；本地数据不完整时
  按 INCONCLUSIVE/数据不足提示，由用户通过导入任务补齐。
- ``DATA_SOURCE_TYPE`` 配置项已废止（保留仅为配置兼容），本工厂不再按其分支。
- 实时数据来源唯一：SignalR Hub（realtime_subscriber），与本工厂无关。
"""

from __future__ import annotations

import logging

from app.services.data_source.base import HistoryDataProvider

logger = logging.getLogger(__name__)

_provider_instance: HistoryDataProvider | None = None


def get_provider() -> HistoryDataProvider:
    """获取全局数据源 Provider 单例（计算任务一律为本地 TDengineProvider）.

    Returns:
        HistoryDataProvider 实例（本地 TDengine 宽表 + Redis 实时缓存探测）
    """
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    from app.services.data_source.tdengine_provider import TDengineProvider

    _provider_instance = TDengineProvider()
    logger.info("数据源: TDengineProvider（计算一律本地 TDengine，远端 API 仅导入任务调用）")
    return _provider_instance


async def close_provider() -> None:
    """关闭全局 Provider（应用关闭时调用）."""
    global _provider_instance
    if _provider_instance is not None:
        await _provider_instance.close()
        _provider_instance = None
        logger.info("数据源 Provider 已关闭")
