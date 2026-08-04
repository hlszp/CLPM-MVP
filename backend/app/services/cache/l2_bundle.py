"""L2 MetricDataBundle 缓存 — 组装去重 + zstd 压缩.

缓存 DataPlanner 组装后的 MetricDataBundle 列表，命中时跳过
``MetricDataBundleAssembler.assemble`` 步骤，避免同批次内重复组装。
仅在 ``batch_calc_metrics`` 等组合调用场景下使用，TTL 短于 L1
（Bundle 组装轻量，TTL=600s）。

缓存 Key 格式（任务规范）::

    pdb_l2:{loopId}:{metrics_hash}:{window_hash}:{control_type}:{op_limits_hash}:{cfg_version}

序列化链路（与 L1 一致，兼容 ``decode_responses=True`` 的 Redis 客户端）::

    list[MetricDataBundle] → dict → JSON(utf-8) → zstd 压缩 → base64 → str → Redis

设计依据：ADS §10.7.1, FDS §5.3.9, 数据流程图 §7.1 Phase 8
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import zstandard

from app.contracts.data_types import (
    DataBlock,
    DataLineage,
    MetricDataBundle,
    QualitySummary,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL（秒）— 设计依据：ADS §10.7.1（Bundle 组装轻量，TTL 短于 L1）
# ---------------------------------------------------------------------------
DEFAULT_TTL = 600  # 10 分钟

# L2 缓存 Key 前缀（任务规范）
_KEY_PREFIX = "pdb_l2"

# zstd 压缩级别（与 L1 一致）
_ZSTD_LEVEL = 3


class L2BundleCache:
    """L2 MetricDataBundle 缓存（zstd 压缩 + Pipeline 批量写入）.

    职责：
        - ``get``：从 Redis 读取并反序列化为 ``list[MetricDataBundle]``
        - ``set``：zstd 压缩 + base64 编码后写入 Redis
        - ``get_or_set``：命中即返回，未命中调用 factory 生成并写入

    设计依据：ADS §10.7.1, FDS §5.3.9, 数据流程图 §7.1 Phase 8
    """

    def __init__(self, redis_client: Any) -> None:
        """初始化 L2 缓存.

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
        metrics: list[str],
        time_window_start: datetime,
        time_window_end: datetime,
        control_type: str,
        op_output_lower_limit: float | None = None,
        op_output_upper_limit: float | None = None,
        cfg_version: str = "v1",
    ) -> str:
        """生成 L2 缓存 Key.

        Key 格式（任务规范）::

            pdb_l2:{loopId}:{metrics_hash}:{window_hash}:{control_type}:{op_limits_hash}:{cfg_version}

        - ``metrics_hash``：对 metrics 列表排序后取 MD5 前 8 位，
          确保相同指标集合（无论顺序）命中同一 Key
        - ``window_hash``：时间窗口短哈希（与 L1 ``time_window_hash`` 一致）
        - ``op_limits_hash``：OP 输出限位哈希（用于区分不同限位配置的缓存）
        - ``cfg_version``：配置版本（与 L1 同一 ``cfg_{updated_at}`` 口径），
          量程变更/tag 重关联/契约变更后旧 bundle 不再命中，
          不必等待 TTL（600s）自然过期

        设计依据：ADS §10.7.1, 任务规范, loop-range-and-output-limits-design-v1.0.md §4.3
        """
        metrics_hash = _metrics_hash(metrics)
        window_hash = _time_window_hash(time_window_start, time_window_end)
        op_limits_hash = _op_limits_hash(op_output_lower_limit, op_output_upper_limit)
        return (
            f"{_KEY_PREFIX}:{loop_id}:{metrics_hash}:{window_hash}:"
            f"{control_type}:{op_limits_hash}:{cfg_version}"
        )

    # ------------------------------------------------------------------
    # 单条读写
    # ------------------------------------------------------------------

    async def get(self, cache_key: str) -> list[MetricDataBundle] | None:
        """从 Redis 获取 MetricDataBundle 列表（zstd 解压 + 反序列化）.

        Args:
            cache_key: 完整的缓存 Key（由 ``build_key`` 生成）

        Returns:
            MetricDataBundle 列表；未命中返回 ``None``

        设计依据：ADS §10.7.2
        """
        raw: str | None = await self._redis.get(cache_key)
        if raw is None:
            logger.debug("L2 cache MISS: key=%s", cache_key)
            return None
        try:
            # 反序列化（zstd 解压 + JSON 解析）移至线程池
            bundles = await asyncio.to_thread(_deserialize, raw)
        except Exception:  # noqa: BLE001
            logger.warning("L2 cache 反序列化失败，丢弃脏数据: key=%s", cache_key, exc_info=True)
            await self._redis.delete(cache_key)
            return None
        logger.debug(
            "L2 cache HIT: key=%s, bundles=%d",
            cache_key,
            len(bundles),
        )
        return bundles

    async def set(
        self,
        cache_key: str,
        bundles: list[MetricDataBundle],
        ttl: int | None = None,
    ) -> None:
        """写入 Redis（zstd 压缩 + base64 编码 + SETEX）.

        Args:
            cache_key: 完整缓存 Key（由 ``build_key`` 生成）
            bundles: 待缓存的 MetricDataBundle 列表
            ttl: 过期时间（秒），``None`` 时使用默认 TTL（600s）

        设计依据：ADS §10.7.2
        """
        if ttl is None:
            ttl = DEFAULT_TTL
        # 序列化（JSON + zstd 压缩）移至线程池，避免 CPU 密集型操作阻塞事件循环
        payload = await asyncio.to_thread(_serialize, bundles)
        await self._redis.setex(cache_key, ttl, payload)
        logger.debug(
            "L2 cache SET: key=%s, ttl=%ds, bundles=%d, payload_bytes=%d",
            cache_key,
            ttl,
            len(bundles),
            len(payload),
        )

    # ------------------------------------------------------------------
    # get_or_set
    # ------------------------------------------------------------------

    async def get_or_set(
        self,
        cache_key: str,
        factory: Callable[[], Awaitable[list[MetricDataBundle]]],
        ttl: int | None = None,
    ) -> list[MetricDataBundle]:
        """命中则返回，未命中则调用 factory 生成并写入缓存.

        Args:
            cache_key: 完整缓存 Key（由 ``build_key`` 生成）
            factory: 未命中时的异步工厂函数，返回 MetricDataBundle 列表
            ttl: 写入时的 TTL，``None`` 时使用默认 TTL

        Returns:
            MetricDataBundle 列表（命中缓存或新生成）

        设计依据：ADS §10.7.2, 数据流程图 §7.1 Phase 8
        """
        cached = await self.get(cache_key)
        if cached is not None:
            logger.info("L2 cache 命中，跳过 Bundle 组装: key=%s", cache_key)
            return cached

        logger.info("L2 cache 未命中，触发组装: key=%s", cache_key)
        bundles = await factory()
        if bundles:
            await self.set(cache_key, bundles, ttl=ttl)
        return bundles


