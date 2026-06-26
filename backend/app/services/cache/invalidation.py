"""配置变更缓存失效模块.

在回路量程/控制类型变更、指标配置变更或预处理版本升级时，
主动删除受影响的 DataBlock 缓存，避免脏数据被复用。

失效策略（ADS §10.7.3）：
    - 回路配置变更 → 失效该回路全部 L1 DataBlock（``pdb:{loopId}:*``）
    - 指标配置变更 → 失效所有相关回路该 tagGroup 的 DataBlock
    - 配置版本递增 → 旧版本 Key 自然过期 + 主动 DEL 加速

所有失效操作通过 Redis ``SCAN + DEL`` 批量执行，避免 ``KEYS`` 阻塞 Redis。

设计依据：ADS §10.7.3, FDS §5.3.9, 数据流程图 §7.4
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# L1 DataBlock 缓存 Key 前缀（与 l1_datablock 保持一致）
_L1_PREFIX = "pdb"

# SCAN 每批返回的 Key 数量（避免单批过大阻塞 Redis）
_SCAN_COUNT = 200


class CacheInvalidator:
    """配置变更缓存失效器.

    通过 Redis SCAN + DEL 批量删除受影响的缓存 Key。
    SCAN 是非阻塞的游标式扫描，不会阻塞 Redis 主线程
    （``KEYS`` 命令会阻塞，禁止使用）。

    设计依据：ADS §10.7.3, 数据流程图 §7.4
    """

    def __init__(self, redis_client: Any) -> None:
        """初始化失效器.

        Args:
            redis_client: 异步 Redis 客户端，需支持 ``scan`` / ``delete`` 接口。
        """
        self._redis = redis_client

    async def invalidate_loop(self, loop_id: str, config_version: str | None = None) -> int:
        """回路配置变更时，删除该回路所有（旧版本）缓存.

        失效范围（ADS §10.7.3）：回路量程/控制类型变更 → 该回路全部 DataBlock。
        若指定 ``config_version``，仅删除不含该版本的旧 Key；
        未指定则删除该回路所有 Key。

        Args:
            loop_id: 回路 ID
            config_version: 新配置版本号，``None`` 时删除该回路全部缓存

        Returns:
            删除的 Key 数量

        设计依据：ADS §10.7.3, 数据流程图 §7.4
        """
        pattern = f"{_L1_PREFIX}:{loop_id}:*"
        deleted = await self._scan_and_delete(pattern, keep_version=config_version)
        logger.warning(
            "缓存失效（回路配置变更）: loop_id=%s, new_cfg_version=%s, deleted=%d",
            loop_id,
            config_version,
            deleted,
        )
        return deleted

    async def invalidate_metric_config(self, metric_code: str) -> int:
        """指标配置变更时，删除所有相关缓存.

        指标配置变更（如 mask_expression / tag_group 调整）影响所有回路
        中依赖该指标的 DataBlock。由于无法精确反查受影响回路列表，
        此处采取保守策略：清空全部 L1 DataBlock 缓存。

        Args:
            metric_code: 指标代码

        Returns:
            删除的 Key 数量

        设计依据：ADS §10.7.3
        """
        pattern = f"{_L1_PREFIX}:*"
        deleted = await self._scan_and_delete(pattern)
        logger.warning(
            "缓存失效（指标配置变更）: metric_code=%s, deleted=%d (全量清理)",
            metric_code,
            deleted,
        )
        return deleted

    async def invalidate_tag_group(
        self, loop_id: str, tag_group: str, old_quality_policy: str | None = None
    ) -> int:
        """按 tagGroup 精准失效（质量策略切换时使用）.

        失效范围（ADS §10.7.3）：``quality_policy`` 切换 →
        该回路该 tagGroup 的 DataBlock。

        Args:
            loop_id: 回路 ID
            tag_group: tagGroup 名称
            old_quality_policy: 旧质量策略，``None`` 时删除该 tagGroup 全部

        Returns:
            删除的 Key 数量

        设计依据：ADS §10.7.3
        """
        pattern = f"{_L1_PREFIX}:{loop_id}:{tag_group}:*"
        deleted = await self._scan_and_delete(pattern)
        logger.warning(
            "缓存失效（tagGroup 精准失效）: loop_id=%s, tag_group=%s, deleted=%d",
            loop_id,
            tag_group,
            deleted,
        )
        return deleted

    async def invalidate_all(self) -> int:
        """清空全部 L1 DataBlock 缓存（预处理版本升级时使用）.

        设计依据：ADS §10.7.3
        """
        deleted = await self._scan_and_delete(f"{_L1_PREFIX}:*")
        logger.warning("缓存失效（全量清理，预处理版本升级）: deleted=%d", deleted)
        return deleted

    # ------------------------------------------------------------------
    # 内部：SCAN + DEL
    # ------------------------------------------------------------------

    async def _scan_and_delete(
        self, pattern: str, keep_version: str | None = None
    ) -> int:
        """通过 SCAN 游标式扫描匹配 Key，批量 DEL.

        使用 SCAN 而非 KEYS，避免阻塞 Redis 主线程（ADS §10.7.3）。
        若指定 ``keep_version``，则保留包含该版本号的 Key（即新版本缓存），
        仅删除旧版本。

        Args:
            pattern: Key 匹配模式（glob）
            keep_version: 保留的版本号，``None`` 时全部删除

        Returns:
            删除的 Key 数量
        """
        deleted = 0
        cursor: int | str = 0
        batch: list[str] = []

        while True:
            cursor, keys = await self._scan(cursor, pattern, _SCAN_COUNT)
            if not keys:
                if cursor in (0, "0"):
                    break
                continue

            for key in keys:
                if keep_version and keep_version in str(key):
                    # 保留新版本缓存
                    continue
                batch.append(str(key))

            if batch:
                deleted += await self._delete_batch(batch)
                batch.clear()

            if cursor in (0, "0"):
                break

        return deleted

    async def _scan(
        self, cursor: int | str, pattern: str, count: int
    ) -> tuple[int | str, list[Any]]:
        """封装 Redis SCAN 调用，兼容不同 redis-py 版本的返回类型."""
        # redis-py 异步 SCAN 签名: scan(cursor, match=None, count=None)
        result = await self._redis.scan(cursor=cursor, match=pattern, count=count)
        # 返回 (next_cursor, [keys])
        if isinstance(result, tuple) and len(result) == 2:
            return result[0], list(result[1])
        # 兼容某些 mock 实现返回 list
        return 0, list(result) if result else (0, [])

    async def _delete_batch(self, keys: list[str]) -> int:
        """批量删除 Key（单次 DEL 多个 Key）."""
        if not keys:
            return 0
        # redis-py delete 支持多参数：delete(*keys)
        return await self._redis.delete(*keys)


__all__ = ["CacheInvalidator"]
