"""L1 DataBlock 缓存 — zstd 压缩 + Redis Pipeline 批量写入.

缓存预处理后的 DataBlock（按 tagGroup 分组），命中时直接组装
MetricDataBundle 返回，跳过 TDengine 查询与 8 步预处理 Pipeline。
支持分层 TTL（BASE 长周期、高频 tagGroup 短周期）与 Pipeline 批量写入。

缓存 Key 格式（ADS §10.7.1）::

    pdb:{loopId}:{tagGroup}:{startEpoch}:{endEpoch}:{samplingFreq}:{qualityPolicy}:{preVersion}:{cfgVersion}

序列化链路（兼容现有 ``decode_responses=True`` 的 Redis 客户端）::

    DataBlock → dict → JSON(utf-8) → zstd 压缩 → base64 → str → Redis

设计依据：ADS §10.7.1-10.7.2, FDS §5.3.9, PRD §8.2
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

from app.contracts.data_types import DataBlock, QualitySummary, TagGroup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 分层 TTL（秒）— 设计依据：ADS §10.7.1, PRD §8.2 热层 1 小时
# ---------------------------------------------------------------------------
# BASE 服务核心指标（准确率/稳定率/振荡率/快速率），对配置敏感，TTL 较长
# 以支撑标准任务每小时滚动复用（PRD §8.2 热层 1 小时）。
# 高频 tagGroup（OP_HF 等）服务诊断指标，数据量大但变更频繁，TTL 较短以控制内存。
DEFAULT_TTL_BASE = 3600  # 1 小时
DEFAULT_TTL_HF = 300  # 5 分钟

_TTL_BY_TAG_GROUP: dict[str, int] = {
    TagGroup.BASE.value: DEFAULT_TTL_BASE,
    TagGroup.OP_HF.value: DEFAULT_TTL_HF,
    TagGroup.PVOP_HF.value: DEFAULT_TTL_HF,
    TagGroup.MODE_HF.value: DEFAULT_TTL_HF,
    TagGroup.QUALITY_HF.value: DEFAULT_TTL_HF,
    TagGroup.CONFIG.value: DEFAULT_TTL_BASE,
}

# L1 缓存 Key 前缀（ADS §10.7.1）
_KEY_PREFIX = "pdb"

# zstd 压缩级别（1-22，3 为默认，工业时序数据重复度高，级别 3 已可达成 3~5 倍压缩）
_ZSTD_LEVEL = 3


class L1DataBlockCache:
    """L1 DataBlock 缓存（zstd 压缩 + Pipeline 批量写入）.

    职责：
        - ``get``：从 Redis 读取并 zstd 解压反序列化为 DataBlock
        - ``set``：zstd 压缩 + base64 编码后写入 Redis（单条）
        - ``set_many``：通过 Redis Pipeline 批量写入多条 DataBlock，减少网络往返
        - ``get_or_set``：命中即返回，未命中调用 factory 生成并写入

    设计依据：ADS §10.7.1-10.7.2, FDS §5.3.9
    """

    def __init__(self, redis_client: Any) -> None:
        """初始化 L1 缓存.

        Args:
            redis_client: 异步 Redis 客户端（兼容 ``decode_responses=True``），
                需支持 ``get`` / ``setex`` / ``pipeline`` / ``delete`` / ``scan`` 接口。
                通过依赖注入传入，便于测试时替换为 FakeRedis。
        """
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Key 生成
    # ------------------------------------------------------------------

    @staticmethod
    def build_key(
        loop_id: str,
        tag_group: str,
        time_window_start: datetime,
        time_window_end: datetime,
        sampling_freq: str,
        quality_policy: str,
        pre_version: str,
        cfg_version: str,
    ) -> str:
        """生成 L1 缓存 Key.

        Key 格式（ADS §10.7.1）::

            pdb:{loopId}:{tagGroup}:{startEpoch}:{endEpoch}:{freq}:{qualityPolicy}:{preVer}:{cfgVer}

        时间窗口通过 epoch 整数纳入 Key，确保相同窗口的请求命中同一 Key。

        设计依据：ADS §10.7.1
        """
        start_epoch = int(time_window_start.timestamp())
        end_epoch = int(time_window_end.timestamp())
        return (
            f"{_KEY_PREFIX}:{loop_id}:{tag_group}:"
            f"{start_epoch}:{end_epoch}:"
            f"{sampling_freq}:{quality_policy}:{pre_version}:{cfg_version}"
        )

    @staticmethod
    def get_ttl(tag_group: str) -> int:
        """获取 tagGroup 对应的分层 TTL（秒）.

        设计依据：ADS §10.7.1, PRD §8.2
        """
        return _TTL_BY_TAG_GROUP.get(tag_group, DEFAULT_TTL_BASE)

    # ------------------------------------------------------------------
    # 单条读写
    # ------------------------------------------------------------------

    async def get(self, data_block_id: str) -> DataBlock | None:
        """从 Redis 获取 DataBlock（zstd 解压 + 反序列化）.

        Args:
            data_block_id: 完整的缓存 Key（由 ``build_key`` 生成）

        Returns:
            DataBlock 实例；未命中返回 ``None``

        设计依据：ADS §10.7.2
        """
        raw: str | None = await self._redis.get(data_block_id)
        if raw is None:
            logger.debug("L1 cache MISS: key=%s", data_block_id)
            return None
        try:
            data_block = _deserialize(raw)
        except Exception:  # noqa: BLE001
            logger.warning(
                "L1 cache 反序列化失败，丢弃脏数据: key=%s", data_block_id, exc_info=True
            )
            await self._redis.delete(data_block_id)
            return None
        logger.debug(
            "L1 cache HIT: key=%s, points=%d, tagGroup=%s",
            data_block_id,
            data_block.point_count,
            data_block.tag_group,
        )
        return data_block

    async def set(
        self,
        data_block: DataBlock,
        ttl: int | None = None,
        key: str | None = None,
    ) -> None:
        """写入 Redis（zstd 压缩 + base64 编码 + SETEX）.

        Args:
            data_block: 待缓存的 DataBlock
            ttl: 过期时间（秒），``None`` 时按 tag_group 自动选择分层 TTL
            key: 缓存 Key，``None`` 时由 ``build_key_from_block`` 推导。
                ``get_or_set`` 调用时传入外部 Key，确保 get/set Key 一致。

        设计依据：ADS §10.7.2
        """
        cache_key = key or self.build_key_from_block(data_block)
        if ttl is None:
            ttl = self.get_ttl(data_block.tag_group)
        payload = _serialize(data_block)
        await self._redis.setex(cache_key, ttl, payload)
        ratio = _compression_ratio(data_block, payload)
        logger.debug(
            "L1 cache SET: key=%s, ttl=%ds, compressed_bytes=%d, ratio=%.1f%%",
            cache_key,
            ttl,
            len(payload),
            ratio,
        )

    # ------------------------------------------------------------------
    # Pipeline 批量写入
    # ------------------------------------------------------------------

    async def set_many(
        self,
        data_blocks: list[DataBlock],
        ttl: int | None = None,
        keys: list[str] | None = None,
    ) -> int:
        """通过 Redis Pipeline 批量写入多个 DataBlock，减少网络往返.

        单次 Pipeline 包含 N 个 SETEX 命令，RTT 从 N 次降为 1 次。
        任一条目序列化失败不影响其他条目（记录 WARNING 后跳过）。

        Args:
            data_blocks: DataBlock 列表
            ttl: 过期时间（秒），``None`` 时各条目按自身 tag_group 选择分层 TTL
            keys: 与 data_blocks 平行的缓存 Key 列表；``None`` 时由
                ``build_key_from_block`` 推导。DataPlanner 传入显式 Key
                以确保与 get 使用的 Key 一致。

        Returns:
            成功写入的条目数

        设计依据：ADS §10.7.2 Pipeline 批量写入
        """
        if not data_blocks:
            return 0

        pipe = self._redis.pipeline()
        written = 0
        for idx, block in enumerate(data_blocks):
            try:
                payload = _serialize(block)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "L1 cache set_many 序列化失败，跳过: block_id=%s",
                    block.data_block_id,
                    exc_info=True,
                )
                continue
            key = keys[idx] if keys is not None else self.build_key_from_block(block)
            block_ttl = ttl if ttl is not None else self.get_ttl(block.tag_group)
            pipe.setex(key, block_ttl, payload)
            written += 1

        if written:
            await pipe.execute()

        logger.info(
            "L1 cache Pipeline 批量写入: total=%d, written=%d, skipped=%d",
            len(data_blocks),
            written,
            len(data_blocks) - written,
        )
        return written

    # ------------------------------------------------------------------
    # get_or_set
    # ------------------------------------------------------------------

    async def get_or_set(
        self,
        data_block_id: str,
        factory: Callable[[], Awaitable[DataBlock]],
        ttl: int | None = None,
    ) -> DataBlock:
        """命中则返回，未命中则调用 factory 生成并写入缓存.

        Args:
            data_block_id: 完整缓存 Key（由 ``build_key`` 生成）
            factory: 未命中时的异步工厂函数，返回 DataBlock
            ttl: 写入时的 TTL，``None`` 时按 DataBlock 的 tag_group 自动选择

        Returns:
            DataBlock 实例（命中缓存或新生成）

        设计依据：ADS §10.7.2, 数据流程图 §7.1 Phase 4-7
        """
        cached = await self.get(data_block_id)
        if cached is not None:
            logger.info("L1 cache 命中，跳过 TDengine 查询+预处理: key=%s", data_block_id)
            return cached

        logger.info("L1 cache 未命中，触发回源: key=%s", data_block_id)
        data_block = await factory()
        # 传入显式 Key，确保与 get 使用的 Key 一致
        await self.set(data_block, ttl=ttl, key=data_block_id)
        return data_block

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @classmethod
    def build_key_from_block(cls, data_block: DataBlock) -> str:
        """从 DataBlock 实例推导缓存 Key（需 timestamps 非空）.

        设计依据：ADS §10.7.1
        """
        if data_block.timestamps:
            start = data_block.timestamps[0]
            end = data_block.timestamps[-1]
        else:
            # 无时间戳时用 epoch 0 占位（理论上不会发生）
            start = end = datetime.fromtimestamp(0)
        return cls.build_key(
            loop_id=data_block.loop_id,
            tag_group=data_block.tag_group,
            time_window_start=start,
            time_window_end=end,
            sampling_freq=data_block.sampling_freq,
            quality_policy=_infer_quality_policy(data_block),
            pre_version=data_block.preprocess_version,
            cfg_version=data_block.config_version,
        )


# ---------------------------------------------------------------------------
# 序列化 / 反序列化（zstd 压缩 + base64）
# ---------------------------------------------------------------------------


def _serialize(data_block: DataBlock) -> str:
    """将 DataBlock 序列化为 base64 字符串（zstd 压缩）.

    链路：DataBlock → dict → JSON(utf-8) → zstd → base64 → str
    """
    raw_json = json.dumps(_data_block_to_dict(data_block), default=_json_default)
    raw_bytes = raw_json.encode("utf-8")
    compressed = _compressor_singleton().compress(raw_bytes)
    return base64.b64encode(compressed).decode("ascii")


def _deserialize(payload: str) -> DataBlock:
    """从 base64 字符串反序列化为 DataBlock.

    链路：str → base64 decode → zstd decompress → JSON → dict → DataBlock
    """
    compressed = base64.b64decode(payload)
    raw_bytes = _decompressor_singleton().decompress(compressed)
    data = json.loads(raw_bytes.decode("utf-8"))
    return _data_block_from_dict(data)


# 单例压缩器（线程安全，避免重复创建）
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


def _data_block_to_dict(block: DataBlock) -> dict[str, Any]:
    """DataBlock → 可 JSON 序列化的 dict."""
    return {
        "data_block_id": block.data_block_id,
        "loop_id": block.loop_id,
        "tag_group": block.tag_group,
        "sampling_freq": block.sampling_freq,
        "timestamps": [ts.isoformat() for ts in block.timestamps],
        "signals": block.signals,
        "validity": block.validity,
        "outlier_reasons": block.outlier_reasons,
        "quality_summary": _quality_summary_to_dict(block.quality_summary),
        "consecutive_segments": [
            [s, e] for s, e in block.consecutive_segments
        ],
        "config_version": block.config_version,
        "preprocess_version": block.preprocess_version,
        "point_count": block.point_count,
    }


def _quality_summary_to_dict(qs: QualitySummary) -> dict[str, Any]:
    return {
        "total_count": qs.total_count,
        "valid_count": qs.valid_count,
        "bad_count": qs.bad_count,
        "missing_count": qs.missing_count,
        "valid_rate": qs.valid_rate,
        "bad_rate": qs.bad_rate,
        "missing_rate": qs.missing_rate,
        "good_value_rate": qs.good_value_rate,
    }


def _data_block_from_dict(data: dict[str, Any]) -> DataBlock:
    """dict → DataBlock（反序列化）."""
    timestamps = [datetime.fromisoformat(ts) for ts in data["timestamps"]]
    quality_summary = QualitySummary(**_quality_summary_from_dict(data["quality_summary"]))
    consecutive_segments = [
        (int(s), int(e)) for s, e in data.get("consecutive_segments", [])
    ]
    return DataBlock(
        data_block_id=data["data_block_id"],
        loop_id=data["loop_id"],
        tag_group=data["tag_group"],
        sampling_freq=data["sampling_freq"],
        timestamps=timestamps,
        signals=data["signals"],
        validity=data["validity"],
        outlier_reasons=data.get("outlier_reasons", {}),
        quality_summary=quality_summary,
        consecutive_segments=consecutive_segments,
        config_version=data.get("config_version", "v1"),
        preprocess_version=data.get("preprocess_version", "pre_v1"),
        point_count=data.get("point_count", len(timestamps)),
    )


def _quality_summary_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_count": data.get("total_count", 0),
        "valid_count": data.get("valid_count", 0),
        "bad_count": data.get("bad_count", 0),
        "missing_count": data.get("missing_count", 0),
        "valid_rate": data.get("valid_rate", 0.0),
        "bad_rate": data.get("bad_rate", 0.0),
        "missing_rate": data.get("missing_rate", 0.0),
        "good_value_rate": data.get("good_value_rate"),
    }


def _infer_quality_policy(data_block: DataBlock) -> str:
    """根据 DataBlock 推断质量策略标签.

    QUALITY_HF tagGroup 使用 KEEP_ALL（好值率不删除行），
    其余默认 KEEP_ALL_WITH_VALIDITY。
    """
    if data_block.tag_group == TagGroup.QUALITY_HF.value:
        return "KEEP_ALL"
    return "KEEP_ALL_WITH_VALIDITY"


def _compression_ratio(data_block: DataBlock, payload: str) -> float:
    """计算压缩率（压缩后 / 压缩前 × 100%）."""
    raw_json = json.dumps(_data_block_to_dict(data_block), default=_json_default)
    raw_bytes = len(raw_json.encode("utf-8"))
    if raw_bytes == 0:
        return 0.0
    return len(payload) / raw_bytes * 100.0


def compute_compression_ratio(data_block: DataBlock) -> float:
    """计算给定 DataBlock 的 zstd+base64 压缩率（百分比）.

    用于测试与监控。返回值越小表示压缩效果越好（如 25 表示压缩到 25%，即 75% 压缩率）。
    """
    payload = _serialize(data_block)
    return _compression_ratio(data_block, payload)


def time_window_hash(start: datetime, end: datetime) -> str:
    """计算时间窗口的短哈希（用于 Key 或日志标识）."""
    raw = f"{start.isoformat()}|{end.isoformat()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


__all__ = [
    "L1DataBlockCache",
    "compute_compression_ratio",
    "time_window_hash",
]