# ---------------------------------------------------------------------------
# 序列化 / 反序列化（zstd 压缩 + base64，与 L1 一致）
# ---------------------------------------------------------------------------


def _serialize(bundles: list[MetricDataBundle]) -> str:
    """将 MetricDataBundle 列表序列化为 base64 字符串（zstd 压缩）.

    链路：list[MetricDataBundle] → dict → JSON(utf-8) → zstd → base64 → str
    """
    raw_json = json.dumps(
        {"bundles": [_bundle_to_dict(b) for b in bundles]},
        default=_json_default,
    )
    raw_bytes = raw_json.encode("utf-8")
    compressed = _compressor_singleton().compress(raw_bytes)
    return base64.b64encode(compressed).decode("ascii")


def _deserialize(payload: str) -> list[MetricDataBundle]:
    """从 base64 字符串反序列化为 MetricDataBundle 列表.

    链路：str → base64 decode → zstd decompress → JSON → dict → list[MetricDataBundle]
    """
    compressed = base64.b64decode(payload)
    raw_bytes = _decompressor_singleton().decompress(compressed)
    data = json.loads(raw_bytes.decode("utf-8"))
    bundles_data = data.get("bundles", []) if isinstance(data, dict) else data
    return [_bundle_from_dict(b) for b in bundles_data]


# 线程局部压缩器（asyncio.to_thread 多线程安全）
_zstd_local = threading.local()


def _compressor_singleton() -> zstandard.ZstdCompressor:
    """获取线程局部的 ZstdCompressor 实例（asyncio.to_thread 安全）."""
    c = getattr(_zstd_local, "compressor", None)
    if c is None:
        c = zstandard.ZstdCompressor(level=_ZSTD_LEVEL)
        _zstd_local.compressor = c
    return c


def _decompressor_singleton() -> zstandard.ZstdDecompressor:
    """获取线程局部的 ZstdDecompressor 实例（asyncio.to_thread 安全）."""
    d = getattr(_zstd_local, "decompressor", None)
    if d is None:
        d = zstandard.ZstdDecompressor()
        _zstd_local.decompressor = d
    return d


