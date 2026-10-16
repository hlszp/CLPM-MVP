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

P5-03（2026-10-13，03 号文 §8 卡六项之三/四/五）：

3. **绝对有效期（命中不延寿）**：TTL 一律从**写入时刻**起算——进程层存
   绝对到期时刻（monotonic 基），命中只读不重写；Redis 层 setex 用
   ``ceil(ttl)``（原 ``int(ttl)+5`` 会超出 TTL 窗口 5s）；Redis→进程晋升
   携带 Redis 剩余 TTL（GET+TTL 双读），晋升条目不晚于 Redis 层到期。
   两层到期时刻近似一致（±1s，Redis 秒粒度）。
4. **single-flight**：同 key 冷请求并发只重建一次。进程内以"当前事件循环
   的 Future 合并表"实现（leader 重建、follower 等 leader 的 Future）；
   跨进程以 Redis ``SET NX PX`` 锁 + 轮询合并（其他 worker 在建时本地等待
   对方写值，锁超时兜底自建——极端情况多建一次，正确性不受影响）。
   **失败不缓存**：builder 抛异常时两层都不写、锁释放、异常传播给全部
   合并等待者，下一次请求自然重试（分块局部重试的基座）。
   注：合并表中的 Future 按需在**当前运行循环**内创建（非模块级
   Lock/Semaphore/Event——避红线：跨循环条目直接丢弃不 await，Celery 每
   任务新循环场景安全退化为无合并）。
5. **失效三代数（权限/数据/配置）**：``generation_suffix(db)`` =
   policyRevision（P4-03 既有）+ dataRevision（unit_kpi_summary 最新小时
   窗落库时刻——新窗落库即换 key）+ configRevision（sys_config 最新
   updated_at——配置更新即换 key）。旧 key 按 TTL 自然过期，无需清理。

容错：Redis 异常（不可用/超时 5s 上限）静默降级进程内缓存，不阻断请求。
数据安全性：值为本服务自写自读的聚合 dict，pickle 反序列化风险可控。

