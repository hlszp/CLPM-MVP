"""数据源抽象层 — 计算类历史数据查询统一走本地 TDengine.

架构决策（2026-07-20）：**导入走远端、计算全本地**。
- 远端历史数据接口（remote_api）仅"数据管理→历史数据导入"任务直接调用
  （services/data_import.py 自带独立 HTTP 客户端，不经本层）
- 性能评估、回路诊断、回路整定等计算任务一律通过本层获取
  本地 TDengineProvider（宽表查询 + Redis 实时缓存探测），
  不得自动降级或切换到远端 API

设计原则：DataPlanner 接收的 ``tdengine_query_fn`` 签名不变，
Provider 负责将该签名的调用转发到本地 TDengine。
"""

from __future__ import annotations

from app.services.data_source.base import HistoryDataProvider
from app.services.data_source.factory import get_provider

__all__ = ["HistoryDataProvider", "get_provider"]