def _json_default(obj: Any) -> Any:
    """JSON 序列化兜底：datetime → isoformat。"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Unserializable type: {type(obj)!r}")


# ---------------------------------------------------------------------------
# MetricDataBundle ↔ dict
# ---------------------------------------------------------------------------


def _bundle_to_dict(bundle: MetricDataBundle) -> dict[str, Any]:
    """MetricDataBundle → 可 JSON 序列化的 dict."""
    return {
        "metric_code": bundle.metric_code,
        "data_block": _data_block_to_dict(bundle.data_block),
        "mask_expression": bundle.mask_expression,
        "masked_indices": list(bundle.masked_indices),
        "lineage": _lineage_to_dict(bundle.lineage),
    }


def _bundle_from_dict(data: dict[str, Any]) -> MetricDataBundle:
    """dict → MetricDataBundle（反序列化）."""
    return MetricDataBundle(
        metric_code=data["metric_code"],
        data_block=_data_block_from_dict(data["data_block"]),
        mask_expression=data.get("mask_expression", ""),
        masked_indices=list(data.get("masked_indices", [])),
        lineage=_lineage_from_dict(data.get("lineage", {})),
    )


def _data_block_to_dict(block: DataBlock) -> dict[str, Any]:
    """DataBlock → 可 JSON 序列化的 dict（与 L1 一致）."""
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
        "consecutive_segments": [[s, e] for s, e in block.consecutive_segments],
        "config_version": block.config_version,
        "preprocess_version": block.preprocess_version,
        "point_count": block.point_count,
        # 可信度统一 Phase 2：回路级可信度字段必须纳入序列化（与 L1 一致）
        "loop_confidence_level": block.loop_confidence_level,
        "loop_valid_rate": block.loop_valid_rate,
    }


def _data_block_from_dict(data: dict[str, Any]) -> DataBlock:
    """dict → DataBlock（反序列化，与 L1 一致）.

    旧缓存兼容：``loop_confidence_level`` / ``loop_valid_rate`` 缺失时
    从 validity 重算，避免使用默认值 "E"/0.0。
    """
    timestamps = [datetime.fromisoformat(ts) for ts in data["timestamps"]]
    quality_summary = QualitySummary(**_quality_summary_from_dict(data["quality_summary"]))
    consecutive_segments = [(int(s), int(e)) for s, e in data.get("consecutive_segments", [])]

    # 回路级可信度：优先读缓存值，旧缓存缺失时从 validity 重算
    loop_confidence_level = data.get("loop_confidence_level")
    loop_valid_rate = data.get("loop_valid_rate")
    if loop_confidence_level is None or loop_valid_rate is None:
        from app.services.confidence_evaluator import ConfidenceEvaluator
        from app.services.preprocessing.data_quality_assessor import DataQualityAssessor

        n = data.get("point_count", len(timestamps))
        validity = data.get("validity", {})
        loop_valid_rate = DataQualityAssessor.compute_loop_valid_rate(validity, n)
        loop_confidence_level = ConfidenceEvaluator.evaluate(loop_valid_rate).value
        logger.info(
            "L2 cache 旧缓存兼容：重算回路级可信度: loop_valid_rate=%.4f, level=%s",
            loop_valid_rate,
            loop_confidence_level,
        )

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
        loop_confidence_level=loop_confidence_level,
        loop_valid_rate=loop_valid_rate,
    )


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


def _lineage_to_dict(lineage: DataLineage) -> dict[str, Any]:
    """DataLineage → dict（含 to_dict 兜底）."""
    if hasattr(lineage, "to_dict"):
        return lineage.to_dict()
    return {
        "sampling_freq": lineage.sampling_freq,
        "aggregation_policy": lineage.aggregation_policy,
        "quality_policy": lineage.quality_policy,
        "tag_group": lineage.tag_group,
        "data_block_ids": list(lineage.data_block_ids),
        "valid_rate": lineage.valid_rate,
        "data_policy_version": lineage.data_policy_version,
        "algorithm_version": lineage.algorithm_version,
    }


def _lineage_from_dict(data: dict[str, Any]) -> DataLineage:
    """dict → DataLineage."""
    return DataLineage(
        sampling_freq=data.get("sampling_freq", ""),
        aggregation_policy=data.get("aggregation_policy", ""),
        quality_policy=data.get("quality_policy", ""),
        tag_group=data.get("tag_group", ""),
        data_block_ids=list(data.get("data_block_ids", [])),
        valid_rate=data.get("valid_rate", 0.0),
        data_policy_version=data.get("data_policy_version", "pre_v1"),
        algorithm_version=data.get("algorithm_version", "KPI_CALC_v2.0"),
    )


# ---------------------------------------------------------------------------
# 哈希工具
# ---------------------------------------------------------------------------


def _metrics_hash(metrics: list[str]) -> str:
    """对指标列表排序后计算 MD5 短哈希（8 位）.

    排序确保不同顺序的相同指标集合命中同一 Key。
    """
    sorted_metrics = sorted(metrics)
    raw = "|".join(sorted_metrics)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def _time_window_hash(start: datetime, end: datetime) -> str:
    """计算时间窗口的短哈希（与 L1 ``time_window_hash`` 一致）."""
    raw = f"{start.isoformat()}|{end.isoformat()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def _op_limits_hash(lower: float | None, upper: float | None) -> str:
    """计算 OP 输出限位的短哈希（8 位）.

    用于 L2 缓存 Key，确保不同限位配置生成不同的缓存条目。
    修复问题：修改 OP 输出限位后，L2 缓存仍返回旧限位数据。
    """
    raw = f"{lower}|{upper}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


__all__ = ["L2BundleCache"]