数据延迟上限 = TTL（默认 60s；cockpit-overview 240s 对齐驾驶舱 5min
刷新），对总览/趋势类场景无感知影响；三代数换 key 提供跨 TTL 的即时
失效（权限/新数据窗/配置三类变更）。
"""

import asyncio
import base64
import math
import pickle
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

_TTL_SECONDS = 60.0
_REDIS_PREFIX = "agg_cache:"
_FLIGHT_LOCK_PREFIX = "agg_flight:"

#: 跨进程 single-flight 锁 TTL（ms）：须覆盖最慢冷算（5~9s 聚合）+ 余量
_FLIGHT_LOCK_MS = 15_000
#: 等待其他 worker 写值的轮询间隔（s）
_FLIGHT_POLL_INTERVAL = 0.1
#: 等待其他 worker 的总上限（s）：超过即自建（锁 TTL + 宽限）
_FLIGHT_POLL_MAX_WAIT = 16.0

#: 可注入时钟（默认 monotonic）——"临到期命中不延寿"需要时间可控单测
_clock: Callable[[], float] = monotonic


async def policy_revision_suffix(db: Any) -> str:
    """P4-03：缓存 key 的 policyRevision 后缀（guard revision 变化即换 key）.

    单测环境（CLPM_TEST_MODE=1）/读取异常 → "r0"（key 稳定，不触碰 mock DB；
    真实链路 revision 推进自然失效旧缓存，旧 key 按 TTL 过期——"权限更新
    失效"）。
    """
    import os

    if os.environ.get("CLPM_TEST_MODE") == "1":
        return "r0"
    try:
        from app.services.authz_scope import read_guard_revision

        return f"r{await read_guard_revision(db)}"
    except Exception:  # noqa: BLE001 —— 读取失败不阻断聚合（缓存退化旧口径）
        return "r0"


async def _max_ts_suffix(db: Any, stmt: Any, prefix: str) -> str:
    """通用代数后缀：标量 max(datetime) → 紧凑整数（epoch 秒）.

    - CLPM_TEST_MODE=1 → "{prefix}0"（key 稳定，不触碰 mock DB）；
    - 查询异常/返回不可解析值 → "{prefix}0"（TTL 仍兜底新鲜度上限）；
    - naive 时间按 UTC 挂接（DB 存储口径；单值非热路径逐点调用）。
    """
    import os

    if os.environ.get("CLPM_TEST_MODE") == "1":
        return f"{prefix}0"
    try:
        from datetime import UTC, datetime

        val = (await db.execute(stmt)).scalar()
        if not isinstance(val, datetime):
            return f"{prefix}0"
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return f"{prefix}{int(val.timestamp())}"
    except Exception:  # noqa: BLE001 —— 读取失败不阻断聚合（TTL 兜底）
        return f"{prefix}0"


async def data_revision_suffix(db: Any) -> str:
    """P5-03 ⑤：数据源代数后缀（"d{epoch}"）.

    取 ``unit_kpi_summary`` 最新 ``snapshot_time``（小时窗落库时刻）——
    新的小时统计落库即推进代数、旧缓存 key 整体失效（"数据更新后缓存
    失效"）。查询走 ix_unit_kpi_summary_snapshot_time 索引（P5-03 迁移
    p503cache01），单行索引反向扫描，微秒级。
    """
    from sqlalchemy import func, select

    from app.models.unit_kpi_summary import UnitKpiSummary

    stmt = select(func.max(UnitKpiSummary.snapshot_time))
    return await _max_ts_suffix(db, stmt, "d")


async def config_revision_suffix(db: Any) -> str:
    """P5-03 ⑤：配置源代数后缀（"c{epoch}"）.

    取 ``sys_config.max(updated_at)``——任何 sys_config 行更新（阈值/
    角色清单/等级线等）即推进代数、旧缓存 key 失效（"配置更新后缓存
    失效"）。sys_config 为极小表，max 扫描无感知成本。
    """
    from sqlalchemy import func, select

    from app.models.sys_config import SysConfig

    stmt = select(func.max(SysConfig.updated_at))
    return await _max_ts_suffix(db, stmt, "c")


async def generation_suffix(db: Any) -> str:
    """P5-03 ⑤：三源代数合体（policy + data + config），供聚合 key 直接拼.

    三源任一推进 → 新 key → 旧条目按 TTL 自然过期（无需清理）。
    """
    rev = await policy_revision_suffix(db)
    data_rev = await data_revision_suffix(db)
    cfg_rev = await config_revision_suffix(db)
    return f"{rev}:{data_rev}:{cfg_rev}"


# Redis 层开关（默认开启；测试进程在 conftest 顶部关闭以隔离 mock 数据）
REDIS_LAYER_ENABLED = True

#: 进程层：key → (绝对到期时刻 monotonic 基, data)。命中只读不重写（不延寿）
_cache: dict[str, tuple[float, Any]] = {}

#: single-flight 合并表：key → (创建该 Future 的事件循环, Future)。
#: Future 在当前运行循环内按需创建（非模块级 asyncio 原语）；跨循环/已
#: 完成的陈旧条目直接丢弃重建，不 await（Celery 每任务新循环安全）。
_inflight: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Future]] = {}


def _redis_seconds(ttl: float) -> int:
    """TTL → Redis setex 秒数：ceil，且 ≥1（绝对有效期——不外加宽限）."""
    return max(1, math.ceil(ttl))


async def cached_agg(
    key: str,
    builder: Callable[[], Awaitable[Any]],
    ttl: float = _TTL_SECONDS,
) -> Any:
    """按 key 取缓存（进程内 → Redis）；全 miss 时 single-flight 重建并双写.

    - 命中**不延寿**：TTL 从写入时刻起算，进程层存绝对到期时刻；
      Redis→进程晋升携带 Redis 剩余 TTL，两层到期时刻一致；
    - single-flight：同 key 并发冷请求只执行一次 builder（进程内 Future
      合并 + 跨进程 Redis 锁/轮询合并）；
    - 失败不缓存：builder 抛异常时两层均不写、锁释放、异常传播。
    """
    # --- 进程层命中（绝对有效期；临到期即过期）---
    hit = _cache.get(key)
    if hit is not None and _clock() < hit[0]:
        return hit[1]

    redis_key = f"{_REDIS_PREFIX}{key}"
    if REDIS_LAYER_ENABLED:
        data = await _redis_get_with_remaining(redis_key)
        if data is not None:
            return data[0]

    # --- 全 miss：single-flight 重建 ---
    return await _single_flight_build(key, redis_key, builder, ttl)


async def _redis_get_with_remaining(redis_key: str) -> tuple[Any, int] | None:
    """Redis 层读取：值 + 剩余 TTL（GET+TTL 双读）.

    命中时把值晋升进进程层，到期时刻 = now + Redis 剩余 TTL（不晚于
    Redis 层到期——不延寿）。Redis 故障静默降级（返回 None 走重建）。
    """
    try:
        from app.core.redis import redis_client

        raw = await redis_client.get(redis_key)
        if not raw:
            return None
        remaining = await redis_client.ttl(redis_key)
        if remaining is None or remaining < 0:
            # -2=键已消失（GET/TTL 间过期）；-1=无过期（setex 语义下不可达）
            remaining = 0
        data = pickle.loads(base64.b64decode(raw))
        _cache[redis_key.removeprefix(_REDIS_PREFIX)] = (_clock() + remaining, data)
        return data, remaining
    except Exception:  # noqa: BLE001 —— Redis 故障降级，不影响冷算链路
        return None


async def _single_flight_build(
    key: str,
    redis_key: str,
    builder: Callable[[], Awaitable[Any]],
    ttl: float,
) -> Any:
    """同 key 冷请求合并重建（P5-03 ④）.

    进程内：leader 执行 builder，follower 等待 leader 的 Future（同 loop）；
    跨进程：leader 抢 Redis ``SET NX PX`` 锁后重建，其他 worker 轮询等值，
    锁超时兜底自建。builder 失败：不写任何缓存层、释放锁、异常传播。
    """
    loop = asyncio.get_running_loop()
    entry = _inflight.get(key)
    if entry is not None:
        flight_loop, future = entry
        if flight_loop is loop and not future.done() and not future.cancelled():
            return await future  # follower：等 leader 结果（含异常）
        # 陈旧条目（跨循环/已完成未清理）——丢弃，自己当 leader
        _inflight.pop(key, None)

    future: asyncio.Future = loop.create_future()
    _inflight[key] = (loop, future)
    try:
        data = await _leader_build(key, redis_key, builder, ttl)
        if not future.done():
            future.set_result(data)
        return data
    except asyncio.CancelledError:
        # leader 请求被取消（客户端断开等）：不缓存、传播取消给 leader 自身；
        # follower 收到可重试的明确异常（不是被牵连取消）
        if not future.done():
            future.set_exception(RuntimeError("agg_cache leader cancelled; builder 未缓存"))
        raise
    except BaseException as exc:  # noqa: BLE001 —— 失败必须传播且不缓存
        # 失败不缓存（两层都未写）；合并等待者收到同一异常
        if not future.done():
            future.set_exception(exc)
        raise
    finally:
        if _inflight.get(key) is not None and _inflight[key][1] is future:
            _inflight.pop(key, None)


async def _leader_build(
    key: str,
    redis_key: str,
    builder: Callable[[], Awaitable[Any]],
    ttl: float,
) -> Any:
    """leader 路径：跨进程锁 → 双检 Redis → builder → 双写 → 释放锁."""
    token = f"{key}:{_clock()}"
    lock_key = f"{_FLIGHT_LOCK_PREFIX}{redis_key}"
    locked = False
    if REDIS_LAYER_ENABLED:
        try:
            from app.core.redis import redis_client

            locked = bool(await redis_client.set(lock_key, token, nx=True, px=_FLIGHT_LOCK_MS))
        except Exception:  # noqa: BLE001 —— Redis 故障退化为进程内合并
            locked = False

    try:
        if REDIS_LAYER_ENABLED:
            if locked:
                # 双检：GET 与 SET NX 之间可能已有并发写值
                existing = await _redis_get_with_remaining(redis_key)
                if existing is not None:
                    return existing[0]
            else:
                # 其他 worker 在建：轮询等值（成功者写 Redis 即返回）
                waited = await _wait_peer_value(redis_key)
                if waited is not None:
                    return waited

        data = await builder()
        _cache[key] = (_clock() + ttl, data)
        if REDIS_LAYER_ENABLED:
            await _redis_setex(redis_key, ttl, data)
        return data
    finally:
        if locked:
            await _release_lock(lock_key, token)


async def _wait_peer_value(redis_key: str) -> Any | None:
    """follower 等待其他 worker 写值：轮询 GET 至锁 TTL 上限，超时返回 None

    （leader 兜底自建——极端情况多建一次，正确性不受影响）。命中即按
    剩余 TTL 晋升进程层（不延寿）。
    """
    waited = 0.0
    while waited < _FLIGHT_POLL_MAX_WAIT:
        got = await _redis_get_with_remaining(redis_key)
        if got is not None:
            return got[0]
        await asyncio.sleep(_FLIGHT_POLL_INTERVAL)
        waited += _FLIGHT_POLL_INTERVAL
    return None


async def _release_lock(lock_key: str, token: str) -> None:
    """释放 single-flight 锁（token 比对，两步非原子——误删风险窗口极小且

    后果仅是多一次重建，可接受）。Redis 故障静默（锁按 PX 自然过期）。
    """
    try:
        from app.core.redis import redis_client

        current = await redis_client.get(lock_key)
        if current == token:
            await redis_client.delete(lock_key)
    except Exception:  # noqa: BLE001
        pass


async def _redis_setex(redis_key: str, ttl: float, data: Any) -> None:
    """写 Redis 层（setex ceil(ttl)——绝对有效期，不外加宽限）."""
    try:
        from app.core.redis import redis_client

        await redis_client.setex(
            redis_key,
            _redis_seconds(ttl),
            base64.b64encode(pickle.dumps(data)).decode("ascii"),
        )
    except Exception:  # noqa: BLE001
        pass


def invalidate_agg(prefix: str = "") -> int:
    """清空（按前缀匹配的）进程内缓存；返回清除条数。供需要强一致的场景调用。

    仅清进程层：Redis 层按 TTL 自然过期（强一致场景调用方应接受 ≤TTL 延迟，
    或用 invalidate_agg_async 双端失效）。
    """
    if not prefix:
        n = len(_cache)
        _cache.clear()
        return n
    keys = [k for k in _cache if k.startswith(prefix)]
    for k in keys:
        _cache.pop(k, None)
    return len(keys)


async def invalidate_agg_async(prefix: str) -> int:
    """进程层 + Redis 层按前缀失效（2026-10-10 参评口径/停用复用即时反馈）。

    生产 backend 为 4 workers + Redis 共享层：只清进程层时其余 worker 仍会
    命中 Redis 旧值（最长 TTL 240s），故回路停用/启用/参评切换等改变聚合
    分母的操作必须双端失效。Redis SCAN+DEL，异常静默降级（最坏退化为 TTL
    自然过期，不阻断写操作）。返回进程层清除条数。
    """
    n = invalidate_agg(prefix)
    if not REDIS_LAYER_ENABLED:
        return n
    try:
        from app.core.redis import redis_client

        pattern = f"{_REDIS_PREFIX}{prefix}*" if prefix else f"{_REDIS_PREFIX}*"
        keys = [key async for key in redis_client.scan_iter(match=pattern, count=200)]
        if keys:
            await redis_client.delete(*keys)
    except Exception:  # noqa: BLE001 —— Redis 故障降级为 TTL 自然过期
        pass
    return n


async def prime_agg(key: str, data: Any, ttl: float = _TTL_SECONDS) -> None:
    """主动写入缓存（进程内 + Redis）。供 Beat 预热使用——绕过 cached_agg
    的取或算语义（缓存仍有效时 Beat 侧不会重算，预热必须强制写新值）。

    P5-03 ③：进程层存绝对到期时刻（now+ttl）；Redis setex 用 ceil(ttl)
    ——两层自写入时刻起同期到期，预热值不被读命中延寿。
    """
    _cache[key] = (_clock() + ttl, data)
    if not REDIS_LAYER_ENABLED:
        return
    await _redis_setex(f"{_REDIS_PREFIX}{key}", ttl, data)
