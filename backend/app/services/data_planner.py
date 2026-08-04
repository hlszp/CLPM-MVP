"""DataPlanner 数据编排器 — v4.0 架构中枢.

DataPlanner 负责指标驱动的数据获取与编排：
    1. 读取指标数据需求契约（clpm_metric_data_requirement）
    2. 按 tagGroup 合并查询计划（流量回路复用 BASE）
    3. 查询 L1 DataBlock 缓存（zstd 压缩）
    4. 未命中 → 查询 TDengine + 8 步预处理 → 写入缓存
    5. 组装 MetricDataBundle（含 8 字段数据血缘）

核心优化：
    - tagGroup 复用：所有控制类型的 OP_HF/PVOP_HF/MODE_HF/QUALITY_HF
      直接从 BASE DataBlock 派生，仅需 1 次 TDengine 查询（算法说明 §3.5.2）
    - 缓存复用：多指标共享同一 tagGroup 时仅查询/预处理一次
    - Pipeline 批量写入：减少 Redis 网络往返

采样策略（P3 #56 文档对齐）：
    KPI 计算路径**不进行 LTTB 降采样**。DataPlanner 按控制类型阈值决定采样率：
        - STABLE/SLOW/FAST/LOGIC 四类阈值由 ``get_threshold(control_type)`` 提供
        - interval_s 是固定值（典型为 1s，由 base_threshold.base_sampling_freq 决定）
        - HF tagGroup（OP_HF/PVOP_HF/MODE_HF/QUALITY_HF）固定 1s 高频采样；
          存在 BASE 组时 HF 组从 BASE 派生（宽表查询本身返回全列全量行，
          派生不降低实际数据分辨率），interval_s=1 仅保留为元数据
    KPI 计算需要全量数据点参与运算（好值率/自控率/振荡率等指标依赖每个采样点），
    LTTB 降采样会破坏指标计算的准确性。

    AGENTS.md §性能边界 提到的 "LTTB 降采样 maxPoints=2000，30 天时间窗口" 是
    **波形查询接口**（monitor.py::lttb_downsample + waveform.py::lttb_downsample_multi_series）
    的渲染优化约束，**不是** KPI 计算路径的约束：
        - monitor.py: LTTB_THRESHOLD=10000 + LTTB_TARGET_POINTS=2000（波形展示路径）
        - waveform.py: DEFAULT_MAX_POINTS=5000（前端波形渲染）
        - ADS.md: max_points=2000 是 ``get_timeseries_data`` 波形查询接口的默认值

    30 天时间窗口 × 1s 采样 ≈ 2,592,000 点，KPI 计算直接处理（7200 点性能测试
    0.38s 通过 #40，远低于性能阈值）。如未来扩展更长窗口，可考虑：
        - 缩短保留周期（dataRetentionDays）
        - 增加 DataBlock 缓存命中率（tagGroup 复用）
        - 在 TDengine 查询阶段做时间聚合（而非计算后降采样）

设计依据：ADS §2/§8/§10.1/§10.7, FDS §4/§5.3.9, PRD §8.1-8.3, 数据流程图 §7
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.contracts.data_types import (
    ControlType,
    DataBlock,
    LoopPreprocessConfig,
    MetricDataBundle,
    RawTimeSeries,
    TagGroup,
    TimeWindow,
)
from app.services.cache.l1_datablock import L1DataBlockCache
from app.services.cache.l2_bundle import L2BundleCache
from app.services.metric_data_bundle import MetricDataBundleAssembler
from app.services.preprocessing.pipeline import PREPROCESS_VERSION, PreprocessingPipeline
from app.services.preprocessing.thresholds import get_threshold

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 指标数据需求契约进程内缓存（静态数据，TTL 300s）
# ---------------------------------------------------------------------------
_REQUIREMENTS_CACHE: dict[str, Any] = {}
_REQUIREMENTS_CACHE_TS: float = 0.0
_REQUIREMENTS_CACHE_TTL = 300.0  # 5 分钟

# DB 列名 → 契约 metric_code 别名（请求方用 DB 列名、契约表用 Calculator 代码时解析）。
# 与 app.tasks.kpi_calc._DB_TO_CALCULATOR_METRIC_CODE 的唯一差异保持一致：
# 快照表列名 steady_rate（平稳率）↔ 契约/计算器 stability_rate。
# 缺失该映射会导致按 DB 列名请求时契约查询为空、对应指标静默跳过（快照只剩 PARTIAL）。
_REQUIREMENT_CODE_ALIASES: dict[str, str] = {
    "steady_rate": "stability_rate",
}


def clear_requirements_cache() -> None:
    """清空指标契约进程内缓存（测试 / 配置变更时调用）."""
    global _REQUIREMENTS_CACHE, _REQUIREMENTS_CACHE_TS
    _REQUIREMENTS_CACHE = {}
    _REQUIREMENTS_CACHE_TS = 0.0


def _filter_requirements(metrics: list[str]) -> dict[str, Any]:
    """按请求代码筛选契约，解析 DB 列名 → 契约 metric_code 别名.

    返回字典以**请求方代码**为键（如 steady_rate），确保下游
    ``_build_query_plan`` / ``_assemble_bundles`` 产出的 Bundle 沿用请求方命名。
    """
    resolved: dict[str, Any] = {}
    for code in metrics:
        row = _REQUIREMENTS_CACHE.get(code)
        if row is None:
            alias = _REQUIREMENT_CODE_ALIASES.get(code)
            if alias:
                row = _REQUIREMENTS_CACHE.get(alias)
        if row is not None:
            resolved[code] = row
    return resolved


# TDengine 查询函数签名：按 tag 角色列表查询原始时序数据
# 生产环境由适配器将现有 query_trend_data 包装为此签名；测试时注入 mock
TDengineQueryFn = Callable[
    [str, list[str], datetime, datetime, int],
    Awaitable[RawTimeSeries],
]

# 回路预处理配置加载器签名：返回 LoopPreprocessConfig（含 range_min/range_max/config_version）
ConfigLoader = Callable[[str, ControlType], Awaitable[LoopPreprocessConfig]]


# ---------------------------------------------------------------------------
# 查询计划数据结构
# ---------------------------------------------------------------------------


@dataclass
class QueryTask:
    """单个 tagGroup 的查询任务（合并后）.

    Attributes:
        tag_group: 目标 tagGroup
        metrics: 依赖此 tagGroup 的指标列表
        tag_roles: 需要查询的 tag 角色并集（如 ["pv", "sp"]）
        interval_s: 采样间隔（秒）
        reused_from: 若复用 BASE，则为 BASE；否则 None
    """

    tag_group: TagGroup
    metrics: list[str]
    tag_roles: list[str]
    interval_s: int
    reused_from: TagGroup | None = None


# ---------------------------------------------------------------------------
# DataPlanner
# ---------------------------------------------------------------------------


class DataPlanner:
    """数据编排器：指标驱动取数 + 查询计划合并 + DataBlock 缓存管理.

    使用方式::

        planner = DataPlanner(
            cache=L1DataBlockCache(redis_client),
            tdengine_query_fn=my_query_fn,
            assembler=MetricDataBundleAssembler(),
            db=session,
            config_loader=my_config_loader,
            bundle_cache=L2BundleCache(redis_client),  # 可选，启用 L2 Bundle 缓存
        )
        bundles = await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate", "stability_rate"],
            time_window=TimeWindow(start, end),
            control_type=ControlType.TEMPERATURE,
        )

    设计依据：ADS §2, 数据流程图 §7.1
    """

    def __init__(
        self,
        cache: L1DataBlockCache | None,
        tdengine_query_fn: TDengineQueryFn,
        assembler: MetricDataBundleAssembler,
        db: Any | None = None,
        config_loader: ConfigLoader | None = None,
        bundle_cache: L2BundleCache | None = None,
    ) -> None:
        """初始化 DataPlanner.

        Args:
            cache: L1 DataBlock 缓存
            tdengine_query_fn: TDengine 查询函数（按 tag 角色列表查询）
            assembler: MetricDataBundle 组装器
            db: 异步数据库会话（查询契约表；config_loader 为 None 时也用于加载回路配置）
            config_loader: 回路预处理配置加载器（注入便于测试，None 时用默认 db 查询）
            bundle_cache: L2 MetricDataBundle 缓存（可选，``None`` 时禁用 L2 缓存）。
                启用后 ``request_bundles`` 会优先查询 L2，命中则跳过查询计划与组装。
        """
        self._cache = cache
        self._query_fn = tdengine_query_fn
        self._assembler = assembler
        self._db = db
        self._config_loader = config_loader or self._default_config_loader
        self._bundle_cache = bundle_cache
        # 待写入 L2 缓存的 Key（request_bundles 中设置，_maybe_write_l2_cache 消费）
        self._pending_l2_key: str | None = None
        # 预加载的 OP 限位 {loop_id: (lower, upper)}，批量计算时注入避免逐回路查 DB
        self._preloaded_op_limits: dict[str, tuple[float | None, float | None]] | None = None

    # ------------------------------------------------------------------
    # 核心入口
    # ------------------------------------------------------------------

    async def request_bundles(
        self,
        loop_id: str,
        metrics: list[str],
        time_window: TimeWindow,
        control_type: ControlType,
    ) -> list[MetricDataBundle]:
        """提交数据需求，返回 MetricDataBundle 列表.

        流程（数据流程图 §7.1）：
            Phase 1: L2 Bundle 缓存查询（若启用，命中则直接返回，跳过组装）
            Phase 2: 读取指标数据需求契约
            Phase 3: 合并相同 tagGroup 的查询计划
            Phase 4: 查询 DataBlock 缓存
            Phase 5: 未命中 → 查询 TDengine
            Phase 6: 8 步预处理
            Phase 7: 写入缓存（Pipeline 批量）
            Phase 8: 组装 MetricDataBundle + 写入 L2 缓存

        Args:
            loop_id: 回路 ID
            metrics: 指标代码列表，如 ``["accuracy_rate", "stability_rate"]``
            time_window: 评估时间窗口
            control_type: 回路控制类型（决定采样率与阈值）

        Returns:
            MetricDataBundle 列表（每个指标一个 Bundle）

        设计依据：ADS §2, §10.7.1, 数据流程图 §7.1
        """
        logger.debug(
            "DataPlanner.request_bundles: loop=%s, metrics=%s, window=%s~%s, control=%s",
            loop_id,
            metrics,
            time_window.start.isoformat(),
            time_window.end.isoformat(),
            control_type.value,
        )

        # 加载回路预处理配置（含 range_min/range_max/config_version）
        # 提前到 Phase 1 之前：L2 缓存 Key 需纳入 cfg_version，
        # 量程变更/tag 重关联/契约变更（updated_at 变化）后旧 bundle 不再命中。
        preprocess_config = await self._config_loader(loop_id, control_type)

        # Phase 1: L2 Bundle 缓存查询（若启用，命中则直接返回，跳过查询与组装）
        # v6.1：缓存 key 包含 OP 限位，修改限位后自动失效
        # v6.2：优先使用预加载的 OP 限位，避免逐回路查 DB
        # v6.2：缓存 key 包含 cfg_version（与 L1 同一 cfg_{updated_at} 口径）
        op_lower: float | None = None
        op_upper: float | None = None
        if self._bundle_cache is not None and metrics:
            if self._preloaded_op_limits is not None:
                # 使用预加载的 OP 限位（批量计算场景）
                op_limits = self._preloaded_op_limits.get(loop_id)
                if op_limits:
                    op_lower, op_upper = op_limits
            elif self._db is not None:
                from sqlalchemy import select

                from app.models.loop import LoopLedger

                op_result = await self._db.execute(
                    select(
                        LoopLedger.op_output_lower_limit,
                        LoopLedger.op_output_upper_limit,
                    ).where(LoopLedger.id == loop_id)
                )
                op_row = op_result.first()
                if op_row:
                    op_lower = float(op_row[0]) if op_row[0] is not None else None
                    op_upper = float(op_row[1]) if op_row[1] is not None else None

        if self._bundle_cache is not None and metrics:
            l2_key = L2BundleCache.build_key(
                loop_id=loop_id,
                metrics=metrics,
                time_window_start=time_window.start,
                time_window_end=time_window.end,
                control_type=control_type.value,
                op_output_lower_limit=op_lower,
                op_output_upper_limit=op_upper,
                cfg_version=preprocess_config.config_version,
            )
            cached_bundles = await self._bundle_cache.get(l2_key)
            if cached_bundles is not None:
                logger.info(
                    "DataPlanner L2 命中，跳过查询+组装: loop=%s, bundles=%d",
                    loop_id,
                    len(cached_bundles),
                )
                return cached_bundles
            # 未命中，记录 Key 供 Phase 8 写入使用
            self._pending_l2_key = l2_key
        else:
            self._pending_l2_key = None

        # Phase 2: 读取指标数据需求契约
        requirements = await self._load_requirements(metrics)
        if not requirements:
            logger.warning("DataPlanner: 未找到任何指标契约: metrics=%s", metrics)
            return []

        # Phase 3: 合并查询计划（按 tagGroup 分组）
        query_plan = self._build_query_plan(requirements, control_type)
        logger.info(
            "DataPlanner 查询计划: loop=%s, metrics=%d→tagGroups=%d, tasks=%s",
            loop_id,
            len(metrics),
            len(query_plan),
            [
                f"{t.tag_group.value}(metrics={t.metrics},tags={t.tag_roles},"
                f"interval={t.interval_s}s,reuse={t.reused_from})"
                for t in query_plan
            ],
        )

        # Phase 4-7: 执行查询计划（查缓存 → 未命中查 TDengine + 预处理 → 写缓存）
        data_blocks = await self._execute_query_plan(
            query_plan, loop_id, time_window, control_type, preprocess_config
        )

        # Phase 8: 按指标组装 MetricDataBundle
        bundles = self._assemble_bundles(requirements, data_blocks)

        # v6.1 填充 OP 输出限位到每个 bundle 的 signals 字典
        # 设计依据：loop-range-and-output-limits-design-v1.0.md §4.3
        # 优先级：Loop 表字段 > OP Tag range_min/range_max > 默认值（不填充，由算法兜底）
        await self._fill_op_output_limits(bundles, loop_id)

        # Phase 8 (续): 写入 L2 缓存（若启用且本次未命中）
        await self._maybe_write_l2_cache(bundles)

        logger.info(
            "DataPlanner 完成: loop=%s, bundles=%d, cached_blocks=%d",
            loop_id,
            len(bundles),
            len(data_blocks),
        )
        return bundles

    # ------------------------------------------------------------------
    # Phase 2: 读取指标数据需求契约
    # ------------------------------------------------------------------

    async def _load_requirements(self, metrics: list[str]) -> dict[str, Any]:
        """从 clpm_metric_data_requirement 表读取指标契约（带进程内缓存）.

        静态数据（指标契约 rarely 变更），缓存 5 分钟，避免 1000 回路重复查询。

        Args:
            metrics: 指标代码列表

        Returns:
            ``{metric_code: ClpmMetricDataRequirement}`` 字典

        设计依据：DDS §2.15, ADS §2, 算法说明 §3.6
        """
        if self._db is None:
            logger.debug("DataPlanner: db session 未注入，返回空契约")
            return {}

        # 进程内缓存检查（静态数据，TTL 300s）
        global _REQUIREMENTS_CACHE, _REQUIREMENTS_CACHE_TS
        now = time.monotonic()
        if _REQUIREMENTS_CACHE and (now - _REQUIREMENTS_CACHE_TS) < _REQUIREMENTS_CACHE_TTL:
            # 从缓存中筛选请求的 metrics（含 DB 列名 → 契约代码别名解析）
            return _filter_requirements(metrics)

        # 缓存未命中或过期 → 查询全量并缓存
        from sqlalchemy import select

        from app.models.metric_data_requirement import ClpmMetricDataRequirement

        result = await self._db.execute(select(ClpmMetricDataRequirement))
        rows = result.scalars().all()
        _REQUIREMENTS_CACHE = {row.metric_code: row for row in rows}
        _REQUIREMENTS_CACHE_TS = now
        logger.info("DataPlanner 指标契约缓存已刷新: %d 条", len(_REQUIREMENTS_CACHE))

        return _filter_requirements(metrics)

    # ------------------------------------------------------------------
    # Phase 3: 构建合并查询计划
    # ------------------------------------------------------------------

    def _build_query_plan(
        self,
        requirements: dict[str, Any],
        control_type: ControlType,
    ) -> list[QueryTask]:
        """按 tagGroup 分组指标并合并查询计划.

        合并规则（算法说明 §3.5.2）：
            - 相同 tagGroup 的指标合并为一次查询（tags 取并集）
            - 所有控制类型复用 BASE：OP_HF/PVOP_HF/MODE_HF/QUALITY_HF
              从 BASE DataBlock 派生（宽表查询固定 SELECT 全部列，派生不丢数据），
              每回路-窗口仅需 1 次 TDengine 查询；派生组 interval_s 固定 1s
              （与原独立 HF 查询的取值一致，仅为元数据，派生不发起查询）
            - 查询计划中无 BASE 组时（如波形接口按单 tagGroup 取数），
              HF 组回退为独立查询（HF 固定 1s 采样）
            - CONFIG tagGroup 跳过（无时序数据，如 ideal_settling_time）

        Args:
            requirements: ``{metric_code: requirement}`` 字典
            control_type: 控制类型

        Returns:
            QueryTask 列表（每个对应一次 TDengine 查询或一次 BASE 派生）

        设计依据：算法说明 §3.5.2, PRD §8.3
        """
        base_threshold = get_threshold(control_type)
        base_interval = base_threshold.base_sampling_freq

        # 按 tagGroup 分组指标，并合并 tags
        grouped: dict[TagGroup, dict[str, Any]] = {}
        for metric_code, req in requirements.items():
            tag_group = self._parse_tag_group(req.tag_group)
            # CONFIG tagGroup 无时序数据，跳过
            if tag_group == TagGroup.CONFIG:
                logger.debug("跳过 CONFIG 指标（无时序数据）: %s", metric_code)
                continue

            if tag_group not in grouped:
                grouped[tag_group] = {"metrics": [], "tag_roles": set(), "req": req}
            grouped[tag_group]["metrics"].append(metric_code)
            # 合并 tags（并集）
            tags = req.tags if isinstance(req.tags, list) else list(req.tags or [])
            for t in tags:
                grouped[tag_group]["tag_roles"].add(t)

        # 所有控制类型复用 BASE：宽表查询固定 SELECT 全部列，HF 组与 BASE 组
        # 取数内容完全一致（同样的行、同样的列子集），独立查询只是重复拉取，
        # 因此 HF tagGroup 一律从 BASE DataBlock 派生（此前仅 FC 回路复用，
        # PC/TC/LC/CC 会把同一段数据重复拉 5 遍）。
        # 仅当计划中存在 BASE 组时启用复用（KPI 路径固定请求全量 12 指标，始终
        # 含 BASE）；无 BASE 组时（如波形接口按单 tagGroup 取数）HF 组保持独立查询。
        reuse_base = TagGroup.BASE in grouped

        # 构建 QueryTask 列表
        tasks: list[QueryTask] = []
        base_tags: set[str] = set()

        # 若复用 BASE，先收集 BASE 需要的额外 tags（来自所有 HF tagGroup）
        if reuse_base and TagGroup.BASE in grouped:
            base_tags = grouped[TagGroup.BASE]["tag_roles"].copy()
            for tg, info in grouped.items():
                if tg != TagGroup.BASE:
                    base_tags.update(info["tag_roles"])

        for tag_group, info in grouped.items():
            tag_roles = sorted(info["tag_roles"])

            # 复用 BASE：HF tagGroup 标记为 reused_from=BASE。
            # interval_s 固定 1s：与原独立 HF 查询的取值一致（FLOW 复用时
            # base_interval 本就为 1），派生不发起查询，此值仅为元数据。
            if reuse_base and tag_group != TagGroup.BASE:
                task = QueryTask(
                    tag_group=tag_group,
                    metrics=info["metrics"],
                    tag_roles=tag_roles,
                    interval_s=1,
                    reused_from=TagGroup.BASE,
                )
            elif tag_group == TagGroup.BASE and reuse_base:
                # BASE 需包含所有 HF tagGroup 的 tags（用于派生）
                task = QueryTask(
                    tag_group=tag_group,
                    metrics=info["metrics"],
                    tag_roles=sorted(base_tags),
                    interval_s=base_interval,
                    reused_from=None,
                )
            else:
                # 非复用：BASE 按控制类型采样，HF 固定 1s
                interval = base_interval if tag_group == TagGroup.BASE else 1
                task = QueryTask(
                    tag_group=tag_group,
                    metrics=info["metrics"],
                    tag_roles=tag_roles,
                    interval_s=interval,
                    reused_from=None,
                )
            tasks.append(task)

        # 排序：BASE 优先（派生依赖 BASE）
        tasks.sort(key=lambda t: (t.reused_from is not None, t.tag_group.value))
        return tasks

    @staticmethod
    def _parse_tag_group(value: str | TagGroup) -> TagGroup:
        """将字符串解析为 TagGroup 枚举."""
        if isinstance(value, TagGroup):
            return value
        try:
            return TagGroup(value)
        except ValueError:
            logger.warning("未知 tagGroup 值，回退为 BASE: %s", value)
            return TagGroup.BASE

    # ------------------------------------------------------------------
    # Phase 4-7: 执行查询计划
    # ------------------------------------------------------------------

    async def _execute_query_plan(
        self,
        query_plan: list[QueryTask],
        loop_id: str,
        time_window: TimeWindow,
        control_type: ControlType,
        preprocess_config: LoopPreprocessConfig,
    ) -> dict[TagGroup, DataBlock]:
        """执行查询计划：查缓存 → 未命中查 TDengine + 预处理 → 写缓存.

        对于复用 BASE 的 tagGroup，从 BASE DataBlock 派生子集，不单独查询。
        未命中的 DataBlock 通过 Pipeline 批量写入缓存（减少 RTT）。
        """
        data_blocks: dict[TagGroup, DataBlock] = {}
        pending_writes: list[tuple[str, DataBlock]] = []

        # 分离非复用 task（需查缓存/TDengine）和复用 task（从 BASE 派生）
        non_reuse_tasks = [t for t in query_plan if t.reused_from is None]
        reuse_tasks = [t for t in query_plan if t.reused_from is not None]

        async def _process_non_reuse(
            task: QueryTask,
        ) -> tuple[TagGroup, DataBlock, tuple[str, DataBlock] | None]:
            """处理单个非复用 task：查缓存 → 未命中查 TDengine + 预处理."""
            cache_key = L1DataBlockCache.build_key(
                loop_id=loop_id,
                tag_group=task.tag_group.value,
                time_window_start=time_window.start,
                time_window_end=time_window.end,
                sampling_freq=f"{task.interval_s}s",
                quality_policy=self._quality_policy_for(task.tag_group),
                pre_version=PREPROCESS_VERSION,
                cfg_version=preprocess_config.config_version,
            )
            cached = await self._cache.get(cache_key) if self._cache else None
            if cached is not None:
                return task.tag_group, cached, None
            data_block = await self._query_and_preprocess(
                loop_id=loop_id,
                task=task,
                time_window=time_window,
                preprocess_config=preprocess_config,
            )
            if data_block.point_count == 0:
                # 空 DataBlock 不写 L1（负缓存）：否则「先算后导」场景下
                # backfill 补齐数据后最长 TTL 内仍命中空块（2026-07 Phase 2 修复）。
                # 空块仍返回参与 Bundle 组装（INCONCLUSIVE 口径不变），仅不缓存。
                logger.info(
                    "DataPlanner 空 DataBlock 跳过 L1 写入: loop=%s, tagGroup=%s, key=%s",
                    loop_id,
                    task.tag_group.value,
                    cache_key,
                )
                return task.tag_group, data_block, None
            return task.tag_group, data_block, (cache_key, data_block)

        # Phase 4-6: 并行执行所有非复用 task（asyncio.gather 释放事件循环）
        if non_reuse_tasks:
            results = await asyncio.gather(*[_process_non_reuse(t) for t in non_reuse_tasks])
            for tag_group, block, write_pair in results:
                data_blocks[tag_group] = block
                if write_pair is not None:
                    pending_writes.append(write_pair)

        # 复用 BASE：从已查询的 BASE DataBlock 派生子集
        for task in reuse_tasks:
            base_block = data_blocks.get(task.reused_from)
            if base_block is None:
                logger.warning(
                    "无法派生 %s：BASE DataBlock 未就绪，跳过",
                    task.tag_group.value,
                )
                continue
            derived = self._derive_from_base(base_block, task.tag_group, task.tag_roles, loop_id)
            data_blocks[task.tag_group] = derived
            logger.debug(
                "从 BASE 派生 %s: tags=%s, points=%d",
                task.tag_group.value,
                task.tag_roles,
                derived.point_count,
            )

        # Phase 7: Pipeline 批量写入未命中的 DataBlock（cache=None 时跳过）
        if pending_writes and self._cache:
            keys = [k for k, _ in pending_writes]
            blocks = [b for _, b in pending_writes]
            written = await self._cache.set_many(blocks, keys=keys)
            logger.info(
                "DataPlanner 缓存写入: loop=%s, blocks=%d, written=%d",
                loop_id,
                len(pending_writes),
                written,
            )

        return data_blocks

    async def _query_and_preprocess(
        self,
        loop_id: str,
        task: QueryTask,
        time_window: TimeWindow,
        preprocess_config: LoopPreprocessConfig,
    ) -> DataBlock:
        """查询 TDengine + 8 步预处理，生成 DataBlock.

        Phase 5: 查询 TDengine
        Phase 6: 8 步预处理

        设计依据：数据流程图 §7.1 Phase 5-6
        """
        logger.info(
            "DataPlanner 回源查询: loop=%s, tagGroup=%s, tags=%s, interval=%ds",
            loop_id,
            task.tag_group.value,
            task.tag_roles,
            task.interval_s,
        )

        # Phase 5: 查询数据源
        t_query_start = time.perf_counter()
        raw = await self._query_fn(
            loop_id=loop_id,
            tag_roles=task.tag_roles,
            start=time_window.start,
            end=time_window.end,
            interval_s=task.interval_s,
        )
        t_query_elapsed = time.perf_counter() - t_query_start

        if not raw.timestamps:
            logger.warning(
                "TDengine 返回空数据: loop=%s, tagGroup=%s, query_time=%.3fs",
                loop_id,
                task.tag_group.value,
                t_query_elapsed,
            )
            # 返回空 DataBlock（避免后续 KeyError）
            return self._empty_data_block(loop_id, task.tag_group, task.interval_s)

        # Phase 6: 8 步预处理（移至线程池释放事件循环，纯 Python CPU 密集型）
        t_pre_start = time.perf_counter()
        pipeline = PreprocessingPipeline(preprocess_config)
        data_block = await asyncio.to_thread(pipeline.process, raw, task.tag_group)
        t_pre_elapsed = time.perf_counter() - t_pre_start

        logger.info(
            "DataPlanner 取数+预处理: loop=%s, tagGroup=%s, points=%d, "
            "query=%.3fs, preprocess=%.3fs",
            loop_id,
            task.tag_group.value,
            data_block.point_count,
            t_query_elapsed,
            t_pre_elapsed,
        )

        logger.debug(
            "预处理完成: loop=%s, tagGroup=%s, points=%d, valid_rate=%.4f",
            loop_id,
            task.tag_group.value,
            data_block.point_count,
            data_block.quality_summary.valid_rate,
        )
        return data_block

    # ------------------------------------------------------------------
    # tagGroup 复用派生
    # ------------------------------------------------------------------

    def _derive_from_base(
        self,
        base_block: DataBlock,
        tag_group: TagGroup,
        tag_roles: list[str],
        loop_id: str,
    ) -> DataBlock:
        """从 BASE DataBlock 派生子 tagGroup DataBlock（流量回路复用）.

        提取指定 tag 角色的信号/有效性/异常原因码，构造新的 DataBlock。
        时间戳、连续段、配置版本等与 BASE 一致。

        设计依据：算法说明 §3.5.2, 数据流程图 §7.3
        """
        signals: dict[str, list[Any]] = {}
        validity: dict[str, list[bool]] = {}
        outlier_reasons: dict[str, list[list[str]]] = {}

        for role in tag_roles:
            if role in base_block.signals:
                signals[role] = base_block.signals[role]
            valid_key = f"{role}_valid"
            if valid_key in base_block.validity:
                validity[valid_key] = base_block.validity[valid_key]
            if role in base_block.outlier_reasons:
                outlier_reasons[role] = base_block.outlier_reasons[role]

        data_block_id = f"db_{loop_id}_{tag_group.value}_{base_block.sampling_freq}"
        return DataBlock(
            data_block_id=data_block_id,
            loop_id=loop_id,
            tag_group=tag_group.value,
            sampling_freq=base_block.sampling_freq,
            timestamps=list(base_block.timestamps),
            signals=signals,
            validity=validity,
            outlier_reasons=outlier_reasons,
            quality_summary=base_block.quality_summary,
            consecutive_segments=list(base_block.consecutive_segments),
            config_version=base_block.config_version,
            preprocess_version=base_block.preprocess_version,
            point_count=base_block.point_count,
            # P0-B: 派生 DataBlock 继承 BASE 的响应类别
            control_type=base_block.control_type,
            # P2-1: 派生 DataBlock 必须继承回路级可信度，否则使用默认 "E"
            # 导致 effective_auto_rate 等子 tagGroup 指标全部 E → 综合评分 INCONCLUSIVE
            loop_confidence_level=base_block.loop_confidence_level,
            loop_valid_rate=base_block.loop_valid_rate,
        )

    # ------------------------------------------------------------------
    # Phase 8: 组装 MetricDataBundle
    # ------------------------------------------------------------------

    def _assemble_bundles(
        self,
        requirements: dict[str, Any],
        data_blocks: dict[TagGroup, DataBlock],
    ) -> list[MetricDataBundle]:
        """按指标组装 MetricDataBundle.

        每个指标根据其契约的 tag_group 和 mask_expression，从对应 DataBlock
        组装 Bundle（应用 Metric Validity Mask + 生成数据血缘）。

        设计依据：数据流程图 §7.1 Phase 8, 算法说明 §3.6-3.7
        """
        bundles: list[MetricDataBundle] = []
        for metric_code, req in requirements.items():
            tag_group = self._parse_tag_group(req.tag_group)
            if tag_group == TagGroup.CONFIG:
                # CONFIG 指标（如 ideal_settling_time）无时序数据，不生成 Bundle
                continue

            data_block = data_blocks.get(tag_group)
            if data_block is None:
                logger.warning(
                    "DataBlock 缺失，跳过指标组装: metric=%s, tagGroup=%s",
                    metric_code,
                    tag_group.value,
                )
                continue

            bundle = self._assembler.assemble(
                metric_code=metric_code,
                data_block=data_block,
                mask_expression=req.mask_expression,
                requirement=req,
            )
            bundles.append(bundle)
        return bundles

    # ------------------------------------------------------------------
    # Phase 8 (续): 写入 L2 缓存
    # ------------------------------------------------------------------

    async def _maybe_write_l2_cache(self, bundles: list[MetricDataBundle]) -> None:
        """将组装好的 Bundle 列表写入 L2 缓存（若启用且本次 L2 未命中）.

        仅在本次 ``request_bundles`` 触发了 L2 查询且未命中时写入，
        避免重复写入已命中的 Key。空 Bundle 列表不写入。

        设计依据：ADS §10.7.1, 数据流程图 §7.1 Phase 8
        """
        l2_key = self._pending_l2_key
        # 消费后立即清空，避免跨请求泄漏
        self._pending_l2_key = None
        if l2_key is None or self._bundle_cache is None or not bundles:
            return
        try:
            await self._bundle_cache.set(l2_key, bundles)
            logger.debug("DataPlanner L2 写入: key=%s, bundles=%d", l2_key, len(bundles))
        except Exception:  # noqa: BLE001
            # L2 写入失败不应影响主流程（缓存只是优化）
            logger.warning("DataPlanner L2 写入失败，忽略: key=%s", l2_key, exc_info=True)

    async def _fill_op_output_limits(self, bundles: list[MetricDataBundle], loop_id: str) -> None:
        """v6.1 填充 OP 输出限位到每个 bundle 的 signals 字典.

        v6.2 优化：优先使用预加载的 OP 限位（批量计算场景），避免逐回路查 DB。

        优先级（设计文档 §2.3）：
            1. Loop 表 op_output_lower_limit / op_output_upper_limit（非 NULL）
            2. OP Tag range_min / range_max（已关联且非 NULL）
            3. 默认值（不填充，由 SaturationRateCalculator 用 DEFAULT_OP_LOW/HIGH 兜底）

        signals 字典的值统一为列表类型（对齐 _read_config_scalar 约定）。

        设计依据：loop-range-and-output-limits-design-v1.0.md §4.3
        """
        if not bundles:
            return

        # v6.2：优先使用预加载的 OP 限位
        if self._preloaded_op_limits is not None:
            op_limits = self._preloaded_op_limits.get(loop_id)
            if op_limits:
                op_lower, op_upper = op_limits
                for bundle in bundles:
                    if op_lower is not None:
                        bundle.data_block.signals["op_low"] = [op_lower]
                    if op_upper is not None:
                        bundle.data_block.signals["op_high"] = [op_upper]
                if op_lower is not None or op_upper is not None:
                    logger.debug(
                        "DataPlanner 填充 OP 限位(预加载): loop=%s, op_low=%s, op_high=%s",
                        loop_id,
                        op_lower,
                        op_upper,
                    )
            return

        if self._db is None:
            return

        from sqlalchemy import select

        from app.models.loop import LoopLedger, LoopTagMapping
        from app.models.tag import TagRegistry

        # 查询 Loop 表限位字段
        loop_result = await self._db.execute(
            select(
                LoopLedger.op_output_lower_limit,
                LoopLedger.op_output_upper_limit,
            ).where(LoopLedger.id == loop_id)
        )
        loop_row = loop_result.first()
        loop_lower = float(loop_row[0]) if loop_row and loop_row[0] is not None else None
        loop_upper = float(loop_row[1]) if loop_row and loop_row[1] is not None else None

        # 若 Loop 表字段为 NULL，回退到 OP Tag 量程
        op_lower = loop_lower
        op_upper = loop_upper
        if op_lower is None or op_upper is None:
            op_result = await self._db.execute(
                select(TagRegistry.range_min, TagRegistry.range_max)
                .join(LoopTagMapping, LoopTagMapping.tag_id == TagRegistry.id)
                .where(
                    LoopTagMapping.loop_id == loop_id,
                    LoopTagMapping.tag_role == "OP",
                )
            )
            op_row = op_result.first()
            if op_row is not None:
                if op_lower is None and op_row[0] is not None:
                    op_lower = float(op_row[0])
                if op_upper is None and op_row[1] is not None:
                    op_upper = float(op_row[1])

        # 填充到每个 bundle 的 signals 字典
        for bundle in bundles:
            if op_lower is not None:
                bundle.data_block.signals["op_low"] = [op_lower]
            if op_upper is not None:
                bundle.data_block.signals["op_high"] = [op_upper]
        if op_lower is not None or op_upper is not None:
            logger.debug(
                "DataPlanner 填充 OP 限位: loop=%s, op_low=%s, op_high=%s",
                loop_id,
                op_lower,
                op_upper,
            )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _quality_policy_for(tag_group: TagGroup) -> str:
        """根据 tagGroup 推断质量策略标签.

        QUALITY_HF 使用 KEEP_ALL（好值率不删除行），
        其余默认 KEEP_ALL_WITH_VALIDITY。

        设计依据：算法说明 §3.4.1, §3.6.2
        """
        if tag_group == TagGroup.QUALITY_HF:
            return "KEEP_ALL"
        return "KEEP_ALL_WITH_VALIDITY"

    @staticmethod
    def _empty_data_block(loop_id: str, tag_group: TagGroup, interval_s: int) -> DataBlock:
        """构造空 DataBlock（TDengine 返回空数据时使用）."""
        return DataBlock(
            data_block_id=f"db_{loop_id}_{tag_group.value}_{interval_s}s",
            loop_id=loop_id,
            tag_group=tag_group.value,
            sampling_freq=f"{interval_s}s",
            timestamps=[],
            signals={},
            validity={},
            outlier_reasons={},
            point_count=0,
        )

    async def _default_config_loader(
        self, loop_id: str, control_type: ControlType
    ) -> LoopPreprocessConfig:
        """默认回路配置加载器：从数据库查询量程与配置版本.

        查询 LoopTagMapping + TagRegistry 获取 PV tag 的 range_min/range_max。
        config_version 基于 LoopLedger.updated_at 生成。

        设计依据：DDS §3/§4/§5
        """
        if self._db is None:
            # 无 db session 时返回默认配置（便于单元测试）
            return LoopPreprocessConfig(
                loop_id=loop_id,
                control_type=control_type,
                range_min=0.0,
                range_max=100.0,
                config_version="v1",
                op_range_min=0.0,
                op_range_max=100.0,
            )

        from sqlalchemy import select

        from app.models.loop import LoopLedger, LoopTagMapping
        from app.models.tag import TagRegistry

        # 查询回路
        loop_result = await self._db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
        loop = loop_result.scalar_one_or_none()

        # 查询 PV tag 的量程
        range_min = 0.0
        range_max = 100.0
        mapping_result = await self._db.execute(
            select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id)
        )
        mappings = {m.tag_role: m for m in mapping_result.scalars().all()}
        pv_mapping = mappings.get("PV")
        if pv_mapping:
            tag_result = await self._db.execute(
                select(TagRegistry).where(TagRegistry.id == str(pv_mapping.tag_id))
            )
            pv_tag = tag_result.scalar_one_or_none()
            if pv_tag:
                if pv_tag.range_min is not None:
                    range_min = float(pv_tag.range_min)
                if pv_tag.range_max is not None:
                    range_max = float(pv_tag.range_max)

        # 查询 OP tag 的量程（OP 归一化用，OP 是百分比输出与 PV 物理量程不同）
        op_range_min = 0.0
        op_range_max = 100.0
        op_mapping = mappings.get("OP")
        if op_mapping:
            op_tag_result = await self._db.execute(
                select(TagRegistry).where(TagRegistry.id == str(op_mapping.tag_id))
            )
            op_tag = op_tag_result.scalar_one_or_none()
            if op_tag:
                if op_tag.range_min is not None:
                    op_range_min = float(op_tag.range_min)
                if op_tag.range_max is not None:
                    op_range_max = float(op_tag.range_max)

        # config_version 基于 loop.updated_at + OP 量程（配置变更或 OP 量程变更时自动递增）
        op_suffix = f"opr{op_range_min}_{op_range_max}"
        if loop and loop.updated_at:
            config_version = f"cfg_{int(loop.updated_at.timestamp())}_{op_suffix}"
        else:
            config_version = f"v1_{op_suffix}"

        # P0-B: 响应类别（STABLE/SLOW/FAST/LOGIC）来自 loop_ledger.control_type，
        # 供指标计算器读取算法参数；None 时计算器回落 STABLE 默认值
        response_category = loop.control_type if loop else None

        return LoopPreprocessConfig(
            loop_id=loop_id,
            control_type=control_type,
            range_min=range_min,
            range_max=range_max,
            config_version=config_version,
            response_category=response_category,
            op_range_min=op_range_min,
            op_range_max=op_range_max,
        )


__all__ = ["DataPlanner", "QueryTask"]
