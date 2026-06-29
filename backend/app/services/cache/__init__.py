"""DataBlock 缓存模块.

提供三层缓存能力与配置变更失效：
    - L1 DataBlock 缓存（zstd 压缩 + Pipeline 批量写入）：缓存预处理后的 DataBlock
    - L2 MetricDataBundle 缓存：缓存组装后的 Bundle，避免同批次重复组装
    - L3 特征缓存：缓存中间计算特征值（ARMA 参数、IAE 累积值等），避免重复计算
    - 配置变更失效：回路/指标/预处理版本变更时主动删除受影响缓存

DataPlanner 通过 L1DataBlockCache 复用已预处理的标准化数据块，
通过 L2BundleCache 跳过 Bundle 组装，通过 L3FeatureCache 复用中间特征值。

设计依据：ADS §10.7, FDS §5.3.9, PRD §8.2
"""

from app.services.cache.invalidation import CacheInvalidator
from app.services.cache.l1_datablock import L1DataBlockCache
from app.services.cache.l2_bundle import L2BundleCache
from app.services.cache.l3_feature import L3FeatureCache

__all__ = [
    "L1DataBlockCache",
    "L2BundleCache",
    "L3FeatureCache",
    "CacheInvalidator",
]
