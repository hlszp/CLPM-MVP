"""聚合接口短 TTL 缓存（2026-10-03 驾驶舱性能优化；2026-10-09 升两级）。

驾驶舱/工作台聚合在生产 1209 回路下冷算耗时显著（overview/diagnosis 5~9s），
而页面刷新周期为 5min、多用户并发/手动刷新会重复触发同参数聚合。按参数键
TTL 缓存聚合结果（仅缓存**聚合结果**，不缓存 db 会话等不可复用对象）。

两级结构（2026-10-09）：
1. 进程内 dict——热路径零网络；
2. Redis（base64(pickle) 保真 Decimal/datetime；键前缀 ``agg_cache:``）——
   **生产 backend 为 uvicorn 4 workers**，纯进程内缓存请求轮转命中率仅 1/4，
   同参数请求 75% 概率冷算 5~9s（实测第 2 次仍 6.7s 的根因）。Redis 层跨
   worker 共享后第二次起全 worker 命中。

容错：Redis 异常（不可用/超时 5s 上限）静默降级进程内缓存，不阻断请求。
数据安全性：值为本服务自写自读的聚合 dict，pickle 反序列化风险可控。

数据延迟上限 = TTL（默认 60s；cockpit-overview 240s 对齐驾驶舱 5min 刷新），
对总览/趋势类场景无感知影响。
"""

import base64
import pickle
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

_TTL_SECONDS = 60.0
_REDIS_PREFIX = "agg_cache:"

# Redis 层开关（默认开启；测试进程在 conftest 顶部关闭以隔离 mock 数据）
REDIS_LAYER_ENABLED = True

_cache: dict[str, tuple[float, Any]] = {}


async def cached_agg(
    key: str,
    builder: Callable[[], Awaitable[Any]],
    ttl: float = _TTL_SECONDS,
) -> Any:
    """按 key 取缓存（进程内 → Redis）；全 miss 时执行 builder 重建并双写。"""
    hit = _cache.get(key)
    if hit is not None and monotonic() - hit[0] < ttl:
        return hit[1]

    redis_key = f"{_REDIS_PREFIX}{key}"
    if REDIS_LAYER_ENABLED:
        try:
            from app.core.redis import redis_client

            raw = await redis_client.get(redis_key)
            if raw:
                data = pickle.loads(base64.b64decode(raw))
                _cache[key] = (monotonic(), data)
                return data
        except Exception:  # noqa: BLE001 —— Redis 故障降级，不影响冷算链路
            pass

    data = await builder()
    _cache[key] = (monotonic(), data)
    if REDIS_LAYER_ENABLED:
        try:
            from app.core.redis import redis_client

            await redis_client.setex(
                redis_key,
                int(ttl) + 5,
                base64.b64encode(pickle.dumps(data)).decode("ascii"),
            )
        except Exception:  # noqa: BLE001
            pass
    return data


def invalidate_agg(prefix: str = "") -> int:
    """清空（按前缀匹配的）进程内缓存；返回清除条数。供需要强一致的场景调用。

    仅清进程层：Redis 层按 TTL 自然过期（强一致场景调用方应接受 ≤TTL 延迟，
    或另行扩 Redis 侧失效）。
    """
    if not prefix:
        n = len(_cache)
        _cache.clear()
        return n
    keys = [k for k in _cache if k.startswith(prefix)]
    for k in keys:
        _cache.pop(k, None)
    return len(keys)


async def prime_agg(key: str, data: Any, ttl: float = _TTL_SECONDS) -> None:
    """主动写入缓存（进程内 + Redis）。供 Beat 预热使用——绕过 cached_agg
    的取或算语义（缓存仍有效时 Beat 侧不会重算，预热必须强制写新值）。"""
    _cache[key] = (monotonic(), data)
    if not REDIS_LAYER_ENABLED:
        return
    try:
        from app.core.redis import redis_client

        await redis_client.setex(
            f"{_REDIS_PREFIX}{key}",
            int(ttl) + 5,
            base64.b64encode(pickle.dumps(data)).decode("ascii"),
        )
    except Exception:  # noqa: BLE001
        pass
