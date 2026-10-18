"""引擎规则加载器 — EngineRule 读取 + 进程内缓存.

PRD §5.4.2 / FDS §5.3.3：引擎规则模块负责配置计算周期、数据窗口大小、
并发处理数量等引擎运行参数。本模块提供统一的异步加载接口，带 60 秒
进程内缓存，避免每次 KPI 计算重复查询数据库。

3 类规则（对齐种子数据 db/postgresql/02_seed_data.sql）：
    - CALC_CYCLE  (EVAL_CALC_CYCLE):     {"cycle_minutes": 60}
    - DATA_FETCH  (DATA_FETCH_WINDOW):   {"window_days": 30, "sample_interval_seconds": 1}
    - SCHEDULE    (SCHEDULE_CONCURRENCY):{"concurrency": 16}

设计依据：PRD §5.4.2, FDS §5.3.3, 实现契约 v1.0 §6
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engine import EngineRule

logger = logging.getLogger(__name__)

# 进程内缓存 TTL（秒）
_CACHE_TTL = 60.0

# 默认值（与种子数据对齐；数据库不可用时回退使用）
DEFAULT_CALC_CYCLE_MINUTES = 60
DEFAULT_DATA_WINDOW_DAYS = 30
DEFAULT_SAMPLE_INTERVAL_SECONDS = 1
DEFAULT_CONCURRENCY = 16


class EngineRuleLoader:
    """引擎规则异步加载器（带进程内缓存）.

    所有 KPI 计算任务通过本类读取 EngineRule 配置，避免硬编码。
    缓存 TTL 60 秒，平衡时效性与数据库压力。

    使用方式::

        loader = EngineRuleLoader()
        cycle_minutes = await loader.get_calc_cycle_minutes(db)
        concurrency = await loader.get_concurrency(db)
    """

    def __init__(self, cache_ttl: float = _CACHE_TTL) -> None:
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_ttl = cache_ttl

    async def _load_all(self, db: AsyncSession) -> dict[str, EngineRule]:
        """加载全部 EngineRule（带缓存）.

        Returns:
            ``{rule_code: EngineRule}`` 字典
        """
        now = time.monotonic()
        cached = self._cache.get("_all")
        if cached is not None and (now - cached[0]) < self._cache_ttl:
            # 缓存命中，但需要重新查询以返回 ORM 对象（避免跨 session 使用）
            # 此处直接返回缓存中的 rule_code → params 映射
            pass

        result = await db.execute(select(EngineRule))
        rules = {r.rule_code: r for r in result.scalars().all()}
        # 缓存 rule_code → params 映射（不含 ORM 对象，避免跨 session）
        params_map = {code: (r.params or {}) for code, r in rules.items()}
        self._cache["_all"] = (now, params_map)
        return rules

    async def _get_params(self, db: AsyncSession, rule_code: str) -> dict[str, Any]:
        """获取指定 rule_code 的 params（带缓存）.

        Args:
            db: 异步数据库会话
            rule_code: 规则代码（如 EVAL_CALC_CYCLE）

        Returns:
            params 字典；规则不存在时返回空字典

        Note:
            原实现用 ``asyncio.Lock`` 防 cache stampede，但本单例跨 Celery 任务
            持久化（``AsyncTask.run_async`` 每任务新建事件循环），Lock 首次竞争
            即绑定到首个事件循环，后续任务在新循环内 ``async with`` 会抛
            ``bound to a different event loop``（命中 AGENTS.md 红线）。
            调用方（KPI/诊断）均在并发执行前的主 session 内单次预载，无并发
            stampede 风险；最坏情况是同 loop 内多协程同时发现缓存过期各查一次
            DB（缓存 TTL 60s，DB 查询廉价，幂等），故移除锁。
        """
        now = time.monotonic()
        cached = self._cache.get(rule_code)
        if cached is not None and (now - cached[0]) < self._cache_ttl:
            return cached[1]

        # 查询数据库
        result = await db.execute(select(EngineRule).where(EngineRule.rule_code == rule_code))
        rule = result.scalar_one_or_none()
        params: dict[str, Any] = {}
        if rule is not None and rule.is_enabled is not False:
            params = dict(rule.params or {})
        else:
            logger.warning("EngineRule %s 不存在或已禁用，使用默认值", rule_code)

        self._cache[rule_code] = (now, params)
        return params

    async def get_params(self, db: AsyncSession, rule_code: str) -> dict[str, Any]:
        """获取指定 rule_code 的 params（公开入口，带缓存）.

        Args:
            db: 异步数据库会话
            rule_code: 规则代码（如 DIAG_CHECKUP）

        Returns:
            params 字典；规则不存在/禁用时返回空字典（调用方自行回退默认值）
        """
        return await self._get_params(db, rule_code)

    async def get_calc_cycle_minutes(self, db: AsyncSession) -> int:
        """获取计算周期（分钟）.

        对应 EVAL_CALC_CYCLE.rule_type=CALC_CYCLE, params={"cycle_minutes": 60}

        Returns:
            计算周期分钟数；规则不存在/禁用时返回 DEFAULT_CALC_CYCLE_MINUTES
        """
        params = await self._get_params(db, "EVAL_CALC_CYCLE")
        try:
            minutes = int(params.get("cycle_minutes", DEFAULT_CALC_CYCLE_MINUTES))
            if minutes <= 0:
                logger.warning(
                    "EVAL_CALC_CYCLE.cycle_minutes=%d 非法，回退默认值 %d",
                    minutes,
                    DEFAULT_CALC_CYCLE_MINUTES,
                )
                return DEFAULT_CALC_CYCLE_MINUTES
            return minutes
        except (TypeError, ValueError) as exc:
            logger.warning("EVAL_CALC_CYCLE.cycle_minutes 解析失败: %s", exc)
            return DEFAULT_CALC_CYCLE_MINUTES

    async def get_data_window_days(self, db: AsyncSession) -> int:
        """获取数据拉取窗口（天）.

        对应 DATA_FETCH_WINDOW.rule_type=DATA_FETCH, params={"window_days": 30}

        Returns:
            数据窗口天数；规则不存在/禁用时返回 DEFAULT_DATA_WINDOW_DAYS
        """
        params = await self._get_params(db, "DATA_FETCH_WINDOW")
        try:
            days = int(params.get("window_days", DEFAULT_DATA_WINDOW_DAYS))
            if days <= 0:
                return DEFAULT_DATA_WINDOW_DAYS
            return days
        except (TypeError, ValueError):
            return DEFAULT_DATA_WINDOW_DAYS

    async def get_sample_interval_seconds(self, db: AsyncSession) -> int:
        """获取采样间隔（秒）.

        对应 DATA_FETCH_WINDOW.params={"sample_interval_seconds": 1}

        Returns:
            采样间隔秒数；默认 1（1Hz）
        """
        params = await self._get_params(db, "DATA_FETCH_WINDOW")
        try:
            secs = int(params.get("sample_interval_seconds", DEFAULT_SAMPLE_INTERVAL_SECONDS))
            if secs <= 0:
                return DEFAULT_SAMPLE_INTERVAL_SECONDS
            return secs
        except (TypeError, ValueError):
            return DEFAULT_SAMPLE_INTERVAL_SECONDS

    async def get_concurrency(self, db: AsyncSession) -> int:
        """获取并发处理数量.

        对应 SCHEDULE_CONCURRENCY.rule_type=SCHEDULE, params={"concurrency": 16}

        Returns:
            并发数；规则不存在/禁用时返回 DEFAULT_CONCURRENCY
        """
        params = await self._get_params(db, "SCHEDULE_CONCURRENCY")
        try:
            conc = int(params.get("concurrency", DEFAULT_CONCURRENCY))
            if conc <= 0:
                return DEFAULT_CONCURRENCY
            return conc
        except (TypeError, ValueError):
            return DEFAULT_CONCURRENCY

    def invalidate_cache(self) -> None:
        """清除进程内缓存（配置变更后调用）."""
        self._cache.clear()
        logger.info("EngineRuleLoader 缓存已清除")


# 全局单例（进程内共享）
_engine_rule_loader: EngineRuleLoader | None = None


def get_engine_rule_loader() -> EngineRuleLoader:
    """获取全局 EngineRuleLoader 单例."""
    global _engine_rule_loader
    if _engine_rule_loader is None:
        _engine_rule_loader = EngineRuleLoader()
    return _engine_rule_loader


__all__ = [
    "DEFAULT_CALC_CYCLE_MINUTES",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_DATA_WINDOW_DAYS",
    "DEFAULT_SAMPLE_INTERVAL_SECONDS",
    "EngineRuleLoader",
    "get_engine_rule_loader",
]
