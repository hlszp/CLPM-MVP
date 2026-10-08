"""聚合接口短 TTL 内存缓存（2026-10-03 驾驶舱性能优化）。

驾驶舱/工作台聚合在生产 1209 回路下耗时显著，而页面刷新周期为 5min、
多用户并发/手动刷新会重复触发同参数聚合。按参数键 60s TTL 缓存结果：
- backend 为单进程 API 服务（worker/beat 为独立进程不经过本层），进程内
  dict 即可，无需分布式一致性；
- 数据延迟上限 60s，对总览/趋势类场景无感知影响；
- 仅缓存**聚合结果 dict**（不缓存 db 会话等不可复用对象）。
"""

from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

_TTL_SECONDS = 60.0

_cache: dict[str, tuple[float, Any]] = {}


async def cached_agg(
    key: str,
    builder: Callable[[], Awaitable[Any]],
    ttl: float = _TTL_SECONDS,
) -> Any:
    """按 key 取缓存；过期/未命中时执行 builder 重建并回填。

    ttl 可按调用方覆盖（2026-10-08 性能批：cockpit-overview 用 240s 对齐
    驾驶舱 5min 刷新周期，冷算频率降 4 倍）。
    """
    hit = _cache.get(key)
    if hit is not None and monotonic() - hit[0] < ttl:
        return hit[1]
    data = await builder()
    _cache[key] = (monotonic(), data)
    return data


def invalidate_agg(prefix: str = "") -> int:
    """清空（按前缀匹配的）缓存；返回清除条数。供需要强一致的场景调用。"""
    if not prefix:
        n = len(_cache)
        _cache.clear()
        return n
    keys = [k for k in _cache if k.startswith(prefix)]
    for k in keys:
        _cache.pop(k, None)
    return len(keys)
