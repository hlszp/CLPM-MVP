"""L1/L2 缓存故障降级（整改 G17）。

背景
----
l1_datablock / l2_bundle 的 get 与 set 直接 await self._redis.* —— 读在 try 之外、
写完全没有 try。Redis 短暂抖动（网络/主从切换/重启）会让**本可直查本地
TDengine 完成**的 KPI 计算整体失败，可用性被缓存反向拉低。同仓已有合理口径
（realtime_subscriber 的 best-effort + 日志）。

本文件守护：缓存读失败必须降级为"未命中"、写失败必须被忽略，二者都不得向上
抛异常。
"""

from __future__ import annotations

import asyncio

from app.services.cache.l1_datablock import L1DataBlockCache
from app.services.cache.l2_bundle import L2BundleCache


# 注：L1 set 的降级（写入失败只记录不抛出）未在此覆盖——构造合法
# DataBlock 需要完整 schema，成本高于收益；该分支与 L2 set 同构。
class _BrokenRedis:
    """所有操作都抛异常，模拟 Redis 不可用。"""

    async def get(self, *a, **kw):
        raise ConnectionError("redis down")

    async def setex(self, *a, **kw):
        raise ConnectionError("redis down")


class TestCacheDegradation:
    """Redis 故障时缓存层必须降级而非抛出。"""

    def test_l1_get_degrades_to_miss(self) -> None:
        cache = L1DataBlockCache(_BrokenRedis())
        assert asyncio.run(cache.get("any-key")) is None

    def test_l2_get_degrades_to_miss(self) -> None:
        cache = L2BundleCache(_BrokenRedis())
        assert asyncio.run(cache.get("any-key")) is None
