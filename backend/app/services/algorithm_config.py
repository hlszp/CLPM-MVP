"""指标算法参数配置服务（P0-B 配置化基础设施）.

三层配置合并链：
    1. 算法默认值（``_DEFAULTS``，与计算器硬编码常量一致）
    2. ``algorithm_parameter`` 表（系统级默认覆盖，按 control_type 分组）
    3. ``metric_config.threshold`` JSONB（指标级覆盖，已有字段复用）

热路径（指标计算器）通过 ``get_algorithm_params()`` 读取合并后的进程内缓存，
不查库。配置保存后通过 ``apply_runtime()`` 刷新缓存。

设计依据：HiaMonitor 借鉴重构计划评审报告 P0-B, P0-3, P1-2
复用模式：参照 ``app.services.preprocessing.outlier_params`` 的存储+缓存+应用三段式
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.algorithm_parameter import AlgorithmParameter
from app.models.metric import MetricConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 算法默认参数（与计算器内硬编码常量一致，作为配置链最底层回退）
# ---------------------------------------------------------------------------

#: 每个指标在每个控制类型下的算法默认参数
#: key = metric_code, value = {control_type: {param_name: value}}
#: 注意：默认值与计算器内硬编码常量一致，确保未配置时行为不变（behavior-preserving）。
_DEFAULTS: dict[str, dict[str, dict[str, Any]]] = {
    "oscillation_rate": {
        "STABLE": {"similarity_threshold": 0.4, "min_ratio": 0.05, "max_ratio": 15.0},
        "SLOW": {"similarity_threshold": 0.4, "min_ratio": 0.05, "max_ratio": 15.0},
        "FAST": {"similarity_threshold": 0.4, "min_ratio": 0.05, "max_ratio": 15.0},
        "LOGIC": {"similarity_threshold": 0.4, "min_ratio": 0.05, "max_ratio": 15.0},
    },
    "fast_rate": {
        # settling_tolerance=0.0 + ideal_settling_ratio=1.0
        # → 阈值=ideal_t，与原 actual_t<=ideal_t 一致
        "STABLE": {"ideal_settling_ratio": 1.0, "settling_tolerance": 0.0},
        "SLOW": {"ideal_settling_ratio": 1.0, "settling_tolerance": 0.0},
        "FAST": {"ideal_settling_ratio": 1.0, "settling_tolerance": 0.0},
        "LOGIC": {"ideal_settling_ratio": 1.0, "settling_tolerance": 0.0},
    },
    "accuracy_rate": {
        # e_max_percentile=100 → 不对数据驱动 e_max 做百分位截断，与原算法一致
        "STABLE": {"e_max_percentile": 100},
        "SLOW": {"e_max_percentile": 100},
        "FAST": {"e_max_percentile": 100},
        "LOGIC": {"e_max_percentile": 100},
    },
}

#: 支持的控制类型
_CONTROL_TYPES = ("STABLE", "SLOW", "FAST", "LOGIC")


def get_default_params(metric_code: str, control_type: str) -> dict[str, Any]:
    """获取算法默认参数（不含任何覆盖）."""
    return dict(_DEFAULTS.get(metric_code, {}).get(control_type, {}))


# ---------------------------------------------------------------------------
# 运行时合并缓存（热路径读取）
# ---------------------------------------------------------------------------

#: 合并后的参数缓存 key=(metric_code, control_type) value=dict
_merged_cache: dict[tuple[str, str], dict[str, Any]] = {}


def _rebuild_merged(
    table_overrides: dict[str, dict[str, dict[str, Any]]],
    metric_thresholds: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """合并三层配置：默认值 + algorithm_parameter 表覆盖 + metric_config.threshold 覆盖.

    Args:
        table_overrides: {metric_code: {control_type: {param: value}}}
        metric_thresholds: {metric_code: {param: value}}（指标级覆盖，不区分控制类型）

    Returns:
        {(metric_code, control_type): {param: value}}
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for metric_code, ct_map in _DEFAULTS.items():
        for ct in _CONTROL_TYPES:
            # Layer 1: 算法默认值
            params = dict(ct_map.get(ct, {}))
            # Layer 2: algorithm_parameter 表覆盖
            table_params = table_overrides.get(metric_code, {}).get(ct, {})
            params.update(table_params)
            # Layer 3: metric_config.threshold 指标级覆盖（不区分控制类型）
            mc_params = metric_thresholds.get(metric_code, {})
            params.update(mc_params)
            merged[(metric_code, ct)] = params
    return merged


