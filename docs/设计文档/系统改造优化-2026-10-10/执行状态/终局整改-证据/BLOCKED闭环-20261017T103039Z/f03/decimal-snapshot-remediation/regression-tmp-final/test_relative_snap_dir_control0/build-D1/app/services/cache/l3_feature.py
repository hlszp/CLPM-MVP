"""L3 特征缓存 — 中间计算结果复用 + zstd 压缩.

缓存指标计算过程中的中间特征值（如 ARMA 模型参数、IAE 累积值、
sum/sumSq/count/ΔOP 等统计量），避免滚动窗口或同批次内重复计算。
TTL=1800s（特征值相对稳定，较长 TTL 提升复用率）。

缓存 Key 格式（任务规范）::

    pdb_l3:{loopId}:{metric_code}:{feature_name}:{window_hash}

序列化链路（与 L1 一致，兼容 ``decode_responses=True`` 的 Redis 客户端）::

    dict → JSON(utf-8) → zstd 压缩 → base64 → str → Redis

设计依据：ADS §10.7.1（L3 特征缓存）, §10.7.4（L3 特征增量计算）,
FDS §5.3.9, 数据流程图 §7.1
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import zstandard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL（秒）— 设计依据：ADS §10.7.1（特征值较稳定，30 分钟）
# ---------------------------------------------------------------------------
DEFAULT_TTL = 1800  # 30 分钟

# L3 缓存 Key 前缀（任务规范）
_KEY_PREFIX = "pdb_l3"

# zstd 压缩级别（与 L1 一致）
_ZSTD_LEVEL = 3


class L3FeatureCache:
    """L3 特征缓存（zstd 压缩 + Pipeline 批量写入）.

    职责：
        - ``get``：从 Redis 读取并反序列化为特征值字典
        - ``set``：zstd 压缩 + base64 编码后写入 Redis
        - ``set_many``：通过 Redis Pipeline 批量写入多个特征，减少网络往返
        - ``get_or_set``：命中即返回，未命中调用 factory 生成并写入

    典型场景：
        - ARMA 模型参数（``fast_rate`` 指标，O(N²) 复杂度，复用价值高）
        - IAE 累积值（积分绝对误差）
        - sum/sumSq/count 统计量（支撑滚动窗口增量计算，ADS §10.7.4）

    设计依据：ADS §10.7.1, §10.7.4, FDS §5.3.9
    """

    def __init__(self, redis_client: Any) -> None:
        """初始化 L3 缓存.

        Args:
            redis_client: 异步 Redis 客户端（兼容 ``decode_responses=True``），
                需支持 ``get`` / ``setex`` / ``pipeline`` / ``delete`` 接口。
                通过依赖注入传入，便于测试时替换为 FakeRedis。
        """
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Key 生成
    # ------------------------------------------------------------------

    @staticmethod
    def build_key(
        loop_id: str,
        metric_code: str,
        feature_name: str,
        time_window_start: datetime,
        time_window_end: datetime,
    ) -> str:
        """生成 L3 缓存 Key.

        Key 格式（任务规范）::

            pdb_l3:{loopId}:{metric_code}:{feature_name}:{window_hash}

        - ``window_hash``：时间窗口短哈希（与 L1 ``time_window_hash`` 一致）

        设计依据：ADS §10.7.1, 任务规范
        """
        window_hash = _time_window_hash(time_window_start, time_window_end)
        return f"{_KEY_PREFIX}:{loop_id}:{metric_code}:{feature_name}:{window_hash}"

    # ------------------------------------------------------------------
    # 单条读写
    # ------------------------------------------------------------------

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        """从 Redis 获取特征值字典（zstd 解压 + 反序列化）.

        Args:
            cache_key: 完整的缓存 Key（由 ``build_key`` 生成）

        Returns:
            特征值字典；未命中返回 ``None``

        设计依据：ADS §10.7.2
        """
        raw: str | None = await self._redis.get(cache_key)
        if raw is None:
            logger.debug("L3 cache MISS: key=%s", cache_key)
            return None
        try:
            feature = _deserialize(raw)
        except Exception:  # noqa: BLE001
            logger.warning("L3 cache 反序列化失败，丢弃脏数据: key=%s", cache_key, exc_info=True)
            await self._redis.delete(cache_key)
            return None
        logger.debug(
            "L3 cache HIT: key=%s, fields=%d",
            cache_key,
            len(feature),
        )
        return feature

    async def set(
        self,
        cache_key: str,
        feature: dict[str, Any],
        ttl: int | None = None,
    ) -> None:
        """写入 Redis（zstd 压缩 + base64 编码 + SETEX）.

        Args:
            cache_key: 完整缓存 Key（由 ``build_key`` 生成）
            feature: 待缓存的特征值字典
            ttl: 过期时间（秒），``None`` 时使用默认 TTL（1800s）

        设计依据：ADS §10.7.2
        """
        if ttl is None:
            ttl = DEFAULT_TTL
        payload = _serialize(feature)
        await self._redis.setex(cache_key, ttl, payload)
        logger.debug(
            "L3 cache SET: key=%s, ttl=%ds, fields=%d, payload_bytes=%d",
            cache_key,
            ttl,
            len(feature),
            len(payload),
        )

    # ------------------------------------------------------------------
    # Pipeline 批量写入
    # ------------------------------------------------------------------

    async def set_many(
        self,
        items: list[tuple[str, dict[str, Any]]],
        ttl: int | None = None,
    ) -> int:
        """通过 Redis Pipeline 批量写入多个特征，减少网络往返.

        单次 Pipeline 包含 N 个 SETEX 命令，RTT 从 N 次降为 1 次。
        任一条目序列化失败不影响其他条目（记录 WARNING 后跳过）。

        Args:
            items: ``[(cache_key, feature_dict), ...]`` 列表
            ttl: 过期时间（秒），``None`` 时使用默认 TTL

        Returns:
            成功写入的条目数

        设计依据：ADS §10.7.2 Pipeline 批量写入
        """
        if not items:
            return 0

        if ttl is None:
            ttl = DEFAULT_TTL

        pipe = self._redis.pipeline()
        written = 0
        for cache_key, feature in items:
            try:
                payload = _serialize(feature)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "L3 cache set_many 序列化失败，跳过: key=%s",
                    cache_key,
                    exc_info=True,
                )
                continue
            pipe.setex(cache_key, ttl, payload)
            written += 1

        if written:
            await pipe.execute()

        logger.info(
            "L3 cache Pipeline 批量写入: total=%d, written=%d, skipped=%d",
            len(items),
            written,
            len(items) - written,
        )
        return written

    # ------------------------------------------------------------------
    # get_or_set
    # ------------------------------------------------------------------

    async def get_or_set(
        self,
        cache_key: str,
        factory: Callable[[], Awaitable[dict[str, Any]]],
        ttl: int | None = None,
    ) -> dict[str, Any]:
        """命中则返回，未命中则调用 factory 生成并写入缓存.

        Args:
            cache_key: 完整缓存 Key（由 ``build_key`` 生成）
            factory: 未命中时的异步工厂函数，返回特征值字典
            ttl: 写入时的 TTL，``None`` 时使用默认 TTL

        Returns:
            特征值字典（命中缓存或新生成）

        设计依据：ADS §10.7.2, §10.7.4
        """
        cached = await self.get(cache_key)
        if cached is not None:
            logger.info("L3 cache 命中，跳过特征计算: key=%s", cache_key)
            return cached

        logger.info("L3 cache 未命中，触发特征计算: key=%s", cache_key)
        feature = await factory()
        if feature:
            await self.set(cache_key, feature, ttl=ttl)
        return feature


# ---------------------------------------------------------------------------
# 序列化 / 反序列化（zstd 压缩 + base64，与 L1 一致）
# ---------------------------------------------------------------------------


def _serialize(feature: dict[str, Any]) -> str:
    """将特征值字典序列化为 base64 字符串（zstd 压缩）.

    链路：dict → JSON(utf-8) → zstd 压缩 → base64 → str
    """
    raw_json = json.dumps(feature, default=_json_default)
    raw_bytes = raw_json.encode("utf-8")
    compressed = _compressor_singleton().compress(raw_bytes)
    return base64.b64encode(compressed).decode("ascii")


def _deserialize(payload: str) -> dict[str, Any]:
    """从 base64 字符串反序列化为特征值字典.

    链路：str → base64 decode → zstd decompress → JSON → dict
    """
    compressed = base64.b64decode(payload)
    raw_bytes = _decompressor_singleton().decompress(compressed)
    return json.loads(raw_bytes.decode("utf-8"))


# 单例压缩器（线程安全，与 L1 实现风格一致）
_compressor_inst: zstandard.ZstdCompressor | None = None
_decompressor_inst: zstandard.ZstdDecompressor | None = None


def _compressor_singleton() -> zstandard.ZstdCompressor:
    global _compressor_inst
    if _compressor_inst is None:
        _compressor_inst = zstandard.ZstdCompressor(level=_ZSTD_LEVEL)
    return _compressor_inst


def _decompressor_singleton() -> zstandard.ZstdDecompressor:
    global _decompressor_inst
    if _decompressor_inst is None:
        _decompressor_inst = zstandard.ZstdDecompressor()
    return _decompressor_inst


def _json_default(obj: Any) -> Any:
    """JSON 序列化兜底：datetime → isoformat。"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Unserializable type: {type(obj)!r}")


# ---------------------------------------------------------------------------
# 哈希工具
# ---------------------------------------------------------------------------


def _time_window_hash(start: datetime, end: datetime) -> str:
    """计算时间窗口的短哈希（与 L1 ``time_window_hash`` 一致）."""
    raw = f"{start.isoformat()}|{end.isoformat()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


__all__ = ["L3FeatureCache"]