def get_algorithm_params(metric_code: str, control_type: str | None) -> dict[str, Any]:
    """热路径读取：返回合并后的算法参数.

    Args:
        metric_code: 指标代码，如 ``"oscillation_rate"``
        control_type: 控制类型（STABLE/SLOW/FAST/LOGIC）；None 时回退 STABLE

    Returns:
        合并后的参数字典；未知指标/控制类型返回空字典
    """
    ct = control_type if control_type in _CONTROL_TYPES else "STABLE"
    return dict(_merged_cache.get((metric_code, ct), {}))


# ---------------------------------------------------------------------------
# DB 加载与运行时应用
# ---------------------------------------------------------------------------


async def load_stored_config(db: AsyncSession) -> dict[str, Any]:
    """从 algorithm_parameter 表加载全部配置.

    Returns:
        ``{metric_code: {control_type: {param: value}}}`` 字典
    """
    result = await db.execute(
        select(AlgorithmParameter).where(AlgorithmParameter.is_enabled.is_(True))
    )
    rows = result.scalars().all()

    stored: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        stored.setdefault(row.metric_code, {})[row.control_type] = dict(row.params or {})
    return stored


async def load_metric_thresholds(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """从 metric_config.threshold JSONB 加载指标级覆盖.

    ``metric_config.threshold`` 原为阈值配置字段，P0-B 复用为算法参数覆盖。
    仅加载与 ``_DEFAULTS`` 中已知 metric_code 相关的行。

    Returns:
        ``{metric_code: {param: value}}`` 字典
    """
    known_metrics = tuple(_DEFAULTS.keys())
    result = await db.execute(
        select(MetricConfig.metric_code, MetricConfig.threshold).where(
            MetricConfig.metric_code.in_(known_metrics),
            MetricConfig.threshold.is_not(None),
        )
    )
    thresholds: dict[str, dict[str, Any]] = {}
    for row in result.all():
        mc = row.metric_code
        if row.threshold and isinstance(row.threshold, dict):
            thresholds[mc] = dict(row.threshold)
    return thresholds


async def preload_algorithm_params(db: AsyncSession) -> None:
    """lifespan + worker_process_init 预载：从 DB 加载并合并到进程内缓存."""
    global _merged_cache
    table_overrides = await load_stored_config(db)
    metric_thresholds = await load_metric_thresholds(db)
    _merged_cache = _rebuild_merged(table_overrides, metric_thresholds)
    logger.info(
        "算法参数配置已预载: metrics=%s, table_overridden=%s, metric_thresholds=%s",
        sorted(_DEFAULTS.keys()),
        {k: sorted(v.keys()) for k, v in table_overrides.items()},
        sorted(metric_thresholds.keys()),
    )


def apply_runtime(
    table_overrides: dict[str, dict[str, dict[str, Any]]],
    metric_thresholds: dict[str, dict[str, Any]] | None = None,
) -> None:
    """配置保存后刷新运行时缓存（不查库）.

    Args:
        table_overrides: 从 ``algorithm_parameter`` 表加载的覆盖
        metric_thresholds: 从 ``metric_config.threshold`` 加载的覆盖（可选）
    """
    global _merged_cache
    _merged_cache = _rebuild_merged(table_overrides, metric_thresholds or {})
    logger.info(
        "算法参数运行时缓存已刷新: %d 个 (metric, control_type) 组合",
        len(_merged_cache),
    )


def build_merged_view() -> dict[str, Any]:
    """构建 API 返回的合并视图（含默认值 + 覆盖标记）.

    Returns:
        ``{metric_code: {control_type: {params: {...}, defaults: {...}, overridden: bool}}}``
    """
    view: dict[str, Any] = {}
    for metric_code, ct_map in _DEFAULTS.items():
        view[metric_code] = {}
        for ct in _CONTROL_TYPES:
            defaults = dict(ct_map.get(ct, {}))
            merged = dict(_merged_cache.get((metric_code, ct), defaults))
            overridden = merged != defaults
            view[metric_code][ct] = {
                "params": merged,
                "defaults": defaults,
                "overridden": overridden,
            }
    return view


def _now_naive() -> datetime:
    """当前 UTC naive datetime."""
    return datetime.now(UTC).replace(tzinfo=None)
